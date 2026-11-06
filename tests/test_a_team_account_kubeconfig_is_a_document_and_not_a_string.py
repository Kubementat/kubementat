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

import base64
import json
import os
import sys

import pytest
import yaml

from click.exceptions import ClickException

from kubementat import user_management
from kubementat.infrastructure.kubernetes import KubernetesUtils

from cluster_fixture import TARGET_DEV, UNREAD_ROOT, configuration_only_context

SERVER = "https://kube-api.example.test:6443"
CA_PEM = b"-----BEGIN CERTIFICATE-----\nfixture-authority\n-----END CERTIFICATE-----\n"
CA_DATA = base64.b64encode(CA_PEM).decode()
TOKEN = "eyJhbGciOiJIUzI1NiIsImtpZCI6ImZpeHR1cmUifQ.payload.signature"

# Answers a `kubectl config view --flatten --minify -o json` that nothing wrote an answer
# for: a cluster whose address is unlike anything in this repository, so a value that came
# from a constant or from the platform configuration cannot accidentally match it.
LOCAL_KUBECONFIG_VIEW = json.dumps({
    "clusters": [{"name": "wherever-this-machine-talks-to", "cluster": {
        "server": "https://the-cluster-i-am-actually-on.example.test:6443",
        "certificate-authority-data": CA_DATA}}],
    "contexts": [], "current-context": "wherever-this-machine-talks-to", "users": [],
})


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


def test_the_text_is_the_product_of_a_dump_and_not_of_a_quoting_decision():
    """A document built from data dumps to itself, because a dump is canonical.

    Re-dumping what `yaml.safe_load` read back is the cheapest way to say "this text carries
    nothing but the document": a hand-rolled string with a stray line, a value pasted in
    unquoted and re-parsed as a mapping, or a key that appears twice all show up as a
    difference here.
    """
    text = kubeconfig()

    assert text == yaml.safe_dump(loaded(text), default_flow_style=False, sort_keys=True)


def test_the_emitted_kubeconfig_loads_into_the_shape_kubectl_reads():
    """One cluster, one user, one context, wired to each other.

    `current-context` has to name a context that exists, that context's cluster and user have
    to name entries that exist, and the namespace the account works in has to be inside the
    context — not only in a comment.
    """
    document = loaded(kubeconfig())

    assert document["apiVersion"] == "v1"
    assert document["kind"] == "Config"
    assert document["preferences"] == {}
    assert len(document["clusters"]) == len(document["users"]) == len(document["contexts"]) == 1

    context = document["contexts"][0]["context"]
    assert document["current-context"] == document["contexts"][0]["name"]
    assert context["cluster"] == document["clusters"][0]["name"]
    assert context["user"] == document["users"][0]["name"] == "ops-account"
    assert context["namespace"] == "ops-deployments"


def test_the_account_authenticates_with_the_token_it_was_given_and_nothing_else():
    text = kubeconfig()

    assert loaded(text)["users"][0]["user"] == {"token": TOKEN}


def test_the_cluster_is_addressed_by_address_and_by_its_authority_data():
    """Exactly the two facts that travel, and no third that cannot.

    An `insecure-skip-tls-verify`, or a `certificate-authority` naming a path, would be the
    document working here and failing on the machine it was written for.
    """
    cluster = loaded(kubeconfig())["clusters"][0]["cluster"]

    assert cluster == {"server": SERVER, "certificate-authority-data": CA_DATA}


@pytest.mark.parametrize("awkward_token", [
    "not-a-token\napiVersion: v1",            # a newline, and a document inside it
    "key: value\nother: ignored",             # something that reads as a mapping
    "#the-token-starts-with-a-comment",       # something that reads as a comment
    "@@@ reserved characters, and a colon: ",
    "  spaces around  ",
])
def test_a_token_that_looks_like_yaml_still_arrives_whole(awkward_token):
    """The reason the document is a dict: nothing here decides how a token is written.

    Every one of these is a value a real token has been seen to look like, and every one of
    them broke a heredoc. Interpolated, they truncate the document or move its keys; dumped,
    they are strings, and the person at the other end gets the credential they were promised.
    """
    text = kubeconfig(token=awkward_token)

    assert loaded(text)["users"][0]["user"]["token"] == awkward_token


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


def test_two_teams_on_one_environment_do_not_end_up_with_the_same_context():
    """The context name is the only thing that keeps two accounts apart in one config file.

    Teams on one cluster differ in this name; when it collides, the second paste silently
    overwrites the first and the first team is left talking to a namespace it does not own.
    """
    first = loaded(kubeconfig(team="ops"))["contexts"][0]["name"]
    second = loaded(kubeconfig(team="platform"))["contexts"][0]["name"]
    other_environment = loaded(kubeconfig(environment="prod", team="ops"))["contexts"][0]["name"]

    assert first != second != other_environment
    for document in (loaded(kubeconfig(team="ops")), loaded(kubeconfig(team="platform"))):
        assert document["current-context"] == document["contexts"][0]["name"]


def test_every_reference_moves_when_the_account_moves():
    """Nothing is spelled out twice, so a different account cannot half-land.

    The cluster, the context and the user are all derived from the same three arguments; a
    reference that stayed behind from a previous account is exactly the "valid YAML, wrong
    user" failure this file exists to keep shut.
    """
    document = loaded(kubeconfig(environment="prod", team="platform",
                                 service_account_name="platform-admin",
                                 namespace="platform-deployments"))
    context = document["contexts"][0]["context"]

    assert document["clusters"][0]["name"] == "prod-cluster" == context["cluster"]
    assert context["user"] == document["users"][0]["name"] == "platform-admin"
    assert context["namespace"] == "platform-deployments"


# --- where the two cluster facts come from ------------------------------------------------
#
# `kubectl config view` is what answers them, because the client resolves a certificate
# authority given as a file into a temporary path — useless inside a document that is about
# to be pasted somewhere else. `--flatten` re-inlines it, `--minify` narrows the answer to
# the context this run is on. Two `kubectl`s answer these: the recorder, for the argv, and an
# emulator that reads `$KUBECONFIG` and flattens it for real, because a fixed response from a
# recorder cannot tell "the code read this machine" apart from "the test told it what to say".

FLATTENING_KUBECTL = '''"""A kubectl that answers `config view` from the kubeconfig its environment names.

Only `config view` is answered, and the answer is computed rather than stored: the current
context's cluster, with a `certificate-authority` file inlined into
`certificate-authority-data` (that is `--flatten`) and every other context, user and cluster
dropped (that is `--minify`). Everything else exits 1, because a fake that answers what it
was not asked for hides more than it shows.
"""
import base64
import json
import os
import sys

import yaml


def die(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


# The call carries its cluster, so the subcommand is no longer at a fixed position. Read
# the words and drop the flags, exactly the way the harness's own reducer does it -- a fake that
# matched on argv positions would answer a correctly-flagged call with "not what I was asked",
# which is a false alarm about code that is right.
VALUE_FLAGS = {"-o", "--output", "--context", "--kubeconfig", "--cluster", "--user",
               "--request", "--namespace"}


def words(argv):
    picked, skip = [], False
    for word in argv:
        if skip:
            skip = False
            continue
        if word.startswith("-"):
            skip = word in VALUE_FLAGS
            continue
        picked.append(word)
    return picked


if words(sys.argv[1:])[:2] != ["config", "view"]:
    die("this kubectl answers only `config view`, asked: " + " ".join(sys.argv[1:]))

path = os.environ.get("KUBECONFIG") or os.path.expanduser("~/.kube/config")
try:
    with open(path) as handle:
        config = yaml.safe_load(handle)
except OSError as error:
    die("could not read the kubeconfig: " + str(error))

contexts = {entry["name"]: entry.get("context", {}) for entry in config.get("contexts") or []}
current = config.get("current-context")
if current not in contexts:
    die("current-context names no context that exists: " + str(current))

def one(kind):
    return {entry["name"]: entry for entry in config.get(kind) or []}

cluster_name = contexts[current].get("cluster")
user_name = contexts[current].get("user")
cluster = dict(one("clusters").get(cluster_name, {}).get("cluster") or {})
if "certificate-authority" in cluster:
    with open(cluster.pop("certificate-authority"), "rb") as handle:
        cluster["certificate-authority-data"] = base64.b64encode(handle.read()).decode()

print(json.dumps({
    "apiVersion": "v1", "kind": "Config", "current-context": current,
    "preferences": {},
    "clusters": [{"name": cluster_name, "cluster": cluster}],
    "contexts": [{"name": current, "context": contexts[current]}],
    "users": [one("users").get(user_name, {"name": user_name})],
}))
'''


def utils():
    """A `KubernetesUtils` for the `kubectl` this module still runs.

    `local_cluster_settings` is a kubeconfig read and so stays with kubectl — but it stays with
    *this* cluster, which means the argv assertion below has to see the flags a target carries.
    Without a target there is nothing to name and the call would be the bug the assertion
    exists to catch.
    """
    return KubernetesUtils(TARGET_DEV)


def flattening_kubectl(tmp_path, monkeypatch):
    """Put a `kubectl` in front of `PATH` that reads `$KUBECONFIG` instead of a test's answer."""
    directory = tmp_path / "flattening-kubectl"
    directory.mkdir()
    script = directory / "kubectl"
    script.write_text(f"#!{sys.executable}\n{FLATTENING_KUBECTL}")
    script.chmod(0o707)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
    return directory


def test_the_server_and_the_authority_are_read_from_the_kubeconfig_this_machine_carries(
        fake_binaries):
    """The two facts are the cluster's, not kubementat's, so they are asked of kubectl.

    The response carries an address no configuration file in this tree contains, which is
    what makes the assertion mean something.
    """
    fake_binaries.respond("kubectl", LOCAL_KUBECONFIG_VIEW, subcommand="config.view")

    server, authority = user_management.local_cluster_settings(utils())

    assert server == "https://the-cluster-i-am-actually-on.example.test:6443"
    assert authority == CA_DATA
    assert [call.argv for call in fake_binaries.calls() if call.program == "kubectl"] == [
        ["kubectl", "--context", "dev", "config", "view", "--flatten", "--minify", "-o", "json"]], \
        "the read was not pointed at the cluster this command resolved"


def test_the_ask_names_the_flags_that_make_the_answer_usable_somewhere_else(fake_binaries):
    """`--flatten` and `--minify` are not decoration; without them the text is wrong.

    `--flatten` is what turns a file-backed authority into data, and `--minify` is what stops
    the answer coming back with five clusters and the code having to guess.
    """
    fake_binaries.respond("kubectl", LOCAL_KUBECONFIG_VIEW, subcommand="config.view")

    user_management.local_cluster_settings(utils())

    argv = [argument for call in fake_binaries.calls() for argument in call.argv]
    assert "--flatten" in argv and "--minify" in argv


def test_an_authority_held_in_a_file_here_is_handed_over_as_data(tmp_path, monkeypatch):
    """The case `--flatten` exists for, measured instead of asserted about.

    A kubeconfig with two clusters and the current one behind a file: `local_cluster_settings`
    must return *this* context's address and the file's bytes, and the document printed from
    them must contain no path anybody here owns.
    """
    authority_file = tmp_path / "cluster-ca.crt"
    authority_file.write_bytes(CA_PEM)
    kubeconfig_path = tmp_path / "two-cluster-config"
    kubeconfig_path.write_text(
        "apiVersion: v1\nkind: Config\n"
        "current-context: on-this-one\n"
        "clusters:\n"
        "- name: elsewhere\n"
        "  cluster:\n"
        "    server: https://a-different-cluster.example.test:6443\n"
        "    certificate-authority-data: " + base64.b64encode(b"a different authority").decode() + "\n"
        "- name: this-one\n"
        "  cluster:\n"
        "    server: " + SERVER + "\n"
        "    certificate-authority: " + str(authority_file) + "\n"
        "contexts:\n"
        "- name: elsewhere\n  context: {cluster: elsewhere, user: nobody}\n"
        "- name: on-this-one\n  context: {cluster: this-one, user: someone}\n"
        "users:\n"
        "- name: nobody\n  user: {}\n"
        "- name: someone\n  user: {}\n")
    flattening_kubectl(tmp_path, monkeypatch)
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig_path))

    server, authority = user_management.local_cluster_settings(utils())
    text = kubeconfig(server=server, certificate_authority_data=authority)

    assert server == SERVER, "the address came from a cluster this kubeconfig is not on"
    assert authority == CA_DATA, "the authority is not the bytes of the file that holds it"
    assert str(authority_file) not in text, "a local path travelled into the document"
    assert loaded(text)["clusters"][0]["cluster"]["certificate-authority-data"] == CA_DATA


def test_an_authority_that_never_got_flattened_in_is_refused_and_not_printed(fake_binaries):
    """A path plus no data is a document that works here and nowhere else.

    Rather than emit one, the command says what it looked at. That is the same rule the rest
    of the module runs on: an answer the flow cannot use is a reason to stop, not a warning.
    """
    fake_binaries.respond("kubectl", json.dumps({"clusters": [{"name": "one", "cluster": {
        "server": SERVER, "certificate-authority": "/home/operator/clusters/dev-ca.crt"}}]}),
        subcommand="config.view")

    with pytest.raises(ClickException) as raised:
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

    with pytest.raises(ClickException) as raised:
        user_management.local_cluster_settings(utils())

    assert "kubectl config view" in str(raised.value)


def test_a_config_view_that_answers_nothing_is_not_read_as_a_cluster_with_no_address(fake_binaries):
    """Empty stdout is not a cluster; it is kubectl saying nothing.

    `json.loads("")` is the ValueError this turns into a message. Silent here would mean a
    kubeconfig with a `server` of `None` on the other end.
    """
    fake_binaries.respond("kubectl", "", subcommand="config.view")

    with pytest.raises(ClickException) as raised:
        user_management.local_cluster_settings(utils())

    assert "kubeconfig this command resolved" in str(raised.value)
