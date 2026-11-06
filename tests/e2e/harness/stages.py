"""The lanes: what the suite actually does, in the order the requirements name it.

Stage names are the ones from the requirements: `01-provision`, `02-install`,
`03-run`, `04-rerun`, `05-features`, `99-teardown`. Destroying the VM is not a
stage; it happens in a `finally`.

Two rules shape this file: commands come from the capability table and never
appear as literals here, and everything executed in the guest goes through
the single primitive in `harness/guest.py`.
"""

import base64
import json
import posixpath
import re
import secrets
import shlex
import time
from pathlib import Path

from harness import asserts, config
from harness.guest import Guest
from harness import vmm as vmm_lib

PROBE_POD = "kmt-e2e-probe"
PROBE_CLAIM = "kmt-e2e-probe"
PIPELINE_RUN = "hello-world-pipeline-run"
FAILING_RUN = "kmt-e2e-failing-run"

# How long the harness waits for a fact it can observe, and how often it looks. Everything here
# is a deadline plus a poll, never a sleep that ends when it is told to: if one of these needs
# raising, the measurement is what is wrong, not the patience.
PORT_FREE_WAIT_S = 15
STOP_WAIT_S = 15
# Namespace deletion is asynchronous on a real cluster and was measured at 42 s to drain after the
# last delete was accepted, so a single read of `kubectl get ns` is not a verdict - it is one instant.
# This is the ceiling for giving the cluster time to finish what the uninstall asked for.
NS_GONE_WAIT_S = 180
POLL_INTERVAL_S = 1
# How often the tunnel probe re-reads the body inside its deadline. Named rather than a literal in
# the loop so a unit test can run the loop out to its own short deadline without spending the
# lane's patience; the lane still polls every three seconds.
TUNNEL_POLL_S = 3
# How long a detached run is given to say who it is. The wrapper publishes its own pid before it
# runs anything, so this is a start, not a wait for a fact nobody recorded.
LEADER_WAIT_S = 15
# Where detached runs keep their log and their leader's pid. `/var/tmp` because it survives the
# sweeps that `/tmp` can get.
BACKGROUND_DIR = "/var/tmp"
# The budget for a later install group, whichever capability applies it. It is one number rather
# than a per-capability map because the work behind it is the same set of releases either way --
# today `helm.helmfile-apply` applies the routing group inside 900 s, and `kmt routing install`
# applies the same chart behind the same selector, plus the reads that decide whether to.
LATER_GROUP_TIMEOUT_S = 900
# How long a group's workloads get to reach Running and ready. Generous because an OpenBao server
# restores storage before it answers, and stingy because a pod in CrashLoopBackOff is not going to
# recover inside this window no matter how long it is given.
WORKLOAD_TIMEOUT_S = 180

# Where a harness-written file waits to be merged into a generated config: `/var/tmp` because `/tmp`
# can be swept mid-run. The row the lane seeds carries a password, so it lands through stdin here
# rather than in an argv, and the stage that wrote it removes it.
GUEST_STAGING_DIR = "/var/tmp"

# ------------------------------------------------------------- the routing lane
#
# The routing lane is the only one that turns k3s's bundled controller off, and the only one that
# installs a controller of its own, so its knobs live together rather than scattered through three
# stage methods. `config.ROUTING_LANE` holds the names and the image; this block holds the two
# numbers and the two strings everything else decides from.
#
# How long the lane gives k3s to finish uninstalling a controller it was told to disable. The k3s
# docs say a disabled add-on is *actively* uninstalled -- the manifest is deleted and the
# HelmChart goes with it, which runs a `helm uninstall` and takes the ServiceLB daemonset down --
# and that is a few jobs, not an instant. It is a ceiling and not a verdict: what makes it green is
# the read inside `_k3s_traefik_stopped`, and the number only says when to stop asking.
ROUTING_TRAEFIK_STOP_WAIT_S = 300

# How long an entrypoint gets to answer. `helmfile apply` waits for the release's own pods, so
# this is the ServiceLB daemonset and the router table, not the controller's start-up.
ROUTING_ENTRYPOINT_WAIT_S = 360

# What Traefik binds an IngressClass to. `constants.TRAEFIK_CONTROLLER_NAME` is the product's copy
# of the same string -- the chart's `_helpers.tpl` spells it this way -- and
# it is not the uninstall sweep's `k8s.io/ingress-nginx`, which points the other way. The lane
# spells it here because the harness must not import `src/` -- that import would be a second
# product, and a lane that reads the product's own constants cannot catch the product reading them
# wrong. The parity is what `test_the_routing_lane_counts_bindings_and_not_names.py` guards.
ROUTING_TRAEFIK_CONTROLLER = "traefik.io/ingress-controller"

# The two issuers `kmt routing install` applies, both by name, because `create_ingress_yml.sh` and
# both EventListener templates reference `letsencrypt` by string and only the solver moves.
ROUTING_ISSUERS = ("letsencrypt", "letsencrypt-staging")

# The ACME directory each of those two names has to resolve to. It is a table and not one expected
# value because the failure worth catching is the two of them landing on the same URL: an account is
# registered per server, the rate-limiting penalty is per account, and a swap of staging and
# production presents as certificates mysteriously failing long after the change that caused it.
# Checking each name against its own directory catches the swap and the collision in one read. The
# harness spells the URLs rather than importing them, for the same reason
# `ROUTING_TRAEFIK_CONTROLLER` is spelled here: `routing.py`'s `LETSENCRYPT_ISSUERS` is the product's
# copy, and a lane that read the product's constants could not catch the product reading them wrong.
ROUTING_ISSUER_SERVERS = {
    "letsencrypt": "https://acme-v02.api.letsencrypt.org/directory",
    "letsencrypt-staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
}


class StageFailed(Exception):
    """Abort the lane: the stage could not be executed at all."""


class FeatureSkip(Exception):
    """A capability that does not exist yet; recorded as skipped, not as red."""


def _sh(argv):
    return " ".join(shlex.quote(str(token)) for token in argv)


def _probe_manifest(storage_class):
    return "\n".join([
        "apiVersion: v1",
        "kind: PersistentVolumeClaim",
        "metadata:",
        "  name: %s" % PROBE_CLAIM,
        "spec:",
        "  storageClassName: %s" % storage_class,
        "  accessModes: [ReadWriteOnce]",
        "  resources:",
        "    requests:",
        "      storage: 1Gi",
        "---",
        "apiVersion: v1",
        "kind: Pod",
        "metadata:",
        "  name: %s" % PROBE_POD,
        "spec:",
        "  containers:",
        "  - name: consumer",
        "    image: docker.io/library/alpine:3.21",
        "    command: [\"sh\", \"-c\", \"touch /data/probe; sleep 3600\"]",
        "    volumeMounts:",
        "    - name: data",
        "      mountPath: /data",
        "  volumes:",
        "  - name: data",
        "    persistentVolumeClaim:",
        "      claimName: %s" % PROBE_CLAIM,
        "",
    ])


class LaneRunner:
    def __init__(self, lane, opts, artifacts, vmm_client, state, record, log):
        self.lane = lane
        self.opts = opts
        self.artifacts = artifacts
        self.vmm = vmm_client
        self.state = state
        self._emit = record  # recorder(stage, name, cls, passed, detail, attempts, artifacts)
        self.log = log
        self.guest = None
        self.cluster = None
        self.stage = "init"

    def record(self, name, cls, passed, detail="", attempts=1, artifacts=None, log_files=None):
        self._emit(self.stage, name, cls, passed, detail, attempts, artifacts or [])

    # ---------------------------------------------------------------- plumbing

    @property
    def ctx(self):
        # `group` is per-stage (the rerun loop does not name one, stage 02 overrides it per
        # installation group), everything else comes from the lane identity. The configuration root is
        # part of that identity: a row that asks for `{config_dir}` is asking for the tree this lane
        # decided to read, and not for a path some stage happened to know.
        return dict(config.LANE_PLACEHOLDERS, group=self.lane.install_groups[0],
                    config_dir=self.lane.config_dir)

    def kmt_argv(self, capability, **kw):
        """Resolve a capability to argv. The only place commands are ever composed.

        `uv run` prefixes it because the guest copy's CLI lives in its virtualenv.
        """
        return ["uv", "run"] + config.argv_for(capability, **dict(self.ctx, **kw))

    def km(self, capability, timeout=180, **kw):
        argv = self.kmt_argv(capability, **kw)
        stdin = config.CAPABILITIES[capability].stdin or None
        return self.guest.run_argv(argv, timeout=timeout, stdin=stdin)

    def km_background(self, capability, log_name, timeout=30, **kw):
        """Start a long-running capability (a tunnel) detached; returns (pid, log path).

        The pid is the session leader of everything the capability leaves behind, which is
        what `_stop_background` kills. See `start_session` for why it is not `$!`.
        """
        return self.start_session(_sh(self.kmt_argv(capability, **kw)), log_name, timeout=timeout)

    def start_session(self, command_line, log_name, timeout=30):
        """Run `command_line` in a session of its own and return the leader's pid.

        Two facts make a detached run stoppable: everything it leaves behind dies with it, and
        the harness can name what it is killing. Both follow from recording the *session
        leader*, because `kill -TERM -<sid>` is then the whole subtree.

        `$!` is not that pid, twice over:

        * `rm -f <log> && setsid ... &` puts the whole `rm && setsid` list in the async job, so
          `$!` is the subshell bash forked for the compound list. That is the shape the lane
          hit: the recorded pid had led no session of its own, the group kill fell back to a
          pid kill, and the port-forward stayed bound.
        * Even with `&` bound to `setsid` alone, `$!` is the leader only while `setsid` execs
          rather than forks, and `setsid` forks whenever the background job is already a
          process-group leader — which depends on the shell context ssh lands in.

        So the leader is read from the process itself: the wrapper writes its own `$$` from
        inside the new session before it runs anything, and that number is both the sid and the
        pgid of everything under it. `setsid` is what makes it a leader; `nohup` keeps it alive
        when the connection closes; the wrapper also appends the command's exit code, because a
        vanished pid says nothing about whether the command failed or is still starting.
        """
        wrapper = posixpath.join(BACKGROUND_DIR, "kmt-e2e-%s.sh" % log_name)
        log_path = posixpath.join(BACKGROUND_DIR, "kmt-e2e-%s.log" % log_name)
        pid_path = posixpath.join(BACKGROUND_DIR, "kmt-e2e-%s.pid" % log_name)
        # The first line is the whole fix: `printf '%s\n' "$$" > <pid>` records the wrapper's own
        # pid, and at that point the wrapper *is* the session leader.
        self.guest.write_file(
            path=wrapper,
            content="#!/bin/bash\nprintf '%%s\\n' \"$$\" > %s\n%s\nprintf 'KMT_E2E_EXIT=%%s\\n' \"$?\" >> %s\n"
                    % (shlex.quote(pid_path), command_line, shlex.quote(log_path)))
        # `rm` in the foreground and `&` bound to `setsid` alone: a compound list is what put a
        # subshell between the harness and the session.
        self.guest.run(command="rm -f %s %s; setsid nohup bash %s > %s 2>&1 < /dev/null &"
                       % (shlex.quote(log_path), shlex.quote(pid_path), shlex.quote(wrapper),
                          shlex.quote(log_path)),
                       timeout=timeout)
        return self._session_leader_pid(pid_path), log_path

    def _session_leader_pid(self, pid_path, timeout=LEADER_WAIT_S, interval=None):
        """The leader the wrapper published, or '' when the wrapper never got that far.

        Polled, never slept: the start has to happen before it can say who started, and a pid
        nobody wrote is not evidence.
        """
        interval = POLL_INTERVAL_S if interval is None else interval
        deadline = time.time() + timeout
        while True:
            written = self.guest.run(command="cat %s 2>/dev/null" % shlex.quote(pid_path), timeout=20)
            pid = written.stdout.strip().splitlines()[-1] if written.stdout.strip() else ""
            if pid.isdigit():
                return pid
            if time.time() >= deadline:
                return ""
            time.sleep(interval)

    # ------------------------------------------------- what is holding the port

    def port_listeners(self, port):
        return self._listeners(port)[0]

    def _listeners(self, port):
        """`(something is listening, who)` on `port`.

        `-p` is not decoration: the socket is the fact under test, and the process behind it is
        what turns a leaked port-forward in a lane report from a mystery into one line the
        reader can act on.
        """
        result = self.guest.run(command="ss -ltnp 2>/dev/null | grep ':%s ' || true" % port, timeout=30)
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        return bool(lines), "; ".join(lines)

    def _wait_port_free(self, port, timeout=None, interval=None):
        """Poll until nothing listens on `port`. Returns `(free, detail)`.

        Bounded and polled, never a longer sleep: a sleep is not evidence, the socket is. If
        this goes red the detail names the survivor, which is the whole point of measuring the
        port instead of trusting the kill.
        """
        timeout = PORT_FREE_WAIT_S if timeout is None else timeout
        interval = POLL_INTERVAL_S if interval is None else interval
        deadline = time.time() + timeout
        bound, detail = self._listeners(port)
        while bound and time.time() < deadline:
            time.sleep(interval)
            bound, detail = self._listeners(port)
        return (not bound, detail)

    # --------------------------------------------------- stopping detached work

    def _session_id(self, pid):
        """The session `pid` *leads* — its sid — and '' when it leads none.

        `sid == pid` is the whole test, and it is what makes a group kill safe rather than a
        hazard: a pid that shares somebody else's session reports somebody else's sid, and
        `kill -TERM -<sid>` is then somebody else's processes — under ssh that can be the
        harness's own shell. A pid that is already gone answers nothing, which is the other
        way into the same answer.
        """
        if not (pid and str(pid).isdigit()):
            return ""
        result = self.guest.run(command="ps -o sid= -p %s 2>/dev/null | tr -d ' '" % pid, timeout=20)
        sid = result.stdout.strip()
        return sid if sid == str(pid) else ""

    def _session_processes(self, sid):
        result = self.guest.run(command="pgrep -s %s 2>/dev/null || true" % sid, timeout=20)
        return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]

    def _stop_background(self, pid, wait_s=None, interval=None):
        """Stop a detached capability and say how it was stopped.

        The unit is the process group the *leader* owns, which is why `start_session` hands back
        the leader rather than `$!`. The tree is `kmt` -> `kubectl` with no shell
        exec-ing the last command, so a SIGTERM to one pid in the chain kills the CLI and leaves
        `kubectl port-forward` bound as an orphan — which is what `tunnel-port-freed` was
        measuring twice per run. TERM first, KILL for whatever the group is still holding, and
        the port gets proved afterwards.

        Returns a string for the assert detail; the assertion itself is `_wait_port_free`.
        """
        wait_s = STOP_WAIT_S if wait_s is None else wait_s
        interval = POLL_INTERVAL_S if interval is None else interval
        sid = self._session_id(pid)
        if not sid:
            if pid:
                self.guest.run(command="kill %s 2>/dev/null || true" % pid, timeout=30)
            return ("pid %s alone (it leads no session of its own, so the group is not ours to "
                    "kill — that is a start that did not make a leader, not a stopped tunnel)"
                    % (pid or "nothing"))
        self.guest.run(command="kill -TERM -%s 2>/dev/null || true" % sid, timeout=30)
        waited = 0
        while waited < wait_s and self._session_processes(sid):
            time.sleep(interval)
            waited += interval
        if self._session_processes(sid):
            self.guest.run(command="kill -KILL -%s 2>/dev/null || true" % sid, timeout=30)
        return "process group %s" % sid

    def expect(self, name, passed, detail="", cls="product"):
        self.record("assert:%s" % name, cls, bool(passed), detail)

    def wait(self, name, description, predicate, timeout, cls="product", interval=5):
        passed, detail, attempts = self.cluster.wait(description, predicate, timeout=timeout, interval=interval, cls=cls)
        self.record("assert:%s" % name, cls, passed, detail, attempts=attempts)
        return passed

    def record_result(self, name, cls, result, detail_prefix="", **_ignored):
        passed = result.ok
        detail = detail_prefix + ("" if passed else "exit=%s | %s" % (result.exit_code, result.excerpt(500)))
        if not passed:
            # Blocker attribution reads whole output, not the 500-char summary that
            # makes the report readable.
            self.state.setdefault("blocker_evidence", []).append("%s\n%s" % (name, result.output[-4000:]))
        self.record(name, cls, passed, detail.strip(),
                    artifacts=[self.artifacts.log(self.stage, "%s.stdout" % name, result.stdout),
                               self.artifacts.log(self.stage, "%s.stderr" % name, result.stderr)])
        return passed

    def run_capability(self, capability, cls="product", detail="", timeout=180, **kw):
        result = self.km(capability, timeout=timeout, **kw)
        return self.record_result("cap:%s" % capability, cls, result, detail_prefix=detail)

    def log_guest(self, name, text):
        return self.artifacts.log(self.stage, name, text)

    def dump_failure_context(self):
        """On failure, the diagnosis lands in the artifact dir, never on stdout (NFR 6.5)."""
        if not self.guest:
            return
        for index, command in enumerate(config.FAILURE_DUMP):
            result = self.guest.run(command=command, timeout=180)
            self.artifacts.log("diagnostics", "dump-%02d-%s" % (index, re.sub(r"\W+", "-", command)[:30]), result.output)

    # ------------------------------------------------------------ 01-provision

    def stage_provision(self):
        """VM create, single-node k3s, prerequisites, then the storage probe."""
        name = self.opts.vm_name
        self.log("creating %s (%s %s, %sGiB ram, %s vcpu, %sGiB disk)"
                 % (name, config.DISTRO, config.RELEASE, self.opts.ram, self.opts.vcpu, self.opts.disk))
        envelope, _create_stderr = self.vmm.create(
            name,
            ram_gib=self.opts.ram,
            vcpu=self.opts.vcpu,
            disk_gib=self.opts.disk,
            distro=config.DISTRO,
            release=config.RELEASE,
            mount="%s:%s" % (self.opts.repo_root, config.GUEST_MOUNT),
            script=self.opts.provision_script,
            # The create must finish inside this stage's budget, with room left for
            # the provisioning work that follows. A cold image cache is the one
            # thing that can dominate it (measured warm create: 16-30 s).
            timeout=max(120, self.lane.timeouts["01-provision"] - 30),
        )
        self.artifacts.envelope("create.json", envelope)
        scripts = envelope.get("scripts") or []
        script_exit = scripts[0].get("exit_code") if scripts else 0
        self.expect("provision-script-exit", script_exit == 0, "provision script exit_code=%s" % script_exit)
        if script_exit != 0:
            raise StageFailed("the provisioning script failed (exit %s)" % script_exit)

        ip = vmm_lib.lease_from(envelope)
        if not ip:
            raise StageFailed("virt-runner reported no IP lease")
        image = envelope.get("image") or {}
        self.state["image"] = {
            "path": image.get("path"),
            "cache_hit": image.get("cache_hit"),
            "verified": image.get("verified"),
            "verification": image.get("verification"),
            "distro": config.DISTRO,
            "release": config.RELEASE,
        }
        self.state["vm"] = {"name": name, "ip": ip}
        self.guest = Guest(
            ip=ip,
            user=config.GUEST_USER,
            identity_file=vmm_lib.ssh_identity_from(envelope),
            known_hosts_path=self.opts.known_hosts,
            workdir=config.GUEST_TREE,
            env=self.opts.env,
            log=self.log,
        )
        self.cluster = asserts.Cluster(self.guest, log=self.log)

        # k3s reaches Ready in seconds on a healthy host, so a slow node is a harness bug.
        passed = self.wait("node-ready", "exactly one Ready node", lambda: self._nodes_ready(),
                           timeout=180, cls="infra")
        if not passed:
            raise StageFailed("the node never became Ready")

        # The kubeconfig mode must survive a restart, not just chmod. No
        # restart happens here: the provisioning script already restarted k3s once
        # for the write-kubeconfig-mode change, and restarting again this early
        # aborts k3s's own addon deploy mid-flight (measured: node Ready, zero pods,
        # zero StorageClasses, repeating on every restart).
        stat = self.guest.run(command="stat -c '%a' /etc/rancher/k3s/k3s.yaml", timeout=30)
        self.expect("kubeconfig-mode-644-after-restart", "644" in stat.stdout,
                    "k3s.yaml mode is %s, expected 644 after a service restart" % stat.stdout.strip())
        self._wait_for_addons()

        # `kmt --help` as a plain user with no KUBECONFIG is what the fix is *for*.
        help_result = self.guest.run_argv(["uv", "run", "kmt", "--help"], cwd=config.GUEST_TREE, timeout=180)
        self.record_result("kmt-help-unprivileged", "infra", help_result)
        # ...and the Python client is what actually breaks: it reads $KUBECONFIG or
        # ~/.kube/config, so a mode-644 /etc/rancher/k3s/k3s.yaml alone is not enough.
        loaded = self.guest.run_argv(
            ["uv", "run", "python", "-c", "from kubernetes import config; config.load_kube_config(); print('loads')"],
            cwd=config.GUEST_TREE, timeout=180)
        self.record_result("kubeconfig-readable-by-python-client", "infra", loaded)

        self.expect("guest-tools-present", not self._missing_tools(), self._tool_versions())
        # ...and that they are the *pinned* ones. `tool_versions` has always been recorded, and has
        # always been read by nobody: a guest running helm 3.22.1 under a checkout that declares
        # 4.3.1 still made the lane green, because the only claim the numbers supported was "there
        # is a helm here". Asking the guest's own product code for both tables is what turns the
        # next disagreement into a named failure instead of a line in a JSON file.
        drift, detail = self._tool_version_drift()
        self.expect("guest-tool-versions-match-the-pin", not drift, detail, cls="infra")
        # k3s is not in `PINNED_BINARY_VERSIONS`, because it is not a binary the product execs — it is the
        # cluster, and D8 records its version rather than the product declaring it. That record used to be
        # the whole implementation, which is how a stable-channel promotion arrived as a guest that
        # crash-looped with nobody noticing: the version was written into `result.json`, and read by
        # nothing. Now that the lane pins k3s in `01-provision.sh`, the pin is a fact with a check beside
        # it — if the guest reports something other than the release the script installs, that is a
        # named failure and not a paragraph in a log file nobody opens until the run is over.
        k3s_drift, k3s_detail = self._k3s_version_drift()
        self.expect("guest-k3s-matches-the-pinned-release", not k3s_drift, k3s_detail, cls="infra")
        # k3s installs its own charts (traefik and its CRD) through the HelmChart
        # controller, so those releases exist before kubementat ever runs. Without a
        # baseline they look like residue that survived uninstall - and the baseline has
        # to be taken *after* those charts land, which can trail the node going Ready.
        baseline = self._helm_baseline()
        # Two spellings of the same baseline, on purpose. `helm_baseline_releases`
        # stays what it has always been -- a sorted list of release *names*, which is what
        # result.json carries and what its readers are used to -- and `helm_baseline_ids` adds the
        # `namespace/name` form the residue assert has to compare, because k3s's `kube-system/traefik`
        # and kubementat's `traefik/traefik` are two objects that share one name. Redefining the
        # first in place is the mistake that leaves a caller subtracting tuples from strings and
        # reading the difference as empty.
        self.state["helm_baseline_releases"] = sorted(row["release"] for row in baseline)
        self.state["helm_baseline_ids"] = sorted(_release_id(row) for row in baseline)
        self._storage_probe()

        self.state["baseline_namespaces"] = self.cluster.namespaces()
        self.artifacts.snapshot("ns-post-provision", self.state["baseline_namespaces"])
        self.state["baseline_cluster_scoped"] = {kind: self.cluster.cluster_scoped_names(kind)
                                                 for kind in config.CLUSTER_SCOPED_RESIDUE_KINDS}
        self.artifacts.snapshot("cluster-scoped-post-provision", self.state["baseline_cluster_scoped"])
        listing = self.guest.run(command="ls %s | head -20" % shlex.quote(config.GUEST_TREE), timeout=30)
        self.expect("guest-tree-copied", listing.ok, "guest copy exists (%s entries visible)" % len(listing.stdout.splitlines()))
    def _point_pipeline_at_a_resolvable_image(self):
        """An isolated VM has no in-cluster registry, so the image the task composes as
        `<base>/<name>:<tag>` has to point somewhere that serves something.

        How that happens changed, and the change is the whole point of this method now. It used to be
        a `jq` that rewrote three keys of `static.json` immediately after the tree was prepared, which
        was configuration arriving through the lane's own hand instead of through the verb that
        prepared the directory -- and it was also unnecessary, because deciding which keys a lane owes
        is the same rule `config init` follows, and it answers exactly two of them (`DOCKER_REGISTRY_BASE_URL`, which the
        shipped layers do not answer at all, and `TEKTON_CI_IMAGE_NAME`, which differs from
        `ubuntu-ci-minimal`). The third, `TEKTON_CI_IMAGE_TAG`, is `latest` in the shipped layer and in
        the lane's own assertions, so naming it in an argument would be answering a question nobody
        asked, which is the rule for a lane as much as for a `config init`.

        So the harness reads. `config show` answers the three keys and the layer each one came
        from, and the row says whether the reference those three answers compose into is the reference
        this run's own manifests will pull. That is evidence about `config init`'s answers; the jq was
        evidence about jq.
        """
        composed = "%s/%s:%s" % tuple(
            self._config_shown(key, environment=config.ENVIRONMENT)[1]
            for key in ("DOCKER_REGISTRY_BASE_URL", "TEKTON_CI_IMAGE_NAME", "TEKTON_CI_IMAGE_TAG"))
        self.expect("ci-image-reference-resolvable", composed == config.CI_IMAGE,
                    "the tree points the CI image at %r through kmt config init's own answers, which is "
                    "the reference this run's manifests pull (%s). The base and the name are answers "
                    "this lane owes and therefore arguments to the preparation; the tag is what the "
                    "shipped layer answers, so nothing was set for it"
                    % (composed, config.CI_IMAGE))

    # --------------------------------------------- the cluster this lane is addressing
    #
    # Everything the unit suite structurally cannot answer, in one place: what does a real
    # `kmt install` do with a real kubeconfig on a real node. The unit tests can say what
    # `Context.build` computes from a cluster key; they cannot say that the address it computed is
    # the address the releases landed behind, that a refusal really does leave the cluster as it was
    # found, or that a report line a fake filesystem can print at will matches what a real command
    # prints. The four asserts that answer those are here, and they are the reason this ticket
    # exists.
    #
    # One rule runs through the whole block, and it is what makes the answers worth reading: the rig
    # never exports `$KUBECONFIG`, and it never asks kubectl which cluster it thinks is current. Both
    # would let a lane *look* addressed while its own configuration says nothing, which is the exact
    # hole that deleting the fallback closed.

    def _declare_the_cluster(self):
        """Fill in the key `config init` deliberately left unset, and say which context was chosen.

        A freshly prepared environment is *intentionally* invalid until a
        human names the cluster, and the sentence `config init` prints is the instruction to that
        human.
        This lane is that human. It resolves the kubeconfig the way the product resolves it, reads the
        contexts that file declares, names the one it found in the environment's own `static.json`,
        the layer an operator edits, and
        logs which one it chose and why -- so a reader of a lane run can tell the difference between
        "the rig declared its cluster" and "the rig got lucky".

        What it does not do is the two things that would make the rest of the block meaningless:
        export `$KUBECONFIG`, or accept the kubeconfig's own `current-context` as the answer. Either
        would address the run from outside kubementat's configuration, which is the defect v2 retires.
        """
        static = self._config_path(config.ENVIRONMENT, "static.json")
        present, value = self._config_declaration_at(static, config.CLUSTER_KUBE_CONTEXT_KEY)
        self.expect("config-init-written-the-cluster-key-as-null", present and value is None,
                    "after config init, platform_config/%s/static.json %s. What `config init` promises "
                    "is that a new "
                    "environment exists and addresses nothing yet, because the preparation recording "
                    "what kubectl happens to be pointed at would be kubementat making a claim it did "
                    "not earn" % (config.ENVIRONMENT, _declaration_wording(present, value)))

        kubeconfig = self._resolved_kubeconfig_path()
        contexts, unreadable = self._kubeconfig_contexts(kubeconfig)
        names = sorted(contexts)
        self.expect("rig-addresses-the-one-context-its-kubeconfig-declares", bool(names) and len(names) == 1,
                    "%s declares %s. The rig needs exactly one context to name: this VM's k3s install "
                    "has one, and a machine that declares more is addressing a cluster this lane was "
                    "not built against -- picking between them would be the current-context fallback "
                    "with extra steps%s" % (kubeconfig, names or "no contexts at all",
                                           "" if not unreadable else " | kubectl: " + unreadable))
        if len(names) != 1:
            raise StageFailed("cannot name the cluster to install into: %s declares %s%s"
                              % (kubeconfig, names or "no contexts",
                                 " (kubectl: %s)" % unreadable if unreadable else ""))
        context = names[0]
        facts = contexts[context]
        address = facts["address"]

        # The one answer this stage owes the tree, and the verb that answers it. `config set` is
        # what a supported write looks like; a `jq --arg` into the same file is the defect. The read-back
        # is `config show`, whose per-key line also names which layer and which file the answer came
        # from, so the row can name the layer it landed in from kubementat's own sentence instead of
        # from the harness pointing at a path and calling it that layer.
        written = self._config_set(config.CLUSTER_KUBE_CONTEXT_KEY, context)
        shown, round_trip, provenance = self._config_shown(config.CLUSTER_KUBE_CONTEXT_KEY)
        self.expect("environment-names-its-cluster-in-static-json",
                    written and shown and round_trip == context and bool(address),
                    "environment %s declares %s=%r through kmt config set (shown as: %s), and the "
                    "context reaches %s -- the environment's own static.json, which is where an operator "
                    "would edit it, "
                    "not an export the harness made"
                    % (config.ENVIRONMENT, config.CLUSTER_KUBE_CONTEXT_KEY, context,
                       provenance or "(no line for this key)", address))

        # The line a human reads. It states the choice and the reason for it, because "the rig picked
        # a cluster" is not evidence and "the only context this kubeconfig declares is this VM's own
        # k3s" is.
        self.log("cluster: environment %s now declares %s=%r, which reaches %s; chosen because the "
                 "kubeconfig the product resolves (%s) declares exactly that one context, the file was "
                 "named where the environment's own static.json is read, and nothing was exported to "
                 "$%s to make the "
                 "run work"
                 % (config.ENVIRONMENT, config.CLUSTER_KUBE_CONTEXT_KEY, context, address, kubeconfig,
                    config.KUBECONFIG_ENVIRONMENT_VARIABLE))
        self.state.setdefault("config_adjustments", []).append({
            "file": self._config_report(config.ENVIRONMENT, "static.json"),
            "field": config.CLUSTER_KUBE_CONTEXT_KEY,
            "value": "%s -> %s" % (context, address)})
        self.state["rig_cluster"] = {"kubeconfig": kubeconfig, "context": context, "address": address,
                                     "cluster": facts["cluster"], "user": facts["user"],
                                     "static_file": self._config_report(config.ENVIRONMENT, "static.json")}
        self._expect_nothing_exports_kubeconfig(kubeconfig)

    def _declare_the_team_namespaces(self):
        """Write down the two namespaces a team's work lands in, which the tree is currently guessing.

        `APP_DEPLOYMENT_NAMESPACE` and `PIPELINE_NAMESPACE` are answered on a `config init` tree by a
        rule over the team's name, and that is the right answer: `smoke` team's applications live in
        `smoke`, its pipelines in `smoke-pipelines`, with nobody having typed anything. The answer is
        about *placement*, and the plan's ruling on derivation is that it stops there -- "a rule says
        where things go and is silent on what may be deleted". So `kmt uninstall` reads what the team's
        file set and deletes that, reads the derived pair only to say what it declined to sweep, and
        warns. On a tree that declares nothing the warning is true and the namespaces survive, and the
        lane's residue row is then measuring the difference between kubementat's own work and nothing.

        The product tells the operator what to do about it in that warning's own words: *if it is a
        team, write the namespace into the file*. So the lane does that, through `config set --team`,
        with the values the rule was already giving. Placement is provably unchanged -- the assert reads
        the answer back after the write for exactly that reason -- and what changed is only that
        kubementat now owns those namespaces to delete.

        """
        declared = {}
        for key in ("APP_DEPLOYMENT_NAMESPACE", "PIPELINE_NAMESPACE"):
            answered = self._team_config_value(key)
            if not answered:
                self.expect("team-namespaces-declared", False,
                            "%s is answered by nothing on the team's tree, so there is no namespace to "
                            "write down and uninstall cannot be told what it owns" % key)
                return
            declared[key] = answered
        written = all(self._config_set(key, value, environment=config.ENVIRONMENT, team=config.TEAM)
                      for key, value in declared.items())
        now_declared = {key: value for key, value in
                        ((key, self._team_config_declares(key)[1]) for key in declared)}
        self.expect("team-namespaces-declared", written and now_declared == declared,
                    "kmt config set --team wrote %s into the team layer, and the team now declares "
                    "exactly the pair the rule over its own name had been giving (%s). Placement is "
                    "unchanged, which is the point: only the permission to delete moved."
                    % (declared,
                       ", ".join("%s=%r" % (key, now_declared.get(key)) for key in sorted(declared))))
        self.state.setdefault("config_adjustments", []).append({
            "file": self._config_report(config.ENVIRONMENT, config.TEAM, "static.json"),
            "field": " + ".join(declared),
            "value": ", ".join("%s=%s" % (key, value) for key, value in sorted(declared.items()))})

    def _declare_the_acme_account(self):
        """Answer the one key routing reads that preparation cannot know for anyone.

        Both ClusterIssuers kubementat applies register an ACME account with a contact address, and
        routing reads that address from the environment's own `static.json` before it touches
        cert-manager. `kmt config init` deliberately writes no address -- it knows the cluster's keys
        and nothing about anybody's domain -- so on a tree straight out of the preparation the key is
        absent and `kmt routing install` refuses, saying it will not apply an issuer that cannot issue.

        That refusal was correct and the lane was wrong. The key has been sitting in this file's
        constants since the suite was written, in a table of dummy answers that stopped being read when
        the environment-variable layer did, and a value that is declared but never passed anywhere is
        indistinguishable from a value that is not declared at all. Only the full lane installs the
        routing group, which is why no run noticed: every lane that ran before was a lane that never
        asked the question.

        So the address goes in the way every other answer goes in, through `config set`, and the assert
        reads it back with `config show` so the claim is the product's and not this file's. The domain
        is the reserved one this suite uses for every address it invents, for the same reason: a real
        one would be an account somebody registered.
        """
        written = self._config_set("CLUSTER_MANAGER_EMAIL", config.CLUSTER_MANAGER_EMAIL,
                                   environment=config.ENVIRONMENT)
        shown, round_trip, _ = self._config_shown("CLUSTER_MANAGER_EMAIL")
        self.expect("environment-answers-the-acme-contact-routing-needs",
                    written and shown and round_trip == config.CLUSTER_MANAGER_EMAIL,
                    "environment %s declares CLUSTER_MANAGER_EMAIL=%r through kmt config set, which is "
                    "the address both ClusterIssuers register with and the key routing install reads "
                    "before it would refuse (shown as: %s). Nothing was exported and nothing was written "
                    "into the tree by hand"
                    % (config.ENVIRONMENT, config.CLUSTER_MANAGER_EMAIL, round_trip or "(no line)"))
        self.state.setdefault("config_adjustments", []).append({
            "file": self._config_report(config.ENVIRONMENT, "static.json"),
            "field": "CLUSTER_MANAGER_EMAIL", "value": config.CLUSTER_MANAGER_EMAIL})

    def _resolved_kubeconfig_path(self):
        """The file the product will resolve, read without changing anything.

        The rule when `CLUSTER_KUBECONFIG_PATH` is unset is the Kubernetes library's own: the
        variable, then `~/.kube/config`. The rig answers that question with a `printf` and sets
        nothing, so the next assert can say honestly that the address came from a file and not from
        an environment the harness arranged.
        """
        found = self.guest.run(command='printf %%s "${%s:-$HOME/.kube/config}"'
                               % config.KUBECONFIG_ENVIRONMENT_VARIABLE, timeout=30)
        path = (found.stdout or "").strip()
        return path or "$HOME/.kube/config"

    def _kubeconfig_contexts(self, kubeconfig):
        """`({name: {address, cluster, user}}, error)` for one kubeconfig file.

        Read out of the file with kubectl's own reader, which resolves no connection and no credential
        refresh -- `config view` answers what the file declares. Asking the cluster is not an option
        and asking `current-context` is the gate that resolving from configuration removed from
        `automation.py` for the same
        reason: a lane that found its cluster that way would be addressing something its own
        configuration never claimed.
        """
        view = self.guest.run(command="kubectl --kubeconfig %s config view -o json" % shlex.quote(kubeconfig),
                              timeout=90)
        if not view.ok:
            return {}, view.excerpt(200)
        try:
            document = json.loads(view.stdout)
        except ValueError as exc:
            return {}, "config view did not answer JSON: %s" % exc
        clusters = {entry.get("name"): (entry.get("cluster") or {}) for entry in document.get("clusters") or []}
        contexts = {}
        for entry in document.get("contexts") or []:
            body = entry.get("context") or {}
            server = str((clusters.get(body.get("cluster")) or {}).get("server") or "").strip()
            contexts[entry.get("name")] = {"address": server.rstrip("/"),
                                           "cluster": body.get("cluster"),
                                           "user": body.get("user")}
        return contexts, ""

    def _expect_nothing_exports_kubeconfig(self, kubeconfig):
        """The run is addressed by the file the rig named, not by an environment variable.

        Checked in the guest rather than in the harness source, because the claim is about the process
        that ran. `readlink -f` on both sides so the rig is not accused of exporting a variable that
        points at the same file under a different spelling -- the guest's `~/.kube/config` is a
        symlink to k3s's own, and two names for one file is not two files.
        """
        verdict = self.guest.run(
            command='p="${%s:-}"; if [ -z "$p" ]; then echo unset; '
                    'elif [ "$(readlink -f "$p")" = "$(readlink -f %s)" ]; then echo same-file; '
                    'else echo "$p"; fi'
            % (config.KUBECONFIG_ENVIRONMENT_VARIABLE, shlex.quote(kubeconfig)),
            timeout=30)
        answer = (verdict.stdout or "").strip()
        self.expect("nothing-exports-kubeconfig-to-address-the-run",
                    answer in ("unset", "same-file"),
                    "%s in the guest command environment is %s, so the cluster the run addresses comes "
                    "from %s (the library's own rule, read rather than exported)%s"
                    % (config.KUBECONFIG_ENVIRONMENT_VARIABLE,
                       "unset" if answer == "unset" else answer,
                       kubeconfig,
                       "" if answer != "same-file" else " — the variable and the file are two names "
                       "for one file, which is not an export but is worth saying",))

    def _expect_install_named_the_cluster(self, install_result):
        """The cluster report, in the output of the command that was about to write to the cluster.

        The line is the whole promise of naming a cluster in one sentence: the address, and where the claim came
        from. `install` is the command that has to print it *before* it writes, so this is the place
        a regression that hides the cluster shows up -- either as a missing line, or as a line that
        names a cluster this lane never declared.
        """
        facts = self.state.get("rig_cluster") or {}
        line = self._resolution_report_line(install_result.output, config.ENVIRONMENT)
        address = facts.get("address") or ""
        named = bool(line) and bool(address) and address in line \
            and "static.json" in line
        self.expect("install-names-the-cluster-it-installed-into", named,
                    "install printed %r; the rig's cluster is %s at %s, declared by %s | %s"
                    % (line or "no `cluster %s:` line at all" % config.ENVIRONMENT, config.ENVIRONMENT,
                       address or "(unknown)", facts.get("static_file", "the environment's static.json"),
                       install_result.excerpt(160)))

    def _expect_releases_landed_where_the_environment_says(self):
        """The namespace is the other half of "which cluster", and it is read from where the tree answers.

        A resolution report that names a cluster proves nothing if the releases it installed went
        somewhere else than the environment's own `*_DEPLOYMENT_NAMESPACE` key names, because the
        address and the namespace are the two halves of one claim. Grafana is the release this asks
        about: it belongs to the `standard` group every lane installs, its namespace comes from
        environment-level configuration rather than a chart default, and nothing in a later stage
        moves it before this assert runs (`feature_namespace_rename` renames namespaces, but that is
        stage 05, after install).

        The answer and its layer arrive on one line of `config show`, and the reason is the one that
        made this read a file a bad instrument: a namespace that came from a shipped default is a
        different claim from one an operator typed, and both have to land the release in the same place.
        Saying which layer answered is what lets a red here be attributed -- to the release, to the
        chart, to the layer, or to nothing.
        """
        answered, declared, provenance = self._config_shown("GRAFANA_DEPLOYMENT_NAMESPACE",
                                                            environment=config.ENVIRONMENT)
        rows = [row for row in self.cluster.helm_list() if row["release"] == "grafana"]
        landed = sorted({row["namespace"] for row in rows})
        self.expect("releases-landed-where-the-environment-says",
                    bool(declared) and landed == [declared],
                    "GRAFANA_DEPLOYMENT_NAMESPACE=%r%s and the grafana release is in %s%s"
                    % (declared or "(the tree answers no GRAFANA_DEPLOYMENT_NAMESPACE)",
                       "" if not declared else " (%s)" % (provenance or "layer untagged"),
                       landed or "(no such release)",
                       "" if landed == [declared] else " | releases visible: %s"
                       % ", ".join(sorted("%s/%s" % (row["namespace"], row["release"]) for row in rows)[:5])))

    def _resolution_report_line(self, output, environment):
        """The cluster line for one environment, or '' when the command said nothing about its cluster.

        Line-based because the report goes to stderr (it is a claim and not output) and the run's
        combined output has everything else in it too. Contained rather than anchored because stdout
        and stderr are joined: a command that finished its last line without a newline would otherwise
        hide the report one character inside it, and an assert that only reads whole lines would report
        that the command said nothing about its cluster when it said it plainly. An empty answer is
        still a real answer here: a command that wrote to a cluster without naming it is the failure
        this report exists to catch.
        """
        for line in (output or "").splitlines():
            if "cluster %s:" % environment in line:
                return line.strip()
        return ""

    def _config_declaration_at(self, path, field):
        """`(present, value)` for one key of one file, so an error can name the value it found.

        Absent, null and empty are the three shapes an "absent / null" column allows, they read as
        three different files to whoever is looking, and the refuse path says which one it met. A read
        that flattened them would let the harness assert a fact the product could not have told.
        """
        program = 'if has($key) then "present" else "absent" end, (.[$key]|tojson)'
        read = self.guest.run(command="jq -r --arg key %s %s %s"
                              % (shlex.quote(field), shlex.quote(program), shlex.quote(path)), timeout=30)
        if not read.ok:
            return False, None
        lines = (read.stdout or "").strip().splitlines()
        present = bool(lines) and lines[0].strip() == "present"
        value = None
        if len(lines) > 1:
            try:
                value = json.loads(lines[1])
            except ValueError:
                value = lines[1].strip()
        return present, value

    def _helm_release_ids(self):
        """`(sorted namespace/name ids, error)` -- the release set as an identity rather than a name.

        The error half is the one that matters for "nothing was written": `helm_list()` answers `[]`
        both for a cluster with nothing installed and for a cluster that would not say, and a negative
        assert that reads a failure as an absence is an assert that passes on a broken cluster.
        """
        rows, error = self.cluster.helm_list_rows()
        return sorted(_release_id(row) for row in rows), error

    def _nodes_ready(self):
        try:
            count, nodes = self.cluster.node_count_ready()
        except asserts.KubectlError as exc:
            return False, "kubectl failed: %s" % exc
        if not count:
            conditions = asserts._conditions(nodes[0]) if nodes else {}
            return False, "no Ready node (conditions=%s)" % conditions
        return count == 1, "%s Ready node(s)" % count

    def _wait_for_addons(self, patience_s=150, restart_after_s=150, total_s=400):
        """Wait for k3s to deploy its own addons before anything reads the SC.

        The manifests under /var/lib/rancher/k3s/server/manifests are applied once per
        boot and can land after the HelmChart controller is discoverable, which leaves
        a Ready node with zero pods and zero StorageClasses. Patience first: the apply
        usually lands within a couple of minutes. A restart is a last resort and only
        after the cluster has settled, because restarting too early re-enters the same
        race. Environment behaviour, so infra: it may retry once per run.
        """
        def addons_deployed():
            # Both reads are guarded, not just the first. The storage class was wrapped and the pod
            # list two lines later was not, so the probe could answer "readable" at one call and die
            # with KubectlError at the next one: an apiserver that blips between the two — which is
            # exactly what a k3s mid-restart does — turned a retryable wait into a stage failure.
            # A probe inside a `wait` loop is allowed to say "not yet"; it is not allowed to raise.
            try:
                name, _item = self.cluster.default_storage_class()
                pods = len(self.cluster.pod_list())
            except asserts.KubectlError as exc:
                return False, "cluster not readable yet: %s" % str(exc)[:120]
            return True, "default SC %s, %s pod(s) running" % (name, pods)

        started = time.time()
        passed, detail, attempts = self.cluster.wait("k3s addons deployed (default StorageClass exists)",
                                                    addons_deployed, timeout=patience_s, interval=5, cls="infra")
        if not passed and time.time() - started < total_s:
            self.log("k3s addons still not deployed after %ss, restarting k3s once" % int(time.time() - started))
            self.guest.run(command="sudo systemctl restart k3s", timeout=180)
            self.cluster.wait("Ready after the single addon restart", lambda: self._nodes_ready(),
                              timeout=120, interval=3, cls="infra")
            passed, detail, more = self.cluster.wait("k3s addons deployed after one restart",
                                                    addons_deployed, timeout=total_s - (time.time() - started),
                                                    interval=5, cls="infra")
            attempts += more
        self.record("assert:k3s-addons-deployed", "infra", passed, detail, attempts=attempts)

    def _k3s_pinned_version(self):
        """The release `01-provision.sh` installs, read out of the script that installs it.

        The number lives in the shell script and not in a Python constant for one reason: the script is
        the thing that execs `get.k3s.io`, and a copy of a version in a language that cannot install it
        is a second place that has to agree. Here the lane reads the only place it is written, which is
        also what makes a promotion show up as a one-line change to one file.

        A script that stopped declaring it is reported and not swallowed: the assert that consumes this
        answer treats "no pin found" as drift, because a silently unpinned lane is the exact shape that
        let a stable-channel promotion crash-loop a guest with nothing naming it.
        """
        try:
            script = Path(self.opts.provision_script).read_text()
        except OSError as e:
            return None, "the provisioning script at %s could not be read (%s), so no pin exists to check"
        match = re.search(r'^K3S_VERSION="([^"]+)"', script, re.MULTILINE)
        if not match:
            return None, "01-provision.sh no longer declares K3S_VERSION; the lane is unpinned again"
        return match.group(1), None

    def _k3s_version_drift(self):
        """Whether the guest runs the k3s the lane pinned.

        Two answers, like `_tool_version_drift`: what the script installs, and what the guest reports.
        They are compared exactly and not as a range, for the reason that already governs the binaries:
        a lane that runs an unmeasured cluster is evidence about the guest image.
        """
        pinned, problem = self._k3s_pinned_version()
        if problem:
            return [problem], problem
        result = self.guest.run(command="k3s --version | head -1", timeout=60)
        running = self._k3s_release(result.stdout)
        if not result.ok or running is None:
            return (["k3s did not answer a version this lane can parse (exit %s): %s"
                     % (result.exit_code, result.excerpt(120))], result.excerpt(160))
        if running != pinned:
            return (["k3s on the guest is %s, but 01-provision.sh pins %s" % (running, pinned)],
                    "The guest is running k3s %s and the lane pinned %s. Either the channel promoted "
                    "and the pin was not bumped, or a guest survived from an earlier run; both mean the "
                    "evidence below is about a cluster nobody certified." % (running, pinned))
        return [], "guest k3s is %s, the pinned release" % pinned

    @staticmethod
    def _k3s_release(text):
        """The release tag inside `k3s --version`'s first line, or None when there is not one.

        The line reads `k3s version v1.36.5+k3s1 (deadbeef)` and the last field is a commit hash, so
        the answer is matched and not split. Splitting is what a comparison that looks exact does, and
        it is what this check did at first: it took the hash and compared it to a version, which fails
        for every guest including a correctly pinned one.
        """
        match = re.search(r"v\d+\.\d+\.\d+\+k3s\d+", text or "")
        return match.group(0) if match else None

    def _missing_tools(self):
        missing = []
        for tool in ("helm", "helmfile", "tkn", "uv", "git", "git-crypt", "yq", "pip", "kubectl"):
            result = self.guest.run(command="command -v %s" % tool, timeout=30)
            if not result.ok:
                missing.append(tool)
        if not self.guest.run(command="helm diff version", timeout=60).ok:
            missing.append("helm diff")
        self.state["missing_tools"] = missing
        return missing

    # Both tables, read in the guest by the code that owns them: the declaration is
    # `constants.PINNED_BINARY_VERSIONS`, the argv each binary is asked with is
    # `automation.BINARY_VERSION_ARGV`, and the parser is `automation.first_binary_version`. A
    # harness-side copy of any of the three is exactly the drift this probe exists to catch, so the
    # guest answers its own question and this stage only compares what it says.
    PINNED_TOOL_PROBE = """
import json
from kubementat import shell
from kubementat.core import constants
from kubementat.automation import BINARY_VERSION_ARGV, first_binary_version

seen = {}
for binary, argv in sorted(BINARY_VERSION_ARGV.items()):
    answer = shell.run(argv, capture=True, check=False)
    seen[binary] = first_binary_version("%s%s" % (answer.stdout or "", answer.stderr or ""))
print(json.dumps({"declared": dict(constants.PINNED_BINARY_VERSIONS), "observed": seen}))
""".strip()

    def _tool_version_drift(self):
        """What the guest runs against what the guest's checkout says it must run.

        Two answers and not one, because the failure this guards is the lane measuring a binary the
        product does not declare: the install would pass, the uninstall would pass, and the evidence
        would be about the guest image rather than about kubementat. The comparison is exact and not
        a range on purpose -- the product only *warns* when a cluster's helm sits a major off, since
        `e57fed3` bought that portability deliberately, but a lane is not a cluster and has no
        license to run an unmeasured version.

        Returns:
            (drift, detail): one line per binary that disagrees, empty when they all match, plus the
                detail the assert records. A probe that cannot answer is drift, because "we do not
                know which helm this was" is not an answer the evidence may be built on.
        """
        probe = self.guest.run_argv(["uv", "run", "python", "-c", self.PINNED_TOOL_PROBE],
                                    cwd=config.GUEST_TREE, timeout=180)
        if not probe.ok:
            return (["the guest could not ask its own product what versions it declares (exit %s): %s"
                     % (probe.exit_code, probe.excerpt(160))], probe.excerpt(200))
        try:
            tables = json.loads(probe.stdout)
            declared = tables["declared"]
            observed = tables["observed"]
        except (ValueError, KeyError, TypeError):
            return (["the guest's version probe answered unreadably: %s" % probe.excerpt(160)],
                    probe.excerpt(200))
        drift = []
        for binary in sorted(set(declared) | set(observed)):
            wanted, running = declared.get(binary), observed.get(binary)
            if running == wanted:
                continue
            if running is None:
                drift.append("%s did not answer a version this product can parse, while "
                             "constants.PINNED_BINARY_VERSIONS declares %s" % (binary, wanted))
            else:
                drift.append("%s on the guest is %s, but constants.PINNED_BINARY_VERSIONS declares %s"
                             % (binary, running, wanted))
        if drift:
            return drift, ("; ".join(drift)
                           + ". A lane runs the binaries the product declares or it is evidence about"
                             " the guest image, not about kubementat: 01-provision.sh installs what"
                             " constants.py says, so a drift here means that read broke.")
        return [], ("guest runs " + ", ".join("%s %s" % (b, observed[b]) for b in sorted(observed))
                    + " -- what constants.PINNED_BINARY_VERSIONS declares")

    def _tool_versions(self):
        versions = {}
        for name, command in (
            ("helm", "helm version --short"),
            ("helmfile", "helmfile --version"),
            ("tkn", "tkn version"),
            ("uv", "uv --version"),
            ("kubectl", "kubectl version --client=true -o json"),
            # k3s is pinned in `01-provision.sh` rather than declared in `PINNED_BINARY_VERSIONS`, because
            # it is the cluster and not a binary kubementat execs, and this line is what keeps the older
            # half of D8 true: a green run still names what it ran against, so a pinned version that stops
            # matching reality is visible in the evidence instead of only in a script nobody reads.
            ("k3s", "k3s --version | head -1"),
        ):
            result = self.guest.run(command=command, timeout=60)
            line = result.stdout.strip().splitlines()
            if name == "kubectl":
                # `-o json` would otherwise leave the first brace as the "version".
                try:
                    versions[name] = json.loads(result.stdout)["gitVersion"]
                except (ValueError, KeyError):
                    versions[name] = result.excerpt(80)
                continue
            versions[name] = line[0] if line else result.excerpt(80)
        self.state["tool_versions"] = versions
        return json.dumps(versions)

    def _helm_baseline(self, patience_s=300):
        """The releases that exist before kubementat installs anything, as the cluster reports them.

        k3s installs `traefik-crd` and then `traefik` through its HelmChart controller, and both
        trail the node going Ready. Until both are `deployed`, their releases, CRDs and cluster
        RBAC are still arriving, and every baseline taken from this point on would read them as
        residue kubementat left behind - measured: a lane whose uninstall was blamed for traefik.

        It returns the release rows and not a name list for the same reason a residue assert needs a
        namespace: two releases can hold the same name and be different objects,
        so "was this already here" is only answerable from `namespace/name`.
        """
        deadline = time.time() + patience_s
        rows = []
        while time.time() < deadline:
            rows = self.cluster.helm_list()
            deployed = {row["release"] for row in rows if row["status"] == "deployed"}
            if {"traefik", "traefik-crd"} <= deployed:
                return rows
            time.sleep(3)
        self.log("k3s's traefik releases were not both deployed after %ss; baseline: %s"
                 % (patience_s, sorted(row["release"] for row in rows)))
        return rows

    def _storage_probe(self):
        """Read the default SC from the cluster, then prove it binds by scheduling a consumer."""
        try:
            name, item = self.cluster.default_storage_class()
        except asserts.KubectlError as exc:
            self.expect("storage-class-discovered", False, str(exc))
            raise StageFailed("no StorageClass in the cluster: %s" % exc)
        self.state["storage_class"] = {
            "name": name,
            "volume_binding_mode": item.get("volumeBindingMode"),
            "provisioner": item.get("provisioner"),
        }
        # The line that handed the discovered class to the guest's environment is gone.
        # `KUBERNETES_DEFAULT_STORAGE_CLASS` left the product in `7888bdf` and has no reader in
        # `src/`; its successor, `TEKTON_KUBERNETES_STORAGE_CLASS`, is a configuration key the tree
        # answers (L0 `local-path`, `constants.DEFAULT_STATIC_ANSWERS`), so the stage's output of the
        # probe is this state entry and the expectation below, and nothing else. `self.state` is what
        # the lane reports from; the environment was always inert.
        self.expect("storage-class-discovered", True,
                    "default SC %s (%s, %s)" % (name, item.get("volumeBindingMode"), item.get("provisioner")))

        applied = self.cluster.apply(_probe_manifest(name))
        self.record_result("storage-probe-apply", "infra", applied)
        if not applied.ok:
            raise StageFailed("could not create the storage probe: %s" % applied.excerpt(200))
        try:
            passed = self.wait("storage-probe-consumer-running",
                               "probe consumer pod Running and claim Bound",
                               self._probe_running, timeout=120, interval=4)
        finally:
            self.cluster.kubectl("delete", "pod/%s" % PROBE_POD, "pvc/%s" % PROBE_CLAIM,
                                 "--ignore-not-found", "--wait=false", timeout=180)
        claims = self.cluster.pvc_status()
        self.state["probe_claims"] = {"bound": len(claims["bound"]), "waiting": claims["waiting_for_consumer"]}
        return passed

    def _probe_running(self):
        try:
            pod = self.cluster.json("get", "pod", PROBE_POD, "-o", "json", timeout=60)
        except asserts.KubectlError:
            return False, "probe pod not visible yet"
        phase = (pod.get("status") or {}).get("phase")
        claims = self.cluster.pvc_status()
        bound = any(name.endswith(PROBE_CLAIM) for name in claims["bound"])
        return phase == "Running" and bound, "pod phase=%s claim bound=%s" % (phase, bound)

    # -------------------------------------------------------------- 02-bootstrap

    #: The four keypair names `config init` generates inside the configuration directory, and the
    #: symmetric key git-crypt's export step writes beside them. They belong to the directory and not to
    #: the program that wrote them, and one assert reads both halves of that sentence.
    CONFIG_KEYPAIR_NAMES = ("deployer_ssh_key", "deployer_ssh_key.pub",
                            "gpg_private.key", "gpg_public_key.gpg")
    CONFIG_SYMMETRIC_KEY = "git_crypt_symmetric.key"

    def stage_bootstrap(self):
        """One command, a ready directory, and one install that reaches the cluster.

        Every other lane starts from a tree `config init` prepared and then spends its asserts on what
        the tree is *used for*. This stage asks the earlier question instead: does the preparation
        command alone leave a directory somebody could actually work in, with real `git`, real
        `git-crypt` and real `gpg` in the way, and no file written by hand anywhere between that
        command and the install? The ten properties of the readiness checklist are asserted against
        this filesystem in the order the checklist lists them, and the last one is the install itself,
        because "ready" is a word that has to be earned by a command that ran and not by a list of
        paths that exist.

        Two of the ten decide whether git-crypt is *live* rather than merely configured, and they are
        the reason this stage exists at all: unit tests against fixtures cannot show that the ordering
        step was done in the right order. Both are written against bytes that were actually written,
        for the reasons in the two helper functions below.
        """
        root = self.lane.config_dir
        if self.cluster is None:
            raise StageFailed("no cluster: provisioning never completed")
        # The directory is the lane's own and it has to start out empty: `config init` refuses foreign
        # material rather than editing around it, so a non-empty directory is the stage's own bug and
        # not something to discover three asserts late.
        if self.guest.run(command="test -d %s" % shlex.quote(root), timeout=20).ok:
            raise StageFailed("the bootstrap lane's configuration directory already exists: %s" % root)

        prepared = self.km("config.init", timeout=600)
        self.record_result("cap:config.init", "product", prepared)
        if not prepared.ok:
            raise StageFailed("config init failed, so there is no tree to assert anything against")

        self._expect_the_first_commit(prepared)
        self._expect_git_crypt_initialised_there()
        self._expect_the_keypair_belongs_to_the_directory(root)
        self._expect_the_encrypted_file_was_stored_encrypted(root)
        self._expect_the_layout_and_no_markers(root)
        self._expect_exactly_one_generated_credential(root)
        self._expect_the_cluster_is_still_unaddressed(prepared)
        self._expect_validate_says_only_that(prepared)
        self._expect_a_second_init_writes_nothing()

        # The tree is now known good, and the one thing the checklist says `config init` must not
        # invent is the cluster. It is named here, through the verb that names clusters, and only then
        # does anything install into it -- so row 10's claim is that the install followed the
        # preparation, and not that a fixture had quietly addressed the cluster beforehand.
        self._declare_the_cluster()
        install_result = self.km("install", group=self.lane.install_groups[0],
                                 timeout=self.lane.timeouts["02-bootstrap"])
        self.record_result("cap:install", "product", install_result,
                           detail_prefix="group=%s " % self.lane.install_groups[0])
        self.expect("bootstrap-install-ran-against-the-prepared-directory", install_result.ok,
                    "kmt install ran against %s with no write step in between the preparation and it, "
                    "and %s. The whole claim of the checklist's last row is that nothing had to be "
                    "written by hand first: exit=%s%s"
                    % (root, "it succeeded" if install_result.ok else "it failed",
                       install_result.exit_code,
                       "" if install_result.ok else " | " + install_result.excerpt(400)))
        if install_result.ok:
            self._expect_install_named_the_cluster(install_result)
            self._expect_release_set(self.lane.install_groups)

        # Read second, assert last: a plaintext blob anywhere in the history of the encrypted path is
        # the defect the first of the two git-crypt asserts can miss, and the history is the only place
        # it cannot hide.
        self._expect_no_plaintext_credential_in_the_history(root)

    def _git(self, root, *argv, timeout=30):
        """One `git` read inside the lane's own configuration directory. Reads only, always.

        The lane never runs `git-crypt lock` or `unlock` and never commits: the worktree is plaintext by
        design and stays that way, and both of the git-crypt asserts work in the state `config init`
        leaves. Locking the tree would measure a state the product never creates, and would itself be a
        write.
        """
        line = " ".join(shlex.quote(token) for token in ("git", "-C", root) + tuple(argv))
        return self.guest.run(command=line, timeout=timeout)

    def _expect_the_first_commit(self, prepared):
        # The first commit exists is not a vague claim: it is a count, measured on the first commit and
        # nowhere else. A second commit would mean the preparation wrote history the operator was never
        # told about, and a missing one would mean the tree was staged and never committed.
        repository = self._git(self.lane.config_dir, "rev-parse", "--git-dir")
        commits = self._git(self.lane.config_dir, "rev-list", "--count", "HEAD")
        count = commits.stdout.strip() if commits.ok else ""
        self.expect("bootstrap-made-one-commit-and-only-one", repository.ok and count == "1",
                    "git says this is %s and %s commits exist. The preparation is meant to commit once, "
                    "so anything other than one is either an uncommitted tree or history nobody was "
                    "shown | git: %s | count: %s | init: %s"
                    % (repository.stdout.strip() if repository.ok else "not a repository at all",
                       count or "nothing", repository.excerpt(120), count or "(unreadable)",
                       prepared.excerpt(200)))

    def _expect_git_crypt_initialised_there(self):
        # Three artifacts, three different steps. The keystore under .git is what `git-crypt init`
        # leaves, the registered collaborator under .git-crypt/keys is what makes a *person* able to
        # read the tree, and the symmetric key at the root is what makes this directory readable by
        # something that is not a person -- which is why its mode is asserted alongside its existence.
        keystore = self.guest.run(command="test -e %s" % shlex.quote(
            posixpath.join(self.lane.config_dir, ".git", "git-crypt", "keys", "default")), timeout=20)
        collaborators = self.guest.run(command="ls %s" % shlex.quote(posixpath.join(
            self.lane.config_dir, ".git-crypt", "keys", "default", "0")), timeout=20)
        registered = sorted(name for name in collaborators.stdout.split() if name.endswith(".gpg"))
        key = posixpath.join(self.lane.config_dir, self.CONFIG_SYMMETRIC_KEY)
        # One quoted format argument, because `stat -c %a %n FILE` is a format of `%a` and *two file
        # operands*: `%n` is then looked up as a filename, the mode still prints, and the exit code says
        # failure over a file that is there and readable. That shape of bug reads as "missing with mode
        # 600", which is exactly as confusing as it sounds.
        listed = self.guest.run(command="stat -c '%%a %%n' %s" % shlex.quote(key), timeout=20)
        mode, _, named = listed.stdout.strip().partition(" ")
        self.expect("git-crypt-is-live-in-that-directory",
                    keystore.ok and bool(registered) and listed.ok and mode == "600",
                    "the keystore is %s, the registered collaborators are %s, and the symmetric key is "
                    "%s at %s with mode %s. Three steps of the dance, three things that can go missing "
                    "on their own -- and 600 matters because that file is the ability to read every "
                    "credential in the cluster. Its absence and an unregistered collaborator are two "
                    "different failures: one says the tree cannot be opened, the other says it cannot be "
                    "opened without a particular person"
                    % ("there" if keystore.ok else "missing", registered or "nobody registered",
                       "there" if listed.ok else "missing", named or key, mode or "(unreadable)"))

    def _expect_the_keypair_belongs_to_the_directory(self, root):
        # Where the deployer keypair lives is a sentence with two halves, and the second one is the half
        # a present-tense assert
        # always forgets: the deployer keypair is *not in the program's own tree* anymore. The lane's
        # configuration directory is deliberately outside the guest copy of the checkout for that
        # reason, so this can read the absence and not merely the presence.
        inside = {name: self.guest.run(command="test -s %s" % shlex.quote(posixpath.join(root, name)),
                                       timeout=20).ok
                  for name in self.CONFIG_KEYPAIR_NAMES}
        found = self.guest.run(command=r"find %s \( -name %s \) 2>/dev/null" % (
            shlex.quote(config.GUEST_TREE),
            " -o -name ".join(shlex.quote(name) for name in self.CONFIG_KEYPAIR_NAMES)), timeout=60)
        outside = sorted(line.strip() for line in (found.stdout or "").splitlines() if line.strip())
        self.expect("keypair-lives-in-the-configuration-directory",
                    all(inside.values()) and not outside,
                    "inside %s: %s. Anywhere else under the guest checkout: %s. A keypair that is a "
                    "property of the configuration directory is the half that cannot be seen by looking "
                    "in the one place people actually check"
                    % (root, {name: ("there" if present else "MISSING") for name, present
                              in sorted(inside.items())},
                       outside or "nothing, which is the point"))

    def _expect_the_encrypted_file_was_stored_encrypted(self, root):
        # Assert 1, and the reason it is written this way. `git show` reads the stored object and applies
        # no filter, so the bytes it prints are the bytes that were written -- which is exactly what a
        # `.gitattributes` that arrived too late puts there. The worktree is the other read, and it is
        # the plaintext the product is meant to leave: both halves have to be read, because a single
        # command that handed back a verdict would let a mock grade its own fixture.
        path = posixpath.join(root, config.ENVIRONMENT, "static.encrypted.json")
        worktree = self.guest.run(command="jq -e 'has(\"GRAFANA_ADMIN_PASSWORD\")' %s" % shlex.quote(path),
                                  timeout=30)
        stored = self.guest.run(command="git -C %s show HEAD:%s | jq -e . 2>&1" % (
            shlex.quote(root),
            posixpath.join(config.ENVIRONMENT, "static.encrypted.json")), timeout=30)
        marker = self.guest.run(command="git -C %s show HEAD:%s 2>/dev/null | grep -a -c GITCRYPT"
                                % (shlex.quote(root), posixpath.join(config.ENVIRONMENT,
                                                                     "static.encrypted.json")),
                                timeout=30)
        magic = marker.stdout.strip().splitlines()[-1] if marker.stdout.strip() else ""
        self.expect("encrypted-files-are-encrypted-where-they-are-stored",
                    worktree.ok and not stored.ok and magic != "0",
                    "the worktree answers %s while the stored object answers %s, and the stored object "
                    "names the git-crypt marker %s times. Plaintext in the worktree and ciphertext in "
                    "the object store is the correct state: if the stored bytes were readable JSON the "
                    "`.gitattributes` arrived after the `git add`, and the one defect that makes this "
                    "tree unsafe is invisible to every other read in the suite | worktree: %s | stored: "
                    "%s" % ("readable JSON" if worktree.ok else "unreadable -- which is not a claim "
                            "about encryption, only about decoding",
                            "still JSON, which is the leak" if not stored.ok else
                            "not JSON, which is what being stored encrypted looks like",
                            magic or "0", worktree.excerpt(160), stored.excerpt(160)))

    def _expect_the_layout_and_no_markers(self, root):
        layer_files = [posixpath.join(config.ENVIRONMENT, "static.json"),
                       posixpath.join(config.ENVIRONMENT, "static.encrypted.json"),
                       posixpath.join(config.ENVIRONMENT, config.TEAM, "static.json"),
                       posixpath.join(config.ENVIRONMENT, config.TEAM, "static.encrypted.json"),
                       "kubementat.yaml"]
        layer_directories = [posixpath.join(config.ENVIRONMENT, "tekton", "tasks"),
                             posixpath.join(config.ENVIRONMENT, "tekton", "pipelines"),
                             posixpath.join(config.ENVIRONMENT, config.TEAM, "tekton", "tasks"),
                             posixpath.join(config.ENVIRONMENT, config.TEAM, "tekton", "pipelines"),
                             posixpath.join(config.ENVIRONMENT, config.TEAM, "tekton", "pipeline-runs"),
                             posixpath.join(config.ENVIRONMENT, config.TEAM, "tekton", "triggers")]
        missing_files = [relative for relative in layer_files
                         if not self.guest.run(command="test -s %s" % shlex.quote(
                             posixpath.join(root, relative)), timeout=20).ok]
        missing_directories = [relative for relative in layer_directories
                               if not self.guest.run(command="test -d %s" % shlex.quote(
                                   posixpath.join(root, relative)), timeout=20).ok]
        # The marker's absence is a fact about the seed this build ships and not an achievement of this
        # run, so the detail says which of the two it is: a tree that answers every key has nothing left
        # to be a placeholder *for*, and that is a property of having no templates to fill.
        markers = self.guest.run(command="grep -r -l REPLACEME_ %s --exclude-dir=.git 2>/dev/null" %
                                 shlex.quote(root), timeout=30)
        left = sorted(line.strip() for line in (markers.stdout or "").splitlines() if line.strip())
        self.expect("prepared-tree-is-complete-and-placeholders-are-gone",
                    not missing_files and not missing_directories and not left,
                    "layer files missing: %s; tekton directories missing: %s; files still carrying a "
                    "REPLACEME_ marker: %s. Nothing here was written by the lane -- that is the claim -- "
                    "and a marker could only survive a tree that was assembled out of templates, which "
                    "is what no longer happens when the preparation writes its own answers"
                    % (missing_files or "none", missing_directories or "none", left or "none"))

    def _expect_exactly_one_generated_credential(self, root):
        # Checklist row 6 says the bootstrap invents nothing, and it is not quite true: one key is
        # generated, because an admin password has no sane default and `config init` is the moment the
        # operator is present to hear about it. So the property is not "nothing is generated" and the
        # assert does not claim that -- it is that exactly one is generated, that it is the key the
        # product's own generation table names, and that nothing *else* in the encrypted document came
        # from anywhere but the operator's `--encrypted-set` or the seeded defaults. Naming the
        # exception is the point: a reader who finds a token in a tree that was told invented nothing
        # will trust the wrong sentence.
        encrypted = posixpath.join(root, config.ENVIRONMENT, "static.encrypted.json")
        read = self.guest.run(command="jq -r 'keys | .[]' %s" % shlex.quote(encrypted), timeout=30)
        keys = sorted(line.strip() for line in (read.stdout or "").splitlines() if line.strip())
        generated_present = config.GENERATED_ENCRYPTED_ANSWER_NAMES[0] in keys
        self.expect("bootstrap-generates-exactly-one-credential", read.ok and generated_present,
                    "the environment's encrypted document answers %s. Exactly one value is generated "
                    "by the preparation, and it is %s -- seeded defaults and anything the lane passed "
                    "with --encrypted-set are the only other sources a key here can have, so a key that "
                    "is not one of those three things is a value kubementat made up"
                    % (keys or "(nothing)",
                       "present, which is the one the generation table names" if generated_present
                       else "absent, which means the tree is not ready to install into"))

    def _expect_the_cluster_is_still_unaddressed(self, prepared):
        shown = self._config_shown(config.CLUSTER_KUBE_CONTEXT_KEY)
        other = self._config_shown(config.CLUSTER_KUBECONFIG_PATH_KEY)
        sentence = (prepared.stderr or "") + (prepared.stdout or "")
        named_the_file = "static.json" in sentence and config.ENVIRONMENT in sentence
        self.expect("a-new-environment-addresses-no-cluster-and-says-so",
                    shown[1] == "null" and other[1] == "null" and named_the_file,
                    "%s=%s and %s=%s, and the sentence printed at preparation was %s. A new environment "
                    "that *did* address a cluster would have recorded what kubectl happens to be pointed "
                    "at, which is a claim the preparation did not earn -- and a new environment that said "
                    "nothing would leave the operator with a directory and no next step"
                    % (config.CLUSTER_KUBE_CONTEXT_KEY, shown[1] or "(no line)",
                       config.CLUSTER_KUBECONFIG_PATH_KEY, other[1] or "(no line)",
                       "it named the file and the key" if named_the_file else
                       "silent about which file to go and edit"))

    def _expect_validate_says_only_that(self, prepared):
        verdict = self.km("config.validate", timeout=120)
        # A fault and the verdict share no shape, and picking on the word "error" cannot tell them apart:
        # the line that says the tree is fine is "no errors for environment ...", which contains it. What
        # a real fault looks like is the indented `code: message` line the command prints when it refuses,
        # where the code is lower-case and hyphenated, so the assert matches that shape and nothing else.
        faults = [line.strip() for line in ((verdict.stdout or "") + (verdict.stderr or "")).splitlines()
                  if re.match(r"^\s+[a-z][a-z0-9_-]*:", line)]
        warnings = [line for line in (verdict.stderr or "").splitlines() if line.strip().startswith("WARNING")]
        about_the_cluster = len(warnings) == 1 and "address" in warnings[0].lower()
        self.expect("validate-says-the-prepared-tree-is-usable-for-everything-except-a-cluster",
                    verdict.ok and not faults and about_the_cluster,
                    "exit=%s, faults=%s, warnings=%s. The one warning a freshly prepared tree is supposed "
                    "to carry is the cluster it does not address yet; anything else it complains about is "
                    "a fault in what config init just wrote, and a tree that is not usable is not 'ready' "
                    "however many files exist"
                    % (verdict.exit_code, faults or "none", warnings or "none"))

    def _expect_a_second_init_writes_nothing(self):
        # The trap the handover names: this second call has to repeat every answer the first one was
        # given. A second `config init` that omits an answer refuses, because the file that is already
        # there is not what the verb would have written, and the assert would then be measuring a
        # refusal while reading it as the skip. Repeating the answers is what makes the claim true.
        before = self._git(self.lane.config_dir, "status", "--porcelain")
        second = self.km("config.init", timeout=300)
        after = self._git(self.lane.config_dir, "status", "--porcelain")
        commits = self._git(self.lane.config_dir, "rev-list", "--count", "HEAD")
        skipped = second.stdout or ""
        reported_skips = [line for line in skipped.splitlines() if line.strip().startswith("skipped")]
        self.expect("a-second-init-changes-nothing-and-says-which-steps-it-skipped",
                    second.ok and after.ok and after.stdout.strip() == before.stdout.strip()
                    and commits.stdout.strip() == "1" and bool(reported_skips),
                    "the second run exited %s, named %d step(s) as skipped, left the working tree %s and "
                    "the commit count at %s. Idempotence that is silent is idempotence an operator cannot "
                    "check, so the skips are asserted as output and not only as absence of change"
                    % (second.exit_code, len(reported_skips),
                       "untouched" if after.stdout.strip() == before.stdout.strip()
                       else "changed: " + (after.stdout.strip() or "(unreadable)"),
                       commits.stdout.strip() or "(unreadable)"))

    def _expect_no_plaintext_credential_in_the_history(self, root):
        # Assert 2, and not the instrument the checklist names. `git log --all -p` against a tree whose
        # attributes arrived on time prints nothing about the encrypted file, because the textconv
        # driver owns how that path is displayed -- which means a green grep answers for the wrong
        # reason on a tree that really did leak. So the question is asked of the object store, one blob
        # per revision the path ever had, where the bytes are the bytes that were written.
        #
        # The credential is resolved inside the guest and never crosses the wire. A value pulled back
        # into an argv of a harness command would be a second home for a secret, and `--from-file` is
        # the whole reason that shape of bug was closed.
        path = posixpath.join(config.ENVIRONMENT, "static.encrypted.json")
        key_file = posixpath.join(root, path)
        command = ("cd " + shlex.quote(root) + "\n"
                   + "secret=$(jq -r '.GRAFANA_ADMIN_PASSWORD // empty' " + shlex.quote(key_file) + ")\n"
                   + "if [ -z \"$secret\" ]; then echo NO-SECRET; exit 0; fi\n"
                   + "hits=0; total=0\n"
                   + "for rev in $(git log --all --format=%H -- " + shlex.quote(path) + "); do\n"
                   + "  total=$((total+1))\n"
                   + "  if git cat-file blob \"$rev:" + path + "\" | grep -a -q -- \"$secret\""
                   + "; then hits=$((hits+1)); echo \"LEAK $rev\"; fi\n"
                   + "done\n"
                   + "echo \"REVISIONS=$total HITS=$hits\"")
        scanned = self.guest.run(command=command, timeout=120)
        summary = ""
        leaks = []
        for line in (scanned.stdout or "").splitlines():
            if line.startswith("REVISIONS="):
                summary = line.strip()
            elif line.startswith("LEAK "):
                leaks.append(line.split()[-1][:12])
        counted = "REVISIONS=" in (scanned.stdout or "")
        self.expect("no-plaintext-credential-in-any-commit-ever",
                    scanned.ok and counted and "NO-SECRET" not in (scanned.stdout or "") and not leaks,
                    "scanned %s and found %s. The credential itself was read out of the working tree "
                    "inside the guest and matched against every stored blob the path has ever had, so "
                    "this row says something the diff-driver-shaped version of it cannot: a history is "
                    "clean only if the bytes are"
                    % (summary or "(no revision count answered -- the scan itself failed, which is not "
                                  "the same as a clean history)",
                       "no plaintext anywhere in it" if (counted and not leaks)
                       else ("plaintext at %s" % leaks if leaks else "nothing, because the scan answered "
                                                                     "nothing")))

    # -------------------------------------------------------------- 02-install

    def stage_install(self):
        if self.cluster is None:
            raise StageFailed("no cluster: provisioning never completed")
        if not self.run_capability("config.init", timeout=300):
            raise StageFailed("config init failed; nothing else in this stage can run")
        self._expect_initialized_tree()
        # Between the preparation and the first command that touches a cluster: the `null` the
        # preparation left
        # behind is filled in here and nowhere else, so the install that follows is addressing a
        # cluster the configuration names rather than one the machine happens to have.
        self._declare_the_cluster()
        # The two namespaces a team's work lands in are answered by a rule over the team's name on any
        # tree `config init` prepared, and the plan's own rule is that deriving them is right: a rule
        # says where objects live and not that this run may remove them. So `kmt uninstall` declines to
        # sweep them and says what it declined -- which is correct behaviour against a tree that declares
        # nothing, and a lane that leaves the tree that way is a lane that can never tell its own
        # residue from its own creation. The product says the same thing in its own warning: *if it is a
        # team, write the namespace into the file*. So the lane does what the product tells an operator
        # to do, through `config set`, and the namespaces it created become its own to delete.
        self._declare_the_team_namespaces()
        self._point_pipeline_at_a_resolvable_image()
        if "routing" in self.lane.install_groups or self._routing_lane():
            # The one answer this lane owes and nobody had been paying: a ClusterIssuer registers an
            # ACME account with an address, `config init` writes no address because it cannot know
            # anybody's domain, and routing is the first thing to ask. The gate is "will this run apply
            # routing", which two lanes answer differently: `full` installs the group without running
            # the certificate feature, `routing` does the reverse. Either way the key is read, so the
            # predicate names both routes instead of picking the one name that happens to be on a lane.
            self._declare_the_acme_account()
        if self._routing_lane():
            # First, not last: the guard's "`kmt install` installed no controller" claim is only
            # decidable against a cluster that had no controller to begin with, and the ports
            # kubementat's own Service needs are only free once the old holder is gone. Both reasons
            # say before the install, and the restart this is the one thing here that has to happen
            # before anything reads the cluster's node ports. This one stays on the feature, because a
            # run that only installs the group has no guard claim to make and no Service to free.
            self._disable_k3s_bundled_traefik()

        # The documented group order. The first group goes through the install
        # capability, the later ones through whatever the table says applies that group.
        groups = list(self.lane.install_groups)
        self.state["install_groups"] = groups
        install_result = self.km("install", group=groups[0], timeout=self.lane.timeouts["02-install"])
        self.record_result("cap:install", "product", install_result, detail_prefix="group=%s " % groups[0])
        if not install_result.ok:
            raise StageFailed("install failed for group=%s: %s" % (groups[0], install_result.excerpt(400)))
        # The report is printed before the first cluster write, so the install's own output is
        # where "which cluster" has to be answerable from. If the line is absent the run installed
        # into something and said nothing about it, which is the defect that deleting the fallback
        # was meant to close.
        self._expect_install_named_the_cluster(install_result)
        if self._routing_lane():
            # `kmt install` is not a routing command, and on this lane that claim can be made against
            # a cluster that had no controller to keep. Nowhere else can it: a lane that starts with
            # k3s's own Traefik on it cannot tell "installed nothing" from "installed nothing new".
            self._expect_kmt_install_installed_no_controller()
        for group in groups[1:]:
            # "How is this group applied" is answered by the table, not here: `routing` goes through
            # the command that reads the cluster and declines when a Traefik it does not own is
            # already serving, which a label filter has no way to express. Stage 04 resolves the
            # same table, so a group cannot be applied one way at install and another way at rerun.
            capability = config.apply_capability_for_group(group)
            extra = self.km(capability, group=group, timeout=LATER_GROUP_TIMEOUT_S)
            self.record_result("cap:%s:%s" % (capability, group), "product", extra,
                               detail_prefix="group=%s " % group)

        self._expect_release_set(groups)
        self._expect_releases_landed_where_the_environment_says()
        self._expect_group_workloads(groups)
        self._expect_pvcs()
        self._expect_single_ready_node()

        self._seed_team_encrypted_answers()
        for capability in ("tekton.setup-pipelines", "tekton.setup-triggers", "registry.access", "secrets.setup"):
            if not self.run_capability(capability, timeout=600):
                raise StageFailed("%s failed" % capability)

        self._expect_secret_round_trip()
        self._expect_pull_secret_attached()
        if self._routing_lane():
            # The last thing stage 02 does, so that a slow load-balancer daemonset cannot eat the
            # budget of the capabilities before it, and so that the `post_install_resource_sets`
            # snapshot below still describes the cluster as the install left it -- with whatever the
            # routing command put on it. This is the only place in the suite where the install branch
            # of the guard runs: every other lane starts with a controller on the node already.
            self._install_routing_and_expect_it()
        self.state["post_install_resource_sets"] = asserts.resource_sets(self.cluster, config.IDEMPOTENCY_KINDS,
                                                                          config.IDEMPOTENCY_EXCLUDED_NAMESPACES)
        self.state["post_install_pvcs"] = self.cluster.pvc_status()["bound"]

    def _expect_initialized_tree(self):
        # The four layer files under the environment and team, then the keypair and the symmetric
        # key -- which live *inside* the configuration directory now: the four keypair
        # names are `config_keys.KEYPAIR_FILE_NAMES`, and `git_crypt_symmetric.key` is the
        # git-crypt step's write target. `platform_config/dev/kubementat_components/
        # helmfile.yaml.gotmpl` is deliberately not on this list: the manifest moved into the
        # package and nothing writes it into a configuration tree any more.
        expected = [
            "platform_config/dev/static.json",
            "platform_config/dev/static.encrypted.json",
            "platform_config/%s/%s/static.json" % (config.ENVIRONMENT, config.TEAM),
            "platform_config/%s/%s/static.encrypted.json" % (config.ENVIRONMENT, config.TEAM),
            "platform_config/deployer_ssh_key",
            "platform_config/deployer_ssh_key.pub",
            "platform_config/gpg_private.key",
            "platform_config/gpg_public_key.gpg",
            "platform_config/git_crypt_symmetric.key",
        ]
        missing = []
        for relative in expected:
            path = posixpath.join(config.GUEST_TREE, relative)
            if not self.guest.run(command="test -s %s" % shlex.quote(path), timeout=20).ok:
                missing.append(relative)
        self.expect("initialized-tree-consistent", not missing, "missing inside the guest copy: %s" % (missing or "none"))

    def _expect_release_set(self, groups):
        """Every release the installed groups declare is there and `deployed`.

        "At least one release" could not fail: k3s installs `traefik` and `traefik-crd` before
        kubementat runs anything. What the product promised is in the helmfile it applied, so the
        expected set is read from that file in the guest copy, not written down a second time.

        What it cannot say yet is *declared and applicable*. The routing group is now
        applied by `kmt routing install`, which declines when the cluster already runs a Traefik it
        does not own -- so a helmfile that declares `traefik` in `group: routing` makes this row
        demand a release the product deliberately did not install. The rule belongs to the routing
        lane's own asserts, not here, and it is the one change between this file and a green full
        lane: subtract a declared release when the cluster already runs that chart in a namespace
        the configuration does not name, decided from the same `helm list` read, and say in the
        detail what was subtracted. Note too that `status` is keyed by release *name* below, so two
        same-named releases collapse into whichever row comes last -- the same identity gap the
        residue rows had, still open on this side.
        """
        releases = self.cluster.helm_list()
        self.state["helm_releases"] = releases
        expected = self._expected_releases(groups)
        status = {release["release"]: release["status"] for release in releases}
        missing = [name for name in expected if name not in status]
        not_deployed = ["%s=%s" % (name, status[name]) for name in expected
                        if name in status and status[name] != "deployed"]
        self.expect("helm-releases-present", bool(expected) and not missing and not not_deployed,
                    "group(s) %s declare %s; missing=%s, not deployed=%s%s"
                    % (groups, expected or "nothing (helmfile unreadable?)", missing or "none",
                       not_deployed or "none",
                       "" if expected else "; an empty expectation makes this assert vacuous, so a green "
                                          "row here is not evidence that anything was installed"))

    def _expected_releases(self, groups):
        # The manifest the product applies, not a copy that used to be written into the configuration
        # tree. `kmt initialize` carried that copy -- `kubementat_components/helmfile.yaml.gotmpl` -- and
        # that copy was retired, so this read had been asking a path that exists in no tree for the *expected*
        # set of releases. An empty expectation makes "everything declared is present" true of nothing,
        # so the row was vacuous whenever it passed and reported "declare nothing" whenever the release
        # list it compared against came from somewhere else. The manifest lives in the package now, which
        # is the one place both the apply and this assert can point at without disagreeing.
        path = posixpath.join(config.GUEST_TREE, "src", "kubementat", "core", "assets", "values",
                              "helmfile.yaml.gotmpl")
        helmfile = self.guest.run(command="cat %s" % shlex.quote(path), timeout=30)
        releases = _releases_in_groups(helmfile.stdout, groups)
        if not releases:
            # An empty answer here is the same vacuity as the old path, just moved to a nicer file. The
            # detail is carried on the assert that consumes this, because `record_result` is a result
            # object and not a place to file a note: anything else asks the reporter to call `.ok` on a
            # dict, which is how a warning about a vacuous assert becomes an AttributeError in stage 04.
            return releases
        return releases

    def _expect_group_workloads(self, groups):
        """For the groups the configuration names, its pods must actually run.

        `helm-releases-present` cannot say this and the suite has never asked it. A release becomes
        `deployed` when helm has handed its manifests to the API, so the record answers even when the
        workload they describe never starts -- a StatefulSet whose pods sit in CrashLoopBackOff
        reports `deployed` and passes. The distinction is the whole reason `extra` is in this lane:
        nothing had ever installed that group, so every claim about it was made from a chart render on
        a host, and a chart render cannot fail to start something it never started.

        Two rules in here that are not decoration:

        * The namespace comes from the cluster's own `helm list` rows and not from
          `constants.DEFAULT_NAMESPACES`. A rename that moved the namespace would otherwise keep
          passing by reading the constant it was supposed to update.
        * A group that declares releases but yields no rows, or yields pods and none of them run, is
          asserted rather than skipped. An empty group is indistinguishable from a typo in a `group:`
          label, and both read as success to a lane that only looks for what is missing.
        """
        for group in [g for g in groups if g in config.WORKLOAD_ASSERTED_GROUPS]:
            declared = self._expected_releases([group])
            namespaces = sorted({row["namespace"] for row in self.state["helm_releases"]
                                 if row["release"] in declared})
            if not namespaces:
                self.expect("workloads-running:%s" % group, False,
                            "group=%s declares %s but no release row names a namespace -- either the "
                            "apply did not install them or they landed somewhere this lane cannot see"
                            % (group, declared or "nothing"))
                continue

            def running(watched=namespaces, group=group):
                seen = []
                for namespace in watched:
                    pods = self.cluster.pod_list(namespace=namespace)
                    if not pods:
                        return False, "group=%s namespace %s has no pods yet" % (group, namespace)
                    for pod in pods:
                        name = (pod.get("metadata") or {}).get("name")
                        phase = (pod.get("status") or {}).get("phase")
                        if phase != "Running":
                            return False, "group=%s pod %s phase=%s in %s" % (group, name, phase, namespace)
                        stuck = [c.get("name") for c in (pod.get("status") or {}).get("containerStatuses") or []
                                 if not c.get("ready")]
                        if stuck:
                            return False, "group=%s pod %s Running but waiting on %s" % (group, name, stuck)
                        seen.append("%s/%s" % (namespace, name))
                return True, "group=%s running and ready: %s" % (group, seen)

            passed = self.wait("workloads-running:%s" % group,
                      "workloads of group=%s are Running with every container ready" % group,
                      running, timeout=WORKLOAD_TIMEOUT_S)
            if not passed:
                # A red here is about a container that will not answer a probe, and the phase alone
                # cannot say why. The evidence is three reads: what the pod thinks it is doing, what
                # it printed, and what its last restart printed before dying. Without these the
                # assert says "not ready" forever and every reader has to go and recreate the run.
                for namespace in namespaces:
                    described = self.cluster.kubectl("describe", "pods", "-n", namespace, timeout=60)
                    self.artifacts.log(self.stage, "workloads-%s-%s.describe" % (group, namespace),
                                       described.stdout or described.stderr or "")
                    for pod in self.cluster.pod_list(namespace=namespace):
                        name = (pod.get("metadata") or {}).get("name")
                        for suffix, args in (("logs", []), ("logs.previous", ["--previous"])):
                            logged = self.cluster.kubectl("logs", name, "-n", namespace, *args, timeout=60)
                            if logged.stdout or logged.stderr:
                                self.artifacts.log(self.stage,
                                                   "workloads-%s-%s.%s" % (group, name, suffix),
                                                   logged.stdout or logged.stderr or "")

    def _expect_pvcs(self):
        status = self.cluster.pvc_status()
        self.state["pvcs_after_install"] = status
        detail = "bound=%s waiting-for-consumer=%s pending=%s" % (
            len(status["bound"]), len(status["waiting_for_consumer"]), len(status["pending"]))
        if status["pending"]:
            detail += " | " + json.dumps(status["pending"][:5])
        self.expect("no-claims-left-pending", not status["pending"], detail)

    def _expect_single_ready_node(self):
        count, nodes = self.cluster.node_count_ready()
        self.expect("single-node-ready", count == 1, "Ready nodes: %s" % count)
        if nodes:
            self.state["node_allocatable"] = (nodes[0].get("status") or {}).get("allocatable", {})

    def _seed_team_encrypted_answers(self):
        """Declare, one layer deeper, the two answers the encrypted team layer used to inherit from a
        template -- because a command that writes nothing when nothing is declared is not tested by a
        lane that declares none.

        Two rows were green for the wrong reason on every lane. `setup-secrets` answers "no layer
        declares SSH_DEPLOY_KEYS for this team, so no Secret was written" and exits zero -- correct,
        since every field of an entry is the operator's key material and kubementat invents none -- and
        `setup_docker_registry_access` answers "no DOCKER_REGISTRY_CREDENTIALS answers this team, so no
        pull secret is created" and exits zero, also correct. Both rows after them read an empty file,
        got an empty list, and had no assertion left to make. What actually made them pass while
        `kmt initialize` was still copying a template into the tree was the placeholder credentials and
        key the template shipped: the lane was measuring inert garbage rather than measuring nothing,
        and once the template stopped being copied it measured nothing while still reporting a pass.

        Why the team layer and not the environment's: both answers belong to the team that runs
        pipelines in it. `SSH_DEPLOY_KEYS` feeds the Secret that key becomes, and
        `DOCKER_REGISTRY_CREDENTIALS` is scoped to the namespaces of the team that pulls with it. The
        cross-team probe in stage 05 gives a *foreign* team a deploy key of its own for the opposite
        claim, and that fixture stays separate on purpose -- the whole point of that row is that the
        two teams' answers do not mix.

        The write is one run of `config set --team --encrypted --from-file`, which is the shape a
        document is for: both answers land together, the private key and the generated password never
        appear in an argv where every process on the machine could read them, and no layer file is
        written by hand. Not a template edit, because `config init` copies none since
        `kmt initialize` went.

        The registry answer is one credential aimed at a host that cannot resolve, for a reason that
        outlives this fixture: a pull Secret whose auth URL is a real registry would be tried before
        an anonymous pull, and a lane that cannot pull its own images is a lane that fails somewhere
        far away from here. `example.invalid` is the reserved TLD every other dummy identity in this
        harness uses, so the Secret is real, the attachment is real, and nothing could ever be pulled
        with it.

        No assert of its own: the two rows that depend on this one already name a missing declaration
        as the reason they prove nothing, and a third assert saying the same thing is one more thing
        for a later edit to contradict.
        """
        pipeline_namespace = self._team_config_value("PIPELINE_NAMESPACE")
        app_namespace = self._team_config_value("APP_DEPLOYMENT_NAMESPACE")
        if not pipeline_namespace or not app_namespace:
            self.log("the team answers no namespaces (%s=%r, %s=%r), so neither encrypted answer can be "
                     "aimed at anything" % ("PIPELINE_NAMESPACE", pipeline_namespace,
                                            "APP_DEPLOYMENT_NAMESPACE", app_namespace))
            return
        key_name, key_secret = "kmt-e2e-deploy-key", "kmt-e2e-deploy-ssh"
        registry_name = "kmt-e2e-registry-secret"
        document = posixpath.join(GUEST_STAGING_DIR, "kmt-e2e-team-answers.json")
        staged = self.guest.write_file(document, json.dumps({
            "SSH_DEPLOY_KEYS": [{
                "NAME": key_name,
                "PRIVATE_KEY_BASE64": base64.b64encode(b"not a key, only a round-trip subject").decode(),
                "PUBLIC_KEY": "",
                "TARGET_NAMESPACE": pipeline_namespace,
                "TARGET_SECRET_NAME": key_secret,
            }],
            "DOCKER_REGISTRY_CREDENTIALS": [{
                "NAME": registry_name,
                "DOCKER_REGISTRY_AUTH_URL": "https://registry.%s" % config.BASE_DOMAIN,
                "DOCKER_REGISTRY_EMAIL": "kmt-e2e-registry@" + config.BASE_DOMAIN,
                "DOCKER_REGISTRY_USERNAME": "kmt-e2e-lane",
                # Generated per run, for the same reason the grafana row's password is: it is not
                # anywhere in this repository, and a rerun that wants one generates its own.
                "DOCKER_REGISTRY_PASSWORD": secrets.token_urlsafe(18),
            }],
        }), timeout=30)
        landed = staged.ok and self.km("config.set.team.encrypted.document", team=config.TEAM,
                                       answers_document=document, timeout=120).ok
        self.state.setdefault("config_adjustments", []).append({
            "file": self._config_report(config.ENVIRONMENT, config.TEAM, "static.encrypted.json"),
            "field": "SSH_DEPLOY_KEYS (one entry) and DOCKER_REGISTRY_CREDENTIALS (one credential), "
                     "seeded for this run",
            "value": "%s -> %s/%s; %s -> https://registry.%s" % (key_name, pipeline_namespace,
                                                                 key_secret, registry_name,
                                                                 config.BASE_DOMAIN)})
        self.log("team encrypted answers seeded=%s through one run of config set --team --encrypted "
                 "--from-file (staged ok=%s, set ok=%s)" % (landed, staged.ok, landed))

    def _expect_secret_round_trip(self):
        """Each SSH deploy key the team config declares is the Secret `setup-secrets` wrote.

        The old row passed when *any* Secret with data existed in the namespace named after the
        team, which is not where `setup-secrets` writes: every key names its own
        `TARGET_NAMESPACE`. So the round trip is per declared key - right place, right type, and
        the bytes the config holds. Values never reach the detail, only whether they match.
        """
        declared = self._team_config_json("SSH_DEPLOY_KEYS", "static.encrypted.json") or []
        declared = [entry for entry in declared if isinstance(entry, dict)]
        if not declared:
            self.expect("secret-round-trip", False,
                        "platform_config/%s/%s/static.encrypted.json declares no SSH_DEPLOY_KEYS, so "
                        "setup-secrets had nothing to write and this row would prove nothing"
                        % (config.ENVIRONMENT, config.TEAM))
            return
        verdicts = []
        for entry in declared:
            where = "%s/%s" % (entry.get("TARGET_NAMESPACE"), entry.get("TARGET_SECRET_NAME"))
            found = self.cluster.kubectl("get", "secret", str(entry.get("TARGET_SECRET_NAME")), "-n",
                                         str(entry.get("TARGET_NAMESPACE")), "-o", "json", timeout=60)
            if not found.ok:
                verdicts.append((where, False, "missing"))
                continue
            secret = json.loads(found.stdout)
            stored = (secret.get("data") or {}).get("ssh-privatekey", "")
            if secret.get("type") != "kubernetes.io/ssh-auth":
                verdicts.append((where, False, "type=%s" % secret.get("type")))
            elif stored != entry.get("PRIVATE_KEY_BASE64"):
                verdicts.append((where, False, "ssh-privatekey differs from the config (%s vs %s bytes)"
                                 % (len(stored), len(entry.get("PRIVATE_KEY_BASE64") or ""))))
            else:
                verdicts.append((where, True, "matches"))
        self.state["secret_round_trip"] = [where for where, _ok, _why in verdicts]
        self.expect("secret-round-trip", all(ok for _where, ok, _why in verdicts),
                    "; ".join("%s: %s" % (where, why) for where, _ok, why in verdicts))

    def _expect_pull_secret_attached(self):
        service_accounts = self.cluster.kubectl("get", "sa", "-n", config.TEAM, "-o", "json", timeout=60)
        if not service_accounts.ok:
            self.expect("imagepullsecret-attached", False, "no service account readable: %s" % service_accounts.excerpt(200))
            return
        attached = []
        for item in json.loads(service_accounts.stdout).get("items", []):
            attached += [entry.get("name") for entry in item.get("imagePullSecrets") or []]
        self.expect("imagepullsecret-attached", bool(attached),
                    "imagePullSecrets attached to %s service accounts: %s" % (config.TEAM, attached or "none"))

    # ----------------------------------------------------------------- 03-run

    def stage_run(self):
        result = self.km("tekton.run-pipeline", timeout=300)
        self.record_result("cap:tekton.run-pipeline", "product", result)
        if not result.ok:
            raise StageFailed("the pipeline run never started: %s" % result.excerpt(300))
        namespace = "%s-pipelines" % config.TEAM
        # Poll well inside the stage budget so the verdict lands on an assertion with
        # its diagnostics rather than on the stage-level timeout (which loses detail).
        passed, detail, attempts = self.cluster.wait(
            "the hello-world PipelineRun reaches Succeeded",
            lambda: self._pipelinerun_succeeded(namespace, PIPELINE_RUN),
            timeout=min(150, self.lane.timeouts["03-run"] - 60),
            interval=5,
        )
        dump = self._dump_pipelinerun(namespace, self._latest_pipelinerun(namespace, PIPELINE_RUN) or PIPELINE_RUN)
        self.record("assert:pipelinerun-succeeded", "product", passed, detail, attempts=attempts, artifacts=dump)
        if not passed:
            raise StageFailed("PipelineRun did not succeed: %s" % detail)

    def _pipelinerun_succeeded(self, namespace, name):
        """A bounded poll of the condition itself; no log scraping, no fixed sleep.

        The run is looked up by prefix because the product generates a suffixed name
        for each execution, so `name` is what the run is called *by*.
        """
        current = self._latest_pipelinerun(namespace, name)
        if not current:
            return False, "no PipelineRun named like %s in %s yet" % (name, namespace)
        result = self.cluster.kubectl(
            "get", "pipelinerun", current, "-n", namespace,
            "-o", "jsonpath={.status.conditions[0].status}|{.status.conditions[0].reason}",
            timeout=30)
        raw = result.stdout.strip()
        if "|" not in raw:
            return False, "no condition yet on %s (%s)" % (current, raw or result.excerpt(120))
        status, reason = raw.split("|", 1)
        if reason == "Succeeded" and status == "True":
            return True, "%s Succeeded" % current
        if status == "False":
            return False, "%s terminal failure: reason=%s" % (current, reason)
        return False, "%s in progress: reason=%s" % (current, reason)

    def _latest_pipelinerun(self, namespace, prefix):
        names = self.cluster.kubectl(
            "get", "pipelinerun", "-n", namespace,
            "-o", "jsonpath={.items[*].metadata.name}", timeout=30).stdout.split()
        matching = sorted(name for name in names if name.startswith(prefix))
        return matching[-1] if matching else ""

    def _dump_pipelinerun(self, namespace, name):
        dump = self.cluster.kubectl("get", "pipelinerun", name, "-n", namespace, "-o", "yaml", timeout=60)
        return [self.artifacts.log(self.stage, "pipelinerun-%s.yaml" % name, dump.stdout)]

    # --------------------------------------------------------------- 04-rerun

    def stage_rerun(self):
        """The D4 detector: everything twice in one cluster state, same resource set."""
        if self.cluster is None:
            raise StageFailed("no cluster")
        excluded = config.IDEMPOTENCY_EXCLUDED_NAMESPACES
        for capability, kw in self._rerun_capabilities():
            # The suffix is what tells two applies of *different groups by the same capability*
            # apart. A capability whose argv takes no `{group}` placeholder -- routing.install
            # applies a group by name rather than by selector -- has nothing to disambiguate, so
            # its id is the bare capability, because there is nothing else to disambiguate it with:
            # `idempotent:routing.install`.
            label = capability + (":%s" % kw["group"]
                                  if kw.get("group") and "{group}" in config.CAPABILITIES[capability].argv
                                  else "")
            before = asserts.resource_sets(self.cluster, config.IDEMPOTENCY_KINDS, excluded)
            before_content = asserts.content_digests(self.cluster, config.IDEMPOTENCY_CONTENT_KINDS, excluded)
            passed = self.run_capability(capability, timeout=900, detail="second pass ", **kw)
            after = asserts.resource_sets(self.cluster, config.IDEMPOTENCY_KINDS, excluded)
            after_content = asserts.content_digests(self.cluster, config.IDEMPOTENCY_CONTENT_KINDS, excluded)
            delta = asserts.diff_sets(before, after)
            # Same names are not the same cluster: a rerun that regenerates a password keeps every
            # Secret's name and hands out a new credential. The content is what an operator relies on.
            changed = asserts.changed_content(before_content, after_content)
            self.expect("idempotent:%s" % label, passed and not delta and not changed,
                        "exit=%s, resource-set delta=%s, content changed=%s"
                        % ("0" if passed else "non-zero",
                           json.dumps(delta)[:300] if delta else "none",
                           json.dumps(changed)[:300] if changed else "none"))

    def _rerun_capabilities(self):
        """(capability, placeholders) pairs, each executed a second time over the installed cluster.

        `config.init` is deliberately absent: a rerun would be a nine-step preparation run
        twice against a tree that is already prepared, which is a different test and not an
        idempotency measurement. `uninstall` is teardown, not
        a mutation to repeat. `install` re-applies the first group only, so every later group is
        re-applied the way stage 02 applied it -- which is exactly why the later groups come from
        `config.apply_capability_for_group` and not from the apply capability by default. Spelling
        them all as `helm.helmfile-apply` here would make stage 04 the half of the lane that
        bypasses the routing guard: stage 02 would skip a Traefik that is already there, and this
        loop would install a second controller while the idempotency line compared a change the
        lane had just said would not happen.
        """
        later_groups = [(config.apply_capability_for_group(group), {"group": group})
                        for group in self.lane.install_groups[1:]]
        return ([("install", {})] + later_groups
                + [(capability, {}) for capability in ("tekton.setup-pipelines", "tekton.setup-triggers",
                                                       "registry.access", "secrets.setup")])

    # ------------------------------------------------------------- 05-features

    def stage_features(self):
        for feature in self.lane.features:
            handler = getattr(self, "feature_" + feature, None)
            if handler is None:
                self.state.setdefault("skipped", []).append({"item": feature, "reason": "no implementation"})
                continue
            try:
                handler()
            except FeatureSkip as exc:
                self.state.setdefault("skipped", []).append({"item": feature, "reason": str(exc)})
            except Exception as exc:  # one broken feature must not lose the rest of the lane
                self.record("assert:%s" % feature, "product", False,
                            "feature stage raised %s: %s" % (type(exc).__name__, exc))

    # ------------------------------------- the cluster key, answered against a real cluster
    #
    # That one environment addresses one cluster, what the refusal for a missing key says, the two
    # shapes of a report about an environment that could not be checked, and the invariant that no two
    # environments address the same cluster: four
    # claims about `CLUSTER_KUBE_CONTEXT` that a unit test can make a fake filesystem answer and
    # cannot make a real `platform_config` answer. They are asserted here, against the k3s this VM
    # runs, in the order that keeps the fixtures from stepping on each other: the listing first
    # (nothing is in the root but `dev`), the missing key second (no fixture, one edit), the
    # collision third (the fixture that would have written), the unreadable sibling last.

    def feature_cluster_addressing(self):
        """The four answers the one-k3s rig can give, and the fifth answer it cannot.

        Each fixture is a real environment -- a directory of the configuration root that declares its
        own answers -- and is created for the length of the one
        command that needs it, because an environment that claims the same cluster as `dev` is not a
        fixture a later command can be run beside -- it is a collision the check would refuse on.
        And the line that says what the rig cannot prove is produced here rather than in a report, so
        that `result.json` carries the limitation instead of a reader having to remember it.
        """
        self._expect_environments_list_answers()
        self._expect_missing_cluster_key_refuses()
        self._expect_collision_refuses_and_writes_nothing()
        self._expect_unresolvable_environment_is_reported_and_skipped()
        self._expect_cluster_fixtures_are_removed()
        self.state.setdefault("not_covered", []).append(config.NOT_COVERED_TWO_CLUSTERS)
        self.log(config.NOT_COVERED_TWO_CLUSTERS)

    def _expect_environments_list_answers(self):
        """What environments exist, what cluster each addresses, and where that was claimed.

        Two asserts because they are two claims. The first is that the listing enumerates the tree the
        1:1 check enumerates -- both go through `EnvironmentRegistry`, and a listing that kept its own
        list would be a second answer to one question, which is how a report and a refusal start to
        disagree. The second is that each row says *which file* made the claim and what the check
        compared: an address with no provenance is the same address the fallback used to produce.
        """
        facts = self.state.get("rig_cluster") or {}
        result = self.km("environments.list", timeout=180)
        self.record_result("cap:environments.list", "product", result)
        self.artifacts.log(self.stage, "environments-list", result.output)
        rows = _listing_rows(result.output)
        header = next((line for line in (result.output or "").splitlines() if line.startswith("ENVIRONMENT")), "")
        columns = [name for name in ("ENVIRONMENT", "CLUSTER_ADDRESS", "CONTEXT", "SOURCE", "CHECK")
                   if name in header]
        listed = self.guest.run(command="ls -d %s/platform_config/*/static.json 2>/dev/null"
                                " | sed 's|.*/platform_config/||; s|/static.json||'"
                                % shlex.quote(config.GUEST_TREE), timeout=30)
        on_disk = sorted(line.strip() for line in (listed.stdout or "").splitlines() if line.strip())
        dev = rows.get(config.ENVIRONMENT, [])
        answers = (result.ok and columns and sorted(rows) == on_disk
                   and len(dev) >= 2 and dev[1] == facts.get("address") and dev[2] == facts.get("context"))
        self.expect("environments-list-answers-what-environments-and-what-cluster", bool(answers),
                    "the listing sees %s and platform_config has a static.json in %s; the %s row is "
                    "%s and the rig's cluster is %s at %s via %s%s"
                    % (sorted(rows), on_disk, config.ENVIRONMENT, dev or "(no row)", config.ENVIRONMENT,
                       facts.get("address", "(unknown)"), facts.get("context", "(unknown)"),
                       "" if len(columns) == 5 else " | columns seen: %s" % columns))
        self.expect("environments-list-says-where-the-claim-came-from-and-what-was-checked",
                    len(dev) >= 5 and "platform_config/%s/static.json" % config.ENVIRONMENT in dev[3]
                    and dev[4] == "ok",
                    "%s's row reads SOURCE=%r, CHECK=%r: the source is the environment's own static.json, "
                    "the file an operator edits "
                    "and the check says what the address was compared against (an address with no "
                    "provenance is the fallback wearing a different hat)"
                    % (config.ENVIRONMENT, dev[3] if len(dev) > 3 else "(absent)",
                       dev[4] if len(dev) > 4 else "(absent)"))

    def _expect_missing_cluster_key_refuses(self):
        """With the key gone the error *is* the interface, and it names everything editable.

        The environment, the file with a path, the key, and the state found there -- the four parts
        a refusal must name, asserted as parts rather than as a non-zero exit, because a refusal an operator
        cannot act on is a stack trace with an exit code attached. The command used is a read
        (`tekton list`): the point is that the failure closes the command before it touches anything,
        which a read proves just as well and a write would leave ambiguous.

        How the key gets *away* is the part that changed. This stage used to `jq` the value to
        `null`, which is a state `config init` writes and no operator edits a tree into, and it is not
        the state `kmt config unset` leaves behind either -- which deletes the key and lets the layer
        above answer. So the fixture now takes the key out with the same verb a human would, and the
        refusal is measured against the file that verb actually produces: *is not set*, and not the
        *is null* that a mutation no product verb writes used to fake.
        """
        context = (self.state.get("rig_cluster") or {}).get("context", "")
        if not self._config_unset(config.CLUSTER_KUBE_CONTEXT_KEY):
            self.expect("undeclared-cluster-refuses-and-names-the-file", False,
                        "kmt config unset could not take %s out of the environment layer, so there is "
                        "no missing key to refuse" % config.CLUSTER_KUBE_CONTEXT_KEY)
            return
        try:
            result = self.km("tekton.list", timeout=180)
            self.artifacts.log(self.stage, "cluster-key-missing", result.output)
            out = result.output or ""
            self.expect("undeclared-cluster-refuses-and-names-the-file",
                        (not result.ok) and ("'%s'" % config.ENVIRONMENT) in out and "static.json" in out
                        and config.CLUSTER_KUBE_CONTEXT_KEY in out and "is not set" in out
                        and "Traceback" not in out,
                        "exit=%s names-environment=%s names-file=%s names-key=%s names-the-state=%s "
                        "no-traceback=%s | %s"
                        % (result.exit_code, ("'%s'" % config.ENVIRONMENT) in out, "static.json" in out,
                           config.CLUSTER_KUBE_CONTEXT_KEY in out, "is not set" in out,
                           "Traceback" not in out, result.excerpt(240)))
        finally:
            # The lane leaves the tree as it found it, and says so: a mutation whose restoration is
            # not checked is how a lane goes red forty stages later over a key nobody edited. The
            # read-back is `config show`, because the assert is about the answer the product will
            # give and not about what a byte-comparison says a file used to hold.
            restored = self._config_set(config.CLUSTER_KUBE_CONTEXT_KEY, context)
            shown, round_trip, _provenance = self._config_shown(config.CLUSTER_KUBE_CONTEXT_KEY)
            self.expect("cluster-key-restored-after-the-refusal",
                        restored and shown and round_trip == context,
                        "environment %s declares %s=%r again after the refusal (kmt config show "
                        "agreed=%s, the lane had written %r)"
                        % (config.ENVIRONMENT, config.CLUSTER_KUBE_CONTEXT_KEY, round_trip, explained,
                           context))

    def _expect_collision_refuses_and_writes_nothing(self):
        """The collision half of the address check: two environments, one k3s address, a block, no writes.

        The invariant is *no two environments address the same cluster*, and it is answerable on one
        cluster because both sides of the check are address claims read from local kubeconfigs,
        not two machines. So the fixture is a second environment whose static.json names a second
        context reaching the **same** server -- which is what makes this the collision rather than a
        name comparison -- and its config is a copy of `dev`'s with one namespace changed.

        That copy is the assert's whole value. A fixture the command would have failed anyway would
        make "writes nothing" vacuous, so this one is a valid install that would have written: the
        only thing standing between it and a second namespace on the cluster is the check. The
        namespace it would have created is read out of the copied file rather than written down here,
        and the negative fact after the refusal is that namespace plus an unchanged release set.
        """
        facts = self.state.get("rig_cluster") or {}
        environment = config.COLLISION_ENVIRONMENT
        self._remove_environment(environment)
        # The fixture is built by the verbs that build environments, and not by copying one.
        # `cp -a` of `dev`'s directory was the old shape, and it is a writer of configuration --
        # A stage that "just" copies a JSON file is a defect and not a shortcut, and the lane's whole
        # shape is that it configures as a consumer of `kmt
        # config`. What a copy bought was "this environment has dev's answers", which is not what the
        # check needs: the only thing that makes this a collision is a second *address*, and the only
        # thing that makes "wrote nothing" mean something is a fixture that would have written.
        created = self.km("config.add-environment", env=environment, timeout=120)
        if not created.ok:
            self.expect("two-environments-one-cluster-are-refused", False,
                        "kmt config add-environment could not create %s (%s), so there is nothing to "
                        "collide" % (environment, created.excerpt(160)))
            return
        named = self._config_set(config.CLUSTER_KUBE_CONTEXT_KEY, config.COLLISION_CONTEXT,
                                 environment=environment)
        named = self._config_set(config.CLUSTER_KUBECONFIG_PATH_KEY, config.COLLISION_KUBECONFIG,
                                 environment=environment) and named
        # The one namespace the refused command is about to fail to create. Deliberately the same
        # string as the environment's name, so a reader has one name to remember rather than two.
        named = self._config_set("GRAFANA_DEPLOYMENT_NAMESPACE", environment,
                                 environment=environment) and named
        would_create = self._config_shown("GRAFANA_DEPLOYMENT_NAMESPACE",
                                           environment=environment)[1]
        if not named:
            self.expect("two-environments-one-cluster-are-refused", False,
                        "the new environment exists but its answers did not all land, so the fixture's "
                        "claim is a directory and not a collision: %s" % environment)
            self._remove_environment(environment)
            return
        made, failure = self._build_collision_kubeconfig(facts)
        if not made:
            self.expect("two-environments-one-cluster-are-refused", False,
                        "the rig could not build the %r context that reaches %s, so the fixture's claim "
                        "would have been a name and not an address: %s"
                        % (config.COLLISION_CONTEXT, facts.get("address"), failure))
            self._remove_environment(environment)
            return
        try:
            before, unreadable = self._helm_release_ids()
            namespaces_before = self.cluster.namespaces()
            result = self.km("install", env=environment, group=self.lane.install_groups[0], timeout=300)
            self.artifacts.log(self.stage, "collision-install", result.output)
            out = result.output or ""
            address = facts.get("address", "")
            self.expect("two-environments-one-cluster-are-refused",
                        (not result.ok) and environment in out
                        and ("'%s'" % config.ENVIRONMENT) in out,
                        "install for %s exited %s and %s: the refusal has to say that %s and %s claim "
                        "one cluster, because a check that refused silently is indistinguishable from "
                        "one that never ran | %s"
                        % (environment, result.exit_code,
                           "names both environments" if (environment in out and ("'%s'" % config.ENVIRONMENT) in out) else "names only one",
                           environment, config.ENVIRONMENT, result.excerpt(240)))
            self.expect("refusal-gives-the-address-and-the-command-that-shows-it",
                        bool(address) and address in out and "kmt environments list" in out,
                        "The refusal has to name both addresses and the command that shows "
                        "the whole picture: address present=%s, points at `kmt environments list`=%s"
                        % (address in out, "kmt environments list" in out))
            after, unreadable_after = self._helm_release_ids()
            namespaces_after = self.cluster.namespaces()
            appeared = sorted(set(namespaces_after) - set(namespaces_before))
            self.expect("refused-collision-install-wrote-nothing",
                        bool(would_create) and not unreadable and not unreadable_after and after == before
                        and would_create not in namespaces_after and not appeared,
                        "nothing exists that the refusal did not put there: helm release set %s, the "
                        "namespace %s would have created exists=%s, namespaces that appeared=%s%s"
                        % ("unchanged (%s releases)" % len(after) if after == before else "CHANGED",
                           would_create or "(the fixture declared no namespace to read)",
                           bool(would_create) and would_create in namespaces_after, appeared or "none",
                           " | helm would not say: %s" % (unreadable or unreadable_after) if (unreadable or unreadable_after) else ""))
        finally:
            self._remove_environment(environment)

    def _expect_unresolvable_environment_is_reported_and_skipped(self):
        """The other report shape: a sibling that cannot be read is reported, not fatal, and not silent.

        The command under test addresses `dev`; a third environment points at a context no kubeconfig
        has. A check that blocked on that would make `tekton list dev` depend on a stranger's config,
        and a check that dropped it would be reporting a comparison that never happened -- so the row
        is asserted twice, once for the line that says *not verified* with the reason, once for the
        command that still ran.
        """
        # Built the way a human would build it: the verb creates the environment, the verb names the
        # key. What the row is about is a sibling that declares a context no kubeconfig has, so the
        # smallest declaration that is still a declaration is one key and no kubeconfig path --
        # Resolving the context is the library's own rule, and the address was never the point.
        self._remove_environment(config.UNRESOLVABLE_ENVIRONMENT)
        created = self.km("config.add-environment", env=config.UNRESOLVABLE_ENVIRONMENT, timeout=120)
        if not created.ok:
            self.expect("unresolvable-environment-is-reported-and-skipped", False,
                        "kmt config add-environment could not create %s (%s), so the check had nothing "
                        "to report about it"
                        % (config.UNRESOLVABLE_ENVIRONMENT, created.excerpt(160)))
            return
        declared = self._config_set(config.CLUSTER_KUBE_CONTEXT_KEY, config.UNRESOLVABLE_CONTEXT,
                                    environment=config.UNRESOLVABLE_ENVIRONMENT)
        if not declared:
            self.expect("unresolvable-environment-is-reported-and-skipped", False,
                        "the environment exists but kmt config set would not name %s in it, so there is "
                        "no unreadable sibling to report"
                        % config.UNRESOLVABLE_CONTEXT)
            self._remove_environment(config.UNRESOLVABLE_ENVIRONMENT)
            return
        try:
            result = self.km("tekton.list", timeout=300)
            line = self._resolution_report_line(result.output, config.ENVIRONMENT)
            self.expect("unresolvable-environment-is-reported-and-skipped",
                        "not verified" in line and config.UNRESOLVABLE_ENVIRONMENT in line
                        and config.UNRESOLVABLE_CONTEXT in line,
                        "the report line is %r: it must name the sibling it could not resolve, say so "
                        "in the *not verified: <reason>* shape, and not claim a comparison it held back "
                        "from" % (line or "(no cluster line printed)"))
            self.expect("unreadable-sibling-did-not-stop-the-command",
                        result.ok and "hello-world" in (result.output or "").lower(),
                        "exit=%s and the listing still says hello-world=%s: the check is report-and-skip, "
                        "which is only measurable if the command actually ran | %s"
                        % (result.exit_code, "hello-world" in (result.output or "").lower(),
                           result.excerpt(200)))
        finally:
            self._remove_environment(config.UNRESOLVABLE_ENVIRONMENT)

    def _expect_cluster_fixtures_are_removed(self):
        """The fixtures are gone from the root, read from the same command that would have listed them.

        A fixture left behind is not tidiness: it is an environment that claims this cluster, so every
        later command on `dev` would refuse because of it. Trusting the `rm` would be the lane
    describing its own state rather than measuring it, so the listing is the evidence.
        """
        result = self.km("environments.list", timeout=180)
        rows = _listing_rows(result.output)
        left = sorted(name for name in (config.COLLISION_ENVIRONMENT, config.UNRESOLVABLE_ENVIRONMENT)
                      if name in rows)
        self.expect("cluster-fixtures-are-removed", result.ok and not left,
                    "the environments this feature needed are out of platform_config: %s; the listing "
                    "now names %s" % ("both removed" if not left else "LEFT BEHIND: %s" % left,
                                      sorted(rows)))

    def _build_collision_kubeconfig(self, facts):
        """A kubeconfig of the rig's own, holding a second context that reaches the same server.

        `kubectl config set-context` into a **copy** of the resolved file: it writes a mapping and asks
        a cluster nothing, and because the new context names the k3s install's own cluster entry its
        address is equal by construction rather than asserted. Copying is the point: had the fixture
        reused the one existing context, this row would have tested only that two strings compared
        equal, and the identity that decides is the server.

        Editing `~/.kube/config` itself would be the lane writing on the machine, and that is not one
        of this lane's claims to make. `kubectl config add-context` is not used because it is not a
        command this cluster's kubectl has -- a fixture that quietly skipped the step would be an
        address claim the check never saw, so the error is returned to the assert rather than logged.
        """
        if not facts.get("kubeconfig") or not facts.get("cluster") or not facts.get("user"):
            return False, "the rig never recorded which cluster and user its own context names"
        written = self.guest.run(
            command="cp %s %s && kubectl --kubeconfig %s config set-context %s --cluster=%s --user=%s"
                    % (shlex.quote(facts["kubeconfig"]), shlex.quote(config.COLLISION_KUBECONFIG),
                       shlex.quote(config.COLLISION_KUBECONFIG), shlex.quote(config.COLLISION_CONTEXT),
                       shlex.quote(facts["cluster"]), shlex.quote(facts["user"])),
            timeout=60)
        return written.ok, "" if written.ok else written.excerpt(200)

    def _remove_environment(self, environment):
        """Delete one rig fixture environment, and the kubeconfig copy it may have used.

        Called on the way in and on the way out: a fixture that outlives its assert is a live claim on
        the cluster `dev` already addresses, and the lane would spend the rest of its run refusing
        work because of a directory nothing asked for.
        """
        self.guest.run(command="rm -rf %s %s"
                       % (shlex.quote(self._config_path(environment)),
                          shlex.quote(config.COLLISION_KUBECONFIG)), timeout=60)

    def feature_tekton_list(self):
        result = self.km("tekton.list", timeout=180)
        self.record_result("cap:tekton.list", "product", result)
        # The lister logs through the logger, i.e. on stderr, so stdout alone lies.
        listed = "hello-world" in (result.output or "").lower()
        clean = "Traceback" not in result.output
        self.expect("tekton-list-lists-hello-world", listed and clean,
                    "hello-world visible=%s, output clean=%s" % (listed, clean))

    def feature_tunnels(self):
        for capability in ("tunnel.grafana", "tunnel.tekton"):
            self._tunnel_body_signature(capability)

    def _tunnel_body_signature(self, capability, detail="", expect_signature=True, deadline_s=90,
                               attempt="initial"):
        """The body signature decides, not an HTTP 200 from the wrong pod.

        Four things have to be true for this to measure anything. The body has to be read *through*
        the redirect: grafana answers `/` with a 302 and the body of a 302 is `<a href="/login">Found</a>.`,
        so a probe without `-L` compares a redirect against a table of page tokens and can only ever
        miss. The failure has to arrive as *text*: `-s` alone makes curl swallow its own error, so a
        connection refused here leaves exit 7 and an empty stderr, and that is what `body=empty` had
        been reading as - the assert could not tell "never bound" from "bound and answered nothing",
        which is the only decision the row exists to make. `-S` says the difference out loud.
        The table has to name tokens the served document actually *contains*, a claim about that
        document rather than about the product: `api/login` and `signin` are words a login form
        renders, not bytes in the HTML Grafana serves, and `PipelineRun` is a component name in the
        tekton dashboard's JS bundle, so judging it against `index.html` judged a page for a fact it
        never carried. And a live tunnel has to look live to the loop: `kill -0` answers for a
        process that is *there*, so the last-resort break has to read that as alive. The inverted
        pair was the whole reason a healthy tunnel's verdict came back in seconds against a
        90-second deadline.

        So the verdict states what it measured - `connection-failure`, `empty-body`, or
        `body-without-tokens` - and every probe writes an evidence file of its own, because the
        rename retry used to stand over the row a reader was judging.
        """
        probe = config.SIGNATURES[capability]
        port = config.TUNNEL_PORTS.get(capability, "3000")
        name = capability.replace(".", "-")
        bound_before_start = self.port_listeners(port)
        pid, log_path = self.km_background(capability, log_name=name, port=port)
        tokens = list(probe["tokens"])
        body, error, matched, missing = "", "", False, list(tokens)
        # `last` is what the verdict is read off: the probe that was on the wire when the loop
        # ended. `stop` says why the loop ended, which is a separate fact from what curl saw.
        last, stop_reason = None, "deadline"
        deadline = time.time() + deadline_s
        while time.time() < deadline:
            read = self.guest.run(command="curl -sSL --max-time 10 http://127.0.0.1:%s%s" % (port, probe["path"]),
                                  timeout=30)
            last, body, error = read, read.stdout, read.stderr.strip()
            matched, missing = _signature(body, tokens)
            if read.ok and matched:
                stop_reason = "matched"
                break
            # The pid can vanish under ssh long before the tunnel has bound anything,
            # so a dead pid alone is not evidence - the log is.
            log = self.guest.run(command="cat %s 2>/dev/null" % shlex.quote(log_path), timeout=20).stdout
            if re.search(r"Traceback|Unable to listen|error:|refused", log) or "KMT_E2E_EXIT=" in log:
                error = log
                stop_reason = "tunnel-log"
                break
            if pid and "dead" in self.guest.run(command="kill -0 %s 2>/dev/null && echo alive || echo dead" % pid, timeout=20).stdout:
                # Last resort: the process is gone and the log said nothing, so the
                # tunnel is not coming. Do not burn the rest of the deadline. Note the
                # polarity: success here means alive, so it must not be the break.
                stop_reason = "process-gone"
                break
            time.sleep(TUNNEL_POLL_S)
        stopped = self._stop_background(pid)
        free, listeners = self._wait_port_free(port)
        verdict = _probe_verdict(last, body, matched)
        # One name per probe, because both grafana probes record under the same assert line and the
        # file was the only thing separating them. `Artifacts.log` appends `.log`, so the name is
        # what a reader greps for, not what the writer writes out.
        evidence = "%s.body.%s" % (name, attempt)
        # When the tunnel's own log is what stopped the loop it is also what the reader has to see,
        # so it goes in the detail line and not only in the file.
        logged = " tunnel-log=%s" % error[:140].replace("\n", " ") if stop_reason == "tunnel-log" else ""
        self.artifacts.log(self.stage, evidence, body or error)
        self.expect("tunnel-signature:%s" % capability, matched,
                    "%sprobe=%s (%s) attempt=%s tokens=%s missing=%s matched=%s bound-before-start=%s "
                    "stop=%s%s verdict=%s curl=%s body=%s evidence=%s" % (
                        detail, probe["path"], probe["read"], attempt, tokens, missing, matched,
                        bound_before_start, stop_reason, logged, verdict, _curl_verdict_note(last),
                        (body[:140] or "zero bytes").replace("\n", " "), evidence))
        self.expect("tunnel-port-freed:%s" % capability, free,
                    "port %s free after stopping %s: %s" % (port, stopped, listeners or "no listener"))
        return matched

    def feature_tunnel_openbao_negative(self):
        """Nothing installs openbao *on this lane*, so the tunnel must fail fast and bind nothing.

        Lane-bound, and that is the part worth knowing before editing it. This feature is only in
        `smoke`'s feature list, and `smoke` installs no `extra`. The `full` lane does install openbao
        and deliberately does not run this feature, so the assert never fires on a cluster where the
        component is present -- where it would be wrong in the opposite direction, demanding a failure
        from a tunnel that has every reason to work.
        """
        port = config.TUNNEL_PORTS["tunnel.openbao-ui"]
        pid, log_path = self.km_background("tunnel.openbao-ui", log_name="tunnel-openbao-ui", port=port)
        tail = ""
        bound, listeners = self._listeners(port)
        deadline = time.time() + 30
        while time.time() < deadline:
            tail = self.guest.run(command="cat %s 2>/dev/null" % shlex.quote(log_path), timeout=20).stdout
            bound, listeners = self._listeners(port)
            # The wrapper reports the exit code; waiting for that marker is what makes
            # this deterministic, rather than betting on a pid that ssh may have reaped.
            if bound or "KMT_E2E_EXIT=" in tail:
                break
            time.sleep(2)
        stopped = self._stop_background(pid)
        bound, listeners = self._listeners(port)
        self.artifacts.log(self.stage, "tunnel-openbao-ui.log", tail)
        failed_closed = (not bound) and bool(tail.strip())
        self.record("assert:tunnel-negative:openbao-ui", "product", failed_closed,
                    "port bound=%s (%s, stopped by %s), log=%s" % (bound, listeners or "no listener", stopped,
                                                                   (tail.strip().replace("\n", " ")[:200] or "empty")))
        # "The component is not installed" is an ordinary state, so the product should
        # say that rather than raise. A traceback is a product finding, not a harness
        # artifact, so it gets its own line instead of breaking the line above.
        self.record("assert:tunnel-negative:openbao-ui-fails-cleanly", "product",
                    failed_closed and "Traceback" not in tail,
                    "traceback in output" if "Traceback" in tail else "clean error message")

    def feature_tunnel_pod(self):
        pod, port = self._tunnelable_pod()
        if not pod:
            raise FeatureSkip("no Running pod exposing a containerPort to tunnel to")
        namespace = pod["metadata"]["namespace"]
        name = pod["metadata"]["name"]
        pid, log_path = self.km_background("tunnel.pod", log_name="tunnel-pod", ns=namespace, pod=name, port=port)
        connected = False
        deadline = time.time() + 60
        while time.time() < deadline:
            probe = self.guest.run(command="curl -sL -o /dev/null -w '%%{http_code}' --max-time 5 http://127.0.0.1:%s/" % port, timeout=30)
            if probe.stdout.strip():
                connected = True
                break
            time.sleep(3)
        stopped = self._stop_background(pid)
        free, listeners = self._wait_port_free(port)
        self.expect("tunnel-pod-tcp-connect", connected,
                    "tunnel to %s/%s:%s answered (%s, port free afterwards: %s)" % (
                        namespace, name, port, stopped, listeners or "no listener"))

    def _tunnelable_pod(self):
        for pod in self.cluster.pod_list():
            if (pod.get("status") or {}).get("phase") != "Running":
                continue
            for container in (pod.get("spec") or {}).get("containers") or []:
                for exposed in container.get("ports") or []:
                    if exposed.get("containerPort"):
                        return pod, exposed["containerPort"]
        return None, None

    def feature_namespace_rename(self):
        """A feature-namespace rename is refused, not obeyed.

        It rewrites the environment-level `static.json`, because that is the file the product
        reads its component namespaces from. Patching the team-level file would have
        proved nothing except that the harness can edit a file nobody looks at.

        **What changed here, and why the rows are not what they were.** This feature used to assert
        that renaming `GRAFANA_DEPLOYMENT_NAMESPACE` and re-applying *moves* the release, and that
        claim is what its three red rows were: `helmfile apply` exited 1 on helm's ownership check,
        `namespace-rename-tunnel-still-correct` reported the release still living in the namespace it
        was installed into, and the probe written under `attempt=after-namespace-rename` came back
        `verdict=connection-failure`. Those rows are gone, and not because they were flaky: asserting
        "the release moved" is asserting a behaviour kubementat does not have and is not going to
        have. A deployed release's namespace is convention, not configuration -- helm stamps the
        cluster-scoped objects a release makes with `meta.helm.sh/release-namespace`, and applying
        them under another name orphans them -- so the product's answer to a rename is a refusal,
        before anything is written, in words. What is measured now is that refusal and its one
        consequence: the release is still serving in the namespace it was installed into.

        `namespace-rename-applied` stays. It was always about the harness editing the right file, and
        that is a fact this feature still has to establish before it can ask anything of the product.
        """
        original = self._config_shown("GRAFANA_DEPLOYMENT_NAMESPACE",
                                       environment=config.ENVIRONMENT)[1]
        renamed = "kmt-e2e-renamed"
        if not original:
            self.expect("namespace-rename-applied", False,
                        "kmt config show answered nothing for GRAFANA_DEPLOYMENT_NAMESPACE on the "
                        "environment layer, so there is no namespace for a rename to move away from")
            return
        changed = self._config_set("GRAFANA_DEPLOYMENT_NAMESPACE", renamed,
                                   environment=config.ENVIRONMENT)
        if not changed:
            self.expect("namespace-rename-applied", False,
                        "kmt config set would not answer GRAFANA_DEPLOYMENT_NAMESPACE=%s, so the "
                        "rename never happened and the rest of the check would measure the old "
                        "namespace" % renamed)
            return
        self.expect("namespace-rename-applied", True, "GRAFANA_DEPLOYMENT_NAMESPACE -> %s" % renamed)
        # The apply is what an operator would run to make the rename real, and it is the command
        # under test: `helmfile.yaml.gotmpl` reads the release namespace out of the file this feature
        # just edited, so `helm.helmfile-apply` is the shortest path from the key to the cluster.
        #
        # The row keeps its name and flips its meaning. Before, a green `cap:helmfile-apply:standard-
        # after-namespace-rename` meant the apply had succeeded, which is only true if the release
        # moved; a red meant either the refusal was missing or helm had said so instead of us. The
        # correct answer is now the opposite verdict -- the command must not apply -- and a reader
        # who greps result.json for this row still finds it.
        #
        # Both applies here name `standard`, the group this feature re-applies on purpose. It is
        # not in `GROUP_APPLY_CAPABILITIES`, so resolving it would return this capability anyway --
        # spelled out rather than resolved so the row stays `cap:helmfile-apply:...`, which is what
        # a reader of result.json is looking for when this feature goes red.
        applied = self.km("helm.helmfile-apply", group="standard", timeout=900)
        refused = not applied.ok
        if not refused:
            # The same whole-output evidence `record_result` collects, for the inverted sense of this
            # row: what a blocker reader has to see is the apply that went ahead.
            self.state.setdefault("blocker_evidence", []).append(
                "cap:helmfile-apply:standard-after-namespace-rename\n%s" % applied.output[-4000:])
        self.record("cap:helmfile-apply:standard-after-namespace-rename", "product", refused,
                    "group=standard exit=%s expected=refused refused=%s | %s"
                    % (applied.exit_code, refused, applied.excerpt(500)),
                    artifacts=[self.artifacts.log(self.stage, "cap:helmfile-apply:standard-after-namespace-rename.stdout",
                                                  applied.stdout),
                               self.artifacts.log(self.stage, "cap:helmfile-apply:standard-after-namespace-rename.stderr",
                                                  applied.stderr)])
        # The refusal has to be a sentence and not an exit code, because the alternative the lane
        # measured before is what the operator gets without it: helm's ownership error, about
        # `meta.helm.sh/release-namespace`, after a partial pass over the rest of the group. Both
        # namespaces have to be in what it said -- a refusal that only says "conflict" is a
        # redirection the operator still has to finish by reading the cluster.
        said = applied.output or ""
        self.expect("namespace-rename-refused-with-reason", (not applied.ok) and original in said
                    and renamed in said,
                    "exit=%s names the original namespace %r=%s and the renamed one %s; output=%s"
                    % (applied.exit_code, original, original in said, renamed,
                       (said.strip().replace("\n", " ")[:300] or "empty")))
        # And the one fact the move-asserts used to get wrong: refusing is only right if the release
        # is still where it was. Read it from the cluster rather than from the config -- the renamed
        # namespace must be empty of grafana, because the apply that would have put it there was
        # refused, and the original one must still answer.
        still_there = self._service_endpoint("grafana", namespace=original)
        put_there = self._service_endpoint("grafana", namespace=renamed)
        answered = self._http_status(still_there)
        self.expect("namespace-rename-release-stays-put", bool(still_there) and answered != "000"
                    and not put_there,
                    "original namespace %r: service=%s http=%s; renamed namespace %r: service=%s "
                    "(refused applies write nothing, so anything here is the move this decision "
                    "rules out)" % (original, still_there or "none", answered or "no answer", renamed,
                                    put_there or "none"))
        self._config_set("GRAFANA_DEPLOYMENT_NAMESPACE", original, environment=config.ENVIRONMENT)
        # Put the cluster back to what its own configuration says, so what runs after this feature
        # measures a cluster rather than the residue of this test. Recorded, because every feature
        # after this one reads a half-renamed cluster when the restore fails. Nothing moved in the
        # refused apply, so this apply is a no-op -- which is itself a fact: the only apply that ever
        # had to move a release was the one that was refused.
        restored = self.km("helm.helmfile-apply", group="standard", timeout=900)
        self.record_result("cap:helmfile-apply:standard-restoring-the-namespace", "product", restored,
                           detail_prefix="group=standard ")

    def _http_status(self, url):
        """What one URL answers, as the number curl reports -- including curl's own `000`.

        The rename feature has to tell "no service there" apart from "a service there that answers
        nothing", and `_service_endpoint` is a lookup that gives it only the first. A refused apply
        is expected to leave the original endpoint answering (grafana redirects, so `302` is a pass
        and `000` is a finding), so the status is what the row states rather than a boolean the
        reader has to trust.
        """
        if not url:
            return ""
        probe = self.guest.run(command="curl -s -o /dev/null -w '%%{http_code}' --max-time 10 %s/"
                               % shlex.quote(url), timeout=30)
        return (probe.stdout or "").strip()

    def _config_answer(self, field, value) -> str:
        """One `KEY=VALUE` the way `kmt config set` reads it.

        A bare string stays bare because a quoted string in a config file is a string with quotes in
        it; anything structured goes through JSON, which is the parser the verb itself uses
        (`config_init.parse_answer_argument`). Spelling the value twice — once here, once in an argv a
        stage assembles by hand — is how a lane starts writing JSON that the product reads as text.
        """
        if isinstance(value, str):
            return "%s=%s" % (field, value)
        return "%s=%s" % (field, json.dumps(value))

    def _config_set(self, field, value, *, environment=None, team=None, encrypted=False) -> bool:
        """Name one key's answer with the verb that answers keys, and nothing else.

        The rule this obeys: the lane is a *consumer* of `kmt config`, so an answer it wants in the tree goes
        in through `config set`, on the layer the key is read from. The four capability rows differ only
        in which layer and which file the answer lands in, which is why a stage selects between them and
        does not build an argv of its own.

        The return value is whether the verb succeeded, which is what the assert after it needs: an
        answer that did not land is a fixture that never existed, and a lane that goes on to blame the
        product for its own missing write is the worst result this file can produce.
        """
        capability = "config.set"
        if encrypted and team:
            capability = "config.set.encrypted.team"
        elif encrypted:
            capability = "config.set.encrypted"
        elif team:
            capability = "config.set.team"
        return self.km(capability, answer=self._config_answer(field, value),
                       **({"env": environment} if environment else {}),
                       **({"team": team} if team else {})).ok

    def _config_unset(self, field, *, environment=None, team=None) -> bool:
        """Take one key back out, which is the only way to reach the state kubementat leaves behind.

        This replaces a `jq '.[key]=null'`. That write produced a shape no product verb ever writes,
        so the refusal it was meant to exercise was measured against a file the product does not
        produce and would not describe correctly. `config unset` makes the layer *above* answer, which
        is what "this environment has not named its cluster" means when a human gets there.
        """
        return self.km("config.unset" if not team else "config.unset.team",
                       key=field,
                       **({"env": environment} if environment else {}),
                       **({"team": team} if team else {})).ok

    def _config_path(self, *parts):
        """One path inside the configuration tree this lane reads its answers from.

        Thirteen capability rows say `{config_dir}` for this, and it resolves from the lane shape. But
        about half the stage's file reads happen outside a capability -- a `jq` of a layer file, a
        `test -s`, a `rm -rf` of an environment the lane made -- and those sites used to spell
        `{GUEST_TREE}/platform_config` as their own string. That is one fact in twenty-odd places, and
        it fails in only the interesting direction: a lane that reads somewhere else still writes into
        the tree named here, so the write lands where the lane says it does and the read finds the old
        one and reports a pass about a file nobody touched.

        Naming the root is not optional here even where the value is the same, because "the same" is a
        statement about the lane and not about the code, and code outlives lanes.
        """
        return posixpath.join(self.lane.config_dir, *parts)

    def _config_report(self, *parts):
        """How one written file is named in a report: the path the lane actually wrote to.

        `result.json`'s `config_adjustments` block answers "what did the harness change and where", and
        its `file` entries used to be spelled as `platform_config/...` literals, which were true for as
        long as every lane wrote in the checkout copy's own tree. A lane that prepares a directory of
        its own would keep reporting the old one, so the name is derived here.

        It is returned relative to the guest tree whenever it lives under it, because that is what
        every report a human has read so far says and there is no reason to churn it. A root outside
        the copy is spelled in full, which is the only honest way to name a file a reader could go and
        look at and not find.
        """
        path = self._config_path(*parts)
        under = config.GUEST_TREE + "/"
        return path[len(under):] if path.startswith(under) else path

    def _config_shown(self, field, *, environment=None, team=None):
        """Read one key's answer back through `kmt config show`, the verb that reads the tree back.

        `(ok, value, provenance)` — `show` prints every key the tree answers as
        `KEY = value   (layer from path)`, so the answer and the layer that answered it arrive on the
        same line and a stage asserts both from one run instead of pairing a value read with a guess
        about where it came from. A lane that reads a value out of a file and then says "this is
        this layer" is a lane asserting its own fixture, which is the sentence this helper exists to
        retire.

        The value is cut at the **three-space** separator and not at a single space. `show` puts the
        tag there on purpose, so that the value stays greppable, and cutting one space early reads the
        tag's opening parenthesis into the answer. This is the one place a later edit of this helper
        goes wrong.
        """
        result = self.km("config.show" if not team else "config.show.team",
                         **({"env": environment} if environment else {}),
                         **({"team": team} if team else {}))
        value = ""
        provenance = ""
        for line in (result.stdout or "").splitlines():
            if line.startswith("%s = " % field):
                answer, _, tag = line[len(field) + 3:].partition("   (")
                value = answer.strip()
                provenance = tag[:-1] if tag.endswith(")") else tag
                break
        return result.ok, value, provenance

    def _environment_config_value(self, field):
        """One key the *environment's* tree answers, read back through the verb that answers it.

        This read used to be a `jq` of one named file, and one blank answered two different questions:
        "no layer declares this" and "the tree answers this from a shipped default". Since `config init`
        prepares the directory instead of copying a template, the second answer is the common one, so a
        stage that greps a file for `*_DEPLOYMENT_NAMESPACE` reports a value the product never chose
        and calls a correct install a failure. `kmt config show` prints every key the tree answers and
        tags each line with the layer and the file that answered it, so the answer and its provenance
        arrive together and a blank now means the tree really does answer nothing. The tag is also what
        makes an assert say *which* of the five sources the answer came out of, which is the difference
        between "the environment said grafana" and "the chart's default said grafana".

        Keys a credential is made of do not go through here; see `_environment_credential`.
        """
        answered, value, _provenance = self._config_shown(field, environment=config.ENVIRONMENT)
        return value if answered else ""

    def _environment_credential(self, field):
        """One secret the environment's encrypted layer declares, read out of the file that holds it.

        Asking a file for this is asking the right question. No rule derives a password from a team's
        name and no shipped default carries one, so an encrypted file is the only place `kmt config show`
        could have found `GRAFANA_ADMIN_USER`, which makes the two reads answer the same question. The
        verb is not the same cost: `show` prints every key the tree answers, so reading two credentials
        through it dumps the whole configuration -- and every other secret in it -- into a command the
        harness then has to be careful not to log. One `jq` of the file named is narrower, and narrower
        is what a diagnostic read of a credential is for.
        """
        path = self._config_path(config.ENVIRONMENT, "static.encrypted.json")
        found = self.guest.run(command="jq -r --arg key %s '.[$key] // empty' %s"
                               % (shlex.quote(field), shlex.quote(path)), timeout=30)
        return found.stdout.strip() if found.ok else ""

    def _team_config_value(self, field):
        """One key the *team's* tree answers, one layer deeper, read through the same verb.

        Same rule as `_environment_config_value`: a team account lives in `APP_DEPLOYMENT_NAMESPACE` and
        a pipeline's runs live in `PIPELINE_NAMESPACE`, and a harness that invents either namespace for
        itself asserts about a place the product never chose. `PIPELINE_NAMESPACE` in particular is
        answered by a rule over the team's name unless someone overrides it, so the team's own
        `static.json` says nothing about it while the tree answers it plainly -- the exact shape that
        made the last full lane's cross-team probe report "not set".
        """
        answered, value, _provenance = self._config_shown(field, environment=config.ENVIRONMENT,
                                                         team=config.TEAM)
        return value if answered else ""

    def _team_config_declares(self, field, filename="static.json"):
        """Whether the team's own layer *declares* one key, which is not the question a value asks.

        `config unset` refuses a key no layer declares -- a different fact from "the answer is null", and
        the refusal text says which it met. So a stage that wants to reach the state a missing
        answer leaves behind has to know which case it is in before it calls the verb. Asking `show`
        would be wrong here: the tree answers derived keys all day long, so "answered" says nothing
        about whether there is an answer left to take away.
        """
        path = self._config_path(config.ENVIRONMENT, config.TEAM, filename)
        return self._config_declaration_at(path, field)

    def _team_config_json(self, field, filename="static.json"):
        """One key of the team's config as the JSON value it is, or None when it is absent."""
        path = self._config_path(config.ENVIRONMENT, config.TEAM, filename)
        found = self.guest.run(command="jq -c --arg key %s '.[$key] // empty' %s"
                               % (shlex.quote(field), shlex.quote(path)), timeout=30)
        try:
            return json.loads(found.stdout) if found.ok and found.stdout.strip() else None
        except ValueError:
            return None

    # The backing-service features (mysql / redis / nginx_proxy) went with the install
    # automation they drove -- owner decision 2026-10-27, docs/plans/lane-verification-3e8b3f6.md
    # SS2.1. `weak-ok:install-nginx` was red on every full lane for the same reason as the
    # other two: the pinned bitnami chart's image is gone from docker.io. Nothing replaces
    # them until the standard-helm-chart backing-service deploy lands, and a stub assert
    # that can only pass is not an assertion.

    def feature_config_scope(self):
        """The scope claim: a command run for one team acts on that team's config and on no other.

        This feature used to carry a second row, `config-negative-names-the-file`, which asked that a
        team whose layer cannot be read be refused by path. It cannot run against this tree and it is
        deleted rather than left reporting a red for an unreachable fixture. Two facts close it:
        `config unset` clears a *pair* of files, so no file ever goes missing for a reader to name, and
        the key the probe used (`PIPELINE_NAMESPACE`) is answered by a rule over the team's name, so a
        layer that declares nothing still has an answer to resolve and no refusal follows. The claim is
        not lost -- `tests/test_config_missing_team_config.py` asserts that a team config which is not
        there names the file it expected and produces no traceback, and that is the whole of what the
        row was for. What a lane cannot do is make a layer that its own verbs refuse to unmake.

        The D9 half survives because it measures a different thing: not what a refusal says, but which
        directory a command's writes land in. It needs no missing file, only two teams and a key that
        proves itself visible.
        """
        self._expect_setup_secrets_stays_in_its_team()

    def _expect_setup_secrets_stays_in_its_team(self):
        """D9: a command run for one team acts on that team's config and on no other.

        The old probe hid a sentinel in another team's `static.json` and looked for it in
        `tekton-list` output, which prints tekton resources and never config values - it could not
        have leaked. This one gives a second team an SSH deploy key aimed at *this* team's pipeline
        namespace, so a `setup-secrets` that read the wrong directory, or every directory, writes a
        Secret the assert can see.
        """
        namespace = self._team_config_value("PIPELINE_NAMESPACE")
        if not namespace:
            self.expect("config-scope-ignores-foreign-team", False,
                        "PIPELINE_NAMESPACE is not set in the team config, so there is no namespace to watch")
            return
        foreign_team = "kmt-e2e-foreign"
        sentinel = "kmt-e2e-foreign-ssh-secret"
        foreign = self._config_path(config.ENVIRONMENT, foreign_team)
        # The team directory and both of its files come from `kmt config add-team`, and the answers
        # follow from `config set`. Building them with `write_file` was the smallest version of the
        # mistake that is a defect and not a shortcut: the fixture's file and the product's file are not
        # the same
        # bytes, so a command that reads the product's layer is being measured against a document
        # nothing in kubementat would have written. A team layer is a pair of files with a manifest,
        # and only the verb that makes teams makes all three. (Teardown still removes the directory by
        # path, which is not a configuration write: uninstall sweeps team directories of its own.)
        created = self.km("config.add-team", team=foreign_team, timeout=120)
        if not created.ok:
            self.expect("config-scope-ignores-foreign-team", False,
                        "kmt config add-team could not create %s beside %s (%s), so there is no foreign "
                        "team to be scoped away from"
                        % (foreign_team, config.ENVIRONMENT, created.excerpt(160)))
            return
        try:
            answered = self._config_set("APP_DEPLOYMENT_NAMESPACE", foreign_team,
                                        environment=config.ENVIRONMENT, team=foreign_team)
            answered = self._config_set("PIPELINE_NAMESPACE", "%s-pipelines" % foreign_team,
                                        environment=config.ENVIRONMENT, team=foreign_team) and answered
            # The deploy key that is the whole probe, in the encrypted half of the layer: a name, an
            # inert private key, and a target this team's pipeline namespace. It travels as a staged
            # document for the same reason the grafana row does -- an argv is not where a key goes.
            key_document = posixpath.join(GUEST_STAGING_DIR, "kmt-e2e-foreign-team-answers.json")
            staged = self.guest.write_file(key_document, json.dumps({"SSH_DEPLOY_KEYS": [{
                "NAME": sentinel,
                "PRIVATE_KEY_BASE64": base64.b64encode(b"not a key, only a sentinel").decode(),
                "PUBLIC_KEY": "",
                "TARGET_NAMESPACE": namespace,
                "TARGET_SECRET_NAME": sentinel,
            }]}), timeout=30)
            answered = staged.ok and self.km("config.set.team.encrypted.document",
                                             team=foreign_team, answers_document=key_document,
                                             timeout=120).ok and answered
            if not answered:
                self.expect("config-scope-ignores-foreign-team", False,
                            "the foreign team's directory exists but kmt config would not answer its "
                            "keys, so this assert would be measuring a team that declares nothing")
                return
            result = self.km("secrets.setup", timeout=300)
            self.record_result("cap:secrets.setup-beside-a-foreign-team", "product", result)
            names = [name.split("/")[-1] for name in self.cluster.get_names("secret", namespace=namespace)]
            leaked = sentinel in names
            self.expect("config-scope-ignores-foreign-team", result.ok and not leaked,
                        "setup-secrets for %s exit=%s; team %s's key aimed at %s was %s"
                        % (config.TEAM, result.exit_code, foreign_team, namespace,
                           "WRITTEN (leaked across teams)" if leaked else "not written"))
            if leaked:
                self.cluster.delete("secret/%s" % sentinel, namespace=namespace)
        finally:
            # Uninstall sweeps every team directory, so a test team left here changes teardown.
            self.guest.run(command="rm -rf %s" % shlex.quote(foreign), timeout=30)

    def feature_user_accounts(self):
        """The emitted kubeconfig resolves an identity whose can-i answers are documented."""
        created = self.km("task.create-team-account", timeout=600)
        self.record_result("cap:create-team-account", "product", created)
        if not created.ok:
            return
        service_account = config.TEAM_ACCOUNT
        # The namespace is the one the product itself puts the account in, read from the file
        # that says so. Grepping it out of the command's own output made this stage depend on
        # the *first* word beginning "namespace" that a log line happens to contain, and the
        # first one was the pipeline namespace - so the account's permissions were checked in
        # a namespace the account is not bound in, and `account-deleted` passed for a namespace
        # that never had the account in it.
        namespace = self._team_config_value("APP_DEPLOYMENT_NAMESPACE") or config.TEAM
        # The impersonation flag is `--as=system:serviceaccount:<ns>:<name>`. There is no
        # `--serviceaccount` flag on `kubectl auth can-i`, so the check used to be one command
        # that always answered "unknown flag" - which reads as a denied permission rather than
        # as the probe being wrong.
        identity = {"as": "system:serviceaccount:%s:%s" % (namespace, service_account)}
        self.expect("account-service-account-resolvable", service_account in created.output,
                    "the command must name the account it created (%r was the name it was given); "
                    "output: %s" % (service_account, created.excerpt(300)))
        allowed = self.cluster.auth_can_i("create", "pods", namespace, identity)
        self.expect("account-can-i-create", allowed == "yes",
                    "%s can-i create pods in %s: %s" % (service_account, namespace, allowed))

        read_only = self.km("task.create-team-account-read-only", timeout=600)
        self.record_result("cap:create-team-account-read-only", "product", read_only)
        read_only_account = config.TEAM_ACCOUNT_READ_ONLY
        self.expect("account-read-only-account-resolvable", read_only_account in read_only.output,
                    "the read-only command must name the account it created (%r); output: %s"
                    % (read_only_account, read_only.excerpt(300)))
        denied = self.cluster.auth_can_i("delete", "pods", namespace,
                                        {"as": "system:serviceaccount:%s:%s" % (namespace, read_only_account)})
        self.expect("account-read-only-cannot-mutate", denied == "no",
                    "read-only %s can-i delete pods in %s: %s" % (read_only_account, namespace, denied))

        deleted = self.km("task.delete-team-account", timeout=600)
        self.record_result("cap:delete-team-account", "product", deleted)
        remaining = self.cluster.get_names("serviceaccount", namespace=namespace)
        self.expect("account-deleted", deleted.ok and service_account not in [n.split("/")[-1] for n in remaining],
                    "delete exit=%s, service accounts left in %s: %s"
                    % (deleted.exit_code, namespace, remaining))
        # The credential is the other half of the account. `kmt users delete-team-account`
        # deletes the Secret the token came from; the bash deleted the service account and
        # left it, so a kubeconfig handed to a person kept working after the account was
        # "deleted" and nothing in the lane could see that.
        secrets = self.cluster.get_names("secret", namespace=namespace)
        self.expect("account-token-secret-deleted",
                    "%s-sa-secret" % service_account not in [n.split("/")[-1] for n in secrets],
                    "token secret left in %s after deleting the account: %s"
                    % (namespace, [n for n in secrets if "sa-secret" in n]))

    def feature_grafana_accounts(self):
        """Every account the config declared is created, and readable through the API."""
        # The declaration first, and before anything is read: this stage used to be honest about being
        # vacuous, because an environment that declared nobody made the command return "nothing was
        # created" and both rows went green on nothing. The lane now seeds exactly one row for
        # itself, so `declared` below is a fact the harness wrote rather than a coincidence of the
        # template, and a green row means somebody was created.
        self._seed_grafana_account_row()
        declared = self._grafana_user_emails()
        # Before the command runs, and independent of it: a row whose config declares no accounts
        # is green for the same reason the old one was, just quietly. What makes this row honest
        # is that somebody had to be created, so the count is asserted and not assumed.
        self.expect("grafana-account-rows-declared", bool(declared),
                    ("platform_config/%s/static.encrypted.json declares %d GRAFANA_USERS row(s): %s. "
                     "The create row is only as good as this count: with somebody declared, a green "
                     "create means somebody had to be created."
                     % (config.ENVIRONMENT, len(declared), ", ".join(declared)))
                    if declared else
                    "platform_config/%s/static.encrypted.json declares no GRAFANA_USERS rows, so "
                    "the command has nothing to create and this row proves nothing. The vacuity "
                    "would be in the configuration, not in the code, and it is still a vacuous "
                    "row." % config.ENVIRONMENT)
        created = self.km("task.create-grafana-accounts", timeout=600)
        self.record_result("cap:create-grafana-accounts", "product", created)
        if not created.ok:
            # The command failing is the honest answer, and it is already recorded as the
            # capability result. Carrying on into a probe would report a credential problem as a
            # grafana problem.
            return
        namespace = self._environment_config_value("GRAFANA_DEPLOYMENT_NAMESPACE")
        if not namespace:
            self.expect("grafana-api-readable", False,
                        "GRAFANA_DEPLOYMENT_NAMESPACE is not set in the environment config")
            return
        service = self._service_endpoint("grafana", namespace=namespace)
        if not service:
            self.expect("grafana-api-readable", False,
                        "no grafana ClusterIP to probe in %s: a ClusterIP is what a probe running "
                        "on the node can reach, and a headless service is not" % namespace)
            return
        # Both keys come from the file that decides them: `HelmUtils.helmfile_apply` exports
        # exactly this pair before helmfile runs, and `kmt users create-grafana-accounts` reads
        # exactly this pair through `GrafanaClient.from_environment` when it logs in. The probe
        # used to hardcode `admin` and take the password from the *team* file, which declares
        # neither key - so the only answer on offer was the one nobody had a credential for. (The
        # endpoint had its own reason for reading `000`; see `_service_endpoint`.)
        admin_user = self._environment_credential("GRAFANA_ADMIN_USER")
        admin_password = self._environment_credential("GRAFANA_ADMIN_PASSWORD")
        missing = [key for key, value in (("GRAFANA_ADMIN_USER", admin_user),
                                         ("GRAFANA_ADMIN_PASSWORD", admin_password)) if not value]
        if missing:
            self.expect("grafana-api-readable", False,
                        "platform_config/%s/static.encrypted.json declares no %s, so the probe has "
                        "no credential to read" % (config.ENVIRONMENT, " or ".join(missing)))
            return
        probe = self._org_users_probe(service, admin_user, admin_password)
        code = probe.stdout.strip()[-3:]
        self.artifacts.log(self.stage, "grafana.users", probe.stdout)
        self.expect("grafana-api-readable", code.startswith("2"),
                    "grafana api replied %s from %s as %r (the credential platform_config/%s/static.encrypted.json names)"
                    % (code or probe.excerpt(120), service, admin_user, config.ENVIRONMENT))
        # The half the old row could not answer, because an empty body from a port-forward that
        # led nowhere looked like "the user already exists": the accounts the command was asked
        # to create, named in the same file, visible in the org roster afterwards. If the create
        # ran against the wrong address, this is where the row goes red.
        roster = self._org_roster(probe.stdout.strip()[:-3])
        if roster is None:
            self.expect("grafana-account-created", False,
                        "grafana did not answer as a roster (no JSON array of rows, no users list), "
                        "so nothing can be said about which accounts exist -- this is the probe "
                        "failing or a non-grafana body, not an absent account: %s"
                        % probe.excerpt(120))
        else:
            present = {str(row.get("email", "")).strip().lower() for row in roster}
            missing = [email for email in declared if email not in present]
            self.expect("grafana-account-created", not missing,
                        "asked grafana for %d account(s) and the roster answers with %s; missing: %s"
                        % (len(declared), sorted(present), missing))
        # The other half of declaring an account, and the one that makes the seeded row worth having.
        # One run cannot
        # tell an idempotent create from a create that would run twice, so the command runs again
        # and both halves of the second answer are asserted: the roster it leaves behind (a second
        # account with the same address is the failure) and the sentence the code prints when it
        # found the account already there (`users.py:134` -- "already exists ... nothing changed and
        # no password was applied", reached through `ensure_account`'s `created=False`).
        self._expect_grafana_accounts_are_idempotent(declared, first=created, service=service,
                                                    admin_user=admin_user,
                                                    admin_password=admin_password)

        # The other half used to be a question this suite could not answer: does the
        # `GRAFANA_ADMIN_USER` an operator exports reach the install? The owner decided it does not
        # have to: `static.encrypted.json` is the source of truth, and kubementat exports that pair
        # itself, so a value in a shell is not a second authority. The assert that measured the old
        # contradiction measured a contract that no longer exists -- and the login above *is* the
        # regression net, because it uses the file's credential and fails the moment an environment
        # variable starts overriding it. So the redundant assert is gone, and `config.py`'s block
        # records that the exported pair is now inert rather than load-bearing.

    def _org_users_probe(self, service, admin_user, admin_password):
        """One read of `/api/org/users`, from the node, with the pair the file names.

        The same access path everywhere: a ClusterIP (a `svc.cluster.local` name answers `000` to
        a probe on the node) and the admin pair from the environment's encrypted file. Two stages
        read it now -- after the create, and after the rerun -- and a probe that invented an
        address or a user for the second one would report a grafana that is not there.
        """
        return self.guest.run(
            command="curl -sL -w '%%{http_code}' --max-time 15 %s/api/org/users -u %s"
                    % (service, shlex.quote("%s:%s" % (admin_user, admin_password))),
            timeout=60)

    def _expect_grafana_accounts_are_idempotent(self, declared, first, service, admin_user,
                                                admin_password):
        """The second run: the same command, the same declared rows, no second account and no lie.

        Two asserts because two failures are possible, and they look different:

        * the roster *after* the rerun. A duplicate is a second user holding the same address, and
          the first run's roster cannot see it, so the roster is read again. This is the half no
          amount of reading the command's stdout would supply.
        * the rerun's own sentence. `ensure_account` returns `created=False` for an account it
          found, and the CLI says so; `created grafana account ...` on a second run means it made
          the account again, which is the vacuity this row exists to close, in the write direction.
        """
        second = self.km("task.create-grafana-accounts", timeout=600)
        self.record_result("cap:create-grafana-accounts-second-pass", "product", second)
        probe = self._org_users_probe(service, admin_user, admin_password)
        self.artifacts.log(self.stage, "grafana.users.rerun", probe.stdout)
        roster = self._org_roster(probe.stdout.strip()[:-3])
        if roster is None:
            self.expect("grafana-account-rerun-confirms-existing", False,
                        "the second run ran (exit %s), but grafana did not answer as a roster after "
                        "it, so a duplicate is not ruled out and the command's own words cannot "
                        "stand in for a read: %s" % (second.exit_code, probe.excerpt(160)))
            return
        counts = {}
        for row in roster:
            email = str(row.get("email", "")).strip().lower()
            if email:
                counts[email] = counts.get(email, 0) + 1
        duplicated = sorted(email for email in declared if counts.get(email, 0) > 1)
        lowered = second.output.lower()
        created_again = [email for email in declared if "created grafana account %s" % email in lowered]
        unconfirmed = [email for email in declared if "%s already exists" % email not in lowered]
        self.expect("grafana-account-rerun-confirms-existing",
                    bool(declared) and second.ok and not duplicated and not created_again
                    and not unconfirmed,
                    "asked grafana for the same %d account(s) twice; the roster afterwards holds %s "
                    "and the second run said %r. Red is one of: a duplicate (%s), the same account "
                    "made a second time (%s), a run that confirmed nothing (%s), or a run that did "
                    "not run at all (exit %s, first run ok=%s)."
                    % (len(declared), counts or "nobody", second.excerpt(160),
                       duplicated or "none", created_again or "none", unconfirmed or "none",
                       second.exit_code, first.ok))

    def feature_cleanup_job(self):
        """The job exists, is scheduled, and removes exactly what the filter claims."""
        if not self.run_capability("task.cleanup-job", timeout=600):
            return
        namespace = self._environment_config_value("CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE")
        if not namespace:
            self.expect("cleanup-cronjob-exists", False,
                        "CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE is not set in the environment "
                        "config, so nothing says where the job lives")
            return
        cronjobs = self.cluster.kubectl("get", "cronjob", "-n", namespace, "-o", "json", timeout=60)
        if not cronjobs.ok:
            self.expect("cleanup-cronjob-exists", False, "no cronjobs readable in %s: %s" % (namespace, cronjobs.excerpt(200)))
            return
        items = json.loads(cronjobs.stdout).get("items", [])
        self.expect("cleanup-cronjob-exists", bool(items), "cronjobs in %s: %s" % (namespace, [i["metadata"]["name"] for i in items]))
        self.expect("cleanup-cronjob-scheduled", any(item.get("spec", {}).get("schedule") for item in items),
                    "schedules: %s" % [item.get("spec", {}).get("schedule") for item in items])
        self._expect_cleanup_removes_only_succeeded_runs()

    def _expect_cleanup_removes_only_succeeded_runs(self):
        """`--filter succeeded` deletes the succeeded runs and keeps the failed one.

        The old row counted runs in the cronjob namespace, where no PipelineRun ever lives, and
        passed on `before=0 after=0`. Runs live in the team's `PIPELINE_NAMESPACE`; stage 03 left a
        succeeded one there, and this adds a failed one, so the filter has something to keep and
        something to remove.
        """
        namespace = self._team_config_value("PIPELINE_NAMESPACE")
        if not namespace:
            self.expect("cleanup-runs-filtered", False, "PIPELINE_NAMESPACE is not set in the team config")
            return
        applied = self.cluster.apply(_failing_pipelinerun_manifest(FAILING_RUN), namespace=namespace)
        if not applied.ok:
            self.expect("cleanup-runs-filtered", False,
                        "could not create the failing PipelineRun: %s" % applied.excerpt(200))
            return
        try:
            self.cluster.wait("the deliberately failing PipelineRun finishes",
                              lambda: self._run_finished(namespace, FAILING_RUN), timeout=180, interval=4)
            before = self._pipelinerun_outcomes(namespace)
            succeeded = sorted(name for name, outcome in before.items() if outcome == "True")
            self.run_capability("tekton.cleanup-pipeline-runs", timeout=300)
            after = self._pipelinerun_outcomes(namespace)
            survivors = [name for name in succeeded if name in after]
            failed_kept = before.get(FAILING_RUN) == "False" and FAILING_RUN in after
            self.expect("cleanup-runs-filtered", bool(succeeded) and failed_kept and not survivors,
                        "before=%s after=%s; succeeded runs to remove=%s, survivors=%s, failed run kept=%s"
                        % (before, sorted(after), succeeded or "none (nothing to filter)",
                           survivors or "none", failed_kept))
        finally:
            self.cluster.delete("pipelinerun/%s" % FAILING_RUN, namespace=namespace)

    def _pipelinerun_outcomes(self, namespace):
        """{run: status of its Succeeded condition} - "True", "False", or "Unknown" while running."""
        try:
            items = self.cluster.json("get", "pipelinerun", "-n", namespace, "-o", "json", timeout=60)["items"]
        except asserts.KubectlError:
            return {}
        outcomes = {}
        for item in items:
            conditions = {c.get("type"): c.get("status") for c in (item.get("status") or {}).get("conditions") or []}
            outcomes[item["metadata"]["name"]] = conditions.get("Succeeded", "Unknown")
        return outcomes

    def _run_finished(self, namespace, name):
        outcome = self._pipelinerun_outcomes(namespace).get(name)
        return outcome in ("True", "False"), "%s Succeeded=%s" % (name, outcome)

    # ------------------------------------------------------------- 99-teardown

    def stage_teardown(self):
        if self.cluster is None:
            raise StageFailed("no cluster to tear down")
        before = self.cluster.namespaces()
        self.artifacts.snapshot("ns-before-uninstall", before)

        if self.scope == "install":
            # The everyday run: one scripted uninstall, then the same residue checks the full
            # scope applies. The confirmation probes, the second pass and the reinstall cycle are
            # questions about the uninstall command itself, and belong to `--scope full`.
            self.km_uninstall(timeout=660, label="cap:uninstall")
            self._expect_no_residue()
            self._expect_routing_lane_absence()
            return

        self._confirm_rejected(before)
        self._confirm_fails_closed()
        # Both passes run with --yes and without stdin, so they also prove the flag
        # works where the prompt cannot. The second pass measures idempotency.
        self.km_uninstall(timeout=660, label="cap:uninstall")
        self.km_uninstall(timeout=330, label="cap:uninstall-second-pass")
        self._expect_no_residue()
        self._expect_reinstall_after_uninstall()
        # Before `_expect_tree_independent`, which deletes the guest copy: what the routing lane says
        # about its own absence is decided from the cluster, but it is recorded here while the run can
        # still be read.
        self._expect_routing_lane_absence()
        self._expect_tree_independent()

    def _expect_reinstall_after_uninstall(self):
        """An uninstall is clean when the next install on the same cluster works.

        Leftover CRDs, webhooks and cluster-scoped RBAC rarely show up as anything but an install
        that fails the second time round, which is the moment an operator finds out. So the first
        group goes back on, its releases are checked, and the second uninstall has to leave the
        cluster as clean as the first one did.
        """
        group = self.lane.install_groups[0]
        reinstalled = self.km("install", group=group, timeout=900)
        self.record_result("cap:reinstall-after-uninstall", "product", reinstalled,
                           detail_prefix="group=%s " % group)
        if reinstalled.ok:
            expected = self._expected_releases([group])
            status = {release["release"]: release["status"] for release in self.cluster.helm_list()}
            missing = [name for name in expected if status.get(name) != "deployed"]
            self.expect("reinstall-releases-deployed", bool(expected) and not missing,
                        "group %s declares %s; not deployed after reinstall: %s"
                        % (group, expected, ["%s=%s" % (n, status.get(n, "absent")) for n in missing] or "none"))
        self.km_uninstall(timeout=660, label="cap:uninstall-after-reinstall")
        extra = self._wait_no_extra_namespaces(self.state.get("baseline_namespaces") or [])
        self.expect("no-extra-namespaces-after-reinstall", bool(self.state.get("baseline_namespaces")) and not extra,
                    "namespaces beyond the post-provision baseline after the second uninstall: %s"
                    % (", ".join(extra) or "none"))

    def km_uninstall(self, timeout, label, stdin=None):
        command = "timeout --signal=KILL %d %s 2>&1" % (max(1, timeout - 30), _sh(self.kmt_argv("uninstall")))
        result = self.guest.run(command=command, timeout=timeout, cwd=config.GUEST_TREE, stdin=stdin)
        return self.record_result(label, "product", result)

    def _confirm_rejected(self, before):
        """Answering "no" must delete nothing.

        The answer has to reach a *terminal*, or the CLI's fail-closed guard would fire
        first and this would measure the no-terminal refusal twice. `script` gives the command a pty
        and forwards our answer to it.
        """
        pty_command = shlex.quote(_sh(self.kmt_argv("uninstall.confirm")))
        answered = self.guest.run(command="printf 'n\\n' | timeout --signal=KILL 120 "
                                         "script -qec %s /dev/null 2>&1" % pty_command,
                                  timeout=140, cwd=config.GUEST_TREE)
        self.artifacts.log(self.stage, "uninstall-answer-no", answered.output)
        now = self.cluster.namespaces()
        self.expect("confirmation-rejected-changes-nothing",
                    now == before and not answered.ok and "Cancelled uninstall script" in answered.output,
                    "exit=%s (cancelled must be non-zero), namespaces unchanged=%s, cancelled message=%s"
                    % (answered.exit_code, now == before, "Cancelled uninstall script" in answered.output))

    def _confirm_fails_closed(self):
        """No terminal and no --yes must fail closed fast, never hang."""
        before = self.cluster.namespaces()
        result = self.guest.run(command="timeout --signal=KILL 5 %s < /dev/null 2>&1"
                                % _sh(self.kmt_argv("uninstall.confirm")),
                                timeout=20, cwd=config.GUEST_TREE)
        self.artifacts.log(self.stage, "uninstall-no-tty", result.output)
        self.expect("confirmation-fails-closed", (not result.ok) and self.cluster.namespaces() == before,
                    "exit=%s, nothing deleted=%s, tail=%s"
                    % (result.exit_code, self.cluster.namespaces() == before, result.excerpt(200)))
        # The point of failing closed is that the operator is *told* about the escape hatch.
        self.expect("uninstall-advertises-yes-flag", "--yes" in result.output,
                    "the CLI never mentions its --yes/-y flag: %s" % result.excerpt(160).replace("\n", " "))

    def _wait_no_extra_namespaces(self, baseline, timeout=None, interval=None):
        """Poll until nothing beyond `baseline` is reported, bounded, and return what is left.

        One read cannot decide this: namespace deletion is accepted before it is complete, and a
        lane that samples the instant after `kmt uninstall --yes` returns is measuring the cluster's
        garbage collector rather than the uninstall. It is polled for the same reason every wait in
        this suite is polled - a sleep is not evidence - and the ceiling is a ceiling, not a verdict.
        """
        deadline = time.time() + (NS_GONE_WAIT_S if timeout is None else timeout)
        interval = POLL_INTERVAL_S if interval is None else interval
        while True:
            extra = sorted(set(self.cluster.namespaces()) - set(baseline))
            if not extra or time.time() >= deadline:
                return extra
            time.sleep(interval)

    def _namespace_phases(self, namespaces):
        """`name=Terminating` for each survivor, so a red reads as a phase and not as a bare name.

        A namespace that disappears between reading the list and reading its phase is simply absent
        from the answer, which is what a cluster mid-deletion actually looks like.
        """
        out = []
        for name in namespaces:
            result = self.cluster.kubectl("get", "ns", name, "-o", "jsonpath={.status.phase}", timeout=20)
            phase = (result.stdout or "").strip() if result and result.stdout else ""
            out.append("%s=%s" % (name, phase or "gone"))
        return ", ".join(out) or "none"

    def _expect_no_residue(self):
        """What survives uninstall must be a subset of the post-provision cluster.

        This used to compare against `post_install_namespaces`, which is taken *after* the
        install - so every namespace kubementat created was in the baseline it was allowed to
        leave behind, and the assert could not fire. The lane's own snapshot is the proof: six
        namespaces on the machine, `ns-after-uninstall.json` still naming cert-manager and
        nginx-ingress, and the detail reading "namespaces beyond baseline: none". Nothing in
        that comparison could have failed.

        The baseline is what the cluster looked like before kubementat touched it - recorded by
        `stage_provision` into `baseline_namespaces`, after the k3s add-on charts have landed so
        they are not mistaken for residue. Anything that set does not already contain is
        residue, which is the only definition under which a green here means something. Namespace
        deletion is asynchronous and was measured draining over 42 s, so "extra" is polled for,
        not sampled once - one read of `kubectl get ns` immediately after `--yes` returns tells you
        about the cluster's scheduler, not about the uninstall.
        """
        after = self.cluster.namespaces()
        self.artifacts.snapshot("ns-after-uninstall", after)
        baseline = self.state.get("baseline_namespaces")
        if not baseline:
            # There is no "extra" without the set "extra" is measured against, and a green
            # invented from an empty baseline is the same lie the old baseline told.
            self.expect("no-extra-namespaces", False,
                        "no post-provision baseline in the run state (stage 01-provision records "
                        "it), so nothing can be called residue: %s namespaces on the cluster" % len(after))
        else:
            extra = self._wait_no_extra_namespaces(baseline)
            self.expect("no-extra-namespaces", not extra,
                        "namespaces beyond the post-provision baseline (%s): %s%s"
                        % (", ".join(sorted(baseline)) or "none",
                           ", ".join(extra) or "none",
                           "  (phase: %s)" % self._namespace_phases(extra) if extra else ""))

        tekton_crds = sorted(name for name in self.cluster.crds()
                             if name.endswith(".tekton.dev") or name.endswith(".triggers.tekton.dev"))
        self.expect("no-tekton-crds", not tekton_crds, "tekton CRDs left: %s" % (tekton_crds or "none"))

        clusterroles = self.cluster.clusterroles()
        self.expect("no-helm-deployer-clusterrole", "helm-deployer-cluster-role" not in clusterroles,
                    "helm clusterroles left: %s" % ([role for role in clusterroles if "helm" in role] or "none"))

        # Cluster-scoped objects survive every namespace delete, so they are what an uninstall
        # forgets: the trigger and cleanup-job RBAC, the CRDs a chart keeps on uninstall, the
        # webhooks that then point at a service which no longer exists. Measured against the same
        # post-provision baseline as the namespaces, kind by kind.
        #
        # Two objects in one of those kinds are not forgettings, they are facts: the EventListener
        # controller binds `tekton-triggers-eventlistener-roles` and its clusterroles sibling at
        # run time, so the release manifests never bind them, they carry no `managed-by`, and the
        # sweep that takes everything else is right to leave them alone. They are declared per kind
        # in `config.CLUSTER_SCOPED_EXPECTED_RESIDUE` rather than swept by name in the product,
        # because a name list in the uninstall path is a list that goes stale on an upstream rename
        # and the sweep is supposed to ask the cluster instead. Subtracting that set is the whole
        # of the difference between "this leftover is known" and "nothing is left": the rest of
        # what the cluster reports is residue and goes red, including a third ClusterRole that is
        # not on the declared list.
        baseline_scoped = self.state.get("baseline_cluster_scoped")
        if not baseline_scoped:
            self.expect("no-extra-cluster-scoped", False,
                        "no post-provision baseline of cluster-scoped objects in the run state")
        else:
            for kind in config.CLUSTER_SCOPED_RESIDUE_KINDS:
                extra = set(self.cluster.cluster_scoped_names(kind)) - set(baseline_scoped.get(kind, []))
                declared = config.CLUSTER_SCOPED_EXPECTED_RESIDUE.get(kind, config.ExpectedResidue())
                excused = sorted(extra & declared.names)
                unexpected = sorted(extra - declared.names)
                detail = ("%s beyond the post-provision baseline: unexpected-%d (%s), expected-%d (%s)"
                          % (kind, len(unexpected), ", ".join(unexpected) or "none",
                             len(excused), ", ".join(excused) or "none"))
                if excused:
                    # The reason travels with the names, or the line reads as a bug being excused
                    # by a reader who has no way to check it.
                    detail += " -- %s" % declared.why
                self.expect("no-extra-cluster-scoped:%s" % kind, not unexpected, detail)

        claims = self.cluster.pvc_status()
        created = set(self.state.get("post_install_pvcs") or [])
        remaining = set(claims["bound"]) | {name for name, _phase, _message in claims["pending"]}
        survivors = sorted(created & remaining)
        self.expect("no-kubementat-claims", not survivors,
                    "claims created during the run that survived uninstall: %s" % (survivors or "none"))

        # Release identity is `namespace/name`, not the name: kubementat's traefik is
        # `traefik/traefik`, k3s's is `kube-system/traefik`, and comparing bare names let one object
        # answer for the other -- ours, still installed in its own namespace, looked accounted for
        # because the same name had been on the cluster at baseline in `kube-system`. The other half
        # of that correctness is not here: the baseline has to have been taken after k3s finished
        # installing its own addons, which is what stage 01 waits for.
        baseline_ids = self.state.get("helm_baseline_ids")
        if baseline_ids is None:
            self.expect("no-kubementat-releases", False,
                        "no namespaced release baseline was recorded before kubementat ran, so "
                        "nothing can be called ours and a green here would be a guess")
        else:
            releases_now = {_release_id(release) for release in self.cluster.helm_list()}
            installed = {_release_id(release) for release in (self.state.get("helm_releases") or [])}
            ours = installed - set(baseline_ids)
            self.expect("no-kubementat-releases", not (ours & releases_now),
                        "helm releases that survived uninstall: %s"
                        % (sorted(ours & releases_now) or "none"))

    def _expect_tree_independent(self):
        """After teardown nothing may need the checkout any more.

        "Nothing" is two places: a process on the node still running out of the checkout (a
        tunnel nobody stopped, a CLI still waiting), and an object in the cluster that mounts it.
        The old row asserted that `uv run kmt` fails in `/tmp` once the tree is gone, which is true
        of every uv project and so said nothing about the uninstall.
        """
        removed = self.guest.run(command="rm -rf %s" % shlex.quote(config.GUEST_TREE), timeout=120, cwd="/")
        under_tree = re.compile(r"%s(/|\s|$)" % re.escape(config.GUEST_TREE))
        listing = self.guest.run(command="ps -eo pid=,args=; for p in /proc/[0-9]*; do "
                                         "printf '%s cwd=%s\\n' \"${p#/proc/}\" \"$(readlink $p/cwd 2>/dev/null)\"; done",
                                 timeout=60, cwd="/")
        processes = [line.strip() for line in listing.stdout.splitlines() if under_tree.search(line)]
        objects = self.cluster.kubectl("get", "pods,pv", "-A", "-o", "json", timeout=60)
        mounted = bool(objects.ok and under_tree.search(objects.stdout.replace('"', " ")))
        self.expect("uninstall-independent-of-tree", removed.ok and not processes and not mounted,
                    "guest copy deletable=%s, processes still using it=%s, cluster objects mounting it=%s"
                    % (removed.ok, processes[:5] or "none", mounted))

    def _grafana_user_emails(self):
        """The emails the create command was given, read from the file that gives them.

        Same route as `_environment_config_value`, because a second opinion about which accounts
        this run is supposed to have made is a guess about configuration. It goes through
        `_environment_config_json` rather than a line split: `GRAFANA_USERS` is a list of objects,
        and a line-by-line parse of a pretty-printed list answers "no emails" for a config that
        named several, which is how a declared row came to look undeclared.
        """
        rows = self._environment_config_json("GRAFANA_USERS", "static.encrypted.json")
        if isinstance(rows, dict):          # one object where a list was written is still one row
            rows = [rows]
        emails = []
        for row in rows or []:
            if isinstance(row, dict) and str(row.get("email") or "").strip():
                emails.append(str(row["email"]).strip().lower())
        return emails

    def _environment_config_json(self, field, filename="static.json"):
        """One key of one environment-level config file, as the JSON value it is, or None.

        The `jq -c` is the whole point: `-r` answers an array of objects one *key* per line, so the
        line-by-line reader that used to parse `GRAFANA_USERS` found nothing to parse and reported a
        config that declared rows as one that declared none. Compact output is one value per line,
        which is the shape `GRAFANA_USERS` is actually written in and what the row asserts on.
        """
        path = self._config_path(config.ENVIRONMENT, filename)
        found = self.guest.run(command="jq -c --arg key %s '.[$key] // empty' %s"
                               % (shlex.quote(field), shlex.quote(path)), timeout=30)
        try:
            return json.loads(found.stdout) if found.ok and found.stdout.strip() else None
        except ValueError:
            return None

    def _seed_grafana_account_row(self):
        """Declare one real account, because a command that refuses an empty list is not tested by
        a lane that declares nothing.

        `create-grafana-accounts` answers "`GRAFANA_USERS` declares no accounts, so nothing was
        created" (exit 0, `grafana_accounts.py:194`) and the two rows built on it go green while
        creating nothing. The owner's option A seeds one real row; the rejected option -- `skipped`
        naming the config file -- leaves the command unexecuted forever.

        The write is deliberately *not* a template edit, not a `write_file` of the whole file, and no
        longer a `jq --slurpfile` that merged two keys into a file the verbs own. Three facts decide
        it:

        * `kmt config set --encrypted` is the door a credential the lane declares goes in through,
          and the verb is the only way in. The encrypted file is written by `kmt config init`
          (which invents `GRAFANA_ADMIN_PASSWORD` itself, per `constants.GENERATED_ENCRYPTED_ANSWERS`)
          and a wholesale rewrite would have replaced the credential grafana is installed with, so the
          lane answers exactly the two keys it cares about and nothing else.
        * the row's password is still generated here, for this run, with the same stdlib generator
          `secrets` gives. It is not in the template, not in `config.py`, not in this file's text, and
          after this change it is not in a command line either: the row travels as a staged document
          and only its path is an argument, which is why `--from-file` exists and the reason
          the write is not a `jq --slurpfile` any more. If a rerun ever wants the password, that rerun
          generates its own.
        * both keys land in one run of one verb, and the placeholder blanking is why. The template
          ships `GRAFANA_SERVICE_ACCOUNT_TOKEN` as `REPLACEME_GRAFANA_SEVICE_ACCOUNT_TOKEN` in the shape
          the retired writers left behind, and `GrafanaClient.from_environment` prefers a non-empty token
          over the admin pair (`grafana_client.py:188-190`) -- so the placeholder, which is not a
          credential, is what made the last run's create answer 401 before a single account was asked
          for. The lane's own credential is the admin pair; a lane that wants a service-account token
          puts a real one here.

        Returns the seeded row when the config now declares it, else None.
        """
        row = {"email": config.GRAFANA_ACCOUNT_EMAIL, "name": config.GRAFANA_ACCOUNT_NAME,
               "password": secrets.token_urlsafe(18)}
        # The document `--from-file` reads, staged first because a file cannot answer anything until it
        # is on the guest. Both keys land in one run of one verb: `GRAFANA_USERS` is the row the accounts
        # stage will be asked to create, and `GRAFANA_SERVICE_ACCOUNT_TOKEN` is blanked so the shipped
        # placeholder stops answering before a credential that is not one.
        staged = self.guest.write_file(config.GRAFANA_ANSWER_DOCUMENT,
                                       json.dumps({"GRAFANA_USERS": [row],
                                                   "GRAFANA_SERVICE_ACCOUNT_TOKEN": ""}),
                                       timeout=30)
        declared = self.km("config.set.encrypted.document",
                           answers_document=config.GRAFANA_ANSWER_DOCUMENT, timeout=120).ok
        written = staged.ok and declared
        self.state.setdefault("config_adjustments", []).append({
            "file": self._config_report(config.ENVIRONMENT, "static.encrypted.json"),
            "field": "GRAFANA_USERS (one row, seeded for this run) and GRAFANA_SERVICE_ACCOUNT_TOKEN (blanked)",
            "value": config.GRAFANA_ACCOUNT_EMAIL})
        seeded = config.GRAFANA_ACCOUNT_EMAIL in (self._grafana_user_emails() or [])
        self.expect("grafana-account-row-seeded", written and seeded,
                    "the harness asked kmt config set --encrypted --from-file for one GRAFANA_USERS row "
                    "for %s and a blanked GRAFANA_SERVICE_ACCOUNT_TOKEN (document staged ok=%s, set ok=%s) "
                    "and the environment afterwards declares %s. Without that row the accounts stage is a "
                    "command that refuses to act, reported as a pass."
                    % (config.GRAFANA_ACCOUNT_EMAIL, staged.ok, declared,
                       "it" if seeded else "nobody"))
        return row if written and seeded else None
        return row

    def _org_roster(self, body):
        """`/api/org/users`' rows, or None when the body is not a roster.

        Distinguishing "no such account" from "that is not grafana answering" is the whole
        difference between a failed create and a failed probe, and the bash could not tell them
        apart either. The endpoint answers with a **bare JSON array** of rows carrying
        orgId/userId/uid/email/login/role -- the shape the full lane captured on 2026-10-30
        (`20261030T122617Z-full-full-d7d1de1/stages/05-features--grafana.users.log`), which this
        parser read as `None` and turned a green row red. A bare empty array is still a roster:
        it says nobody is there, which is an answer. The `{"users": [...]}` shape is accepted
        too, because `/api/users/search` and older grafana builds return it. An HTML error page,
        a truncated body, or an error document like `{"message": "Unauthorized"}` stays None, so
        a credential failure is never read as an absent account.
        """
        try:
            payload = json.loads(body or "")
        except ValueError:
            return None
        if isinstance(payload, list):
            return payload
        rows = payload.get("users") if isinstance(payload, dict) else None
        return rows if isinstance(rows, list) else None

    def _service_endpoint(self, needle, namespace):
        """The URL a guest-resident probe can actually open for one service.

        `<name>.<namespace>.svc.cluster.local` is a *cluster-internal* name: the node the probe
        runs on has no cluster DNS, so asking for it answers `000` (measured on the full lane:
        `assert:grafana-api-readable | grafana api replied 000`, with the service found and the
        namespace right). A ClusterIP is routable from the node, which is what turns this from a
        connection failure into the credential check this stage is about.
        """
        services = self.cluster.kubectl("get", "svc", "-n", namespace, "-o", "json", timeout=60)
        if not services.ok:
            return ""
        for item in json.loads(services.stdout).get("items", []):
            if needle in item["metadata"]["name"]:
                cluster_ip = (item.get("spec") or {}).get("clusterIP")
                port = (item["spec"].get("ports") or [{}])[0].get("port", 80)
                if not cluster_ip or cluster_ip == "None":
                    return ""
                return "http://%s:%s" % (cluster_ip, port)
        return ""

    # ------------------------------------------------------------- the routing lane
    #
    # What `--lane routing` proves, in the order the lane does it. The whole point of the lane is that
    # it is the only place the *install* branch of `kmt routing install` runs: every other lane starts
    # with k3s's controller on the node, so every other lane can only ever exercise the guard.
    #
    # Three rules hold this together, and they are the reason the steps look fussy:
    #
    # * Nothing is deleted. The disable is a k3s config entry, not `helm uninstall`, not `kubectl
    #   delete`, not a `.skip` file -- plan decision 3 says kubementat does not delete a release
    #   another component created, and a lane that deletes it in the harness would be teaching the
    #   product a habit it must not have.
    # * A wait is a read. "Disabled" and "stopped" are different facts, and the second one is the
    #   only thing that makes the install of a second controller safe, because the old holder of
    #   node ports 80/443 is a daemonset pod and a Service that arrives early is a Service that
    #   never schedules.
    # * The verdict is the cluster's. Chart, revision, endpoints, IngressClass bindings, fingerprints
    #   -- all of it is read back. stdout is only ever asked what the command *said* it did.

    def _routing_lane(self):
        """Whether this run walks the routing steps. Gated on the feature, not on the lane name.

        A gate by lane name would be the kind of correctness that breaks on the first new lane: the
        day `full` grows `routing_ingress` the steps would switch on whether or not anyone decided
        that. The feature is the decision, so the feature is the gate.
        """
        return "routing_ingress" in self.lane.features

    def _routing_configuration(self):
        """The routing values this run uses, read from the file the product reads them from.

        `TRAEFIK_DEPLOYMENT_NAMESPACE` and `TRAEFIK_INGRESS_CLASS` are the environment's own keys, and
        `_environment_config_value` is where every other stage reads a key like these. What the
        harness spells out instead is a second opinion about the same fact, which is exactly the
        mistake `namespace_rename` and `task.cleanup-job` were written to catch.

        The fallback is stated rather than hidden, because the product warns about the same fallback
        (`config.py`: "config answers, code falls back") and a lane that silently used its own
        default would be asserting about a namespace the product never chose.
        """
        cached = self.state.get("routing_configuration")
        if cached:
            return cached
        namespace = self._environment_config_value("TRAEFIK_DEPLOYMENT_NAMESPACE")
        ingress_class = self._environment_config_value("TRAEFIK_INGRESS_CLASS")
        cert_manager_namespace = self._environment_config_value("CERT_MANAGER_DEPLOYMENT_NAMESPACE")
        configuration = {
            "namespace": namespace or "traefik",
            "ingress_class": ingress_class or "traefik",
            "cert_manager_namespace": cert_manager_namespace or "cert-manager",
            "from_config": {
                "TRAEFIK_DEPLOYMENT_NAMESPACE": bool(namespace),
                "TRAEFIK_INGRESS_CLASS": bool(ingress_class),
                "CERT_MANAGER_DEPLOYMENT_NAMESPACE": bool(cert_manager_namespace),
            },
        }
        self.state["routing_configuration"] = configuration
        return configuration

    def _routing_traefik_releases(self):
        """(rows, error): every release on the cluster that runs a Traefik *proxy*, by chart.

        The filter is the product's own (see `asserts.traefik_proxy_releases`), so the lane and the
        uninstall sweep ask the same question of the same field and cannot disagree.
        """
        rows, error = self.cluster.helm_list_rows()
        return asserts.traefik_proxy_releases(rows), error

    @staticmethod
    def _release_description(row):
        return "%s/%s chart=%s revision=%s status=%s" % (row.get("namespace"), row.get("release"),
                                                          row.get("chart") or "?", row.get("revision") or "?",
                                                          row.get("status") or "?")

    def _routing_classes_on_traefik(self):
        """The IngressClasses bound to Traefik's controller, as `{name: controller}`.

        Raises `KubectlError` when the cluster will not answer: a guard that decided "exactly one" on
        top of a failed read would be a green nobody can use.
        """
        return {name: controller for name, controller in self.cluster.ingress_classes().items()
                if controller == ROUTING_TRAEFIK_CONTROLLER}

    # ------------------------------------------------------ 1. the disable, and 2. the wait

    def _disable_k3s_bundled_traefik(self):
        """Take k3s's bundled Traefik off the node, the way k3s says to take a component off.

        The mechanism is the config file, and the reason is in k3s's own code rather than in a
        preference. `pkg/deploy/controller.go`'s `shouldDisableFile` decides per file, by basename:
        a disabled manifest is actively deleted, and `watcher.delete` says it "completely removes
        both a manifest, and any resources that it did or would have created" -- the HelmChart goes,
        and the helm-controller runs a real uninstall of the release that came from it. That is what
        k3s documents for `--disable`/`disable:` on a cluster where the component is already running.

        Everything else on the table is something else:

        * a `.skip` file *never* removes what is already installed -- k3s's own words are that
          Traefik "would stay as-is", so the node would keep both controllers and the lane would be
          asserting nothing while it looked green.
        * `helm uninstall` / `kubectl delete` of a release k3s created is the opposite of plan
          decision 3, and it is unrepeatable: k3s's manifest is still on disk and would redeploy the
          chart the next time the server starts.
        * a `.skip`-style or delete-based "disable" would also be per-run, which means it would not
          survive the reinstall cycle this lane runs at the end of `--scope full`.

        The entry is written into `/etc/rancher/k3s/config.yaml`, next to the `write-kubeconfig-mode`
        line `01-provision.sh` wrote, and preserved -- the mode is what makes the kubeconfig readable
        by the unprivileged `kmt` the lane runs, and losing it fails the install two steps later. It
        is written on one line as a flow sequence so that re-running this step is a rewrite of the
        same entry rather than a second `disable:` key in one file.

        Two names are listed. `traefik` is the documented one, and in current k3s the single
        `manifests/traefik.yaml` declares both the proxy and the `traefik-crd` chart, so disabling it
        reaches both. `traefik-crd` is listed as well because a k3s that ships the companion as its
        own manifest file is only reached by that file's name, and a name that matches no file is
        inert here -- `shouldDisableFile` is consulted per file and never the other way round. The
        wait underneath is what decides, not this list.

        `01-provision.sh` gets no line here: it is parameter-free, so anything it did would be
        done to every lane, and decision 4 says the everyday lane keeps k3s's controller exactly as
        the cluster has it.
        """
        path = "/etc/rancher/k3s/config.yaml"
        entry = 'disable: ["traefik", "traefik-crd"]'
        before = self.guest.run(command="cat %s 2>/dev/null" % shlex.quote(path), timeout=30)
        written = self.guest.run(
            # Drop any disable entry first, so a second run of this step rewrites one line rather
            # than leaving two lists the YAML parser has to choose between.
            #
            # The entry is passed as a quoted `printf` argument, not baked into the command with
            # `%s`. The first version of this line used `.format(f=path)` on a string that also
            # contained `printf %s` -- and `str.format` does not substitute `%`-placeholders, so the
            # shell received a literal `printf %s` with no argument and appended an empty line. The
            # file ended up holding only `write-kubeconfig-mode: 644`, k3s restarted happily, and the
            # bundled controller stayed deployed: the write reported ok because nothing it ran failed,
            # and the lane's next assert caught it by reading the cluster instead of the exit code.
            command=("touch {f} 2>/dev/null || sudo touch {f}; sudo sed -i -E '/^[[:space:]]*disable:/d' {f}; "
                     "grep -qE '^[[:space:]]*write-kubeconfig-mode:' {f} || "
                     "printf 'write-kubeconfig-mode: 644\\n' | sudo tee -a {f} >/dev/null; "
                     "printf '%s\\n' " + shlex.quote(entry) + " | sudo tee -a {f} >/dev/null").format(
                f=shlex.quote(path)),
            timeout=60)
        after = self.guest.run(command="sudo cat %s" % shlex.quote(path), timeout=30)
        restarted = self.guest.run(command="sudo systemctl restart k3s", timeout=180)
        shown = (after.stdout or before.stdout or "").strip().replace("\n", " | ")
        self.expect("routing-k3s-traefik-disabled",
                    written.ok and restarted.ok and "disable:" in (after.stdout or "")
                    and "write-kubeconfig-mode" in (after.stdout or ""),
                    "config.yaml is %r; restarted k3s with it=%s. The mechanism is k3s's own disable "
                    "entry (active uninstall of what is already installed), not a .skip file, not a "
                    "helm uninstall, not a kubectl delete -- the lane deletes no release it did not "
                    "install (plan decision 3). Before: %r" % (shown, restarted.ok,
                                                               (before.stdout or "(no config file)").strip()))
        if not (written.ok and restarted.ok):
            raise StageFailed("could not disable k3s's bundled Traefik: write=%s restart=%s | %s"
                              % (written.ok, restarted.ok, restarted.excerpt(200)))
        self.wait("routing-k3s-traefik-stopped",
                  "no release running a traefik chart, and no svclb pod holding node ports 80/443",
                  self._k3s_traefik_stopped, timeout=ROUTING_TRAEFIK_STOP_WAIT_S,
                  cls="infra", interval=5)

    def _k3s_traefik_stopped(self):
        """The two things that have to be gone before a Service is worth creating.

        The release, because it is what the chart installed and what k3s's uninstall is working on;
        the `svclb-*` pods, because they are the thing that actually holds the ports. ServiceLB
        creates a daemonset pod per LoadBalancer Service with `hostPort` on the Service's port, and a
        second LoadBalancer Service whose ports are taken does not fail -- it stays Pending and the
        endpoints of the controller we installed never appear. Reading only the release would be
        reading half the race: the release can report itself gone while the daemonset is still being
        torn down.

        `svclb-` is not scoped to the old controller's pods because at this point in the lane there
        is no LoadBalancer Service of ours to tell them apart. Everything the standard group installs
        later is ClusterIP, so the next `svclb-` pod this lane sees belongs to kubementat.
        """
        rows, error = self._routing_traefik_releases()
        if error:
            return False, "helm would not answer whether k3s had finished: %s" % error
        try:
            pods = self.cluster.pod_list()
        except asserts.KubectlError as exc:
            return False, "the cluster will not say which pods exist yet: %s" % exc
        holders = ["%s/%s(%s)" % ((pod.get("metadata") or {}).get("namespace"),
                                  (pod.get("metadata") or {}).get("name"),
                                  ((pod.get("status") or {}).get("phase") or "unknown"))
                   for pod in self.cluster.pod_list()
                   if str((pod.get("metadata") or {}).get("name") or "").startswith("svclb-")]
        if rows or holders:
            return False, "still on the cluster: %s; still holding node ports: %s" % (
                ", ".join(self._release_description(row) for row in rows) or "no traefik release",
                ", ".join(holders) or "no svclb pod")
        return True, ("k3s's controller is off the cluster: no release running a traefik chart, no "
                      "svclb pod on any node (%s release(s) on the cluster now)" % len(self.cluster.helm_list()))

    # ------------------------------------------------ 3. what the two installs have to have done

    def _expect_kmt_install_installed_no_controller(self):
        """`kmt install` is not a routing command, and here that can be proved rather than asserted.

        The cluster had no Traefik at all when this capability ran -- the step above just took the
        node's own away -- so "installed nothing" and "installed nothing new" are the same question,
        and the answer is a read. On every other lane they are not the same question, which is why
        this assert belongs to this lane and not to `stage_install` in general.
        """
        rows, error = self._routing_traefik_releases()
        try:
            bindings = self._routing_classes_on_traefik()
            class_error = ""
        except asserts.KubectlError as exc:
            bindings, class_error = {}, str(exc)
        namespace = self._routing_configuration()["namespace"]
        present = namespace in self.cluster.namespaces()
        clean = not rows and not error and not bindings
        # The namespace is reported and not decided on: creating it would be untidy, installing a
        # controller is what this assert is about, and the two facts do not come from the same place.
        self.expect("routing-kmt-install-installed-no-controller", clean,
                    "after `kmt install` on a cluster the lane had just taken its controller off: "
                    "traefik releases=%s, IngressClasses bound to %s=%s, namespace %s present=%s%s" % (
                        ", ".join(self._release_description(row) for row in rows) or "none",
                        ROUTING_TRAEFIK_CONTROLLER, ", ".join(sorted(bindings)) or "none", namespace, present,
                        (" | " + error) if error else ((" | " + class_error) if class_error else "")))

    def _install_routing_and_expect_it(self):
        """`kmt routing install`, then the five facts it is supposed to leave behind.

        Nothing here aborts the stage: a red `cap:routing.install` with the cluster's own answer
        underneath it says more than a stage that stopped early, and the asserts are reads rather
        than assumptions, so they stay honest whatever the command did.
        """
        result = self.km("routing.install", timeout=LATER_GROUP_TIMEOUT_S)
        self.record_result("cap:routing.install", "product", result, detail_prefix="group=routing ")
        self._expect_routing_release()
        self._expect_single_traefik_class()
        self._expect_service_endpoints()
        self._expect_entrypoint_redirect()
        self._expect_cert_manager_connected()

    def _expect_routing_release(self):
        """One release, in the namespace the *configuration* names, reporting `deployed`.

        The namespace comes from the config file and not from a literal, so a lane that ran against a
        renamed `TRAEFIK_DEPLOYMENT_NAMESPACE` still looks for the release where the product put it.
        """
        configuration = self._routing_configuration()
        rows, error = self._routing_traefik_releases()
        in_place = [row for row in rows if row.get("namespace") == configuration["namespace"]]
        elsewhere = [row for row in rows if row not in in_place]
        deployed = [row for row in in_place if row.get("status") == "deployed"]
        self.expect("routing-traefik-release-deployed",
                    bool(deployed) and not error and not elsewhere,
                    "a release running a traefik chart in %s: %s%s. The class config names is %r%s%s."
                    % (configuration["namespace"],
                       ", ".join(self._release_description(row) for row in deployed) or "none",
                       (" | also on the cluster somewhere else: "
                        + ", ".join(self._release_description(row) for row in elsewhere)) if elsewhere else "",
                       (" | " + error) if error else "",
                       configuration["ingress_class"],
                       " (read from static.json)" if configuration["from_config"]["TRAEFIK_DEPLOYMENT_NAMESPACE"]
                       else " (default: the key is not in static.json)"))

    def _expect_single_traefik_class(self):
        """`traefik` is the *only* class bound to Traefik's controller, and it is bound to that.

        Two classes carrying the same controller string is the state that makes Ingress status
        flap -- two classes bound to the same controller -- and a chart that installs a class named
        something else is the state that makes the solver point at nothing. Both are decided from
        `spec.controller`, because a name tells you what somebody wanted and a controller tells you
        who serves it.
        """
        try:
            all_classes = self.cluster.ingress_classes()
        except asserts.KubectlError as exc:
            self.expect("routing-single-traefik-ingress-class", False, "no IngressClass could be read: %s" % exc)
            return
        bindings = {name: controller for name, controller in all_classes.items()
                    if controller == ROUTING_TRAEFIK_CONTROLLER}
        configured = self._routing_configuration()["ingress_class"]
        others = {name: controller for name, controller in all_classes.items()
                  if controller and controller != ROUTING_TRAEFIK_CONTROLLER}
        self.expect("routing-single-traefik-ingress-class",
                    list(bindings) == [configured],
                    "classes bound to %s: %s. The class config names is %r, and the cluster also has "
                    "%s -- every class, with what binds it: %s" % (
                        ROUTING_TRAEFIK_CONTROLLER,
                        ", ".join("%s -> %s" % (name, controller) for name, controller in sorted(bindings.items()))
                        or "none",
                        configured,
                        ", ".join("%s -> %s" % (name, controller) for name, controller in sorted(others.items()))
                        or "no other class on the cluster",
                        json.dumps(dict(sorted(all_classes.items())), sort_keys=True)))

    def _expect_service_endpoints(self):
        """The Service's endpoints resolve to pods that are Traefik, read as endpoints.

        A selector match is the product's opinion. `kubectl get endpoints -o json` is what the data
        plane is told to route to, and its `targetRef` is a pod object rather than a guess about one.
        Then each of those pods is read once: `pod running: true` and an image that is Traefik is a
        controller; a pod that is Running and is not Traefik is the thing this assert exists to catch.
        """
        namespace = self._routing_configuration()["namespace"]
        try:
            endpoints = self.cluster.endpoints(namespace, "traefik")
        except asserts.KubectlError as exc:
            self.expect("routing-service-endpoints-resolve-to-traefik-pods", False,
                        "no Endpoints object for %s/traefik: %s" % (namespace, exc))
            return
        named = [((address.get("targetRef") or {}).get("name") or "")
                 for address in endpoints["ready"] if address.get("ip")]
        unresolved = [name for name in named if not name]
        pods, not_traefik, not_running = [], [], []
        for name in sorted(set(named)):
            try:
                pod = self.cluster.json("get", "pod", name, "-n", namespace, "-o", "json", timeout=60)
            except asserts.KubectlError as exc:
                pods.append("%s: unreadable (%s)" % (name, exc))
                continue
            containers = ((pod.get("spec") or {}).get("containers") or [])
            images = " ".join(container.get("image") or "" for container in containers)
            phase = ((pod.get("status") or {}).get("phase") or "unknown")
            if phase != "Running":
                not_running.append("%s=%s" % (name, phase))
            if "traefik" not in images:
                not_traefik.append("%s=%s" % (name, images or "no image"))
            pods.append("%s/%s image=%s phase=%s" % (namespace, name, images, phase))
        self.expect("routing-service-endpoints-resolve-to-traefik-pods",
                    bool(named) and not unresolved and not not_running and not not_traefik,
                    "%s/traefik resolves to %d address(es): %s; not ready addresses=%d; %s%s" % (
                        namespace, len(endpoints["ready"]), "; ".join(pods) or "no target pod",
                        len(endpoints["not_ready"]),
                        ("not Running: %s; " % ", ".join(not_running)) if not_running else "",
                        ("not a traefik image: %s" % ", ".join(not_traefik)) if not_traefik else ""))

    def _expect_entrypoint_redirect(self):
        """The node's own port 80 answers with the redirect, before any Ingress of ours exists.

        A router-level `ssl-redirect` would only answer for one host; this is the entrypoint
        redirect the values file sets, which is the cluster-wide answer that replaced nginx's
        `ssl-redirect`/`force-ssl-redirect` pair. Asked for with no SNI and no host of
        ours, so it cannot accidentally be measuring a router the lane created.
        """
        passed, detail, attempts = self.cluster.wait(
            "the web entrypoint answers with a redirect to https",
            lambda: self._entrypoint_redirect(""), timeout=ROUTING_ENTRYPOINT_WAIT_S, interval=4)
        self.record("assert:routing-entrypoint-redirects-http", "product", passed, detail, attempts=attempts)

    def _entrypoint_redirect(self, host):
        """`<status, where it points>` for a plain HTTP request on the node's port 80.

        `host` empty means ask for the node address itself; anything else is resolved to that
        address by `--resolve`, so nothing in the lane is asked to resolve a name through DNS.
        """
        address = self.state.get("vm", {}).get("ip") or "127.0.0.1"
        target = host or address
        result = self.guest.run(
            # `-sS` rather than `-s`: the silent form swallows the curl diagnostic, and a detail that
            # says only "no status, headers=nothing" cannot be told apart from a controller that is
            # answering with empty headers. The exit code says which of the two it was.
            #
            # The URL is the argument and not part of the format string around it. It used to be both
            # at once -- `"... --resolve %s http://%s/" % (..., shlex.quote("http://%s/" % target))`
            # -- and the second substitution therefore landed inside the first, producing
            # `http://http://192.168.122.75//`. curl reads the host out of that as the literal word
            # `http`, so the probe asked nothing of the cluster: measured on lane routing at
            # 20261028T202014Z as `GET http://192.168.122.75/ -> no status (curl exit 6: curl: (6)
            # Could not resolve host: http)`. The same one-line shape is what made the entrypoint
            # redirect row red on the four earlier runs too -- their `no status, headers=nothing` is
            # the same probe with its diagnostic swallowed -- and it means the diagnosis carried into
            # this harness (that the wait window was shorter than ServiceLB takes to bind the node
            # ports) was never the cause: no run before this one had sent the request well-formed.
            # The other probe in this file (the TLS one, `curl -ksS ... https://%s/`) is built
            # correctly and is the model: quote the URL, then place it.
            command="curl -sSI --max-time 10 --resolve %s %s" % (
                shlex.quote("%s:80:%s" % (target, address)), shlex.quote("http://%s/" % target)),
            timeout=30)
        headers = (result.stdout or "").lower()
        status = ""
        for line in (result.stdout or "").splitlines():
            if line.upper().startswith("HTTP/"):
                status = line.split()[1] if len(line.split()) > 1 else line
        redirect = status in ("301", "302", "308") and "location: https://" in headers
        return redirect, "GET http://%s/ -> %s%s, headers=%s" % (
            target, status or "no status",
            # `exit_code`, not `returncode`: `harness.guest.Result` carries the ssh exit that way
            # (`guest.py:39`), and this branch only runs when curl failed, so a wrong attribute here
            # is a crash in the one detail a red is read from. Measured: lane routing at
            # 20261028T201109Z aborted stage 02 (and every stage after it) with
            # `AttributeError: 'Result' object has no attribute 'returncode'` on the first probe that
            # found node port 80 not yet bound -- 1 s after `routing-service-endpoints-resolve-to-
            # traefik-pods` went green, so the wait helper never got its retry, and 25 asserts of the
            # lane were never run. The prior run's equivalent detail printed the same fact without
            # the exit code, which is why nothing caught this before it ate a whole 30-minute lane.
            (" (curl exit %s: %s)" % (result.exit_code,
                                      (result.stderr or "").strip().splitlines()[-1][:120]))
            if not result.ok else "",
            headers.strip().replace("\n", " | ")[:200] or "nothing")

    def _expect_cert_manager_connected(self):
        """cert-manager is installed, both issuers are `Ready`, and the solver names the class Traefik serves.

        This is the lane's reason for existing. Two things made the old state wrong and both are read
        here rather than argued: cert-manager's solver used to set `class: nginx`, which writes the
        `kubernetes.io/ingress.class` annotation that Traefik's default provider does not read, so a
        challenge got an Ingress nothing routed and issuance failed forever without an error; and the
        class it names has to be the one the cluster actually binds, not the one a template remembers.

        **What is decided here, and what is only looked at.** Decided: the controller pods run; each
        named issuer exists; each `spec.acme.server` is the directory that name is supposed to point
        at; the ACME contact is an address; the solver uses `ingressClassName` and not the retired
        `class`; and the class the solver names is bound to Traefik's controller. Reported and not
        decided: the `Ready` condition. This row used to read `Ready` into its detail and never put it
        in the verdict, so it passed while both ClusterIssuers were saying `Ready=False` (measured:
        20261028T194038Z, whose detail printed `Ready=False` for both and went green). The reason it
        still is not a verdict is not that the failure might be excusable -- it is that `Ready` is the
        half of the condition the CA answers rather than the half kubementat writes. An Issuer's only
        condition type is `Ready` (upstream `pkg/apis/certmanager/v1/types_issuer.go`: "known values
        are (`Ready`)", one constant), and its True depends on reaching Let's Encrypt and registering
        an account. This lane runs hermetic on purpose -- `config.py:167` puts the contact on the
        reserved `.invalid` TLD for the same reason `BASE_DOMAIN` does, and the prior run's message
        proves what that does: `Failed to register ACME account: 400 ... contact email has invalid
        domain: Domain name does not end with a valid public suffix (TLD)`. So `Ready=False` here is
        the environment, is stated in the detail every run, and is not evidence about kubementat.
        Deciding on it would make a permanently red row; deciding on the account conditions upstream
        added later (`AcmeAccountPending`, `AcmeUserEmailsNotVerified`, `Expired`) would be deciding on
        the CA too, and on text an upstream reword is free to change.
        """
        namespace = self._routing_configuration()["cert_manager_namespace"]
        pods = [pod for pod in self.cluster.pod_list()
                if (pod.get("metadata") or {}).get("namespace") == namespace]
        not_running = ["%s=%s" % ((pod.get("metadata") or {}).get("name"),
                                  ((pod.get("status") or {}).get("phase") or "unknown"))
                       for pod in pods if ((pod.get("status") or {}).get("phase") or "") != "Running"]
        try:
            issuers = self.cluster.json("get", "clusterissuer", "-o", "json", timeout=90)["items"]
        except asserts.KubectlError as exc:
            self.expect("routing-cert-manager-connected-to-traefik", False,
                        "no ClusterIssuer could be read: %s" % exc)
            return
        bound = self._routing_class_bindings()
        by_name = {((issuer.get("metadata") or {}).get("name") or ""): issuer for issuer in issuers}
        report = []
        verdicts = []
        for name in ROUTING_ISSUERS:
            issuer = by_name.get(name)
            if not issuer:
                verdicts.append(False)
                report.append("%s: absent" % name)
                continue
            acme = ((issuer.get("spec") or {}).get("acme") or {})
            conditions = {condition.get("type"): condition for condition in
                          ((issuer.get("status") or {}).get("conditions") or [])}
            ready = conditions.get("Ready") or {}
            # Reported, never decided -- and said in the row so a reader who skims only the verdict
            # line cannot mistake it for one. The message is kept because it is the one place that
            # says *why* the CA refused, which is what separates the environment's limitation from a
            # controller that is broken.
            observed = "Ready=%s%s" % (ready.get("status") or "unset",
                                       " -- %s" % (ready.get("message") or "no message")
                                       if (ready.get("status") or "").lower() != "true" else "")
            problems = []
            server = str(acme.get("server") or "")
            if server != ROUTING_ISSUER_SERVERS[name]:
                problems.append("spec.acme.server is %r and not %r, which is the swap-or-collision"
                                " of the two directories -- they share one ACME account, the rate-limit"
                                " penalty is per account, and a collision shows up as certificates"
                                " failing long after the change that caused it"
                                % (server, ROUTING_ISSUER_SERVERS[name]))
            email = str(acme.get("email") or "")
            if not _is_an_address(email):
                problems.append("the ACME contact is not an address (present=%s, %d character(s)):"
                                " CLUSTER_MANAGER_EMAIL in platform_config/%s/static.json is the key to"
                                " edit. The product refuses an empty or REPLACEME value and ships"
                                " anything else, and Let's Encrypt rejects a malformed contact at"
                                " registration, after which the issuer sits in a state nothing else"
                                " surfaces" % (bool(email), len(email), config.ENVIRONMENT))
            solver = (((((acme.get("solvers") or [{}])[0]
                         .get("http01") or {}).get("ingress") or {})))
            named = solver.get("ingressClassName")
            if named is None and solver.get("class"):
                # The retired field. It is not a cosmetic difference: `class` writes the
                # `kubernetes.io/ingress.class` annotation and Traefik's default Kubernetes Ingress
                # provider does not read annotations it was not told about, so the solver Ingress is
                # created, routed by nobody, and the challenge never resolves.
                problems.append("the solver still sets the retired `class`=%r, which writes the"
                                " annotation Traefik's default provider does not read"
                                % solver["class"])
            elif named not in bound:
                problems.append("solver ingressClassName=%r is not bound to %s (bound: %s)"
                                % (named, ROUTING_TRAEFIK_CONTROLLER,
                                   ", ".join(sorted(bound)) or "nothing"))
            verdicts.append(not problems)
            report.append("%s: %s | reported: %s" % (
                name,
                ("server ok, solver ingressClassName=%r -> %s" % (named, bound[named])
                 if not problems else " ".join(problems)),
                observed or "no status conditions read"))
        self.expect("routing-cert-manager-connected-to-traefik",
                    bool(pods) and not not_running and all(verdicts) and bool(verdicts),
                    "asserted: pods Running, both issuers present, server per issuer, an address-shaped"
                    " contact, solver bound to Traefik's class. reported and not decided: `Ready`, the"
                    " CA-answered half (see the method's docstring). pods in %s: %s%s; issuers: %s" % (
                        namespace, len(pods),
                        (" | not Running: %s" % ", ".join(not_running)) if not_running else "",
                        "; ".join(report)))

    def _routing_class_bindings(self):
        """`{class: controller}` for the classes bound to Traefik, `{}` if the read fails.

        A solver that names nothing bound is a red in the assert above, so the empty answer here is
        not silent -- it is read as 'nothing is bound' and reported as such.
        """
        try:
            return self._routing_classes_on_traefik()
        except asserts.KubectlError:
            return {}

    # -------------------------------------------------------- 4. a certificate that terminates TLS

    def feature_routing_ingress(self):
        """Issue a certificate without DNS, and make Traefik terminate TLS with it.

        What this feature asks for, and what a lane cannot ask for: a real HTTP-01 against
        Let's Encrypt needs a host the CA can resolve. An isolated VM has none, so issuance there
        fails for a reason that is about the network and not about kubementat -- and a lane that
        asserted it would be reporting a DNS failure as a product failure, which is how this ticket
        found the wording this method replaces. So the lane asserts the half it can
        prove, with the fixture names in `config.ROUTING_LANE`:

        * cert-manager runs and the two issuers it applies name the class Traefik serves;
        * a certificate issues through a lane-local self-signed Issuer, which needs no challenge and
          therefore no resolvable host, and reaches `Ready`, and writes the Secret;
        * Traefik terminates TLS *with that Secret*: the certificate Traefik presents on the node's
          443 is byte-for-byte the one cert-manager issued, matched by fingerprint rather than by
          trust, and the Ingress answers over https.
        * port 80 for that host answers the redirect rather than the content.

        What is deliberately not claimed: that Let's Encrypt was reached, that an ACME account was
        registered, or that renewal works. `cert-manager.io/cluster-issuer` is deliberately absent
        from the Ingress for exactly that reason -- pointing at that issuer would have this lane fail
        on DNS and call it a TLS result.
        """
        fixtures = config.ROUTING_LANE
        namespace = fixtures["namespace"]
        ingress_class = self._routing_configuration()["ingress_class"]
        this = self._routing_https_fixture_manifests(namespace, ingress_class)
        applied = self.cluster.apply(this, namespace=namespace)
        self.artifacts.log(self.stage, "routing-ingress-fixture.yaml", this)
        self.expect("routing-ingress-fixture-applied", applied.ok,
                    "applied the backend, the self-signed Issuer, the Certificate and the Ingress in "
                    "%s: %s" % (namespace, applied.excerpt(300) if not applied.ok else "ok"))
        if not applied.ok:
            return
        try:
            self._expect_certificate_issued(namespace, ingress_class)
            self._expect_tls_terminated_by_traefik(namespace, ingress_class)
        finally:
            self.state["routing_fixture_cleanup"] = self._delete_routing_fixture(namespace)

    def _routing_https_fixture_manifests(self, namespace, ingress_class):
        """The four objects the TLS half of the lane reads its facts out of.

        One YAML, applied once, so that a failure says which of the four it is. The Ingress names the
        class so the assert does not depend on what else in the cluster claims a default class. The
        other half of kubementat's answer -- `ingressClass.isDefaultClass: true`, an Ingress that names
        nothing and is still routed -- is what the shipped routing installs, and no lane assert reads
        it yet.
        """
        fixtures = config.ROUTING_LANE
        return "\n".join([
            "apiVersion: apps/v1",
            "kind: Deployment",
            "metadata:",
            "  name: %s" % fixtures["backend"],
            "spec:",
            "  replicas: 1",
            "  selector:",
            "    matchLabels:",
            "      app: %s" % fixtures["backend"],
            "  template:",
            "    metadata:",
            "      labels:",
            "        app: %s" % fixtures["backend"],
            "    spec:",
            "      containers:",
            "      - name: echo",
            "        image: %s" % fixtures["image"],
            "        ports:",
            "        - containerPort: 80",
            "---",
            "apiVersion: v1",
            "kind: Service",
            "metadata:",
            "  name: %s" % fixtures["backend"],
            "spec:",
            "  selector:",
            "    app: %s" % fixtures["backend"],
            "  ports:",
            "  - port: 80",
            "    targetPort: 80",
            "---",
            "# A self-signed Issuer is the whole of the DNS problem: it signs from a CA it creates in",
            # the lane, so no challenge is ever solved and no host ever has to resolve.
            "apiVersion: cert-manager.io/v1",
            "kind: Issuer",
            "metadata:",
            "  name: %s" % fixtures["issuer"],
            "spec:",
            "  selfSigned: {}",
            "---",
            "apiVersion: cert-manager.io/v1",
            "kind: Certificate",
            "metadata:",
            "  name: %s" % fixtures["certificate"],
            "spec:",
            "  secretName: %s" % fixtures["certificate"],
            "  issuerRef:",
            "    name: %s" % fixtures["issuer"],
            "    kind: Issuer",
            "  dnsNames:",
            "  - %s" % fixtures["host"],
            "---",
            "# No `cert-manager.io/cluster-issuer`: that annotation is how the platform asks for a",
            "# real certificate, and a real ACME solver needs an address Let's Encrypt can reach.",
            "apiVersion: networking.k8s.io/v1",
            "kind: Ingress",
            "metadata:",
            "  name: %s" % fixtures["ingress"],
            "spec:",
            "  ingressClassName: %s" % ingress_class,
            "  tls:",
            "  - secretName: %s" % fixtures["certificate"],
            "    hosts:",
            "    - %s" % fixtures["host"],
            "  rules:",
            "  - host: %s" % fixtures["host"],
            "    http:",
            "      paths:",
            "      - path: /",
            "        pathType: Prefix",
            "        backend:",
            "          service:",
            "            name: %s" % fixtures["backend"],
            "            port:",
            "              number: 80",
            "",
        ])

    def _expect_certificate_issued(self, namespace, ingress_class):
        """The Certificate reached `Ready`, and the Secret it writes is a TLS Secret.

        Two facts, read from the objects rather than from cert-manager's log: the Request's `Ready`
        condition -- with the reason and message when it is not, because a Certificate that will not
        issue says why and that is the useful half -- and the Secret's `type`/`data` keys, which is
        what makes the next assert about *that* Secret and not about something Traefik found.
        """
        fixtures = config.ROUTING_LANE
        passed, detail, attempts = self.cluster.wait(
            "the Certificate is Ready",
            lambda: self._certificate_condition(namespace), timeout=240, interval=4)
        secret = self.cluster.kubectl("get", "secret", fixtures["certificate"], "-n", namespace,
                                      "-o", "json", timeout=60)
        shape, secret_detail = False, "no Secret named %s/%s: %s" % (namespace, fixtures["certificate"],
                                                                     secret.excerpt(160))
        if secret.ok:
            try:
                document = json.loads(secret.stdout)
            except ValueError:
                document = {}
            keys = sorted((document.get("data") or {}).keys())
            shape = document.get("type") == "kubernetes.io/tls" and "tls.crt" in keys and "tls.key" in keys
            secret_detail = "type=%s keys=%s" % (document.get("type"), keys)
        self.record("assert:routing-certificate-issued-without-dns", "product",
                    passed and shape,
                    "%s; Secret %s/%s: %s. Issued by the lane's own self-signed Issuer, so no "
                    "challenge was solved and no host had to resolve; the class the platform's real "
                    "issuers solve through is still %s" % (detail, namespace, fixtures["certificate"],
                                                            secret_detail, ingress_class),
                    attempts=attempts)

    def _certificate_condition(self, namespace):
        fixtures = config.ROUTING_LANE
        try:
            document = self.cluster.json("get", "certificate", fixtures["certificate"], "-n", namespace,
                                         "-o", "json", timeout=60)
        except asserts.KubectlError as exc:
            return False, "the Certificate is not readable yet: %s" % exc
        conditions = ((document.get("status") or {}).get("conditions") or [])
        ready = [condition for condition in conditions if condition.get("type") == "Ready"]
        if any(condition.get("status") == "True" for condition in ready):
            return True, "Certificate %s is Ready" % fixtures["certificate"]
        said = "; ".join("%s=%s(%s: %s)" % (condition.get("type"), condition.get("status"),
                                            condition.get("reason") or "no reason",
                                            (condition.get("message") or "no message")[:120])
                         for condition in conditions) or "no conditions yet"
        return False, "Certificate %s not Ready: %s" % (fixtures["certificate"], said)

    def _expect_tls_terminated_by_traefik(self, namespace, ingress_class):
        """The certificate Traefik serves on 443 is the one cert-manager wrote into that Secret.

        Fingerprint, not trust. The handshake happens with SNI set to the host and with `--resolve`
        pinning it to the node, because DNS is not available to anything here; what decides the claim
        is that the leaf Traefik presents and the `tls.crt` cert-manager wrote hash to the same
        value. A Traefik that had not loaded the Secret would present its own default certificate and
        hash differently, which is the failure this assert is drawn tight enough to catch.

        Then the same Ingress must answer: 200 and the backend's own body over https, and the
        redirect instead of the content over 80.

        All three reads happen inside one wait, not three single shots. The first run to get this far
        reported `status=503 body='no available server'` while the backend Pod was Running and its
        EndpointSlice was populated -- measured directly on the kept VM, where `kubectl get endpoints`
        showed the address and the pod was 1/1 Ready. That is Traefik's own behaviour, not a defect:
        the router is built from the Ingress before the provider has registered the service's
        servers, and "503 with no available server" is what the proxy says while it is working on it.
        Asking once was asking about a transition and reporting it as a verdict. It is also the last
        remaining instance of the mistake this harness keeps making -- asserting a network fact without
        giving the network a window -- while the two entrypoint probes beside it already waited.
        """
        fixtures = config.ROUTING_LANE
        address = self.state.get("vm", {}).get("ip") or "127.0.0.1"
        host = fixtures["host"]
        passed, detail, attempts = self.cluster.wait(
            "https terminates with the issued certificate and the backend answers",
            lambda: self._tls_termination_attempt(namespace, host, address, ingress_class),
            timeout=180, interval=4)
        self.record("assert:routing-traefik-terminates-tls-with-the-issued-secret", "product",
                    passed, detail, attempts=attempts)
        redirected_passed, redirected_detail, redirected_attempts = self.cluster.wait(
            "port 80 for the same host redirects rather than serving",
            lambda: self._entrypoint_redirect(host), timeout=120, interval=4)
        self.record("assert:routing-ingress-redirects-on-80", "product", redirected_passed,
                    "port 80 for the same host: %s" % redirected_detail, attempts=redirected_attempts)

    def _tls_termination_attempt(self, namespace, host, address, ingress_class):
        """One full pass of the TLS claim: what was issued, what is served, what answering gets.

        Returns `(passed, detail)`. A failing pass prints all three readings rather than the first
        mismatch, because the three failing together and one of them failing alone are different
        problems -- the first is Traefik not having loaded anything, the second is it having loaded
        the wrong thing.
        """
        fixtures = config.ROUTING_LANE
        issued = self.guest.run(
            # The decode happens once, on the pipe, with no argument of its own. This used to read
            # `... | %s base64 -d ...` while also substituting the string `"base64 -d"` for that
            # `%s`, which produced `base64 -d base64 -d`: a *decode* of the arguments meant nothing,
            # so base64 tried to open a file named `base64`, its error went to the `2>/dev/null` that
            # was already on the line, and openssl reported `Could not find certificate from <stdin>`.
            # Measured on lane routing at 20261028T202014Z, where the certificate itself was `Ready`
            # and the Secret was present and well-formed on the Python read -- so the assert said the
            # Secret was unreadable while the cluster said it was fine, and pointed its finger at
            # Traefik for serving a certificate that had not been misread at all.
            #
            # No `2>/dev/null` now, for the same reason: a suppressed stderr is what kept this four
            # runs long enough to be blamed on the product.
            command="kubectl get secret %s -n %s -o json | jq -r '.data[\"tls.crt\"]' | base64 -d "
                    "| openssl x509 -noout -fingerprint"
                    % (shlex.quote(fixtures["certificate"]), shlex.quote(namespace)),
            timeout=60)
        expected = _fingerprint(issued.stdout)
        served = self.guest.run(
            command="echo | openssl s_client -connect %s -servername %s 2>/dev/null | openssl x509 -noout -fingerprint"
                    % (shlex.quote("%s:443" % address), shlex.quote(host)),
            timeout=60)
        presented = _fingerprint(served.stdout)
        answered = self.guest.run(
            # `%%{http_code}` -- doubled, and it has to be. This string is built with `%`, and
            # Python parses `%{` as the start of a named mapping conversion, not as a literal: with
            # the single percent it raised `TypeError: not enough arguments for format string` inside
            # the TLS feature and took the whole stage with it. Doubling it yields the literal
            # `%{http_code}` that curl needs to write its own status line.
            command="curl -ksS --max-time 15 --resolve %s https://%s/ -w '\\nKMT_HTTP_STATUS:%%{http_code}\\n'"
                    % (shlex.quote("%s:443:%s" % (host, address)), shlex.quote(host)),
            timeout=40)
        status = _grep_first(r"KMT_HTTP_STATUS:(\d+)", answered.stdout)
        body = (answered.stdout or "").split("KMT_HTTP_STATUS:")[0].strip()
        passed = bool(expected) and expected == presented and status == "200"
        return passed, ("https://%s/ (resolved to %s) served status=%s body=%r; the certificate Traefik "
                        "presented is %s and the Secret cert-manager wrote is %s%s. The Ingress names "
                        "class %s and kubementat's own issuer annotation is deliberately not on it."
                        % (host, address, status or "none", body[:80],
                           presented or "unreadable (%s)" % served.excerpt(120),
                           expected or "unreadable (%s)" % issued.excerpt(120),
                           "-- they are the same certificate" if expected and expected == presented else "",
                           ingress_class))

    def _delete_routing_fixture(self, namespace):
        """Remove what the TLS fixture added, and say what is left.

        The objects are namespaced, in `default`, and named for the lane, so nothing here is a
        mutation of anything the product owns -- and the uninstall asserts at the end of the stage run
        against a cluster that is not carrying the lane's own scaffolding.
        """
        fixtures = config.ROUTING_LANE
        deleted = self.cluster.kubectl(
            "delete", "ingress", fixtures["ingress"], "certificate", fixtures["certificate"],
            "issuer", fixtures["issuer"], "deployment", fixtures["backend"], "service", fixtures["backend"],
            "secret", fixtures["certificate"], "-n", namespace, "--ignore-not-found", "--wait=false",
            timeout=120)
        return deleted.ok

    # ---------------------------------------------------------------- 5. the two guards

    def feature_routing_guard(self):
        """Both halves of the rule: `kmt install` does not install Traefik, and a second
        `routing install` is a skip that deletes nothing.

        The first half is re-run live rather than read out of the install stage, because a cluster that
        already *has* Traefik is the state where the claim is worth something: on a cluster with none,
        "installed nothing" is nearly free to get right, and stage 02 already asserted that version of
        it (`routing-kmt-install-installed-no-controller`). Here it is the same command, minutes later,
        against a controller that exists -- and the verdict is that the release object did not move.

        The second half runs the command again and reads the same object. A skip that deleted
        something would show up twice: the revision would move, or the release would go and come back.
        So the identity of the release is the assert, and the cluster probe under it is what catches
        the thing a release identity cannot see -- an extra Secret, an extra pod, a second class.
        """
        self._expect_install_still_installs_nothing()
        self._expect_second_install_is_a_skip()
        self._expect_update_reports_the_crds_first()

    def _expect_install_still_installs_nothing(self):
        before = self._routing_release_identity()
        result = self.km("install", group=self.lane.install_groups[0], timeout=900)
        self.record_result("cap:install-inside-routing-guard", "product", result,
                           detail_prefix="group=%s " % self.lane.install_groups[0])
        after = self._routing_release_identity()
        rows, error = self._routing_traefik_releases()
        self.expect("routing-kmt-install-does-not-install-traefik",
                    bool(before) and before == after and not error,
                    "`kmt install %s` over a cluster that already runs kubementat's Traefik: releases "
                    "before=%s after=%s%s. Stage 02 asserted the same claim on a cluster that had no "
                    "controller at all (assert:routing-kmt-install-installed-no-controller)."
                    % (self.lane.install_groups[0], before or "none", after or "none",
                       (" | " + error) if error else ""))

    def _expect_second_install_is_a_skip(self):
        before = self._routing_release_identity()
        probe = self._routing_probe()
        result = self.km("routing.install", timeout=LATER_GROUP_TIMEOUT_S)
        self.record_result("cap:routing.install-second-pass", "product", result,
                           detail_prefix="group=routing ")
        after = self._routing_release_identity()
        delta = asserts.diff_sets(probe, self._routing_probe())
        output = result.output or ""
        said_not_applied = "was not applied" in output
        self.expect("routing-second-install-skips-and-deletes-nothing",
                    result.ok and bool(before) and before == after and said_not_applied,
                    "exit=%s; the release did not move: before=%s after=%s. The command said it is not "
                    "applying kubementat's Traefik=%s. Identity is namespace/name + chart + revision, so "
                    "a delete-and-recreate would read as a change here."
                    % (result.exit_code, before or "none", after or "none", said_not_applied))
        # k3s's own helm churn (`helm-install-*` jobs, `helm-operation-*` pods, all in kube-system)
        # is not evidence about this command, and it is why the probe drops the same namespaces the
        # idempotency check does rather than inventing a rule of its own.
        self.expect("routing-skip-left-the-cluster-alone", not delta,
                    "resource set / pod set / release / class delta across the second install "
                    "(kube-system excluded, the way the idempotency check excludes it): %s"
                    % (json.dumps(delta, sort_keys=True)[:400] if delta else "none"))

    def _expect_update_reports_the_crds_first(self):
        """The one half Helm refuses to do, asked for deliberately, and proved two ways.

        `--update` is not a re-apply: Helm applies `crds/` on install and never again, so the command
        takes the definitions itself, server-side, before it touches the release. The lane proves the
        apply happened by finding the evidence it leaves -- `kubectl apply --server-side` owns the
        fields it applies, so a traefik.io definition that carries a `kubectl` managed-fields entry was
        written by that path and not by the chart's install hook -- and proves the command *said* it
        ran before the release from the summary the command prints.

        What is not claimed: that the release was upgraded. `helmfile apply` diffs before it acts, so a
        cluster that already matches the values needs no upgrade and its revision does not move, and an
        assert that insisted on a moved revision would be a lane that goes red when nothing needed to
        happen. The code's ordering is what `tests/test_routing_update_applies_the_crds_before_the_release.py`
        locks down; the lane attests that the step ran and that the cluster still answers.
        """
        before = self._routing_release_identity()
        result = self.km("routing.install.update", timeout=LATER_GROUP_TIMEOUT_S)
        self.record_result("cap:routing.install-update", "product", result)
        crd_evidence, detail = self._crds_applied_server_side()
        answered, answered_detail = self._entrypoint_redirect("")
        still_deployed = bool(self._routing_release_identity())
        self.expect("routing-update-applied-the-crds-before-the-release",
                    result.ok and crd_evidence and "CustomResourceDefinitions were applied before the release"
                    in (result.output or ""),
                    "the command reported: %r; the definitions carry kubectl's server-side mark: %s; "
                    "release identity before=%s after=%s. The ordering itself is what the unit test "
                    "named above locks down -- a cluster that needs no upgrade is a cluster whose "
                    "revision does not move."
                    % (result.excerpt(180), detail, before or "none",
                       self._routing_release_identity() or "none"))
        self.expect("routing-update-left-the-cluster-answering",
                    answered and still_deployed,
                    "after --update: %s; release still there: %s" % (answered_detail, still_deployed))

    def _crds_applied_server_side(self):
        """Does any `traefik.io` definition carry a server-side-apply managed-fields entry?

        The read is one `kubectl get crd --show-managed-fields -o json`; the answer is
        `(something, detail)`, and the detail names the definitions so a red says whether the
        definitions are missing or merely untouched.

        `--show-managed-fields` is not optional. kubectl drops `managedFields` from its output by
        default (`--show-managed-fields=false`), so `-o json` alone returns objects with no ownership
        data to inspect, and the check then reports "none carry an Apply entry" over a cluster where
        all twenty-five were applied correctly. Measured on lane routing at 20261028T202014Z:
        `manager: none; none` across 25 definitions, with the release present and `--update` having
        run. That is a permanent red with no cause the owner can lift -- the same mistake this harness
        has made three times today, asserting a field it never asked the reader to return.

        The discriminator is `operation: Apply`, and getting this right matters more than it looks.
        A server-side apply records `Apply`; `Update` is what a client-side `kubectl apply`, a PUT or
        a POST records -- so asking for `Update` here was asking a question the product could never
        answer. This lane failed three runs in a row for exactly that reason: the CRDs were applied,
        and the check said they were not.

        The manager name is reported rather than required. `kubectl apply --server-side` from a
        multi-document stream can write either `kubectl` or `kubectl-slice`, depending on whether it
        split the request, and which one it used says nothing about whether the definitions landed.
        It is `Apply` that identifies the write.
        """
        try:
            items = self.cluster.json("get", "crd", "--show-managed-fields", "-o", "json",
                                      timeout=120).get("items") or []
        except asserts.KubectlError as exc:
            return False, "no CustomResourceDefinition could be read: %s" % exc
        traefik = [item for item in items
                   if str(((item.get("metadata") or {}).get("name")) or "").endswith("traefik.io")]
        if not traefik:
            return False, "the cluster serves no *traefik.io definition at all"
        owners, managers = [], set()
        for item in traefik:
            for field in item.get("metadata", {}).get("managedFields") or []:
                if field.get("operation") == "Apply":
                    managers.add(str(field.get("manager") or "no manager"))
                    owners.append((item["metadata"]["name"], field.get("time") or "no timestamp"))
                    break
        return bool(owners), ("%d of %d traefik.io definition(s) carry a server-side Apply entry "
                              "(manager: %s; %s)"
                              % (len(owners), len(traefik), ", ".join(sorted(managers)) or "none",
                                 "; ".join("%s@%s" % (name, when) for name, when in owners[:3])
                                 or "none"))

    def _routing_release_identity(self):
        """`{namespace/name: [chart, revision, status]}` for every Traefik release on the cluster.

        The identity is `namespace/name` plus chart and revision, because
        kubementat's Traefik and k3s's are two objects that share one name, and "nothing was
        overwritten" is a sentence about the revision. A delete-and-recreate changes the revision, a
        skip changes nothing, so one value answers both questions.
        """
        rows, _error = self._routing_traefik_releases()
        return {"%s/%s" % (row["namespace"], row["release"]):
                [str(row.get("chart")), str(row.get("revision")), str(row.get("status"))]
                for row in rows}

    def _routing_probe(self):
        """Everything a skip could change, as one snapshot of sorted string lists.

        `resource_sets` gives the namespaced objects the idempotency check already watches, minus the
        namespaces k3s churns in (`kube-system`, which is where `helm-install-*` and `helm-operation-*`
        live and where the ServiceLB pods it creates for our Service come and go). On top of that the
        two things a routing-only change would move and a `namespace`/`secret` set would not: which
        pods exist, and which class is bound to what.
        """
        excluded = config.IDEMPOTENCY_EXCLUDED_NAMESPACES
        probe = asserts.resource_sets(self.cluster, config.IDEMPOTENCY_KINDS, excluded)
        probe["pods-outside-the-excluded-namespaces"] = sorted(
            "%s/%s" % ((pod.get("metadata") or {}).get("namespace"), (pod.get("metadata") or {}).get("name"))
            for pod in self.cluster.pod_list()
            if (pod.get("metadata") or {}).get("namespace") not in excluded)
        try:
            bindings = self.cluster.ingress_classes()
        except asserts.KubectlError:
            bindings = {}
        probe["ingress-class-bindings"] = sorted("%s -> %s" % item for item in bindings.items())
        rows, error = self._routing_traefik_releases()
        probe["traefik-releases"] = sorted(self._release_description(row) for row in rows) + ["error: " + error] * bool(error)
        return probe

    # ------------------------------------------------------------------ 7. its own absence

    def _expect_routing_lane_absence(self):
        """What `kmt uninstall` is supposed to leave behind: nothing that was the lane's.

        Three reads, on this lane only, and all three are stronger here than on `full` for one
        reason: k3s's own controller was disabled before kubementat installed anything, so there is no
        node-owned release hiding behind the answer. That is what makes `helm list -A` mean something
        as a verdict, and it is why the check belongs to this lane rather than to the residue assert
        the everyday lanes already run.

        The CRD half is the part that stayed unverified -- nobody has checked the group name -- and
        the lane answers it. If the group had been guessed wrong the sweep would still log a clean
        uninstall while serving `IngressRoute` definitions out of objects nobody owns -- so what gets
        asserted is that no `traefik.io`/`hub.traefik.io` definition is left, and if that comes back
        red the detail says which ones and whose they look like.
        """
        if not self._routing_lane():
            return
        configuration = self.state.get("routing_configuration")
        if not configuration:
            self.expect("routing-uninstall-removed-its-namespace", False,
                        "the routing install never ran in this run, so there is no install for this "
                        "uninstall to be accountable for")
            return
        namespace = configuration["namespace"]
        passed, detail, attempts = self.cluster.wait(
            "namespace %s is gone" % namespace,
            lambda: (namespace not in self.cluster.namespaces(),
                     "namespace %s is still reported by the cluster" % namespace),
            timeout=NS_GONE_WAIT_S, interval=POLL_INTERVAL_S)
        self.record("assert:routing-uninstall-removed-its-namespace", "product", passed, detail,
                    attempts=attempts)

        rows, error = self.cluster.helm_list_rows()
        self.expect("routing-uninstall-left-the-cluster-with-no-releases", bool(not rows) and not error,
                    "helm list -A reports %s. The node's own Traefik releases were disabled before "
                    "kubementat installed anything, so this empty answer is the uninstall's and not "
                    "k3s's absence at baseline%s" % (
                        ", ".join(self._release_description(row) for row in rows) or "nothing",
                        (" | " + error) if error else ""))

        left = sorted(name for name in self.cluster.crds()
                      if name.endswith(".traefik.io") or name.endswith(".hub.traefik.io"))
        self.expect("routing-uninstall-left-no-traefik-crds", not left,
                    "CustomResourceDefinitions still serving after uninstall: %s. The sweep is gated on "
                    "no Traefik release running, which is the state this lane created by disabling "
                    "k3s's controller -- so a green here means both halves of the chart went, and a red "
                    "names the group that was missed."
                    % (", ".join(left) or "none"))

    # ------------------------------------------------------------- dispatching

    # The lifecycle, in the order it runs: stage name to method. A scope is a subset of these names, so
    # a lane that wants a shorter run says so in its scope and not here. This is the one place that says
    # what the stages are; the lane shapes say which of them they walk and what each of them costs.
    #
    # A stage listed here but reached by no scope is dead code with a name that reads like a stage, and
    # the reverse is a stage that no lane can run. The tests that keep the two lists agreeing are the
    # only thing between this comment and a drift that shows up on a lane run.
    STAGES = (
        ("01-provision", "stage_provision"),
        ("02-bootstrap", "stage_bootstrap"),
        ("02-install", "stage_install"),
        ("03-run", "stage_run"),
        ("04-rerun", "stage_rerun"),
        ("05-features", "stage_features"),
        ("99-teardown", "stage_teardown"),
    )

    @property
    def scope(self):
        return getattr(self.opts, "scope", config.DEFAULT_SCOPE)

    def stages(self):
        # Budgets come from the lane and can be overridden per stage by
        # PROVISION_TIMEOUT / E2E_TIMEOUT_<STAGE>. The scope decides which stages run.
        #
        # Two things about the order, because the first version of this got both wrong.
        #
        # A budget is read only for a stage the scope selected. Building all six tuples before filtering
        # made every lane carry every stage's budget, so the first lane that legitimately skipped the
        # run, re-run and feature stages died on a missing key before stage one -- and the error named a
        # dict, not the lane. Skipping stages is a lane's business; it is not a reason to borrow numbers
        # for work it never does.
        #
        # A stage the lane runs has to name its own budget. There is no suite-wide default, and that is
        # deliberate: every number in a lane's table is a measured one, and a default would let a stage
        # without a measurement run without a bound instead of telling somebody which stage and which
        # lane to go measure.
        selected = config.SCOPES[self.scope]
        out = []
        for name, function in self.STAGES:
            if name not in selected:
                continue
            try:
                seconds = self.lane.timeouts[name]
            except KeyError:
                raise KeyError("lane %r has no budget for stage %r, which its scope runs; measure it "
                               "and add it to that lane's timeouts" % (self.lane.name, name))
            out.append((name, getattr(self, function), self.opts.stage_timeout(name, seconds)))
        return out


# -------------------------------------------------------------------- helpers


def _signature(body, tokens):
    return asserts.signature_matches(body, tokens)


def _probe_verdict(read, body, matched):
    """What an unmatched tunnel probe actually measured, in words a reader can act on.

    `body=empty` was the whole reason this row stayed open: a refused connection and a bound port
    that answered with nothing both leave zero bytes behind, and they want different fixes. curl
    states the difference itself once `-S` is on, so the verdict comes off the exit code and the
    body rather than being inferred from a port that has already been stopped. The three names are
    deliberately distinct strings, and a test plants each one.
    """
    if matched:
        return "matched"
    if read is None:
        return "never-probed"
    if not read.ok:
        return "connection-failure"
    if not (body or "").strip():
        return "empty-body"
    return "body-without-tokens"


def _curl_verdict_note(read):
    """The exit code and, when there was one, curl's own words. Never empty on a failure.

    `Connection refused` is not the same sentence as `Connection reset` and the reader should be
    able to tell them apart from this line, so the stderr curl was told to keep is what gets quoted.
    """
    if read is None:
        return "no probe issued"
    stderr = (read.stderr or "").strip()
    return "exit %s%s" % (read.exit_code, " - %s" % stderr.replace("\n", " ") if stderr else "")


def _fingerprint(text):
    """The hex digits of an `openssl x509 -fingerprint` line, without the colons.

    `SHA256 Fingerprint=AA:BB:...` compared against `sha256 Fingerprint=aa:bb:...` is the same
    certificate, so the comparison is on digits and nothing else -- which is what makes it a proof
    rather than a text match. Nothing readable comes out of it either, so it is safe to print.
    """
    return "".join(character for character in (text or "").split("=")[-1] if character.isalnum())


def _release_id(release):
    """`namespace/name` for a helm release row. A name is not an identity on a shared cluster.

    Both spellings come from `asserts.Cluster.helm_list`, whose rows carry `namespace` and
    `release`; the defaults keep a row that answers nothing from raising inside an assert.
    """
    return "%s/%s" % (release.get("namespace", ""), release.get("release", ""))


def _listing_rows(output):
    """`{environment: [cells]}` out of `kmt environments list`, header excluded.

    Split on two-or-more spaces because that is how the command pads a column, and a row is a row
    only at the full five columns: a warning that arrives in the same stream is not a fifth
    environment, and reading it as one would make the assert fail for the shape of the log rather
    than for the shape of the configuration.
    """
    rows = {}
    for line in (output or "").splitlines():
        cells = [cell for cell in re.split(r"\s{2,}", line.strip()) if cell]
        if len(cells) >= 5 and not line.startswith("ENVIRONMENT"):
            rows[cells[0]] = cells
    return rows


def _declaration_wording(present, value):
    """How one key of one file reads, in the words the product's own refusal would use.

    Absent, null, empty and set are the four states a key can be in, and the harness has to describe
    them the same way the error does or an assert that quotes the file and an assert that quotes the
    command start meaning different things. The phrasing mirrors `cluster._declaration_state` for
    that reason, and for the same reason the harness spells it here rather than importing it: a lane
    that read the product's strings could not catch the product writing them differently.
    """
    if not present:
        return "is not set"
    if value is None:
        return "is null"
    if not str(value).strip():
        return "is empty"
    return "is set to %r" % (value,)


def _releases_in_groups(helmfile_text, groups):
    """The release names whose `group:` label is one of `groups`, commented-out releases excluded.

    A release is a top-level `- name:` line; its group is the first indented `group:` line after
    it. Repository entries share the `- name:` shape but carry no group, so the next `- name:`
    replaces them before a group line is ever seen.
    """
    found, name = [], None
    for line in (helmfile_text or "").splitlines():
        release = re.match(r"^- name:\s*(\S+)", line)
        if release:
            name = release.group(1)
            continue
        group = re.match(r"^\s+group:\s*(\S+)", line)
        if group and name:
            if group.group(1) in groups:
                found.append(name)
            name = None
    return sorted(set(found))


def _failing_pipelinerun_manifest(name):
    """A run that fails on its own step, using the image stage 03 already pulled."""
    return "\n".join([
        "apiVersion: tekton.dev/v1",
        "kind: PipelineRun",
        "metadata:",
        "  name: %s" % name,
        "spec:",
        "  pipelineSpec:",
        "    tasks:",
        "    - name: fail",
        "      taskSpec:",
        "        steps:",
        "        - name: fail",
        "          image: %s" % config.CI_IMAGE,
        "          script: |",
        "            #!/usr/bin/env bash",
        "            exit 1",
        "",
    ])


def _is_an_address(text):
    """True for something shaped like an email address, and only that.

    The rule the lane needs is not the rule the product runs. `routing.py::_cluster_manager_email`
    refuses empty and `REPLACEME*` and ships everything else, which is the right call for a command
    that cannot know anybody's domain -- but it is also why a contact that lost the word `REPLACEME`
    on its way into `static.json` still applies, registers nothing, and then sits in the issuer
    while `Ready=False` is the only trace of it, and that trace is not visible in a hermetic lane.
    Shape catches that case without deciding on the CA, which is the line this file's routing asserts
    are drawn on.

    What an address is here: one `@`, a non-empty local part, a non-empty domain that is not an address
    literal, has no empty label and neither leads nor ends with a dot, and ends in a label of at least
    two letters. No whitespace anywhere. What it deliberately is not asked: that the domain exists,
    resolves, or is a public suffix. That is what `Ready` measures and the lane reports rather than
    decides -- and `example.invalid`, which the harness config deliberately uses
    (`config.py:167`), passes every rule here.
    """
    if not text or any(character.isspace() for character in text):
        return False
    if "REPLACEME" in text.upper():
        return False
    local, separator, domain = text.rpartition("@")
    if not separator or not local or not domain:
        return False
    if domain.startswith("[") or domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    head, _dot, top_level = domain.rpartition(".")
    return bool(head) and len(top_level) >= 2 and top_level.isalpha()


def _grep_first(pattern, text):
    for match in re.finditer(pattern, text or "", re.IGNORECASE):
        for group in match.groups():
            if group:
                return group
    return ""
