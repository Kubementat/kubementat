"""The ingress release is pinned, the pin is reviewed, and the values file agrees with it."""

import re

from asset_paths import values_path

from kubementat.core import versions

# The manifest helmfile applies, read from the package: it is product data and no longer
# a template `initialize` hands to the configuration.
HELMFILE_MANIFEST = values_path("helmfile.yaml.gotmpl")

# The chart the release pins, read from the template rather than written here (see
# `pinned_chart_version`); this is the mapping a changed pin has to be re-derived against. The first
# row was produced on 2026-10-29 on this host:
#
#     $ helm show chart traefik/traefik --version 42.1.3
#     appVersion: v3.7.16
#     kubeVersion: '>=1.30.0-0'
#     version: 42.1.3
#
#     $ helm template traefik traefik/traefik --version 42.1.3 | grep 'image:'
#             image: docker.io/traefik:v3.7.16
#
# `v3.7.16` is the tag the chart itself renders, not a tag chosen here. A pin that moves off a row
# in this table fails the check that reads it, and the fix it demands is a re-derivation and not a new
# row -- the same is true of the CRD groups that ship beside the chart.
#
# The 43.0.0 row was produced on 2026-11-26, when the pin moved to the chart's major bump, by the
# same two commands.
CHART_VERSION_TO_IMAGE_TAG = {
    "42.1.3": "v3.7.16",
    "43.0.0": "v3.8.0",
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
    """The template's lines for one release, commented lines dropped."""
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
    """The `version:` the traefik release actually binds, answered the way the apply answers it.

    The manifest line names the exported variable and no longer keeps a literal of its own, so the
    pin this check can read is the one `versions.yaml` owns — through its reader, not by copying it
    into this file.
    """
    for line in release_block("traefik"):
        declared = re.match(r'^\s+version:\s*"?\{\{\s*env\s+"([A-Z0-9_]+)"\s*\}\}"?\s*$', line)
        if not declared:
            continue
        assert declared.group(1) == "TRAEFIK_VERSION", (
            f"the traefik `version:` line binds {declared.group(1)}, not the knob the product "
            "exports for that release")
        return versions.chart_version("traefik")
    return None


# --- the release is pinned, and the pin is one that was reviewed --------------------------


def test_the_ingress_release_installs_a_named_chart_version_and_not_the_newest_it_could_find():
    """`version:` present, quoted, exact -- so "newest" is never what routes the cluster."""
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
