"""The three grafana scripts, run from their git record, against the commands that replaced them.

`create_grafana_accounts.sh`, `create_grafana_read_only_account.sh` and `grafana_helpers.sh` are
deleted by this commit. They are still reachable — `git show 5c306bd:scripts/user_management/<name>`
prints each — and this file runs that record against a `curl` that answers, then runs the Python
command against the same fixture, and asks whether the two sides touched the same API.

The declared differences are all of them, and they are declared because a silent one is the bug:

* **Where the address comes from.** bash defaulted it to `http://127.0.0.1:31827` when the operator
  passed nothing. The command resolves a URL, then a ClusterIP, and refuses when it has neither.
  The bash side is given an address explicitly in every comparison below, because the interesting
  question is what each side *does* with one, not whether it can guess.
* **What an unanswered request means.** The bash sent the request inside `$( )`, where `set -e`
  does not look, and read the empty body as "that account is already here". The command treats it
  as a failure. The last test is this difference, measured on both sides with the same fixture.
* **No role is set, on either side.** "read only" is a filename. The command keeps the script's
  behaviour and the plan records the question; granting `Viewer` would be a product change in a
  migration commit, and this file would rather show it not happening.
* **No team is joined, on either side.** `create_user` took a `team_id` and dropped it in a
  payload that never mentioned it, so the two sides agree by agreeing to do nothing.

The `jq` the scripts use is not a cluster tool and never enters any log of ours: it runs against
the shim's stdout on a host that has it, which is how the scripts' own one-liners always ran.
"""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from cli.commands import users
from cluster_fixture import stamp_tree, write_kubeconfig
from conftest import (ENVIRONMENT_STATIC, FIXTURE_ENVIRONMENT, FIXTURE_KUBE_CONTEXT, FIXTURE_TEAM,
                      TEAM_STATIC, TEAM_STATIC_ENCRYPTED)
import kubementat.infrastructure.grafana.client as grafana_client

REPO_ROOT = Path(__file__).resolve().parent.parent

# What the fixture environment addresses. Unreachable, like every fixture address: resolving is a
# read of the kubeconfig and nothing else, so a green parity run still proves no cluster was touched.
GRAFANA_SERVER = "https://grafana-parity-fixture.invalid:6443"

# The commit the three scripts were still in. They are deleted here, so this is a record, not a run.
BLOB_REVISION = "5c306bd"
SCRIPTS = {
    "accounts": "scripts/user_management/create_grafana_accounts.sh",
    "read_only": "scripts/user_management/create_grafana_read_only_account.sh",
    "helpers": "scripts/user_management/grafana_helpers.sh",
}

EMAIL = "mranderson@something.test"
NAME = "Thomas Anderson"
PASSWORD = "the-password-the-row-declares"
USER_ID = 12

# Which of the helper's functions answers which. `secrets` is the one that is not a method: the
# bash made a password by asking gpg for random bytes, which is a subprocess and not this
# library's business, so the stdlib generator replaced it.
COVERAGE_STDLIB = "secrets"
COVERAGE = {
    "construct_auth_header": "_request",
    "create_new_user_for_team": "ensure_account",
    "create_user": "create_user",
    "generate_password": COVERAGE_STDLIB,
    "get_users_for_email": "find_user",
    "send_get_request": "_request",
    "send_post_request": "_request",
}

# What the bash asked for, and what the client asks for. Spelled once so the comparison has two
# real sides rather than a rule.
SEARCH_PATH = "/api/users/search"
CREATE_PATH = "/api/admin/users"

CURL_SHIM = """#!/usr/bin/env bash
# A stand-in for `curl`, for the script side of a parity test: it records the request, prints the
# body this test put in `$GRAFANA_RESPONSE_DIR/<path>`, and never connects anywhere.
method="GET"; data=""; url=""; auth=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -X) method="$2"; shift 2;;
    -d) data="$2"; shift 2;;
    -u) auth="$2"; shift 2;;
    --header) shift 2;;
    -*) shift 1;;
    *) url="$1"; shift 1;;
  esac
done
path="${url#*://}"; path="${path#*/}"; key="${path%%\\?*}"; key="${key//\\//_}"
body=""
if [[ -f "${GRAFANA_RESPONSE_DIR:-/nonexistent}/${key}" ]]; then
  body="$(cat "${GRAFANA_RESPONSE_DIR}/${key}")"
fi
printf '%s\\t%s\\t%s\\t%s\\n' "$method" "$url" "$data" "$auth" >> "${GRAFANA_CURL_LOG:?}"
printf '%s' "$body"
"""


def deleted_script(root, name):
    """One deleted script's own text, out of git history, written into *root*.

    Skips, loudly, if the history is not reachable in this checkout: the parity below is a
    comparison, and without the other side of it there is nothing to compare.
    """
    path = root / SCRIPTS[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["git", "show", f"{BLOB_REVISION}:{SCRIPTS[name]}"],
                            cwd=str(REPO_ROOT), capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"the script's record is not reachable here: git show "
                    f"{BLOB_REVISION}:{SCRIPTS[name]} exited {result.returncode} "
                    f"({result.stderr.strip()})")
    path.write_text(result.stdout)
    path.chmod(0o755)
    return path


@pytest.fixture
def tree(tmp_path):
    """A checkout shaped like the one the scripts were run in, with their blobs put back.

    `create_grafana_accounts.sh` resolves `../../platform_config` against wherever it is being
    run from and calls its sibling by `./`, so the fixture reproduces the directory rather than
    the paths. The four config files are the ones the unit fixtures use, because both sides have
    to read the same GRAFANA_USERS and the same admin pair.
    """
    root = tmp_path / "checkout"
    stamp_tree(root)
    scripts = root / "scripts" / "user_management"
    for name in SCRIPTS:
        deleted_script(root, name)
    environment = root / "platform_config" / FIXTURE_ENVIRONMENT
    team = environment / FIXTURE_TEAM
    team.mkdir(parents=True)
    encrypted = {
        "GRAFANA_ADMIN_USER": "grafadmin",
        "GRAFANA_ADMIN_PASSWORD": "not-a-password",
        "GRAFANA_USERS": [{"email": EMAIL, "name": NAME, "password": PASSWORD}],
    }
    # The environment's own claim on a cluster, written with the file: `kmt users
    # create-grafana-accounts` resolves one before it touches grafana, which is the whole point of the parity this
    # file runs is against the script side, which never had the question.
    kubeconfig = write_kubeconfig(root / "kubeconfig", {FIXTURE_KUBE_CONTEXT: GRAFANA_SERVER})
    (environment / "static.json").write_text(json.dumps(
        {**ENVIRONMENT_STATIC,
         "CLUSTER_KUBE_CONTEXT": FIXTURE_KUBE_CONTEXT,
         "CLUSTER_KUBECONFIG_PATH": str(kubeconfig)}, indent=2))
    (environment / "static.encrypted.json").write_text(json.dumps(encrypted, indent=2))
    (team / "static.json").write_text(json.dumps(TEAM_STATIC, indent=2))
    (team / "static.encrypted.json").write_text(json.dumps(TEAM_STATIC_ENCRYPTED, indent=2))
    return root


@pytest.fixture
def curl_shim(fake_binaries, tmp_path):
    """`curl` as a logging stand-in, plus the directory its answers live in."""
    shim = Path(fake_binaries.directory) / "curl"
    shim.write_text(CURL_SHIM)
    shim.chmod(0o755)
    responses = tmp_path / "grafana-responses"
    responses.mkdir()
    (responses / "api_users_search").write_text(json.dumps({"users": [], "totalCount": 0}))
    (responses / "api_admin_users").write_text(json.dumps({"id": USER_ID, "message": "User created"}))
    log = tmp_path / "curl.log"
    environment = fake_binaries.environment({**os.environ,
                                             "GRAFANA_CURL_LOG": str(log),
                                             "GRAFANA_RESPONSE_DIR": str(responses)})
    return SimpleNamespace(environment=environment, log=log, responses=responses)


def requests_the_shim_saw(curl_shim):
    """What the script side sent, as `(method, path-without-query, body, credential)` per call."""
    seen = []
    for line in curl_shim.log.read_text().splitlines():
        method, url, body, auth = (line.split("\t") + ["", "", "", ""])[:4]
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        seen.append({"method": method, "path": path, "body": body, "auth": auth, "url": url})
    return seen


def run_script_side(tree, curl_shim, base_url, extra_body=None):
    """The deleted script, run against the curl that answers. Returns its exit code and stdout."""
    if extra_body is not None:
        curl_shim.responses.joinpath("api_users_search").write_text(extra_body)
    result = subprocess.run(
        ["bash", str(tree / "scripts" / "user_management" / "create_grafana_accounts.sh"),
         FIXTURE_ENVIRONMENT, base_url],
        cwd=str(tree / "scripts" / "user_management"), env=curl_shim.environment,
        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    return result


def run_python_side(tree, monkeypatch, session, base_url=None):
    """The command, on the same fixture. `--grafana-url` keeps the two sides pointed at one place."""
    monkeypatch.setenv("KUBEMENTAT_HOME", str(tree))
    # A configuration root is named and not located, so the Python half reads `tree`'s own config. The
    # bash half resolves the same directory out of its own location, which is the comparison.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(tree / "platform_config"))
    monkeypatch.delenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.delenv("GRAFANA_ADMIN_USER", raising=False)
    monkeypatch.delenv("GRAFANA_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr(grafana_client.requests, "Session", lambda *a, **kw: session)
    # No `--team`: the command reads its rows and its credential out of the environment's file,
    # and the parity that matters here is which endpoints it hit, not which team directory it
    # refused to name. The fixture still writes a team layer, so the environment-level reads it
    # makes are the same ones the bash made.
    argv = ["create-grafana-accounts", FIXTURE_ENVIRONMENT, "--yes"]
    argv += ["--grafana-url", base_url] if base_url else []
    return CliRunner().invoke(users.users, argv)


class AnsweringSession:
    """`requests.Session` that behaves like the grafana the shim behaved like.

    `search` is what the directory says; the default is the empty directory, which is what "that
    account is not here yet" looks like. `fail_with` raises instead of answering, which is how a
    socket that is not there is expressed without opening one.
    """

    def __init__(self, search=None, created=None, fail_with=None):
        self.sent = []
        self.fail_with = fail_with
        self.search = json.dumps({"users": [], "totalCount": 0}) if search is None \
            else json.dumps(search)
        self.created = json.dumps({"id": USER_ID, "message": "User created"}) if created is None \
            else json.dumps(created)

    def request(self, method, url, headers=None, auth=None, params=None, json=None, timeout=None):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
        self.sent.append({"method": method, "path": path, "params": params or {}, "json": json,
                          "auth": auth, "url": url})
        if self.fail_with is not None:
            raise self.fail_with
        if method == "GET" and path == SEARCH_PATH:
            return _Response(200, self.search)
        if method == "POST" and path == CREATE_PATH:
            return _Response(201, self.created)
        raise AssertionError(f"the command sent {method} {path}, which this fixture never set up")


class _Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text

    def json(self):
        return json.loads(self.text)


def test_the_three_scripts_are_gone_from_the_tree_and_reachable_in_history():
    """Deleted, not renamed, not aliased, not left behind as a second way to do it."""
    for name, relative in SCRIPTS.items():
        assert not (REPO_ROOT / relative).exists(), f"{relative} is still in the tree"
        shown = subprocess.run(["git", "show", f"{BLOB_REVISION}:{relative}"],
                               cwd=str(REPO_ROOT), capture_output=True, text=True)
        if shown.returncode != 0:
            pytest.skip(f"no record of {relative} at {BLOB_REVISION}")
        assert "grafana" in shown.stdout.lower(), f"{relative} is not the script this replaced"


def test_the_script_side_and_the_command_side_touched_the_same_endpoints(tree, curl_shim,
                                                                        monkeypatch):
    """Two implementations, one API. Compared as calls, not as code.

    Both sides ask the search whether the address is taken and then create it with the four fields
    grafana's payload is made of, in the same order of endpoints, with the same credential. What
    is *not* compared is how many argvs each side needed.
    """
    script = run_script_side(tree, curl_shim, "http://grafana.test:3000")
    assert script.returncode == 0, script.stdout + script.stderr
    script_calls = requests_the_shim_saw(curl_shim)

    session = AnsweringSession()
    command = run_python_side(tree, monkeypatch, session, "http://grafana.test:3000")
    assert command.exit_code == 0, command.output
    python_calls = [{"method": call["method"], "path": call["path"], "body": call["json"]}
                    for call in session.sent]

    assert [call["path"] for call in script_calls] == [call["path"] for call in python_calls]
    assert [call["method"] for call in script_calls] == [call["method"] for call in python_calls]

    # The create's payload, field for field. bash interpolated a string; the client sends a
    # document; what matters is that grafana is asked for the same account either way.
    from_bash = json.loads(script_calls[-1]["body"])
    from_python = python_calls[-1]["body"]
    assert set(from_bash) == set(from_python) == {"name", "email", "login", "password"}
    assert from_bash == from_python

    # The search, including the query it asked with: the same email, the same sort.
    assert "query=" + EMAIL in script_calls[0]["url"]
    assert session.sent[0]["params"]["query"] == EMAIL
    assert session.sent[0]["params"]["sort"] == "email-asc"

    # And the credential, because the scripts sent the pair on every request and never logged in.
    assert script_calls[0]["auth"] == "grafadmin:not-a-password"
    assert ("grafadmin", "not-a-password") in _auth_pairs(session)


def _auth_pairs(session):
    return [call.get("auth") for call in session.sent]


def test_both_sides_create_nothing_for_an_account_that_is_already_there(tree, curl_shim,
                                                                        monkeypatch):
    """The one behaviour the bash got right by accident, kept as parity.

    A search that answers makes the create unnecessary. bash skipped on an `if` that also fired on
    nothing; the command skips on an answer that is really an answer. Same outcome, different
    evidence, so it is asserted on both sides.
    """
    script = run_script_side(tree, curl_shim, "http://grafana.test:3000",
                             extra_body=json.dumps({"users": [{"id": USER_ID, "email": EMAIL,
                                                               "login": EMAIL, "name": NAME}],
                                                    "totalCount": 1}))
    assert script.returncode == 0, script.stdout
    assert "already exists, skipping" in script.stdout
    assert len(requests_the_shim_saw(curl_shim)) == 1, "the script should not have written"

    session = AnsweringSession(search={"users": [{"id": USER_ID, "email": EMAIL, "login": EMAIL,
                                                  "name": NAME}], "totalCount": 1})
    command = run_python_side(tree, monkeypatch, session, "http://grafana.test:3000")
    assert command.exit_code == 0, command.output
    assert [call["method"] for call in session.sent] == ["GET"]
    assert "already exists" in command.output


def test_the_helper_library_is_fully_covered_by_the_client(tmp_path):
    """Everything `grafana_helpers.sh` declared has an answer in `grafana_client.py`.

    The helper is seven functions, and which one is which is the interesting part: two request
    senders (both answered by one transport method), a bearer-header builder nobody ever called,
    a search, a create, the skip guard, and a password generator that shelled out to gpg. A
    migration that covers five of seven leaves a half-replaced library, which is the shape the
    dead `create_team_account` stub had.
    """
    helpers = deleted_script(tmp_path, "helpers")
    declared = [line.split("function ")[1].split("{")[0].strip().rstrip("()")
                for line in helpers.read_text().splitlines() if "function " in line]
    assert sorted(declared) == sorted(COVERAGE), sorted(declared)

    surface = {name for name in vars(grafana_client.GrafanaClient)}
    missing = [answer for helper, answer in COVERAGE.items()
               if answer not in surface and answer != COVERAGE_STDLIB]
    assert not missing, f"the helper declares {missing} and the client answers nothing"
    assert grafana_client.secrets.token_urlsafe, "the gpg random byte generator has no answer"
    # Nothing in the helper authenticated with a cookie or a login call, so nothing here does.
    assert not [name for name in surface if "login" in name.lower()]

    # And the surface that goes past the helper: reads and the calls the bash never made.
    for beyond in ("find_user", "org_members", "set_password", "add_user_to_team"):
        assert beyond in surface, f"the client does not have {beyond}"


def test_an_unreachable_grafana_makes_the_script_green_and_the_command_red(tree, curl_shim,
                                                                           monkeypatch):
    """The vacuous row, reproduced and then refused, in one test.

    This is the measurement. The `curl` on the left could not connect, so it printed nothing;
    `jq '.users[0]'` exited **0** on that nothing; the `if` read the nothing as "the user already
    exists" and the script exited **0**. Twenty lines of code, four lost steps, one green row that
    created no account.

    The right half is the same fixture and the same silence, and the command exits non-zero naming
    the address. That is the whole ticket, stated as an assert instead of as a rule.
    """
    curl_shim.responses.joinpath("api_users_search").write_text("")

    script = run_script_side(tree, curl_shim, "http://127.0.0.1:31827")
    assert script.returncode == 0, "the point of this file is that the bash side still does this"
    assert "already exists, skipping" in script.stdout
    assert "created" not in script.stdout.lower()

    session = AnsweringSession(fail_with=grafana_client.requests.ConnectionError(
        "Connection refused"))
    command = run_python_side(tree, monkeypatch, session, "http://127.0.0.1:31827")

    assert command.exit_code != 0
    assert "Could not reach grafana" in command.output
    assert "already exists" not in command.output
