"""Fixtures every parity and CLI-surface test builds on.

Three things are needed to compare a bash script against the Python path that replaced it,
and none of them is a cluster:

* **`fake_binaries`** — `tests/shims/` on `PATH`, with the calls it makes appended to a log.
  Both halves of the comparison run against it, so neither side can tell it is not real.
* **`fixture_repo`** — a throwaway checkout in `tmp_path`: the scripts, a `tekton_ci/`, and
  the smallest `platform_config/dev/fixture/` that answers every read. The real repo's
  configuration is never opened, and a test that writes into it cannot leak.
* **`dry_run_env`** — `KUBEMENTAT_HOME` pointed at that checkout, so a `kmt` started by the
  test reads the fixture and not this working tree.

The fixture values are the ones the tests compare *both* sides against, so they are written
down here once and are the same strings a `jq` read of `static.json` would return.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest
from kubernetes import client

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM_SOURCE_DIR = Path(__file__).resolve().parent / "shims"

# Where `run_kmt` points `PYTHONPATH` so a subprocess gets the fake API transport.
# The directory holding `sitecustomize.py`, and the tests directory it imports `apiserver_fake`
# from. Both go on `PYTHONPATH` for a `kmt` run as a subprocess.
SITE_DIR = Path(__file__).resolve().parent / "_site"
TESTS_DIR = Path(__file__).resolve().parent

# The team the fixture configuration is written for. A name this specific so a test that
# forgets to pass a team fails on the path rather than reading someone else's config.
FIXTURE_ENVIRONMENT = "dev"
FIXTURE_TEAM = "fixture"

# What the fixture config says, spelled out once because two sides have to agree on it.
PIPELINE_NAMESPACE = "fixture-pipelines"
APP_DEPLOYMENT_NAMESPACE = "fixture-deployments"
HELM_DEPLOYER_SERVICE_ACCOUNT = "fixture-helm-deployer"
MYSQL_DEPLOYMENT_NAME = "fixture-mysql"

# The cluster the fixture addresses, under the key a resolution reads from the fixture's own
# `static.json`, because every command that touches a cluster now resolves one before it runs — so a
# fixture that declared nothing would fail every CLI test with a resolution error rather than with
# whatever the test is about. It names the context the `KUBECONFIG` document below defines, so the
# answer is the fixture's and not the machine's.
FIXTURE_KUBE_CONTEXT = "fixture"

# The server that fixture kubeconfig names, spelled once: the document below and
# `FIXTURE_TARGET`'s address both read it from here, so a comparison between "what the file says"
# and "what the target says" cannot go stale in one of the two places.
FIXTURE_SERVER = "https://kubeconfig-fixture.invalid:6443"

# The environment variables that outrank `static.json` in cluster resolution. The fixture declares
# its cluster in `static.json`, so these are cleared from every fixture-run: left set by someone's
# shell they would answer every CLI test from outside the test, which is the one way a resolution
# test can go quietly wrong.
PROCESS_CLUSTER_VARIABLES = ("CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH")

ENVIRONMENT_STATIC = {
    "TEKTON_KUBERNETES_STORAGE_CLASS": "fixture-storage",
    "DOCKER_REGISTRY_BASE_URL": "registry.example.test",
    "TEKTON_CI_IMAGE_NAME": "ci-runner",
    "TEKTON_CI_IMAGE_TAG": "1.2.3",
    "AUTOMATION_GIT_URL": "git.example.test",
    "AUTOMATION_GIT_PROJECT_NAME": "automation",
    "AUTOMATION_GIT_REVISION": "main",
    "AUTOMATION_GIT_SERVER_HOST": "git.example.test",
    "AUTOMATION_GIT_SERVER_PORT": "22",
    "AUTOMATION_GIT_SERVER_SSH_USER": "git",
    # Namespaces and selectors are configuration; the scripts hardcode these
    # values, so the fixture has to use the same ones or the comparison is meaningless.
    "GRAFANA_DEPLOYMENT_NAMESPACE": "grafana",
    "POLARIS_DEPLOYMENT_NAMESPACE": "polaris",
    "OPENBAO_DEPLOYMENT_NAMESPACE": "openbao",
    "TEKTON_DEPLOYMENT_NAMESPACE": "tekton-pipelines",
    "GRAFANA_SERVICE_SELECTOR": "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana",
    "POLARIS_SERVICE_SELECTOR": "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris",
    # OpenBao's chart puts `app.kubernetes.io/name` on the server pods as the *chart* name and
    # `app.kubernetes.io/instance` as the release name, so the pair is the shape the chart itself
    # writes and not what the Vault selector looked like. Order does not matter to `kubectl`, and
    # `cluster_parity._selector` folds it anyway, but the fixture should not carry an order that
    # only ever matched a chart kubementat no longer installs.
    "OPENBAO_SERVICE_SELECTOR": "app.kubernetes.io/name=openbao,app.kubernetes.io/instance=openbao",
    "TEKTON_SERVICE_SELECTOR": "app=tekton-dashboard",
}

TEAM_STATIC = {
    "PIPELINE_NAMESPACE": PIPELINE_NAMESPACE,
    "APP_DEPLOYMENT_NAMESPACE": APP_DEPLOYMENT_NAMESPACE,
    "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": HELM_DEPLOYER_SERVICE_ACCOUNT,
    # Where the team's MySQL lives, and the release its pods carry. kubementat does not install
    # it any more, and row 25 retired the one command that read it (`tunnel-mysql`), so this key is
    # fixture configuration nothing in `src/` consults. It is left here because pruning a config
    # key is not this ticket's to do; the name is written down so the next reader can tell
    # unreachable from forgotten.
    "MYSQL_DEPLOYMENT_NAME": MYSQL_DEPLOYMENT_NAME,
}

# The environment-level encrypted file. `Config` requires it (its absence is a `FileNotFoundError`
# that aborts the command), so the fixture has to write one; its values are only ever printed.
ENVIRONMENT_STATIC_ENCRYPTED = {
    "GRAFANA_ADMIN_USER": "grafadmin",
    "GRAFANA_ADMIN_PASSWORD": "not-a-password",
    "GIT_DEPLOYER_PRIVATE_KEY_BASE64": "emVib3JlLW5vdC1hLWtleQ==",
    "GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64": "emVib3JlLW5vdC1hLWtleQ==",
    "GRAFANA_SERVICE_ACCOUNT_TOKEN": "not-a-token",
    "GRAFANA_USERS": [],
}

TEAM_STATIC_ENCRYPTED = {
    "SSH_DEPLOY_KEYS": [
        {
            "NAME": "deploy-key-one",
            "TARGET_NAMESPACE": PIPELINE_NAMESPACE,
            "TARGET_SECRET_NAME": "deploy-key-one-secret",
            # Any base64 body will do; nothing here decodes it.
            "PRIVATE_KEY_BASE64": "Zm9yLW5vLXJlYXNvbnMtb25seQ==",
        }
    ],
    "DOCKER_REGISTRY_CREDENTIALS": [
        {
            "NAME": "registry-credentials",
            "DOCKER_REGISTRY_AUTH_URL": "https://registry.example.test",
            "DOCKER_REGISTRY_EMAIL": "team@example.test",
            "DOCKER_REGISTRY_PASSWORD": "not-a-password",
            "DOCKER_REGISTRY_USERNAME": "deployer",
        }
    ],
}

# A kubeconfig that points nowhere. It is still written because a command that *resolves* a cluster
# reads one, and `static.json` names this file as `CLUSTER_KUBECONFIG_PATH` — but nothing here
# loads it into the kubernetes library any more: `KubernetesUtils` is handed a target and an injected
# client, so which machine is running pytest has stopped being an input to anything.
KUBECONFIG = """apiVersion: v1
kind: Config
clusters:
- cluster:
    server: {server}
  name: fixture
contexts:
- context:
    cluster: fixture
    user: fixture
  name: fixture
current-context: fixture
users:
- name: fixture
  user:
    token: not-a-token
""".format(server=FIXTURE_SERVER)


def _fixture_target():
    """The cluster the fixture checkout actually addresses, as a `ClusterTarget`.

    This had to exist once recorded cluster reads could be compared against the target a command
    resolved. Three parity tests drive `KubernetesUtils` directly rather
    than through a command, and they were handed `cluster_fixture.TARGET_DEV` — a fabricated target
    whose context is `dev` while the checkout they run their bash side in declares `fixture` in its
    `static.json` and in the `current-context` of its kubeconfig. Nothing in the tree is wrong about
    that: the two sides of those comparisons had been addressed to two different clusters for as
    long as anyone looked, and the record's old shape could not say so. A target the test made up is
    allowed wherever a *comparison* is not what the test is doing; when it is, the target has to be
    the one the fixture declares, which is this.

    `kubeconfig_path` is left unset rather than pointed at a file that only exists inside a test's
    `tmp_path`. The identity the record compares is the context, and the address is a value
    nothing in these tests contacts.
    """
    from kubementat.core.identity.target import ClusterTarget
    from kubementat.core.constants import STATIC_SOURCE_PREFIX

    return ClusterTarget(environment=FIXTURE_ENVIRONMENT, context=FIXTURE_KUBE_CONTEXT,
                         kubeconfig_path=None, address=FIXTURE_SERVER,
                         source=f"{STATIC_SOURCE_PREFIX}tests/conftest.py")


# The one target a test that compares both channels has to address, for the reason in the helper.
FIXTURE_TARGET = _fixture_target()


def pytest_collection_modifyitems(config, items):
    """`integration` means "needs a cluster", and this suite's promise is that it does not.

    The marker is not decoration: a cluster-dependent test that runs everywhere turns a green
    `pytest -q` into a green-on-one-machine-only, so these are skipped unless the operator
    asks for them with `-m integration`.
    """
    if config.getoption("markexpr", default=""):
        return
    skip = pytest.mark.skip(reason="needs a cluster - run with -m integration to get it")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


class _CallLog(list):
    """A `(method, kwargs)` log that also remembers which cluster each entry was asked of.

    The entries stay two-element tuples — every test that reads the log unpacks it as
    `for method, kwargs in recorder.calls`, and that is 47 places the record's new field must not
    disturb. So the cluster lives alongside, and the two are kept in step by overriding every
    mutation the log offers rather than by trusting the caller: a `calls.clear()` between two
    commands that left the clusters behind would attribute the second run's calls to the first
    command's target, which is a wrong answer delivered as a passing test.

    `None` is not "the default cluster", it is "nobody said". `operations_from_api_calls` raises on
    it, so an unattributed entry cannot quietly become a comparison that proves nothing.
    """

    def __init__(self, *args):
        super().__init__(*args)
        self.clusters = [None] * len(self)

    def append(self, entry, cluster=None):
        """One call, and the cluster it was addressed to (`None` when nothing said)."""
        super().append(entry)
        self.clusters.append(cluster)

    def extend(self, entries):
        super().extend(entries)
        self.clusters.extend([None] * len(list(entries)))

    def insert(self, index, entry):
        super().insert(index, entry)
        self.clusters.insert(index if index >= 0 else len(self.clusters) + index + 1, None)

    def pop(self, index=-1):
        entry = super().pop(index)
        self.clusters.pop(index)
        return entry

    def remove(self, entry):
        position = list.index(self, entry)
        super().remove(entry)
        self.clusters.pop(position)

    def clear(self):
        super().clear()
        del self.clusters[:]

    def __setitem__(self, index, entry):
        super().__setitem__(index, entry)
        if isinstance(index, slice):
            self.clusters[index] = [None] * len(entry)
        else:
            self.clusters[index] = None

    def __delitem__(self, index):
        super().__delitem__(index)
        del self.clusters[index]


class RecordedApi:
    """One generated Kubernetes client, recording instead of calling.

    Every call is appended to the shared `calls` log as `(method, kwargs)`, which is what
    `cluster_parity.operations_from_api_calls` reads, together with the cluster this particular
    client was built to address — which is what makes the same reading say *which* cluster was
    asked. `answers` decides what it gets back: anything unconfigured returns a plausible empty
    list for a `list_*`, and nothing for the rest, because the operations are what is under test,
    not the data. A call the implementation treats as a failure is configured with an exception.

    `cluster=None` means this recorder answers for no cluster in particular, and the operations
    reduced from its log raise rather than being compared as if they had said. A recorder that is
    meant to answer a command's own client is built by `ApiRecorder.for_target`.
    """

    def __init__(self, log, answers=None, recorder=None, cluster=None):
        self.log = log
        self.recorder = recorder
        self.cluster = cluster
        self.answers = dict(answers or {})

    def __getattr__(self, method):
        def call(*args, **kwargs):
            if args:
                # The recorder reads keyword arguments; a positional call would be recorded
                # with its arguments in the wrong place, i.e. as a parity failure nobody can
                # see. Nothing in `src/` does this, and if it starts, say so here.
                raise AssertionError(f"{method} was called positionally: {args}")
            self.log.append((method, kwargs), cluster=self.cluster)
            answers = dict(self.answers)
            answers.update(self.recorder.answers if self.recorder else {})
            answer = answers.get(method)
            if isinstance(answer, Exception):
                raise answer
            if answer is None:
                if method.startswith("list_"):
                    return SimpleNamespace(items=[])
                return None
            return answer(**kwargs) if callable(answer) else answer

        return call


class ApiRecorder:
    """The Python side's equivalent of `$SHIM_LOG`: what the cluster was asked to do.

    `answer` sets what any client returns for a method, so a test configures the *call* it
    cares about without caring which client class it happened to go through.
    """

    def __init__(self):
        self.calls = _CallLog()
        self.answers = {}

    def answer(self, method, value):
        self.answers[method] = value
        return self

    def api(self, answers=None):
        return RecordedApi(self.calls, answers, recorder=self)

    def for_target(self, target):
        """A client that answers as this recorder does and says which cluster it addresses.

        This is the client half of the comparison: a `ClusterOperation` reduced from a call made
        through the returned object carries the target's cluster, so it can be compared against the
        `kubectl` the same run would have made instead of merely sitting beside it. Where the
        recorder is handed in as `api_client=`, handing it in *addressed* is the line that makes the
        comparison mean something.
        """
        from cluster_fixture import target_cluster

        return RecordedApi(self.calls, recorder=self, cluster=target_cluster(target))

    def __getattr__(self, method):
        """Answer as any one of the four API objects, so one recorder is enough.

        This is what makes `KubernetesUtils(TARGET_DEV, api_client=recorder)` work: the recorder is
        handed to every API attribute the utility builds, and a call lands in the same log the
        generated clients would have written to. Only names the recorder does not already own land
        here — `answer`, `api`, `calls` and the rest keep answering as themselves.

        What lands here is *unaddressed*: it records the call and no cluster, so reducing it says
        "which cluster?" and raises. A test that means its client to carry one passes
        `api_client=recorder.for_target(target)` instead, which is the honest spelling of the same
        object.
        """
        return self.api().__getattr__(method)

    def operations(self):
        from cluster_parity import operations_from_api_calls

        return operations_from_api_calls(self.entries())

    def entries(self):
        """The log as `(method, kwargs, cluster)` triples — the record's own shape.

        `operations()` reads this, and a test that wants to say something about one call's cluster
        can read it without reaching into two parallel lists.
        """
        return [(method, kwargs, attributed)
                for (method, kwargs), attributed in zip(self.calls, self.calls.clusters)]

    def methods(self):
        return [method for method, _kwargs in self.calls]

    def called_as(self, method):
        """The last call made as *method*, as its kwargs.

        Tests that assert what a read was asked with need the arguments and nothing else, so this
        is the one accessor that keeps the shape out of the assertion. A test that cares about two
        calls of the same name reads `calls` directly; one call is what this is for.
        """
        matching = [kwargs for name, kwargs in self.calls if name == method]
        return matching[-1] if matching else None

    def as_str(self):
        return "\n".join(f"  {index}: {method} {kwargs}" for index, (method, kwargs) in enumerate(self.calls))


class ClusterNotInjected(AssertionError):
    """A test reached for a cluster without handing the client anything to reach it with.

    This is what the rule that no test may dial out looks like when it is enforced instead of
    remembered. Before the cluster reads moved from `kubectl` into the generated client, a test that
    forgot to fake its cluster read got a shim that printed nothing; after them the same test dials
    whatever kubeconfig the machine has, which is quiet on a laptop and red on a runner with none.
    Patched at the transport and not at the constructor, so a test that builds a real client to
    assert something about the client still gets one -- it just cannot make it talk.
    """


def _refuse_the_cluster(self, *args, **kwargs):
    raise ClusterNotInjected(
        f"{type(self).__name__} was asked to contact a cluster by a test that injected no "
        f"recorder. Ask for `recorded_api`, or hand a client to "
        f"KubernetesUtils(..., api_client=...). Nothing in the suite may fall back to a machine's "
        f"own kubeconfig, so the client is injected rather than discovered.")


def is_a_write(method: str) -> bool:
    """Whether a recorded client call changed the cluster.

    A test that asks "did this flow write anything" cannot spell it as
    `method.startswith("create_")` once the client path brings in the permission reviews: the generated
    client calls a `SelfSubjectAccessReview` `create_...`, and a preflight is not a write. One
    function, and the reducer is where the answer lives, so the two cannot disagree.
    """
    from cluster_parity import PROBE_METHODS

    if method in PROBE_METHODS:
        return False
    return method.startswith(("create_", "replace_", "delete_", "patch_", "put_"))


def is_apply(argv) -> bool:
    """Whether a recorded command was `kubectl apply`, however it was addressed.

    The kubectl calls that stay carry their cluster, so `apply` is no longer the second
    word of the argv. A test that reads the verb out of a position would report a correctly
    addressed call as a call that never happened — which is the false alarm the whole harness is
    built to avoid.
    """
    if not argv or argv[0] != "kubectl":
        return False
    words = [argument for index, argument in enumerate(argv)
              if not argument.startswith("-") and (index == 0 or not _is_flag_value(argv, index))]
    return "apply" in words


def _is_flag_value(argv, index):
    """Whether `argv[index]` is the value of the flag before it."""
    if index == 0:
        return False
    previous = argv[index - 1]
    return previous.startswith("-") and "=" not in previous


CLUSTER_READ_METHODS = {
    "crd": "list_custom_resource_definition",
    "clusterrole": "list_cluster_role",
    "clusterrolebinding": "list_cluster_role_binding",
    "ingressclass": "list_ingress_class",
    "persistentvolumeclaims": "list_namespaced_persistent_volume_claim",
    "pod": "list_namespaced_pod",
}
"""A `kubectl get` word and the client method the converted code now asks for.

The table states the cluster reads that moved into the client once, as the harness sees it: a test
that declares "the cluster has these ClusterRoles" says it with the word an operator would type, and
this is where it becomes the method the client calls. It belongs in `conftest` and not in each test
file because the same handful of reads is converted everywhere, and two tables that must agree would
be the bug waiting to happen.
"""


def _attribute(key):
    """A JSON key the way the deserialised model spells it.

    An API body says `roleRef`; the model the code reads says `role_ref`. Tests declare state in
    the first shape because that is what a `kubectl get -o json` body always was, so the
    translation is done here once rather than in every fixture.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


# Keys whose *values* are operator-authored maps and not model fields, so their inner keys must
# survive exactly as written: `managed-by` is a label, and turning it into `managed_by` would make
# the "whose is this object?" sweep read nothing and own nothing.
OPAQUE_KEYS = frozenset({"labels", "annotations", "data", "string_data", "match_labels",
                         "match_expressions"})


class RecordedObject(dict):
    """The payload behind a recorded cluster object, addressable both ways.

    Production reads these either as `to_dict()` does (`item.get("metadata")`, because a sweep that
    used to parse `kubectl` JSON kept the dictionary shape) or as a model is read (`item.metadata
    .name`, because the converted code has no reason to prefer either). One dict subclass that also
    answers attribute access keeps both readings honest, so a test never has to know which one a
    given function happens to use — and a change of style in `src/` shows up as a passing test
    rather than a fixture rewrite.
    """

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)

    def to_dict(self):
        """The same shape `to_dict()` returns, which is what the sweeps read."""
        return dict(self)


def as_k8s_object(payload):
    """A parsed API body, addressable the way the SDK's models are.

    Tests write cluster state as JSON because that is the shape they had when the answer was a
    `kubectl` body, and the recorder hands back objects the code reads by attribute. Rather than
    make every test construct `V1ObjectMeta`, the payload is given attributes recursively — which
    is what the models are, viewed from a test that only needs `metadata.name`.
    """
    if isinstance(payload, dict):
        return RecordedObject(**{
            (key if key in OPAQUE_KEYS else _attribute(key)):
                (value if key in OPAQUE_KEYS else as_k8s_object(value))
            for key, value in payload.items()})
    if isinstance(payload, list):
        return [as_k8s_object(item) for item in payload]
    return payload


def cluster_state(recorded_api, resource, items=None, error=None):
    """What the cluster contains, answered to the client the converted code now speaks.

    `resource` is the kubectl word (`crd`, `clusterrole`, `ingressclass`, ...), `items` is the list
    as it would appear in a `kubectl get -o json` body, and `error` is an `ApiException` (or the
    reason string for one) for the kind that cannot be read.

    A dict answers *per namespace*, which is what a namespaced sweep needs: the recorder answers
    one method and not one call shape, so a claim set that differs between namespaces has to be
    keyed by the namespace the client was asked about. The shim had this for free, because its call
    shape included the namespace.
    """
    from kubernetes.client.exceptions import ApiException

    method = CLUSTER_READ_METHODS[resource]
    if isinstance(items, dict):
        by_namespace = {key: list(value) for key, value in items.items()}

        def answer(namespace=None, **kwargs):
            payload = by_namespace.get(namespace, [])
            return SimpleNamespace(items=[as_k8s_object(item) for item in payload])
    else:
        payload = [] if items is None else list(items)

        def answer(**kwargs):
            return SimpleNamespace(items=[as_k8s_object(item) for item in payload])

    if error is not None:
        error = ApiException(status=503, reason=error) if isinstance(error, str) else error

        def answer(**kwargs):
            raise error

    recorded_api.answer(method, answer)
    return recorded_api


def cluster_deletes(recorded_api, resource, refused=None):
    """The delete half of the same declaration, for a sweep that must report a refusal.

    A refusal is an `ApiException` here and not an exit code, because the answer now comes from
    the API server and not from a process. A dict of refusals keyed by name answers per object,
    which is what the one-name-per-delete rule makes assertable.
    """
    from kubernetes.client.exceptions import ApiException

    method = "delete_" + CLUSTER_READ_METHODS[resource].removeprefix("list_")

    def answer(name=None, **kwargs):
        error = refused(name) if callable(refused) else refused
        if isinstance(error, str):
            error = ApiException(status=500, reason=error)
        if error is not None:
            raise error
        return None

    recorded_api.answer(method, answer)
    return recorded_api


@pytest.fixture(autouse=True)
def no_cluster_is_contacted(monkeypatch):
    """Nothing a test runs may dial out, asserted rather than assumed."""
    for verb in ("call_api", "request", "GET", "POST"):
        monkeypatch.setattr(client.ApiClient, verb, _refuse_the_cluster, raising=False)


@pytest.fixture(autouse=True)
def no_cluster_is_running_this_process(monkeypatch):
    """The in-cluster layer is answered "no", in every test, whatever the machine says.

    `no_cluster_is_contacted` asserts about what a test may reach. This one asserts about where the
    suite happens to be running, and the layer that reads the kubelet's own mount made that a
    question the suite had to keep answering: a resolution that finds the kubelet's own files can
    name a cluster without any configuration, so a test about *missing* configuration would quietly
    resolve instead of refusing if pytest were sitting inside a pod.

    So the mount root is pointed at a path that cannot exist and the two variables the kubelet
    injects are cleared. A test that wants to be a pod fabricates the mount itself — see
    `cluster_fixture.mounted_service_account` — and does it inside the test body, after this
    fixture has run, which is the whole of the ordering rule.

    The patch lands on `kubementat.core.identity.kubeconfig`, the module that owns
    `SERVICE_ACCOUNT_ROOT` and that `core/identity/context.py` reads it through, and
    `raising=True` is what makes that home an assertion rather than a hope: this fixture used to
    patch `kubementat.cluster` with
    `raising=False`, which is the shape of failure that stays green after the name moves, because
    the miss is silent and the layer under test reads the machine's real mount.
    """
    from kubementat.core.identity import kubeconfig as _kubeconfig_module

    monkeypatch.setattr(_kubeconfig_module, "SERVICE_ACCOUNT_ROOT",
                        Path("/no-pod-runs-tests-here"), raising=True)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT", raising=False)


@pytest.fixture
def recorded_api(monkeypatch):
    """`KubernetesUtils` with its clients swapped for recorders, and nothing read from this machine.

    The constructor used to load a kubeconfig, which every test neutralised by patching
    `config.load_kube_config`. It does not any more, so there is nothing left to neutralise:
    the seam is the `api_client` parameter, and a test that wants the recorder passes it
    (`KubernetesUtils(TARGET_DEV, api_client=recorded_api)`). A machine with a real `~/.kube/config`
    and a machine with none get the same answer here because neither is consulted.

    Two answers are seeded: a service account that exists, and a Service whose webhook has
    endpoints. `patch_service_account_with_pull_secrets` reads the account before it patches it, and
    `TektonUtils.install` holds the Triggers release until the Pipelines webhook is reachable — so a
    `None` in either place is a cluster that answers nothing, which is a bug the test would invent
    rather than one it is testing. A test that cares about the unreachable case says so with its own
    `answer()`.
    """
    from kubernetes.client import (V1EndpointAddress, V1EndpointSubset, V1Endpoints, V1ObjectMeta,
                                   V1ServiceAccount)

    from kubementat.core.identity.target import ClusterTarget

    recorder = ApiRecorder()
    recorder.answer("read_namespaced_service_account",
                    lambda name=None, namespace=None, **kwargs: V1ServiceAccount(
                        metadata=V1ObjectMeta(name=name, namespace=namespace)))
    # A permission probe that was never configured answers "allowed". That is the same stance as
    # the empty list a `list_*` gets: the recorder is a cluster that cooperates, and the test that
    # wants a denial says so with its own `answer()`. A `None` here would be read as a denial, which
    # is a bug the test would invent rather than one it is testing.
    recorder.answer("create_self_subject_access_review", lambda **kwargs: SimpleNamespace(
        status=SimpleNamespace(allowed=True)))
    recorder.answer("read_namespaced_endpoints",
                    lambda name=None, namespace=None, **kwargs: V1Endpoints(
                        metadata=V1ObjectMeta(name=name, namespace=namespace),
                        subsets=[V1EndpointSubset(
                            addresses=[V1EndpointAddress(ip="10.42.0.1")])]))
    # The seam for a test that cannot inject one. A test driving `Automation` or `TektonUtils` gets
    # its `KubernetesUtils` from a constructor it does not control, so the one place it can put the
    # recorder is the target's own client factory — which is also the only line in `src/` that
    # touches a credential, so replacing it is exactly what a suite that must not read the machine
    # asks for. `KubernetesUtils` then takes the injected path: a client that is not an
    # `ApiClient` is used as the API object, which is what this recorder already is.
    #
    # It is answered *per target*, and that is the comparison's other half: `for_target` binds the
    # recorder to the `ClusterTarget` whose `client()` was asked for, so every recorded call knows
    # which cluster it went to. Handing back one unbound recorder would put the cluster back to
    # "this process", which is exactly the answer a parity test must not be able to fall back to.
    monkeypatch.setattr(ClusterTarget, "client", lambda self: recorder.for_target(self))
    return recorder


def run_bash(script, args=(), cwd=None, env=None):
    """Run a script the way `kmt run-script` would, through the shims.

    bash is the reference implementation being compared against, so it is run directly rather
    than through any wrapper: a wrapper that fixes the arguments up is a wrapper that can hide
    what the script actually does.
    """
    script = Path(script)
    return subprocess.run(["bash", str(script), *[str(arg) for arg in args]],
                          cwd=str(cwd) if cwd else str(script.parent),
                          env=env, capture_output=True, text=True, timeout=120,
                          # /dev/null, not the terminal pytest itself is sitting on: a shim
                          # that reads stdin would otherwise block on whoever ran the suite.
                          stdin=subprocess.DEVNULL)


def run_kmt(argv, env):
    """The CLI the way an operator runs it, as its own process.

    Subprocess rather than `CliRunner` because the point is what the whole command reaches:
    the shims must be found through `PATH` the same way the real binaries would be, and no
    in-process test can prove `main()` wired the command in correctly.

    The cluster is answered on the way too, and by the same mechanism: `PYTHONPATH` gets the
    directory holding `sitecustomize.py`, which points the generated client's transport at the fake
    in `apiserver_fake`. Without it, a command that reads through the client instead of
    through `kubectl` would dial the machine running the test, which is the one thing a suite that
    must not contact a cluster forbids.

    The child is also told that it is not inside a cluster. The in-cluster layer would otherwise
    answer a *subprocess* from the pod the suite is running in, which is the one place
    `no_cluster_is_running_this_process` cannot reach with a monkeypatch. A test that wants a CLI
    resolving in-cluster drives `Context` in the test process instead, where the mount is fabricated
    deliberately.
    """
    environment = dict(env)
    for kubelet_variable in ("KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT"):
        environment.pop(kubelet_variable, None)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(SITE_DIR), str(TESTS_DIR),
         *[part for part in environment.get("PYTHONPATH", "").split(os.pathsep) if part]])
    return subprocess.run([sys.executable, "-m", "cli.kmt", *[str(argument) for argument in argv]],
                          env=environment, capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL)


class RecordedCall:
    """One invocation of one shim binary.

    Equality is defined over what the call *was*, not over the object identity, because a
    parity test compares two of these lists, not two of these objects.
    """

    def __init__(self, program, args, cwd, stdin=None):
        self.program = program
        self.args = list(args)
        self.cwd = cwd
        self.stdin = stdin

    @property
    def argv(self):
        return [self.program] + self.args

    def __eq__(self, other):
        return isinstance(other, RecordedCall) and (
            self.program == other.program and self.args == other.args and self.stdin == other.stdin)

    def __hash__(self):
        return hash((self.program, tuple(self.args), self.stdin))

    def __repr__(self):
        rendered = " ".join(repr(a) for a in self.argv)
        if self.stdin is not None:
            rendered = f"{rendered} <<stdin({len(self.stdin)} chars)"
        return rendered


class ShimEnvironment:
    """`tests/shims/` on `PATH`, plus the log the shims wrote to."""

    def __init__(self, directory, log, responses):
        self.directory = directory
        self.log = log
        self.responses = responses

    def respond(self, program, text, exit_code=0, subcommand=None, stderr=None):
        """What `program` prints (and exits) when the shim is called.

        `subcommand` narrows it to one call shape, e.g. `respond('kubectl', json,
        subcommand='get')` answers `kubectl get …` and leaves `kubectl apply …` silent.

        `stderr` is what goes to file descriptor 2. Most answers do not need it; the code that has
        to tell "it was not there" apart from "it was refused" does, because that difference lives
        only in the server's reason on stderr and nowhere in the exit code.
        """
        key = f"{program}.{subcommand}" if subcommand else program
        self.responses.joinpath(key).write_text(text)
        if exit_code:
            self.responses.joinpath(f"{key}.exit").write_text(str(exit_code))
        if stderr is not None:
            self.responses.joinpath(f"{key}.stderr").write_text(stderr)
        return self

    def calls(self):
        """Everything the shims recorded, in the order they were called."""
        if not self.log.exists():
            return []
        return parse_shim_log(self.log.read_text())

    def take_calls(self):
        """The calls so far, and an empty log behind them.

        A parity test needs to know which calls belonged to which side, and both sides write
        to one log. Truncating between them is that separation, and the only kind this
        harness needs: nothing runs concurrently. `O_TRUNC` rather than a rewrite so an empty
        log means "nothing called", not "nothing read".
        """
        recorded = self.calls()
        self.log.write_text("")
        return recorded

    def commands(self):
        """The same calls reduced to argv lists — the thing a parity test compares."""
        return [call.argv for call in self.calls()]

    def environment(self, base=None):
        """A copy of `base` (os.environ if omitted) with the shims in front of `PATH`."""
        environment = dict(os.environ if base is None else base)
        environment["PATH"] = f"{self.directory}{os.pathsep}{environment.get('PATH', '')}"
        environment["SHIM_LOG"] = str(self.log)
        environment["SHIM_RESPONSE_DIR"] = str(self.responses)
        return environment


def parse_shim_log(text):
    """Read a `$SHIM_LOG` body back into calls.

    The format is tab-separated on purpose: an argument may contain anything except a tab
    (and the recorder quotes nothing), so a delimiter that can appear inside an argument
    would silently split one call into two.
    """
    calls = []
    current = None
    for line in text.splitlines():
        kind, _, rest = line.partition("\t")
        if kind == "CALL":
            program, _, cwd = rest.partition("\t")
            current = RecordedCall(program, [], cwd)
            calls.append(current)
        elif kind == "ARG" and current is not None:
            current.args.append(rest)
        elif kind == "STDIN" and current is not None:
            current.stdin = base64.b64decode(rest).decode("utf-8", "replace")
    return calls


@pytest.fixture
def fake_binaries(tmp_path, monkeypatch):
    """`tests/shims/` first on `PATH`, with `SHIM_LOG`/`SHIM_RESPONSE_DIR` set.

    The shims are copied into `tmp_path` rather than used in place so a test can overwrite
    one binary with a variant that answers differently without touching the repo, and so two
    tests cannot write the same log.
    """
    directory = tmp_path / "shims"
    shutil.copytree(SHIM_SOURCE_DIR, directory, symlinks=True)
    for shim in directory.iterdir():
        shim.chmod(0o755)
    environment = ShimEnvironment(directory, tmp_path / "shim.log", tmp_path / "shim-responses")
    (tmp_path / "shim-responses").mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SHIM_LOG", str(environment.log))
    monkeypatch.setenv("SHIM_RESPONSE_DIR", str(environment.responses))
    return environment


class FixtureRepo:
    """A throwaway kubementat checkout.

    `scripts/` is a copy so a bash script run from the fixture reads the fixture's
    `platform_config` (they resolve `../../../platform_config` against themselves) instead
    of this working tree's.
    """

    def __init__(self, root):
        self.root = root
        self.scripts = root / "scripts"
        self.platform_config = root / "platform_config"
        self.environment = FIXTURE_ENVIRONMENT
        self.team = FIXTURE_TEAM
        self.kubeconfig = root / "kubeconfig"
        self.kubeconfig.write_text(KUBECONFIG)
        # The stamp this fixture stands for. This class is the fixture's whole claim to be *a prepared
        # tree*, and a prepared tree says which product it was made for (`config init` writes
        # `kubementat.yaml` as one of its steps), so a fixture that omits it is not simulating an
        # older tree — it is simulating a directory that was never prepared, which no CLI verb here
        # is testing. Written through the same constants the writer uses, so the fixture and the
        # product cannot drift apart and make every command agree on a stale version.
        self.stamp()

    def stamp(self, *, schema_version=None, constraint=None):
        """Write `<platform_config>/kubementat.yaml` and hand the path back.

        A test that wants an unstamped tree calls neither; one that wants a tree that disagrees
        passes the values it is checking. Both shapes are answers and neither is a default.
        """
        from kubementat.core import constants
        path = self.platform_config / constants.CONFIG_MANIFEST_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'kubementat: "{constraint or constants.CONFIG_KUBEMENTAT_CONSTRAINT}"\n'
            f"schema_version: {constants.CONFIG_SCHEMA_VERSION if schema_version is None else schema_version}\n")
        return path

    @property
    def pipeline_namespace(self):
        return PIPELINE_NAMESPACE

    @property
    def app_namespace(self):
        return APP_DEPLOYMENT_NAMESPACE

    def write_json(self, path, payload):
        """Write `payload` to `path` and hand it back, so a fixture can be both write and read."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return payload

    def environment_static(self, **overrides):
        """The environment's `static.json`, with the cluster the fixture addresses written in.

        The two cluster keys are merged in rather than pasted into `ENVIRONMENT_STATIC`, so a test
        that calls this to override one namespace does not quietly delete the environment's claim on
        a cluster and get a resolution error instead of the behaviour it is checking. A test that
        *wants* an unresolvable environment passes `CLUSTER_KUBE_CONTEXT=None`.
        """
        return self.write_json(self.platform_config / self.environment / "static.json",
                               {**ENVIRONMENT_STATIC,
                                "CLUSTER_KUBE_CONTEXT": FIXTURE_KUBE_CONTEXT,
                                "CLUSTER_KUBECONFIG_PATH": str(self.kubeconfig),
                                **overrides})

    def environment_static_encrypted(self, **overrides):
        return self.write_json(self.platform_config / self.environment / "static.encrypted.json",
                               {**ENVIRONMENT_STATIC_ENCRYPTED, **overrides})

    def team_static(self, **overrides):
        return self.write_json(self.platform_config / self.environment / self.team / "static.json",
                               {**TEAM_STATIC, **overrides})

    def team_static_encrypted(self, **overrides):
        return self.write_json(self.platform_config / self.environment / self.team / "static.encrypted.json",
                               {**TEAM_STATIC_ENCRYPTED, **overrides})

    def write_pipeline_run(self, relative_path="hello-run.yml", name="hello-run", pipeline="hello",
                           **extra):
        """A pipeline-run manifest, with the placeholders `run_pipeline` substitutes.

        The bash side used to read this file and sed eleven tokens into place; the Python side loads
        the same file and replaces the same tokens in the dict. All eleven are in here — in
        `annotations`, where a string value is legal and a wrong substitution is visible — so that a
        token renamed on one side of the comparison is caught by it and not by a pipeline failing on
        somebody else's cluster. The keys the substitution really belongs in (`serviceAccountName`)
        are in the shape they have in a real run file.

        The tokens in `annotations` are quoted. bash sed-ed the *text* of the file, so a bare token
        holding a number (`git-port: AUTOMATION_GIT_SERVER_PORT_PLACEHOLDER`) would have landed as a
        number after parsing where Python's dict substitution leaves a string. This fixture is not the
        place that difference is judged, so nothing here carries a token unquoted on a line that
        could turn into an integer.
        """
        path = self.team_pipeline_run_dir().joinpath(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(f"""\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
              name: {name}
              annotations:
                storage: "STORAGE_CLASS_PLACEHOLDER"
                registry: "DOCKER_REGISTRY_BASE_URL_PLACEHOLDER"
                ci-image-name: "TEKTON_CI_IMAGE_NAME_PLACEHOLDER"
                ci-image-tag: "TEKTON_CI_IMAGE_TAG_PLACEHOLDER"
                git-url: "AUTOMATION_GIT_URL_PLACEHOLDER"
                git-project: "AUTOMATION_GIT_PROJECT_NAME_PLACEHOLDER"
                git-revision: "AUTOMATION_GIT_REVISION_PLACEHOLDER"
                git-host: "AUTOMATION_GIT_SERVER_HOST_PLACEHOLDER"
                git-port: "AUTOMATION_GIT_SERVER_PORT_PLACEHOLDER"
                git-user: "AUTOMATION_GIT_SERVER_SSH_USER_PLACEHOLDER"
            spec:
              pipelineRef:
                name: {pipeline}
              serviceAccountName: "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME_PLACEHOLDER"
              timeout: 1h0m0s
            """))
        return path

    def team_pipeline_run_dir(self):
        return self.platform_config / self.environment / self.team / "tekton/pipeline-runs"

    def all_static_files(self):
        self.environment_static()
        self.environment_static_encrypted()
        self.team_static()
        self.team_static_encrypted()
        return self


@pytest.fixture
def fixture_repo(tmp_path):
    """The throwaway checkout — scripts, a `tekton_ci/`, and a seeded `platform_config`."""
    root = tmp_path / "checkout"
    root.mkdir()
    # `scripts/` is gone from the product and git cannot represent an empty directory, so a clean
    # checkout has no `scripts/` at all while a developer's tree still has the husk. Copying fails
    # there and silently succeeds here, which is the difference between a suite that measures the
    # repository and one that measures one working tree. The fixture therefore gets the directory
    # either way and takes what is in it if anything is.
    (root / "scripts").mkdir()
    if (REPO_ROOT / "scripts").is_dir():
        shutil.copytree(REPO_ROOT / "scripts", root / "scripts", dirs_exist_ok=True)
    (root / "tekton_ci" / "pipeline-runs").mkdir(parents=True)
    # The fixture gets a `templates/` directory for exactly one reason: one guard's subject is two
    # files disagreeing, and it relocates the resolver into this root so the template half stays
    # writable (`tests/test_one_component_is_installed_by_the_label_the_environment_names_it_under.py`).
    # The product reads its templates out of the package now, so this is fixture scaffolding
    # and not a copy of anything — nothing here is ever opened by a command that was told nothing.
    (root / "templates" / "environment").mkdir(parents=True)
    (root / "platform_config" / FIXTURE_ENVIRONMENT / "kubementat_components").mkdir(parents=True)
    return FixtureRepo(root).all_static_files()


@pytest.fixture
def dry_run_env(fixture_repo, fake_binaries, monkeypatch):
    """The environment a fixture-run `kmt` needs: home, config dir, kubeconfig, shims."""
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    # The configuration root is named, never located, so a fixture that wants its own
    # `platform_config` read has to name it. Pointing `KUBEMENTAT_HOME` at the checkout is not
    # enough any more, and pointing it there *and* clearing this variable would make every CLI
    # fixture fail on the refusal rather than on what the test is about.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    monkeypatch.setenv("KUBECONFIG", str(fixture_repo.kubeconfig))
    # Nothing here patches `KUBE_CONFIG_DEFAULT_LOCATION` any more, and the reason is the shape of
    # the read rather than the shape of the fixture: `ClusterTarget.client()` names the file it
    # wants, so `$KUBECONFIG` is honoured when the client is built and the constant the library
    # captured at import is never consulted. The env var above still belongs, because a `kmt` run as a
    # subprocess resolves its cluster through it.
    for name in PROCESS_CLUSTER_VARIABLES:
        # The variables that outrank `static.json`. Left set by someone's shell, it answers every
        # fixture command from outside the test, and the fixture's own `static.json` then goes
        # unread.
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", fixture_repo.environment)
    monkeypatch.setenv("TEAM", fixture_repo.team)
    monkeypatch.delenv("SCRIPT_LOG", raising=False)
    return fake_binaries.environment()


@pytest.fixture
def resolvable_environment(tmp_path, monkeypatch):
    """A checkout whose one environment declares a cluster, on the dials that decide it.

    Every command that reaches a cluster resolves one before it runs, so a CLI
    test that has no cluster to talk to still has to have *somewhere* that says which cluster it is
    not talking to. This fixture is that: the smallest `platform_config` that resolves, in a
    `tmp_path` checkout, with `KUBEMENTAT_HOME`/`KUBECONFIG` pointed at nothing else and the two
    environment variables that outrank `static.json` cleared.

    It is deliberately not `fixture_repo`: no `scripts/`, no team configuration, nothing a service
    reads. Tests that replace the service — the ones this exists for — need the resolution half and
    nothing else, and a test that needs a real namespace already has the bigger fixture.

    The address is `cluster_fixture.DEV_SERVER`, which is not reachable by construction: resolving a
    cluster is a file read, so a green test here still proves nothing was contacted.
    """
    from cluster_fixture import checkout, redirect_checkout, write_environment, write_kubeconfig, DEV_SERVER

    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_kubeconfig(root / "kubeconfig", {"fixture": DEV_SERVER})
    write_environment(root, FIXTURE_ENVIRONMENT, context=FIXTURE_KUBE_CONTEXT)
    # The commands that reach a cluster without declaring the argument — the `users` group is the
    # in-tree case, and the decorator answers for its environment because the command never names it
    # — read this; the ones that declare it ignore it, so setting it costs those nothing.
    monkeypatch.setenv("ENVIRONMENT", FIXTURE_ENVIRONMENT)
    return root
