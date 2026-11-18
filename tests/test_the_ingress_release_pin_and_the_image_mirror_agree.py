"""The ingress release is pinned, the pin is reviewed, and the values file agrees with it.

`src/kubementat/core/assets/values/helmfile.yaml.gotmpl` decides which chart the routing group installs.
An unpinned release is a chart nobody validated, and the shape of that failure is silence: chart 42's root
`values.schema.json` sets `additionalProperties: false`, so a chart that renamed a key does not install
with different defaults, it refuses (`helmfile apply -l group=routing` stops with
`additional properties ... not allowed`). Two checks say the pin is real, and a third says the values file
that ships alongside it agrees with it.

**What this file used to prove, and no longer can.** It also asserted that the image the pinned chart
renders is an image the platform *mirrors*, read from
`templates/environment/mirrored_docker_images.json.template`. That template is deleted: no verb ever
wrote it into a tree, and nothing in `src/` opened it. Its two checks went with the file rather than
being kept alive against an empty glob — deleting an artefact and keeping a vacuous assertion over it
would be the worse trade. Two facts survive the deletion as sentences, because they are owed to the next
reader and no test can say them:

* The mirroring the template described is **not** a property kubementat maintains.
  `tekton_ci/tasks/skopeo-sync-images.yml` defaults its input to
  `$(workspaces.workspace.path)/kubementat/platform_config/dev/mirrored_docker_images.json` — a path
  inside a checkout, in a layout 1.x wrote — and no verb creates it. So an air-gapped cluster was always
  served by a file somebody made by hand, and the template was that somebody's starting point.
* If mirroring is ever made real, the tag it copies is the chart's `appVersion`
  (`v3.7.16` for chart `42.1.3`), so a pin that moves must move the mirror with it. That is the rule the
  deleted test was holding, and it is why `CHART_VERSION_TO_IMAGE_TAG` stays below.

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

import re

from asset_paths import values_path

# The manifest helmfile applies, read from the package: it is product data and no longer
# a template `initialize` hands to the configuration.
HELMFILE_MANIFEST = values_path("helmfile.yaml.gotmpl")

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
# `docker://None`. The keys themselves went with the template that held them; what is left of the
# sync task's contract is the note above.


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
