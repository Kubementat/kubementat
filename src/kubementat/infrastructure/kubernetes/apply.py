"""The primitive that applies everything a directory actually holds.

This is a module function and not a method, because nothing about the walk needs a class: it reads
files, decides what each document is, and makes one call. A class holding it is what made every
other holder of the same rule reach for that class, and `TektonUtils` — which needs the walk and
not an `Automation` — was paying for it with a deferred import.

`ResourceWithoutNameError` is what this primitive raises when a document cannot be addressed. It
lives in `kubementat.errors` because the refusal outlives this module — `setup_triggers` reuses the
walk and has to be able to name the same failure — while what the refusal says about the file it
found is told here, at the raise.
"""

import logging
from pathlib import Path

import yaml

from kubementat.errors import ResourceWithoutNameError

# Generator templates are not resources. They live in `templates/` directories by convention
# (`tekton_ci/pipelines/templates/`, `tekton_ci/triggers/templates/`) and they carry unresolved
# placeholders, so applying one would create a resource named after a placeholder. Skipping the
# directory is how that stays out of an install while everything else under a resource root is
# walked.
GENERATOR_TEMPLATE_DIRECTORY = "templates"

# Both spellings of the same format, because a team that writes `task.yaml` means `task.yml`.
RESOURCE_FILE_SUFFIXES = (".yml", ".yaml")


def _document_target(resource, path, index, group, version, kind):
    """Where one document belongs, which is a different question for each document now.

    The rule is "the document answers for itself, the caller answers for what it does not say":
    a manifest that says `apiVersion: networking.k8s.io/v1` is sent there whatever the caller
    passed, and a manifest that says nothing is sent where the caller said to. The second half is
    why `kind=None` is now legal — a directory of mixed kinds has no single answer to pass.

    Raises:
        ResourceWithoutNameError: If a core-group document shows up in a directory this applies.
            The custom-objects client addresses `/apis/<group>/<version>/...`; `apiVersion: v1`
            lives under `/api/v1/`, so sending one here is a wrong URL and not a wrong resource.
            That is a real limit, and the message names the alternative instead of failing with a
            serialization error four frames deep.
    """
    api_version = resource.get('apiVersion')
    if api_version:
        declared_group, _, declared_version = str(api_version).rpartition('/')
        if '/' not in str(api_version):
            raise ResourceWithoutNameError(
                f"{path} document {index} declares apiVersion {api_version}, which is the core "
                "API group and cannot be addressed by the client this applies through. Send a "
                "core-group resource (Service, ConfigMap, Secret) with `kubectl apply -f`, or "
                "write a component that owns it."
            )
        group, version = declared_group, declared_version
    resolved_kind = resource.get('kind') or kind
    if not resolved_kind:
        raise ResourceWithoutNameError(
            f"{path} document {index} declares apiVersion {api_version} and no kind, and the "
            f"directory it was read from has no default kind to fall back on. Give the document "
            f"a kind, or apply it somewhere that states one."
        )
    return group, version, resolved_kind


def _resource_files(directory) -> list:
    """Every resource file under `directory`, in a stable order, templates excluded.

    Args:
        directory: A `pathlib.Path` to a resource root.

    Returns:
        list: `Path` objects, sorted by path so the application order is the same twice.
    """
    directory = Path(directory)
    found = [path for path in directory.rglob("*")
             if path.is_file() and path.suffix.lower() in RESOURCE_FILE_SUFFIXES]
    return sorted(path for path in found
                  if GENERATOR_TEMPLATE_DIRECTORY not in path.relative_to(directory).parts[:-1])


def _metadata_name(item) -> str:
    """The name a cluster-scoped object answers with, and an empty string when it has none.

    Written out once because the sweeps that read the cluster off `kubectl get ... -o json` meet
    the same hole three times over: a `metadata` key the API server omitted, and a `name` it may
    not have set. Neither is a reason to raise out of a teardown -- `uninstall` is allowed to meet
    a malformed object it did not write, and its own objects are the ones this cannot happen to.
    """
    return ((item.get("metadata") or {}).get("name")) or ""


def apply_resources(kubernetes_utils, resource_dir, target_namespace, kind, group, version):
    '''
    Apply every Kubernetes resource a directory actually holds.

    This is the primitive Wave 1 and Wave 2 reuse (`setup_pipelines` today, `setup_triggers`
    when it lands), so its blind spots are every caller's blind spots. Three of them were
    the defect this rewrite closes:

    * `.yaml` is the same file format. A tree that ships `task.yml` today is one editor away
      from `task.yaml`, and ignoring the difference meant a resource somebody wrote was
      simply never applied.
    * Subdirectories are not optional. A team that files its tasks under `tasks/ci/` is not
      doing anything strange, so the scan walks the tree.
    * A document has to have a name to be applied. Reading `resource['metadata']['name']` for
      the log line raised `KeyError` on a manifest that was merely malformed, and a
      `KeyError` reads like kubementat's bug rather than the operator's.

    Directories named `templates/` are skipped, deliberately: that is where the generator
    templates sit inside both shipped roots
    (`Config.tekton_pipeline_template_dir`, `Config.tekton_trigger_template_dir`), and they are
    inputs to the generators, not resources. Applying one would create a Pipeline
    called `deploy-pipeline-APP_NAME_PLACEHOLDER`, which is not a pipeline.

    A directory is not one kind of thing. What a team files under `triggers/` is an
    EventListener, a TriggerBinding, a TriggerTemplate and an Ingress, and the primitive that
    applied all four of them as `kind=EventListener` would have sent three of them to an API
    group that does not answer for them. So each document states its own `apiVersion` and
    `kind` — as every real manifest does — and the arguments are what is used for the document
    that does not state them, which is what `kind="Task"` meant before.

    The first argument is the object that answers the one cluster call this makes —
    `create_or_replace_custom_resource` — and it is spelled as that object rather than as a union
    with anything that might hand it over, because the union would be unwrapped on every call and
    buys nothing: a caller already holds its utils object, and a test that wants to watch this
    primitive hands it the recorder it built.

    Parameters:

    - kubernetes_utils: KubernetesUtils - The object that answers the cluster this applies to.
      Every shipped caller hands it the `KubernetesUtils` it built from its own `Context`; a test
      passes its own recorder.
    - resource_dir: str - Directory containing resource files
    - target_namespace: str - Target namespace for resource application
    - kind: str - Kind for the documents that do not declare one; None to require every
      document to name its own
    - group: str - API group for the documents that do not declare one
    - version: str - API version for the documents that do not declare one

    Returns:

    - int - How many documents this directory actually sent. The primitive that applies a
      *layer* reports that number in its own log line, so a caller debugging a collision does
      not have to count log lines to work out how much of a layer landed.

    Raises:

    - FileNotFoundError - If `resource_dir` is not a directory. A path built from a
      configuration value that points nowhere is not the same as a team that has no
      resources yet, and saying "applied nothing" would hide the difference.
    - ResourceWithoutNameError - If a document that should have been applied cannot be named,
      or cannot be addressed at all.
    '''
    logging.info(f"Applying {kind or 'declared'} resources from directory: "
                 f"{resource_dir} in namespace: {target_namespace} ...")

    directory = Path(resource_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"Resource directory does not exist: {resource_dir}")

    applied = 0
    for path in _resource_files(directory):
        logging.info(f"Processing file: {path}")
        for index, resource in enumerate(yaml.safe_load_all(path.read_text()), start=1):
            if resource is None:
                # A stray `---`, or a file that is only comments. Empty is not the same as
                # wrong, and this one is not an error.
                logging.info(f"{path.name} document {index} is empty, skipping")
                continue
            if not isinstance(resource, dict):
                logging.warning(f"{path.name} document {index} is not a resource document, skipping")
                continue
            name = (resource.get('metadata') or {}).get('name')
            if not name:
                raise ResourceWithoutNameError(
                    f"{path} document {index} declares "
                    f"{resource.get('apiVersion', 'unknown apiVersion')} "
                    f"{resource.get('kind', 'unknown kind')} with no metadata.name, so there "
                    f"is nothing to apply. Give it a name, or delete the document if it was "
                    f"never meant to be sent."
                )
            document_group, document_version, document_kind = _document_target(
                resource, path, index, group, version, kind)
            logging.info(f"Resource name: {name} ({document_group or 'core apiVersion'}/"
                         f"{document_version} {document_kind})")
            kubernetes_utils.create_or_replace_custom_resource(
                group=document_group,
                version=document_version,
                namespace=target_namespace,
                kind=document_kind,
                body=resource
            )
            applied += 1

    logging.info(f"Applied {applied} {kind} resource(s) from {resource_dir}")
    return applied
