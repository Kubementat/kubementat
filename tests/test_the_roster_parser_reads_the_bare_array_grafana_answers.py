"""`/api/org/users` answers with a bare JSON array, and the roster parser has to read it.

The full lane (`20261030T122617Z-full-full-d7d1de1`, stage 05) probed the org roster, got HTTP
200 and a valid array of rows carrying `orgId/userId/uid/email/login/role`, and reported
`grafana-account-created` red with the sentence "the /api/org/users answer was not a roster" —
followed, in the same detail, by the body that is a roster. The parser only accepted the dict
form `{"users": [...]}` (which `/api/users/search` answers, and `/api/org/users` never did), so
a correct grafana answer was parsed to `None` and both roster rows went red whatever the product
did. The stub this suite used to run against wrapped its rows in the same fictional shape, which
is why no test noticed.

These tests fix the parser on the real shapes in both directions, and — the half that matters
for the row's purpose — keep the distinction between "grafana answered nobody" (a roster, may be
empty, and the missing accounts are named) and "that is not grafana answering" (an HTML page, a
truncated body, an error document; the assert must say grafana did not answer as a roster, never
that the account is missing).
"""

import json

import pytest
from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness, shown

stages = load_harness()

CONFIGURED_USER = "grafadmin"
ADMIN_PASSWORD = "not-a-real-password"
NAMESPACE = "grafana"
CLUSTER_IP = "10.43.71.20"
DECLARED_EMAIL = "kmt-e2e-account@example.invalid"
DECLARED_ROW = json.dumps([{"email": DECLARED_EMAIL, "name": "kmt-e2e lane account",
                             "password": "not-a-password"}])

# Rows in exactly the shape the lane captured: a bare array, and each entry carrying
# orgId/userId/uid/email/login/role alongside the display fields.
LANE_ROW = {"orgId": 1, "userId": 2, "uid": "eg2sf6zbajjlsa", "email": DECLARED_EMAIL,
            "login": DECLARED_EMAIL, "name": "kmt-e2e lane account", "role": "Viewer",
            "lastSeenAt": "2026-10-30T12:32:05Z", "isDisabled": False,
            "avatarUrl": "/avatar/d85160f401ee4015673866fdfdd5ffbb"}
ADMIN_ROW = {"orgId": 1, "userId": 1, "uid": "ag2sexjdxhfy8c", "email": "admin@localhost",
             "login": "grafadmin", "name": "", "role": "Admin",
             "lastSeenAt": "2026-10-30T12:32:05Z", "isDisabled": False}


@pytest.fixture
def runner():
    """The stage, its fake guest, and everything it asserted."""
    guest = FakeGuest()
    recording = Recording()
    lane = stages.config.LANES["full"]
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    instance = stages.LaneRunner(lane, options, FakeArtifacts(), None, {}, recording, lambda message: None)
    instance.guest = guest
    instance.cluster = stages.asserts.Cluster(guest)
    instance.stage = "05-features"
    instance._recording = recording
    return instance


def given_grafana_answering(runner, body, code="200", declared=DECLARED_ROW):
    """A reachable, correctly credentialed grafana whose `/api/org/users` sends `body` verbatim.

    The config side is the same every test: one seeded row, the admin pair the encrypted file
    names, a ClusterIP to open. Only the roster body and its status move, so every difference in
    what the stage reports is attributable to how the parser read `body`.
    """
    guest = runner.guest
    guest.state.setdefault("creates", 0)

    def create(command):
        # One answer per invocation: the stage runs the command twice on purpose, and the second
        # answer is what makes the rerun row meaningful.
        guest.state["creates"] += 1
        if guest.state["creates"] == 1:
            return Answer(0, "created grafana account %s (user 7, kmt-e2e lane account)\n"
                          % DECLARED_EMAIL, "")
        return Answer(0, "grafana account %s already exists (user 7); nothing changed and no "
                         "password was applied\n" % DECLARED_EMAIL, "")

    guest.respond("create_grafana_accounts.sh", Answer(0, "creating grafana accounts\n", ""))
    guest.respond("kmt users create-grafana-accounts", create)
    guest.respond("static.encrypted.json", lambda command: CONFIGURED_USER
                  if "GRAFANA_ADMIN_USER" in command else
                  (declared if "GRAFANA_USERS" in command else ADMIN_PASSWORD))
    # `GRAFANA_DEPLOYMENT_NAMESPACE` is answered, not declared: `config init` writes no template and
    # `constants.DEFAULT_NAMESPACES` carries grafana, so the file the stage used to grep holds nothing
    # while the tree answers the key plainly. The fake answers the verb the stage now reads through.
    guest.respond("config show", shown(GRAFANA_DEPLOYMENT_NAMESPACE=NAMESPACE))
    guest.respond("kubectl get svc",
                  '{"items": [{"metadata": {"name": "grafana"}, '
                  '"spec": {"clusterIP": "%s", "ports": [{"port": 3000}]}}]}' % CLUSTER_IP)
    guest.respond("curl ", Answer(0, body + code, ""))
    return guest


# ---------------------------------------------------------------------------
# the parser, shape by shape
# ---------------------------------------------------------------------------


def test_the_bare_array_the_endpoint_actually_sends_is_a_roster_of_its_rows(runner):
    body = json.dumps([ADMIN_ROW, LANE_ROW])
    assert runner._org_roster(body) == [ADMIN_ROW, LANE_ROW]


def test_a_bare_empty_array_is_a_roster_saying_nobody_not_an_absent_answer(runner):
    roster = runner._org_roster("[]")
    assert roster == [], "an empty org is an answer, and calling it None turns 'no such account' " \
                         "into 'that is not grafana answering' — the exact confusion the " \
                         "distinction exists to avoid"


def test_a_users_list_wrapped_in_an_object_is_still_a_roster(runner):
    roster = runner._org_roster(json.dumps({"users": [LANE_ROW], "totalCount": 1}))
    assert roster == [LANE_ROW], "the wrapped shape may exist on other endpoints and older " \
                                 "grafanas; accepting it costs nothing and rejecting it is a new lie"


def test_an_html_error_page_where_json_was_expected_is_not_a_roster(runner):
    assert runner._org_roster("<html><body>grafana login</body></html>") is None


def test_a_body_truncated_off_the_end_of_the_array_is_not_a_roster(runner):
    body = json.dumps([ADMIN_ROW, LANE_ROW])
    assert runner._org_roster(body[:len(body) // 2]) is None


def test_an_unauthorized_document_is_not_a_roster(runner):
    assert runner._org_roster(json.dumps({"message": "Unauthorized"})) is None


def test_a_body_that_is_not_json_or_is_null_is_not_a_roster(runner):
    assert runner._org_roster("") is None
    assert runner._org_roster("null") is None


# ---------------------------------------------------------------------------
# the asserts that consume it, in both directions
# ---------------------------------------------------------------------------


def test_the_lane_shape_bare_array_and_all_makes_the_create_and_rerun_rows_green(runner):
    """The exact body the lane captured, end to end: two runs, no duplicate, both rows green.

    This is the regression the parser bug caused: grafana answered correctly and the harness
    read it wrong. Nothing here makes any assert more permissive — with the array accepted, the
    whole stage passes on the captured shape.
    """
    given_grafana_answering(runner, json.dumps([ADMIN_ROW, LANE_ROW]))

    runner.feature_grafana_accounts()

    assert runner._recording.passed("assert:grafana-api-readable")
    assert runner._recording.passed("assert:grafana-account-created"), \
        runner._recording.detail("assert:grafana-account-created")
    assert runner._recording.passed("assert:grafana-account-rerun-confirms-existing"), \
        runner._recording.detail("assert:grafana-account-rerun-confirms-existing")


def test_an_error_page_says_grafana_did_not_answer_as_a_roster_not_that_the_account_is_missing(
        runner):
    """200 plus a login page is the probe failing, and the detail has to own that.

    The roster fix is only honest if this stays red: an HTML body is not a roster that happens
    to lack the address, it is no roster at all, and naming the account as missing would read a
    credential or proxy failure as a product failure.
    """
    given_grafana_answering(runner, "<html><body>grafana login</body></html>")

    runner.feature_grafana_accounts()

    assert runner._recording.passed("assert:grafana-account-created") is False
    detail = runner._recording.detail("assert:grafana-account-created")
    assert "grafana did not answer as a roster" in detail, detail
    assert "missing" not in detail, \
        "an unreadable body is the probe failing, not an absent account: %s" % detail
    assert runner._recording.passed("assert:grafana-account-rerun-confirms-existing") is False
    assert "did not answer as a roster" in \
        runner._recording.detail("assert:grafana-account-rerun-confirms-existing")


def test_a_rejected_credential_never_becomes_a_report_of_a_missing_account(runner):
    """A 401 body of `{"message": "Unauthorized"}`: readable goes red, and so does the roster —
    with the probe-failure sentence, not the account-missing one. Accepting the bare array must
    not make this shape parse as anything; an error document is still not a roster."""
    given_grafana_answering(runner, json.dumps({"message": "Unauthorized"}), code="401")

    runner.feature_grafana_accounts()

    assert runner._recording.passed("assert:grafana-api-readable") is False
    assert runner._recording.passed("assert:grafana-account-created") is False
    detail = runner._recording.detail("assert:grafana-account-created")
    assert "grafana did not answer as a roster" in detail and "missing" not in detail, detail


def test_a_roster_that_says_nobody_names_the_absent_account_rather_than_ceding_to_the_error_page(
        runner):
    """The other side: an empty array is a roster, so this one goes red for the right reason.

    It has to say the roster answered and list what is missing — if it said "not a roster", the
    empty answer would be indistinguishable from an HTML page, and the distinction this parser
    exists to keep would be gone again.
    """
    given_grafana_answering(runner, "[]")

    runner.feature_grafana_accounts()

    assert runner._recording.passed("assert:grafana-account-created") is False
    detail = runner._recording.detail("assert:grafana-account-created")
    assert "roster answers" in detail, detail
    assert "not a roster" not in detail, detail
    assert DECLARED_EMAIL in detail, "an empty roster must still name the account it rules out"


def test_the_declared_row_reports_the_count_and_addresses_it_measured(runner):
    """A green row must not recite the vacuity sentence it exists to catch.

    The lane reported "static.encrypted.json declares no GRAFANA_USERS rows ... this row proves
    nothing" on a row that had just seeded exactly one row and was passing. The detail is now
    what was measured — how many rows and which addresses.
    """
    given_grafana_answering(runner, json.dumps([ADMIN_ROW, LANE_ROW]))

    runner.feature_grafana_accounts()

    assert runner._recording.passed("assert:grafana-account-rows-declared")
    detail = runner._recording.detail("assert:grafana-account-rows-declared")
    assert "1 GRAFANA_USERS row" in detail, detail
    assert DECLARED_EMAIL in detail, "the verdict has to show its measurements: %s" % detail
    assert "proves nothing" not in detail, \
        "the vacuity sentence belongs to the branch that is vacuous: %s" % detail


def test_the_vacuity_sentence_survives_where_the_config_really_declares_nobody(runner):
    """Both directions, so neither green text nor red text can be the default.

    Accepting the bare array must not make this assert vacuous: when nothing is declared, the
    row goes red and says exactly what the old green report used to say about itself.
    """
    given_grafana_answering(runner, json.dumps([ADMIN_ROW, LANE_ROW]), declared="")

    runner.feature_grafana_accounts()

    assert runner._recording.passed("assert:grafana-account-rows-declared") is False
    detail = runner._recording.detail("assert:grafana-account-rows-declared")
    assert "GRAFANA_USERS" in detail and "proves nothing" in detail, detail
