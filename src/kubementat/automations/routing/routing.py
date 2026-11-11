"""`kmt routing install`: put a Traefik on the cluster exactly once, and say what was there instead.

One rule, and everything below is a consequence of it: kubementat installs Traefik when the cluster
has none. When one is already running it keeps its hands off, warns, and applies the rest of the
routing group anyway. `--update` is the operator saying "the one you found is the one I want
overwritten", and it is a different *sequence of operations* rather than the same one with a boolean,
because Helm applies a chart's `crds/` on install and never again afterwards. The
consequences that follow, stated before the code rather than buried in a log level:

* **A skip is a helmfile selector.** The difference between installing and skipping is one string
  handed to `HelmUtils.helmfile_apply`, so there is no second code path that could
  drift, and nothing in this module decides *which releases* to touch. helmfile's own grammar
  (verified against `helmfile --help`, v1.7.0: "A release must match all labels in a group") does it.
  cert-manager is applied either way, because a skip that suppressed the whole group would take the
  platform's ClusterIssuer down with it.
* **The decision comes off the cluster.** `traefik_presence.discover()` answers `none`/`ours`/
  `foreign` from three reads, and this module decides from that answer rather than from a name list
  or from a value `platform_config` happens to carry. What `install` prints is the same answer
  `kmt routing status` prints, because two callers reading two answers is two products.
* **Nothing here is a second namespace answer.** The namespace is `Config.component_namespace`, the
  class is the class key, and both go through `config.py`'s one rule: a key that is not there warns
  and uses the built-in default rather than crashing or inventing a name.
* **`--update` asks before it writes.** A foreign controller plus `--update` means a second
  controller, a second class and a second writer of `Ingress.status`, and the plan's own reason for
  saying so out loud is that it is the one branch the operator might not have chosen. Without a
  terminal to ask in it fails closed and names `--yes` -- the same contract `kmt uninstall` and `kmt
  users create-team-account` run on.

The failure model is the codebase's: a `helmfile apply` that exits non-zero raises with its output
attached, and nothing in this module retries. A cluster that will not answer a *read* is a finding
(see `traefik_presence`), not a failure; a command that was told to write and could not is a failure.
"""

import json
import logging
import sys
from dataclasses import dataclass, field, replace

import click
import yaml
from kubernetes import client

from kubementat import shell
from kubementat.automations.routing import traefik_presence
from kubementat.config import Config, component_config_key
from kubementat.constants import DEFAULT_STATIC_ANSWERS, TRAEFIK_CHART
from kubementat.helm_utils import HelmUtils

# The component, spelled the way every other component is spelled, so the config keys it names are
# derived rather than remembered. Same string as `traefik_presence.COMPONENT`, for the same reason.
COMPONENT = "traefik"

# What `install_routing` decided. One of these three is what the CLI and any later caller reads, and
# the string is the one that has to survive being pasted into a bug report.
ACTION_INSTALL = "install"
ACTION_UPGRADE = "upgrade"
ACTION_SKIP = "skip"

# Whether the second-controller consent had to be asked for. `accepted` is the case where `--yes`
# answered it, `asked` the case where a person did -- and both are recorded rather than left out,
# because "did anything ask?" is the question a log is read to answer.
CONSENT_NOT_NEEDED = "not needed"
CONSENT_ACCEPTED = "accepted"
CONSENT_ASKED = "asked"

# The two selectors, and the entire expression of the decision. The skip is not a
# branch, a flag or a templated `installed:` gate: it is one label a release does not match. The
# `group=routing` half of both is what keeps cert-manager in the apply, which is why a skip is a
# *narrowing* of the same filter rather than a different one.
INSTALL_LABEL_FILTER = "group=routing"
SKIP_LABEL_FILTER = "group=routing,component_name!=traefik"

# The one read the verification step makes, and the one it warns about. `ingressClassName` on a
# solver Ingress names the class the challenge has to travel through; a name no IngressClass carries
# is accepted by the API, written by cert-manager, and served by nobody -- which is how certificate
# issuance fails forever without ever printing an error. It is read through
# `NetworkingV1Api.list_ingress_class` rather than an argv, so the check is bound to the
# cluster the command was pointed at and carries no address of its own.

# How `--update` takes the definitions, verbatim from the chart README's upgrade instruction
# (`helm show crds traefik/traefik | kubectl apply --server-side --force-conflicts -f -`). The pipe
# is gone rather than worked around: `kubementat.shell` runs argv lists and refuses command strings,
# so the text is captured and handed over on stdin.
# The CRD apply's shape, spelled where it runs (this one stays `kubectl`, so the cluster is inserted
# after the verb there). It is not a constant any more because the one thing that may not travel on
# it is the cluster: a list assembled at the call site is the only form that can carry it.


# The two issuers `ingress_generator.py` (once `create_ingress_yml.sh`) and both tekton
# EventListener ingress templates name by hand, so these names are load-bearing and only the solver
# moves. `privateKeySecretRef` is carried
# over from the script this replaced exactly as it stood -- both issuers pointed at a secret named
# `letsencrypt` -- because renaming it is a different question ("does staging reuse production's ACME
# account key?") with a migration attached, and this ticket said *solver*, not *secret*.
LETSENCRYPT_ISSUERS = (
    {"name": "letsencrypt-staging",
     "server": "https://acme-staging-v02.api.letsencrypt.org/directory",
     "secret": "letsencrypt"},
    {"name": "letsencrypt",
     "server": "https://acme-v02.api.letsencrypt.org/directory",
     "secret": "letsencrypt"},
)

# Where the ACME account's contact address comes from -- the same key the deleted script read with
# `jq -r` out of `platform_config/<environment>/static.json`.
CLUSTER_MANAGER_EMAIL_KEY = "CLUSTER_MANAGER_EMAIL"

# What a class is called when `static.json` does not say. `constants.DEFAULT_NAMESPACES` is the
# sibling this mirrors: a name the configuration is asked for and code answers for, so that renaming
# the key in one file is not a code change. It is not a second answer, and this is the line that
# proves it: the value is read out of `constants.DEFAULT_STATIC_ANSWERS` under the key this read
# takes (`TRAEFIK_INGRESS_CLASS`, built the same way `_component_value` builds it), so the one place
# the word `traefik` is written as this key's default is `constants.py`. It used to be written
# here, which is how the routing path acquired its own private L0 while the read path had none.
# Indexing the table rather than calling the accessor is deliberate: a missing entry should be a
# `KeyError` on import and not a `None` that reaches `spec.ingressClassName` months later.
DEFAULT_INGRESS_CLASSES = {COMPONENT: DEFAULT_STATIC_ANSWERS[component_config_key(COMPONENT, "INGRESS_CLASS")]}


# ---------------------------------------------------------------------------------------
# the answer
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingInstallResult:
    """What this run decided, what it applied, and what would have decided differently.

    The reason it is a value and not a log stream: the CLI prints it, a later caller
    branches on it, and a decision that lives only in prose is a decision nobody can act on twice.
    `presence` is carried along so the reader can see the read the decision was made from -- the two
    must never disagree, and if they do this is where the difference is visible.
    """

    environment: str
    namespace: str
    ingress_class: str
    classification: str
    action: str
    installs_traefik: bool
    label_filter: str
    update: bool
    crds_applied: bool
    releases_applied: bool
    issuers_applied: bool
    consent: str = CONSENT_NOT_NEEDED
    presence: object = field(compare=False, repr=False, default=None)

    def command(self, *flags) -> str:
        """The same command, spelled the way it would be typed to change the outcome."""
        environment = self.environment or "<environment>"
        return " ".join(["kmt routing install", environment] + list(flags))

    def summary(self) -> str:
        """A handful of lines for whoever ran the command. The detail is in the log.

        This is deliberately not a restatement of the decision table: the warnings already say what
        was found and what `--update` would do, and a summary that repeats them is one more place
        that can disagree with the cluster. What is worth printing is the *fact* of what changed --
        what was selected, whether the definitions moved, where the class points -- because that is
        what a scripted run has to be able to read.
        """
        applied = "kubementat's Traefik was applied" if self.installs_traefik \
            else "kubementat's Traefik was not applied"
        lines = [
            f"routing applied to {self.namespace} through {self.label_filter}: {applied}.",
            f"the cluster answered: {self.classification}.",
        ]
        if self.crds_applied:
            lines.append("the chart's CustomResourceDefinitions were applied before the release.")
        if self.issuers_applied:
            lines.append(f"the ClusterIssuers solve through IngressClass {self.ingress_class}.")
        if self.consent == CONSENT_ACCEPTED:
            lines.append("the second-controller warning was accepted by --yes.")
        if not self.installs_traefik:
            lines.append(f"{self.command('--update')} is the command that would change this.")
        return "\n".join(lines)


def _apply_note(result: RoutingInstallResult) -> None:
    """One INFO line that is the decision, readable without the log. Kept for one call site."""
    logging.info("routing decision: %s (%s), filter %s", result.action,
                 result.classification, result.label_filter)


# ---------------------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------------------


def install_routing(ctx, update: bool = False,
                    assume_yes: bool = False) -> RoutingInstallResult:
    """Apply the routing group, installing Traefik only when the cluster has none.

    Parameters
    ----------
    ctx:
        The `Context` the command resolved: whose `platform_config` this is, and which cluster
        it is about to apply a controller into. `HelmUtils` takes this object and nothing else, so
        a routing install that carried the two halves as loose arguments would have to build a
        second `Config` and a second cluster read to reach it. Nothing here derives a team
        from the environment or defaults one -- and nothing here needs one: the namespace the
        release and the issuers go into is `Config.component_namespace`, which is the environment's
        own `static.json`, and the only other key read is `CLUSTER_MANAGER_EMAIL`, also
        environment-level. There is no `require_team` because there is no team-scoped read to guard.
    update:
        Upgrade in place instead of skipping, which is a different operation rather than a bigger
        one: the repository index is refreshed and the chart's CustomResourceDefinitions are applied
        server-side *before* the release, because Helm will not do either of those for a release that
        already exists.
    assume_yes:
        The scripted answer to the one branch that needs consent -- a foreign controller being
        overwritten. It does not silence the warning, it answers it, and the difference is stated in
        the log rather than skipped.

    Returns
    -------
    RoutingInstallResult
        The decision, what was applied, and what would have changed it.

    Raises
    ------
    FileNotFoundError:
        When the environment has no `static.json`, which is what every other command does too.
    kubementat.shell.CommandExecutionError:
        When `helmfile apply` fails, or when the `--update` CRD step fails: both raise with the
        command's output attached, like every other apply in the codebase. Nothing retries, so a run
        that half-applied is the operator's to read rather than a second attempt's to hide.
    click.ClickException:
        When consent is needed and there is no terminal to ask in. It says so and names `--yes`, and
        applies nothing at all.
    """
    environment = ctx.environment
    ingress_class, namespace = _configuration(ctx)
    presence = traefik_presence.discover(ctx, namespace=namespace)

    installs_traefik, action, consent = _decide(presence, namespace, update, assume_yes)
    label_filter = INSTALL_LABEL_FILTER if installs_traefik else SKIP_LABEL_FILTER
    result = RoutingInstallResult(
        environment=environment,
        namespace=namespace,
        ingress_class=ingress_class,
        classification=presence.classification,
        action=action,
        installs_traefik=installs_traefik,
        label_filter=label_filter,
        update=update,
        crds_applied=False,
        releases_applied=False,
        issuers_applied=False,
        consent=consent,
        presence=presence,
    )
    _apply_note(result)

    # `--update` is the only branch that touches the definitions, and it does it before the release
    # it upgrades. On a first install the chart's own `crds/` land with `helm install`, and on a
    # default skip nothing is being changed -- which is the point of not running it there.
    if update:
        _apply_chart_crds_before_the_release(ctx=ctx)
        result = _as(result, crds_applied=True)

    HelmUtils(ctx).helmfile_apply(
        environment_name=environment,
        helmfile_label_filter=result.label_filter,
        interactive=False)
    result = _as(result, releases_applied=True)

    _apply_cluster_issuers(ingress_class, ctx)
    result = _as(result, issuers_applied=True)

    _warn_when_nothing_serves_the_class(ctx, ingress_class)
    return result


# ---------------------------------------------------------------------------------------
# step 1: the two values, read the way every other component's are read
# ---------------------------------------------------------------------------------------


def _configuration(ctx) -> tuple:
    """The namespace and the class, from the configuration, never from a literal here.

    `component_namespace` is the accessor helmfile, the tunnels and the uninstall sweep all read the
    namespace through, so a routing command that used its own `traefik` string would be the second
    answer to a question that has one. The class goes through the same rule (`_component_value`:
    config first, code second, warn once), for the reason `config.py` gives -- a missing key is not
    an error, and inventing a name for it is worse.

    The team is carried and never consulted. The `Config` the `Context` carries was built from the
    `(environment, team)` pair that command resolved, and it supports `team=None` for the commands
    that run without one; both keys this function reads live in the environment's own
    `static.json`, so that `None` changes nothing it returns. The `require_team` that used to sit
    upstream of this line claimed it changed something, and refused `kmt routing install dev` over a
    read that never happens.

    One `Config`, and it is the one the `Context` carries. This function used to build a second one
    from `ctx.environment` and `ctx.team`, which is the shape that was refused here: the pair
    re-derived a
    root from the process's own environment, so a command told which root to read answered with a
    namespace from whichever tree `Config`'s default rule found. `HelmUtils` already stopped doing
    that for its own reads, which left this line as the last second read on the path.
    """
    config = ctx.config
    namespace = config.component_namespace(COMPONENT)
    ingress_class = config._component_value(COMPONENT, "INGRESS_CLASS", DEFAULT_INGRESS_CLASSES)
    return ingress_class, namespace


# ---------------------------------------------------------------------------------------
# steps 2 and 3: the decision table
# ---------------------------------------------------------------------------------------


def _decide(presence, namespace: str, update: bool, assume_yes: bool) -> tuple:
    """Read the classification, say what it means, return `(installs, action, consent)`.

    The action is the table. The *message* is what the table says plus whose Traefik it was, because
    warning about "an existing installation" without saying whether kubementat put it there would be
    an honest-looking warning about the wrong thing.
    """
    classification = presence.classification
    if classification == traefik_presence.CLASSIFICATION_NONE:
        # The only branch that is not a warning: nothing is being left alone, so the log says what is
        # about to happen instead of what is not.
        logging.info("No Traefik was found on this cluster, so kubementat installs its own: chart"
                     " %s into namespace %s, through %s%s. cert-manager is part of the same group and"
                     " is applied either way.",
                     TRAEFIK_CHART, namespace, INSTALL_LABEL_FILTER,
                     " (its CustomResourceDefinitions first)" if update else "")
        return True, ACTION_INSTALL, CONSENT_NOT_NEEDED

    if classification == traefik_presence.CLASSIFICATION_OURS:
        if update:
            logging.warning("Upgrading the Traefik kubementat installed: %s. Its"
                            " CustomResourceDefinitions are applied first, because Helm upgrades a"
                            " release and never the definitions it was installed with.",
                            _found(presence, presence.owned_findings()))
            return True, ACTION_UPGRADE, CONSENT_NOT_NEEDED
        logging.warning("Leaving Traefik alone: %s, installed by kubementat, is already what the"
                        " configuration names in namespace %s. Its release is not applied and"
                        " editing traefik's values then running this command again changes nothing."
                        " %s is what would change it -- the rest of group=routing (cert-manager) is"
                        " applied either way.",
                        _found(presence, presence.owned_findings()), namespace,
                        _update_command(presence))
        return False, ACTION_SKIP, CONSENT_NOT_NEEDED

    # Anything else is `foreign`: kubementat cannot say it put that controller there.
    if not update:
        logging.warning("The cluster already runs a Traefik kubementat did not install: %s."
                        " kubementat's own release is not applied into namespace %s, and"
                        " cert-manager and the ClusterIssuers are still applied through %s. %s is"
                        " the flag that changes this, and using it means a second controller, a"
                        " second class and a second status writer.",
                        _found(presence, presence.foreign_findings()), namespace, SKIP_LABEL_FILTER,
                        _update_command(presence))
        return False, ACTION_SKIP, CONSENT_NOT_NEEDED

    consent = _consent_to_a_second_controller(presence, namespace, assume_yes)
    if not _found_release(presence):
        # The manifest-install row of the table: there is a controller but no release to upgrade, so
        # `--update` cannot "upgrade" it. What it does is apply the definitions underneath a
        # controller that stays, which is a sentence the operator deserves before it happens.
        logging.warning("There is no helm release behind the Traefik that is running (%s), so"
                        " --update is not upgrading it: the chart's CustomResourceDefinitions are"
                        " applied while the existing controller stays, and kubementat's own release"
                        " is applied alongside it.",
                        _found(presence, presence.foreign_findings()))
    return True, ACTION_INSTALL, consent


def _consent_to_a_second_controller(presence, namespace: str, assume_yes: bool) -> str:
    """Say the second-controller sentence, then get the answer the branch is running on.

    The sentence comes first and is not conditional on the answer: a warning that is only printed
    when somebody has already agreed to it is not a warning. `--yes` answers it, and saying
    "accepted because --yes" is the difference between a scripted run and a silent one -- `kmt
    uninstall` keeps the same distinction, and for the same reason (`_confirm_uninstall`).
    """
    logging.warning("--update on a cluster that already runs Traefik applies kubementat's own"
                    " release into namespace %s beside it: that is a second controller, a second"
                    " class and a second status writer. What is already running is left running --"
                    " kubementat has no way to stop two controllers both writing Ingress.status.",
                    namespace)
    if assume_yes:
        logging.warning("Accepted because --yes was given, so this run continues without asking.")
        return CONSENT_ACCEPTED
    if not sys.stdin.isatty():
        raise click.ClickException(
            f"Installing a second Traefik next to the one already running needs confirmation, but"
            f" stdin is not a terminal. Re-run with --yes (or -y) to accept the second-controller"
            f" warning, or run {_update_command(presence)} alone to see what is there: "
            f"kmt routing status {presence.environment or '<environment>'}.")
    while True:
        answer = input("A Traefik is already running here. Install a second controller, class and"
                       " status writer alongside it? (yes/no): ").strip().lower()
        if answer in ("y", "yes"):
            return CONSENT_ASKED
        if answer in ("n", "no"):
            raise click.ClickException(
                "Cancelled. Nothing was applied and the controller that is running was left"
                " alone; re-run with --yes (or -y), or answer yes, to go ahead.")
        logging.info("Please answer yes or no.")


# ---------------------------------------------------------------------------------------
# step 4: the definitions `--update` owes, ordered before the release
# ---------------------------------------------------------------------------------------


def _apply_chart_crds_before_the_release(chart: str = TRAEFIK_CHART, ctx=None) -> None:
    """Refresh the repository index, then apply the chart's CRDs server-side.

    Both halves are only ever reached under `--update`, and both come before the release apply. The
    index is refreshed first because `helm show crds` reads the copy of the chart this machine has:
    asking for definitions from a stale index is how an "upgrade" ships the definitions of the
    version that was already installed, which is the exact failure the sequence exists to prevent.

    Raises
    ------
    kubementat.shell.CommandExecutionError:
        If either read fails. Proceeding would mean reporting an *upgraded* release over
        definitions that cannot serve it, and that is the one outcome here the next command cannot
        diagnose -- a controller that starts but 404s every custom resource looks, from the outside,
        exactly like a controller that installed correctly.
    """
    repository = chart.partition("/")[0]
    if repository:
        refreshed = shell.run(["helm", "repo", "update", repository], capture=True, check=False)
        if not refreshed.ok:
            # Not fatal on its own: a repository helm has never added is a repository it cannot
            # refresh, and the definitions read below is the step that decides. The failure is
            # reported rather than swallowed, because "the index is old" is the sentence that would
            # otherwise be missing from a run that upgraded to something other than what it shipped.
            logging.warning("helm could not refresh the index for repository '%s' (exit %s), so the"
                            " chart this run reads may be the one already downloaded: %s",
                            repository, refreshed.returncode,
                            (refreshed.stderr or refreshed.stdout or "").strip())
    else:
        logging.warning("chart %s names no repository, so there is no index to refresh and the"
                        " definitions below come from whatever helm has on disk.", chart)

    shown = shell.run(["helm", "show", "crds", chart], capture=True, check=False)
    definitions = (shown.stdout or "").strip()
    if not shown.ok:
        raise shell.CommandExecutionError(shown, (
            f"--update asks for {chart}'s CustomResourceDefinitions before it upgrades anything, and"
            f" `helm show crds {chart}` failed. An upgrade over definitions that cannot serve the new"
            f" controller is not an upgrade, so nothing was applied. Most often the repository is"
            f" not added to this helm:\n{shell_output(shown)}"))
    if not definitions:
        raise shell.CommandExecutionError(shown, (
            f"--update asks for {chart}'s CustomResourceDefinitions before it upgrades anything, and"
            f" `helm show crds {chart}` answered with nothing. A chart that ships no crds/ is not"
            f" {TRAEFIK_CHART}, and applying nothing then calling the release upgraded is what this"
            f" check exists to stop."))

    logging.info("Applying %s's CustomResourceDefinitions before the release, server-side and with"
                 " conflicts resolved, because Helm applies a chart's crds/ on install and never"
                 " again afterwards.", chart)
    # The apply is addressed. Definitions applied to a cluster other than the one the
    # upgrade is about to run on are the `--update` that appears to work and serves nothing.
    applied = shell.run(["kubectl", "apply", *_kubectl_args(ctx),
                         "--server-side", "--force-conflicts", "-f", "-"],
                        input=definitions, capture=True, check=False)
    if not applied.ok:
        raise shell.CommandExecutionError(applied, (
            f"the CustomResourceDefinitions of {chart} could not be applied (exit"
            f" {applied.returncode}), so no release was upgraded. `kubectl apply --server-side"
            f" --force-conflicts` owns the fields it applies on purpose; a conflict it refuses is"
            f" usually somebody else's controller having written the same definitions, and that is"
            f" worth reading before it is forced again:\n{shell_output(applied)}"))
    for line in (applied.stdout or "").splitlines():
        if line.strip():
            logging.info("  %s", line.strip())


def shell_output(result) -> str:
    """Whatever a command printed, for an exception message that can be acted on."""
    text = ((result.stderr or "") + (result.stdout or "")).strip()
    return text if text else "(the command printed nothing)"


# ---------------------------------------------------------------------------------------
# step 5: the ClusterIssuers, whose solver is the one thing that has to move
# ---------------------------------------------------------------------------------------


def _kubectl_args(ctx) -> list:
    """The cluster a surviving `kubectl` has to carry, or the empty list when there is no Context.

    A helper rather than a call to `KubernetesUtils.kubectl_args` because the two callers here need
    it before and independent of anything a `KubernetesUtils` would build: this is an argv being
    assembled, not a client being used. A `None` ctx is not a licence to guess -- it is a caller
    that has not resolved one, and `kubectl_target_args` is where that gets refused.
    """
    if ctx is None:
        return []
    from kubementat.kubernetes_utils import KubernetesUtils

    return KubernetesUtils(ctx).kubectl_args


def _apply_cluster_issuers(ingress_class: str, ctx) -> None:
    """Apply both issuers with the solver pointed at the configured class.

    The key that moves is `solvers[0].http01.ingress.ingressClassName`. The old field, `class`,
    writes the `kubernetes.io/ingress.class` annotation, and Traefik's default Kubernetes Ingress
    provider does not read annotations it was not told about -- so the solver Ingress is created,
    nothing routes the challenge, and the certificate never appears. That is why this runs after the
    releases and before the verification read: the solver has to name a class the cluster can show.

    Both issuer names are kept, and only the solver is touched, for the reason in the module header:
    `ingress_generator.py` (once `create_ingress_yml.sh`) and both EventListener templates
    reference `letsencrypt` by string, so
    renaming one is a migration nobody asked for.

    It takes the `Context` rather than a `Config` because the apply is one of the kubectl calls
    that stay kubectl — a manifest written to stdin, where server-side apply would mean owning the
    document splitting — and a manifest applied to the wrong cluster is a certificate that renews
    somewhere else. The email is read from `ctx.config`, which is the same read under a different
    name.
    """
    email = _cluster_manager_email(ctx.config)
    for issuer in LETSENCRYPT_ISSUERS:
        manifest = _cluster_issuer(issuer, email, ingress_class)
        logging.info("Applying ClusterIssuer %s (ACME server %s, solver ingressClassName %s) ...",
                     issuer["name"], issuer["server"], ingress_class)
        # `check=True` is deliberate: an issuer that is not there means every certificate in the
        # platform stops renewing, and a run that reported success while that failed is the failure
        # mode this whole file is written against.
        shell.run(["kubectl", "apply", *_kubectl_args(ctx), "-f", "-"], input=yaml.dump(
            manifest, default_flow_style=False, sort_keys=False))


def _cluster_issuer(issuer: dict, email: str, ingress_class: str) -> dict:
    """One `ClusterIssuer`, as the deleted script wrote it, with the solver as the only delta."""
    return {
        "apiVersion": "cert-manager.io/v1",
        "kind": "ClusterIssuer",
        "metadata": {"name": issuer["name"]},
        "spec": {
            "acme": {
                "server": issuer["server"],
                "email": email,
                "privateKeySecretRef": {"name": issuer["secret"]},
                "solvers": [{"http01": {"ingress": {"ingressClassName": ingress_class}}}],
            }
        },
    }


def _cluster_manager_email(config: Config) -> str:
    """The ACME contact address, from `platform_config/<environment>/static.json`.

    There is no built-in default for an email address, so this is the one key the rule cannot answer
    for. Applying an issuer with a blank or placeholder contact is worse than not applying it --
    cert-manager registers the account and Let's Encrypt rejects a malformed contact, which is an
    issuance failure that stays invisible until a certificate expires -- so this says what is missing
    and stops, exactly the way `helm_utils._grafana_admin_credentials` says grafana cannot be built
    without an admin user and stops.
    """
    value = config.get('env_static').get(CLUSTER_MANAGER_EMAIL_KEY)
    value = value.strip() if isinstance(value, str) else ""
    if not value or value.startswith("REPLACEME"):
        raise click.ClickException(
            f"{CLUSTER_MANAGER_EMAIL_KEY} is not set in {config.env_static_config_path}. Both"
            f" ClusterIssuers register an ACME account with it, so an issuer applied without one"
            f" cannot issue a certificate. Put the address in that file -- kmt config set <dir>"
            f" <environment> {CLUSTER_MANAGER_EMAIL_KEY}=<address> is the verb, and nothing rewrites"
            f" an answer that is there -- and run this command again.")
    return value


# ---------------------------------------------------------------------------------------
# step 6: the read that costs one call and pays for the whole feature
# ---------------------------------------------------------------------------------------


def _warn_when_nothing_serves_the_class(ctx, ingress_class: str) -> None:
    """Warn when the class the solvers now name is not an IngressClass the cluster has.

    One read, and it is the only check here on the *result* rather than on the sequence. An Ingress
    (and a cert-manager solver Ingress) that names a class which does not exist is accepted, stored
    and served by nobody forever, so the only failure signal is this one: an operator who renames
    `TRAEFIK_INGRESS_CLASS` without touching the controller sees a line saying the solver now points
    at something that answers nothing.

    Which controller the existing classes bind is deliberately not the question. A class named here
    is served by whatever controller binds it, and the solver works through that -- whether it is
    Traefik's or somebody else's is the ingress's problem, not the challenge's, and warning about it
    would be a warning about a configuration that does issue certificates.

    This is the conversion to the client: `NetworkingV1Api.list_ingress_class` and not a `kubectl get`,
    so the
    cluster is the `KubernetesUtils`'s own rather than a flag that could be missing. What the
    check warns about is unchanged; only how it asks moved.
    """
    from kubementat.kubernetes_utils import KubernetesUtils

    try:
        classes = KubernetesUtils(ctx).list_ingress_classes()
    except client.exceptions.ApiException as error:
        logging.warning("The ClusterIssuers now solve through IngressClass %s, and no IngressClass"
                        " could be read to check that one exists (%s). If it does not, the solver"
                        " creates an Ingress nothing serves and issuance fails without an error.",
                        ingress_class, _refusal(error))
        return
    names = sorted(str(getattr(item.metadata, "name", "") or "") for item in classes
                   if getattr(item, "metadata", None) is not None)
    if ingress_class in names:
        logging.info("IngressClass %s is on the cluster, so the solver has something to solve"
                     " through.", ingress_class)
        return
    logging.warning("Nothing serves the IngressClass the ClusterIssuers were just pointed at:"
                    " no IngressClass named %s exists. The solver will create an Ingress bound to a"
                    " class no controller watches, and certificate issuance will fail silently,"
                    " forever -- %s. Either name a class that exists or install a controller that"
                    " creates it.",
                    ingress_class,
                    f"the classes present are: {', '.join(names)}" if names
                    else "the cluster has no IngressClass at all")


def _refusal(error) -> str:
    """The API's own sentence about a read it would not answer, without the SDK's wrapper.

    `body` is what the server actually replied with and usually names the kind that is missing;
    `reason` is the HTTP phrase, which alone is a word an operator cannot act on.
    """
    body = getattr(error, "body", None)
    if body:
        text = body if isinstance(body, str) else str(body)
        try:
            decoded = json.loads(text)
            message = decoded.get("message") if isinstance(decoded, dict) else None
            if message:
                return str(message)
        except ValueError:
            return text
    return str(getattr(error, "reason", "") or error)


# ---------------------------------------------------------------------------------------
# the small rules the sentences are built on
# ---------------------------------------------------------------------------------------


def _found(presence, findings) -> str:
    """What was found, as one sentence. Namespace, release, chart and revision, class, or pod.

    The three kinds are reported together because the interesting case is the cluster that carries
    two controllers: naming only the release would describe half of what the cluster runs.
    """
    phrases = []
    for finding in findings:
        for release in finding.releases:
            phrases.append(f"release {release.name} in namespace {release.namespace} (chart"
                           f" {release.chart}, status {release.status}, revision {release.revision})")
        for ingress_class in finding.classes:
            phrases.append(f"IngressClass {ingress_class.name} bound to controller"
                           f" {ingress_class.controller}")
        for pod in finding.pods:
            phrases.append(f"pod {pod.namespace}/{pod.name} ({', '.join(pod.images)})")
    if not phrases:
        return "nothing a probe could name"
    return "; ".join(phrases)


def _found_release(presence) -> tuple:
    """Every release the probes found, whatever namespace it lives in."""
    return tuple(release for finding in presence.findings for release in finding.releases)


def _update_command(presence) -> str:
    """The command that changes this outcome, spelled so it can be pasted.

    The warning has to name the flag; a warning that says "an installation exists" without
    saying which flag is the answer is a warning the operator cannot act on. The environment is the
    one the presence was read from, so the line is the line that applies to this cluster.
    """
    return presence.command("--update")


def _as(result: RoutingInstallResult, **changes) -> RoutingInstallResult:
    """A new result with `changes` applied. The result is frozen, so the decision is not edited."""
    return replace(result, **changes)
