"""Generated deploy pipelines and trigger configs land where the commands that read them look.

Two generators live in `infrastructure/tekton/generation.py`: `generate_deploy_pipeline()` turns a
team's template into `deploy-pipeline-<app>.yml`, and `generate_trigger_configs()` turns the shipped
trigger templates into the four manifests an app needs. Both are `kmt` commands, and where they write
is the decision that makes the next command work — `setup-pipelines` applies
`Config.tekton_team_pipeline_dir` and `setup_triggers` applies `Config.tekton_team_trigger_dir`, so a
generated file that is reviewed and committed is a file the team's cluster gets. Templates and output
are never read from the same place, which is also why `apply_resources` skips any `templates/`
directory.

Three refusals are as much the contract as the writes, because each closes a way to produce a file
that looks right and does nothing: a placeholder the configuration does not supply, an app name that
would escape the team's directory, and a pre-move `<team>/triggers/` directory whose contents would be
applied nowhere.

What the round trip costs is said out loud rather than argued about: a template is not a text
substitution any more, so the review guidance a template leaves in comments does not reach the
generated file — which is why the command prints that itself.
"""


import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT, run_kmt
from kubementat.errors import (GenerationArgumentNotSuppliedError,
                                 GenerationTemplateNotFoundError, ObsoleteTriggerDirectoryError)
from asset_paths import relocate_asset_root, tekton_ci_path
from cluster_fixture import configuration_only_context
from kubementat.infrastructure.tekton import generation

APP = "nginx-example"
PIPELINE = "build-nginx-example"

# The app's git coordinates, spelled the way the command's options spell them.
APP_GIT = {
    "APP_NAME": APP,
    "APP_GIT_URL": "ssh://github.com/julweber/nginx-example.git",
    "APP_GIT_PROJECT_NAME": APP,
    "APP_GIT_SERVER_HOST": "github.com",
    "APP_GIT_SERVER_PORT": "22",
    "APP_GIT_SERVER_SSH_USER": "git",
}
PYTHON_GIT = {
    "git_url": APP_GIT["APP_GIT_URL"],
    "git_project_name": APP_GIT["APP_GIT_PROJECT_NAME"],
    "git_server_host": APP_GIT["APP_GIT_SERVER_HOST"],
    "git_server_port": APP_GIT["APP_GIT_SERVER_PORT"],
    "git_server_ssh_user": APP_GIT["APP_GIT_SERVER_SSH_USER"],
}

PLACEHOLDERS_LEFT = re.compile(r"[A-Z][A-Z0-9_]*_PLACEHOLDER")

OBSOLETE_TRIGGER_DIR = "platform_config/dev/fixture/triggers"
TEAM_TRIGGER_DIR = "platform_config/dev/fixture/tekton/triggers"
TEAM_PIPELINE_DIR = "platform_config/dev/fixture/tekton/pipelines"


@pytest.fixture
def checkout(fixture_repo, dry_run_env, monkeypatch):
    """The fixture checkout, with the generator templates added back.

    `fixture_repo` seeds scripts and configuration and no `tekton_ci` templates, and the generators
    read templates from `Config.tekton_pipeline_template_dir`. Those are tracked product assets, so
    the fixture uses the real ones; a template invented for the test would be a template nobody
    generates from.

    `BASE_DOMAIN` is set here rather than in `conftest.ENVIRONMENT_STATIC` because it is the trigger
    generator's own read, and a fixture that always had it could never show what happens when it
    is missing.
    """
    # The bytes come from where the installed kubementat carries them, and they are laid
    # down in the fixture checkout so the relocated resolver below can point the generator at them.
    # Without the relocation the generator would read the real package and every one of this file's
    # "the template was edited" rows would
    # be editing a file nothing opens.
    for subtree in ("pipelines/templates", "triggers/templates"):
        shutil.copytree(tekton_ci_path(*subtree.split("/")),
                        fixture_repo.root / "tekton_ci" / subtree, dirs_exist_ok=True)
    relocate_asset_root(fixture_repo.root, monkeypatch)
    fixture_repo.environment_static(BASE_DOMAIN="hooks.example.test")
    return fixture_repo


def generation_config(checkout):
    """The configuration the two generators read, with no `TektonUtils` in front of it.

    The generation group moved out of the class, so the parity harness calls the module the way a
    caller does now: the parameter the conversion gave those functions is `Config`, which is what
    the old methods read from the instance, and nothing else about the call changed.
    """
    return configuration_only_context(checkout.environment, checkout.team).config


def documents_of(path):
    """The documents of one manifest file, empty ones dropped the way `apply_resources` drops them."""
    return [document for document in yaml.safe_load_all(Path(path).read_text()) if document is not None]


def generated_trigger_files(checkout, trigger_type="github", app_name=APP):
    return generation.generate_trigger_configs(generation_config(checkout), app_name,
                                               pipeline_name=PIPELINE,
                                               trigger_type=trigger_type)


def generated_deploy_pipeline(checkout, **overrides):
    arguments = dict(PYTHON_GIT)
    arguments.update(overrides)
    return generation.generate_deploy_pipeline(generation_config(checkout), APP, **arguments)


# --- the deploy pipeline ---------------------------------------------------------------------


def test_a_generated_deploy_pipeline_lands_in_the_team_pipeline_directory_and_holds_the_values(
        checkout):
    """One team's pipeline, in its own directory, with every value the two sides can supply filled.

    The directory is what makes the next command work: `setup-pipelines` applies
    `Config.tekton_team_pipeline_dir`, so a generated pipeline that is reviewed and committed is a
    pipeline the team's cluster gets. The template stays where nothing applies it.
    """
    target = generated_deploy_pipeline(checkout)

    assert target == checkout.root / TEAM_PIPELINE_DIR / f"deploy-pipeline-{APP}.yml"
    assert target.is_file()
    document = documents_of(target)[0]
    assert document["kind"] == "Pipeline"
    assert document["metadata"]["name"] == f"deploy-pipeline-{APP}"
    defaults = {parameter["name"]: parameter.get("default")
                for parameter in document["spec"]["params"]}
    assert defaults["app-name"] == APP
    assert defaults["app-git-url"] == APP_GIT["APP_GIT_URL"]
    assert defaults["app-git-project-name"] == APP_GIT["APP_GIT_PROJECT_NAME"]
    assert defaults["app-git-server-host"] == APP_GIT["APP_GIT_SERVER_HOST"]
    assert defaults["app-git-server-port"] == APP_GIT["APP_GIT_SERVER_PORT"]
    assert defaults["app-git-server-ssh-user"] == APP_GIT["APP_GIT_SERVER_SSH_USER"]
    assert defaults["helm-path-to-chart"] == (
        f"/workspace/$(params.automation-git-project-name)/helm_charts/{APP}")
    assert not PLACEHOLDERS_LEFT.search(target.read_text())


def test_the_generated_api_version_is_the_configured_one_and_not_the_templates(checkout):
    """Stamped from `Config.TEKTON_API_VERSION`, so one version bump moves every generated file.

    Both sides read `v1` today, so the visible half of the rule is the second clause: a template
    whose `tekton.dev` apiVersion has gone stale is generated at the version kubementat speaks,
    rather than at the one somebody edited into a file that is never applied.
    """
    template = checkout.root / "tekton_ci/pipelines/templates/template-deploy-pipeline.yml"
    template.write_text(template.read_text().replace("apiVersion: tekton.dev/v1",
                                                     "apiVersion: tekton.dev/v1beta1"))

    document = documents_of(generated_deploy_pipeline(checkout))[0]

    assert document["apiVersion"] == "tekton.dev/v1"


def test_the_ingress_api_version_survives_the_stamp(checkout):
    """The other half of the stamping rule: an Ingress is not a Tekton resource.

    A rule that stamped every `apiVersion` it found would have rewritten an Ingress into
    `tekton.dev/v1` and a TriggerTemplate out of the only version the triggers controller serves.
    That is asserted rather than explained because a blanket rewrite is exactly the kind of
    simplification a later edit would reach for.
    """
    written = generated_trigger_files(checkout)
    documents = {documents_of(path)[0]["kind"]: documents_of(path)[0] for path in written}

    assert documents["Ingress"]["apiVersion"] == "networking.k8s.io/v1"
    assert documents["TriggerTemplate"]["apiVersion"] == "triggers.tekton.dev/v1beta1"
    # ... and the PipelineRun nested inside the TriggerTemplate *is* a Tekton object, so it is stamped.
    assert documents["TriggerTemplate"]["spec"]["resourcetemplates"][0]["apiVersion"] == \
        "tekton.dev/v1"


def test_generating_the_same_pipeline_twice_writes_the_same_bytes(checkout):
    """Idempotence, measured as bytes rather than as "nothing crashed".

    The second run is the one that follows a review, so it has to be a function of the same inputs
    and not of whatever the template looked like when the first run happened. Regeneration is what
    makes a committed deploy pipeline safe to touch again.
    """
    first = generated_deploy_pipeline(checkout)
    expected = first.read_bytes()

    second = generated_deploy_pipeline(checkout)

    assert second == first
    assert second.read_bytes() == expected


def test_a_deploy_pipeline_template_that_is_not_there_says_where_it_reached_for(checkout):
    from kubementat.errors import GenerationTemplateNotFoundError

    with pytest.raises(GenerationTemplateNotFoundError) as error:
        generated_deploy_pipeline(checkout, template_file="no-such-template.yml")

    assert "no-such-template.yml" in str(error.value)
    assert not (checkout.root / TEAM_PIPELINE_DIR).exists()


def test_a_placeholder_the_config_does_not_supply_fails_naming_the_key_and_writes_nothing(
        checkout, tmp_path):
    """The rule that makes this a generator rather than a `sed` with a personality.

    A generated file that still carries a placeholder token is applied and then fails somewhere the
    manifest does not point at, and `null` is worse: it is four characters that parse. So nothing is
    written, and the message names the key and the file it is read from, so the fix
    is a line in `static.json` rather than a hunt through a manifest.
    """
    template = tmp_path / "template-with-a-config-placeholder.yml"
    template.write_text(
        "apiVersion: tekton.dev/v1\n"
        "kind: Pipeline\n"
        "metadata:\n"
        f"  name: deploy-pipeline-{APP}\n"
        "spec:\n"
        "  params:\n"
        "    - name: base-domain\n"
        "      default: BASE_DOMAIN_PLACEHOLDER\n")
    checkout.environment_static(BASE_DOMAIN=None)
    config = generation_config(checkout)

    with pytest.raises(Exception) as error:
        generation.generate_deploy_pipeline(config, APP, template_file=str(template), **PYTHON_GIT)

    assert type(error.value).__name__ == "GenerationPlaceholderNotConfiguredError"
    assert "BASE_DOMAIN_PLACEHOLDER" in str(error.value)
    assert "static.json" in str(error.value)
    assert not (checkout.root / TEAM_PIPELINE_DIR / f"deploy-pipeline-{APP}.yml").exists()


def test_a_missing_git_coordinate_is_refused_by_name_and_writes_nothing(checkout):
    """Those five coordinates belong to the app rather than to the platform, so nothing looks them up.

    The answer names every one that is missing at once, because an operator who forgot two should not
    have to discover them one run at a time.
    """
    from kubementat.errors import GenerationArgumentNotSuppliedError

    with pytest.raises(GenerationArgumentNotSuppliedError) as error:
        generation.generate_deploy_pipeline(generation_config(checkout), APP,
                                            git_url=PYTHON_GIT["git_url"],
                                            git_project_name=PYTHON_GIT["git_project_name"])

    assert "`--git-server-host`" in str(error.value)
    assert "`--git-server-port`" in str(error.value)
    assert not (checkout.root / TEAM_PIPELINE_DIR).exists()


def test_an_app_name_that_would_escape_the_directory_is_refused(checkout):
    """`../other-team` as an app name wrote another team's files, with no rule against it."""
    from kubementat.errors import GenerationArgumentNotSuppliedError

    with pytest.raises(GenerationArgumentNotSuppliedError) as error:
        generation.generate_deploy_pipeline(generation_config(checkout), "../other-team",
                                            **PYTHON_GIT)

    assert "other-team" in str(error.value)
    assert not [path for path in (checkout.root / "platform_config").rglob("deploy-pipeline-*")
                if ".." in str(path)]


# --- the trigger configuration ---------------------------------------------------------------


def test_the_generated_trigger_set_lands_where_the_trigger_reader_looks(checkout):
    """Four manifests, in the team's trigger directory, under the app's own subdirectory.

    The location is the point of the decision: generated trigger configuration is
    team configuration, it lives in `platform_config`, and `setup_triggers` applies exactly this
    directory. Templates and output are never read from the same place — which is also why
    `apply_resources` skips any directory named `templates/`.
    """
    written = generated_trigger_files(checkout)
    directory = checkout.root / TEAM_TRIGGER_DIR / APP

    assert sorted(path.name for path in written) == [
        f"{APP}-github-event-listener-ingress.yml",
        f"{APP}-github-event-listener.yml",
        f"{APP}-github-trigger-binding.yml",
        f"{APP}-github-trigger-template.yml",
    ]
    assert not (checkout.root / OBSOLETE_TRIGGER_DIR).exists(), \
        "the write moved under tekton/; the pre-move directory must not reappear"
    assert all(path.parent == directory for path in written)
    assert all(path.is_file() for path in written)
    for path in written:
        assert not PLACEHOLDERS_LEFT.search(path.read_text()), path

    documents = {documents_of(path)[0]["kind"]: documents_of(path)[0] for path in written}
    assert set(documents) == {"Ingress", "EventListener", "TriggerBinding", "TriggerTemplate"}
    resourcetemplate = documents["TriggerTemplate"]["spec"]["resourcetemplates"][0]
    assert resourcetemplate["spec"]["pipelineRef"]["name"] == PIPELINE
    assert resourcetemplate["metadata"]["namespace"] == "fixture-pipelines"
    assert resourcetemplate["spec"]["taskRunTemplate"]["serviceAccountName"] == \
        "fixture-helm-deployer"
    assert documents["Ingress"]["spec"]["rules"][0]["host"] == \
        f"{APP}-github.trigger-hooks.fixture.hooks.example.test"


def test_generated_trigger_files_are_the_same_bytes_when_generated_twice(checkout):
    """The same rule as the deploy pipeline, asserted separately: four files, twice, byte for byte.

    Overwriting is what makes this the interesting case. The script checked each target file and
    stopped on the first that already existed, having written the ones before it — so the second run
    of a four-file generator left three new files and one old one in a directory that looked fine.
    """
    written = generated_trigger_files(checkout)
    expected = {path: path.read_bytes() for path in written}

    again = generated_trigger_files(checkout)

    assert again == written
    assert {path: path.read_bytes() for path in again} == expected


def test_a_trigger_type_with_no_templates_names_the_resource_and_the_layer_it_came_from(checkout):
    """A trigger type that exists nowhere has to be answered about the installation, not the checkout.

    The message used to read "this checkout has `github`, `gitlab`", which is a sentence a wheel
    cannot make true: there is no checkout, so the diagnosis points at something the operator does
    not have and the directory listing reads as a fact about a tree that is not there. Two things
    replace it — the resource that was looked for, and the layer it would have come from — and the
    listing stays, because "which trigger types exist" is the question that actually gets asked.
    """
    from kubementat.errors import GenerationTemplateNotFoundError

    with pytest.raises(GenerationTemplateNotFoundError) as error:
        generated_trigger_files(checkout, "azure")

    message = str(error.value)
    assert "azure" in message
    assert "github" in message and "gitlab" in message, \
        "the listing answers which trigger types exist, so it is part of the message"
    assert "trigger template" in message, \
        f"the message never names what it looked for: {message}"
    assert "installed" in message, \
        f"the message must name the layer it looked in: {message}"
    assert "this checkout" not in message, \
        f"a wheel has no checkout, and the message says so about one it has: {message}"
    assert not (checkout.root / TEAM_TRIGGER_DIR).exists()


def test_a_trigger_template_with_no_configured_base_domain_names_the_key(checkout):
    """A key that is not configured is an error, not a value.

    An unset `BASE_DOMAIN` otherwise becomes a host of
    `nginx-example-github.trigger-hooks.fixture.null` and an Ingress rule nobody will ever resolve —
    the kind of defect that survives a review because the manifest looks complete.
    """
    checkout.environment_static(BASE_DOMAIN=None)
    config = generation_config(checkout)

    with pytest.raises(Exception) as error:
        generation.generate_trigger_configs(config, APP, pipeline_name=PIPELINE,
                                            trigger_type="github")

    assert type(error.value).__name__ == "GenerationPlaceholderNotConfiguredError"
    assert "BASE_DOMAIN_PLACEHOLDER" in str(error.value)
    assert not (checkout.root / TEAM_TRIGGER_DIR).exists()


def test_a_trigger_set_generated_for_gitlab_does_not_read_the_github_templates(checkout):
    """The trigger type is the directory that is read, and only that directory.

    A wrong-type read here would be a github interceptor inside a gitlab team's webhook, and no
    listing of the output directory would ever show it: the files are named for the app, not for the
    provider whose templates produced them.
    """
    for host in ("gitlab", "github"):
        for name in ("template-event-listener-ingress.yml", "template-event-listener.yml",
                     "template-trigger-binding.yml", "template-trigger-template.yml"):
            (checkout.root / "tekton_ci/triggers/templates" / host / name).write_text(
                "apiVersion: triggers.tekton.dev/v1beta1\n"
                "kind: TriggerTemplate\n"
                "metadata:\n"
                f"  name: APP_NAME_PLACEHOLDER-TEAM_PLACEHOLDER-{host}\n"
                "  annotations:\n"
                f"    read-from: {host}\n")

    written = generated_trigger_files(checkout, "gitlab")

    assert len(written) == 4
    for path in written:
        assert documents_of(path)[0]["metadata"]["annotations"]["read-from"] == "gitlab"


def test_the_command_generates_the_trigger_set_into_the_team_directory(checkout, dry_run_env):
    """The whole reason the library call exists: it is reachable as a command.

    `kmt --help` listing the name is not the same claim as a command that runs, so the CLI is run
    here the way an operator runs it, against the same fixture checkout.
    """
    result = run_kmt(["tekton", "generate-triggers", "dev", "fixture", APP,
                      "--pipeline-name", PIPELINE, "--trigger-type", "github"], dry_run_env)

    assert result.returncode == 0, result.stdout + result.stderr
    directory = checkout.root / TEAM_TRIGGER_DIR / APP
    assert sorted(path.name for path in directory.glob("*.yml")) == [
        f"{APP}-github-event-listener-ingress.yml", f"{APP}-github-event-listener.yml",
        f"{APP}-github-trigger-binding.yml", f"{APP}-github-trigger-template.yml"]
    assert "https://" in result.stdout + result.stderr, \
        "the webhook URL is the operator's next action, and it is logged rather than printed"


def test_the_command_refuses_a_deploy_pipeline_with_no_git_coordinates(checkout, dry_run_env):
    """A missing input is the commonest way this command fails, so the CLI's answer is the library's.

    Exit code and all: a bare traceback is not an answer an operator can act on, and `kmt` is what
    they read.
    """
    result = run_kmt(["tekton", "generate-deploy-pipeline", "dev", "fixture", APP], dry_run_env)

    assert result.returncode != 0
    assert "--git-url" in result.stderr + result.stdout


# --- content parity against the deleted scripts ----------------------------------------------


def test_a_pre_move_trigger_directory_is_refused_and_nothing_is_generated_beside_it(checkout):
    """The tree an older kubementat wrote is refused by name, because only the new path is read.

    Generated triggers moved from `<team>/triggers/` to `<team>/tekton/triggers/`. A tree laid down
    before the move can hold both, and a directory that is read by nothing applies its manifests
    nowhere, silently — so the generator stops, names both paths, and writes nothing into the new one.
    Moving the operator's manifests is a decision, not a side effect of generating, so nothing here
    deletes or renames anything either.
    """
    from kubementat.errors import ObsoleteTriggerDirectoryError

    obsolete = checkout.root / OBSOLETE_TRIGGER_DIR / APP
    obsolete.mkdir(parents=True)
    (obsolete / f"{APP}-github-event-listener.yml").write_text("# laid down before the move\n")

    with pytest.raises(ObsoleteTriggerDirectoryError) as error:
        generated_trigger_files(checkout)

    message = str(error.value)
    assert str(checkout.root / OBSOLETE_TRIGGER_DIR) in message, message
    assert str(checkout.root / TEAM_TRIGGER_DIR) in message, message
    assert not (checkout.root / TEAM_TRIGGER_DIR).exists(), \
        "a refused generation still wrote into the new directory"
    assert obsolete.is_dir(), "refusing is not the same as cleaning up after the operator"


def test_a_generated_file_carries_no_comment_from_its_template(checkout):
    """What the round-trip costs, written down rather than argued about.

    Templates are read as YAML, so their comments do not reach the generated file — and the trigger
    templates hold real review guidance, in a comment, telling whoever generated the file to compare
    the parameters against a real pipeline run. The command says that out loud instead, where a
    later edit to a template cannot silently make it stale.
    """
    written = generated_trigger_files(checkout)
    template_text = (checkout.root / "tekton_ci/triggers/templates/gitlab/"
                     "template-trigger-template.yml").read_text()
    assert "#" in template_text, "the templates do hold comments, which is what this is about"

    assert not any(line.lstrip().startswith("#") for path in written
                   for line in path.read_text().splitlines())


def test_generated_configuration_is_invisible_to_nothing_because_it_is_written_elsewhere():
    """The output moved out of the checkout, so no ignore rule is what keeps it reviewable.

    Generated trigger manifests land under the configuration directory's team layer, and generated deploy
    pipelines under the environment's `tekton/pipelines`, so neither is a path in this tree that
    `git status` could be told to skip. The assertion is that this checkout ignores nothing that could
    hold generated configuration — a rule that hid such a file would be the one thing that could.
    """
    for path in ("tekton_ci/triggers", "tekton_ci/triggers/fixture/x.yml",
                 "tekton_ci/pipeline-runs", "tekton_ci/pipelines"):
        ignored = subprocess.run(["git", "check-ignore", "-q", path], cwd=str(REPO_ROOT))
        assert ignored.returncode == 1, f"{path} would be invisible to a git status"

    assert not (REPO_ROOT / "tekton_ci" / "triggers").exists(), \
        "the generator writes the team layer of the configuration directory, not this checkout"
