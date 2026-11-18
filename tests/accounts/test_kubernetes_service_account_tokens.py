"""Tests for a service account's token: how it is requested, and how it is read back.

A service account does not carry credentials. The token controller fills one in only for a
Secret that declares which service account it belongs to, and clusters since 1.24 do not
create that Secret on their own — so a login command, a kubeconfig, or a `kubectl`-as-a-user
flow has to ask for it first. The old shell path guessed by grepping `kubectl get secret`
for a name ending in `-token`; the name is a convention and the annotation is the fact, so
these tests pin the annotation-based rule and the two states an operator can act on.
"""

import base64

import pytest
from cluster_fixture import TARGET_DEV
from kubernetes import client

from kubementat.errors import ServiceAccountTokenNotFoundError
from kubementat.infrastructure.kubernetes import KubernetesUtils

SERVICE_ACCOUNT = "kubernetes-dashboard-read-only-cluster-user"
NAMESPACE = "kubernetes-dashboard"


class FakeApi:
    """Answers as a client would, and records what it was asked for."""

    def __init__(self, raises=None, returns=None):
        self.calls = []
        self.raises = dict(raises or {})
        self.returns = dict(returns or {})

    def __getattr__(self, method):
        def call(**kwargs):
            self.calls.append((method, kwargs))
            status = self.raises.get(method)
            if status is not None:
                raise client.exceptions.ApiException(status=status, reason="fake")
            answer = self.returns.get(method)
            return answer(**kwargs) if callable(answer) else answer

        return call

    def called_as(self, method):
        matching = [kwargs for name, kwargs in self.calls if name == method]
        return matching[-1] if matching else None


def make_utils(monkeypatch, **apis):
    # No kube-config to neutralise: the constructor binds and builds nothing.
    utils = KubernetesUtils(TARGET_DEV)
    for attribute, api in apis.items():
        setattr(utils, attribute, api)
    return utils


def secret(name, *, for_service_account=SERVICE_ACCOUNT, token="a-token", has_token=True):
    """A Secret as the client returns it: type and annotation say whose token it is."""
    return type("Secret", (), {
        "type": "kubernetes.io/service-account-token",
        "metadata": type("Meta", (), {
            "name": name,
            "annotations": (
                {"kubernetes.io/service-account.name": for_service_account}
                if for_service_account is not None else {}
            ),
        })(),
        "data": {"token": base64.b64encode(token.encode())} if has_token else None,
    })()


def secrets(*items):
    return lambda **kwargs: type("List", (), {"items": list(items)})()


def test_a_service_account_with_no_token_secret_names_the_way_to_get_one(monkeypatch):
    core = FakeApi(returns={"list_namespaced_secret": secrets(secret("other", for_service_account="other"))})
    utils = make_utils(monkeypatch, core_api=core)

    with pytest.raises(ServiceAccountTokenNotFoundError) as excinfo:
        utils.get_service_account_token(NAMESPACE, SERVICE_ACCOUNT)

    message = str(excinfo.value)
    assert SERVICE_ACCOUNT in message and NAMESPACE in message
    assert "create_or_replace_service_account_token_secret" in message


def test_several_token_secrets_are_called_out_and_resolved_by_a_stable_order(monkeypatch):
    """Rotating a token can leave two; an operator should know, and the choice must not vary."""
    core = FakeApi(returns={"list_namespaced_secret": secrets(
        secret("b-token", token="second"),
        secret("a-token", token="first"),
    )})
    utils = make_utils(monkeypatch, core_api=core)

    assert utils.get_service_account_token(NAMESPACE, SERVICE_ACCOUNT) == "first"
