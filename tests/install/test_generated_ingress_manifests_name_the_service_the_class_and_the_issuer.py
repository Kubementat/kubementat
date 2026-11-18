"""The Ingress generator: what the document contains, where its class came from, and what is said when
it could not look.
"""


import pytest
import yaml
from click.testing import CliRunner
from cluster_fixture import configuration_only_context
from conftest import FIXTURE_TEAM

from cli.commands.kubernetes import create_ingress
from kubementat.automations.ingress import (
    GeneratedIngress,
    IngressGenerator,
)
from kubementat.core.config.config import Config
from kubementat.errors import (
    IngressArgumentNotSuppliedError,
    IngressPortNotUsableError,
    IngressTargetExistsError,
)

DOMAIN = "nginx-example.dev1.example.com"

# `routing.py` says Traefik's own default IngressController.className is `traefik`, which is the
# only reason kubementat is allowed to answer when configuration does not. A different value here
# would mean the fallback had stopped matching what the chart installs, so a test pins it.
DEFAULT_CLASS = "traefik"

def generator(environment: str = "dev") -> IngressGenerator:
    """The generator the way `kmt kubernetes create-ingress` builds it, from one `Context`."""
    return IngressGenerator(configuration_only_context(
        environment, FIXTURE_TEAM, Config(environment, FIXTURE_TEAM, preload_configs=False)))


def generate(environment: str = "dev") -> GeneratedIngress:
    """The document for the fixture's own environment, built the way the command builds it."""
    return generator(environment).generate(DOMAIN, "nginx-example", "80")


# ------------------------------------------------------------------------------------------
# The document, and the shape it must not drift into.
# ------------------------------------------------------------------------------------------


def test_the_written_text_is_the_document_in_the_order_a_person_reads_it(fixture_repo,
                                                                         dry_run_env):
    """`sort_keys=False` keeps the file readable: a person opens a generated manifest before they apply
    it, so `metadata` still comes before `spec` the way the script wrote it, and nothing in the file
    is missing that the object has.
    """
    answer = generate("dev")
    text = answer.text()
    assert text.startswith("apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:")
    assert text.index("metadata:") < text.index("spec:")
    assert yaml.safe_load(text) == answer.manifest


# ------------------------------------------------------------------------------------------
# Where the class comes from.
# ------------------------------------------------------------------------------------------


def test_generating_without_an_environment_says_no_configuration_was_read(fixture_repo,
                                                                          dry_run_env):
    """`ENVIRONMENT` is the last chance when the argument is not passed, so an environment that comes
    from the variable is not an error: kubementat writes its default and says it read nothing. The
    sentence is the script's, because it named the two places it looked, and `routing.py`'s
    "kubementat defaulted to it" phrasing cannot say "I read nothing" without sounding like the same
    claim `Config` makes when the file is there.
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
    different problem from naming no environment, and the sentence names the path it would have read
    and the question to ask back -- `Config` cannot warn for a file it was never given.
    """
    fixture_repo.platform_config.joinpath("dev", "static.json").unlink()
    answer = generate("dev")
    assert answer.answered_by == "default"
    assert answer.ingress_class == DEFAULT_CLASS
    assert "static.json" in answer.warnings[0]
    assert str(fixture_repo.platform_config / "dev" / "static.json") in answer.warnings[0]
    assert "initialized" in answer.warnings[0]


# ------------------------------------------------------------------------------------------
# Where the operator is told.
# ------------------------------------------------------------------------------------------

def test_the_default_warning_lands_on_stderr_and_never_inside_the_manifest(
        fixture_repo, dry_run_env, monkeypatch):
    """The manifest goes to stdout and a warning that followed it would be YAML the caller pipes into
    `kubectl apply`. This is the script's separation (`echo` outside the heredoc, so `| kubectl
    apply -f -` stayed usable) and the only one that is worth a test on its own.
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
    """The generator's "no environment was given" answer is not the command's any more."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    result = CliRunner(mix_stderr=False).invoke(create_ingress, [DOMAIN, "svc", "80"])

    assert result.exit_code == 2, result.stderr
    assert "ENVIRONMENT" in result.stderr
    assert result.stdout == "", "an unresolved environment still printed a manifest"


def test_a_command_that_read_its_environment_says_nothing_on_either_stream(fixture_repo,
                                                                           dry_run_env):
    """A run that found its static.json adds no warning of this generator's; config.py's warn-once is a
    different voice and is asserted in
    `test_an_unset_class_falls_back_to_traefik_because_that_is_what_the_chart_installs`.
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
    """A generated manifest is edited before it is applied, so a rerun refuses to overwrite it."""
    target = tmp_path / "nginx-example.yaml"
    target.write_text("# edited by a person, not by kubementat\n")
    with pytest.raises(IngressTargetExistsError) as excinfo:
        generator("dev").write(generate("dev"), target)
    assert "already exists" in str(excinfo.value)
    assert target.read_text().startswith("# edited")


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
    """A missing domain, service or port is refused before a document with an empty field is written."""
    with pytest.raises(IngressArgumentNotSuppliedError):
        generator("dev").generate(domain, service, port)


@pytest.mark.parametrize("port", ["http", "80abc", "1.5", "-1"])
def test_a_port_that_is_not_a_number_is_refused(fixture_repo, dry_run_env, port):
    """A port that is not a number is refused before a manifest Kubernetes would reject is written."""
    with pytest.raises(IngressPortNotUsableError):
        generator("dev").generate(DOMAIN, "svc", port)


def test_a_missing_port_is_refused_by_the_command_before_anything_is_printed(fixture_repo,
                                                                             dry_run_env):
    """The port is the last argument and the only one that can be left off; a missing one is Click's
    business and it prints a usage error rather than half a document.
    """
    result = CliRunner().invoke(create_ingress, [fixture_repo.environment, DOMAIN])
    assert result.exit_code != 0
    assert "kind: Ingress" not in result.output


# ------------------------------------------------------------------------------------------
# It is a Kubernetes object, not only YAML.
# ------------------------------------------------------------------------------------------
