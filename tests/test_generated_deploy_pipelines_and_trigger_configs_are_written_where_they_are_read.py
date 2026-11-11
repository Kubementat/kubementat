"""Parity: the two generator scripts against `generation.generate_deploy_pipeline` and
`generation.generate_trigger_configs`.

Neither script had a caller. `grep -rl generate_deploy_pipeline_from_template src tests/e2e
tests/*.py tekton_ci README.md` answers nothing, and neither does the same search for the trigger
one, so both were reachable only by an operator who knew the file name and which directory to stand
in — the `../../../tekton_ci/...` paths the scripts resolved against themselves. Making them `kmt`
commands is therefore the migration and not decoration: a library call nobody can reach is how a
thing quietly stops getting done.

Both halves of the comparison disagree about where the output goes. The deploy-pipeline script
wrote `tekton_ci/pipelines/deploy-pipeline-<app>.yml`, beside its own template; the Python half
writes `platform_config/{environment}/{team}/tekton/pipelines/`, beside everything else the team
owns and where `setup-pipelines` looks. The trigger script had been moved into
`platform_config/{environment}/{team}/triggers/` when the reader moved, and a later move took the
Python half one directory deeper, to `platform_config/{environment}/{team}/tekton/triggers/`,
where `Config.tekton_team_trigger_dir` and `setup_triggers` agree it lives — so the comparison
here is over names and values, and the directories are asserted separately as the record and the
rule.

What the round-trip costs is written down rather than argued about: a template is not a text
substitution any more, so the review guidance the templates leave in comments does not reach the
generated file, and the command says it out loud instead.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT, run_bash, run_kmt
from asset_paths import relocate_asset_root, tekton_ci_path
from cluster_fixture import configuration_only_context
from kubementat.infrastructure.tekton import generation

BLOB_REVISION = "07368d4"
GENERATOR_SCRIPTS = {
    "deploy_pipeline": "scripts/automation/tekton/generate_deploy_pipeline_from_template.sh",
    "triggers": "scripts/automation/tekton/generate_trigger_config_from_templates.sh",
}

APP = "nginx-example"
PIPELINE = "build-nginx-example"

# The app's git coordinates, in both halves' terms: bash's exported variables, Python's options.
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

# Where each half writes, spelled once. The script's targets are a record; Python's are the rule.
# The trigger row has two entries because the rule moved under `tekton/` and the record is
# what it is: the script in git history still writes the pre-move directory.
SCRIPT_DEPLOY_OUTPUT = "tekton_ci/pipelines"
SCRIPT_TRIGGER_DIR = "platform_config/dev/fixture/triggers"
TEAM_TRIGGER_DIR = "platform_config/dev/fixture/tekton/triggers"
TEAM_PIPELINE_DIR = "platform_config/dev/fixture/tekton/pipelines"


def deleted_script(name):
    """The script's own text, out of git history. Skips, loudly, if the history is missing."""
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{GENERATOR_SCRIPTS[name]}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{BLOB_REVISION}:{GENERATOR_SCRIPTS[name]} exited {result.returncode} "
                    f"({result.stderr.strip()}). This is a comparison, and without the bash side "
                    f"there is nothing to compare it against.")
    return result.stdout


@pytest.fixture
def checkout(fixture_repo, dry_run_env, monkeypatch):
    """The fixture checkout, with the generator templates added back.

    `fixture_repo` seeds scripts and configuration and no `tekton_ci` templates, but both halves of
    this file read them — bash from `../../../tekton_ci/`, Python from
    `Config.tekton_pipeline_template_dir`. They are tracked product assets, so the fixture uses the
    real ones; a template invented for the test would be a template nobody generates from.

    `BASE_DOMAIN` is set here rather than in `conftest.ENVIRONMENT_STATIC` because it is the trigger
    generator's own read, and a fixture that always had it could never show what happens when it
    is missing.
    """
    # The bytes come from where the installed kubementat carries them, and they are laid
    # down in the fixture checkout so that both halves can be pointed at them: bash through its
    # `../../../`, Python through the relocated resolver below. Without the relocation the generator
    # would read the real package and every one of this file's "the template was edited" rows would
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


def run_generator_script(checkout, which, variables):
    """The script's text, placed where it can resolve its `../../../` paths, then run.

    The blob is written back into the fixture checkout rather than run out of history, because the
    script resolves every path against its own location, and the checkout is what that points at.
    """
    target = (checkout.scripts / "automation" / "tekton" /
              GENERATOR_SCRIPTS[which].rsplit("/", 1)[-1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(deleted_script(which))
    environment = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    environment.update({key: str(value) for key, value in variables.items()})
    return run_bash(target, cwd=target.parent, env=environment)


def run_deploy_script(checkout):
    """`generate_deploy_pipeline_from_template.sh`, with the app's coordinates exported."""
    return run_generator_script(checkout, "deploy_pipeline", APP_GIT)


def run_trigger_script(checkout, trigger_type="github"):
    """`generate_trigger_config_from_templates.sh`, with its five exports.

    The script reads nothing besides those five variables and `jq` out of two `static.json` files,
    so the export list is the whole of its input — which is exactly why it was reachable only by
    somebody who had memorised it.
    """
    return run_generator_script(checkout, "triggers", {
        "ENVIRONMENT": checkout.environment, "TEAM": checkout.team, "APP_NAME": APP,
        "PIPELINE_NAME": PIPELINE, "TRIGGER_TYPE": trigger_type})


def documents_of(path):
    """The documents of one manifest file, empty ones dropped the way `apply_resources` drops them."""
    return [document for document in yaml.safe_load_all(Path(path).read_text()) if document is not None]


def everything_under(directory):
    """Every document in every manifest below `directory`, keyed by file name."""
    return {path.name: document
            for path in sorted(Path(directory).rglob("*.y*ml"))
            for document in documents_of(path)}


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

    bash could not have promised this: it substituted text, so an unrelated whitespace edit to a
    template changed the output, and it refused to run a second time at all. The second run is the
    one that follows a review, so it is the run that has to be a function of the same inputs.
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

    bash `cat | sed`ed: a variable that was never exported left `APP_NAME_PLACEHOLDER` in the file,
    and a `jq -r` miss left the four characters `null`. Both wrote the file and reported success.
    Here nothing is written, and the message names the key and the file it is read from, so the fix
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

    bash answered `APP_GIT_SERVER_HOST is not defined` and then a usage block. The answer here names
    every one that is missing at once, because an operator who forgot two should not have to
    discover them one run at a time.
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
    assert not (checkout.root / SCRIPT_TRIGGER_DIR).exists(), \
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
    """The six `jq` reads of `static.json`, replaced by a decision: a missing key is an error.

    bash turned an unset `BASE_DOMAIN` into a host of `nginx-example-github.trigger-hooks.fixture.null`
    and an Ingress rule nobody would ever resolve, because `jq -r` prints `null` and `sed` substitutes
    what it is given.
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

    bash built the four template paths by interpolating `${TRIGGER_TYPE}` into one prefix, so a typo
    failed on the first `cat` — a wrong-type read here would be a github interceptor in a gitlab
    team's webhook, which no listing of the output would ever show.
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

    Exit code and all: a bare traceback would be the answer bash gave, and `kmt` is what an operator
    reads.
    """
    result = run_kmt(["tekton", "generate-deploy-pipeline", "dev", "fixture", APP], dry_run_env)

    assert result.returncode != 0
    assert "--git-url" in result.stderr + result.stdout


# --- content parity against the deleted scripts ----------------------------------------------


def test_the_generated_deploy_pipeline_holds_the_same_values_the_script_substituted(checkout):
    """Same template, same values, same data — bash's `sed` line for line against YAML substitution.

    The two sides disagree about where they write: bash's `tekton_ci/pipelines/`, beside the
    template; Python's `platform_config/{environment}/{team}/tekton/pipelines/`, beside everything
    the team owns and where `setup-pipelines` looks. That difference is asserted rather than
    smoothed over — the script's target is the record, `Config.tekton_team_pipeline_dir` is the rule.
    """
    script_run = run_deploy_script(checkout)
    assert script_run.returncode == 0, script_run.stdout + script_run.stderr
    script_target = checkout.root / SCRIPT_DEPLOY_OUTPUT / f"deploy-pipeline-{APP}.yml"
    assert script_target.is_file(), "the script's record writes beside its template"

    python_target = generated_deploy_pipeline(checkout)

    assert python_target != script_target
    assert documents_of(script_target) == documents_of(python_target)


def test_the_generated_trigger_files_are_named_as_the_script_named_them(checkout):
    """Four files, four names, identical strings — bash's hand-built names against the derivation.

    The names are what a team reads back when it lists the directory, so they are compared exactly
    rather than counted. It is also the test that pins the derivation: a
    `template-trigger-binding.yml` can generate `<app>-<trigger-type>-trigger-binding.yml` and
    nothing else, which is what bash spelled out four separate times.

    Python runs first: the script's record writes the pre-move directory, and the generator
    refuses a tree that has one (tested below), so script-first would be comparing against a
    refusal instead of against the record.
    """
    generated = generated_trigger_files(checkout)

    script_run = run_trigger_script(checkout)
    assert script_run.returncode == 0, script_run.stdout + script_run.stderr

    script_directory = checkout.root / SCRIPT_TRIGGER_DIR / APP
    script_names = {path.name for path in script_directory.glob("*.yml")}

    assert script_names, "the script's record wrote nothing into the fixture checkout"
    assert script_names == {path.name for path in generated}
    assert script_names == {f"{APP}-github-{what}.yml" for what in
                            ("event-listener-ingress", "event-listener", "trigger-binding",
                             "trigger-template")}
    # The one thing the two halves no longer share: the directory. The record writes the pre-move
    # path and the rule writes under `tekton/`, and the difference is asserted, not smoothed over.
    assert all(path.parent == checkout.root / TEAM_TRIGGER_DIR / APP for path in generated)
    assert (checkout.root / TEAM_TRIGGER_DIR / APP) != script_directory


def test_the_generated_trigger_files_hold_the_same_values_the_script_substituted(checkout):
    """Every value the script read from `static.json` with `jq`, plus the five it read from exports.

    Compared as parsed documents, because that is the comparison a name match cannot fake: the
    EventListener's binding reference, the ingress host, the service account and the storage class
    all have to agree before the four documents do.

    Same order as the name comparison: Python first, because the script's own output directory is
    the pre-move path the generator refuses.
    """
    generated = generated_trigger_files(checkout)

    script_run = run_trigger_script(checkout)
    assert script_run.returncode == 0, script_run.stdout + script_run.stderr

    bash_side = everything_under(checkout.root / SCRIPT_TRIGGER_DIR / APP)
    python_side = {path.name: documents_of(path)[0] for path in generated}

    assert set(bash_side) == set(python_side)
    for name, document in sorted(bash_side.items()):
        assert document == python_side[name], name


def test_a_tree_the_script_wrote_is_refused_by_name_and_not_generated_beside(checkout):
    """The pre-move directory is what the record itself produces — so the record is the fixture.

    The deleted script wrote `platform_config/{environment}/{team}/triggers/`; that directory is
    exactly the tree this move makes obsolete. Generating beside it would strand the manifests it
    holds — only `tekton/triggers/` is ever read — so the generator refuses instead, names both
    paths, and writes nothing into the new one.
    """
    from kubementat.errors import ObsoleteTriggerDirectoryError

    script_run = run_trigger_script(checkout)
    assert script_run.returncode == 0, script_run.stdout + script_run.stderr
    assert (checkout.root / SCRIPT_TRIGGER_DIR / APP).is_dir(), \
        "the script's record is the pre-move tree this test is about"

    with pytest.raises(ObsoleteTriggerDirectoryError) as error:
        generated_trigger_files(checkout)

    message = str(error.value)
    assert SCRIPT_TRIGGER_DIR in message, message
    assert TEAM_TRIGGER_DIR in message, message
    assert not (checkout.root / TEAM_TRIGGER_DIR).exists(), \
        "a refused generation still wrote into the new directory"


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
