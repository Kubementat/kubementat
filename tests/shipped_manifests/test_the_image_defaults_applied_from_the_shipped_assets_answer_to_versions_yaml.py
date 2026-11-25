"""The image tags `setup_pipelines` applies come from `versions.yaml`, not from the asset files.

The shipped Task and Pipeline documents name their images and carry the file's current answer as
the literal; the render at apply time is what makes the file the owner. The cases here read the
real shipped assets — and, for the apply-pass case, apply them to a recorder — because what is
under proof is that the documents reaching the cluster answer to `versions.yaml`.
"""

import yaml

from asset_paths import tekton_ci_path
from kubementat.automations.install import pipelines as pipelines_slice
from kubementat.core import versions
from kubementat.infrastructure.kubernetes import apply


def shipped_documents():
    """Every document in the product's Task and Pipeline assets, the way `apply_resources` reads them.

    `templates/` is generator input and never applied, so it carries no `default:` to render.
    """
    for root in (tekton_ci_path("tasks"), tekton_ci_path("pipelines")):
        assert root.is_dir(), f"{root} is where the product's manifests ship"
        for path in sorted(p for p in root.rglob("*") if p.suffix.lower() in (".yml", ".yaml")):
            if "templates" in path.relative_to(root).parts:
                continue
            for document in yaml.safe_load_all(path.read_text()):
                if isinstance(document, dict):
                    yield path, document


def param_defaults(document):
    """The `(name, default)` of every parameter a document declares."""
    for param in ((document.get("spec") or {}).get("params") or []):
        if isinstance(param, dict) and "default" in param:
            yield param["name"], param["default"]


def image_name(value):
    """The image name of a reference string, without the tag — the same split the renderer makes.

    A value that is not an image reference (`docker-registry-secret`, `$(params.x)`, a URL) answers
    with itself, so the substitution table can only match names it actually owns.
    """
    if not isinstance(value, str):
        return value
    name, separator, tag = value.rpartition(":")
    return name if separator and "/" not in tag else value


def test_the_image_default_of_every_shipped_asset_is_the_versions_files_answer():
    """Render the real assets and read every image default back: it equals `image_ref`'s answer.

    The substitution table says which images the shipped documents name; this proves the render
    fills every one of them with the file's current answer. A name the file owns that no shipped
    document uses would make the proof vacuous, so the census is part of the assertion.
    """
    owned = dict(pipelines_slice._ASSET_IMAGE_VERSION_KEYS)
    seen = {key: 0 for key in owned.values()}

    for path, document in shipped_documents():
        for _name, default in param_defaults(pipelines_slice._render_image_defaults(document)):
            if not isinstance(default, str):
                continue
            key = owned.get(image_name(default))
            if key is None:
                continue
            assert default == versions.image_ref(key), (
                f"{path} still carries its own tag: {default!r}, expected "
                f"{versions.image_ref(key)!r}"
            )
            seen[key] += 1

    unused = sorted(key for key, count in seen.items() if count == 0)
    assert unused == [], (
        f"the substitution table names images no shipped document uses ({unused}), so no default "
        "exercises their render — move the table row with the manifest that names the image"
    )


def test_an_image_the_versions_file_does_not_own_is_applied_as_the_asset_wrote_it():
    """`ubuntu-ci-minimal:latest` is owned by the `TEKTON_CI_IMAGE_*` knobs, not by the file.

    The render rewrites what the block owns and nothing else: the CI image keeps its tag, because
    re-tagging a knob's own image would make the asset a second owner of a fact the configuration
    layers already answer.
    """
    documents = [pipelines_slice._render_image_defaults(document)
                 for document in yaml.safe_load_all(
                     tekton_ci_path("tasks", "build-and-push-docker-image.yml").read_text())
                 if isinstance(document, dict)]
    defaults = dict(param_defaults(documents[0]))

    assert defaults["ci-image"] == "julianweberdev/ubuntu-ci-minimal:latest"


def test_the_documents_the_apply_passes_on_carry_the_versions_files_answer():
    """The render happens on the documents that get applied, not as a reading exercise.

    One directory of the real shipped Pipelines goes through `apply_resources` with the product
    layer's renderer into a recorder, and every `builder-image` default that arrives is the file's
    answer — which is what a cluster sees after `kmt install`.
    """
    applied = []

    class Recorder:
        def create_or_replace_custom_resource(self, group, version, namespace, kind, body,
                                              plural_identifier=None):
            applied.append(body)

    applied_count = apply.apply_resources(
        Recorder(), str(tekton_ci_path("pipelines")), "tekton-pipelines",
        kind="Pipeline", group="tekton.dev", version="v1",
        render=pipelines_slice._render_image_defaults)

    builder_defaults = [default
                        for body in applied
                        for name, default in param_defaults(body)
                        if name == "builder-image"]
    assert builder_defaults, (
        f"{applied_count} documents were applied and none named a builder-image, "
        "so this proves nothing"
    )
    assert all(default == versions.image_ref("buildkit") for default in builder_defaults), (
        f"a document reached the recorder with its own tag: {builder_defaults}"
    )


def test_a_new_versions_file_key_reaches_the_default_without_an_asset_edit(monkeypatch):
    """The substitution table is how a shipped image finds its `versions.yaml` key.

    Simulated, not written into the file: the table gains one name, the reader answers the new
    key, and a document that was written before the key existed renders to the file's answer on
    the next apply. A name the table does not name is left alone in the same breath.
    """
    document = {
        "apiVersion": "tekton.dev/v1",
        "kind": "Task",
        "metadata": {"name": "example-task"},
        "spec": {"params": [
            {"name": "image", "default": "example.org/tool:v1"},
            {"name": "other", "default": "example.org/untouched:1.0"},
        ]},
    }
    real = versions.image_ref
    monkeypatch.setattr(pipelines_slice.versions, "image_ref",
                        lambda key: "example.org/tool:9.9" if key == "tool" else real(key))
    monkeypatch.setattr(pipelines_slice, "_ASSET_IMAGE_VERSION_KEYS",
                        {**pipelines_slice._ASSET_IMAGE_VERSION_KEYS, "example.org/tool": "tool"})

    rendered = pipelines_slice._render_image_defaults(document)
    defaults = dict(param_defaults(rendered))

    assert defaults["image"] == "example.org/tool:9.9", "the new key did not reach the default"
    assert defaults["other"] == "example.org/untouched:1.0", "the render touched a name it does not own"
