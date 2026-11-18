"""What every test stands on: no machine state, no cluster, and recorders in place of both.

Two seams replace the cluster. `recorded_api` stands in for the generated Kubernetes client and
logs each call with the cluster it was addressed to; `fake_binaries` puts recorder programs named
`helm`, `helmfile`, `kubectl`, `git-crypt` and `tkn` first on `PATH`. `fixture_repo` writes a small
configuration tree that answers every read, and `dry_run_env` points a `kmt` run at it.
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

# Fixtures the configuration-directory tests share, offered to every test module from here.
from bootstrap_tools import deployer_secret_key, root, tools  # noqa: F401
from kubernetes import client

SHIM_SOURCE_DIR = Path(__file__).resolve().parent / "shims"

# `run_kmt` puts both on `PYTHONPATH`, so a `kmt` subprocess talks to `apiserver_fake` instead of a cluster.
SITE_DIR = Path(__file__).resolve().parent / "_site"
TESTS_DIR = Path(__file__).resolve().parent

# The environment and team the fixture configuration is written for.
FIXTURE_ENVIRONMENT = "dev"
FIXTURE_TEAM = "fixture"

# What the fixture configuration answers, for tests to assert against.
PIPELINE_NAMESPACE = "fixture-pipelines"
APP_DEPLOYMENT_NAMESPACE = "fixture-deployments"
HELM_DEPLOYER_SERVICE_ACCOUNT = "fixture-helm-deployer"
MYSQL_DEPLOYMENT_NAME = "fixture-mysql"

# The context the fixture environment declares, defined by the kubeconfig below.
FIXTURE_KUBE_CONTEXT = "fixture"

FIXTURE_SERVER = "https://kubeconfig-fixture.invalid:6443"

# The variables that state a cluster from outside the configuration and outrank `static.json`.
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
    "GRAFANA_DEPLOYMENT_NAMESPACE": "grafana",
    "POLARIS_DEPLOYMENT_NAMESPACE": "polaris",
    "OPENBAO_DEPLOYMENT_NAMESPACE": "openbao",
    "TEKTON_DEPLOYMENT_NAMESPACE": "tekton-pipelines",
    "GRAFANA_SERVICE_SELECTOR": "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana",
    "POLARIS_SERVICE_SELECTOR": "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris",
    # The shape OpenBao's chart writes: chart name as `name`, release name as `instance`.
    "OPENBAO_SERVICE_SELECTOR": "app.kubernetes.io/name=openbao,app.kubernetes.io/instance=openbao",
    "TEKTON_SERVICE_SELECTOR": "app=tekton-dashboard",
}

TEAM_STATIC = {
    "PIPELINE_NAMESPACE": PIPELINE_NAMESPACE,
    "APP_DEPLOYMENT_NAMESPACE": APP_DEPLOYMENT_NAMESPACE,
    "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": HELM_DEPLOYER_SERVICE_ACCOUNT,
    # A team key kubementat no longer reads; a real team file may still carry it.
    "MYSQL_DEPLOYMENT_NAME": MYSQL_DEPLOYMENT_NAME,
}

# `Config` requires the environment's encrypted file, so the fixture writes one.
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

# A kubeconfig that points at an unreachable server. Commands read it to resolve a cluster; no client
# ever connects through it, because every client is injected.
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


class _CallLog(list):
    """A `(method, kwargs)` log that keeps, alongside, the cluster each call was addressed to."""

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
    """One generated Kubernetes client, recording instead of calling."""

    def __init__(self, log, answers=None, recorder=None, cluster=None):
        self.log = log
        self.recorder = recorder
        self.cluster = cluster
        self.answers = dict(answers or {})

    def __getattr__(self, method):
        def call(*args, **kwargs):
            if args:
                # Calls are recorded by keyword; a positional one would be recorded wrongly.
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
    """What the cluster was asked to do, shared by every client a test hands out."""

    def __init__(self):
        self.calls = _CallLog()
        self.answers = {}

    def answer(self, method, value):
        self.answers[method] = value
        return self

    def api(self, answers=None):
        return RecordedApi(self.calls, answers, recorder=self)

    def for_target(self, target):
        """A client that answers as this recorder does and says which cluster it addresses."""
        from cluster_fixture import target_cluster

        return RecordedApi(self.calls, recorder=self, cluster=target_cluster(target))

    def __getattr__(self, method):
        """Answer as any one of the four API objects, so one recorder is enough."""
        return self.api().__getattr__(method)

    def operations(self):
        from recorded_operations import operations_from_api_calls

        return operations_from_api_calls(self.entries())

    def entries(self):
        """The log as `(method, kwargs, cluster)` triples — the record's own shape."""
        return [(method, kwargs, attributed)
                for (method, kwargs), attributed in zip(self.calls, self.calls.clusters)]

    def methods(self):
        return [method for method, _kwargs in self.calls]

    def called_as(self, method):
        """The last call made as *method*, as its kwargs."""
        matching = [kwargs for name, kwargs in self.calls if name == method]
        return matching[-1] if matching else None

    def as_str(self):
        return "\n".join(f"  {index}: {method} {kwargs}" for index, (method, kwargs) in enumerate(self.calls))


class ClusterNotInjected(AssertionError):
    """A test reached for a cluster without handing the client anything to reach it with."""


def _refuse_the_cluster(self, *args, **kwargs):
    raise ClusterNotInjected(
        f"{type(self).__name__} was asked to contact a cluster by a test that injected no "
        f"recorder. Ask for `recorded_api`, or hand a client to "
        f"KubernetesUtils(..., api_client=...). Nothing in the suite may fall back to a machine's "
        f"own kubeconfig, so the client is injected rather than discovered.")


def is_a_write(method: str) -> bool:
    """Whether a recorded client call changed the cluster."""
    from recorded_operations import PROBE_METHODS

    if method in PROBE_METHODS:
        return False
    return method.startswith(("create_", "replace_", "delete_", "patch_", "put_"))


def is_apply(argv) -> bool:
    """Whether a recorded command was `kubectl apply`, wherever its cluster flags sit."""
    if not argv or argv[0] != "kubectl":
        return False
    words = [argument for index, argument in enumerate(argv)
             if not argument.startswith("-") and (index == 0 or not _is_flag_value(argv, index))]
    return "apply" in words


def _is_flag_value(argv, index):
    """Whether `argv[index]` is the value of the flag before it."""
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
"""A resource as an operator would name it, and the client method that lists it."""


def _attribute(key):
    """A JSON key the way the deserialised model spells it."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


# Keys whose values are operator-authored maps (labels, data), kept verbatim when a body becomes
# a model: `managed-by` must not turn into `managed_by`.
OPAQUE_KEYS = frozenset({"labels", "annotations", "data", "string_data", "match_labels",
                         "match_expressions"})


class RecordedObject(dict):
    """The payload behind a recorded cluster object, addressable both ways."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)

    def to_dict(self):
        """The same shape `to_dict()` returns, which is what the sweeps read."""
        return dict(self)


def as_k8s_object(payload):
    """A parsed API body, addressable the way the SDK's models are."""
    if isinstance(payload, dict):
        return RecordedObject(**{
            (key if key in OPAQUE_KEYS else _attribute(key)):
                (value if key in OPAQUE_KEYS else as_k8s_object(value))
            for key, value in payload.items()})
    if isinstance(payload, list):
        return [as_k8s_object(item) for item in payload]
    return payload


def cluster_state(recorded_api, resource, items=None, error=None):
    """Answer the list call for *resource* with *items*, or raise *error*; a dict answers per namespace."""
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
    """Answer deletes of *resource*, refusing with *refused* (a reason, an exception, or a callable by name)."""
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


# The variables that point kubementat at a root, an environment or a cluster. A developer's shell
# commonly exports some of them, and any one left set answers a test from outside it.
POINTING_VARIABLES = ("KUBEMENTAT_HOME", "KUBEMENTAT_PLATFORM_CONFIG_DIR", "KUBECONFIG",
                      "ENVIRONMENT", "TEAM", *PROCESS_CLUSTER_VARIABLES)


@pytest.fixture(autouse=True)
def nothing_on_this_machine_answers_a_test(monkeypatch, tmp_path):
    """Every test starts from a machine that names no root, no environment and no cluster."""
    monkeypatch.setenv("KUBEMENTAT_CONFIGURED_ROOTS",
                       str(tmp_path / "configuration-roots-are-not-recorded-here.json"))
    for name in POINTING_VARIABLES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_cluster_is_contacted(monkeypatch):
    """Nothing a test runs may dial out, asserted rather than assumed."""
    for verb in ("call_api", "request", "GET", "POST"):
        monkeypatch.setattr(client.ApiClient, verb, _refuse_the_cluster, raising=False)


@pytest.fixture(autouse=True)
def no_cluster_is_running_this_process(monkeypatch):
    """The in-cluster layer is answered "no", in every test, whatever the machine says."""
    from kubementat.core.identity import kubeconfig as _kubeconfig_module

    monkeypatch.setattr(_kubeconfig_module, "SERVICE_ACCOUNT_ROOT",
                        Path("/no-pod-runs-tests-here"), raising=True)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT", raising=False)


@pytest.fixture
def recorded_api(monkeypatch):
    """Every client kubementat builds becomes a recorder bound to the cluster it was built for."""
    from kubernetes.client import (
        V1EndpointAddress,
        V1Endpoints,
        V1EndpointSubset,
        V1ObjectMeta,
        V1ServiceAccount,
    )

    from kubementat.core.identity.target import ClusterTarget

    recorder = ApiRecorder()
    recorder.answer("read_namespaced_service_account",
                    lambda name=None, namespace=None, **kwargs: V1ServiceAccount(
                        metadata=V1ObjectMeta(name=name, namespace=namespace)))
    # Unconfigured permission probes answer "allowed"; a test that wants a denial answers it itself.
    recorder.answer("create_self_subject_access_review", lambda **kwargs: SimpleNamespace(
        status=SimpleNamespace(allowed=True)))
    recorder.answer("read_namespaced_endpoints",
                    lambda name=None, namespace=None, **kwargs: V1Endpoints(
                        metadata=V1ObjectMeta(name=name, namespace=namespace),
                        subsets=[V1EndpointSubset(
                            addresses=[V1EndpointAddress(ip="10.42.0.1")])]))
    # `ClusterTarget.client` is the one place kubementat builds a client, so replacing it reaches
    # code a test cannot hand a client to. Each recorder is bound to the target that asked.
    monkeypatch.setattr(ClusterTarget, "client", lambda self: recorder.for_target(self))
    return recorder


def run_kmt(argv, env):
    """The CLI the way an operator runs it, as its own process."""
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
    """One invocation of one shim binary."""

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
        """What `program` prints (and exits) when the shim is called."""
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
        """The calls so far, and an empty log behind them."""
        recorded = self.calls()
        self.log.write_text("")
        return recorded

    def commands(self):
        """The recorded calls as argv lists."""
        return [call.argv for call in self.calls()]

    def environment(self, base=None):
        """A copy of `base` (os.environ if omitted) with the shims in front of `PATH`."""
        environment = dict(os.environ if base is None else base)
        environment["PATH"] = f"{self.directory}{os.pathsep}{environment.get('PATH', '')}"
        environment["SHIM_LOG"] = str(self.log)
        environment["SHIM_RESPONSE_DIR"] = str(self.responses)
        return environment


def parse_shim_log(text):
    """Read a `$SHIM_LOG` body back into calls."""
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
    """`tests/shims/` first on `PATH`, with `SHIM_LOG`/`SHIM_RESPONSE_DIR` set."""
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
    """A throwaway kubementat checkout."""

    def __init__(self, checkout_root):
        self.root = checkout_root
        self.platform_config = checkout_root / "platform_config"
        self.environment = FIXTURE_ENVIRONMENT
        self.team = FIXTURE_TEAM
        self.kubeconfig = checkout_root / "kubeconfig"
        self.kubeconfig.write_text(KUBECONFIG)
        # A prepared tree carries the stamp `config init` writes.
        self.stamp()

    def stamp(self, *, schema_version=None, constraint=None):
        """Write `<platform_config>/kubementat.yaml` and hand the path back."""
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
        """The environment's `static.json`, with the cluster the fixture addresses written in."""
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
        """A pipeline-run manifest, with the placeholders `run_pipeline` substitutes."""
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
    """A throwaway checkout with a `tekton_ci/` and a seeded `platform_config`."""
    checkout_root = tmp_path / "checkout"
    checkout_root.mkdir()
    (checkout_root / "tekton_ci" / "pipeline-runs").mkdir(parents=True)
    (checkout_root / "platform_config" / FIXTURE_ENVIRONMENT / "kubementat_components").mkdir(parents=True)
    return FixtureRepo(checkout_root).all_static_files()


@pytest.fixture
def dry_run_env(fixture_repo, fake_binaries, monkeypatch):
    """The environment a fixture-run `kmt` needs: home, config dir, kubeconfig, shims."""
    monkeypatch.setenv("KUBEMENTAT_HOME", str(fixture_repo.root))
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(fixture_repo.platform_config))
    monkeypatch.setenv("KUBECONFIG", str(fixture_repo.kubeconfig))
    for name in PROCESS_CLUSTER_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", fixture_repo.environment)
    monkeypatch.setenv("TEAM", fixture_repo.team)
    monkeypatch.delenv("SCRIPT_LOG", raising=False)
    return fake_binaries.environment()


@pytest.fixture
def resolvable_environment(tmp_path, monkeypatch):
    """A checkout whose one environment declares a cluster, on the dials that decide it."""
    from cluster_fixture import (
        DEV_SERVER,
        checkout,
        redirect_checkout,
        write_environment,
        write_kubeconfig,
    )

    checkout_root = checkout(tmp_path)
    redirect_checkout(monkeypatch, checkout_root)
    write_kubeconfig(checkout_root / "kubeconfig", {"fixture": DEV_SERVER})
    write_environment(checkout_root, FIXTURE_ENVIRONMENT, context=FIXTURE_KUBE_CONTEXT)
    # Commands that take no environment argument read it from here.
    monkeypatch.setenv("ENVIRONMENT", FIXTURE_ENVIRONMENT)
    return checkout_root
