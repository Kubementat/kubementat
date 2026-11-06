import json
import logging
import os
import re

import click
from kubernetes import client

from kubementat import config_reads_index, shell
from kubementat.core import constants
from kubementat.cluster import ClusterTarget
from kubementat.config import component_config_key
from kubementat.kubernetes_utils import KubernetesUtils, helm_target_args
from pathlib import Path

# `helm list`'s read for "what releases live here", with the statuses named instead of left to a
# default. The flag that used to carry that intention -- `--all` -- no longer exists: helm 4
# deleted it, and on this product's helm (`helm v4.3.1` here) `helm list -A --all` exits 1 with
# `Error: unknown flag: --all`, which is a read that answers nothing on every cluster.
#
# Dropping it is not enough, because what `--all` was for is true per major and not globally:
#
# * helm 4 defaults to every status. Its `pkg/action/list.go` (v4.3.1) builds the list with
#   `StateMask: ListAll`, and `SetStateMask` falls back to `ListAll` when no filter was given, so
#   on the version this product ships the flag was changing nothing at all.
# * helm 3 starts from `ListDeployed | ListFailed` and narrows from there, so a flag-free
#   `helm list` there answers "deployed or failed" and says nothing about `pending`, `superseded`,
#   `uninstalled` or `uninstalling` releases -- and a component sitting mid-install would be
#   reported as having no release while `helm uninstall <name>` would happily have deleted it. That
#   is the silent wrong answer `--all` was here to prevent.
#
# Naming the six statuses is what makes the question have one answer on both majors: in helm 3 the
# six OR together into precisely the mask `--all` set (`ListAll` in `pkg/action/list.go`), and in
# helm 4 they are the mask the default already is, so the read is neither narrower nor wider than
# what each major would give without being told. `--hidden` is deliberately not here: helm 4.3.1
# rejects it (`unknown flag: --hidden`) and helm 3.0.0's and 3.14.0's own `list.go` never define it,
# so a portable-looking argv that included it would fail exactly the way the flag it replaces does.
#
# The read's own guard is
# `tests/test_the_release_listing_asks_for_the_statuses_helm_actually_has.py`, and it is deliberately
# not written against a version detection: parsing `helm version` to answer a flag question is what
# `helm_version_never_guesses_a_sort_it_cannot_do.py` already pushes back on.
HELM_LIST_RELEASES_ARGV = ["helm", "list", "--all-namespaces",
                           "--deployed", "--failed", "--pending", "--superseded",
                           "--uninstalled", "--uninstalling", "--output", "json"]


def helm_list_releases_argv(target) -> list:
    r'''
    `HELM_LIST_RELEASES_ARGV`, pointed at *target*.

    The statuses above answer "which releases live here"; they say nothing about *which here*,
    and a bare `helm list` answers that question out of whatever `~/.kube/config` the running
    user happens to have. The rule is that helm and helmfile take the same target `kubectl` was
    given, spelled helm's own way (`--kube-context`, and `--kubeconfig` when the target
    resolved a file), on the command line and never in the environment. So the argv is
    `HELM_LIST_RELEASES_ARGV` with the cluster spliced between the program and the read -- the
    read stays one spelling for the guard to own, and the address is the first thing an operator
    reads before the answer.

    A target that names neither is refused rather than guessed, by the same rule
    :func:`~kubementat.kubernetes_utils.kubectl_target_args` wrote down for `kubectl`.

    Args:
        target: The `ClusterTarget` the caller resolved. Never read from a default.

    Returns:
        list[str]: The argv, with the cluster flags after `helm list`.
    '''
    return [*HELM_LIST_RELEASES_ARGV[:2], *helm_target_args(target), *HELM_LIST_RELEASES_ARGV[2:]]


class HelmUtils:
    """Utility class for Helm operations."""

    # helm's machine-readable search output puts the version in `version`; `chart_version` is
    # the key name a couple of helm releases used for the same value, so both are read here
    # rather than one of them guessed at. `app_version` is not on the list, and must not be: it
    # is the version of the software the chart packages, not the version of the chart.
    CHART_VERSION_KEYS = ("version", "chart_version")

    # The label helm puts on every object a release makes, so a release's claims are found by asking
    # the cluster instead of by naming them. Written once, here, because a sweep that guessed at it
    # would find nothing and still report a clean uninstall.
    RELEASE_INSTANCE_LABEL = "app.kubernetes.io/instance"

    def __init__(self, ctx):
        """The helm operations for one environment, from the `Context` that read them.

        One argument and not two plus a `Config` this class builds for itself, because
        the six service classes used to answer "which cluster" six times, and `routing.py` had to
        apologise in a docstring for building a second instance beside `HelmUtils`'s. `Context`
        answers once, before any of them exist, and hands the same `Config` to all of them.

        No `require_team`, because nothing here reads a team. Every value this class asks of
        configuration is environment-level: `env_static_encrypted` for grafana's credentials,
        `component_namespace` -- which is `env_static` -- for every release namespace, and
        `Config.helmfile_asset_dir` -- the packaged manifest -- for the releases helmfile evaluates.
        `grep -n team_static src/kubementat/helm_utils.py` returns nothing, and that is the point.
        The guard that used to be here claimed a `Config` without a team interpolates `None` into
        the paths this class derives; `Config._team_scoped_path` moved that refusal to the read,
        so an unguarded read of a team-scoped path is already answered there, and a constructor that
        refused first was refusing work it has no business refusing: `kmt helm apply dev` would not
        apply a single release for an environment whose files it goes on to read in full.
        """
        self.environment = ctx.environment
        self.config = ctx.config
        # The cluster this class addresses, kept for the one thing the configuration cannot say.
        # helm and helmfile need it on their command line, and it is read lazily so a
        # helm-only command (`kmt helm-version`) never pays for a target it does not use.
        self.ctx = ctx
        # A release's claims are deleted through the client, and the same question is
        # already asked that way by `Automation._delete_claims_in`. One object answers it, built
        # from the `Context` this class was handed -- lazily, so a helm-only command that never
        # sweeps claims pays nothing.
        self.kubernetes_utils = KubernetesUtils(ctx)

    @property
    def target(self) -> ClusterTarget:
        """The cluster every helm and helmfile call here is addressed to.

        Lazy for the same reason `KubernetesUtils.target` is: a `Context` built to write
        configuration (`for_configuration`) has no target at all, and a helm command that only
        reads a chart index must not fail because it assumed one. The three cluster calls below
        ask for it and get `ClusterNotConfiguredError` when there is nothing to address.
        """
        return self.kubernetes_utils.target

    def helmfile_apply(self, environment_name: str, helmfile_label_filter: str, interactive: bool = False, helmfile_working_directory=None, helmfile_path=None):
        """
        Apply a Helmfile configuration

        `interactive` is the only dial: True asks helmfile for confirmation, False lets it
        apply. It defaults to False, so a caller that says nothing never gets a prompt it
        cannot answer. Whether a human is present is not something this class can see, so the
        CLI (`kmt helm apply`) decides that and passes the answer in.
        """
        # Get default variables from the environment if not set
        # Access encrypted static JSON from env_static_encrypted
        # Grafana's admin credentials come from `platform_config/{environment}/static.encrypted.json`
        # and from nowhere else. The template used to document an `export` as the way to set them,
        # which invited a second source for a value that already has one; what that export bought a
        # lane was a grafana that answered to a name no configuration had chosen. So the config is
        # read here and handed to the template, and what happened to be in the operator's shell is
        # not consulted.
        grafana_admin_user, grafana_admin_password = self._grafana_admin_credentials()
        os.environ['GRAFANA_ADMIN_USER'] = grafana_admin_user
        os.environ['GRAFANA_ADMIN_PASSWORD'] = grafana_admin_password

        # ...and for every namespace a release can be deployed into, for the same reason and by
        # the same route: the template reads them, so they have to be in the environment the
        # template is evaluated in.
        namespaces = self._export_component_namespaces()

        # ...and the one routing answer that is neither a namespace nor a release knob: the ingress
        # class the traefik release names. Same destination and same reason -- helmfile reads its
        # templates out of this process's environment -- and this one was missing for a while, so the
        # manifest answered for itself and the cluster's release could disagree with every Ingress the
        # Python side wrote.
        ingress_class = export_ingress_class(self.config)

        # ...and the three that let a manifest shipped inside a wheel say where the operator's own
        # values live. Exporting is the only new mechanism on this surface: the manifest used to sit
        # next to the configuration, so a relative path was enough to reach it, and now it does not,
        # so a path that has to cross out of the package is told where to cross to. The manifest
        # names the root, the environment and the team, and nothing here guesses at a fourth
        # variable the operator's shell might happen to be carrying.
        exported_coordinates = export_configuration_coordinates(self.config, self.environment)

        # The helmfile working directory and the manifest inside it. One resolution of each, in
        # `helmfile_paths`, because the same two paths are what `component_release_index` reads the
        # labels out of: a check that looked at a different file than the one helmfile applies is a
        # check that says nothing.
        packaged_working_directory, packaged_manifest = self.helmfile_paths()
        if helmfile_working_directory is None and helmfile_path is None:
            helmfile_working_directory, helmfile_path = packaged_working_directory, packaged_manifest
        if helmfile_working_directory is None:
            helmfile_working_directory = packaged_working_directory
        if helmfile_path is None:
            helmfile_path = packaged_manifest
            
        helmfile_path = Path(helmfile_path).absolute()
        helmfile_working_directory = Path(helmfile_working_directory).absolute()

        # ...and the two knobs every release that manifest names carries. It is exported after the
        # paths are resolved and not straight after the namespaces above for one reason: the set is
        # read out of *this* manifest's labels, so a check -- and an export -- that looked at a
        # different file than the one helmfile is about to apply would say nothing. The order it
        # needs is the same one the namespaces need: the template is evaluated by the child, so
        # everything has to be in the environment before argv is assembled.
        release_knobs = export_component_release_knobs(
            self.config, self.component_release_index(helmfile_path))

        logging.info(f"Environment name: {self.environment}")
        logging.info(f"Grafana admin user: {grafana_admin_user}")
        logging.info(f"Release namespaces: {namespaces}")
        logging.info(f"Release knobs exported to helmfile: {release_knobs}")
        logging.info(f"Ingress class exported to helmfile: {ingress_class}")
        logging.info(f"Helmfile working directory: {helmfile_working_directory}")
        logging.info(f"Helmfile path: {helmfile_path}")
        logging.info(f"Helmfile label filter: {helmfile_label_filter}")
        logging.info(f"Configuration coordinates exported to helmfile: {exported_coordinates}")

        # A release that is already running somewhere is not free to be told to live somewhere
        # else, and this is the last line every one of those applies is about to cross. Ask the
        # cluster first and refuse the rename in words here, before a single write.
        self.refuse_renaming_a_deployed_release(helmfile_path=helmfile_path,
                                                helmfile_label_filter=helmfile_label_filter)

        # Assemble the helmfile apply command. One argv entry per token: the label filter
        # routinely carries commas and can carry spaces, and a shell would split it there. The
        # cluster goes in right after the verb, because the filter is what decides which releases
        # the apply touches and the operator reading this line has to be able to see which
        # cluster's releases those are.
        argv = ["helmfile", "apply", *helm_target_args(self.target), "--color",
                "-f", str(helmfile_path), "-l", helmfile_label_filter]
        if interactive:
            argv.append("--interactive")

        # Execute the command. Output is not captured: helmfile prints the diff the operator
        # is here to read, and the [y/n] prompt has to reach a terminal when there is one.
        return shell.run(argv, cwd=str(helmfile_working_directory))

    def uninstall_release(self, component: str) -> dict:
        '''
        Take one kubementat component's helm release off the cluster, claims and all.

        The gap this closes: `uninstall_helm_deployment.sh` had no caller in `src/` at all, so
        kubementat could install a component and could tear the whole platform down, but an
        operator who wanted to remove one component had no command to run.
        `uninstall_kubementat` sweeps releases by namespace; this addresses one component, and
        it addresses it through the same two facts rather than a third: the namespace from the
        configuration, the release name from the cluster.

        What it does, in order:

        1. The namespace, from `Config.component_namespace` -- the same accessor the install
           deploys into, the tunnels reach through, and the uninstall sweep deletes. A literal
           here would be a second answer, and the second answer is what makes a rename only
           half happen: the script read the key, but the key it read was spelled from a
           component name it had memorised.
        2. The release name, read from the cluster (`helm list`) instead of constructed. A
           release that predates this repository's naming does not match a name built from the
           chart's default, and `helm uninstall wrong-name` says nothing about a release that
           is sitting right there. Listing is also what lets one component whose release is
           split across names (or whose name someone typed at install time) come off in one
           command. Every status is asked for by name (`HELM_LIST_RELEASES_ARGV`) rather than left
           to a default, because a release that is `failed`, `pending`, `superseded` or being
           uninstalled is still a release, and because what a flag-free `helm list` answers depends
           on the major: helm 4 (the version this product ships, `v4.3.1`) answers every status by
           default, helm 3 answers deployed or failed only. The `--all` that used to be here is gone
           -- helm 4 deleted the flag, so the read died with `unknown flag: --all` before it could
           name anything -- and the statuses spelled in its place are exactly what `--all` selected
           on helm 3 and exactly what helm 4's default already selects.
        3. The claims that release made, deleted by its `app.kubernetes.io/instance` label.
           The script printed a paragraph of advice about them (`kubectl -n <ns> delete pvc
           <PVC_NAME>`) and deleted nothing, which is the part that recorded
           advice-only as a failure. Claims do not have to survive their release, but a
           retained PV bound to a claim that lingers is how the next install of the same
           component mounts last run's data, so they go by name now.

        Absence is a normal answer, not a warning. A component that was never installed, a
        release that is not there, and a component with no claims all log what they found and
        return; only a command that failed for a real reason raises. A claim deletion is
        attempted only after its release went: a release that could not be uninstalled still
        owns its data, and pulling the claims out from under it is the one mistake here that no
        later command can undo.

        Args:
            component (str): Component name, lower-case with hyphens, as
                `kubementat.core.constants.DEFAULT_NAMESPACES` is keyed.

        Returns:
            dict: ``{"component", "namespace", "releases", "claims"}`` -- what was addressed,
            where, and what was deleted. ``releases`` and ``claims`` are empty when there was
            nothing to delete, which is the same shape as a run that deleted something.

        Raises:
            KeyError: If `component` is not a kubementat component. A typo has to say so rather
                than uninstall from a namespace named after the typo.
            click.ClickException: If `helm uninstall` or `kubectl delete pvc` failed on a release
                that is present. The message names the command and what it said.
        '''
        namespace = self.config.component_namespace(component)
        releases = self._installed_release_names(namespace)
        outcome = {"component": component, "namespace": namespace,
                   "releases": list(releases), "claims": []}

        if not releases:
            logging.info(f"No helm release found for component '{component}' in namespace"
                         f" '{namespace}' - nothing to uninstall.")
            return outcome

        failures = []
        for release in releases:
            logging.info(f"Uninstalling helm release '{release}' from namespace"
                         f" '{namespace}' ...")
            result = shell.run(["helm", "uninstall", *helm_target_args(self.target), release,
                                "--namespace", namespace],
                               capture=True, check=False)
            if not result.ok:
                # Not fatal on its own: the other releases of this component are still worth
                # taking off, and the raise below says all of them rather than the first.
                failures.append(f"helm uninstall of '{release}' from '{namespace}' exited"
                                f" {result.returncode}: {(result.stderr or result.stdout or '').strip()}")
                continue
            # Only once the release is gone. Claims that belong to a release still on the cluster
            # are that release's data, and deleting them would be deleting a running component's
            # backing store while the operator is being told the component is still there.
            outcome["claims"] += self._delete_release_claims(namespace, release, failures)

        if failures:
            raise click.ClickException(
                "uninstalling component '%s' from namespace '%s' did not work:\n  %s"
                % (component, namespace, "\n  ".join(failures)))
        return outcome

    def helmfile_paths(self) -> tuple:
        """The two paths of the packaged manifest, for this environment.

        The answer is `config_reads_index.helmfile_paths_for`, which is where it lives: this class
        addresses a cluster, and which manifest the product ships is not a fact about a cluster.
        The delegation is not a compatibility shim -- the method still does its own work, handing
        those two paths to `helmfile_apply` below and to `component_release_index` -- and a caller
        that only wants the paths no longer needs this class to exist.
        """
        return config_reads_index.helmfile_paths_for(self.config)

    def helmfile_state_path(self) -> Path:
        """The packaged manifest itself, as a path.

        `config_reads_index.helmfile_state_path_for`, for the same reason as above and through the
        same single answer.
        """
        return config_reads_index.helmfile_state_path_for(self.config)

    def component_release_index(self, helmfile_path: Path = None) -> dict:
        """The release index of the manifest, read by `config_reads_index.read_release_index`.

        The parsing is there and the call is here, and the split is the one that module states: the
        index comes out of a file and needs nothing from a cluster, so `config_answers` reads it
        from the other side and both layers get one parser. What stays in this class is the cluster
        half -- `_installed_release_names`, `_cluster_release_locations`, and
        `refuse_renaming_a_deployed_release`, which asks `helm list` where each release this index
        names is deployed.

        The failure is unchanged too: a manifest that is not there raises `FileNotFoundError`, which
        is what `refuse_renaming_a_deployed_release` catches and what the CLI turns into a sentence.
        """
        return config_reads_index.read_release_index(self.config, helmfile_path)

    def _installed_release_names(self, namespace: str) -> list:
        '''
        The releases the cluster says live in *namespace*, sorted.

        Read from the cluster rather than constructed, so a release somebody named differently at
        install time is still found. A namespace that does not exist is simply not in the listing,
        which is why the absence of a component costs nothing here.

        The statuses come from `HELM_LIST_RELEASES_ARGV` and not from a default, so this answers
        "every release in the namespace" on both helm majors instead of on whichever one was
        installed when the sentence was written.

        Raises:
            kubementat.errors.CommandExecutionError: When `helm` cannot answer. That is a cluster
                that is not reachable or a kubeconfig that is wrong, and guessing past it would
                report an uninstalled component on a cluster that still has it.
        '''
        listing = shell.run(helm_list_releases_argv(self.target), capture=True)
        rows = json.loads(listing.stdout or "[]")
        return sorted({str(row.get("name")) for row in rows
                       if isinstance(row, dict) and row.get("namespace") == namespace})

    def refuse_renaming_a_deployed_release(self, helmfile_path: Path = None,
                                           helmfile_label_filter: str = None) -> None:
        r'''
        Say no, in words, to an apply that would move a running release to another namespace.

        **The decision this enforces** (owner, 2026-10-30): a deployed release's namespace is
        convention, not configuration. `static.json` says which namespace kubementat *deploys
        into*, and that is the right answer for a release that is not there yet -- but once the
        release exists, the namespace it is in is a fact about the cluster, and editing a key
        does not change it. helm is the one that says so, in the shape of a traceback: every
        cluster-scoped object a release creates is stamped with
        `meta.helm.sh/release-namespace=<the namespace it was installed into>`, so applying the
        same release under a different name than the stamp reads
        `invalid ownership metadata; annotation validation error: key
        "meta.helm.sh/release-namespace" must equal ...: current value is ...`, and the release
        that is serving traffic right now is the one that keeps serving, in the old namespace.

        **Why the refusal is here and nowhere else.** Every cluster write kubementat makes
        through helmfile runs through `helmfile_apply`: `kmt install` (`automation.py:755`),
        `kmt helm apply` (`cli/commands/helm.py:80`), `kmt component install`
        (`cli/commands/component.py:269`) and `kmt routing install`
        (`automations/routing/routing.py:247`). A guard on the install function would be stepped
        around by the other three -- including the one an operator reaches for when they are
        trying this at all, which is `kmt helm apply` -- so a refusal on any of those four would
        be a refusal for some of them, which is the same failure as no refusal wearing a name.

        **What this does not do.** It deletes nothing. No `helm uninstall`, no namespace delete,
        no "clean up the old one first": that is the rejected alternative (take the release off,
        then apply it into the new name) and it is not this product's answer to a rename. It also
        does not install anything when nothing is deployed, because a component that is nowhere on
        the cluster is a normal install and not a rename -- the case most likely to be got wrong,
        so it is asserted in
        `tests/test_a_deployed_release_is_not_moved_by_its_configuration.py`.

        **What it reads.** One `helm list`, cluster-wide (`HELM_LIST_RELEASES_ARGV`), plus the
        environment's own helmfile copy (`component_release_index`). Both are reads: nothing here
        may write, and a test asserts that (`no helm/kubectl write call is made`).

        **What a cluster that cannot answer costs.** Nothing is applied there either, and it says
        so. `helmfile apply` against a cluster `helm` cannot see fails on the same connection,
        but with a traceback instead of a sentence, and guessing past the read would be guessing
        about a write.

        **What is checked when a filter narrows the apply.** `-l component_name=x` names exactly
        the components this command can touch, so only those are asked about; anything else
        (`group=standard`, a selector this file cannot resolve) is treated as reaching everything
        the manifest declares, because a check that silently under-reads is a check that passes.

        **Whose release it is.** A release that is not where the configuration names it is a rename
        only when it is somewhere kubementat addresses. The bundled controller on a k3s node is a
        real `traefik` release in `kube-system`, and kubementat has no configuration that names that
        namespace, so it is a release kubementat did not install and not one it declined to move --
        the command that can tell those apart (`kmt routing install`, whose discovery reads the chart
        and not only the name) has its own answer for it, and a namespace-rename refusal there would
        be the guard answering a question it was never asked.

        Args:
            helmfile_path: The manifest that is about to be applied. Defaults to the environment's
                own copy, which is what `helmfile_apply` reads for every other purpose too.
            helmfile_label_filter: The `-l` selector the apply is being given, used only to decide
                how far the check has to reach.

        Raises:
            click.ClickException: When a release the apply can reach is already deployed in a
                namespace other than the one the configuration names. The message names the
                component, both namespaces, the reason, and the one change that is supported; the
                command writes nothing.
        '''
        conflicts = self._namespace_renames(helmfile_path, helmfile_label_filter)
        if conflicts is None:
            return
        if not conflicts:
            logging.info("No release this apply reaches is deployed outside the namespace its"
                         " configuration names, so nothing is being refused.")
            return
        lines = []
        for conflict in conflicts:
            lines.append(
                f"* {conflict['component']}: release '{conflict['release']}' is already deployed"
                f" in {_namespaces_phrase(conflict['deployed_in'])}, and this configuration now"
                f" names '{conflict['configured_in']}'. Nothing is being applied to it.")
        raise click.ClickException(
            "\n".join(lines) +
            "\n\nA deployed release's namespace is convention, not configuration. helm stamps the"
            f" cluster-scoped objects a release creates with\n"
            f"meta.helm.sh/release-namespace=<that namespace>, so the same objects under the new"
            f" name belong to\nno release and helm refuses them (`invalid ownership metadata`)"
            f" while the release that is running\nnow keeps serving in the namespace it was"
            f" installed into. kubementat does not move a deployed release between namespaces, and"
            f" it will not take one off to make the new name fit.\n\n"
            + _one_supported_fix(conflicts) +
            "\n")

    def _namespace_renames(self, helmfile_path: Path = None,
                           helmfile_label_filter: str = None) -> list:
        r'''
        The releases this apply can reach that are deployed somewhere other than where they are told.

        `{name: namespaces}` from the cluster, `{label: releases}` from the manifest, and the
        namespace the configuration names for that label from `Config.component_namespace` -- three
        reads, none of them guessed, intersected here. A label the manifest carries that is not a
        kubementat component (`constants.DEFAULT_NAMESPACES` is the answer to that question, and it
        is the same answer `kmt component install` gives) has no configuration to disagree with, so
        it is not asked about; the two prometheus exporters are that case, and what they are
        deployed beside is the reason their namespace is not read from a component key.

        Returns:
            list[dict] | None: One entry per colliding component -- `component`, `release`,
                `deployed_in` (the namespaces the cluster named, sorted), `configured_in` -- sorted
                by component, empty when the apply is not a rename of anything, and `None` when the
                manifest could not be read, which is not the same answer and is logged as such.

        Raises:
            click.ClickException: When the cluster cannot be asked where its releases are.
            KeyError: When a configuration key names a namespace for something that is not a
                kubementat component, which `Config.component_namespace` already refuses.
        '''
        try:
            index = self.component_release_index(helmfile_path)
        except OSError as error:
            # The manifest is what both the check and the apply have to have, so a file that is not
            # there is not a rename -- it is an environment that was never initialised, and
            # `helmfile` answers that one itself (with the same path in its error). Reporting a
            # refused rename here would be reporting a decision about releases this command could
            # not have reached even if it had tried, and the traceback would be a worse answer than
            # the one helmfile gives.
            logging.warning(f"The namespace-rename check could not read the manifest this apply is"
                            f" given ({error}), so it is not deciding anything. helmfile will report"
                            f" the same file.")
            return None
        named = _components_named_by_selector(helmfile_label_filter)
        cluster = self._cluster_release_locations()
        # Computed once, for the whole check: the namespaces kubementat addresses, which is what
        # makes a release found elsewhere either a rename or somebody else's.
        claimed = self._kubementat_namespaces()
        conflicts = []
        for component in sorted(index):
            if component not in constants.DEFAULT_NAMESPACES:
                continue
            if named is not None and component not in named:
                continue
            configured = self.config.component_namespace(component)
            for release in index[component]:
                found = sorted(cluster.get(release, ()))
                # The question is "is this release deployed at all, and is that where the
                # configuration says it is", not "did anything disagree". A release that sits in the
                # namespace the key names is where it belongs, whatever else is next to it on the
                # cluster; one that is somewhere else is the rename. And a release with no entry
                # anywhere is not a rename of anything -- that is the case the guard must not touch.
                if found and configured not in found:
                    # But "somewhere else" is only this guard's answer when the somewhere is a
                    # namespace kubementat itself names. A release of the same name sitting in
                    # `kube-system` is k3s's own controller, not kubementat's release renamed, and
                    # the command that can tell the two apart (`kmt routing install`, whose
                    # discovery reads the chart and not just the name) has an answer that is not a
                    # namespace-rename refusal. Stopping a routing install with "the namespace is
                    # convention" would be the guard answering a question it was never asked.
                    if not any(one in claimed for one in found):
                        logging.info(f"Release '{release}' is deployed in {found}, which is not a"
                                     f" namespace kubementat addresses, so '{component}' is being"
                                     f" read as somebody else's release and not as a rename.")
                        continue
                    conflicts.append({
                        "component": component,
                        "release": release,
                        "deployed_in": found,
                        "configured_in": configured,
                    })
        return conflicts

    def _kubementat_namespaces(self) -> set:
        r'''
        The namespaces kubementat addresses: what the configuration names, plus what it falls back to.

        Both halves are needed. `constants.DEFAULT_NAMESPACES` is where a namespace lands when the
        key is absent, so a release installed under the default is in a namespace kubementat named
        even on a cluster whose configuration says nothing about it -- and the configured values are
        what an operator who edited the file actually chose. A release found outside both is not
        sitting in a namespace kubementat moved: it is a release kubementat never made, which is a
        different question and has a different command for it.

        Returns:
            set[str]: The namespaces, with no cluster read involved.
        '''
        return (set(constants.DEFAULT_NAMESPACES.values())
                | {self.config.component_namespace(component)
                   for component in constants.DEFAULT_NAMESPACES})

    def _cluster_release_locations(self) -> dict:
        r'''
        `{release name: the namespaces the cluster says it is in}`, cluster-wide.

        The same read `_installed_release_names` makes (`HELM_LIST_RELEASES_ARGV`, addressed to
        this Context's cluster by `helm_list_releases_argv`, which spells every status so a
        release that is `pending` or mid-`uninstall` is not mistaken for
        absent), left unfiltered by namespace, because the question here is not "is it here" but
        "where is it, if anywhere". Row 29 owns that argv and what it means; this file only asks
        it a second question.

        A release name is only unique per namespace, so a name deployed twice comes back with two
        entries and the refusal says both -- which is also what helm would have to arbitrate.

        Returns:
            dict[str, list[str]]: Names to namespaces, each sorted. Empty when the cluster holds no
                releases at all, which is a clean cluster and not a failure.

        Raises:
            click.ClickException: When `helm` could not answer. Nothing is applied when this
                happens, so the message says the read failed rather than let `helmfile` repeat it
                as a traceback.
        '''
        listing = shell.run(helm_list_releases_argv(self.target), capture=True, check=False)
        if not listing.ok:
            raise click.ClickException(
                "kubementat could not ask the cluster which namespace each of its releases is in,"
                " so nothing is being applied. The check that refuses a namespace rename is a read,"
                f" not a guess, and it could not be answered:\n"
                f"{(listing.stderr or listing.stdout or '').strip()}")
        rows = _parsed_json_list(listing.stdout, "helm list")
        locations: dict = {}
        for row in rows:
            name = row.get("name")
            namespace = row.get("namespace")
            if not isinstance(name, str) or not isinstance(namespace, str):
                continue
            locations.setdefault(name, []).append(namespace)
        return {name: sorted(set(namespaces)) for name, namespaces in locations.items()}

    def _delete_release_claims(self, namespace: str, release: str, failures: list) -> list:
        '''
        Delete the claims the release made, addressed by the label helm puts on them.

        The instance label is how a release owns its objects, so it is also how its claims are
        found without a list of names that only the chart could have written. A chart that used no
        label at all leaves its claims behind, and this says so in the log rather than pretending
        the sweep was complete.

        An unreadable namespace is not a failure: it is the same absence the release listing
        already reported, and a read of a namespace that was never there has nothing to say about
        claims.

        This is the convergence, said out loud because it is the point of the bucket and not
        a detail of it: `Automation._delete_claims_in` already swept claims through the client
        while this method swept them through `kubectl get` and `kubectl delete`. Two halves of the
        same uninstall asked one question two ways and could answer it differently, which is the
        failure this conversion exists to close. Both halves now go through
        `KubernetesUtils.list_persistent_volume_claims` / `delete_persistent_volume_claim`.

        Returns:
            list[str]: The claims deleted.
        '''
        selector = f"{self.RELEASE_INSTANCE_LABEL}={release}"
        try:
            items = self.kubernetes_utils.list_persistent_volume_claims(namespace, selector)
        except client.exceptions.ApiException as error:
            if error.status == 404:
                logging.info(f"No claims to read in namespace '{namespace}' because it is not on"
                             " the cluster.")
                return []
            logging.info(f"No claims to read in namespace '{namespace}'.")
            return []
        claims = sorted(item.metadata.name for item in items)
        if not claims:
            logging.info(f"Release '{release}' in namespace '{namespace}' made no claims to delete.")
            return []

        logging.info(f"Deleting {len(claims)} claim(s) release '{release}' made in namespace"
                     f" '{namespace}': {', '.join(claims)}")
        deleted = []
        for claim in claims:
            if self.kubernetes_utils.delete_persistent_volume_claim(namespace, claim):
                deleted.append(claim)
            else:
                failures.append(f"deleting claim '{claim}' in namespace '{namespace}' was refused"
                                " by the cluster; the other claims and the release go ahead, so"
                                " this is residue and not a reason to stop.")
        return deleted

    def get_latest_chart_version(self, chart: str, repo: str = None) -> str:
        '''
        The newest version helm can see for a chart, as a string.

        `get_latest_version_for_helm_chart.sh` was `helm search repo -l "$1" | head -n 2 |
        tail -n 1 | awk '{ print $2 }'`, which is a shape with two failure modes and neither of
        them said so. If the repo was not added, the pipeline's exit code was still 0 (`head`
        swallows the emptiness) and the answer was an empty line, so a version file was written
        blank. If the search term matched two charts that share a prefix, `head -n 2` picked the
        row helm happened to sort first, not the chart that was asked for. Both are answered
        here, in words, before a version is returned.

        There is no `jq` and no `awk` in this: helm is asked for its own machine-readable output
        (`--output json`) and it is parsed with `json.loads`, the same way every other helm read
        in this class is done. `-l/--latest` is deliberately not used: without it helm already
        reports one row per chart, at its newest version, and `--versions` would make "the
        newest" a sort this file would have to do without semver -- which is precisely the sort a
        `10.0.0` versus `9.9.9` question gets wrong.

        Args:
            chart (str): Chart name, as the repo's index names it (`prometheus`).
            repo (str, optional): Repository the chart is served from (`prometheus-community`).
                When it is absent, *chart* is searched as helm searches it: a term that may
                match more than one chart, whose answer is still read exactly.

        Returns:
            str: The version, e.g. ``25.8.0``.

        Raises:
            click.ClickException: When nothing was searched, when the repo is not added to this
                helm, when no chart matches, or when the search failed. Each names what was
                looked for and what to do about it.
        '''
        chart = (chart or "").strip()
        repo = (repo or "").strip()
        if not chart:
            raise click.ClickException(
                "No chart was named, so there is no version to look up. "
                "The form is `kmt helm-version <ENVIRONMENT> <repo>/<chart>`, e.g. "
                "`kmt helm-version dev prometheus-community/prometheus`.")
        target = f"{repo}/{chart}" if repo else chart

        search = shell.run(["helm", "search", "repo", target, "--output", "json"],
                           capture=True, check=False)
        if not search.ok:
            raise click.ClickException(
                f"helm could not search for '{target}':\n"
                f"{(search.stderr or search.stdout or '').strip()}")

        # A repo that was never added answers `0` and `[]` rather than an error, so the empty
        # list is not the whole answer -- the repo list is what tells the two apart.
        charts = _parsed_json_list(search.stdout, "helm search repo")
        matches = [entry for entry in charts if _is_the_chart(entry, target)]
        if matches:
            version = _chart_version(matches[0])
            if version:
                return version

        if repo and repo.lower() not in self._helm_repo_names():
            raise click.ClickException(
                f"helm repository '{repo}' is not added to this machine, so kubementat cannot "
                f"ask what version '{chart}' is. Add it (`helm repo add {repo} <url>`, then "
                f"`helm repo update`) and run again.")
        raise click.ClickException(
            f"helm found no chart named '{target}'."
            + (" Check the chart name, or update the repository index (`helm repo update`), "
               "which is what makes a chart that exists upstream visible here."
               if repo else " Point it at the repo it lives in: `kmt helm-version <ENVIRONMENT> "
                            "<repo>/<chart>`."))

    def _helm_repo_names(self) -> set:
        '''
        The repositories this helm has, by name, lower-cased.

        Read because "the search returned nothing" has two causes with opposite answers: a
        chart that does not exist, and a repo that is not configured. Naming the second one is
        the whole value of this read, and a repo that cannot be listed (no helm, no config) is
        simply reported as not added.
        '''
        listing = shell.run(["helm", "repo", "list", "--output", "json"], capture=True,
                            check=False)
        if not listing.ok:
            return set()
        return {str(row.get("name", "")).lower()
                for row in _parsed_json_list(listing.stdout, "helm repo list")
                if isinstance(row, dict)}


    def _grafana_admin_credentials(self) -> tuple:
        '''
        The admin user and password helmfile builds grafana from, read from the environment's
        `static.encrypted.json`.

        The user is required, and this is where that requirement is stated. The template renders
        `adminUser` with `requiredEnv`, so an unset key aborts the whole helmfile file -- which is
        helmfile's opinion about a missing credential, not kubementat's, and it arrives after a
        dozen releases have already been touched. The alternative was worse: the value used to be
        read and assigned to `os.environ` unguarded, so a config that did not name an admin reached
        a child process as `None` and raised `TypeError` two frames away from the one thing the
        operator can fix. So say what is missing, name the file, and stop before anything is applied.

        The password is rendered from the environment too -- `helmfile.yaml.gotmpl` asks for both halves
        with `requiredEnv` -- so an absent one aborts the file and nothing is applied. That is a fact
        about the template and not a choice made here, and this method used to describe it wrongly as
        "not rendered from the environment, so it is only warned about" -- the kind of sentence that
        outlives the template it was written against and then reads as an argument for a behaviour that
        no longer holds. So kubementat refuses, naming the file and the verb, which reaches the operator
        before helmfile says something true but unhelpful: `requiredEnv` names a variable and a line
        number, and neither of those points back to the `static.encrypted.json` that would have answered
        it.

        `config init` generates this key when nothing answers it, so the trees that reach this refusal
        are the ones where someone took it away -- which is why the message says where to put it back
        instead of explaining how to install grafana.

        Returns
        -------
        tuple
            ``(admin user, admin password)``, both non-empty strings.

        Raises
        ------
        click.ClickException
            When either half is missing or blank in the environment's encrypted config.
        '''
        encrypted = self.config.get('env_static_encrypted')
        admin_user = (encrypted.get('GRAFANA_ADMIN_USER') or "").strip()
        admin_password = (encrypted.get('GRAFANA_ADMIN_PASSWORD') or "").strip()
        environment_file = f"{self.config.platform_config_dir}/{self.environment}/static.encrypted.json"
        if not admin_user:
            logging.warning("GRAFANA_ADMIN_USER is not set in %s; grafana cannot be installed "
                            "without one, and nothing here will be applied.", environment_file)
            raise click.ClickException(
                f"GRAFANA_ADMIN_USER is not set in {environment_file}. grafana's admin account is "
                f"built from that key, so there is no install to apply. Set it in that file and run "
                f"install again -- not in your shell, where kubementat cannot see it."
            )
        if not admin_password:
            raise click.ClickException(
                f"GRAFANA_ADMIN_PASSWORD is not set in {environment_file}. grafana's admin is built "
                f"from both halves, and helmfile aborts the whole file without one, so nothing would be "
                f"applied. Put it back with kmt config set {self.environment} "
                f"GRAFANA_ADMIN_PASSWORD=<value> --encrypted --config-dir {self.config.platform_config_dir}"
            )
        return admin_user, admin_password

    def _export_component_namespaces(self) -> dict:
        '''
        Hand helmfile the namespace of every component kubementat knows about.

        `Config.component_namespace` is how kubementat *finds* a namespace and
        `constants.DEFAULT_NAMESPACES` is what it falls back to. A release is addressed by
        namespace too, but only the reading side moved: `helmfile.yaml.gotmpl` spelled the
        namespaces out as literals, so renaming `GRAFANA_DEPLOYMENT_NAMESPACE` moved the
        tunnel's lookup, the RBAC, the uninstall sweep and the lane asserts, and left the
        release exactly where it was. A correct tunnel then finds nothing. This is the
        writing half of the same fact, so there is one answer and it lives in
        `platform_config/{environment}/static.json`.

        The set is `constants.DEFAULT_NAMESPACES`, not a list of keys spelled out here: a
        component that is added there is exported without a second edit anywhere, and a
        component nobody named cannot be exported under a guessed name.

        helmfile evaluates its templates with the process environment, which is why this is
        an `os.environ` export and not an argument. The template falls back to the very same
        default when a variable is missing (`env ... | default`), so the two halves of the
        rule are both met: renaming a key moves the release, and a key that is not there does
        not fail the install. No release in `helmfile.yaml.gotmpl` keeps a literal namespace, so
        every release kubementat installs gets a variable of its own; the three git-provider
        releases that used to sit outside this rule were retired on 2026-10-29 and took their
        repositories with them. A component that is not in `DEFAULT_NAMESPACES` gets no key and no
        release, which is a stated gap rather than a silently dead key in
        `static.json.template`.

        Returns
        -------
        dict
            The exported `<KEY>: namespace` pairs, for the log line: what helmfile was told,
            in one place, when a release lands somewhere unexpected.
        '''
        namespaces = {}
        for component in sorted(constants.DEFAULT_NAMESPACES):
            key = component_config_key(component, 'DEPLOYMENT_NAMESPACE')
            namespaces[key] = self.config.component_namespace(component)
            os.environ[key] = namespaces[key]
        return namespaces


def _components_named_by_selector(helmfile_label_filter: str):
    r'''
    The components a helmfile `-l` selector can reach, or `None` for "everything it names".

    `component_name=x` is the only selector that narrows this check to a list of components, and it
    is the shape `kmt component install` passes (`cli/commands/component.py:269`). Everything else
    is answered with `None` on purpose: `group=standard` selects by a label this index does not read,
    a bare `component_name` is a presence test over all of them, and a negated one names everything
    it did not exclude. A check that guessed itself narrower than the command it guards is a check
    that passes for the wrong reason.

    Args:
        helmfile_label_filter: The `-l` argument as it was given, which may be `None`.

    Returns:
        list[str] | None: Component names, sorted and free of duplicates, or `None` when the
        selector cannot be narrowed to names.
    '''
    if not helmfile_label_filter:
        return None
    named = set()
    for clause in re.split(r"[,|]", helmfile_label_filter):
        clause = clause.strip()
        if not clause:
            continue
        equality = re.fullmatch(r"component_name\s*(!?=)\s*(\S+)", clause)
        if equality:
            if equality.group(1) == "!=":
                return None
            named.add(equality.group(2))
            continue
        if "component_name" in clause:
            return None  # presence or negation: it reaches more than one name
    return sorted(named) or None


def export_configuration_coordinates(config, environment: str) -> dict:
    r'''
    Tell the manifest's templating where the configuration is, and return what was told.

    Three variables and no fourth, because that is all a manifest shipped inside a wheel needs in
    order to name the operator's tree: the root, the environment, and -- when one is named -- the
    team. The manifest is the side that reads them (`{{ env "KUBEMENTAT_CONFIG_ROOT" }}` and its
    two neighbours), and the only reason they exist is that a packaged manifest resolves every
    relative path it spells inside the wheel, so a path that has to reach `platform_config/` has to
    be given the distance. Nothing here exports a value the configuration did not already declare:
    a run that names no team gets no `KUBEMENTAT_TEAM`, because a manifest that read one would be
    applying a team nobody chose.

    They go into the process environment and not into an argument, for the reason
    `_export_component_namespaces` gives for the namespaces: helmfile evaluates its templates with
    the environment it is handed, and an argument that only reaches the child's `argv` never reaches
    the template. They are also not the operator's own `$KUBEMENTAT_PLATFORM_CONFIG_DIR`: that
    variable is the *input* to the root's one resolution (`config.resolve_platform_config_dir`) and
    writing the resolved answer back into it would make the process environment a second place that
    claims to know which root this run was given.

    Args:
        config: The `Config` this apply is reading -- supplies the root and, when the command was
            given one, the team.
        environment: The environment this apply addresses, from the `Context` and not from the
            process environment.

    Returns:
        dict: The `{variable: value}` pairs exported, for the log line. `KUBEMENTAT_TEAM` is absent
        when no team was named.
    '''
    exported = {
        "KUBEMENTAT_CONFIG_ROOT": str(Path(str(config.platform_config_dir)).absolute()),
        "KUBEMENTAT_ENVIRONMENT": str(environment),
    }
    team = getattr(config, "team", None)
    if team:
        exported["KUBEMENTAT_TEAM"] = str(team)
    for name, value in exported.items():
        os.environ[name] = value
    return exported


def export_component_release_knobs(config, index) -> dict:
    r'''
    Tell helmfile which releases are installed and at which version, and return what was told.

    Two variables per release and no more, because two is what a helmfile release carries that an
    operator can state from `static.json`: `installed:` and `version:`. The read is
    `Config.component_release_enabled` and `Config.component_release_version`, so the same
    `L2 > L1` walk a namespace gets is what answers — a team that declares
    `ALLOY_ENABLED: false` is not overridden by the environment that declares nothing.

    **The set is the manifest's `component_name` labels, i.e. *index*, and not
    `constants.DEFAULT_NAMESPACES`.** Those two sets do not line up, and both directions of the
    mismatch are wrong. Measured at this HEAD: ten labels, thirteen namespace keys. `alloy`,
    `prometheus-blackbox-exporter` and `prometheus-helm-exporter` have a label and no namespace key
    of their own (all three land in a namespace that belongs to another component), so exporting off
    the namespace set leaves three releases with no knob at all. The other side of it is what
    `_export_component_namespaces` calls the silently dead key: `vpa`, `goldilocks`,
    `cluster-cronjobs` and the three Tekton keys create namespaces and apply no release, so exporting
    off them writes six variables that no template line will ever read. Reading the index instead
    costs nothing, exactly the way `component_release_index` costs nothing: a release added to the
    manifest gets its knobs the way it gets installability, for free and with no second list to
    agree with.

    Only what was declared goes into the environment. A key that answers `None` is not exported,
    because helmfile's own `default` in the manifest is what a tree that declares nothing should
    read, and writing an empty or invented value would take that answer away from the manifest and
    hand it to a function. Like the namespaces and the coordinates, these land in the process
    environment and not in `argv`: helmfile evaluates its templates in the environment it is handed,
    and an argument that reaches only the child's arguments never reaches the template.

    Args:
        config: The `Config` this apply is reading — supplies every layer the knobs are answered from.
        index: `{component_name label: [release, ...]}` for the manifest helmfile is about to apply,
            from `HelmUtils.component_release_index`. Taking it as an argument is what keeps this
            honest: the labels decide the variables, so they had better be the labels of that file.

    Returns:
        dict: The `{variable: value}` pairs exported, for the log line. An operator who declared
        nothing gets `{}` back and an environment nothing was added to.
    '''
    exported = {}
    for component in sorted(index):
        # Both variables, keyed by `component_config_key` rather than assembled from the accessor's
        # name: there is one spelling of "what a component is called" in this tree and this is it.
        answers = ((component_config_key(component, "ENABLED"),
                    config.component_release_enabled(component)),
                   (component_config_key(component, "VERSION"),
                    config.component_release_version(component)))
        for key, answer in answers:
            if answer is None:
                continue
            exported[key] = answer
            os.environ[key] = answer
    return exported


# The component whose class the routing release names is `constants.COMPONENT` — spelled once for the
# whole package since the routing modules started importing it from there. It used to be spelled again
# here, because `routing.py` owned the name and this file could not import it without a cycle: routing
# imports `HelmUtils`. That reason is gone, and a duplicate that is justified by a cycle which no
# longer exists is a bug waiting to be believed.


def export_ingress_class(config) -> dict:
    r'''
    Hand helmfile the ingress class the configuration answers, and return what was handed.

    One variable and one bug that reads as success. The manifest's traefik release configures its
    `ingressClass.name` from `env "TRAEFIK_INGRESS_CLASS"`, and before this function nothing in `src/`
    ever put that variable into the environment helmfile evaluates its templates in. So `env` found
    nothing, the manifest's own `default` answered every single run, and a tree that declared
    `public` got a release configured with the product's default while the Python side -- the generated
    Ingress, the cert-manager solver, the EventListener ingresses -- named `public` in the same
    cluster. Both halves were internally correct and neither was told about the other, which is the
    shape `export_configuration_coordinates` and `export_component_release_knobs` exist to prevent.

    The exported value is the configuration's own answer and nothing else:
    `Config.component_ingress_class` is the read, which walks `L2 > L1 > L0` over the component layers
    the way every other component key does. The destination is `os.environ` for the reason the two
    neighbours give: helmfile evaluates its templates in the environment it is handed, and an answer
    that reaches only the child's argv never reaches the template.

    **The neighbours' skip is deliberately not copied here, and that is the finding.**
    `export_component_release_knobs` writes nothing when a key answers `None`, correctly, because an
    undeclared knob belongs to the manifest's `default`. Copying that here would be the bug: this key's
    L0 *does* answer it (`constants.DEFAULT_STATIC_ANSWERS`, the last layer of the walk), so a tree that
    declared nothing has an answer to be handed over, and an export that skipped absent keys is the
    export that fails for exactly the common tree. There is no fallback in this function either, which
    is the other half of the same decision: an answer that is genuinely missing is refused where it is
    read, and `Config.component_ingress_class` raises `KeyError` naming the table that stopped
    answering. No path in this file can produce a `traefik` nobody chose.

    Args:
        config: The `Config` this apply is reading -- the only source of the answer.

    Returns:
        dict: `{variable: value}` for the one variable, for the log line. For a component this product
        knows it is never empty; the `KeyError` above is the only other shape, and it says which table
        to fix.
    '''
    key = component_config_key(constants.COMPONENT, "INGRESS_CLASS")
    answer = config.component_ingress_class(constants.COMPONENT)
    exported = {key: str(answer)}
    os.environ[key] = str(answer)
    return exported


def _one_supported_fix(conflicts: list) -> str:
    r'''
    The only change the refusal tells the operator about, one line per component.

    The fix is to make the configuration agree with the cluster, because that is the half that is
    still theirs to type: the namespace is a convention once the release exists, so what the key has
    to say is the namespace the release is already in. There is no second line pointing at a way to
    move it, and none may appear: taking the release off first is the alternative the owner decision
    of 2026-10-30 rejected, and this file does not offer it, even as a remark.
    '''
    key_and_value = [(component_config_key(conflict["component"], "DEPLOYMENT_NAMESPACE"),
                      conflict["deployed_in"])
                     for conflict in conflicts]
    if len(key_and_value) == 1:
        key, where = key_and_value[0]
        if len(where) == 1:
            lead = f"The one change that is supported: set {key} back to {where[0]!r}"
        else:
            lead = ("The release named here is deployed in more than one namespace"
                    f" ({', '.join(repr(one) for one in where)}), which is a finding to settle"
                    " before a key is set to anything")
        return (lead + ". The namespace a deployed release is in is not something this file moves,"
                " so the key has to name where the release actually is.")
    return ("The one change that is supported: set each key back to the namespace its release is"
            " already in --\n" + "\n".join(f"* {key} -> {_namespaces_phrase(where)}"
                                          for key, where in key_and_value))


def _namespaces_phrase(namespaces: list) -> str:
    r'''
    One or more namespaces, spelled so that both shapes read as themselves.

    A release name is unique per namespace and not across them, so "where is it" can come back with
    more than one answer. The single case is the ordinary one and has to be actionable -- a fix that
    says "set the key to `x', 'y`" is a sentence nobody can run -- so it is named as one namespace,
    and the plural case is named as a set rather than concatenated.
    '''
    if len(namespaces) == 1:
        return f"namespace '{namespaces[0]}'"
    return "namespaces " + " and ".join(f"'{one}'" for one in namespaces)


def _parsed_json_list(output: str, what: str) -> list:
    '''
    A helm command's JSON output, as a list, with the two shapes that are not one named.

    `helm search repo` prints `[]` when it finds nothing and empty output when it printed
    nothing, and both mean "nothing matched" rather than "the test is broken". Anything that is
    not a list is a helm that answered in a format this file does not speak, and that says so
    instead of failing the same way later inside a `for`.
    '''
    try:
        parsed = json.loads((output or "").strip() or "[]")
    except json.JSONDecodeError as error:
        raise click.ClickException(f"{what} returned output that is not JSON, so its answer "
                                  f"cannot be read: {error}")
    if not isinstance(parsed, list):
        raise click.ClickException(f"{what} returned {type(parsed).__name__} instead of a list "
                                  f"of charts, so nothing here can be trusted about its answer.")
    return [entry for entry in parsed if isinstance(entry, dict)]


def _is_the_chart(entry: dict, target: str) -> bool:
    '''
    Whether a search row *is* the chart that was asked for, and not merely a row that matched.

    helm treats the search term as a case-insensitive regular expression, so
    `prometheus-community/prometheus` also hits `prometheus-community/kube-prometheus-stack`
    and `prometheus-community/prometheus-node-exporter`. The script piped that crowd into
    `head -n 2 | tail -n 1 | awk`, which reported whichever row helm sorted first. Comparing
    the address is the correction, and it is done case-insensitively because repo names are
    lower-case by convention and a chart typed differently is the same chart.
    '''
    wanted = target.lower()
    address = str(entry.get("chart", "")).lower()
    if address:
        return address == wanted
    # An older helm that reports the parts separately rather than the address.
    name = str(entry.get("name", "")).lower()
    repository = str(entry.get("repository", "")).lower()
    if not (name and repository):
        return False
    return f"{repository}/{name}" == wanted


def _chart_version(entry: dict):
    '''
    The version field of a search row, whichever key this helm used.

    `app_version` is deliberately not a fallback: it is the version of the software the chart
    packages, and answering a chart-version question with it is how a wrong number looks
    plausible.
    '''
    for key in HelmUtils.CHART_VERSION_KEYS:
        value = entry.get(key)
        if value:
            return str(value).strip()
    return None
