#!/usr/bin/env python3
"""Build the diagram's data model: boxes, band assignment, box-to-box edges, fan-in.

Shared by the diagram generator (docs/diagram-generator/build_tree_diagram.py) and the checks.
The hand-maintained part of the tooling: `BOXES` below is the box layout and the source of every
label the drawing carries, so a package moved or renamed under `src/` has to be followed by an
edit here. `build()` fails loudly when a module belongs to no box, and `stale_boxes()` reports a
box whose drawn name has stopped resolving — the one thing a rename can leave behind quietly.
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tree_survey  # noqa: E402
from tree_survey import survey  # noqa: E402

# ---- box model -----------------------------------------------------------------------------
# Each box names a real file or package under src/. `members` are the modules whose imports the
# box carries. Ordered by band, and by the order they should appear left to right.
BOXES = collections.OrderedDict()


def box(band, name, members, note, blurb):
    BOXES[name] = {"band": band, "name": name, "members": members, "note": note, "blurb": blurb}
    return name


# band keys are ordered top (the surface a user touches) to bottom (the leaf layer)
BANDS = ["cli", "verbs", "automations", "infrastructure", "core"]

box("cli", "src/cli/kmt.py", ["cli.kmt"], "the root group — registers the 11 command modules", "console entry point")
box("cli", "src/cli/context.py", ["cli.context"], "the one place a command finds which cluster it will touch", "team/environment/options → Context")
box("cli", "src/cli/helpers.py", ["cli.helpers"], "shared click helpers, imports nothing", "no imports at all")
box("cli", "src/cli/commands/", ["cli.commands"], "11 verb groups, one module each", "")

box("core", "src/kubementat/errors.py", ["kubementat.errors"], "every exception class lives here; 37 of them, raised and never returned", "")
box("core", "src/kubementat/crypto_utils.py", ["kubementat.crypto_utils"], "ssh and gpg keypairs — the one flat primitive that imports nothing", "")
box("verbs", "src/kubementat/config_init.py", ["kubementat.config_init"], "lays the configuration files down", "kmt config init")
box("verbs", "src/kubementat/config_bootstrap.py", ["kubementat.config_bootstrap"], "runs init's layer plan, then git-commits the tree", "kmt config …")
box("verbs", "src/kubementat/config_answers.py", ["kubementat.config_answers"], "what is answered today", "kmt config show")
box("verbs", "src/kubementat/config_keys.py", ["kubementat.config_keys"], "the deployer keypair", "kmt config set/unset")
box("verbs", "src/kubementat/config_validate.py", ["kubementat.config_validate"], "the verdict on a tree", "kmt config validate")

box("automations", "automations/install/", ["kubementat.automations.install"], "one slice per install verb", "")
box("automations", "automations/uninstall/", ["kubementat.automations.uninstall"], "one slice per uninstall verb", "")
box("automations", "automations/routing/", ["kubementat.automations.routing"], "traefik presence, then install", "")
box("automations", "automations/tunnels/", ["kubementat.automations.tunnels"], "port-forwards a team asks for", "")
box("automations", "automations/accounts/", ["kubementat.automations.accounts"], "team service accounts", "")
box("automations", "automations/ci.py", ["kubementat.automations.ci"], "the CI image, run inside the cluster", "")
box("automations", "automations/ingress.py", ["kubementat.automations.ingress"], "the ingress manifest, from configuration", "")

box("infrastructure", "infrastructure/kubernetes/", ["kubementat.infrastructure.kubernetes"], "nine area modules; their union is what consumers import", "")
box("infrastructure", "infrastructure/tekton/", ["kubementat.infrastructure.tekton"], "five modules — generation, runs, install, triggers, preflight", "")
box("infrastructure", "infrastructure/grafana/", ["kubementat.infrastructure.grafana"], "the admin API, and the rows it is driven from", "")
box("infrastructure", "infrastructure/helm.py", ["kubementat.infrastructure.helm"], "the one place helmfile is touched", "")
box("infrastructure", "infrastructure/shell.py", ["kubementat.infrastructure.shell"], "the one subprocess policy kubementat owns", "")
box("infrastructure", "infrastructure/user_management.py", ["kubementat.infrastructure.user_management"], "service accounts a team logs in with", "")

box("core", "core/config/", ["kubementat.core.config"], "the tree's own configuration, read as layers", "")
box("core", "core/identity/", ["kubementat.core.identity"], "who this run is about, and which cluster it means", "")
box("core", "core/constants.py", ["kubementat.core.constants"], "the defaults everything else falls back on", "imports nothing")
box("core", "core/version.py", ["kubementat.core.version"], "kmt's own version, from the installed package", "imports nothing")
box("core", "core/quantities.py", ["kubementat.core.quantities"], "cpu/memory parsing", "imports nothing")
box("core", "core/assets/", ["kubementat.core.assets"], "files the package ships, resolved from inside it", "")


def owner_of(module):
    """map a module dotted name to the box that draws it. Longest member prefix wins."""
    best = None
    for name, b in BOXES.items():
        for m in b["members"]:
            if module == m or module.startswith(m + "."):
                if best is None or len(m) > best[1]:
                    best = (name, len(m))
    if best:
        return best[0]
    raise KeyError(module)


# short label for the "what it imports" line inside a box, so the text stays narrow
SHORT = {}
for _n, _b in BOXES.items():
    SHORT[_n] = (_n.replace("src/kubementat/", "").replace("src/cli/", "cli/")
                 .replace("infrastructure/", "").replace("automations/", "").replace("/", ".").rstrip(".")
                 or _n)


# the package __init__.py files that name no module of their own: they import nothing and expose
# nothing, so no box draws them. They are counted in the band caption instead.
BARE_INITS = {"cli", "kubementat", "kubementat.core", "kubementat.automations", "kubementat.infrastructure"}


def short(name):
    return SHORT.get(name, name)


# A box's `name` is also a path, and the drawing labels the box with it. The surface bands spell it
# from the repo root (`src/cli/kmt.py`, `src/kubementat/errors.py`); the bands inside the package
# spell it relative to `src/kubementat/` (`core/config/`, `infrastructure/shell.py`).
def box_path(band, name):
    """a box's display name as a path under the repo root."""
    return os.path.join(tree_survey.REPO_ROOT, *(name if name.startswith("src/") else "src/kubementat/" + name).split("/"))


def stale_boxes():
    """boxes whose drawn name no longer resolves, or which draw nothing."""
    bad = []
    for name, b in BOXES.items():
        p = box_path(b["band"], name)
        if not (os.path.exists(p) or os.path.exists(p.rstrip("/"))):
            bad.append(f"{name}: no such path at {os.path.relpath(p, tree_survey.REPO_ROOT)}")
        elif b.get("files") == []:
            bad.append(f"{name}: draws no module (its `members` match nothing in src/)")
    return bad


def build(mods):
    """returns boxes (with per-file lines/counts) and box-pair edges."""
    unmapped = [m for m in mods if m not in BARE_INITS]
    for m in sorted(unmapped):
        try:
            owner_of(m)
        except KeyError:
            raise SystemExit(f"module not in any box: {m}")
    for name, b in BOXES.items():
        files = []
        for m, v in sorted(mods.items()):
            if m not in BARE_INITS and owner_of(m) == name:
                files.append((m, v))
        b["files"] = files
        b["classes"] = sum(v["classes"] for _, v in files)
        b["methods"] = sum(v["methods"] for _, v in files)
        b["funcs"] = sum(v["funcs"] for _, v in files)
        b["loc"] = sum(v["loc"] for _, v in files)
        b["nfiles"] = len(files)

    pairs = collections.Counter()
    for m, v in sorted(mods.items()):
        if m in BARE_INITS:
            continue
        from_box = owner_of(m)
        for dep in v["imports"]:
            if dep in BARE_INITS:
                continue
            to_box = owner_of(dep)
            if from_box != to_box:
                pairs[(from_box, to_box)] += 1
            else:
                b = BOXES[from_box]
                b["internal"] = b.get("internal", 0) + 1
    return pairs


def module_fan_in(mods):
    fan = collections.Counter()
    for m, v in mods.items():
        for dep in v["imports"]:
            fan[dep] += 1
    return fan


if __name__ == "__main__":
    mods = survey()
    # cli.commands and cli are packages/modules; owner mapping needs cli.* present
    pairs = build(mods)
    fan = module_fan_in(mods)
    print("boxes:", len(BOXES))
    print("box pairs:", len(pairs))
    for (a, b), n in sorted(pairs.items(), key=lambda x: -x[1]):
        print(f"  {a:<34} -> {b:<34} {n}")
    print("box fan-in (by pair count):", collections.Counter())
    tot = collections.Counter()
    for (a, b), n in pairs.items():
        tot[b] += 1
    for b, n in sorted(tot.items(), key=lambda x: -x[1]):
        print(f"  in  {b:<34} {n}")
