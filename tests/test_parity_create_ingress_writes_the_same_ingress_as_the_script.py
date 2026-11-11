"""Parity: the Ingress generator, against the script that wrote the same document as text.

The migration deleted `scripts/utilities/kubernetes/create_ingress_yml.sh` (105 lines). This file
runs the
blob at `git show bb30fb2:scripts/utilities/kubernetes/create_ingress_yml.sh` and
`kubementat.ingress_generator.IngressGenerator` over the same arguments and the same fixture tree,
and asks whether the two documents are the same document. The blob is the other half of the
comparison and not a copy of the code's comments: it is also where the `jq` filter, the
`cat << EOF` layout and the warning sentences are read out of.

The claim is narrower than the other parity files make theirs look, and deliberately so. This script
never spoke to a cluster: no `kubectl`, no `helm`, nothing but `jq` and a heredoc. So the comparison
is between two *documents*, and the operation comparison exists only to prove nothing was left
behind in the translation -- an empty shim log and an empty client log are the finding here, not a
missing test.

What is declared rather than compared, because the harness cannot see it and it is not a regression:

* **the heredoc's comments.** Two lines of prose sat inside the document (`# add an annotation
  indicating the issuer to use.` and `# < placing a host in the TLS config ...`) and a document is
  not a comment. They are the only text the migration drops on purpose; `kind`, `apiVersion`, every
  annotation, both derived names, the host and the port are not.
* **quoting.** bash wrote `kubernetes.io/tls-acme: "true"`; `yaml.safe_dump` writes `'true'`. Same
  string, different pair of marks, which is why the comparison below is made on `safe_load` and not
  on text.
* **where the class is read, and who warns.** bash `jq`'d one file out of a path built from its own
  location; Python asks `Config`, which reads the same key of the same `static.json` and looks at
  the team-level files on the way. When the environment file is there and the key is not, bash
  stayed silent and `config.py` warns once. That extra line is Python-only, it is on stderr, and it
  is the same rule every other config-driven command already follows.
* **the port.** bash interpolated `$SERVICE_PORT` wherever it was told, so `http` produced
  `number: http`. Python refuses to build that document at all -- a stricter answer to a bad
  argument, not a different answer to a good one, and both halves are run.

The optional fourth argument is compared in the same shape on both sides: bash used
`${4:-$ENVIRONMENT}`, and the command reads a fourth argument with `$ENVIRONMENT` behind it.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from cluster_parity import compare, operations_from_calls
from conftest import run_bash
from cluster_fixture import configuration_only_context
from kubementat.config import Config
from kubementat.ingress_generator import (IngressGenerator,
                                          IngressPortNotUsableError)

def generator(environment: str) -> IngressGenerator:
    """The Python side of the comparison, built the way `kmt create-ingress` builds it.

    The `Config` inside is the real one for `environment` -- the read that answers the class is the
    half being compared -- and the `Context` is the configuration-only shape from
    `cluster_fixture`, because this file asserts about a document and not about which cluster
    `environment` addresses.
    """
    return IngressGenerator(configuration_only_context(
        environment, "fixture", Config(environment, "fixture", preload_configs=False)))

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/utilities/kubernetes/create_ingress_yml.sh"

# The commit the script was still in. `HEAD:` is what a first draft names and what breaks the file
# one commit later: after the deletion lands, `git show HEAD:...` finds nothing and this comparison
# skips itself out from under whoever reads it next.
BLOB_REVISION = "bb30fb2"

DOMAIN = "nginx-example.dev1.example.com"
SERVICE = "nginx-example"
PORT = "80"

# The three arguments every usage of this script named, as the script ordered them. The environment
# is not in this tuple: the script took it as an optional fourth, the command takes it as the first
# and required positional, so the parity passes it separately and never positionally.
ARGUMENTS = (DOMAIN, SERVICE, PORT)


def blob_text():
    """The script's own text, out of git history. Skips, loudly, if the history is missing."""
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{SCRIPT}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable in this checkout: git show "
                    f"{BLOB_REVISION}:{SCRIPT} exited {result.returncode} "
                    f"({result.stderr.strip()}). Both halves of this comparison need it, so "
                    f"without it there is nothing to compare.")
    return result.stdout


def blob_script(fixture_repo):
    """The blob, written back where it lived.

    The path is not housekeeping. The script built its configuration path by climbing three
    directories out of itself, so the blob goes at the depth it was deleted from and reads the
    fixture's `platform_config` the way it used to read a real one; `Config` resolves the same tree
    from `KUBEMENTAT_HOME`, which is the other half of `dry_run_env`. Two derivations, one file --
    a comparison where the two sides read different trees would prove nothing about the migration.
    """
    path = fixture_repo.scripts / "utilities/kubernetes/create_ingress_yml.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(blob_text())
    return path


def environment_without(dry_run_env, *names) -> dict:
    """The fixture-run environment, minus the variables a test is pretending are unset.

    `dry_run_env` pins `ENVIRONMENT` for every command, so a test of "the argument was not
    given and `$ENVIRONMENT` did not answer either" has to take it back out rather than hand the
    script a value the operator never set.
    """
    environment = dict(dry_run_env)
    for name in names:
        environment.pop(name, None)
    return environment


def from_script(fixture_repo, arguments, env) -> tuple:
    """What the script printed, parsed, and what it said while it printed it."""
    result = run_bash(blob_script(fixture_repo), arguments, env=env)
    assert result.returncode == 0, f"the script exited {result.returncode}: {result.stderr}"
    return yaml.safe_load(result.stdout), result.stderr


def from_code(fixture_repo, arguments, environment: str) -> tuple:
    """What the command produced, and the warnings it would put on stderr.

    `environment` is spelled explicitly here because the CLI reads it from the first positional or
    from `$ENVIRONMENT` inside Click, and a test that left that to the fixture could not tell a
    missing argument from a pinned environment variable.
    """
    answer = generator(environment).generate(*arguments)
    return answer.manifest, answer.warnings


# ------------------------------------------------------------------------------------------
# The document.
# ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("configured_class", [None, "my-class"], ids=["key-absent", "key-set"])
def test_the_two_sides_write_the_same_ingress_from_the_same_arguments(fixture_repo, dry_run_env,
                                                                      configured_class):
    """The whole comparison, run twice: the key configured, and the key absent.

    `dry_run_env` is what makes both sides read the fixture rather than this checkout, and it is
    what keeps the script's own path derivation honest -- bash climbs out of `scripts/`, `Config`
    walks down from `KUBEMENTAT_HOME`, and both land on the same `static.json`.
    """
    fixture_repo.environment_static(
        **({"TRAEFIK_INGRESS_CLASS": configured_class} if configured_class else {}))
    arguments = (*ARGUMENTS, "dev")

    script, _ = from_script(fixture_repo, arguments, dry_run_env)
    code, warnings = from_code(fixture_repo, ARGUMENTS, "dev")

    assert script == code
    assert warnings == ()
    assert code["spec"]["ingressClassName"] == (configured_class or "traefik")
    assert code["metadata"]["name"] == "nginx-example-ingress"
    assert code["spec"]["tls"] == [{"hosts": [DOMAIN], "secretName": "nginx-example-cert"}]


def test_the_heredoc_comments_are_the_only_text_the_migration_drops(fixture_repo, dry_run_env):
    """The comments inside the document, compared as text and not as data.

    `safe_load` cannot see a comment, so the claim has to be made by reading the blob. It is a test
    and not a note because a comment inside a generated file is the part a reader notices first,
    and the only part of this script that could have gone missing without showing up in what the
    manifest does.
    """
    blob = blob_text()
    assert "# add an annotation indicating the issuer to use." in blob
    assert "# < placing a host in the TLS config" in blob

    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    script, _ = from_script(fixture_repo, (*ARGUMENTS, "dev"), dry_run_env)
    code, _ = from_code(fixture_repo, ARGUMENTS, "dev")
    assert script == code, "the two documents agree, so what is left of the difference is text"


def test_the_script_read_the_key_from_the_same_file_the_code_reads(fixture_repo, dry_run_env):
    """Where the class came from, on both sides, off the blob's own text.

    The script said `jq -r '.TRAEFIK_INGRESS_CLASS // "traefik"' "$STATIC_JSON"`, and `STATIC_JSON`
    was `${PLATFORM_CONFIG_DIRECTORY}/${ENVIRONMENT}/static.json`. The code's read is
    `Config._component_value("traefik", "INGRESS_CLASS", ...)`, which resolves to that same key of
    that same file through `component_config_key`. Both are given the same tree and both answer the
    configured class; then the key is removed and only the silence differs, which is declared above
    and asserted rather than glossed.
    """
    blob = blob_text()
    assert "jq -r '.TRAEFIK_INGRESS_CLASS // \"traefik\"'" in blob, \
        "the script no longer reads the class the way this comparison says, so the equivalence of " \
        "the two reads is unproven"
    assert 'STATIC_JSON="${PLATFORM_CONFIG_DIRECTORY}/${ENVIRONMENT}/static.json"' in blob

    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    script, script_stderr = from_script(fixture_repo, (*ARGUMENTS, "dev"), dry_run_env)
    code, code_warnings = from_code(fixture_repo, ARGUMENTS, "dev")
    assert script["spec"]["ingressClassName"] == code["spec"]["ingressClassName"] == "my-class"
    assert script_stderr == "", "the script said something about a class it was given"
    assert code_warnings == (), "the generator said something about a class it was given"

    fixture_repo.environment_static()
    script, script_stderr = from_script(fixture_repo, (*ARGUMENTS, "dev"), dry_run_env)
    code, code_warnings = from_code(fixture_repo, ARGUMENTS, "dev")
    assert script["spec"]["ingressClassName"] == code["spec"]["ingressClassName"] == "traefik"
    assert script_stderr == "", "bash had a file to read and a default that matched the chart"
    assert code_warnings == (), "the file was there, so config.py answers on its own account and " \
                                "this generator adds nothing of its own"


# ------------------------------------------------------------------------------------------
# The warning, which is not a document.
# ------------------------------------------------------------------------------------------

def test_reading_no_configuration_is_said_by_both_sides_for_the_two_reasons_there_are(
        fixture_repo, dry_run_env, monkeypatch):
    """The part of the script that was prose rather than YAML, and both reasons it appeared.

    `no environment was given` and `there is no static.json at <path>` are compared as claims, not
    as strings: what has to survive is that each side says *that* it
    read nothing and *why*, in the order the two fixes come to mind. The script's third line --
    the default it wrote, and the sentence about an Ingress that is never routed to -- is asserted
    too, because that is the warning an operator acts on.

    The second sentence's *reason* half differs by design now: the script asks whether the
    environment was `initialized`, which is the verb the bash script ran alongside, and this side
    asks whether it has been *prepared*, which is the verb that exists. The claim under both is the
    same two facts -- no `static.json`, and the path it was looked for at -- so that is what is
    compared, and the script's own wording is asserted only against the script's own stderr.
    """
    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    arguments = (*ARGUMENTS, "dev")

    script, script_stderr = from_script(fixture_repo, ARGUMENTS,
                                        environment_without(dry_run_env, "ENVIRONMENT"))
    code, code_warnings = from_code(fixture_repo, ARGUMENTS, "")
    assert script["spec"]["ingressClassName"] == code["spec"]["ingressClassName"] == "traefik"
    assert "did not read TRAEFIK_INGRESS_CLASS" in script_stderr
    assert "no environment was given" in script_stderr
    assert "never be routed to" in script_stderr
    assert any("no environment was given" in warning for warning in code_warnings)
    assert any("never be routed to" in warning for warning in code_warnings)

    fixture_repo.platform_config.joinpath("dev", "static.json").unlink()
    script, script_stderr = from_script(fixture_repo, arguments, dry_run_env)
    code, code_warnings = from_code(fixture_repo, ARGUMENTS, "dev")
    assert "there is no static.json at" in script_stderr
    assert "initialized" in script_stderr
    assert any("static.json" in warning and "been prepared" in warning
               for warning in code_warnings)
    # The two sentences name the path differently, and that is one of the declared differences: the
    # script printed what it had built -- `.../scripts/utilities/kubernetes/../../../platform_config/…`
    # -- and `Config` prints the resolved file. Same file, so resolve the script's string and
    # compare it against the one the warning this side emits carries.
    named = re.search(r"there is no static\.json at (\S+) - is", script_stderr)
    assert named is not None, f"the script's sentence no longer has the shape this reads: {script_stderr}"
    assert Path(named.group(1)).resolve() == (fixture_repo.platform_config / "dev" /
                                              "static.json").resolve()


# ------------------------------------------------------------------------------------------
# What neither side does.
# ------------------------------------------------------------------------------------------

def test_neither_side_asks_a_cluster_anything(fixture_repo, dry_run_env, fake_binaries,
                                              recorded_api):
    """The empty logs, which is the finding.

    This script had one external dependency and it was `jq`: no `kubectl`, no `helm`, nothing a
    shim could record. So the operation comparison is between two empties, and that is the claim --
    that the translation added no cluster read of its own, which is precisely the risk a generator
    that now talks to `Config` runs.

    Both halves run in one fixture and write to one log, so `take_calls` separates them: a shim that
    answered during the script's half cannot be read as silence during the code's half.
    """
    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    script_document, _ = from_script(fixture_repo, (*ARGUMENTS, "dev"), dry_run_env)
    bash_calls = operations_from_calls(fake_binaries.take_calls())

    code, _ = from_code(fixture_repo, ARGUMENTS, "dev")
    assert script_document["kind"] == code["kind"] == "Ingress", \
        "neither half produced a document, so an empty log here would be empty for the wrong " \
        "reason"
    compare(bash_calls, recorded_api.operations(),
            context="create_ingress_yml.sh versus IngressGenerator: neither side belongs near a "
                    "cluster, and no cluster read was added by the translation")
    assert fake_binaries.commands() == [], "a program was run that the deleted script never ran"


# ------------------------------------------------------------------------------------------
# The one place the two answers differ.
# ------------------------------------------------------------------------------------------

def test_the_port_is_the_only_place_python_answers_more_strictly_than_the_script_did(
        fixture_repo, dry_run_env):
    """`http` as a port: the script printed it, Python refuses to.

    bash interpolated `$3` into `number: $SERVICE_PORT` after checking only that it was not empty,
    so that document was created, could be applied, and was rejected by the API server. Python
    refuses to build the object instead. It is a stricter answer to a bad argument and not a
    different answer to a good one, which the second half of this test establishes by running both
    sides on `80` and comparing.
    """
    fixture_repo.environment_static(TRAEFIK_INGRESS_CLASS="my-class")
    bad, _ = from_script(fixture_repo, (DOMAIN, SERVICE, "http", "dev"), dry_run_env)
    assert bad["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]["port"] == \
        {"number": "http"}

    with pytest.raises(IngressPortNotUsableError) as refused:
        generator("dev").generate(DOMAIN, SERVICE, "http")
    assert "not a port number" in str(refused.value)

    good, _ = from_script(fixture_repo, (*ARGUMENTS, "dev"), dry_run_env)
    code, _ = from_code(fixture_repo, ARGUMENTS, "dev")
    assert code == good, "on a port that is a number the documents are identical, which is what " \
                         "makes the refusal above the only difference between the two sides"
