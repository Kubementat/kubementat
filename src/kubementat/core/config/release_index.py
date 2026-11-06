"""The helmfile manifest, read off the disk and nowhere else.

Three questions about one file, answered with no `Context`, no cluster, and no child process:
which directory the manifest sits in, which file it is, and which releases it labels. The three
methods in `HelmUtils` that answer them do that work through these functions, and `config_answers`
calls them directly -- the layer that reports what a configuration tree answers has to read the
release index too, and reading it is not a helm question. It is a `Config` and a file.

The split line these functions sit on is "needs only a `Config` and the filesystem" versus "needs a
subprocess or a `Context`". Everything a binary answers stays in `helm.py`: the `helm list`
that says where a release is deployed, `helm show chart`, the apply itself. Nothing here reaches
any of that, which is what lets this module -- and the config layer that imports it -- stay off the
helm layer. The final home is `core/config/`; until that move lands this is a flat module, and it
imports stdlib plus what `Config` already carries.
"""

import re
from pathlib import Path


def helmfile_paths_for(config) -> tuple:
    '''
    ``(working_directory, manifest_path)`` for the manifest this environment applies.

    One place, because two things need them and only one of them runs `helmfile`: the apply
    itself, and the label index `kmt component install` reads to find out whether a release
    exists. Those two have to be pointed at the same file or the answer the CLI gives about what
    it is about to apply says nothing about what it applies.

    Both halves answer from inside the package, and the operator's checkout is not part of the
    answer. The manifest is `Config.helmfile_asset_dir`'s `helmfile.yaml.gotmpl` -- product data,
    shipped the way the Tekton Tasks are -- and the working directory is its own,
    because that is the directory helmfile resolves a release's `values:` path against, and the
    defaults it names sit next to it under `values/<component>/`. Nothing writes the manifest
    or its values templates into a configuration tree -- `kmt initialize` carried that copy
    once, it came out of the command, and the command itself is gone now -- so there is
    no copy a cluster can have
    outgrown: a stale `platform_config/{environment}/kubementat_components/helmfile.yaml.gotmpl`
    somebody left by hand changes what is applied exactly as much as a note pinned to a door
    does. That is plan row 13's bug, and this function is what makes it unconstructible.

    What the configuration contributes is values and never a manifest. A manifest that wants to
    say where they live gets the root, the environment and the team from
    `export_configuration_coordinates`, which is what `helmfile_apply` calls before it runs.
    '''
    working_directory = Path(config.helmfile_asset_dir).absolute()
    return working_directory, working_directory / "helmfile.yaml.gotmpl"

def helmfile_state_path_for(config) -> Path:
    '''The packaged manifest `helmfile_apply` applies, as a path.

    No environment decides this: `helmfile_paths_for` is the one answer, and the label index below
    reads the same file the apply will hand to `helmfile`.
    '''
    return helmfile_paths_for(config)[1]

def read_release_index(config, helmfile_path: Path = None) -> dict:
    r'''
    `{component_name label: [release, ...]}` for the releases the shipped manifest names.

    The question this answers is "does a release carry the label `component_name=<name>`, and
    how many". `kmt component install` applies by that selector, so the set of components it can
    apply is decided here and not by a list in the CLI: a release that is added to the manifest
    becomes installable, and one that is commented out stops being so, with no second edit. Two
    things this makes visible rather than guess at, both of which the command refuses to paper
    over:

    * **a component with no release.** `vpa`, `goldilocks` and the three Tekton components are
      namespaces kubementat creates, not releases it applies, so they come back with an empty
      list, which is an answer and not an error. `polaris` is not one of them any more: its
      release block was commented out of the template until 2026-11-01 and is declared now, so it
      is installable the way the other releases are.
    * **a component whose label is on more than one release.** Applying that filter would take a
      command named for one component and turn it into two, so the command says so and applies
      nothing.

    Releases whose label is not a `constants.DEFAULT_NAMESPACES` key are in the index too, and
    that is a fact worth having: the two prometheus exporters land in the `prometheus` namespace
    under labels no component names, so a group apply brings them and a component apply does not.

    A commented release is not a release, so a commented line is skipped rather than parsed -- the
    same rule the harness's own parser
    (`tests/e2e/harness/stages.py::_releases_in_groups`) uses, for the same reason.

    Args:
        helmfile_path: The manifest to read. Defaults to what `helmfile_state_path_for` says this
            environment applies; passing it is how a test reads a manifest that is not on disk.

    Returns:
        dict[str, list[str]]: Labels to release names, each list sorted and free of duplicates.

    Raises:
        FileNotFoundError: When the environment has not been prepared, which is the same
            failure `helmfile_apply` would hit. Naming the verb that prepares a tree is the
            CLI's job, and this function does not have it.
    '''
    path = Path(helmfile_path) if helmfile_path is not None else helmfile_state_path_for(config)
    index = {}
    release = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        declared = re.match(r"^- name:\s*(\S+)", line)
        if declared:
            # A `repositories:` entry has this shape too, and carries no labels; the next release
            # header replaces it before a label can be attributed to it.
            release = declared.group(1)
            continue
        labelled = re.match(r"^\s+component_name:\s*(\S+)", line)
        if labelled and release is not None:
            index.setdefault(labelled.group(1), []).append(release)
    return {label: sorted(set(releases)) for label, releases in index.items()}
