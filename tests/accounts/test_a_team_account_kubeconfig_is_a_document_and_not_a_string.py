"""What the kubeconfig a team account is handed out with is made of."""

import base64
import json

import pytest
import yaml
from cluster_fixture import TARGET_DEV, UNREAD_ROOT, configuration_only_context

import kubementat.infrastructure.user_management as user_management
from kubementat.errors import ResolvedKubeconfigUnusableError
from kubementat.infrastructure.kubernetes import KubernetesUtils

SERVER = "https://kube-api.example.test:6443"
CA_PEM = b"-----BEGIN CERTIFICATE-----\nfixture-authority\n-----END CERTIFICATE-----\n"
CA_DATA = base64.b64encode(CA_PEM).decode()
TOKEN = "eyJhbGciOiJIUzI1NiIsImtpZCI6ImZpeHR1cmUifQ.payload.signature"


def context(overrides=None):
    """The `Context` this document is named after."""
    values = overrides or {}
    # The document is built from arguments; `kubeconfig_for_service_account` opens no configuration
    # file, so the `Config` this Context carries is here because the type requires one.
    # An unnamed configuration root is an error rather than a checkout, so it is named as what it is.
    return configuration_only_context(values.get("environment", "dev"), values.get("team", "ops"),
                                      platform_config_dir=UNREAD_ROOT)


def kubeconfig(**overrides):
    """The document a team account is printed with, for a known account."""
    arguments = {
        "service_account_name": "ops-account",
        "namespace": "ops-deployments",
        "token": TOKEN,
        "server": SERVER,
        "certificate_authority_data": CA_DATA,
    }
    ctx = arguments.pop("context", None) or context(overrides)
    arguments.update({key: value for key, value in overrides.items()
                      if key not in ("environment", "team", "context")})
    return user_management.kubeconfig_for_service_account(ctx, **arguments)


def loaded(text):
    """What the next consumer reads, which is the only reading that matters."""
    return yaml.safe_load(text)


def test_the_account_authenticates_with_the_token_it_was_given_and_nothing_else():
    text = kubeconfig()

    assert loaded(text)["users"][0]["user"] == {"token": TOKEN}


def test_no_token_handed_out_blank_rather_than_printing_an_empty_field():
    """`token:` with nothing after it is not a kubeconfig, it is an apology."""
    for blank in ("", "   ", "\n"):
        with pytest.raises(ValueError):
            kubeconfig(token=blank)


# --- where the two cluster facts come from ------------------------------------------------
#
# `kubectl config view` is what answers them, because the client resolves a certificate
# authority given as a file into a temporary path — useless inside a document that is about
# to be pasted somewhere else. `--flatten` re-inlines it, `--minify` narrows the answer to
# the context this run is on. Two `kubectl`s answer these: the recorder, for the argv, and an
# emulator that reads `$KUBECONFIG` and flattens it for real, because a fixed response from a
# recorder cannot tell "the code read this machine" apart from "the test told it what to say".


def utils():
    """A `KubernetesUtils` for the `kubectl` this module still runs."""
    return KubernetesUtils(TARGET_DEV)


def test_an_authority_that_never_got_flattened_in_is_refused_and_not_printed(fake_binaries):
    """A path plus no data is a document that works here and nowhere else."""
    fake_binaries.respond("kubectl", json.dumps({"clusters": [{"name": "one", "cluster": {
        "server": SERVER, "certificate-authority": "/home/operator/clusters/dev-ca.crt"}}]}),
        subcommand="config.view")

    with pytest.raises(ResolvedKubeconfigUnusableError) as raised:
        user_management.local_cluster_settings(utils())

    assert "certificate-authority-data" in str(raised.value)
    assert "/home/operator/clusters/dev-ca.crt" not in str(raised.value).replace(
        "certificate-authority", ""), "the path was reported as if it were usable"


def test_a_kubeconfig_that_names_no_cluster_to_copy_says_so(fake_binaries):
    """Nothing to hand over is a state an operator can be told about."""
    fake_binaries.respond("kubectl", json.dumps({"clusters": []}), subcommand="config.view")

    with pytest.raises(ResolvedKubeconfigUnusableError) as raised:
        user_management.local_cluster_settings(utils())

    assert "kubectl config view" in str(raised.value)


def test_a_config_view_that_answers_nothing_is_not_read_as_a_cluster_with_no_address(fake_binaries):
    """Empty stdout is not a cluster; it is kubectl saying nothing."""
    fake_binaries.respond("kubectl", "", subcommand="config.view")

    with pytest.raises(ResolvedKubeconfigUnusableError) as raised:
        user_management.local_cluster_settings(utils())

    assert "kubeconfig this command resolved" in str(raised.value)
