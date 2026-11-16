"""Which components exist, answered once, from the manifest kubementat applies.

Two questions about one name, answered in two places, is the bug this module closes. "Is there a
component called `grafana`, and does it
have a release?" is answered one way by `constants.DEFAULT_NAMESPACES` (the CLI's accepted-name list,
`cli/commands/component.py:_known_component_or_raise`) and another way by
`release_index.read_release_index` (the labels the shipped helmfile carries, which is what
`helmfile apply -l`, the knob export and `config_answers.effective_answers` read). Both answers are
internally correct, and they can disagree: a name can be refused as "not a kubementat component"
while the very manifest kubementat applies names a release after it. So the answer here is a *kind*
computed from both reads rather than a name list one caller owns.

The rule, stated once:

* **a component is a name kubementat addresses with a namespace** -- `constants.DEFAULT_NAMESPACES`
  keys. That is the universe `kmt component install`/`uninstall` accept, and the set `kmt uninstall`
  sweeps.
* **a release is what the manifest names** -- `read_release_index` and nothing else. Version and
  installed/disabled already read that way (`Config.component_release_version`), so existence has no
  business being answered from a second table.
* **the two do not line up, in both directions, and both directions are correct.** The manifest labels
  `alloy`, `prometheus-blackbox-exporter` and `prometheus-helm-exporter` with no component owning
  those labels: each lands in a namespace belonging to another component (the collector shares
  `loki`'s), so a namespace key for them would be a key helmfile never asks for and a namespace
  `kmt uninstall` would delete twice. `vpa`, `goldilocks`, `cluster-cronjobs` and the three Tekton keys
  are the opposite half: a namespace kubementat creates and no release, because those arrive with
  kubectl rather than helm.

A caller that needs both answers therefore needs one object: `ComponentUniverse.read` reads the
manifest once and answers every question off that read, so a command cannot ask the manifest twice and
get two answers. What this module does not do is read a cluster, run a child process, or consult
`Context`. Like the parser it sits on, it is a `Config` and a file.
"""

from kubementat.core import constants
from kubementat.core.config import release_index

# The four kinds `ComponentUniverse.kind` answers. Spelled as sentences rather than enum names on
# purpose: these strings go straight into a refusal, and a table of words that has to agree with a
# table of facts is the same second owner this module was written to delete.
WITH_RELEASE = "component with a release"
NAMESPACE_ONLY = "namespace kubementat creates, and no release"
RELEASE_WITHOUT_COMPONENT = "release that belongs to no component"
NOT_A_KUBEMENTAT_NAME = "not a kubementat name"


class ComponentUniverse:
    """One read of the shipped manifest, and the answers that read gives.

    Built once per command. `component_kind` below is the module's ordinary entry point; this class is
    what a caller that has to ask more than one question uses, so the manifest is read once and the
    answers it gives cannot drift mid-command.

    Attributes:
        index: `{component_name label: [release, ...]}` exactly as `read_release_index` returned it.
        declared: The namespace-addressed names. Defaults to `constants.DEFAULT_NAMESPACES`' keys, and
            is overridable so a test can add a component to the table and watch every answer here move
            with it -- which is the property the table has to have.
    """

    def __init__(self, index: dict, declared=None):
        self.index = {str(label): list(releases)
                      for label, releases in (index or {}).items() if releases}
        self.declared = tuple(sorted(
            (declared if declared is not None else constants.DEFAULT_NAMESPACES)))

    @classmethod
    def read(cls, config, helmfile_path=None, declared=None) -> "ComponentUniverse":
        """Ask the manifest, once. Raises `FileNotFoundError` exactly as the parser does.

        A manifest that cannot be read is not "nothing exists"; it is an installation that is missing
        its own product data, and a universe built on that failure would answer every question with
        `not a kubementat name`. So the failure is raised and not caught here, and the CLI that has a
        sentence for it is the caller that should own it.
        """
        return cls(release_index.read_release_index(config, helmfile_path), declared)

    def released(self) -> tuple:
        """The component names the manifest gives a release, sorted."""
        return tuple(sorted(self.index))

    def addressed(self) -> tuple:
        """The names kubementat addresses with a namespace, sorted."""
        return self.declared

    def accepted_names(self) -> tuple:
        """The names a `kmt component` verb takes -- the addressed set, and nothing else.

        Worth naming explicitly because it is the claim this item is judged by: what the commands
        accept is what `constants` addresses, not what the manifest labels. The released set is wider,
        and a command that accepted all of it would offer to install releases kubementat owns no
        namespace for.
        """
        return self.declared

    def namespace_only(self) -> tuple:
        """The addressed names that apply no release. Derived, so a new table entry moves it."""
        released = set(self.released())
        return tuple(name for name in self.addressed() if name not in released)

    def releases_without_a_component(self) -> tuple:
        """The labels that name no addressed component -- what a group apply brings. Derived."""
        addressed = set(self.addressed())
        return tuple(name for name in sorted(self.index) if name not in addressed)

    def kind(self, name: str) -> str:
        """Which of the four kinds `name` is. The one answer every caller renders.

        The normalisation is the caller's (`cli/commands/component.py` lower-cases, because the case
        was never the operator's to get right), so this answers about a name exactly as it is keyed.
        """
        released = name in self.index
        addressed = name in self.declared
        if addressed and released:
            return WITH_RELEASE
        if addressed:
            return NAMESPACE_ONLY
        if released:
            return RELEASE_WITHOUT_COMPONENT
        return NOT_A_KUBEMENTAT_NAME

    def releases_for(self, name: str) -> tuple:
        """The releases `name`'s label selects, sorted. Empty is a namespace-only answer, not an error."""
        return tuple(self.index.get(name, ()))


def component_kind(name: str, config, helmfile_path=None) -> str:
    """The kind of `name`, reading the manifest once. See `ComponentUniverse.kind`."""
    return ComponentUniverse.read(config, helmfile_path).kind(name)


def components_the_manifest_releases(config, helmfile_path=None) -> tuple:
    """The released component names, from the one parser. See `ComponentUniverse.released`."""
    return ComponentUniverse.read(config, helmfile_path).released()


def components_kubementat_addresses() -> tuple:
    """The namespace-addressed names, sorted. A view of `constants.DEFAULT_NAMESPACES`, not a second list.

    A function rather than a module-level constant for the same reason the manifest is read: a caller
    that writes `sorted(constants.DEFAULT_NAMESPACES)` is one import away from accepting a name in one
    verb and refusing it in another, which is the disagreement this module removes.
    """
    return tuple(sorted(constants.DEFAULT_NAMESPACES))


def components_without_a_release(config, helmfile_path=None) -> tuple:
    """The addressed names with no release. See `ComponentUniverse.namespace_only`."""
    return ComponentUniverse.read(config, helmfile_path).namespace_only()


def releases_belonging_to_no_component(config, helmfile_path=None) -> tuple:
    """The labels that name no component. See `ComponentUniverse.releases_without_a_component`."""
    return ComponentUniverse.read(config, helmfile_path).releases_without_a_component()
