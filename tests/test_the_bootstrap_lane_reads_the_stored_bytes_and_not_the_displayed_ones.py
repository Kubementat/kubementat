"""The two git-crypt asserts, against a guest that answers each read separately.

`config init` registers git-crypt's filter in `.git/config` and never decides *which files* it
applies to -- that decision belongs to the repository's own `.gitattributes`, and nothing
git-crypt does can cover for one that arrives after the first `git add`. So the ordering of those
two writes is the property that decides whether this tree is safe, and it is invisible to every
read that asks the worktree. The worktree is plaintext by design; on a tree whose attributes
landed late it is *also* the stored object, and it reads exactly the same.

Which is why both asserts are written against the object store, and why the fake has to answer
each read on its own. A single command that handed back a verdict word would let the test grade
its own fixture, and a fixture that says "encrypted" proves nothing about what the lane measured.

The shapes here are the two the mechanism really leaves behind:

* attributes on time, so the stored blob is ciphertext and the worktree is plaintext -- green;
* attributes late and the file never re-added, so the stored blob *is* the plaintext JSON, which
  is the pure form of the defect and goes red on assert 1;
* attributes late and the file later re-added, so `git show HEAD` answers ciphertext while the
  history still holds the plaintext. Assert 1 is green there, correctly: the current commit is
  clean. It is assert 2 that goes red, and that is the pair's whole reason for existing -- the
  second assert is the one that catches the leak the first one is allowed to miss.
"""

import pytest

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness

stages = load_harness()
asserts = stages.asserts

ROOT = "/var/tmp/kmt-e2e-config"
PLAINTEXT = '{"GRAFANA_ADMIN_USER":"grafadmin","GRAFANA_ADMIN_PASSWORD":"s3cret-token"}'
CIPHERTEXT = b"\x00GITCRYPT\x00" + b"\x9f" * 64


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(stages.time, "sleep", lambda seconds: None)


def a_bootstrap_runner():
    """The bootstrap lane, so the asserts read the root that lane prepares and not the shared one."""
    guest = FakeGuest()
    recording = Recording()
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(stages.config.LANES["bootstrap"], options, FakeArtifacts(), None, {},
                               recording, lambda message: None)
    runner.guest = guest
    runner.cluster = asserts.Cluster(guest)
    runner.stage = "02-bootstrap"
    return runner, guest, recording


def stored_is(answer_bytes):
    """What `git show HEAD:<path>` returns for the encrypted file, answered as the reads see it.

    Two commands, both reading the same stored object: `jq` asked to parse it, and `grep` asked
    for the git-crypt marker. Ciphertext fails the first and answers one on the second;
    plaintext JSON does the opposite. Both are needed because the assert names both -- a blob is
    not "encrypted" merely because it does not parse, and the marker is what says *why*.
    """
    parses = CIPHERTEXT not in answer_bytes if isinstance(answer_bytes, bytes) else True
    marker = b"GITCRYPT" in answer_bytes if isinstance(answer_bytes, bytes) else False
    def answer(command):
        if "jq -e ." in command:
            return Answer(1 if not parses else 0, "" if parses else "jq: parse error",
                          "jq: invalid JSON text passed to --argjson" if not parses else "")
        if "grep -a -c GITCRYPT" in command:
            return Answer(0, "1\n" if marker else "0\n", "")
        return None
    return answer


def worktree_is(body):
    """What the working tree answers to the key-existence read `jq -e` performs."""
    def answer(command):
        if "jq -e" in command and "has(" in command:
            return Answer(0, "true\n", "") if body == "plaintext" else Answer(1, "", "jq: parse error")
        return None
    return answer


# ------------------------------------------------------------------ assert 1

def test_a_correctly_ordered_tree_reads_plaintext_here_and_ciphertext_there():
    runner, guest, recording = a_bootstrap_runner()
    guest.respond("git -C", stored_is(CIPHERTEXT))
    guest.respond("jq -e", worktree_is("plaintext"))

    runner._expect_the_encrypted_file_was_stored_encrypted(ROOT)

    assert recording.passed("assert:encrypted-files-are-encrypted-where-they-are-stored"), \
        recording.detail("assert:encrypted-files-are-encrypted-where-they-are-stored")


def test_attributes_written_late_leave_plaintext_in_the_stored_object_and_the_row_says_so():
    """The pure form of the defect: staged before `.gitattributes` existed, and never re-added.

    The worktree is indistinguishable from a healthy tree. Every read that asks the working copy
    is green here, and the stored bytes are the credential.
    """
    runner, guest, recording = a_bootstrap_runner()
    guest.respond("git -C", stored_is(PLAINTEXT.encode()))
    guest.respond("jq -e", worktree_is("plaintext"))

    runner._expect_the_encrypted_file_was_stored_encrypted(ROOT)

    assert not recording.passed("assert:encrypted-files-are-encrypted-where-they-are-stored"), \
        "a stored blob that is still JSON must not pass"


def test_both_halves_are_read_because_a_single_verdict_command_would_measure_the_fixture():
    """The lane runs the worktree read and the stored read as two commands, not one verdict.

    If a later edit collapsed them, this fake would answer the combined command from whichever
    pattern hit first and the assert would be reading a fixture rather than measuring, so the
    command list is part of what is asserted here.
    """
    runner, guest, recording = a_bootstrap_runner()
    guest.respond("git -C", stored_is(CIPHERTEXT))
    guest.respond("jq -e", worktree_is("plaintext"))

    runner._expect_the_encrypted_file_was_stored_encrypted(ROOT)

    worktree_reads = [c for c in guest.commands if "jq -e" in c and "has(" in c]
    stored_reads = [c for c in guest.commands if "git -C" in c and "show HEAD:" in c]
    assert worktree_reads and stored_reads, \
        "the two facts must come from two commands: %s" % guest.commands


# ------------------------------------------------------------------ assert 2

def test_a_history_whose_blobs_never_held_plaintext_is_clean():
    runner, guest, recording = a_bootstrap_runner()
    # One revision, and the blob it stored is ciphertext. The credential itself is resolved inside
    # the guest, so the fake answers the whole scan and reports the tally the shell would report.
    guest.respond("git log --all", Answer(0, "abc1111\nREVISIONS=1 HITS=0\n", ""))
    guest.respond("cat-file", Answer(0, "", ""))

    runner._expect_no_plaintext_credential_in_the_history(ROOT)

    assert recording.passed("assert:no-plaintext-credential-in-any-commit-ever"), \
        recording.detail("assert:no-plaintext-credential-in-any-commit-ever")


def test_the_re_added_file_hides_plaintext_in_the_first_commit_and_the_row_still_says_so():
    """The shape assert 1 is allowed to miss, and the reason the second assert is not redundant.

    Attributes late, then a later `git add` fixes the current commit. `git show HEAD:<path>`
    answers ciphertext and assert 1 is green; the first commit still holds the plaintext JSON.
    The scan finds it, because it asks every revision and not the newest one.
    """
    runner, guest, recording = a_bootstrap_runner()
    guest.respond("git log --all", Answer(0, "abc2222\nabc1111\nREVISIONS=2 HITS=1\nLEAK abc1111\n",
                                         ""))
    guest.respond("cat-file", Answer(0, "", ""))

    runner._expect_no_plaintext_credential_in_the_history(ROOT)

    assert not recording.passed("assert:no-plaintext-credential-in-any-commit-ever"), \
        "a clean newest commit must not carry a history that is not clean"
    assert "abc1111" in recording.detail("assert:no-plaintext-credential-in-any-commit-ever"), \
        "the report has to name the revision that leaked and not merely that something did"


def test_a_history_the_scan_could_not_walk_is_not_reported_as_clean():
    """`git log` answering nothing is not evidence of nothing to find.

    The failure mode of a scan is silence, and silence in the shape of a green row is the worst
    result this file can produce, because it reads exactly like the healthy case.
    """
    runner, guest, recording = a_bootstrap_runner()
    guest.respond("git log --all", Answer(0, "", ""))

    runner._expect_no_plaintext_credential_in_the_history(ROOT)

    assert not recording.passed("assert:no-plaintext-credential-in-any-commit-ever"), \
        "an unanswered scan cannot be a pass"


def test_the_lane_never_locks_or_unlocks_the_tree_it_is_asserting_about():
    """Both asserts work in the state `config init` leaves, so nothing here may change it.

    A `git-crypt lock` would make the worktree ciphertext and turn assert 1 red for a reason that
    is the harness's own, while also measuring a state the product never creates.
    """
    runner, guest, recording = a_bootstrap_runner()
    guest.respond("git -C", stored_is(CIPHERTEXT))
    guest.respond("jq -e", worktree_is("plaintext"))
    guest.respond("git log --all", Answer(0, "abc1111\nREVISIONS=1 HITS=0\n", ""))

    runner._expect_the_encrypted_file_was_stored_encrypted(ROOT)
    runner._expect_no_plaintext_credential_in_the_history(ROOT)

    assert not [c for c in guest.commands if "git-crypt" in c], \
        "the lane must not drive the tool it is asserting about: %s" % guest.commands
