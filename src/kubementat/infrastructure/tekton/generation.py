"""What a team's own Tekton configuration is made of, and where it is written.

Templates in, one team's manifests out: the deploy pipeline, a webhook trigger set, and a run for
a pipeline that already exists. Nothing here talks to a cluster — which namespace an object lands
in is `run_pipeline`'s decision, made later, and a generator that guessed one would write a
manifest the command that applies it cannot correct. So this module is the split's sink: it imports
`errors` and the standard library and nothing that answers a cluster, and the two edges that point
at it (`runs -> generation`, `triggers -> generation`) are one-way for that reason.

The conversion out of `TektonUtils` is the same one the `Automation` slices used: what a method
read from the instance is a parameter, here one `config`, because every read of the old class that
these functions kept was `Config`. Bodies are otherwise what they were. Two of the names below are
shared with work the other tickets own (`_replace_placeholders_in_dict`, which `run_pipeline` also
substitutes through, and `_refuse_obsolete_team_trigger_dir`, which `triggers` calls too); they are
here because the DAG homes both here, and they are called through this module, never re-imported.
"""

import logging
import random
import re
import string
from pathlib import Path

import yaml

from kubementat import errors


def _refuse_obsolete_team_trigger_dir(config) -> None:
    """Refuse a tree that still has `<team>/triggers/`, naming both directories.

    A no-op when the old directory is absent — which is every tree this HEAD's `config init`
    laid down, because init creates the layer where the property says it lives. Called by the
    reader (`_apply_team_trigger_resources`) and by the writer (`generate_trigger_configs`), the
    two places a stale directory would otherwise be silently ignored. Nothing is moved or
    deleted here: kubementat records no rename table and rewrites no operator tree, so the gap is
    closed by hand and this refusal is what says so.
    """
    obsolete = Path(config.tekton_team_trigger_dir).parent.parent / "triggers"
    if obsolete.is_dir():
        raise errors.ObsoleteTriggerDirectoryError(
            f"{obsolete} is the trigger directory a pre-move kubementat wrote, and nothing reads "
            f"it any more: the team's trigger configuration lives under "
            f"{config.tekton_team_trigger_dir}, the only path generated triggers are applied "
            f"from. Leaving both in place would apply the new directory and silently drop what "
            f"the old one holds, so this run stops instead. Move the manifests you are keeping "
            f"into the new directory by hand and remove the old one. Nothing will do this for you: "
            f"kubementat records no rename table and rewrites no operator tree.")


# The parameters of a generated pipeline run that kubementat can name, and the placeholder each
# one is filled from. They are the set `generate_pipeline_run.sh` hard-coded into its heredoc, and
# what makes them a table rather than a guess is that every key but two is a key of
# `CONFIG_PLACEHOLDER_SOURCES` - so the value arrives through the same walk `run_pipeline` uses and
# a key that stops being configured is an error that names it, not a manifest that says
# `STORAGE_CLASS_PLACEHOLDER` to a cluster. `ENVIRONMENT` and `TEAM` are the two exceptions: they are
# not configuration, they are what the command is run for, and they are supplied through
# `extra_values` exactly as the deploy-pipeline generator supplies the app's git coordinates.
#
# The keys are the parameter names a team's pipelines declare, and the mapping is name-for-name
# rather than derived from the token's own spelling. Deriving it (`automation-git-url` screaming it
# into `AUTOMATION_GIT_URL`) is what would silently start filling a parameter that only looks
# platform-wide - `builder-image` screaming into `BUILDER_IMAGE` is not a thing any `static.json`
# holds - so what is unmapped stays `TODO`, which is the honest answer for a value that belongs to
# the app rather than to the platform.
PIPELINE_RUN_PARAMETER_PLACEHOLDERS = {
    "environment": "ENVIRONMENT",
    "team": "TEAM",
    "docker-registry-base-url": "DOCKER_REGISTRY_BASE_URL",
    "tekton-ci-image-name": "TEKTON_CI_IMAGE_NAME",
    "tekton-ci-image-tag": "TEKTON_CI_IMAGE_TAG",
    "automation-git-project-name": "AUTOMATION_GIT_PROJECT_NAME",
    "automation-git-revision": "AUTOMATION_GIT_REVISION",
    "automation-git-server-host": "AUTOMATION_GIT_SERVER_HOST",
    "automation-git-server-port": "AUTOMATION_GIT_SERVER_PORT",
    "automation-git-server-ssh-user": "AUTOMATION_GIT_SERVER_SSH_USER",
    "automation-git-url": "AUTOMATION_GIT_URL",
}

# The parameter value written when no configuration key answers for it. bash wrote the bare word
# `TODO` and left it there; a generated run is reviewed before it is ever applied, so the honest
# marker is a value that cannot be mistaken for a real one.
PIPELINE_RUN_UNFILLED_PARAMETER = "TODO"

# The three fields a generated pipeline run carries that no configuration key supplies. They are
# written here as named constants rather than inline so that a later ticket that finds (or adds)
# a `static.json` key for them has exactly one place to change - and so that reading this file
# tells the truth about which values are configuration and which are the script's own literals.
PIPELINE_RUN_TIMEOUT = "10m"
PIPELINE_RUN_WORKSPACE_STORAGE = "100Mi"
PIPELINE_RUN_WORKSPACE_ACCESS_MODE = "ReadWriteOnce"

# The suffix on the name of a generated run, and the file extension: `build-pipeline-ci-images` is
# run by `build-pipeline-ci-images-run.yml`, which is what the script wrote and what
# `list_available_pipeline_runs` prints.
PIPELINE_RUN_NAME_SUFFIX = "-run"
PIPELINE_RUN_FILE_SUFFIX = ".yml"


# ---------------------------------------------------------------------------
# The generators: templates in, a team's own configuration out
# ---------------------------------------------------------------------------

# The file the deploy-pipeline generator starts from, read relative to
# `Config.tekton_pipeline_template_dir`. It is the same name the deleted script passed to `cat`,
# kept as a name rather than a path a caller builds, so "the template is not there" is one
# checkable failure instead of a `sed: No such file or directory` and then a half-written file.
DEPLOY_PIPELINE_TEMPLATE_FILE = "template-deploy-pipeline.yml"

# A trigger template named `template-<what>.yml` generates `<app>-<trigger-type>-<what>.yml`, so
# the four trigger files are derived from the four template files rather than spelled out.
# `generate_trigger_config_from_templates.sh` listed them one by one, which meant a fifth template
# was an input that silently produced no output.
TRIGGER_TEMPLATE_FILE_PREFIX = "template-"

# Each `<KEY>_PLACEHOLDER` kubementat fills from configuration, with the config category and the
# key inside it the value comes from. One table for the whole substitution machinery: `run_pipeline`
# substitutes through it and so do both generators, so a placeholder added to a template is added
# here once. A key that is configured nowhere is never quietly dropped — the generators refuse the
# document and say which key. The deleted scripts used `jq -r`, which printed the four characters
# `null` into a manifest and kept writing files.
CONFIG_PLACEHOLDER_SOURCES = {
    "DOCKER_REGISTRY_BASE_URL": ("env_static", "DOCKER_REGISTRY_BASE_URL"),
    "TEKTON_CI_IMAGE_NAME": ("env_static", "TEKTON_CI_IMAGE_NAME"),
    "TEKTON_CI_IMAGE_TAG": ("env_static", "TEKTON_CI_IMAGE_TAG"),
    "STORAGE_CLASS": ("env_static", "TEKTON_KUBERNETES_STORAGE_CLASS"),
    "BASE_DOMAIN": ("env_static", "BASE_DOMAIN"),
    "AUTOMATION_GIT_URL": ("env_static", "AUTOMATION_GIT_URL"),
    "AUTOMATION_GIT_PROJECT_NAME": ("env_static", "AUTOMATION_GIT_PROJECT_NAME"),
    "AUTOMATION_GIT_REVISION": ("env_static", "AUTOMATION_GIT_REVISION"),
    "AUTOMATION_GIT_SERVER_HOST": ("env_static", "AUTOMATION_GIT_SERVER_HOST"),
    "AUTOMATION_GIT_SERVER_PORT": ("env_static", "AUTOMATION_GIT_SERVER_PORT"),
    "AUTOMATION_GIT_SERVER_SSH_USER": ("env_static", "AUTOMATION_GIT_SERVER_SSH_USER"),
    "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": ("team_static", "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME"),
}

# What an unfilled placeholder still looks like in a document that has been substituted — the name,
# because a filled placeholder is gone and an unfilled one is the only thing left to report.
UNFILLED_PLACEHOLDER = re.compile(r"([A-Z][A-Z0-9_]*_PLACEHOLDER)")

# The five git coordinates a deploy pipeline is generated from, and the template placeholder each
# one fills. The deleted script read exactly these out of exported environment variables and died
# when one was unset, so the set is recorded rather than invented; `APP_NAME` is the sixth and is
# the generator's required input rather than an option, because the file name and the pipeline name
# are both built from it.
DEPLOY_PIPELINE_GIT_PLACEHOLDERS = {
    "git_url": "APP_GIT_URL",
    "git_project_name": "APP_GIT_PROJECT_NAME",
    "git_server_host": "APP_GIT_SERVER_HOST",
    "git_server_port": "APP_GIT_SERVER_PORT",
    "git_server_ssh_user": "APP_GIT_SERVER_SSH_USER",
}

# The trigger placeholders that do not come from a `static.json` at all, with the option that
# supplies each. `TEAM` and `ENVIRONMENT` are what the command was run for; `APP_NAME` is the
# EventListener's name, which is the app plus the trigger type, and `PIPELINE_NAME` is the pipeline
# an EventListener is allowed to start.
TRIGGER_CONFIG_PLACEHOLDER_SOURCES = {
    "TEAM": "the team the command is run for",
    "ENVIRONMENT": "the environment the command is run for",
    "APP_NAME": "<app-name>-<trigger-type>, which is how the EventListener and its host are named",
    "PIPELINE_NAME": "the --pipeline-name option",
}



def _stamp_tekton_api_version(node, api_group: str, api_version: str) -> None:
    """Set the `tekton.dev` apiVersion every document holds to the configured one, in place.

    Only the `tekton.dev` group is stamped, and that is the whole rule: an Ingress is not a Tekton
    object (`networking.k8s.io/v1` stays), and the Triggers group is versioned by its own constant
    (`Config.TRIGGERS_API_VERSION`, still `v1beta1`) because Triggers serves no `v1` and stamping
    one onto a TriggerTemplate would be a manifest no controller in this tree answers to. What
    changes is that the version comes from kubementat's configuration instead of from a template
    someone edited, including inside a TriggerTemplate's nested PipelineRun.
    """
    if isinstance(node, dict):
        declared = node.get("apiVersion")
        if isinstance(declared, str) and declared.startswith(f"{api_group}/"):
            node["apiVersion"] = f"{api_group}/{api_version}"
        for value in node.values():
            _stamp_tekton_api_version(value, api_group, api_version)
    elif isinstance(node, list):
        for item in node:
            _stamp_tekton_api_version(item, api_group, api_version)


def _unfilled_placeholders(node, found: set = None) -> set:
    """The placeholder names that survive substitution anywhere in a document, as a set."""
    if found is None:
        found = set()
    if isinstance(node, dict):
        for value in node.values():
            _unfilled_placeholders(value, found)
    elif isinstance(node, list):
        for item in node:
            _unfilled_placeholders(item, found)
    elif isinstance(node, str):
        found.update(match.group(1) for match in UNFILLED_PLACEHOLDER.finditer(node))
    return found


def _trigger_output_file_name(template_path, app_name: str, trigger_type: str) -> str:
    """`template-trigger-binding.yml` of app `nginx` on `gitlab` is `nginx-gitlab-trigger-binding.yml`.

    The name is the other half of the derivation, and it is what a team sees when they list the
    directory — so it stays the same string the deleted script built by hand.
    """
    what = template_path.name
    if what.startswith(TRIGGER_TEMPLATE_FILE_PREFIX):
        what = what[len(TRIGGER_TEMPLATE_FILE_PREFIX):]
    return f"{app_name}-{trigger_type}-{what}"

def _substitute_placeholder_token(data, placeholder_key, placeholder_value) -> None:
    """One placeholder, replaced everywhere in a loaded document, in place.

    A value that is configured but empty is no value: leaving `KEY_PLACEHOLDER` in the document is
    the state the generators' refusal rule reports on, so nothing here substitutes an empty string.
    Non-string values are read from configuration as strings so the substitution can only ever put
    text where text was, and a `22` that arrives as an int is still `22`.
    """
    if not placeholder_value:
        return
    token = f"{placeholder_key}_PLACEHOLDER"
    replacement = str(placeholder_value)
    if isinstance(data, dict):
        for key, value in list(data.items()):
            if isinstance(value, str):
                if token in value:
                    data[key] = value.replace(token, replacement)
            else:
                _substitute_placeholder_token(value, placeholder_key, placeholder_value)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, str):
                if token in item:
                    data[index] = item.replace(token, replacement)
            else:
                _substitute_placeholder_token(item, placeholder_key, placeholder_value)


def _first_ingress_host(documents) -> str:
    """The host the generated Ingress answers for, or None when there is nothing to say.

    The webhook URL the operator has to paste into the repository is the ingress host; the deleted
    script rebuilt it from a format string and so could be wrong about its own output. Reading it
    back out of the generated document cannot be.
    """
    for document in documents:
        if not isinstance(document, dict) or document.get("kind") != "Ingress":
            continue
        for rule in (document.get("spec", {}).get("rules") or []):
            host = rule.get("host") if isinstance(rule, dict) else None
            if isinstance(host, str) and host:
                return host
    return None


def _is_supplied(value) -> bool:
    """Whether a generator input carries a value at all.

    `None` and the empty string are the two ways a value that was never set arrives here, and both
    mean "not supplied" — substituting an empty string into `name: APP_NAME-trigger-binding` would
    create a resource named `-trigger-binding`, which is worse than not generating it.
    """
    return value is not None and not (isinstance(value, str) and value.strip() == "")


def _safe_name_component(value: str, description: str) -> str:
    """A value that is about to become part of a file name, checked before it does.

    Both generators write a path built from the app name. bash interpolated it into a
    `../../../`-relative target with no rule whatsoever, so `../other-team` wrote one team's
    manifests into another team's directory and reported success. The check belongs to the generator
    rather than the CLI because the generator is what builds the path.
    """
    if not isinstance(value, str) or not value.strip():
        raise errors.GenerationArgumentNotSuppliedError(f"{description} was not supplied.")
    value = value.strip()
    if value in (".", "..") or "/" in value or "\\" in value:
        raise errors.GenerationArgumentNotSuppliedError(
            f"{description} is {value!r}, which is not something a file name is allowed to be built "
            f"from: it would write outside the directory the generated configuration belongs in.")
    return value


def _unfilled_source_description(token: str, sources: dict) -> str:
    """One unfilled placeholder, written as the thing plus the place it should have come from.

    The token is `KEY_PLACEHOLDER` and the table is keyed by `KEY`, and the difference is the whole
    message: "nowhere kubementat can see" for a key that is sitting in a `static.json` would be the
    same kind of statement the substitution itself refuses to write.
    """
    key = token.rsplit("_PLACEHOLDER", 1)[0]
    where = sources.get(key)
    return f"{token} (read from {where})" if where else f"{token} (read from nowhere kubementat can see)"


def _write_generation_output(path: Path, documents) -> None:
    """Generated manifests, written as YAML in template key order.

    `sort_keys=False` is the whole point. An alphabetised dump reorders every `spec` in the tree, so
    the diff a reviewer reads after a regeneration is noise and a template edited anywhere shuffles
    every file derived from it; a template's own key order is stable, round-trips through the same
    library, and is what makes running the generator twice produce the same bytes.
    """
    dumped = (yaml.safe_dump(documents[0], default_flow_style=False, sort_keys=False)
              if len(documents) == 1 else
              yaml.safe_dump_all(documents, default_flow_style=False, sort_keys=False))
    path.write_text(dumped)


def generate_deploy_pipeline(config, app_name: str, git_url: str = None,
                             git_project_name: str = None, git_server_host: str = None,
                             git_server_port: str = None, git_server_ssh_user: str = None,
                             template_file: str = None) -> Path:
    """Write one team's deploy pipeline, from the tracked template, into the team's own directory.

    The deleted script did this with `sed` and a list of exported `APP_*` variables. Three
    things change in the version that replaces it:

    * the template is read as YAML and written back as YAML, so a value that needs quoting is
      quoted by the same library that parses it, key order stays the template's own, and
      regenerating the same file twice produces the same bytes;
    * the `tekton.dev` apiVersion is stamped from `Config.TEKTON_API_VERSION` instead of copied
      out of the template, so the version kubementat speaks is one decision made in one place;
    * a placeholder kubementat has no value for ends the run and names the key, before a file
      exists. bash substituted what it had and echoed what was left.

    The output is one team's configuration, so it lands in `Config.tekton_team_pipeline_dir` —
    `platform_config/{environment}/{team}/tekton/pipelines/` — and the template stays where
    `apply_resources` deliberately never looks. Writing into the team directory is also what
    makes the next command work: `setup-pipelines` applies that directory, so a generated
    pipeline that is reviewed and committed is a pipeline the team's cluster picks up.

    A rerun overwrites rather than refusing. The script checked that the target file existed and
    exited, so the second run — the one that happens after a review asked for a change — needed a
    `rm` first. This generator is a function of its template, its configuration and its
    arguments, so it has no reason to be that fragile.

    Raises:
        kubementat.errors.GenerationArgumentNotSuppliedError: If the app name or any of the five git coordinates
            is missing. None of them is read from configuration, so none can be looked up here.
        kubementat.errors.GenerationTemplateNotFoundError: If the template is not in the installed package.
        kubementat.errors.GenerationPlaceholderNotConfiguredError: If the template holds a placeholder with no
            configured value, naming the key and the file it would have been read from.
        click.ClickException: From an unreadable `static.json`.

    Returns:
        Path: the file that was written.
    """
    app_name = _safe_name_component(app_name, "The app name")
    template_path = config.tekton_pipeline_template_dir / (
        template_file or DEPLOY_PIPELINE_TEMPLATE_FILE)
    documents = _load_generation_templates(template_path)

    option_names = {placeholder: f"--{argument.replace('_', '-')}"
                    for argument, placeholder in DEPLOY_PIPELINE_GIT_PLACEHOLDERS.items()}
    option_names["APP_NAME"] = "APP_NAME"
    supplied = {"APP_NAME": app_name,
                "APP_GIT_URL": git_url,
                "APP_GIT_PROJECT_NAME": git_project_name,
                "APP_GIT_SERVER_HOST": git_server_host,
                "APP_GIT_SERVER_PORT": git_server_port,
                "APP_GIT_SERVER_SSH_USER": git_server_ssh_user}
    missing = sorted(key for key, value in supplied.items() if not _is_supplied(value))
    if missing:
        raise errors.GenerationArgumentNotSuppliedError(
            f"Generating a deploy pipeline for {app_name} needs a value for "
            f"{', '.join(f'`{option_names[key]}`' for key in missing)}. Those belong to the app "
            f"repository rather than to the platform, kubementat reads no configuration key for "
            f"them, and the template leaves them as placeholders until they are supplied. For "
            f"example:\n  kmt tekton generate-deploy-pipeline {config.environment} "
            f"{config.team} {app_name} "
            f"--git-url ssh://github.com/julweber/{app_name}.git "
            f"--git-project-name {app_name} --git-server-host github.com "
            f"--git-server-port 22 --git-server-ssh-user git")

    sources = {key: (f"the `{name}` argument" if key == "APP_NAME"
                     else f"the `{name}` option")
               for key, name in option_names.items()}
    values = {key: (value.strip() if isinstance(value, str) else str(value))
              for key, value in supplied.items()}

    _substitute_generation_placeholders(config, documents, values, sources, template_path)
    _stamp_tekton_api_version(documents, config.TEKTON_API_GROUP,
                              config.TEKTON_API_VERSION)

    target_dir = Path(config.tekton_team_pipeline_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"deploy-pipeline-{app_name}.yml"
    logging.info(f"Generating deploy pipeline resource from {template_path.name} ...")
    _write_generation_output(target_file, documents)

    logging.info(f"Finished generating deploy pipeline at {target_file}.")
    logging.info("###################################")
    logging.info("ATTENTION: please adjust and review the generated file carefully. Generating "
                 "it cannot know what the app's database setup and seeding processes need, so "
                 "those parts are left as the template wrote them.")
    logging.info("###################################")
    return target_file

def generate_trigger_configs(config, app_name: str, pipeline_name: str,
                             trigger_type: str) -> list:
    """One app's whole webhook-trigger set, from the templates of one git host, into the team's trigger directory.

    Four documents — an Ingress, an EventListener, a TriggerBinding and a TriggerTemplate —
    generated from the four files under
    `Config.tekton_trigger_template_dir/{trigger_type}/` and written to
    `Config.tekton_team_trigger_dir/{app_name}/`, under `tekton/`. That location is
    the one decision 2 of the migration made: the templates are product assets that ship inside
    the package, the generated files are one team's configuration, and `setup_triggers` reads
    the directory written here from exactly that path. Templates and output are never read from
    the same directory.

    Substitution, apiVersion stamping and the refusal-to-fill-nothing rule are the same as
    `generate_deploy_pipeline`'s, because a `TriggerTemplate` whose `PIPELINE_NAME_PLACEHOLDER`
    survived is a template that starts no pipeline, and the trigger set is the artifact where a
    `jq` miss was likeliest: six of its values are `static.json` reads and the seventh is the
    team's ServiceAccount.

    A rerun overwrites. The script checked each of the four target files and stopped on the
    first that already existed, having already written the ones before it — so a team that ran it
    twice got a directory holding two kinds of manifest and an error that named neither.

    Raises:
        kubementat.errors.GenerationArgumentNotSuppliedError: If the app name or the pipeline name is missing, or
            if either would escape the directory the output goes into.
        kubementat.errors.GenerationTemplateNotFoundError: If `trigger_type` names no template directory, or that
            directory is empty. The trigger types the installed package can generate for are
            listed.
        kubementat.errors.GenerationPlaceholderNotConfiguredError: If a template holds a placeholder with no
            configured value, naming the key and the file it would have been read from.
        click.ClickException: From an unreadable `static.json`.
        kubementat.errors.ObsoleteTriggerDirectoryError: If the tree still has the old
      `<team>/triggers/`
            directory — generating into the new path beside it would strand what the old one
            holds, so the run stops and names both.

    Returns:
        list: the files that were written, in template order.
    """
    _refuse_obsolete_team_trigger_dir(config)
    app_name = _safe_name_component(app_name, "The app name")
    if not _is_supplied(pipeline_name):
        raise errors.GenerationArgumentNotSuppliedError(
            f"Generating the trigger configuration for {app_name} needs a pipeline name: a "
            f"TriggerTemplate that does not know which PipelineRun it may start is a listener "
            f"that answers a webhook and does nothing. Pass `--pipeline-name`, for example "
            f"`--pipeline-name build-{app_name}`.")
    trigger_type = _safe_name_component(trigger_type, "The trigger type")

    template_dir = config.tekton_trigger_template_dir / trigger_type
    if not template_dir.is_dir():
        available = sorted(p.name for p in config.tekton_trigger_template_dir.iterdir()
                           if p.is_dir()) if config.tekton_trigger_template_dir.is_dir() else []
        raise errors.GenerationTemplateNotFoundError(
            f"There are no {trigger_type!r} trigger templates to generate from. "
            f"The trigger type is the directory that is read, and the layer it comes from is the "
            f"trigger templates this kmt was installed with — "
            f"{config.tekton_trigger_template_dir}, the package this command runs from, not "
            f"a checkout. Its trigger types are "
            f"{', '.join(repr(name) for name in available) if available else 'none'}. "
            f"Nothing was generated.")
    templates = sorted(set(template_dir.glob(f"{TRIGGER_TEMPLATE_FILE_PREFIX}*.yml"))
                       | set(template_dir.glob(f"{TRIGGER_TEMPLATE_FILE_PREFIX}*.yaml")))
    if not templates:
        raise errors.GenerationTemplateNotFoundError(
            f"{template_dir} holds no {TRIGGER_TEMPLATE_FILE_PREFIX}*.yml files, so there is "
            f"nothing to generate a {trigger_type!r} trigger set from. A template directory "
            f"that is empty is not a team that has configured its own templates; it is an "
            f"installation that lost them.")

    values = {
        "TEAM": str(config.team),
        "ENVIRONMENT": str(config.environment),
        "APP_NAME": f"{app_name}-{trigger_type}",
        "PIPELINE_NAME": str(pipeline_name),
    }
    # Everything is generated into memory before the first byte is written, so a template that
    # asks for something unconfigured leaves no trace: not a manifest, and not even the empty
    # directory the next `apply_resources` would have walked into.
    generated = []
    for template_path in templates:
        documents = _load_generation_templates(template_path)
        _substitute_generation_placeholders(
            config, documents, values, dict(TRIGGER_CONFIG_PLACEHOLDER_SOURCES), template_path)
        _stamp_tekton_api_version(documents, config.TEKTON_API_GROUP,
                                  config.TEKTON_API_VERSION)
        generated.append((template_path, _trigger_output_file_name(template_path, app_name,
                                                                   trigger_type), documents))

    target_dir = Path(config.tekton_team_trigger_dir) / app_name
    target_dir.mkdir(parents=True, exist_ok=True)
    logging.info(f"Generating trigger configuration for {app_name} "
                 f"({trigger_type}) in {target_dir} ...")
    written = []
    generated_documents = []
    for template_path, file_name, documents in generated:
        target_file = target_dir / file_name
        _write_generation_output(target_file, documents)
        written.append(target_file)
        generated_documents.extend(documents)
        logging.info(f"Finished generating "
                     f"{template_path.name[len(TRIGGER_TEMPLATE_FILE_PREFIX):]} "
                     f"at {target_file}.")

    logging.info("###################################")
    logging.info(f"Generated {len(written)} trigger manifests in {target_dir}:")
    for path in written:
        logging.info(f"  {path}")
    host = _first_ingress_host(generated_documents)
    if host:
        logging.info("")
        logging.info(f"Point the repository's webhook at https://{host} and read the secret to "
                     f"configure from "
                     f"{config.team_static_encrypted_config_path} -> "
                     f"GITLAB_WEBHOOK_SECRET / GITHUB_WEBHOOK_SECRET.")
    logging.info("ATTENTION: please adjust and review the generated files carefully, especially "
                 "the trigger template. kubementat cannot know which pipeline-run parameters "
                 "this app needs, so it wrote what the template held.")
    logging.info("###################################")
    return written

def generate_pipeline_run(config, pipeline_file: str) -> Path:
    """One team's pipeline run, generated from the pipeline it runs.

    `generate_pipeline_run.sh` did this with a heredoc and a `for param in` loop over the
    parameter names `jq` had read out of the pipeline file. What changes in the Python version
    is the shape of the answer, not its content:

    * the apiVersion is stamped from `Config.TEKTON_API_GROUP`/`TEKTON_API_VERSION` instead of
      being written into the middle of a heredoc, so the version kubementat speaks is decided in
      one place and a stale copy cannot survive a version bump;
    * the document is built as data and dumped by `yaml.safe_dump`, so a value that needs
      quoting is quoted by the library that parses it and reruns write the same bytes;
    * a parameter that has no configured source is written as the marker `TODO` rather than as
      an empty string, so the review the command asks for is the review the file needs.

    Where it writes is the part that is not a port. bash wrote `tekton_ci/pipeline-runs/<TEAM>/`,
    which is under `Config.tekton_global_pipeline_run_dir` - the directory
    `list_available_pipeline_runs` scans non-recursively and the one `run_pipeline` only reads
    through a `global/` prefix, so the script's own output was invisible to the command that
    applies it and reachable only by typing its whole path. Generated output is one team's
    configuration, so it goes to `Config.tekton_team_pipeline_run_dir` -
    `platform_config/{environment}/{team}/tekton/pipeline-runs/` - which is where
    `run_pipeline` reads a team's run files from and where `list_available_pipeline_runs` looks
    first. That is the decision the deploy-pipeline generator's move settled, and the
    reason `.gitignore` ignores everything under `tekton_ci/` that is not a template.

    It is pure file and configuration work. Nothing here talks to a cluster: which namespace the
    run eventually lands in is `run_pipeline`'s decision, made from
    `PIPELINE_NAMESPACE` when it applies this file, and this generator neither reads nor writes
    a namespace.

    Args:
        pipeline_file: The pipeline manifest to generate a run for. The path is used as
            supplied, resolved against the working directory, and is named in full when it is
            not there.

    Raises:
        kubementat.errors.GenerationInputNotFoundError: If `pipeline_file` is not a file.
        kubementat.errors.GenerationInputNotUsableError: If it is not a single Pipeline document, declares no
            `metadata.name`, or declares a parameter with no name.
        kubementat.errors.GenerationTargetExistsError: If the file this would write already exists.
        click.ClickException: From an unreadable `static.json`.

    Returns:
        Path: the run file that was written.
    """
    source = Path(pipeline_file)
    if not source.is_file():
        raise errors.GenerationInputNotFoundError(
            f"{source} is not a file, so there is no pipeline to generate a run for. The script "
            f"resolved this argument against the directory it was run from, which is why the "
            f"answer here is the path rather than a suggestion: run "
            f"`kmt tekton setup-pipelines {config.environment} {config.team}` to apply "
            f"the pipelines this kmt ships, and pass the file it installed from.")

    documents = [document for document in yaml.safe_load_all(source.read_text())
                 if document is not None]
    if not documents:
        raise errors.GenerationInputNotUsableError(
            f"{source} holds no YAML document, so there is no pipeline in it to generate a run "
            f"of. Nothing was written.")
    if len(documents) > 1:
        raise errors.GenerationInputNotUsableError(
            f"{source} holds {len(documents)} documents, and a run is generated from exactly one "
            f"Pipeline. Split the file or point the command at one of them. Nothing was written.")
    pipeline = documents[0]
    if not isinstance(pipeline, dict):
        raise errors.GenerationInputNotUsableError(
            f"{source} is a {type(pipeline).__name__}, not a mapping, so it declares no pipeline "
            f"to run. Nothing was written.")

    kind = pipeline.get("kind")
    if kind != "Pipeline":
        raise errors.GenerationInputNotUsableError(
            f"{source} is a {kind}, not a Pipeline, so the run would start something that cannot "
            f"be started as a pipeline. Nothing was written.")
    metadata = pipeline.get("metadata")
    pipeline_name = metadata.get("name") if isinstance(metadata, dict) else None
    if not _is_supplied(pipeline_name):
        raise errors.GenerationInputNotUsableError(
            f"{source} declares no metadata.name. bash wrote `null` into both the run's name and "
            f"its `pipelineRef.name` and printed a success line; this names the file instead, "
            f"because a run of a pipeline called `null` is not a thing to review. "
            f"Nothing was written.")
    pipeline_name = _safe_name_component(pipeline_name, "The pipeline's metadata.name")

    spec = pipeline.get("spec")
    spec = spec if isinstance(spec, dict) else {}
    declared = _declared_pipeline_parameters(spec.get("params"), source)
    workspaces = _declared_pipeline_workspaces(spec.get("workspaces"), source)

    run_name = f"{pipeline_name}{PIPELINE_RUN_NAME_SUFFIX}"
    target_file = Path(config.tekton_team_pipeline_run_dir) / f"{run_name}{PIPELINE_RUN_FILE_SUFFIX}"
    if target_file.exists():
        raise errors.GenerationTargetExistsError(
            f"{target_file} already exists, and kubementat will not overwrite it. A generated "
            f"run is the starting point a team edits - the parameters kubementat cannot know are "
            f"written there for whoever generated it to fill in - so a rerun would delete those "
            f"edits without a word. Review or remove the existing file first.")

    document = _pipeline_run_document(config, pipeline_name, run_name, declared, workspaces)

    # `environment` and `team` are not configuration, they are what the command is run for, so
    # they arrive the way the deploy-pipeline generator's git coordinates do: through `extra_values`
    # rather than through a `static.json` the operator did not edit.
    supplied = {"ENVIRONMENT": str(config.environment), "TEAM": str(config.team)}
    supplied_sources = {"ENVIRONMENT": "the environment the command is run for",
                        "TEAM": "the team the command is run for"}
    # The tokens the document is built with are substituted through the same table-driven walk
    # `run_pipeline` runs when it applies this very file, so a value arrives from `static.json`
    # here once rather than being re-read on every run, and what stays unfilled is refused the way
    # the other generators refuse it. That is the half this ticket exists for: bash wrote
    # `STORAGE_CLASS_PLACEHOLDER` as a literal and never read `Config` for it, so a key that has
    # been renamed or dropped left a run that applied and failed. Here it is an error that names
    # the key and the file it is read from.
    _substitute_generation_placeholders(config, [document], supplied, supplied_sources,
        target_file)

    # Everything is decided before the first byte is written, so a refusal here leaves no
    # half-written run behind the way bash's redirect left a file holding the heredoc's tail.
    target_file.parent.mkdir(parents=True, exist_ok=True)
    _write_generation_output(target_file, [document])

    logging.info(f"Generated pipeline run {target_file} for pipeline '{pipeline_name}' "
                 f"(environment {config.environment}, team {config.team}).")
    unfilled = sorted(name for name, value in declared.items()
                      if value == PIPELINE_RUN_UNFILLED_PARAMETER)
    logging.info("###################################")
    logging.info(f"Run it with: kmt tekton run-pipeline {config.environment} "
                 f"{config.team} {target_file.name}")
    if unfilled:
        logging.info(f"ATTENTION: {len(unfilled)} parameter(s) kubementat has no value for are "
                     f"written as `{PIPELINE_RUN_UNFILLED_PARAMETER}`: "
                     f"{', '.join(f'`{name}' for name in unfilled)}. Adjust them and review the "
                     f"file before running it.")
    else:
        logging.info("ATTENTION: review the generated file before running it. Every parameter "
                     "it declares is read from kubementat's configuration, but whether that is "
                     "what this run should carry is a decision this command cannot make.")
    logging.info("###################################")
    return target_file

def _declared_pipeline_parameters(params, source: Path) -> dict:
    """The pipeline's declared parameters, in declaration order, filled from configuration.

    The `name` of every entry is what a run has to pass, so a parameter that declares no name
    is refused the same way a pipeline that declares no name is: bash would have skipped the
    entry silently and written a run that cannot start.
    """
    declared: dict = {}
    if params is None:
        return declared
    if not isinstance(params, list):
        raise errors.GenerationInputNotUsableError(
            f"{source} declares spec.params as a {type(params).__name__}, not a list, so "
            f"kubementat cannot tell what the pipeline accepts. Nothing was written.")
    for entry in params:
        if not isinstance(entry, dict):
            raise errors.GenerationInputNotUsableError(
                f"{source} holds a parameter written as {entry!r}, which is not a mapping with a "
                f"name, so a run generated from it would name a parameter `None`. "
                f"Nothing was written.")
        name = entry.get("name")
        if not _is_supplied(name):
            raise errors.GenerationInputNotUsableError(
                f"{source} declares a parameter with no name. A run that passes a parameter named "
                f"`null` is a run the pipeline rejects; nothing was written.")
        name = str(name)
        declared[name] = _pipeline_run_parameter_value(name)
    return declared

def _pipeline_run_parameter_value(name: str) -> str:
    """The value a generated run starts with for one parameter of the pipeline it runs.

    A parameter kubementat can name is a key of `PIPELINE_RUN_PARAMETER_PLACEHOLDERS` and is
    written as that placeholder token, to be substituted by the same table-driven walk
    `_substitute_generation_placeholders` runs. That indirection is what keeps this honest: the
    value is `static.json`'s and `Config`'s, and writing the token is what makes the substitution
    refuse rather than silently write `None` when the key stops being configured.

    What has no key is written as `TODO`, which is the marker bash left and the reason the command
    asks for a review: those parameters belong to the app rather than to the platform, and a
    generated run that invented a value for `path-to-version-file` would be a run that silently
    builds the wrong thing.
    """
    placeholder = PIPELINE_RUN_PARAMETER_PLACEHOLDERS.get(name)
    return f"{placeholder}_PLACEHOLDER" if placeholder else PIPELINE_RUN_UNFILLED_PARAMETER

def _declared_pipeline_workspaces(workspaces, source: Path) -> list:
    """The workspace names the generated run must bind, in declaration order.

    bash always bound one workspace named `pipeline-workspace`, whatever the pipeline declared,
    so a run of a pipeline with no workspaces at all (every Task-only pipeline in
    `tekton_ci/pipelines/`) declared a binding the pipeline never asked for, and a pipeline that
    declared two got one. The names come from the source now; what does not change is that each
    is a `volumeClaimTemplate`, because that is the only binding this generator can write without
    knowing what the tasks read.
    """
    if workspaces is None:
        return []
    if not isinstance(workspaces, list):
        raise errors.GenerationInputNotUsableError(
            f"{source} declares spec.workspaces as a {type(workspaces).__name__}, not a list, so "
            f"kubementat cannot tell what the pipeline needs mounted. Nothing was written.")
    names = []
    for entry in workspaces:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not _is_supplied(name):
            raise errors.GenerationInputNotUsableError(
                f"{source} declares a workspace with no name, so a generated run could not bind "
                f"it. Nothing was written.")
        names.append(str(name))
    return names

def _pipeline_run_document(config, pipeline_name: str, run_name: str, declared: dict,
                           workspaces: list) -> dict:
    """The generated `PipelineRun` itself, as data.

    The shape is Tekton Pipelines v1, which is why `serviceAccountName` sits under
    `taskRunTemplate` and the timeout under `timeouts.pipeline`: both moved in v1, and the
    apiVersion they are stamped with is the one `Config` declares rather than one copied out of
    a heredoc.
    """
    spec: dict = {
        # Deploying into another namespace than the pipeline's own is what the helm deploy task
        # does, so the run asks for the account the install created for it, not a default.
        "taskRunTemplate": {
            "serviceAccountName": "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME_PLACEHOLDER",
        },
        "pipelineRef": {"name": pipeline_name},
        "timeouts": {"pipeline": PIPELINE_RUN_TIMEOUT},
    }
    if workspaces:
        spec["workspaces"] = [{
            "name": name,
            "volumeClaimTemplate": {
                "spec": {
                    "storageClassName": "STORAGE_CLASS_PLACEHOLDER",
                    "accessModes": [PIPELINE_RUN_WORKSPACE_ACCESS_MODE],
                    "resources": {"requests": {"storage": PIPELINE_RUN_WORKSPACE_STORAGE}},
                },
            },
        } for name in workspaces]
    spec["params"] = [{"name": name, "value": value} for name, value in declared.items()]

    document = {
        "apiVersion": f"{config.TEKTON_API_GROUP}/{config.TEKTON_API_VERSION}",
        "kind": "PipelineRun",
        "metadata": {"name": run_name},
        "spec": spec,
    }
    return document

def _configuration_placeholder_values(config) -> dict:
    """The substitution table's configuration half: every `KEY_PLACEHOLDER` a `static.json` answers for."""
    values = {}
    for placeholder, (category, key) in CONFIG_PLACEHOLDER_SOURCES.items():
        values[placeholder] = config.get(category).get(key)
    return values

def _placeholder_sources(config) -> dict:
    """Where each of those values is read from, spelled from the same table so the message cannot lie.

    A placeholder that is configured nowhere still needs an answer in the error message, and the
    answer that is worth reading is a path and a key rather than the name of a Python constant.
    """
    paths = {"env_static": config.env_static_config_path,
             "team_static": config.team_static_config_path}
    return {placeholder: f"{paths[category]} -> {key}"
            for placeholder, (category, key) in CONFIG_PLACEHOLDER_SOURCES.items()}

def _load_generation_templates(template_path) -> list:
    """A template file, loaded as documents. The failure here is about the installation, not the run.

    bash `cat`ed a `../../../`-relative path, so the answer depended on where the operator was
    standing and a miss produced an empty file plus a cheerful message. A path from `Config`, an
    existence check and a parse error that quotes itself make each of those states say what it is.
    """
    if not template_path.is_file():
        raise errors.GenerationTemplateNotFoundError(
            f"{template_path} is not here, so there is nothing to generate from. A template that "
            f"is not in the installed package is an installation problem, not a configuration "
            f"problem: the generator templates are product files, not team files.")
    try:
        documents = [document for document in
                     yaml.safe_load_all(template_path.read_text()) if document is not None]
    except yaml.YAMLError as error:
        raise errors.GenerationTemplateNotFoundError(
            f"{template_path} is not valid YAML, so kubementat cannot substitute into it: "
            f"{error}") from error
    if not documents:
        raise errors.GenerationTemplateNotFoundError(f"{template_path} holds no YAML document at all.")
    return documents

def _substitute_generation_placeholders(config, documents, supplied_values, supplied_sources,
                                        template_path) -> None:
    """Substitute the configuration table and the caller's values, then refuse what is unfilled.

    The substitution itself is the method `run_pipeline` already uses — same table, same walk,
    so a placeholder that works in a pipeline-run works here. The refusal is the part a generator
    needs and a run does not: writing a document that still holds `TEAM_PLACEHOLDER` is writing
    a resource named for its own placeholder, which bash did without comment.
    """
    values = _configuration_placeholder_values(config)
    values.update({key: value for key, value in supplied_values.items() if _is_supplied(value)})
    sources = {**_placeholder_sources(config), **supplied_sources}
    for document in documents:
        _replace_placeholders_in_dict(config, document, extra_values=values)
        unfilled = sorted(_unfilled_placeholders(document))
        if unfilled:
            named = ", ".join(_unfilled_source_description(placeholder, sources)
                              for placeholder in unfilled)
            raise errors.GenerationPlaceholderNotConfiguredError(
                f"{template_path.name} holds {named}, and kubementat has no value for "
                f"{'it' if len(unfilled) == 1 else 'them'}. Nothing was written, because a "
                f"manifest with a placeholder in it is a resource that carries the placeholder's "
                f"own name.")


def generate_random_postfix(length: int = 6) -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length)).lower()


# helper method for run_pipeline and both generators: substitute a placeholder set, in place.
def _replace_placeholders_in_dict(config, data, extra_values=None) -> None:
    # TODO: #REFACTOR: refactor this massively :D
    # The table is one dict read from `CONFIG_PLACEHOLDER_SOURCES`, which is the same table the
    # generators substitute through. What is not in the table is not substituted, and a
    # placeholder left behind is reported by name rather than written out.
    placeholder_values = _configuration_placeholder_values(config)
    if extra_values:
        placeholder_values.update(extra_values)
    # Longest key first, so a key that is a prefix of another cannot eat part of it.
    for placeholder_key in sorted(placeholder_values, key=len, reverse=True):
        _substitute_placeholder_token(data, placeholder_key, placeholder_values[placeholder_key])
