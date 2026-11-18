"""One component comes off the cluster — the release the cluster names, and the claims it made."""

import json
from types import SimpleNamespace

import pytest
from conftest import run_kmt
from recorded_operations import API_PROGRAM

from kubementat.core.identity.context import Context
from kubementat.errors import UninstallIncompleteError
from kubementat.infrastructure.helm import HelmUtils

COMPONENT = "polaris"
NAMESPACE = "polaris"
RELEASE = "polaris"
CLAIMS = ["polaris-data-0", "polaris-scratch-0"]

# The router, addressed by the table that says which components exist. Read rather than typed so a
# checkout that lost the entry fails here rather than in a test that believed a string.
ROUTER = "traefik"


def namespace_of(argv):
    for index, argument in enumerate(argv):
        if argument in ("-n", "--namespace") and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _address_of(argv):
    """The flags naming the cluster, and what they were given."""
    dropped = set()
    for index, argument in enumerate(argv):
        if argument in ("--kubeconfig", "--kube-context", "--context", "--cluster", "--kube-token"):
            dropped.update((index, index + 1))
    return dropped


def release_commands_to(commands, verb, name, namespace):
    """The commands that address *name* with *verb* in *namespace*, however they were spelled."""
    return [command for command in commands
            if command[:1] == ["helm"] and verb in command
            and named_after(command, verb) == name and namespace_of(command) == namespace]


def named_after(argv, verb):
    """The release a `helm <verb>` addresses: the first word after the verb that is not a flag."""
    dropped = _address_of(argv)
    following = False
    for index, word in enumerate(argv):
        if following and index not in dropped and not word.startswith("-"):
            return word
        if index in dropped:
            following = True
            continue
        if word == verb:
            following = True
    return None


def what_the_cluster_was_asked_to_lose(calls_or_commands):
    """What a run would have taken off the cluster: `(kind, namespace, name)` triples."""
    lost = set()
    for call in calls_or_commands:
        argv = list(call.argv if hasattr(call, "argv") else call)
        program = argv[0]
        if program == API_PROGRAM:
            # A delete the client made, read from the shim log: the path names the namespace, kind
            # and object, `.../namespaces/<ns>/persistentvolumeclaims/<name>`.
            if len(argv) > 2 and argv[1].upper() == "DELETE" and "persistentvolumeclaims" in argv[2]:
                parts = [part for part in argv[2].strip("/").split("/") if part]
                namespace = (parts[parts.index("namespaces") + 1]
                             if "namespaces" in parts and len(parts) > 1 else None)
                lost.add(("claim", namespace, parts[-1]))
            continue
        if program == "helm" and "uninstall" in argv:
            lost.add(("release", namespace_of(argv), named_after(argv, "uninstall")))
        elif program == "helm" and "delete" in argv:
            lost.add(("release", namespace_of(argv), named_after(argv, "delete")))
        elif program == "kubectl" and "delete" in argv:
            position = argv.index("delete")
            kind, names = argv[position + 1], argv[position + 2:]
            if "persistentvolumeclaims" not in kind:
                continue
            stopped = []
            for name in names:
                if name.startswith("-"):
                    break
                stopped.append(name)
            lost.update(("claim", namespace_of(argv), name) for name in stopped)
    return lost


def configured(fixture_repo, namespace=NAMESPACE, release_name=RELEASE):
    """The two keys the script's `jq` reads, spelled the way a team's are."""
    fixture_repo.environment_static(**{
        f"{COMPONENT.upper()}_DEPLOYMENT_NAMESPACE": namespace,
        f"{COMPONENT.upper()}_DEPLOYMENT_NAME": release_name,
    })


def respond(fake_binaries, recorded_api, releases=(), claims=()):
    """What the fake cluster answers when asked."""
    fake_binaries.respond("helm", json.dumps([{"name": name, "namespace": namespace}
                                              for name, namespace in releases]),
                          subcommand="list")
    # The claims are declared once and answered twice, because the run that reads them now has two
    # mechanisms in it: the uninstall itself asks the client, and a command run as its own
    # process answers through the fake transport, which reads the same response files the shims do.
    fake_binaries.respond("kubectl", json.dumps({"items": [{"metadata": {"name": name}}
                                                           for name in claims]}),
                          subcommand="get")
    recorded_api.answer("list_namespaced_persistent_volume_claim",
                        lambda **kwargs: SimpleNamespace(items=[
                            SimpleNamespace(metadata=SimpleNamespace(name=name))
                            for name in claims]))
    return fake_binaries


def uninstalled(fixture_repo, recorded_api):
    """One component off the cluster, with its claims answered by the recorder."""
    return HelmUtils(Context.build(fixture_repo.environment, fixture_repo.team)).uninstall_release(COMPONENT)


def uninstalled_router(fixture_repo, recorded_api):
    """The same command with the router as its component."""
    return HelmUtils(Context.build(fixture_repo.environment, fixture_repo.team)).uninstall_release(ROUTER)


def written_commands(commands):
    """The commands that write, so a namespace check is about the deletions and not the listing."""
    return [command for command in commands if "uninstall" in command or "delete" in command]


def test_a_component_that_is_not_installed_says_so_and_deletes_nothing(fixture_repo,
                                                                       dry_run_env,
                                                                       fake_binaries, recorded_api):
    """Absence is a normal answer, not a warning."""
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[])

    outcome = uninstalled(fixture_repo, recorded_api)

    assert outcome["releases"] == []
    assert outcome["claims"] == []
    assert not [command for command in fake_binaries.commands()
                if "uninstall" in command or "delete" in command], fake_binaries.commands()


def test_a_release_that_made_no_claims_leaves_the_claim_sweep_alone(fixture_repo,
                                                                    dry_run_env,
                                                                    fake_binaries, recorded_api):
    """A stateless release: the uninstall runs, and the sweep reads nothing it should delete."""
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=[])

    outcome = uninstalled(fixture_repo, recorded_api)

    assert outcome["claims"] == []
    commands = fake_binaries.commands()
    assert release_commands_to(commands, "uninstall", RELEASE, NAMESPACE), commands
    assert not [command for command in commands if "delete" in command], commands


def test_a_release_that_will_not_uninstall_keeps_its_claims_and_raises(fixture_repo,
                                                                       dry_run_env,
                                                                       fake_binaries, recorded_api):
    """A real error fails loudly, and loudly in the one way that cannot make it worse."""
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)
    fake_binaries.respond("helm", "release: not found", exit_code=1, subcommand="uninstall")

    with pytest.raises(UninstallIncompleteError) as error:
        uninstalled(fixture_repo, recorded_api)

    assert RELEASE in str(error.value)
    assert not [command for command in fake_binaries.commands()
                if "delete" in command], "the claims of a release that is still installed went"


def test_the_command_is_on_the_surface_and_needs_no_team(fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """`kmt component uninstall` is on the surface and needs no team."""
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)

    result = run_kmt(["component", "uninstall", fixture_repo.environment, "POLARIS"], dry_run_env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"namespace: {NAMESPACE}" in result.stdout
    assert f"releases uninstalled: {RELEASE}" in result.stdout
    assert "claims deleted" in result.stdout
    assert what_the_cluster_was_asked_to_lose(fake_binaries.calls()) == {
        ("release", NAMESPACE, RELEASE),
        ("claim", NAMESPACE, CLAIMS[0]), ("claim", NAMESPACE, CLAIMS[1])}


def test_the_command_names_a_component_it_does_not_know_and_deletes_nothing(fixture_repo,
                                                                            dry_run_env,
                                                                            fake_binaries, recorded_api):
    """A typo through the CLI is one readable line, not a traceback, and it deletes nothing."""
    from conftest import run_kmt

    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)

    result = run_kmt(["component", "uninstall", fixture_repo.environment, "polari"], dry_run_env)

    assert result.returncode != 0, "a component nobody named must not exit 0"
    assert "not a kubementat component" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr, result.stdout + result.stderr
    assert not [command for command in fake_binaries.commands() if "delete" in command], \
        fake_binaries.commands()


def test_the_uninstall_shows_up_once_and_under_the_group_name():
    """One route to taking one component off the cluster, and its name is the group's."""
    from cli_help import commands_from_help, help_output, run_help

    names = commands_from_help(help_output())

    assert "component" in names, sorted(names)
    assert [name for name in names if "component-uninstall" in name] == [], sorted(names)
    # `helm` has a group of its own now, so "there is no helm subgroup" is no longer a true sentence
    # and was never the real claim. What this guards is the hiding place: the group's own listing is
    # the apply and the chart read, and nothing that takes a component off.
    assert commands_from_help(run_help("kmt", "helm", "--help")) == {"apply", "latest-version"}, \
        sorted(names)
    assert [name for name in names if "component" in name] == ["component"], sorted(names)


def test_renaming_the_routers_namespace_uninstalls_from_the_new_place(fixture_repo,
                                                                      dry_run_env,
                                                                      fake_binaries, recorded_api):
    """The other half of the entry being load-bearing: a rename has to move the delete."""
    fixture_repo.environment_static(**{f"{ROUTER.upper()}_DEPLOYMENT_NAMESPACE": "edge"})
    fake_binaries.respond("helm", json.dumps([{"name": ROUTER, "namespace": "edge"}]),
                          subcommand="list")

    outcome = uninstalled_router(fixture_repo, recorded_api)

    assert outcome["namespace"] == "edge"
    commands = fake_binaries.commands()
    assert release_commands_to(commands, "uninstall", ROUTER, "edge"), commands
    assert {namespace_of(command) for command in written_commands(commands)} == {"edge"}, commands
