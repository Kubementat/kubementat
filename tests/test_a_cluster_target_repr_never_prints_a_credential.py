"""A cluster target is safe to print, and printing is how it ends up in a log.

The object ends up in `repr` of something else, in a debug log, and in a failure message that a
human reads over someone else's shoulder. So `__repr__` redacts, and nothing else in the class
prints: a `ClusterTarget` built from connection material holds credentials until a client is
actually built, which is why the redaction exists at all — the same reason
`create-secret-from-json` refuses to echo values.

The tests here hunt for the credential string rather than for the word "redacted", because the
assertion a reader can trust is "the token is not in there", not "somebody left a comment".

"""

from kubementat.core.identity.target import ClusterTarget
from cluster_fixture import DEV_SERVER, checkout, redirect_checkout, write_kubeconfig

TOKEN = "the-token-that-must-not-be-printed"
CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfakeCAmaterialfake\n-----END CERTIFICATE-----\n"
CLIENT_CERT = "-----BEGIN CERTIFICATE-----\nMIIBfakeClientCert\n-----END CERTIFICATE-----\n"
CLIENT_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfakePrivateKey\n-----END RSA PRIVATE KEY-----\n"


def test_the_token_a_connection_target_holds_never_reaches_the_repr(tmp_path, monkeypatch):
    """The failure mode this locks: a debug log that prints the object, prints the credential."""
    redirect_checkout(monkeypatch, checkout(tmp_path))

    target = ClusterTarget.from_connection("https://lib.example:6443", None, TOKEN, "library")

    assert repr(target)
    assert TOKEN not in repr(target)
    assert TOKEN not in str(target)


def test_the_authority_and_the_client_key_a_connection_target_holds_never_reach_the_repr(
    tmp_path, monkeypatch
):
    """Certificate material is the same problem, and it is held for longer.

    A certificate is not a secret the way a token is, but a private key is, and the pair is
    usually printed together. Neither half of the pair appears, and the authority that signs the
    cluster appears nowhere either.
    """
    redirect_checkout(monkeypatch, checkout(tmp_path))

    target = ClusterTarget.from_connection(
        "https://lib.example:6443", CA_PEM, (CLIENT_CERT, CLIENT_KEY), "library"
    )

    for credential in (CA_PEM, CLIENT_CERT, CLIENT_KEY, CA_PEM.splitlines()[1], CLIENT_KEY.splitlines()[1]):
        assert credential not in repr(target)
    assert "BEGIN" not in repr(target)
    assert "redacted" in repr(target)


def test_a_target_read_from_a_kubeconfig_prints_where_it_is_and_not_the_users_it_read(
    tmp_path, monkeypatch
):
    """The kubeconfig path and the address are what belongs in a log line.

    The user entry that file carries is a token, and it never enters the object at all — the
    address is read out of the cluster entry, so there is nothing to redact. This asserts the
    absence from the object rather than from the printer, which is the stronger statement.
    """
    from cluster_fixture import FIXTURE_TOKEN

    root = redirect_checkout(monkeypatch, checkout(tmp_path))
    kubeconfig = write_kubeconfig(root / "kubeconfig", {"dev": DEV_SERVER})

    target = ClusterTarget.from_kubeconfig(str(kubeconfig), "dev", "dev")

    assert FIXTURE_TOKEN not in repr(target)
    assert "token" not in repr(target).lower()
    assert str(kubeconfig) in repr(target)
    assert target.address in repr(target)


def test_the_repr_names_the_environment_the_context_and_where_the_claim_came_from(tmp_path, monkeypatch):
    """Redaction cannot afford to lose the debugging value either.

    A repr that drops the address or the source is a repr that cannot answer "which cluster did
    this command think it was talking to", which is the question the object exists to answer.
    """
    redirect_checkout(monkeypatch, checkout(tmp_path))

    target = ClusterTarget.from_connection("https://lib.example:6443", None, TOKEN, "library")
    printed = repr(target)

    assert "environment='library'" in printed
    assert "https://lib.example:6443" in printed
    assert "source='connection'" in printed
