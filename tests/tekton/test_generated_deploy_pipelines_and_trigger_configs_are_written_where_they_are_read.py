"""Generated deploy pipelines and trigger configs land where the commands that read them look."""


import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from asset_paths import relocate_asset_root, tekton_ci_path
from cluster_fixture import configuration_only_context
from asset_paths import REPO_ROOT
from conftest import run_kmt

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


OBSOLETE_TRIGGER_DIR = "platform_config/dev/fixture/triggers"
TEAM_TRIGGER_DIR = "platform_config/dev/fixture/tekton/triggers"
TEAM_PIPELINE_DIR = "platform_config/dev/fixture/tekton/pipelines"


@pytest.fixture
def checkout(fixture_repo, dry_run_env, monkeypatch):
    """The fixture checkout, with the generator templates added back."""
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
    """The configuration the two generators read, with no `TektonUtils` in front of it."""
    return configuration_only_context(checkout.environment, checkout.team).config


def documents_of(path):
    """The documents of one manifest file, empty ones dropped the way `apply_resources` drops them.
    """
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


def test_the_generated_api_version_is_the_configured_one_and_not_the_templates(checkout):
    """Stamped from `Config.TEKTON_API_VERSION`, so one version bump moves every generated file."""
    template = checkout.root / "tekton_ci/pipelines/templates/template-deploy-pipeline.yml"
    template.write_text(template.read_text().replace("apiVersion: tekton.dev/v1",
                                                     "apiVersion: tekton.dev/v1beta1"))

    document = documents_of(generated_deploy_pipeline(checkout))[0]

    assert document["apiVersion"] == "tekton.dev/v1"


def test_generating_the_same_pipeline_twice_writes_the_same_bytes(checkout):
    """Idempotence, measured as bytes rather than as "nothing crashed"."""
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
    """The rule that makes this a generator rather than a `sed` with a personality."""
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


def test_generated_trigger_files_are_the_same_bytes_when_generated_twice(checkout):
    """The same rule as the deploy pipeline, asserted separately: four files, twice, byte for byte.
    """
    written = generated_trigger_files(checkout)
    expected = {path: path.read_bytes() for path in written}

    again = generated_trigger_files(checkout)

    assert again == written
    assert {path: path.read_bytes() for path in again} == expected


def test_a_trigger_type_with_no_templates_names_the_resource_and_the_layer_it_came_from(checkout):
    """A trigger type that exists nowhere has to be answered about the installation, not the checkout.
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


def test_a_trigger_set_generated_for_gitlab_does_not_read_the_github_templates(checkout):
    """The trigger type is the directory that is read, and only that directory."""
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
    """The whole reason the library call exists: it is reachable as a command."""
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
    """
    result = run_kmt(["tekton", "generate-deploy-pipeline", "dev", "fixture", APP], dry_run_env)

    assert result.returncode != 0
    assert "--git-url" in result.stderr + result.stdout


# --- generated content ----------------------------------------------------------------------


def test_a_pre_move_trigger_directory_is_refused_and_nothing_is_generated_beside_it(checkout):
    """The tree an older kubementat wrote is refused by name, because only the new path is read."""
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


def test_generated_configuration_is_invisible_to_nothing_because_it_is_written_elsewhere():
    """The output moved out of the checkout, so no ignore rule is what keeps it reviewable."""
    for path in ("tekton_ci/triggers", "tekton_ci/triggers/fixture/x.yml",
                 "tekton_ci/pipeline-runs", "tekton_ci/pipelines"):
        ignored = subprocess.run(["git", "check-ignore", "-q", path], cwd=str(REPO_ROOT))
        assert ignored.returncode == 1, f"{path} would be invisible to a git status"

    assert not (REPO_ROOT / "tekton_ci" / "triggers").exists(), \
        "the generator writes the team layer of the configuration directory, not this checkout"
