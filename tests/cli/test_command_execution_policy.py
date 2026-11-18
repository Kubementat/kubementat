"""Tests for the command-execution rule: argv everywhere, one module doing the executing.

`kubementat.infrastructure.shell.run` is the only way kubementat starts a program
(`test_shell_run.py` is its contract). This file proves two things about everywhere else.

Two kinds of test live here on purpose:

**A rule**, asserted against the syntax tree of everything in `src/` — no `shell=True`,
no `os.chdir`, no direct `subprocess` outside `kubementat/shell.py`. AST rather than grep
because the prose in this repo's own comments mentions `shell=True` at least as often as
the code did.

**The call sites**, run against fake `helmfile`/`kubectl`/`.sh` binaries that record the
argv they were given, the directory they were run from, and what arrived on stdin. These
are what prove the conversions did not change a command line, which is the one thing a
mechanical argv rewrite can get wrong.
"""

import os
import shlex
from types import SimpleNamespace

import pytest
from asset_paths import values_path
from cluster_fixture import TARGET_DEV, UNREAD_ROOT, configuration_only_context

from kubementat import errors
from kubementat.automations.install import kubementat as install_slice
from kubementat.automations.install.kubementat import check_cluster_permissions
from kubementat.core import constants
from kubementat.core.config.keys import component_config_key
from kubementat.core.identity.target import kubectl_target_args
from kubementat.errors import PodNotFoundError
from kubementat.infrastructure.helm import HelmUtils
from kubementat.infrastructure.kubernetes import KubernetesUtils

# The marker line that makes an argv mistake visible: one `arg=[...]` per argv entry, so a
# shell that split `a;b` in two would change the count, not just the text.
RECORDER = """#!/bin/sh
{{
  echo "cwd=$PWD"
  echo "argc=$#"
{stdin_line}
  for arg in "$@"; do echo "arg=[$arg]"; done
}} >> "{log}"
{stdout}
exit {code}
"""

# What the fake does instead of answering: print the given text, or nothing at all.
SILENCE = ":"


def say(text):
    """A command that prints *text* verbatim — quoting a JSON body is how it stays inert."""
    return "printf '%s\\n' " + shlex.quote(text)


# `cat` on an inherited terminal would block the test, so stdin is only slurped when the
# test explicitly says the command is supposed to be fed something.
READS_STDIN = '  echo "stdin=$(cat)"'
SKIPS_STDIN = "  : # stdin not read"


def write_binary(bin_dir, name, log, stdout="", exit_code=0, reads_stdin=False):
    """An executable that records how it was invoked instead of doing its job."""
    log.touch()
    script = bin_dir / name
    script.write_text(RECORDER.format(
        log=log, stdout=say(stdout) if stdout else SILENCE, code=exit_code,
        stdin_line=READS_STDIN if reads_stdin else SKIPS_STDIN))
    script.chmod(0o755)
    return log


@pytest.fixture
def shims(tmp_path, monkeypatch):
    """Fake binaries on PATH that append their invocation to `logs[name]`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    logs = {}

    def add(name, stdout="", exit_code=0, reads_stdin=False):
        logs[name] = write_binary(bin_dir, name, tmp_path / f"{name}.log", stdout, exit_code,
                                  reads_stdin=reads_stdin)
        return logs[name]

    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    # The log path is baked into each shim, so nothing depends on the child's environment.
    return add, logs


def calls(log):
    """Everything the fake binary recorded, one call at a time."""
    sections, current = [], {}
    for line in log.read_text().splitlines():
        if line.startswith("cwd="):
            current = {"cwd": line[4:], "argc": 0, "stdin": "", "args": []}
            sections.append(current)
        elif line.startswith("argc=") and current:
            current["argc"] = int(line[5:])
        elif line.startswith("stdin=") and current:
            current["stdin"] = line[6:]
        elif line.startswith("arg=[") and line.endswith("]") and current:
            current["args"].append(line[5:-1])
    return sections


# `kmt run-script` used to be the operator-facing half of that mistake, and it was the only
# command that could be handed a file this checkout had already deleted. It is deleted, so the
# check that survived is the one above: nothing in `src/` may name a script that is not there,
# and nothing that is left takes a path from a human and runs it.

# ---------------------------------------------------------------------------
# 2. helmfile apply (helm.py)
# ---------------------------------------------------------------------------
def apply_helmfile(tmp_path, monkeypatch, shims, **kwargs):
    add, logs = shims
    add("helmfile")
    # The manifest ships now, so the apply asks the cluster which releases it holds before it
    # touches anything. A cluster with no releases is the same state this helper was in before the
    # manifest moved into the package, so the fake answers "none" rather than being a new claim.
    add("helm", stdout="[]")
    main_dir = tmp_path / "checkout"
    platform_config = main_dir / "platform_config"
    # Nothing has to be created for helmfile's `cwd`: the packaged values root is the working
    # directory shell.run hands the child, and that directory shipped with it.

    class FakeConfig:
        """What `HelmUtils` reads off `Config`, without a platform_config tree."""

        def __init__(self, environment, team, preload_configs=False):
            self.kubementat_main_dir = str(main_dir)
            self.platform_config_dir = str(platform_config)
            # The fake stands in for `Config`, and `Config` now carries the packaged helmfile root
            # — the one attribute `helmfile_paths` reads, so leaving it out is a hole in the
            # fake and not a fact about the code under test.
            self.helmfile_asset_dir = str(values_path())

        def get(self, key):
            return {"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "secret"}

        def component_namespace(self, component):
            return constants.DEFAULT_NAMESPACES[component]

        # The two per-release knobs helmfile is handed before argv is assembled. This fake declares
        # none, which is the answer a tree that names nothing gets from the real `Config` too, so the
        # export writes nothing and these tests stay about the one-token label filter they were
        # written for. Leaving the accessors out would be a hole in the fake and not a fact.
        def component_release_enabled(self, component):
            return None

        def component_release_version(self, component):
            return None

        # ...and the routing class helmfile's traefik release names. This scratch tree declares
        # nothing, which is what the real `Config` answers for a tree that names nothing -- L0, out of
        # `constants.py` -- so the export writes that and these tests stay about the argv they were
        # written for. Leaving the accessor out is a hole in the fake and not a fact about the apply.
        def component_ingress_class(self, component):
            return constants.static_answer(component_config_key(component, "INGRESS_CLASS"))

    # The configuration goes into the `Context` the class is built with, which is the
    # documented seam. Patching a `Config` name inside `infrastructure/helm.py` only worked while the class built
    # its own, and it is a fact about a module rather than about the object under test.
    #
    # The namespaces helmfile is handed go into the process environment, which is where a child
    # process looks for them. `monkeypatch` restores it, so this helper cannot decide what a
    # later test thinks the environment exported.
    for component in constants.DEFAULT_NAMESPACES:
        monkeypatch.delenv(component_config_key(component, "DEPLOYMENT_NAMESPACE"), raising=False)

    HelmUtils(configuration_only_context("dev", "smoke",
                                         FakeConfig("dev", "smoke"))).helmfile_apply(
        environment_name="dev",
        helmfile_label_filter="app=nginx, group=core", **kwargs)
    return calls(logs["helmfile"])[-1]


# ---------------------------------------------------------------------------
# 3. kubectl port-forward and pod lookup (`tunnel.py`, `workloads.py`)
# ---------------------------------------------------------------------------
@pytest.fixture
def kubernetes_utils(monkeypatch):
    """A `KubernetesUtils` with no cluster, and one that costs nothing to build.

    There is no kube-config load to neutralise any more: the constructor binds `TARGET_DEV`
    and stops. The two ways this fixture's tests reach a cluster are a shimmed `kubectl` argv and a
    monkeypatched pod lookup, so no client is ever built and none is needed.
    """
    return KubernetesUtils(TARGET_DEV)


def test_open_tunnel_forwards_each_port_as_its_own_argument(kubernetes_utils, monkeypatch, tmp_path, shims, capfd):
    """`{local_port}:{remote_port}` was pasted into a shell string; now it is one argv entry."""
    add, logs = shims
    add("kubectl", stdout="Forwarding from address & port\n")
    utils = kubernetes_utils

    monkeypatch.setattr(utils, "get_pod_name_for_deployment", lambda *args: "grafana-7d9-abc")
    assert utils.open_tunnel("grafana", "grafana-7d9-abc", "3001", "3000") == 0

    call = calls(logs["kubectl"])[-1]
    # The target is in the argv before the subcommand, so the port pair is not the only
    # thing asserted here: an unaddressed port-forward is the same class of bug as a captured one.
    assert call["args"] == [*kubectl_target_args(TARGET_DEV), "--namespace", "grafana",
                            "port-forward", "--address", "0.0.0.0",
                            "pod/grafana-7d9-abc", "3001:3000"]
    # The tunnel's whole failure mode was silence: the output has to reach the terminal.
    assert "Forwarding from address & port" in capfd.readouterr().out


def test_open_tunnel_reports_a_failure_as_exit_1_not_as_a_traceback(kubernetes_utils, monkeypatch, tmp_path, shims):
    add, logs = shims
    add("kubectl", exit_code=1)

    assert kubernetes_utils.open_tunnel("grafana", "grafana-7d9-abc", "3001", "3000") == 1


def test_pod_lookup_reports_an_uninstalled_component_readably(recorded_api):
    """No items is a state the operator can act on, and it is still said that way.

    It used to be a non-zero exit carrying empty stdout; the client answers with an empty list,
    which is a different mechanism and must not be a different message.
    """
    recorded_api.answer("list_namespaced_pod", lambda **kwargs: SimpleNamespace(items=[]))
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)

    with pytest.raises(PodNotFoundError) as excinfo:
        utils.get_pod_name_for_deployment("grafana", "app=absent")

    assert "Is the component installed?" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 5. stdin-carrying permission probes (`automations/install/kubementat.py`)
# ---------------------------------------------------------------------------
def test_cluster_permission_probes_ask_the_server_and_answer_themselves(recorded_api, monkeypatch):
    """The six probes were converted to client reads, and they answer with less machinery, not more.

    `kubectl auth can-i create namespace` needed a stdin to answer its own yes/no prompt, which is
    why `input="yes"` was there. A `SelfSubjectAccessReview` is a request with a boolean in the
    reply and nothing else to answer, so no child process is started at all -- which is what this
    asserts: a run that still went out through a shell would have left calls in the log.

    The resources are asserted as the API spells them. The singular forms kubectl accepted were a
    convenience of its discovery mapping, and a review that carried them would answer "no" on
    every cluster for a permission the service account does have.
    """
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)
    # The probe builds its client from the name the slice imported, which is the seam there is no
    # longer a `self.kubernetes_utils` to hand over. The `Context` carries no cluster claim here:
    # every read goes through the client above.
    monkeypatch.setattr(install_slice, "KubernetesUtils", lambda ctx: utils)

    check_cluster_permissions(configuration_only_context("dev", "smoke",
                                                         platform_config_dir=UNREAD_ROOT))

    reviews = [kwargs for method, kwargs in recorded_api.calls
               if method == "create_self_subject_access_review"]
    assert len(reviews) == len(install_slice.CLUSTER_PERMISSION_CHECK_RESOURCES)
    assert [str(review["body"].spec.resource_attributes.resource) for review in reviews] == \
        list(install_slice.CLUSTER_PERMISSION_CHECK_RESOURCES)
    assert all(review["body"].spec.resource_attributes.verb == "create" for review in reviews)


def test_a_denied_verb_stops_the_run_and_names_the_verbs_it_denied(recorded_api, monkeypatch):
    """A "no" that is printed and thrown away is the same as not checking, which is what bash did."""
    def answering(**kwargs):
        resource = kwargs["body"].spec.resource_attributes.resource
        if resource == "clusterroles":
            return SimpleNamespace(status=SimpleNamespace(allowed=False))
        return SimpleNamespace(status=SimpleNamespace(allowed=True))

    recorded_api.answer("create_self_subject_access_review", answering)
    utils = KubernetesUtils(TARGET_DEV, api_client=recorded_api)
    monkeypatch.setattr(install_slice, "KubernetesUtils", lambda ctx: utils)

    with pytest.raises(errors.CommandExecutionError) as error:
        check_cluster_permissions(configuration_only_context("dev", "smoke",
                                                             platform_config_dir=UNREAD_ROOT))
    assert "clusterroles" in str(error.value)
