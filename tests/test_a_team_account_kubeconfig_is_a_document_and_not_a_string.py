"""What the kubeconfig a team account is handed out with is made of.

This text is the product of the command. An operator pastes it into `~/.kube/config` and a
teammate's `kubectl` starts authenticating with it, so a defect in it is not a log line that
looks odd — it is a credential that does not work, or one that works against the wrong
cluster. The bash it replaced built it by interpolating settings read out of a local
kubeconfig, plus a base64-decoded token, into a heredoc; that makes the *shape* of the
document a property of the quoting, so a token containing a newline produced a document that
was syntactically accepted and semantically wrong, and nothing in the flow could notice. The
Python path assembles a dict and dumps it, which is what these tests hold:

* the document's shape belongs to the function that builds it, not to how a value happened
  to be interpolated;
* the two facts about the cluster — its address and the authority that signs it — are read
  from the kubeconfig this machine is loaded with, not from a second opinion or a constant;
* a certificate authority that lives in a *file* here has to arrive there as *data*, because
  the person pasting it has no such file.

The failure mode worth locking down is the quiet one: a document that is valid YAML, loads
without complaint, and is wired to the wrong user or the wrong cluster.
"""

from kubementat.errors import ResolvedKubeconfigUnusableError
import base64
import json

import pytest
import yaml


import kubementat.infrastructure.user_management as user_management
from kubementat.infrastructure.kubernetes import KubernetesUtils

from cluster_fixture import TARGET_DEV, UNREAD_ROOT, configuration_only_context

SERVER = "https://kube-api.example.test:6443"
CA_PEM = b"-----BEGIN CERTIFICATE-----\nfixture-authority\n-----END CERTIFICATE-----\n"
CA_DATA = base64.b64encode(CA_PEM).decode()
TOKEN = "eyJhbGciOiJIUzI1NiIsImtpZCI6ImZpeHR1cmUifQ.payload.signature"


def context(overrides=None):
    """The `Context` this document is named after.

    Built by the real constructor and not by a stand-in, because the two names the document carries
    are derived from the environment and the team the `Context` owns — and the refusal to guess a
    team is part of what this function is. A fake here would let the guard be bypassed by the very
    test that is supposed to see it.
    """
    values = overrides or {}
    # The document is built from arguments; `kubeconfig_for_service_account` opens no configuration
    # file, so the `Config` this Context carries is here because the type requires one.
    # An unnamed configuration root is an error rather than a checkout, so it is named as what it is.
    return configuration_only_context(values.get("environment", "dev"), values.get("team", "ops"),
                                      platform_config_dir=UNREAD_ROOT)


def kubeconfig(**overrides):
    """The document a team account is printed with, for a known account.

    `environment` and `team` go into the `Context` and not into the call: they are one object's two
    fields, so the only way to vary them is to vary the context.
    """
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
    """`token:` with nothing after it is not a kubeconfig, it is an apology.

    The reader that fills the token in raises rather than returning nothing, and the writer
    refuses what it was handed. Both ends matter: the printed text is what a person pastes,
    and a document whose user authenticates with nothing is indistinguishable from a working
    one until it is rejected.
    """
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
    """A `KubernetesUtils` for the `kubectl` this module still runs.

    `local_cluster_settings` is a kubeconfig read and so stays with kubectl — but it stays with
    *this* cluster, which means the argv assertion below has to see the flags a target carries.
    Without a target there is nothing to name and the call would be the bug the assertion
    exists to catch.
    """
    return KubernetesUtils(TARGET_DEV)


def test_an_authority_that_never_got_flattened_in_is_refused_and_not_printed(fake_binaries):
    """A path plus no data is a document that works here and nowhere else.

    Rather than emit one, the command says what it looked at. That is the same rule the rest
    of the module runs on: an answer the flow cannot use is a reason to stop, not a warning.
    """
    fake_binaries.respond("kubectl", json.dumps({"clusters": [{"name": "one", "cluster": {
        "server": SERVER, "certificate-authority": "/home/operator/clusters/dev-ca.crt"}}]}),
        subcommand="config.view")

    with pytest.raises(ResolvedKubeconfigUnusableError) as raised:
        user_management.local_cluster_settings(utils())

    assert "certificate-authority-data" in str(raised.value)
    assert "/home/operator/clusters/dev-ca.crt" not in str(raised.value).replace(
        "certificate-authority", ""), "the path was reported as if it were usable"


def test_a_kubeconfig_that_names_no_cluster_to_copy_says_so(fake_binaries):
    """Nothing to hand over is a state an operator can be told about.

    An empty `clusters` is what a machine with a half-written config looks like, and the flow
    is about to print a credential; the answer is the file to go and look at, not a traceback.
    """
    fake_binaries.respond("kubectl", json.dumps({"clusters": []}), subcommand="config.view")

    with pytest.raises(ResolvedKubeconfigUnusableError) as raised:
        user_management.local_cluster_settings(utils())

    assert "kubectl config view" in str(raised.value)


def test_a_config_view_that_answers_nothing_is_not_read_as_a_cluster_with_no_address(fake_binaries):
    """Empty stdout is not a cluster; it is kubectl saying nothing.

    `json.loads("")` is the ValueError this turns into a message. Silent here would mean a
    kubeconfig with a `server` of `None` on the other end.
    """
    fake_binaries.respond("kubectl", "", subcommand="config.view")

    with pytest.raises(ResolvedKubeconfigUnusableError) as raised:
        user_management.local_cluster_settings(utils())

    assert "kubeconfig this command resolved" in str(raised.value)
