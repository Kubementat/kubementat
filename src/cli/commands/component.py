"""`kmt component` -- one component, on and off the cluster.

Two verbs for one question. kubementat could add the platform (`kmt install`), add a group of
components (`kmt install --helmfile-installation-group`), and remove one component (the uninstall
half below) -- but it had no way to add *one* component, so an operator who wanted to put
grafana back after taking it off had to apply a whole group to do it. This group is the missing
half of a pair that was already there.

**Why a group and not two flat commands.** The surface had exactly one group before
(`kmt users`), and the surface is going to be regrouped later, so what it needs to know is which way
this landed. It landed as a group: `install` and `uninstall` are the two halves of one question
about one component, which is the same reason `kmt routing` is a group (`status`/`install`), and
flat would have given the surface a third spelling of the same pair (`component-install`,
`component-uninstall`) for someone to guess at. The flat command is renamed into this group
outright -- no alias, no shim, no branch that answers to the old name. That is the
repo's rule and this migration's whole argument against a command having two answers.

**How a component is installed, and what this command will not do.** Everything here goes through
helmfile with a label filter (`component_name=<name>`), the same mechanism `kmt install` uses for a
group (`automation.py:544` passes `group=<name>`, `kmt routing install` passes `group=routing`).
There is no per-component install path, no `helm upgrade` written here, and no component table: what
this command can apply is read out of the manifest kubementat ships and applies
(`HelmUtils.component_release_index`, and `helmfile_paths` is what names that file) and what it is *allowed to be told about* is read out of
`constants.DEFAULT_NAMESPACES`. Both change without an edit here.

Three answers a reader should not have to discover by hitting them:

* **It applies a release, not a namespace.** `cert-manager`, `grafana`, `loki`, `openbao`,
  `polaris`, `prometheus` and `traefik` have a release labelled after them, and these are the components
  `kmt component install` can apply. The other components in `DEFAULT_NAMESPACES` are namespaces
  kubementat creates: `tekton`, `tekton-dashboard`, `tekton-pipelines-resolvers`, `cluster-cronjobs`,
  `goldilocks`, `vpa`. Installing one is refused, with what to run instead in the same message.
* **`traefik` is routing's decision, and this command asks routing.** Traefik is the one release
  whose install question is not "is it there" but "whose is it": applying it next to a controller
  the cluster already runs means two controllers, two classes and two status writers. So before
  applying, this command reads `traefik_presence` and refuses a controller kubementat did not
  install, naming `kmt routing install --update` as the verb that carries that consent.
  `cert-manager` is not asked -- an issuer is not a controller, and it is a plain release.
* **The git providers are not components.** gitea, gitbucket and gitlab were retired as kubementat
  components in `8678526`; they have no release, no namespace key and nothing to install.

Two releases are reachable only through a group: `prometheus-blackbox-exporter` and
`prometheus-helm-exporter` carry `component_name:` labels no `DEFAULT_NAMESPACES` key names, so
`kmt install --helmfile-installation-group standard` applies five releases and `kmt component
install <environment> prometheus` applies one. Say so here rather than in a bug report.
"""

import click

from cli.context import kmt_context
from kubementat import constants
from kubementat.automations.routing import traefik_presence
from kubementat.helm_utils import HelmUtils

# Traefik is a component like any other *and* a controller like no other one, so it gets the one
# branch this command has. The name is a constant rather than a literal because
# `traefik_presence.COMPONENT` and `constants.DEFAULT_NAMESPACES` both carry it already and a fourth
# spelling is a fourth place to be wrong.
ROUTER_COMPONENT = "traefik"


def _known_component_or_raise(component: str, action: str) -> str:
    """The component name, normalised, or one readable line that stops here.

    Lower-cased because that is how `Config.component_namespace` and the `component_name:` labels
    are keyed, and because an operator typing `GRAFANA` is asking about the same component as one
    typing `grafana` -- the case was never theirs to get right.

    A typo has to be refused rather than passed on: this command addresses a namespace through a
    *label*, so an unknown name does not install into a namespace named after the typo, it selects
    nothing and exits 0. The readable line is therefore not cosmetics, it is the difference between
    "nothing happened, and here is what to name" and a green command that applied nothing.

    The valid names are `constants.DEFAULT_NAMESPACES`, read rather than typed: this is the second
    place a component name is spelled on this surface, and a list here is a list that goes stale the
    day a component is renamed.
    """
    name = component.lower()
    if name not in constants.DEFAULT_NAMESPACES:
        raise click.ClickException(
            f"'{component}' is not a kubementat component, so nothing was {action}.\n"
            f"Components kubementat knows: "
            f"{', '.join(sorted(constants.DEFAULT_NAMESPACES))}.")
    return name


def _releases_for_component(utils: HelmUtils, name: str) -> list:
    """The releases labelled `component_name=<name>`, checked for the one thing that stops us.

    Exactly one release is what "install this component" can mean. Zero is the namespace-only case
    and is refused with what to run instead; more than one is a finding about the manifest -- the
    same label on two releases -- and the answer is to say so and apply nothing, not to pick one.
    """
    releases = utils.component_release_index().get(name, [])
    if len(releases) > 1:
        raise click.ClickException(
            f"the manifest kubementat applies gives {len(releases)} releases the label"
            f" component_name={name} ({', '.join(releases)}), so applying that filter would install"
            f" more than the one component this command was asked for. Nothing was installed; the"
            f" manifest is what needs the label corrected or a distinct component name.")
    return releases


def _installable_names(utils: HelmUtils) -> list:
    """The components this command can apply: named by `constants`, and labelled in the manifest.

    The intersection is the whole answer, and both halves of it are read rather than written here.
    A name in `DEFAULT_NAMESPACES` with no release is a namespace; a release with no
    `DEFAULT_NAMESPACES` key is somebody else's component. Neither is installable, and deriving the
    set this way is what keeps it from going stale in two places at once.
    """
    declared = utils.component_release_index()
    return sorted(name for name in constants.DEFAULT_NAMESPACES if declared.get(name))


def _no_release_message(utils: HelmUtils, environment: str, name: str) -> str:
    r'''
    Why nothing can be installed for `name`, in the sentence the evidence supports.

    One manifest, one answer. The command read the manifest `helmfile_apply` is about to apply and
    found no release labelled `component_name=<name>`, and that is the whole of what it is entitled
    to say: true of the namespace-only components, and for any other component a statement about the
    one file that decides what is installable.

    This refusal had three branches, because two files were being read for it -- the environment's
    copy, which `helmfile` applied, and kubementat's template, which it did not -- and the
    disagreement between them is what a refusal that read two paths would have had to describe.
    The packaged manifest took the second
    file out of the question: the manifest ships inside the package and the configuration contributes
    values, so there is nothing left to diverge from, and a refusal that weighed two paths would be
    describing a state this product no longer constructs. The path is still named, because "what will
    this apply" deserves an address.

    No branch points at `kmt config init`, and none may. It writes no helmfile, so it is not the fix
    for a release block, and the two edits the old divergence message offered were edits to a copy
    that nothing reads.
    '''
    return (
        f"'{name}' is a component whose namespace kubementat creates, not a helm release it"
        f" applies: the manifest kubementat applies ({utils.helmfile_state_path()}) declares no"
        f" release labelled component_name={name}, so there is nothing for this command to install."
        f" Components it can install: {', '.join(_installable_names(utils))}."
        f" Tekton arrives with `kmt install {environment}`, which applies its release manifests"
        f" with kubectl rather than helm; anywhere else, kubementat installs nothing into that"
        f" namespace and a team's own helm deploy is what belongs there.")


@click.group(name='component',
             help='Install or uninstall one component kubementat owns. install applies the release'
                  ' the environment names after <component> through helmfile, by its'
                  ' component_name label; uninstall takes that release and its claims off the'
                  ' cluster. Components that are only a namespace (tekton, goldilocks, vpa,'
                  ' cluster-cronjobs, goldilocks and friends) cannot be installed by helm and are'
                  ' refused, and traefik is refused beside a controller kubementat did not install.')
def component():
    pass


# Nothing on this group takes `--team`, and the reason is the same one `routing status`
# gives: both commands are one `HelmUtils` deep, and everything `HelmUtils` reads is
# environment-level -- `component_namespace` for the namespace a release is addressed at, the
# environment's `kubementat_components/helmfile.yaml.gotmpl` for the releases themselves, and
# `env_static_encrypted` for grafana's pair. No release this group installs or deletes is named
# after a team, and the rule is explicit that a command which cannot act on one does not accept a flag.
@component.command(name='install',
                   help='Apply one component\'s helm release: `helmfile apply -l'
                        ' component_name=<component>`. It applies without asking, the same way'
                        ' `kmt install` applies its group, so there is no --yes here: there is no'
                        ' confirmation to skip. The exception is traefik, where the cluster is'
                        ' read first: a Traefik this command did not put there is named and left'
                        ' alone, and the command that can be asked to change it is'
                        ' `kmt routing install --update`.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('component')
@kmt_context
def install(ctx, environment, component):
    # Deliberately not `--helmfile-installation-group standard` with the wrong end cut off: the
    # group filter is a *group*, and the only thing that addresses one release is the label.
    name = _known_component_or_raise(component, "installed")
    utils = HelmUtils(ctx)
    click.echo(f"environment: {environment}")
    try:
        releases = _releases_for_component(utils, name)
    except FileNotFoundError as error:
        # The manifest ships inside the package, so a run that cannot read it is not a tree that
        # needs preparing -- `kmt config init` writes no helmfile and would fix nothing here. What
        # is missing is the installed product's own data, and the message says so rather than
        # sending the operator to a command that no longer touches this file.
        raise click.ClickException(
            f"kubementat could not read the helmfile it ships, so it cannot say what is installable"
            f" for environment '{environment}' ({error}). Nothing was installed. That manifest is"
            f" product data -- `HelmUtils.helmfile_paths` is what names it -- and the installation,"
            f" not `platform_config`, is where it is missing.") from error

    if not releases:
        raise click.ClickException(_no_release_message(utils, environment, name))

    if name == ROUTER_COMPONENT:
        # The same question `kmt routing install` asks, through the same module, because a second
        # implementation of "whose Traefik is this" is a second answer, and the second answer is the
        # one that installs over somebody else's controller. The `Context` is handed over and not the
        # two loose strings, because the cluster is on this read's argv: a question asked without
        # an address is refused rather than answered about whichever cluster the process last saw.
        presence = traefik_presence.discover(ctx)
        if presence.classification == traefik_presence.CLASSIFICATION_FOREIGN:
            raise click.ClickException(
                f"the cluster already runs a Traefik kubementat did not install, so"
                f" component_name={name} is not being applied: that would be a second controller,"
                f" a second class and a second status writer. kubementat asks before it does that,"
                f" and the verb that can ask is `kmt routing install {environment} --update`"
                f" (read the cluster first with `kmt routing status {environment}`). Nothing was"
                f" installed.")
        if presence.classification == traefik_presence.CLASSIFICATION_OURS:
            click.echo("traefik is already installed by kubementat; nothing was applied.")
            click.echo(f"To change it: kmt routing install {environment} --update")
            return

    click.echo(f"applying component '{name}' through component_name={name}")
    utils.helmfile_apply(environment_name=environment,
                         helmfile_label_filter=f"component_name={name}",
                         interactive=False)


@component.command(name='uninstall',
                   help='Uninstall one helm component from the cluster: its release(s) and the'
                        ' claims they made. Absence is a normal answer, not a warning.')
@click.argument('environment', envvar='ENVIRONMENT')
@click.argument('component')
@kmt_context
def uninstall(ctx, environment, component):
    # The command renamed onto this group in the same commit: the implementation it calls is
    # unchanged (`HelmUtils.uninstall_release` reads the namespace from the configuration and the
    # release names from the cluster), and so is the shape of the answers it gives. The namespace
    # it deletes from is `env_static`'s, which is why the `--team` this command used to offer
    # resolved nothing.
    utils = HelmUtils(ctx)
    click.echo(f"environment: {environment}")
    try:
        outcome = utils.uninstall_release(_known_component_or_raise(component, "uninstalled"))
    except KeyError as error:
        # A namespace is what the deletion is addressed at, so the CLI has to say the name is
        # unknown rather than let a traceback stand where one line would do -- the same shape
        # `open_tunnel` gives a tunnel that cannot find its component.
        raise click.ClickException(
            f"'{component}' is not a kubementat component, so nothing was uninstalled.\n"
            f"{error}\nComponents kubementat knows: "
            f"{', '.join(sorted(constants.DEFAULT_NAMESPACES))}.") from error
    click.echo(f"namespace: {outcome['namespace']}")
    if not outcome["releases"]:
        click.echo(f"component '{component}' has no helm release in namespace"
                   f" {outcome['namespace']} - nothing was uninstalled.")
        return
    click.echo(f"releases uninstalled: {', '.join(outcome['releases'])}")
    if outcome["claims"]:
        click.echo(f"claims deleted: {', '.join(outcome['claims'])}")
    else:
        click.echo("the release(s) made no claims to delete.")
