"""A cluster target is safe to print, and printing is how it ends up in a log."""

from cluster_fixture import checkout, redirect_checkout

from kubementat.core.identity.target import ClusterTarget

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
    """Certificate material is the same problem, and it is held for longer."""
    redirect_checkout(monkeypatch, checkout(tmp_path))

    target = ClusterTarget.from_connection(
        "https://lib.example:6443", CA_PEM, (CLIENT_CERT, CLIENT_KEY), "library"
    )

    for credential in (CA_PEM, CLIENT_CERT, CLIENT_KEY, CA_PEM.splitlines()[1], CLIENT_KEY.splitlines()[1]):
        assert credential not in repr(target)
    assert "BEGIN" not in repr(target)
    assert "redacted" in repr(target)


def test_the_repr_names_the_environment_the_context_and_where_the_claim_came_from(tmp_path, monkeypatch):
    """Redaction cannot afford to lose the debugging value either."""
    redirect_checkout(monkeypatch, checkout(tmp_path))

    target = ClusterTarget.from_connection("https://lib.example:6443", None, TOKEN, "library")
    printed = repr(target)

    assert "environment='library'" in printed
    assert "https://lib.example:6443" in printed
    assert "source='connection'" in printed
