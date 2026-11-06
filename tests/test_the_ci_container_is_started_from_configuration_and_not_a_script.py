"""`kmt run-ci-container`, once it is the library calling `kubectl` and not a script.

`scripts/utilities/run_ci_container.sh` did three things in 34 lines: print the kubectl context,
read three keys out of `{env}/static.json` with `jq`, and start one interactive pod. All three are
now Python, and the two interesting branches are the ones bash could not take:

* an image key that is not configured. `jq -r` answered `null`, the pod was accepted, and the
  failure arrived on the cluster as a pull error for `null/ci-runner:null`. Here the key names
  itself, and `kubectl` is never called.
* the arguments that used to reach a script. The script interpolated `$1`/`$2` into a command
  line, which is what the port to Python removed. Asserted as argv here, because that is the only
  difference a shell-based test could not see.

The pod itself is still interactive, so nothing in the port may capture it: a captured `bash` is a
session that never shows up, not a quieter one.
"""

import logging

import pytest
from click.testing import CliRunner

from cli.commands.utilities import run_ci_container
from kubementat.infrastructure import shell
from kubementat.core.identity.target import kubectl_target_args
from kubementat.core.identity.context import Context
from kubementat.ci_utils import CI_CONTAINER_COMMAND, CI_CONTAINER_NAME, CiUtils
from kubementat.errors import CiImageNotConfiguredError

# What the fixture's `static.json` says the CI image is, assembled the way `CiUtils` assembles it.
CI_IMAGE = "registry.example.test/ci-runner:1.2.3"


def container_runs(fake_binaries):
    """The `kubectl run` calls, as argv. Reads are not part of this claim.

    The cluster is in the argv before `run`, so matching on the first two words no longer
    finds the call. What is matched instead is the subcommand, wherever it sits.
    """
    return [call for call in fake_binaries.commands()
            if call[0] == "kubectl" and "run" in call]


def kubectl_reads(fake_binaries):
    """Every kubectl call that is not the pod. One of them used to be a context read."""
    return [call for call in fake_binaries.commands() if call[0] == "kubectl" and "run" not in call]


def test_the_container_is_started_with_argv_and_the_configured_image(fixture_repo, dry_run_env,
                                                                     fake_binaries):
    """The happy path, in one line of argv.

    The pod is the same one the script started, in the namespace the team is named after, with the
    same flags. Order is asserted, not assumed: `kubectl run` is picky about where its arguments
    sit, and a reordered argv list is a different pod.
    """
    CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).run_ci_container()

    assert container_runs(fake_binaries) == [[
        "kubectl", *kubectl_target_args(Context.build(fixture_repo.environment, fixture_repo.team).target), "run", CI_CONTAINER_NAME,
        "-n", fixture_repo.team, "--rm", "-i", "--tty", f"--image={CI_IMAGE}",
        "--command", CI_CONTAINER_COMMAND,
    ]]


def test_the_cluster_the_container_lands_in_is_named_and_never_asked_for(
        fixture_repo, dry_run_env, fake_binaries, caplog):
    """The script printed the context with `set -e` in front of it, so it had to answer.

    The guarantee is the same and the mechanism is gone: the
    `kubectl config current-context` read is deleted because it answered from the local kubeconfig and not
    from what the command had resolved, so on a machine that said `prod` and a command that said
    `dev` it printed `prod`. Which cluster this is is now said from the `Context`, so there is no
    read to get wrong and no call to make before the pod. Asserted as an absence, because the only
    thing that can regress here is the read coming back.
    """
    with caplog.at_level(logging.INFO):
        CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).run_ci_container()

    assert [call for call in kubectl_reads(fake_binaries) if "config" in call] == [], \
        "the context read came back, and it is the read that answered for the wrong cluster"
    assert "context: fixture" in caplog.text, caplog.text
    assert "https://" in caplog.text, "the address travels with the name, so the claim is checkable"


def test_an_image_key_the_configuration_omits_names_itself_and_touches_nothing(
        fixture_repo, dry_run_env, fake_binaries):
    """The missing-configuration branch, asserted as "the cluster was never asked".

    bash sent `null` as the image name. Python refuses to build a reference out of a key it was
    not given, which is also the difference between "nothing happened" and "a pod was created
    that will never start".
    """
    fixture_repo.environment_static(TEKTON_CI_IMAGE_TAG="")

    with pytest.raises(CiImageNotConfiguredError) as error:
        CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).run_ci_container()

    assert "TEKTON_CI_IMAGE_TAG" in str(error.value)
    assert str(fixture_repo.platform_config / fixture_repo.environment / "static.json") \
        in str(error.value), "the message has to name the file the key belongs in"
    assert fake_binaries.commands() == [], "the command created a pod it could not name the image of"


def test_a_registry_url_is_not_glued_to_the_image_name_twice(fixture_repo, dry_run_env,
                                                             fake_binaries):
    """`https://registry/` + `/ci-runner` is `https://registry//ci-runner`, and that is a bad image.

    The base URL is written by people into JSON, and a trailing slash is a thing people do. It is
    trimmed, so the reference is what the registry actually serves.
    """
    fixture_repo.environment_static(DOCKER_REGISTRY_BASE_URL="registry.example.test/")

    assert CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).ci_image() \
        == "registry.example.test/ci-runner:1.2.3"


def test_the_command_is_the_reachable_path_and_takes_the_environment_and_the_team(
        fixture_repo, dry_run_env, fake_binaries):
    """`kmt run-ci-container dev fixture` is still how an operator runs this.

    Invoked as a process would be, through the command module rather than the library, because
    what is under test is that the CLI reaches the library at all. Before this it reached
    `scripts/utilities/run_ci_container.sh`, which is the whole of what changed.
    """
    result = CliRunner().invoke(run_ci_container,
                                [fixture_repo.environment, fixture_repo.team],
)

    assert result.exit_code == 0, result.output
    run = container_runs(fake_binaries)[0]
    assert run[run.index("run") + 1:run.index("run") + 4] == \
        [CI_CONTAINER_NAME, "-n", fixture_repo.team]


def test_the_container_is_not_captured(fixture_repo, dry_run_env, fake_binaries, monkeypatch):
    """Interactive means streamed. A `capture=True` here would break the command silently.

    The shim cannot tell a tty, so the guarantee is asserted on the way the result comes back: an
    empty `stdout` on the recorded call means nothing was captured, whatever the argv said.
    """
    seen = []
    real_run = shell.run

    def watching(argv, **kwargs):
        if argv[0] == "kubectl" and "run" in argv:
            seen.append(kwargs)
        return real_run(argv, **kwargs)

    monkeypatch.setattr("kubementat.ci_utils.shell.run", watching)

    CiUtils(Context.build(fixture_repo.environment, fixture_repo.team)).run_ci_container()

    assert seen, "kubectl run was not reached through the command primitive"
    assert not seen[0].get("capture"), "an interactive container was captured, which is a hung session"
