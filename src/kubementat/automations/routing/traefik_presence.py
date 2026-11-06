"""What Traefik is already on this cluster, read from the cluster.

`kmt routing status` prints what this module answers, and the answer has to be worth reading
before someone types `--update`. That is the whole reason discovery is a module of its own: the
guard in `routing.py` decides, and a decision is only as good as the read underneath it.

**Why three reads and not a name list.** k3s's bundled controller is a real helm release pair in
`kube-system`; a controller installed from plain YAML makes no release at all; and a release
somebody renamed is a release no name list can find. So each of the three questions below is
asked of the cluster on its own terms, and the answers are unioned:

| probe | read | what it contributes |
|---|---|---|
| helm | ``helm list --all-namespaces``, addressed to the cluster, with the statuses named | rows whose **chart** is ``traefik`` or ``traefik-<version>`` |
| ingressclass | ``NetworkingV1Api.list_ingress_class`` | classes whose ``spec.controller`` is `constants.TRAEFIK_CONTROLLER_NAME` |
| pods | ``CoreV1Api.list_pod_for_all_namespaces`` | a container running a traefik image |

Matching the *chart* rather than the release name is the point of the first row: the case a name
list gets wrong is exactly the release somebody renamed. The second row is the controller string
the chart's own `_helpers.tpl` writes, and it is not `automation.py`'s
`NGINX_INGRESS_CONTROLLER_PREFIX` (`k8s.io/ingress-nginx`), which is pointed the other way on
purpose — conflating the two would make the nginx sweep delete the class kubementat installed.

**The classification, and why `foreign` wins.** `TraefikPresence.classification` is exactly one of
`none` / `ours` / `foreign`, resolved `foreign > ours > none`. A finding counts as kubementat's
own only when the cluster says so: `ours` means a Traefik *chart release* in the namespace the
configuration names for the component, read through `Config.component_namespace("traefik")` and
never from a literal here. A class or a pod sitting in that same namespace beside that release
corroborates it; the same class or pod found anywhere else — or in that namespace with no release
to attach it to — is not kubementat's, because nothing in kubementat put it there. So a cluster
that carries both kubementat's release and k3s's answers `foreign` rather than skipping quietly,
and the report names both. A manifest-installed controller with no release at all is `foreign`
too, whatever namespace it happens to be in, and says which probe found it.

**Nothing here writes.** No install, no apply, no `helm`, no `kubectl` beyond the three reads, and
no import of `routing.py` (the dependency runs the other way). Every read is `capture=True,
check=False`, so a cluster that refuses to answer is a *finding* and not an exception: a cluster
old enough to lack `IngressClass` says so with an error and nothing to report, and a cluster that
answers nothing at all still gets an answer — `none`, with the report saying plainly that `none`
here means "nothing could be read", not "there is no Traefik".
"""

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Optional

from kubernetes import client

from kubementat import shell
from kubementat.core.config.keys import component_config_key
from kubementat.core.constants import COMPONENT, TRAEFIK_CHART, TRAEFIK_CONTROLLER_NAME
from kubementat.helm_utils import helm_list_releases_argv
from kubementat.kubernetes_utils import KubernetesUtils

# The only three answers `classification` can take.
CLASSIFICATION_NONE = "none"
CLASSIFICATION_OURS = "ours"
CLASSIFICATION_FOREIGN = "foreign"

# What the three reads are, and what each one is called in the report. Two channels, and the report
# says so: the release read is a command (`helm` is not a Kubernetes client, so there is no client
# call to convert it to, and it has to be told which cluster it addresses) and the other two are API
# calls, which is
# why their half of the table is a method and not an argv. The method names are spelled here rather
# than assembled from the object so the report line says, in words a reader can look up, which API
# answered -- and so the guard that used to excuse this module's two `kubectl` reads has nothing
# left to excuse.
#
# `HELM_LIST_RELEASES_ARGV` is deliberately not restated here. The release read is the product's one
# read (`helm_utils.helm_list_releases_argv`, which points it at the cluster it was told about), and a
# probe that answered "what releases live here" its own way is the disagreement that would show the
# lane a release the guard cannot see -- the same reason `automation.py`'s uninstall sweep and its
# Traefik CRD sweep read it instead of spelling an argv of their own. A narrower read here would
# also have been the one that answers differently per helm major, because on the helm this product
# pins the six statuses *are* the default, so naming them is a portability decision and not a
# widening (see the reasoning written on the constant). `--all` is not in that read either:
# helm 4 removed the flag, so `helm list --all --output json` exits 1 with `unknown flag: --all`
# and reads as "the cluster answered nothing".
READ_INGRESS_CLASS = ("NetworkingV1Api.list_ingress_class",)
READ_PODS = ("CoreV1Api.list_pod_for_all_namespaces",)

# What each read is called in the report. Three names because "which probe found it" is the question
# an operator is actually asking: the same Traefik can arrive as a release, as a class, or as a pod,
# and which one it was says who installed it.
PROBE_HELM = "helm release list"
PROBE_INGRESS_CLASS = "ingressclass"
PROBE_PODS = "pods"

# The two channels, spelled once. The release read is a command and the other two are API calls,
# so a failed read has two different reasons to say "no answer" and the report says which one.
COMMAND_CHANNEL = "command"
API_CHANNEL = "api"

# `traefik` or `traefik-<version>`, and nothing else: `traefik-crds` is deprecated, ships no proxy,
# and counting it would report a controller that cannot route anything. The version group has to
# start with a digit for the same reason — otherwise `traefik-crds-1.18.0` reads as a match.
_CHART = re.compile(r"^traefik(?:-(?P<version>\d.*))?$", re.IGNORECASE)

# A traefik image is a container name somebody else chose, so the match is deliberately loose:
# `traefik`, `rancher/mirrored-library-traefik:3.3.2`, and `ghcr.io/traefik/traefik:v3.7` are all
# a controller running, which is the answer this probe is for.
_TRAEFIK_IMAGE = re.compile(r"traefik", re.IGNORECASE)


# ---------------------------------------------------------------------------------------
# what the cluster answered
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HelmRelease:
    """One `helm list` row whose chart is Traefik, with its namespace carried along.

    The namespace is the entire ours/foreign split, so it is not metadata: `chart` and `name`
    without it would be a release that could be anybody's.
    """

    namespace: str
    name: str
    chart: str
    status: str
    revision: str

    def label(self) -> str:
        return f"{self.name}, chart {self.chart}, status {self.status}, revision {self.revision}"


@dataclass(frozen=True)
class IngressClass:
    """An IngressClass bound to Traefik's controller.

    `spec.service` is what makes a cluster-scoped object findable by namespace, which is why the
    class can say whose controller it is: the chart points it at the release's own Service. A
    class that names no Service cannot be localized, and is reported as the cluster-scoped object
    it is.
    """

    name: str
    controller: str
    service_namespace: Optional[str] = None
    service_name: Optional[str] = None

    def label(self) -> str:
        service = ""
        if self.service_namespace:
            service = f", service {self.service_namespace}" + (
                f"/{self.service_name}" if self.service_name else "")
        return f"{self.name} -> controller {self.controller}{service}"


@dataclass(frozen=True)
class TraefikPod:
    """One pod carrying at least one container whose image is a traefik image."""

    namespace: str
    name: str
    images: tuple

    def label(self) -> str:
        return f"{self.namespace}/{self.name} ({', '.join(self.images)})"


@dataclass(frozen=True)
class ProbeOutcome:
    """What one read did, whether or not the cluster answered.

    `objects` and `matches` are what let the report distinguish "the cluster answered and said
    there is no traefik" from "the cluster said nothing", which is the difference between a clean
    cluster and an unreachable one.
    """

    probe: str
    read: tuple
    ok: bool
    returncode: int
    objects: int = 0
    matches: int = 0
    detail: str = ""
    # Which of the two channels answered. "exit 3" is what a command failed with and "HTTP 404"
    # is what an API refused with; rendering one as the other is the kind of sentence that sends an
    # operator to look for a bug in a script that is not there.
    channel: str = COMMAND_CHANNEL
    # The raw answer is kept for one thing only: saying what the read printed when that printout
    # is not the JSON the read expected. It is never rendered whole, because a cluster that emits
    # two hundred lines of warnings still has to yield a report that fits on a screen.
    stdout: str = ""

    def line(self) -> str:
        if self.ok:
            answered = f"{self.objects} object(s) read, {self.matches} of them traefik"
        elif not self.returncode:
            answered = "an answer this read could not parse"
        else:
            answered = (f"no answer (HTTP {self.returncode})"
                        if self.channel == API_CHANNEL else f"no answer (exit {self.returncode})")
        rendered = f"  {self.probe:<20} {' '.join(self.read)} -> {answered}"
        return rendered + (f"\n  {'':<20} {self.detail}" if self.detail else "")


# ---------------------------------------------------------------------------------------
# one Traefik, where it is, and who put it there
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TraefikFinding:
    """Everything three probes saw at one address: a namespace, or the cluster at large.

    `owned` is the claim this module is honest about, so it is stated as a rule rather than as an
    opinion: True only for kubementat's own address — a Traefik chart release in the namespace the
    configuration names, or a class/pod sitting in that namespace next to that release.
    """

    namespace: Optional[str]
    releases: tuple = ()
    classes: tuple = ()
    pods: tuple = ()
    owned: bool = False

    @property
    def probes(self) -> tuple:
        """Which reads found this finding, in the order they are reported."""
        found = []
        if self.releases:
            found.append(PROBE_HELM)
        if self.classes:
            found.append(PROBE_INGRESS_CLASS)
        if self.pods:
            found.append(PROBE_PODS)
        return tuple(found)

    def lines(self) -> list:
        rendered = []
        for release in self.releases:
            rendered.append(f"  release: {release.label()}")
        for ingress_class in self.classes:
            rendered.append(f"  ingressclass: {ingress_class.label()}")
        for pod in self.pods:
            rendered.append(f"  pod: {pod.label()}")
        rendered.append(f"  found by: {', '.join(self.probes)}")
        return rendered

    def attribution(self, configured_namespace: str) -> str:
        """Whose this is, in one line the reader can act on."""
        if self.owned:
            return f"kubementat's own ({self.namespace})"
        if self.namespace is None:
            return ("not kubementat's: a cluster-scoped class with no Service to say whose "
                    "controller it is")
        if self.namespace == configured_namespace:
            return (f"not kubementat's: it is in {self.namespace}, the namespace kubementat names"
                    " for traefik, but no release in that namespace is what put it there")
        return (f"not kubementat's: the configuration puts traefik in {configured_namespace}, and"
                f" this is in {self.namespace}")


@dataclass(frozen=True)
class TraefikPresence:
    """The whole answer: what was found, whose it is, and what `install` would do about it.

    The plan's diagram calls this `Found`. Everything the CLI and the guard need is on the object
    so both callers read the same answer — a status report that decided for itself and a guard
    that decided differently is two products wearing one command name.
    """

    environment: str
    namespace: str
    findings: tuple = ()
    probes: tuple = ()

    @property
    def classification(self) -> str:
        """`foreign` > `ours` > `none`, and no other value.

        Anything the cluster has that kubementat cannot account for outranks what it can, because
        the consequential case is a second controller: a cluster holding kubementat's release and
        somebody else's has to be told it has two, not be shown the friendly half.
        """
        if not self.findings:
            return CLASSIFICATION_NONE
        if all(finding.owned for finding in self.findings):
            return CLASSIFICATION_OURS
        return CLASSIFICATION_FOREIGN

    @property
    def answered(self) -> bool:
        """True when at least one read got a reply. False means the cluster is not talking."""
        return any(probe.ok for probe in self.probes)

    def owned_findings(self) -> tuple:
        return tuple(finding for finding in self.findings if finding.owned)

    def foreign_findings(self) -> tuple:
        return tuple(finding for finding in self.findings if not finding.owned)

    def command(self, *flags) -> str:
        """The command that would change this, spelled so it can be pasted.

        `--update` is named rather than run: this module reads, and a report that installed
        something would not be a report. The environment is the one this presence was read from,
        so the line a reader copies is the line that applies to the cluster they are looking at.
        """
        environment = self.environment or "<environment>"
        return " ".join(["kmt routing install", environment] + list(flags))

    def action_lines(self) -> list:
        """What `kmt routing install` would do, which is why this is worth running first.

        Names the command that would change the outcome, per branch — a warning that says "an
        existing installation was found" without saying which flag is the answer is a warning the
        operator cannot act on.
        """
        namespace = self.namespace
        update = self.command("--update")
        if self.classification == CLASSIFICATION_NONE:
            lines = [
                f"nothing found, so {self.command()} would install it:"
                f" chart {TRAEFIK_CHART} into namespace {namespace}, through group=routing.",
            ]
            if not self.answered:
                lines.append("that is a guess: no read answered, so a cluster that already has a"
                             " controller would be told to install a second one.")
            return lines
        if self.classification == CLASSIFICATION_OURS:
            return [
                f"{self.command()} would leave Traefik alone and apply the rest of group=routing"
                " (cert-manager is applied either way).",
                f"editing traefik's values and running it again changes nothing; {update} is the"
                " flag that upgrades it.",
            ]
        lines = [
            f"{self.command()} would leave Traefik alone and warn, then apply the rest of"
            " group=routing (cert-manager is applied either way).",
        ]
        if self.foreign_findings() and self.owned_findings():
            lines.append(f"the cluster carries both kubementat's release and another one, so"
                         f" {self.command()} is not the whole answer: check which controller the"
                         " IngressClass is bound to before changing anything.")
        lines.append(f"{update} would apply kubementat's own release into {namespace} on top of"
                     " what is there, which means a second controller, a second IngressClass, and"
                     " a second writer of Ingress.status.")
        return lines

    def report(self) -> str:
        """The text `kmt routing status` prints. Every fact in it came off the cluster.

        Per finding: namespace, release, chart+version, status, revision, the class(es), and which
        probe found it. Then the classification and the action `install` would take. Mixed
        findings are printed together — the winner is stated, it is not substituted.
        """
        key = component_config_key(COMPONENT, "DEPLOYMENT_NAMESPACE")
        lines = [
            "traefik on the cluster, read from the cluster. Nothing here was installed, changed"
            " or deleted.",
        ]
        if self.environment:
            lines.append(f"environment: {self.environment}")
        lines.append(f"namespace kubementat's configuration names for traefik: {self.namespace}"
                     f"  (from {key}, falling back to kubementat's default)")
        lines.append("")
        lines.append("what each read answered:")
        lines.extend(probe.line() for probe in self.probes)
        if not self.answered:
            lines.append("  none of the three reads answered, so this says nothing about whether"
                         " the cluster has a controller.")

        lines.append("")
        if not self.findings:
            lines.append("no traefik found by any of the three reads.")
        for number, finding in enumerate(self.findings, start=1):
            address = finding.namespace if finding.namespace is not None else "cluster-scoped"
            lines.append("")
            lines.append(f"finding {number}: {address} -- {finding.attribution(self.namespace)}")
            lines.extend(finding.lines())

        owned, foreign = len(self.owned_findings()), len(self.foreign_findings())
        lines.append("")
        lines.append(f"classification: {self.classification}"
                     f" ({owned} finding(s) kubementat accounts for, {foreign} it does not)")
        if self.classification == CLASSIFICATION_NONE and not self.answered:
            lines.append("'none' here means nothing could be read, not that there is no traefik.")
        lines.append("")
        lines.append(f"what {self.command()} would do:")
        lines.extend(f"  {line}" for line in self.action_lines())
        return "\n".join(lines)


# ---------------------------------------------------------------------------------------
# the three reads
# ---------------------------------------------------------------------------------------


def discover(ctx, namespace: Optional[str] = None, reader=None,
             cluster=None) -> TraefikPresence:
    """Read the cluster the way `kmt routing status` needs it answered.

    One `Context` and not two loose arguments, for the reason every class like this gives and
    for one more here: the read is addressed. The release read is a command, and a command
    that is not told which cluster to ask gets refused rather than pointed at whatever the running
    user's kubeconfig calls current -- so "whose cluster is this" arrives as a `Context` resolved
    once by the CLI, and this module never guesses it.

    Parameters
    ----------
    ctx:
        The `Context` the command resolved: whose `platform_config` the answer is read from, and
        which cluster it is read against. `namespace` is the only thing taken from the
        configuration here, and only when the caller has not resolved one.
    namespace:
        The namespace to attribute findings to, for a caller that has resolved it itself. That is
        `routing.py`'s shape: it decides before it discovers, and resolving twice is how two
        answers appear. When it is omitted, this function asks `Config.component_namespace`, which
        is the configuration's answer and not this module's.
    reader:
        Who answers the *command* read -- one call, `capture=True, check=False`. Defaults to
        `kubementat.shell.run`; a caller that already knows how its cluster answers -- a test
        holding a fixture cluster, or a tool reading a recorded transcript -- can hand that over
        instead. The seam is a parameter rather than a swapped module attribute because `shell` is
        imported here as the shared `kubementat.shell` module: patching
        `traefik_presence.shell.run` patches it for everything else in the process, which is how a
        test ends up green while the rest of the suite reads a machine nobody intended.
    cluster:
        Who answers the two *API* reads. Defaults to a `KubernetesUtils` bound to `ctx` -- bound,
        never resolved: it is told which cluster it is, which is the whole rule. The seam
        is here because two channels answering one question is exactly what the disagreement
        is about, and a test that wants one cluster to answer all three reads should be able to
        say so in one argument.

    Raises
    ------
    FileNotFoundError:
        If the environment has no `static.json` at all, which is what every other command does
        too — a status command that guessed a namespace rather than say it could not read the
        configuration would be a report that is quietly wrong.
    kubementat.errors.ClusterNotConfiguredError:
        When `ctx` carries no cluster to address. Nothing here reads a machine it was not pointed
        at, and "the release read could not be addressed" is a better answer than an answer.
    """
    if namespace is None:
        namespace = ctx.config.component_namespace(COMPONENT)
    return _read(namespace, environment=ctx.environment, reader=reader,
                 cluster=cluster if cluster is not None else KubernetesUtils(ctx))


def _read(configured_namespace: str, environment: str = "", reader=None,
          cluster=None) -> TraefikPresence:
    """The three reads, unioned. No configuration is touched from here down.

    Split out from `discover` so the cluster side can be answered without a configuration tree --
    but note that the namespace handed in is still the configuration's, never one read guessed at.
    Two of `discover`'s seams are resolved once here so the three reads below go through one
    answer rather than three defaults, which is the whole difference between a cluster that is
    asked twice and one that is asked once.
    """
    reads = reader or shell.run
    releases, helm = _helm_releases(cluster, reads)
    classes, ingress_class = _ingress_classes(cluster)
    pods, pods_probe = _pods(cluster)
    presence = TraefikPresence(
        environment=environment,
        namespace=configured_namespace,
        findings=_findings(configured_namespace, releases, classes, pods),
        probes=(helm, ingress_class, pods_probe),
    )
    _log(presence)
    return presence


def _helm_releases(cluster, reads=shell.run):
    """Probe 1: the releases whose *chart* is Traefik, wherever they live.

    The chart field is what the match is made on, and a row whose chart is missing is reported
    rather than skipped: `helm list` always prints a chart, so an empty one is either a release
    this helm version cannot describe or a different helm writing the row, and either way the
    operator should hear about it instead of being shown a clean list.

    The read is the product's one release read -- `helm_utils.helm_list_releases_argv`, and not an
    argv written here -- for the reason spelled on both of them: `--all` is a flag helm 4
    rejects, and the statuses the flag used to stand for are what a skip decision has to be able to
    see anyway. It is addressed to `cluster`, because that is what stops a controller that is running
    on one cluster from being counted as the answer to a question asked of another.
    """
    outcome = _run(PROBE_HELM, helm_list_releases_argv(cluster.target), reads)
    if not outcome.ok:
        return (), _failed(outcome)
    rows, problem = _json_rows(outcome, "helm")
    if rows is None:
        return (), _failed(outcome, problem)
    releases, skipped = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chart = str(row.get("chart") or "")
        if not chart:
            skipped.append(f"{row.get('namespace')}/{row.get('name')}")
            continue
        if _chart_is_traefik(chart):
            releases.append(HelmRelease(
                namespace=str(row.get("namespace") or ""),
                name=str(row.get("name") or ""),
                chart=chart,
                status=str(row.get("status") or ""),
                revision=str(row.get("revision") or "")))
    if skipped:
        logging.warning("helm listed %d release(s) with no chart field, so nothing here can say"
                        " what they are: %s", len(skipped), ", ".join(sorted(skipped)))
    return tuple(releases), _answered(outcome, len(rows), len(releases))


def _ingress_classes(cluster):
    """Probe 2: the classes bound to Traefik's controller string, through the client.

    A cluster older than `IngressClass` answers the call with a 404, and that is a finding and not a
    failure: it means the cluster has no class to name, and the other two reads still answer. The
    sentence has to survive the log level, because "HTTP 404" is not an answer anyone can act on.

    This is the conversion to the client, and the reason the guard that used to count this module's two
    `kubectl` reads has nothing left to say: the read is the same `kubectl get ingressclass -o
    json` the client sends, and the cluster is no longer a flag on a command line but the object
    the client is bound to.
    """
    items, outcome = _api_read(PROBE_INGRESS_CLASS, READ_INGRESS_CLASS,
                               cluster.list_ingress_classes)
    if not outcome.ok:
        logging.warning("the cluster refused the read of its IngressClasses: %s. A cluster old"
                        " enough to predate IngressClass answers this way, and the other two reads"
                        " still stand.", outcome.detail)
        return (), _failed(outcome, "no IngressClass to read; the cluster is either old or not"
                                    " answering")
    classes = tuple(IngressClass(
        name=str(_field(_metadata(item), "name") or ""),
        controller=str(_field(_spec(item), "controller") or ""),
        service_namespace=_service(item)[0],
        service_name=_service(item)[1])
        for item in items
        if str(_field(_spec(item), "controller") or "") == TRAEFIK_CONTROLLER_NAME)
    return classes, _answered(outcome, len(items), len(classes))


def _pods(cluster):
    """Probe 3: the pods running a traefik image, through the client.

    The read that catches what the other two cannot: a controller that was applied as plain YAML
    creates no helm release, and a cluster whose chart was configured not to write an IngressClass
    creates no class either. Both still run pods, so both are found here.

    `list_pod_for_all_namespaces` and not a namespaced list, because the namespace is the whole
    ours/foreign question and a read narrowed to one address cannot ask it. The client call says
    the same thing from the other side: the namespace is on the object rather than on the command
    line, which is why this probe can say *where* it found the controller.
    """
    items, outcome = _api_read(PROBE_PODS, READ_PODS, cluster.list_pods_for_all_namespaces)
    if not outcome.ok:
        logging.warning("the cluster refused the read of its pods: %s", outcome.detail)
        return (), _failed(outcome)
    pods = []
    for item in items:
        spec = _spec(item)
        images = tuple(str(_field(container, "image") or "")
                       for container in (_field(spec, "containers") or [])
                       if _TRAEFIK_IMAGE.search(str(_field(container, "image") or "")))
        if not images:
            continue
        metadata = _metadata(item)
        pods.append(TraefikPod(namespace=str(_field(metadata, "namespace") or ""),
                               name=str(_field(metadata, "name") or ""),
                               images=images))
    return tuple(pods), _answered(outcome, len(items), len(pods))


# ---------------------------------------------------------------------------------------
# the union
# ---------------------------------------------------------------------------------------


def _findings(configured_namespace, releases, classes, pods) -> tuple:
    """Merge what three independent reads saw into one finding per address.

    Grouped by namespace rather than by probe because a reader has to be able to tell one
    controller from another, and "which probe" is answered inside each finding. An IngressClass
    with no Service to localize it is its own cluster-scoped finding unless kubementat's own
    release is deployed, in which case it is that release's class.
    """
    # `releases` arrive filtered: `_helm_releases` is the one place the chart is matched, so
    # everything here can trust that a HelmRelease is Traefik's without asking twice.
    owns_a_release = any(release.namespace == configured_namespace for release in releases)

    addresses = []
    for found in (*releases, *pods):
        if found.namespace not in addresses:
            addresses.append(found.namespace)
    for ingress_class in classes:
        if ingress_class.service_namespace and ingress_class.service_namespace not in addresses:
            addresses.append(ingress_class.service_namespace)

    findings = [
        TraefikFinding(
            namespace=address,
            releases=tuple(r for r in releases if r.namespace == address),
            classes=tuple(c for c in classes if c.service_namespace == address),
            pods=tuple(p for p in pods if p.namespace == address),
            owned=address == configured_namespace and owns_a_release)
        for address in sorted(addresses)
    ]

    unplaced = tuple(c for c in classes if not c.service_namespace)
    if unplaced:
        if owns_a_release:
            findings = [replace(finding, classes=finding.classes + unplaced)
                        if finding.namespace == configured_namespace else finding
                        for finding in findings]
        else:
            findings.append(TraefikFinding(namespace=None, classes=unplaced, owned=False))
    return tuple(findings)


def _log(presence) -> None:
    """Say what was read, in the log as well as in the report.

    The report is for whoever runs the command; the log is for whoever reads it afterwards. Both
    have to carry the same three facts, and the one that is easy to lose is the read that failed.
    """
    for outcome in presence.probes:
        if not outcome.ok:
            logging.warning("discovery read failed: %s -> %s", outcome.probe,
                            outcome.detail or "the cluster answered nothing")
    if not presence.answered:
        logging.warning("no cluster read answered for traefik discovery, so 'none' here means"
                        " nothing could be read rather than there is nothing there.")
    for finding in presence.findings:
        logging.info("traefik found at %s (%s); found by %s",
                     finding.namespace if finding.namespace is not None else "cluster scope",
                     "kubementat's own" if finding.owned else "not kubementat's",
                     ", ".join(finding.probes))
    logging.info("traefik discovery: classification %s, %d finding(s) across %d namespace(s)",
                 presence.classification, len(presence.findings),
                 len({finding.namespace for finding in presence.findings}))


# ---------------------------------------------------------------------------------------
# the small rules the reads are built on
# ---------------------------------------------------------------------------------------


def _run(probe, argv, reads=shell.run) -> ProbeOutcome:
    """One read taken through the *command* channel. `capture=True, check=False`: a cluster that
    will not answer is data.

    No retries, and no second opinion: these are reads of the present tense, and a retry on a
    cluster that answered "forbidden" reads the same answer twice. `reads` is the caller's answer
    (see `discover`); the default is the one the product uses.
    """
    result = reads(list(argv), capture=True, check=False)
    return ProbeOutcome(probe=probe, read=tuple(argv), ok=result.ok,
                        returncode=result.returncode, detail=_first_line(result.stderr),
                        channel=COMMAND_CHANNEL, stdout=result.stdout or "")


def _api_read(probe, read, call) -> tuple:
    """One read taken through the *client* channel: `(the objects, what the read did)`.

    Same posture as `_run`, different failure. A command that cannot reach the cluster exits and
    prints; a client that cannot reaches an `ApiException`, and the rule that makes the two read
    alike is that neither is raised here: a cluster that refuses a read is a finding the report
    carries, not a traceback. `returncode` carries the status the API answered with so the report
    can say which of the two it was -- and `channel` is what makes the sentence come out as `HTTP`
    and not as `exit`.
    """
    try:
        items = list(call() or [])
    except client.exceptions.ApiException as error:
        return [], ProbeOutcome(probe=probe, read=tuple(read), ok=False,
                                returncode=getattr(error, "status", 0) or 0,
                                detail=_refusal(error), channel=API_CHANNEL)
    return items, ProbeOutcome(probe=probe, read=tuple(read), ok=True, returncode=0,
                               channel=API_CHANNEL, objects=len(items))


def _refusal(error) -> str:
    """One line about why the API said no, in the API's own words.

    `ApiException.reason` is the HTTP phrase and `body` is the JSON the server actually answered
    with; the body is what names the kind that is missing, so it is what a reader needs, and the
    status goes in front of it. A refusal quoted only from the phrase reads as `Forbidden` and
    tells an operator nothing about their own cluster.
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


def _first_line(text) -> str:
    """One line of a cluster's complaint, because a report is not a terminal window."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _json_rows(outcome, subject):
    """The array of rows `helm list` prints, or `(None, what to say about it)`.

    A cluster that answers with a warning where JSON was asked for is the same kind of finding as
    one that answers with nothing: the output is not a fact about the cluster. What it is, is said
    here rather than guessed at by the caller.

    Only the release read is parsed here. The other two are API calls, so there is no text to
    parse, and a Kubernetes list object arrives as `items` on the response rather than as stdout.
    """
    parsed, problem = _loads(outcome, subject)
    if isinstance(parsed, list):
        return parsed, ""
    return None, problem or _wrong_shape(subject, "an array of releases", outcome)


def _wrong_shape(subject, expected, outcome) -> str:
    """Say what a read printed when it parsed, but not as the shape the read asked for.

    A parse that succeeds is not an answer: `"not-json"` is valid JSON, and it is not a pod list.
    The message quotes the first line that came back, because that is the thing an operator can go
    and look at. Without it the report says only "could not parse", which is a sentence about the
    module rather than about the cluster.
    """
    problem = (f"{subject} answered something that is not the JSON this read expects"
               f" (expected {expected}): {_first_line(outcome.stdout)}")
    logging.warning("%s", problem)
    return problem


def _loads(outcome, subject):
    """Parse what a read printed, and keep the reason it could not be parsed."""
    try:
        return json.loads(outcome.stdout or ""), ""
    except ValueError as error:
        problem = (f"{subject} answered something that is not the JSON this read expects"
                   f" ({error}): {_first_line(outcome.stdout)}")
        logging.warning("%s", problem)
        return None, problem


def _answered(outcome, objects, matches) -> ProbeOutcome:
    return ProbeOutcome(probe=outcome.probe, read=outcome.read, ok=True,
                        returncode=outcome.returncode, objects=objects, matches=matches,
                        detail=outcome.detail, channel=outcome.channel, stdout=outcome.stdout)


def _failed(outcome, detail="") -> ProbeOutcome:
    return ProbeOutcome(probe=outcome.probe, read=outcome.read, ok=False,
                        returncode=outcome.returncode, objects=0, matches=0,
                        detail=detail or outcome.detail, channel=outcome.channel,
                        stdout=outcome.stdout)


def _metadata(item):
    """The `metadata` of a cluster object, whichever shape it arrived in.

    Both shapes are real: an API model is an object with attributes, and a fixture is a dict that
    also answers attribute access. `metadata` is the same field either way, so reading it is one
    line here and not a branch the caller has to remember.
    """
    if isinstance(item, dict):
        return item.get("metadata")
    return getattr(item, "metadata", None)


def _spec(item):
    """The `spec` of a cluster object. See `_metadata` for the two shapes."""
    if isinstance(item, dict):
        return item.get("spec")
    return getattr(item, "spec", None)


def _field(container, key):
    """One field of a cluster object, or `None` when there is nothing to read.

    Nothing here needs an object to be well-formed: a `status` with no `spec`, a `metadata` that is
    `null`, or a row that is not an object at all is read as no information and not as a crash --
    and `null` is a value and not a name, which is why every reader keeps `or ""` on what it
    extracts rather than trusting that Kubernetes wrote a key for it.
    """
    if container is None:
        return None
    if isinstance(container, dict):
        return container.get(key)
    return getattr(container, key, None)


def _service(item) -> tuple:
    """The Service a class names, as `(namespace, name)` — either half may be missing.

    This is how a cluster-scoped object says whose controller it is: the chart points the class's
    Service at the release's own namespace. A manifest-installed class may name no Service at all,
    and then the class belongs to no address this module can see. A `null` is read as nothing, not
    as the string "None".
    """
    service = _field(_spec(item), "service")
    if service is None:
        return (None, None)
    return (_field(service, "namespace") or None, _field(service, "name") or None)


def _chart_is_traefik(chart: str) -> bool:
    """True for `traefik` or `traefik-<version>`, and for nothing else that contains the word.

    `traefik-crds` answers False on purpose: it ships no proxy, so counting it as a controller
    would report a Traefik that cannot route anything.
    """
    return bool(_CHART.match(chart))
