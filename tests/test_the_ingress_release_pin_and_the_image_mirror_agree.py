"""The ingress release and the image mirror have to agree, because each is the other's guarantee.

`src/kubementat/core/assets/values/helmfile.yaml.gotmpl` decides which chart the routing group installs,
and `templates/environment/mirrored_docker_images.json.template` decides which images reach the
registry the cluster pulls from. Left unconnected, the two drift the way everything unlinked
drifts: the chart is bumped, the mirrored tag is not, and the platform's answer to "does this work
air-gapped?" is answered by an ImagePullBackOff nobody can see coming from a diff.

Two facts are held here, both from the same evidence the templates cite:

* The release carries a real `version:`. An unpinned release asks helm for "newest", which against
  `traefik/traefik`'s own `kubeVersion: >=1.30.0-0` floor is not a decision but a default -- and chart
  42's root `values.schema.json` sets `additionalProperties: false`, so a chart that arrived unreviewed
  does not install with different defaults, it refuses to install (`helmfile apply -l group=routing`
  stops with `additional properties ... not allowed`). That is the failure this pin exists to prevent,
  and it is the owner's decision of 2026-10-29, stated here and not in the plan that recorded it.
* The image that pinned chart renders is a mirrored image, tagged the way the chart tags it. The tag is
  recorded below rather than computed, for the same reason
  `tests/test_the_routing_values_file_never_writes_a_service_type_or_a_storage_class.py` records the
  chart's accepted root keys: the unit suite does not have a network, so what is written is the
  assertion and the command that produced it is named beside it.
"""

import json
import re

from asset_paths import template_path, values_path

# The manifest helmfile applies, read from the package: it is product data and no longer
# a template `initialize` hands to the configuration.
HELMFILE_MANIFEST = values_path("helmfile.yaml.gotmpl")
MIRROR_TEMPLATE = template_path("environment", "mirrored_docker_images.json.template")

# The chart the release pins, read from the template rather than written here (see
# `pinned_chart_version`); this is the mapping a changed pin has to be re-derived against. Both rows
# were produced on 2026-10-29 on this host:
#
#     $ helm show chart traefik/traefik --version 42.1.3
#     appVersion: v3.7.16
#     kubeVersion: '>=1.30.0-0'
#     version: 42.1.3
#
#     $ helm template traefik traefik/traefik --version 42.1.3 | grep 'image:'
#             image: docker.io/traefik:v3.7.16
#
# so `v3.7.16` is the tag the chart itself renders, not a tag chosen here. A pin that moves off a row
# in this table fails the check that reads it, and the fix it demands is a re-derivation and not a new
# row -- the same is true of the CRD groups that ship beside the chart.
CHART_VERSION_TO_IMAGE_TAG = {
    "42.1.3": "v3.7.16",
}

# The chart the release names, so a rewrite that swaps the chart over for a different product is
# caught here and not in a routing lane fifteen minutes later.
CHART = "traefik/traefik"

# What the mirroring mechanism speaks: `skopeo-sync-images.yml` reads `name`/`description`/
# `source`/`destination`, prefixes `docker://` onto `source` and `destination` when neither is
# already a transport URL, and copies one row at a time. A row missing a key is a row that copies
# `docker://None`.
MIRROR_ROW_KEYS = ("name", "description", "source", "destination")

# `initialize` copies this template to platform_config verbatim
# (`automations/routing/traefik_presence.py`'s read of `helm.py`); the base url is the operator's fill-in.
REGISTRY_PLACEHOLDER = "REPLACEME_DOCKER_REGISTRY_BASE_URL"


def release_block(name):
    """The template's lines for one release, commented lines dropped.

    A commented `version:` is a note and not a pin -- which is precisely the difference that was
    decided on 2026-10-29 -- so a commented line inside the block is not evidence of anything.
    """
    lines = []
    inside = False
    for line in HELMFILE_MANIFEST.read_text(encoding="utf-8").splitlines():
        if re.match(r"^- name: \S+\s*$", line):
            inside = line.strip() == f"- name: {name}"
            continue
        if inside:
            if line.lstrip().startswith("#"):
                continue
            lines.append(line)
    return lines


def pinned_chart_version():
    r"""The `version:` the traefik release actually writes, or None when it writes none.

    Two spellings answer that question and both are read, not one: the bare pin this release carried
    until recently, and the `default "<literal>"` of the templated line that replaced it --
    `version: "{{ env "TRAEFIK_VERSION" | default "42.1.3" }}"`, whose default *is* the pin, since it
    is what helmfile installs when no layer declares the key. A parser that read only the old shape
    would answer `None` and fail three checks over a knob that changed no pin; one that read only the
    new shape would keep passing if someone deleted the knob and put the literal back, which is the
    other half of the same rule and is caught by
    `tests/test_every_release_the_manifest_names_carries_both_knobs.py`.
    """
    for line in release_block("traefik"):
        declared = re.match(r'^\s+version:\s*(.+?)\s*$', line)
        if not declared:
            continue
        value = declared.group(1).strip().strip('"')
        templated = re.match(r'^\{\{\s*env\s+"[A-Z0-9_]+"\s*\|\s*default\s+"(.*?)"\s*\}\}$',
                             value)
        return templated.group(1) if templated else value
    return None


def mirror_rows():
    """Every image row the platform mirrors, in template form."""
    return json.loads(MIRROR_TEMPLATE.read_text(encoding="utf-8"))["images"]


def traefik_mirror_row():
    rows = [row for row in mirror_rows() if "traefik" in str(row.get("source", ""))]
    assert len(rows) <= 1, \
        f"{len(rows)} rows mirror a traefik image, so which one a cluster pulls is a race between rows"
    return rows[0] if rows else None


# --- the release is pinned, and the pin is one that was reviewed --------------------------


def test_the_ingress_release_installs_a_named_chart_version_and_not_the_newest_it_could_find():
    """`version:` present, quoted, exact -- so "newest" is never what routes the cluster.

    An unpinned release is a chart nobody validated against the values file, and the shape of that
    failure is silence: the schema forbids unknown keys at the root, so a chart that renamed one
    produces an apply that installs nothing.
    """
    pinned = pinned_chart_version()

    assert CHART in "\n".join(release_block("traefik")), \
        f"the traefik release must install {CHART}, which is what `constants.TRAEFIK_CHART` names"
    assert pinned is not None, \
        "a commented `# version:` resolves to newest against the chart's kubeVersion floor, which is " \
        "not a pin: the routing group then installs a chart its values file was never checked against"
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), \
        f"the pin is {pinned!r}; a pin that carries a range or a wildcard is still a request for newest"
    assert pinned in CHART_VERSION_TO_IMAGE_TAG, \
        f"{pinned} is not a chart version whose rendered image is recorded here, so the mirror list " \
        "has no way to say it mirrors what this release installs -- re-derive it with " \
        "`helm template traefik traefik/traefik --version <pin> | grep image:`"


def test_the_pinned_chart_is_the_one_the_values_file_was_validated_against():
    """The pin and the values file's own header must not disagree.

    `values/traefik/values.yaml` says which chart it is schema-clean against. If the
    release pins a different one, the sentence and the release are two answers to one question.
    """
    header = values_path("traefik", "values.yaml").read_text(encoding="utf-8")
    validated_against = set(re.findall(r"traefik/traefik (\d+\.\d+\.\d+)", header))

    assert pinned_chart_version() in validated_against, \
        f"the release pins {pinned_chart_version()!r} but the values file claims validation against " \
        f"{sorted(validated_against)}"


# --- the image that chart renders is an image the platform mirrors ------------------------


def test_the_image_the_pinned_ingress_chart_renders_is_mirrored():
    """Mirroring is what makes the pin a guarantee, so the two are one question.

    The tag is the chart's `appVersion` and therefore the chart's answer and not kubementat's: a
    mirror list that drifts from the pin is a platform that installs the same release twice, once
    from the registry and once from the image the registry does not have.
    """
    row = traefik_mirror_row()

    assert row is not None, \
        "the controller kubementat installs is not in the mirror list, so a cluster without docker.io " \
        "pulls an image no one ever copied there"
    expected_tag = CHART_VERSION_TO_IMAGE_TAG[pinned_chart_version()]
    assert row["source"].endswith(f":{expected_tag}"), \
        f"the mirror copies {row['source']} but the pinned chart renders the image tagged " \
        f"{expected_tag}: the release will ask for a tag this row does not copy"


def test_every_mirrored_image_row_is_a_row_the_sync_task_can_copy():
    """The rows are read by a task, not a human, and that task indexes four keys.

    `tekton_ci/tasks/skopeo-sync-images.yml` pulls `name`/`description`/`source`/`destination` out of
    each row and prefixes `docker://` onto the two URLs it copies, so a row is only as good as all
    four of its keys and a destination that never left the template is a copy into
    `docker://REPLACEME_DOCKER_REGISTRY_BASE_URL/...`, which fails as an authentication error.
    """
    rows = mirror_rows()

    assert rows, "the template mirrors nothing, so the pipeline it is the input for copies nothing"
    for row in rows:
        missing = [key for key in MIRROR_ROW_KEYS if not str(row.get(key, "")).strip()]
        assert not missing, f"row {row.get('name')!r} is missing {missing}, and the task copies None"
        assert row["destination"].startswith(f"{REGISTRY_PLACEHOLDER}/"), \
            f"row {row['name']!r} writes its destination somewhere else, so initialize's copy is not " \
            "the operator's knob"
        assert ":" in row["source"].rsplit("/", 1)[-1], \
            f"row {row['name']!r} mirrors an untagged source, which is a pull of :latest by accident"
