"""The tunnel asserts have to measure the tunnel, not the probe or the leftover listener.

Two of these five used to be red on a lane that had nothing wrong with its tunnel: the
signature compared the body of grafana's `302` against a table of page tokens, and the
port-free check trusted a `kill <pid>` that the tunnel teardown left reaching only the CLI —
`kubectl
port-forward` outlives it and keeps the port. Both fixes are about *how the fact is measured*,
so the tests below drive the stage against a fake guest and read what it asserted.

The negative cases are the point. An assert that goes green because the measurement was
weakened is worse than a red lane, so each fix is paired with a guest that behaves badly.

The last six check the measurement itself, which is what row 19 buys: `curl -S` so a refused
connection contributes text instead of nothing, a `kill -0` that answers for a live process
instead of reporting one as gone, an evidence file per probe so the rename retry cannot stand
over the row being judged, and a verdict that says which of the three states the probe found.
"""

import re

import pytest
from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()

PID = "2468"
GRAFANA_PORT = "3001"

# What Grafana serves behind the 302 on `/`, taken from a lane body capture rather than from what
# a login form is expected to render: a `<div id="reactRoot">` and `window.grafanaBootData`.
# The previous fixture offered `api/login` and `signin`, and Grafana's document contains neither,
# so a probe reading the real document would have failed against this fixture the way it failed
# on the lane.
LOGIN_PAGE = ('<html lang="en-US"><head><meta charset="utf-8" /><title>grafana</title></head><body>'
              '<div id="reactRoot"></div>'
              '<script nonce="">window.grafanaBootData = {user: {isSignedIn: false}};</script>'
              '</body></html>')
REDIRECT_BODY = '<a href="/login">Found</a>.'
LISTENER_LINE = ('LISTEN 0 128 0.0.0.0:%s 0.0.0.0:* users:(("kubectl",pid=%s,fd=6))' % (GRAFANA_PORT, PID))

# The three states a red `tunnel-signature` can be in, and the only three it should ever name.
# They are measured through the same fake tunnel, so the only thing that differs between the
# three runs is what curl reported -- which is exactly what `body=empty` could not tell apart.
CONNECTION_REFUSED = Answer(7, "", "curl: (7) Failed to connect to 127.0.0.1 port 3001: Connection refused")
ANSWERED_NOTHING = Answer(0, "", "")
WRONG_DOCUMENT = Answer(0, REDIRECT_BODY, "")
VERDICT = re.compile(r"verdict=([a-z-]+)")


def curl_flags(command):
    """curl's own flags, joined. `-sSL` and `-s -S -L` read alike; `--max-time` is not a flag blob.

    A substring check on the raw command is what let `-sSL` pass for a probe that never asked for
    errors, so the tests read the flags as flags rather than trusting their spelling.
    """
    return "".join(token for token in command.split() if token.startswith("-") and not token.startswith("--"))


def verdict_of(detail):
    """The one word the detail says the probe measured."""
    found = VERDICT.search(detail or "")
    assert found, "the detail has to state what it measured: %s" % detail
    return found.group(1)


@pytest.fixture(scope="module")
def harness():
    return stages


def new_runner():
    """A `LaneRunner` whose only external world is the fake guest."""
    guest = FakeGuest()
    recording = Recording()
    lane = stages.config.LANES["full"]
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    instance = stages.LaneRunner(lane, options, FakeArtifacts(), None, {}, recording, lambda message: None)
    instance.guest = guest
    instance.cluster = stages.asserts.Cluster(guest)
    instance.stage = "05-features"
    instance._recording = recording  # convenience for the assertions below
    return instance


@pytest.fixture
def runner():
    return new_runner()


def given_a_grafana_tunnel(runner, page=True, stoppable=True, session=PID, bound_before_start=False,
                          alive=True):
    """A grafana tunnel that is up and answering, described by what happens when it is stopped.

    `page=False` is the bug the redirect-following fix exists for: the tunnel answers `/` with
    a 302 and the body of a redirect is not a signature. `stoppable=False` is a `port-forward`
    that survives the kill, which is exactly the thing an assert must not sleep past.

    `alive` is state rather than a fixed answer so one guest can be alive and then gone: a probe
    that runs after the tunnel it started has been stopped must not read as alive for the rest of
    its deadline.
    """
    guest = runner.guest
    state = guest.state
    state.setdefault("listener", bound_before_start)
    state.setdefault("members", [PID])
    state["alive"] = alive

    def kill_zero_probe(command):
        """Answer the way a shell would, not the way a fixture would like to.

        `kill -0 PID && echo A || echo B` prints A when the process is there and B when it is not,
        and which word that is belongs to the command. A fake that always said "alive" is what let
        the inverted condition look correct in a test, so this one takes both words from the line it
        was asked to run and answers with the branch the exit status actually selects.
        """
        branches = re.search(r"echo (\w+) \|\| echo (\w+)", command)
        if not branches:
            return "alive" if state.get("alive", True) else "dead"
        here, gone = branches.group(1), branches.group(2)
        return here if state.get("alive", True) else gone

    def read_file(command):
        """Answer a `cat` by naming the file it read.

        Two different `cat`s run through this stub: `start_session` reads the leader out of the
        pid file, and the probe reads the tunnel's own log. A blanket answer made one of them
        lie, so the answer follows the path the command names.
        """
        if ".pid" in command:
            return state.get("pidfile", "")
        return state.get("log", "")

    def curl(command):
        # An override planted by a test wins: it is how the three failure shapes are made to be
        # the only difference between three otherwise identical runs.
        override = state.get("curl")
        if override is not None:
            return override
        if "L" not in curl_flags(command):
            return Answer(0, REDIRECT_BODY, "")
        return Answer(0, LOGIN_PAGE if page else "", "")

    def start(command):
        state["listener"] = True
        # The wrapper records its own `$$` from inside the new session, and that number - not
        # `$!` - is what the harness now reads back. A fake that only answers the `$!` shape
        # would leave the leader empty and silently exercise the fallback path instead.
        state["pidfile"] = PID
        return ""

    def kill(command):
        if stoppable:
            state["listener"] = False
            state["members"] = []
        return Answer(0, "", "")

    # Order matters: a rule matches the first substring it finds, so the dead-pid probe has to
    # be answered before the generic kill rule can claim it.
    guest.respond("kill -0", kill_zero_probe)
    guest.respond("setsid nohup bash", start)
    guest.respond("cat ", read_file)
    guest.respond("curl ", curl)
    guest.respond("ps -o sid=", session)
    guest.respond("pgrep -s", lambda command: " ".join(state["members"]) if state["members"] else "")
    guest.respond("kill ", kill)
    guest.respond("ss -ltn", lambda command: LISTENER_LINE if state["listener"] else "")
    return state


def test_the_body_signature_reads_the_page_the_redirect_hides(runner):
    given_a_grafana_tunnel(runner)

    matched = runner._tunnel_body_signature("tunnel.grafana")

    assert matched is True
    assert runner._recording.passed("assert:tunnel-signature:tunnel.grafana")
    detail = runner._recording.detail("assert:tunnel-signature:tunnel.grafana")
    assert "missing=[]" in detail, "a signature failure should say which token was absent: %s" % detail
    probed = runner.guest.commands_matching("curl ")
    assert probed, "the probe is the measurement; without it there is nothing to assert on"
    assert all("L" in curl_flags(command) for command in probed), \
        "following the redirect is the whole fix; the probe must not go back to `-s`"
    assert all("S" in curl_flags(command) for command in probed), \
        "without `-S` a refused connection contributes zero bytes of stderr, which is what made " \
        "`body=empty` unanswerable"
    assert "--max-time" in " ".join(probed), "the probe stays bounded whichever way curl answers"


def test_a_probe_that_would_not_follow_the_redirect_goes_red_instead_of_silently_green(runner):
    # `page=False` is the same red the lane reads when nothing is served behind the redirect: a
    # body of zero bytes. The redirect case is covered by `curl_flags` above; what this one pins
    # is that a miss stays red and names its tokens, whatever the body looks like.
    # `alive=False` keeps the bounded poll honest: the loop ends on the gone-pid rule instead
    # of running out its deadline in a test.
    given_a_grafana_tunnel(runner, page=False, alive=False)

    matched = runner._tunnel_body_signature("tunnel.grafana")

    assert matched is False
    entry = runner._recording.entry("assert:tunnel-signature:tunnel.grafana")
    assert entry["passed"] is False, "an unmatched body must stay red; nothing may paper it over"
    assert "missing=" in entry["detail"] and "grafana" in entry["detail"]


def test_stopping_the_tunnel_takes_the_process_group_not_the_pid_it_holds(runner):
    given_a_grafana_tunnel(runner)

    runner._tunnel_body_signature("tunnel.grafana")

    assert runner.guest.saw("ps -o sid= -p %s" % PID), "the group is chosen from the session it leads"
    assert runner.guest.saw("kill -TERM -%s" % PID), "the tunnel subtree, not the CLI pid"
    assert not runner.guest.saw("kill %s 2>/dev/null" % PID), \
        "a bare pid kill is what left kubectl listening; it must not be used when there is a group to kill"
    assert runner._recording.passed("assert:tunnel-port-freed:tunnel.grafana")
    assert "process group %s" % PID in runner._recording.detail("assert:tunnel-port-freed:tunnel.grafana")


def test_a_tunnel_that_outlives_the_kill_is_reported_from_the_socket_and_not_sleeped_past(runner, monkeypatch):
    # The wait is stepped, not skipped: what the assert has to prove is that it asked the
    # socket, and a shorter deadline changes nothing about that.
    monkeypatch.setattr(stages, "STOP_WAIT_S", 1)
    monkeypatch.setattr(stages, "PORT_FREE_WAIT_S", 1)
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0.5)
    given_a_grafana_tunnel(runner, stoppable=False)

    runner._tunnel_body_signature("tunnel.grafana")

    entry = runner._recording.entry("assert:tunnel-port-freed:tunnel.grafana")
    assert entry["passed"] is False, "the port is the evidence; a survivor must not read as free"
    assert "kubectl" in entry["detail"] and LISTENER_LINE.split("users:")[1] in entry["detail"], \
        "the detail has to name what is still holding the port: %s" % entry["detail"]
    assert runner.guest.saw("kill -KILL -%s" % PID), "TERM alone was not enough, so KILL gets the survivors"


def test_a_tunnel_that_leads_no_session_of_its_own_is_not_killed_by_group(runner):
    # Killing a group the harness does not own could take its own ssh session down. If the
    # start ever stops being a session leader, the harness says so in the detail and stops the
    # pid alone.
    given_a_grafana_tunnel(runner, session="1")

    runner._tunnel_body_signature("tunnel.grafana")

    assert not runner.guest.commands_matching("kill -TERM -1")
    detail = runner._recording.detail("assert:tunnel-port-freed:tunnel.grafana")
    assert "pid %s alone" % PID in detail, "the stop method the harness used belongs in the detail: %s" % detail


def test_a_leak_from_the_previous_tunnel_is_visible_before_the_signature_is_judged(runner):
    # One port, more than one grafana probe across the lane: if a previous tunnel leaked, the body
    # read here is not this tunnel's answer, and the assert would be vacuous.
    given_a_grafana_tunnel(runner, bound_before_start=True)

    runner._tunnel_body_signature("tunnel.grafana")

    detail = runner._recording.detail("assert:tunnel-signature:tunnel.grafana")
    assert "bound-before-start=True" in detail, detail


def test_a_tunnel_that_is_alive_is_not_read_as_gone_by_the_last_resort_break(runner, monkeypatch):
    # `kill -0` succeeds when the process is *there*, which is why the old `&& echo dead || echo
    # alive` reported a healthy tunnel as dead and the break fired on the first probe. The fix is
    # the condition, so the proof is how long the loop stayed: a live tunnel that has not answered
    # yet gets polled to its deadline, not buried in seconds.
    monkeypatch.setattr(stages, "TUNNEL_POLL_S", 0)
    given_a_grafana_tunnel(runner, page=False, alive=True)

    assert runner._tunnel_body_signature("tunnel.grafana", deadline_s=0.5) is False

    assert len(runner.guest.commands_matching("curl ")) > 1, \
        "one probe against a live tunnel is the inverted condition, not a measurement"
    detail = runner._recording.detail("assert:tunnel-signature:tunnel.grafana")
    assert "stop=deadline" in detail, "the loop ran out its own poll; %s" % detail


def test_the_last_resort_break_still_fires_when_the_process_is_actually_gone(runner, monkeypatch):
    # The other polarity, so the fix cannot have been made by disabling the break: a process that
    # really is gone and a log that said nothing ends the poll without spending the deadline.
    monkeypatch.setattr(stages, "TUNNEL_POLL_S", 0)
    given_a_grafana_tunnel(runner, page=False, alive=False)

    runner._tunnel_body_signature("tunnel.grafana", deadline_s=0.5)

    detail = runner._recording.detail("assert:tunnel-signature:tunnel.grafana")
    assert "stop=process-gone" in detail, detail


@pytest.mark.parametrize("shape,answer,expected",
                         [("a connection that was refused", CONNECTION_REFUSED, "connection-failure"),
                          ("a port that bound and answered nothing", ANSWERED_NOTHING, "empty-body"),
                          ("a body that is not the document the table reads", WRONG_DOCUMENT, "body-without-tokens")])
def test_an_unmatched_probe_names_which_of_the_three_it_measured(runner, shape, answer, expected):
    # The claim under test: `body=empty` was not a verdict, it was a symptom two causes shared.
    # Same fake tunnel in all three runs, so only what curl reported differs here.
    given_a_grafana_tunnel(runner, alive=False)
    runner.guest.state["curl"] = answer

    assert runner._tunnel_body_signature("tunnel.grafana") is False, shape

    detail = runner._recording.detail("assert:tunnel-signature:tunnel.grafana")
    assert "verdict=%s" % expected in detail, "%s read as: %s" % (shape, detail)


def test_the_three_failures_are_three_verdicts_and_not_one_anonymous_empty(runner):
    # Distinctness is the ticket's whole ask, so this asserts it across the three runs rather than
    # one at a time: three causes, three words, and not one word wearing three masks.
    measured = []
    for answer in (CONNECTION_REFUSED, ANSWERED_NOTHING, WRONG_DOCUMENT):
        probe = new_runner()
        given_a_grafana_tunnel(probe, alive=False)
        probe.guest.state["curl"] = answer
        probe._tunnel_body_signature("tunnel.grafana")
        measured.append(verdict_of(probe._recording.detail("assert:tunnel-signature:tunnel.grafana")))

    assert len(set(measured)) == 3, "three causes read as one word: %s" % measured
    assert set(measured) == {"connection-failure", "empty-body", "body-without-tokens"}, measured


def test_the_refused_connection_says_so_in_curl_own_words_and_not_in_inference(runner):
    # `-S` is what buys this line: exit 7 with a sentence about why, where it used to be exit 7
    # with nothing. The reader should not have to guess which end refused.
    given_a_grafana_tunnel(runner, alive=False)
    runner.guest.state["curl"] = CONNECTION_REFUSED

    runner._tunnel_body_signature("tunnel.grafana")

    detail = runner._recording.detail("assert:tunnel-signature:tunnel.grafana")
    assert "curl=exit 7" in detail, detail
    assert "Connection refused" in detail, "stderr belongs in the detail line: %s" % detail


def test_a_tunnel_that_named_its_own_failure_names_it_in_the_detail_and_not_only_in_the_file(runner, monkeypatch):
    # The loop's first break is the tunnel's log, and that log is what the evidence file ends up
    # holding. The reader of `result.json` should not have to open the file to learn the probe was
    # not silently answering nothing but was told why it could not bind.
    monkeypatch.setattr(stages, "TUNNEL_POLL_S", 0)
    given_a_grafana_tunnel(runner, page=False)
    runner.guest.state["log"] = "Unable to listen on 127.0.0.1:3001: address already in use"

    assert runner._tunnel_body_signature("tunnel.grafana", deadline_s=0.5) is False

    detail = runner._recording.detail("assert:tunnel-signature:tunnel.grafana")
    assert "stop=tunnel-log" in detail, detail
    assert "Unable to listen" in detail, "the tunnel's own words belong in the detail line: %s" % detail
    assert "Unable to listen" in runner.artifacts.logs["05-features/tunnel-grafana.body.initial"], \
        "and they belong in the evidence file the detail points at"


def test_the_rename_retry_writes_its_own_evidence_and_leaves_the_first_probe_readable(runner):
    # Both grafana probes record under one assert name, so the artifact file is the only thing
    # that keeps them apart. It used to be one name for both, and what a reader opened was the
    # retry's CLI log for a renamed namespace instead of the body that had been judged.
    given_a_grafana_tunnel(runner)

    assert runner._tunnel_body_signature("tunnel.grafana") is True
    # The retry starts its own tunnel and reads its own body; the guest is told to report the
    # first one as stopped so the second probe does not sit out its deadline in a unit test.
    runner.guest.state["alive"] = False
    runner.guest.state["curl"] = Answer(0, "<html>the document behind the renamed namespace</html>", "")
    assert runner._tunnel_body_signature("tunnel.grafana", detail="after renaming the namespace ",
                                         attempt="after-namespace-rename") is False

    # `Artifacts.log` appends `.log`, so the name the harness passes is what a reader greps for.
    logs = runner.artifacts.logs
    assert logs["05-features/tunnel-grafana.body.initial"] == LOGIN_PAGE, \
        "the retry must not stand over the row: %s" % sorted(logs)
    assert logs["05-features/tunnel-grafana.body.after-namespace-rename"] == \
        "<html>the document behind the renamed namespace</html>"

    details = [entry["detail"] for entry in
               runner._recording.named("assert:tunnel-signature:tunnel.grafana")]
    assert len(details) == 2
    assert "attempt=initial" in details[0] and "evidence=tunnel-grafana.body.initial" in details[0], details[0]
    assert "attempt=after-namespace-rename" in details[1], details[1]
