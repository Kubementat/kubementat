"""A team that names no private registry has nothing to do, and `kmt` has to be able to say so."""


from types import SimpleNamespace

import pytest

from kubementat.automations.install import registry as registry_slice
from kubementat.automations.install.registry import setup_docker_registry_access


class RecordingKubernetes:
    """Remembers every cluster mutation, so "created a Secret full of nulls" is checkable."""

    def __init__(self):
        self.secrets = []
        self.patches = []

    def create_or_replace_docker_secret(self, namespace, name, auth_url, username, password, email):
        self.secrets.append({"namespace": namespace, "name": name, "auth": auth_url,
                            "username": username, "password": password, "email": email})

    def patch_service_account_with_pull_secrets(self, namespace, service_account, pull_secrets):
        self.patches.append({"namespace": namespace, "service_account": service_account,
                             "pull_secrets": list(pull_secrets)})

    # The capability prints what it did at the end, and that report reads the cluster. These answers
    # exist so the test asserts the work and not the printout.
    def get_service_account(self, namespace, name):
        return {"metadata": {"name": name, "namespace": namespace}}

    def list_secrets(self, namespace):
        return [s for s in self.secrets if s["namespace"] == namespace]


class LayerConfig:
    """The two team reads `setup_docker_registry_access` makes, answered from one dict."""

    def __init__(self, team_static, team_static_encrypted):
        self._static = team_static
        self._encrypted = team_static_encrypted

    def get(self, key):
        return {"team_static": self._static, "team_static_encrypted": self._encrypted}.get(key)


def an_automation(team_static_encrypted, monkeypatch, tmp_path):
    """A `Context` whose only collaborators are a recording cluster and a hand-answered config."""
    automation = SimpleNamespace(environment="dev", team="smoke", config=LayerConfig(
        {"PIPELINE_NAMESPACE": "pipelines", "APP_DEPLOYMENT_NAMESPACE": "apps",
         "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer"},
        team_static_encrypted))
    automation.kubernetes_utils = RecordingKubernetes()
    # The slice builds its client from the name it imported, which is the seam this test closes now
    # that there is no `self.kubernetes_utils` left to hand it over.
    monkeypatch.setattr(registry_slice, "KubernetesUtils", lambda _ctx: automation.kubernetes_utils)
    return automation


NAMED_CREDENTIAL = [{"NAME": "docker-registry-secret",
                     "DOCKER_REGISTRY_AUTH_URL": "registry.example.test",
                     "DOCKER_REGISTRY_EMAIL": "ops@example.test",
                     "DOCKER_REGISTRY_PASSWORD": "hunter2",
                     "DOCKER_REGISTRY_USERNAME": "deployer"}]


@pytest.mark.parametrize("block", [None, ""], ids=["absent", "empty"])
def test_a_team_with_no_credentials_does_not_crash(block, monkeypatch, tmp_path):
    """The crash itself. Absence is a state, and `None` is what an absent block reads as."""
    automation = an_automation({"DOCKER_REGISTRY_CREDENTIALS": block} if block is not None else {},
                               monkeypatch, tmp_path)

    setup_docker_registry_access(automation)  # must not raise


def test_a_team_with_no_credentials_creates_no_secret_and_patches_nothing(monkeypatch, tmp_path):
    """Silence is wrong in the other direction: the log must not claim work it did not do."""
    automation = an_automation({}, monkeypatch, tmp_path)

    setup_docker_registry_access(automation)

    assert automation.kubernetes_utils.secrets == [], \
        "it created a docker secret for credentials nobody named"
    assert automation.kubernetes_utils.patches == [], \
        "it patched a service account with an empty pull-secret list, which changes nothing and says it did"


def test_a_team_that_does_name_credentials_still_gets_the_secret(monkeypatch, tmp_path):
    """The other side, because "skip when empty" is only a fix if it is not a blanket no-op."""
    automation = an_automation({"DOCKER_REGISTRY_CREDENTIALS": NAMED_CREDENTIAL}, monkeypatch, tmp_path)

    setup_docker_registry_access(automation)

    created = automation.kubernetes_utils.secrets
    assert len(created) == 2, \
        f"the credentials should have been written into both namespaces, got {created}"
    assert {c["name"] for c in created} == {"docker-registry-secret"}
    assert created[0]["password"] == "hunter2", "the secret was built from something other than the answer"
    assert automation.kubernetes_utils.patches, \
        "it skipped the service-account patch a real credentials block needs"


def test_the_read_that_answers_none_is_not_confused_with_the_read_that_answers_nothing(monkeypatch, tmp_path):
    """`{}` and `{"DOCKER_REGISTRY_CREDENTIALS": None}` must do the same thing, and do it once."""
    with_key = an_automation({"DOCKER_REGISTRY_CREDENTIALS": None}, monkeypatch, tmp_path)
    without_key = an_automation({}, monkeypatch, tmp_path)

    setup_docker_registry_access(with_key)
    setup_docker_registry_access(without_key)

    assert with_key.kubernetes_utils.secrets == without_key.kubernetes_utils.secrets == []
    assert with_key.kubernetes_utils.patches == without_key.kubernetes_utils.patches == []
