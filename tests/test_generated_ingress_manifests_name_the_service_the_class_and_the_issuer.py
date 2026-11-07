"""The Ingress generator: what the document contains, where its class came from, and what is said
when it could not look.

`kmt create-ingress` replaced a script whose whole job was to print one YAML document, so the tests
here are about that document and the three things that decide it: the service the caller named, the
class the environment's configuration answers, and the issuer the certificate is asked of. The
parity file compares this to the deleted script's own output; this file owns the parts that
comparison cannot see, which is why the class's two fallbacks and the missing arguments are here.
"""

import json
import logging
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from kubernetes import client, utils

from cli.commands.utilities import create_ingress
from conftest import FIXTURE_TEAM
from cluster_fixture import configuration_only_context
from kubementat.core.config.config import Config
from kubementat.errors import (IngressArgumentNotSuppliedError, IngressPortNotUsableError,
                              IngressTargetExistsError)
from kubementat.automations.ingress import (
    DEFAULT_INGRESS_CLASS,
    GeneratedIngress,
    IngressGenerator,
)
DOMAIN = "nginx-example.dev1.example.com"

# `routing.py` says Traefik's own default IngressController.className is `traefik`, which is the
# only reason kubementat is allowed to answer when configuration does not. A different value here
# would mean the fallback had stopped matching what the chart installs, so a test pins it.
DEFAULT_CLASS = "traefik"

def generator(environment: str = "dev") -> IngressGenerator:
    """The generator the way `kmt create-ingress` builds it, from one `Context`.

    The `Context` comes from `cluster_fixture.configuration_only_context` rather than
    `Context.build` because this file asserts about a YAML document: what it needs is the real
    `Config` for `environment` (the read that answers `TRAEFIK_INGRESS_CLASS`) and no cluster claim,
    and that `Config` is the same lazy construction the class used to do for itself. So the read
    under test has not changed — only where the object comes from.
    """
    return IngressGenerator(configuration_only_context(
        environment, FIXTURE_TEAM, Config(environment, FIXTURE_TEAM, preload_configs=False)))


def generate(environment: str = "dev") -> GeneratedIngress:
    """The document for the fixture's own environment, built the way the command builds it."""
    return generator(environment).generate(DOMAIN, "nginx-example", "80")


# ------------------------------------------------------------------------------------------
# The document, and the shape it must not drift into.
# ------------------------------------------------------------------------------------------

def test_a_generated_manifest_names_the_service_the_domain_and_the_port(fixture_repo,
                                                                        dry_run_env):
    document = generate("dev").manifest
    assert document["apiVersion"] == "networking.k8s.io/v1"
    assert document["kind"] == "Ingress"
    assert document["metadata"]["name"] == "nginx-example-ingress"
    rule = document["spec"]["rules"][0]
    assert rule["host"] == DOMAIN
    path = rule["http"]["paths"][0]
    assert path["pathType"] == "Prefix"
    assert path["path"] == "/"
    assert path["backend"]["service"]["name"] == "nginx-example"
    assert path["backend"]["service"]["port"]["number"] == 80


def test_a_generated_manifest_covers_the_domain_in_tls_and_names_the_secret_by_the_service(
        fixture_repo, dry_run_env):
    """One `hosts` entry, one secret, both derived from the same two arguments: cert-manager
    writes the certificate into `secretName` and Traefik reads it from there, so a host listed
    twice or a secret named after the Ingress instead would be a document that applies and then
    never serves traffic."""
    document = generate("dev").manifest
    assert document["spec"]["tls"] == [{"hosts": [DOMAIN],
                                        "secretName": "nginx-example-cert"}]


def test_a_generated_manifest_asks_cert_manager_for_a_wildcard_certificate(fixture_repo,
                                                                            dry_run_env):
    """The two annotations are what a cluster needs before `kubectl apply` can mean TLS. They
    name a cluster-scoped resource that is not part of this document, so a value that drifted from
    the `ClusterIssuer` other manifests install would produce an Ingress whose certificate request
    is never satisfied.
    """
    document = generate("dev").manifest
    assert document["metadata"]["annotations"] == {
        "cert-manager.io/cluster-issuer": "letsencrypt",
        "kubernetes.io/tls-acme": "true",
    }


def test_an_unconfigured_nginx_annotation_is_not_carried_into_a_generated_manifest(
        fixture_repo, dry_run_env):
    """The script copied `kubernetes.io/ingress.class` from `$NGINX_INGRESS_CLASS` whenever that
    was set, even though the shipped Ingress manifests never write it and `helmfile`'s Traefik
    entrypoint redirect already answers it cluster-wide. It is not a kubementat setting, so this
    is the one thing the migration dropped on purpose; the parity file declares it."""
    document = generate("dev").manifest
    assert "kubernetes.io/ingress.class" not in document["metadata"]["annotations"]
    assert set(document["spec"]) == {"ingressClassName", "rules", "tls"}


def test_the_written_text_is_the_document_in_the_order_a_person_reads_it(fixture_repo,
                                                                         dry_run_env):
    """`sort_keys=False` keeps the file readable: a person opens a generated manifest before they
    apply it, so `metadata` still comes before `spec` the way the script wrote it, and nothing in
    the file is missing that the object has."""
    answer = generate("dev")
    text = answer.text()
    assert text.startswith("apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:")
    assert text.index("metadata:") < text.index("spec:")
    assert yaml.safe_load(text) == answer.manifest


# ------------------------------------------------------------------------------------------
# Where the class comes from.
# ------------------------------------------------------------------------------------------

def test_the_ingress_class_comes_from_the_environment_configuration_when_the_key_is_there(
        fixture_repo, dry_run_env):
    """Configuration answers and code does not. `TRAEFIK_INGRESS_CLASS` is the key `routing.py`
    reads, so an environment running a class under another name is corrected in one file and both
    commands change together -- which is what the script's own `jq` could not promise, because it
    read the same key through a relative path and stayed silent when it was missing.
    """
    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    answer = generate("dev")
    assert answer.ingress_class == "my-class"
    assert answer.manifest["spec"]["ingressClassName"] == "my-class"
    assert answer.answered_by == "configuration"
    assert answer.warnings == ()


def test_an_unset_class_falls_back_to_traefik_because_that_is_what_the_chart_installs(
        fixture_repo, dry_run_env, caplog):
    """A real environment whose static.json does not set the key: `Config` answers and warns
    once, and this generator adds nothing of its own -- silence here belongs to config.py, which
    is where every other default warning lives.
    """
    with caplog.at_level(logging.WARNING):
        answer = generate("dev")
    assert answer.ingress_class == DEFAULT_CLASS
    assert answer.answered_by == "configuration"
    assert answer.warnings == ()
    assert any("built-in default 'traefik'" in record.message for record in caplog.records)


def test_generating_without_an_environment_says_no_configuration_was_read(fixture_repo,
                                                                          dry_run_env):
    """`ENVIRONMENT` is the last chance when the argument is not passed, so an environment that
    comes from the variable is not an error: kubementat writes its default and says it read nothing.
    The sentence is the script's, because it named the two places it looked, and `routing.py`'s
    "kubementat defaulted to it" phrasing cannot say "I read nothing" without sounding like the
    same claim `Config` makes when the file is there.
    """
    answer = generate("")
    assert answer.answered_by == "default"
    assert answer.ingress_class == DEFAULT_CLASS
    assert len(answer.warnings) == 2
    assert "no environment was given" in answer.warnings[0]
    assert "$ENVIRONMENT" in answer.warnings[0]
    assert "never be routed to" in answer.warnings[1]


def test_an_uninitialized_environment_says_which_file_it_expected_and_where(fixture_repo,
                                                                            dry_run_env,
                                                                            monkeypatch):
    """The other reason nothing is read: the environment's `static.json` is not there. That is a
    different problem from naming no environment, and the sentence names the path it
    would have read and the question to ask back -- `Config` cannot warn for a file it was never
    given.
    """
    fixture_repo.platform_config.joinpath("dev", "static.json").unlink()
    answer = generate("dev")
    assert answer.answered_by == "default"
    assert answer.ingress_class == DEFAULT_CLASS
    assert "static.json" in answer.warnings[0]
    assert str(fixture_repo.platform_config / "dev" / "static.json") in answer.warnings[0]
    assert "initialized" in answer.warnings[0]


def test_the_two_silences_are_not_the_same_silence(fixture_repo, dry_run_env):
    """Three answers, and a reader can tell which one happened: configuration said so,
    configuration had nothing to say, or nothing was read. Collapsing the last two is what made
    the script's warning ambiguous, and it is the reason a test of one cannot stand in for a test
    of the other.
    """
    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    configured = generate("dev")
    fixture_repo.platform_config.joinpath("dev", "static.json").unlink()
    unread = generate("dev")
    assert configured.answered_by == "configuration" and configured.warnings == ()
    assert unread.answered_by == "default" and unread.warnings
    assert configured.ingress_class != unread.ingress_class


# ------------------------------------------------------------------------------------------
# Where the operator is told.
# ------------------------------------------------------------------------------------------

def test_the_default_warning_lands_on_stderr_and_never_inside_the_manifest(
        fixture_repo, dry_run_env, monkeypatch):
    """The manifest goes to stdout and a warning that followed it would be YAML the caller pipes
    into `kubectl apply`. This is the script's separation (`echo` outside the heredoc, so `|
    kubectl apply -f -` stayed usable) and the only one that is worth a test on its own.

    It now has a second line to keep off stdout: `@kmt_context` prints the resolution report before
    the body runs, and this is the one command whose stdout is a document rather than a
    report. That is why the report is stderr-only, so the assertion here is the same one as before
    with one more thing to check — the YAML is clean, and everything that is not the YAML is not on
    stdout. The environment is named explicitly, because an unnamed one is refused and the
    generator's own "no environment was given" branch is now only reachable by a library caller.
    """
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    result = CliRunner(mix_stderr=False).invoke(
        create_ingress, [fixture_repo.environment, DOMAIN, "svc", "80"])
    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.stdout
    document = yaml.safe_load(result.stdout)
    assert document["kind"] == "Ingress"
    assert document["spec"]["ingressClassName"] == DEFAULT_CLASS
    assert "cluster " in result.stderr, result.stderr


def test_an_environment_the_command_cannot_name_is_refused_and_writes_no_manifest(
        fixture_repo, dry_run_env, monkeypatch):
    """The generator's "no environment was given" answer is not the command's any more.

    A command that writes a manifest to stdout has to say which cluster it checked before it
    decided what that manifest routes through, and the state where the argument and
    `$ENVIRONMENT` are both empty is exactly the state kubementat no longer guesses in.
    """
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    result = CliRunner(mix_stderr=False).invoke(create_ingress, [DOMAIN, "svc", "80"])

    assert result.exit_code == 2, result.stderr
    assert "ENVIRONMENT" in result.stderr
    assert result.stdout == "", "an unresolved environment still printed a manifest"


def test_a_command_that_read_its_environment_says_nothing_on_either_stream(fixture_repo,
                                                                           dry_run_env):
    """A run that found its static.json adds no warning of this generator's; config.py's warn-once
    is a different voice and is asserted in
    `test_an_unset_class_falls_back_to_traefik_because_that_is_what_the_chart_installs`.

    The environment is named on the line and not read from `dry_run_env`'s pinned `ENVIRONMENT`,
    because a command cannot be handed its third argument without its first — Click fills the
    positionals from the left. That the variable answers when nothing is typed is the refusal test's
    half, asserted one test above.
    """
    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    result = CliRunner(mix_stderr=False).invoke(
        create_ingress, [fixture_repo.environment, DOMAIN, "svc", "80"])
    assert result.exit_code == 0, result.output
    assert "WARNING" not in result.stdout
    assert "WARNING" not in result.stderr
    assert yaml.safe_load(result.stdout)["spec"]["ingressClassName"] == "my-class"


def test_the_manifest_is_written_where_the_caller_asked_for_it(fixture_repo, dry_run_env, tmp_path):
    # `generate` reads the environment's own `static.json` for the class, so the fixture that
    # answers for every other test here has to answer for this one: a root is named and never
    # located, and this file's generator resolves nothing of its own.
    target = tmp_path / "manifests" / "nginx-example.yaml"
    answer = generate("dev")
    generator("dev").write(answer, target)
    assert yaml.safe_load(target.read_text()) == answer.manifest
    assert target.parent.is_dir(), "the directory is created rather than demanded"


def test_a_manifest_that_would_write_over_someone_elses_work_is_refused(fixture_repo, dry_run_env,
                                                                       tmp_path):
    """A generated manifest is a starting point someone edits before they apply it, so a rerun
    that quietly deleted their work would be worse than an error. Same rule the pipeline-run
    generator keeps; bash's `>` never had the hesitation, and the parity file declares the
    difference.
    """
    target = tmp_path / "nginx-example.yaml"
    target.write_text("# edited by a person, not by kubementat\n")
    with pytest.raises(IngressTargetExistsError) as excinfo:
        generator("dev").write(generate("dev"), target)
    assert "already exists" in str(excinfo.value)
    assert target.read_text().startswith("# edited")


def test_stdout_is_the_default_because_that_is_what_the_script_did(fixture_repo, dry_run_env,
                                                                   tmp_path, monkeypatch):
    """`cat << EOF` was the whole command; `| kubectl apply -f -` is the reason the default stayed
    stdout rather than a file nobody asked for.
    """
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(create_ingress, ["dev", DOMAIN, "svc", "80"])
    assert result.exit_code == 0, result.output
    assert "kind: Ingress" in result.stdout
    assert list(Path(".").glob("*.yaml")) == []


# ------------------------------------------------------------------------------------------
# Refusals.
# ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("domain, service, port", [
    ("", "nginx-example", "80"),
    (DOMAIN, "", "80"),
    (DOMAIN, "nginx-example", ""),
])
def test_an_empty_argument_is_refused_rather_than_printing_a_half_manifest(fixture_repo,
                                                                           dry_run_env,
                                                                           domain, service,
                                                                           port):
    """bash's `set -euo pipefail` turned an unset argument into an empty field inside a document
    that looked plausible enough to apply -- `- host:` with no host, or a rule pointing at a
    service that does not exist. A missing name is an error here, before a misleading document
    exists.
    """
    with pytest.raises(IngressArgumentNotSuppliedError):
        generator("dev").generate(domain, service, port)


@pytest.mark.parametrize("port", ["http", "80abc", "1.5", "-1"])
def test_a_port_that_is_not_a_number_is_refused(fixture_repo, dry_run_env, port):
    """bash interpolated `$SERVICE_PORT` verbatim, so `create_ingress_yml.sh a.b svc http` wrote
    `number: http` and Kubernetes rejected it on apply. This is the same failure, moved earlier;
    the parity file declares it as a Python-only strictness.
    """
    with pytest.raises(IngressPortNotUsableError):
        generator("dev").generate(DOMAIN, "svc", port)


def test_a_missing_port_is_refused_by_the_command_before_anything_is_printed(fixture_repo,
                                                                             dry_run_env):
    """The port is the last argument and the only one that can be left off; a missing one is
    Click's business and it prints a usage error rather than half a document.
    """
    result = CliRunner().invoke(create_ingress, [fixture_repo.environment, DOMAIN])
    assert result.exit_code != 0
    assert "kind: Ingress" not in result.output


# ------------------------------------------------------------------------------------------
# It is a Kubernetes object, not only YAML.
# ------------------------------------------------------------------------------------------

def test_the_generated_manifest_deserializes_as_a_real_ingress_object(fixture_repo,
                                                                      dry_run_env):
    """`yaml.safe_load` proves the file parses. The client proves it is an `Ingress`: a field
    spelled wrongly survives the first and is dropped by the second, so this is the difference
    between "prints like YAML" and "would have been applied".
    """
    answer = generate("dev")
    api = client.ApiClient(configuration=client.Configuration())
    document = yaml.safe_load(answer.text())
    # The client reads JSON off the wire, so the file's YAML is loaded and handed over the way a
    # controller would hand it back. `yaml.safe_load` alone proves the file parses; the typed
    # object is what proves it is the right kind of thing.
    object_ = api.deserialize(_Response(json.dumps(document)), "V1Ingress")

    assert object_.metadata.name == "nginx-example-ingress"
    assert object_.spec.ingress_class_name == DEFAULT_CLASS
    assert object_.spec.rules[0].host == DOMAIN
    assert object_.spec.rules[0].http.paths[0].backend.service.port.number == 80
    assert object_.spec.tls[0].secret_name == "nginx-example-cert"


def test_the_class_and_the_api_version_point_at_the_ingress_api_the_cluster_serves(
        fixture_repo, dry_run_env, monkeypatch):
    """The claim a generated manifest has to survive is that `apiVersion` and `kind` resolve to a
    real client, because that is how `kubectl apply` and `kubernetes.utils` both find the endpoint.
    Patching the API class this resolves to shows the document would reach the networking v1
    Ingress endpoint in the namespace the operator applies into -- which this generator is not
    allowed to decide for them.
    """
    asked = []

    class RecordingIngressApi:
        def __init__(self, api_client=None):
            pass

        def create_namespaced_ingress(self, body, **kwargs):
            asked.append((body, kwargs))
            return {"status": "created"}

    monkeypatch.setattr(client, "NetworkingV1Api", RecordingIngressApi)
    answer = generate("dev")
    document = yaml.safe_load(answer.text())

    utils.create_from_dict(object(), document, namespace="dev")

    assert len(asked) == 1
    body, kwargs = asked[0]
    assert kwargs == {"namespace": "dev"}
    assert body["kind"] == "Ingress"
    assert body["metadata"]["name"] == "nginx-example-ingress"
    assert body["spec"]["ingressClassName"] == DEFAULT_CLASS


class _Response:
    """The shape `ApiClient.deserialize` reads: a body and a status."""

    def __init__(self, data: str):
        self.data = data
        self.status = 200
        self.headers = {}
