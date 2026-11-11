"""The bootstrap asserts run here, not only on a machine, and each one has to reach a pass.

The bootstrap lane's first two runs on a VM both died inside the harness's own asserts, and the
second failed on a line this test executes: an assert's detail string is built before the verdict
is known, so a name left behind by a rename raises whether the tree is good or bad. `config init`
had prepared a correct tree, installed into it, and left no plaintext in the history -- four of
this ticket's rows passed -- and the stage came down over one word in a detail string. The only
defence that costs less than a VM is running the assert.

The fake answers each read the way a correctly prepared tree answers it, and the test says the
assert that reads those answers passes. That makes it two-sided:

* an assert that reads one command differently from the way the product leaves it goes red;
* an assert whose own code is broken -- a stale name, an argument count that does not match its
  format string, a conversion that cannot be applied -- raises, and raises in seconds.

Reads are scripted one at a time rather than with a catch-all, because a fake that answered
everything the same would let an assert grade a fixture instead of the product. The git-crypt pair
is answered as ciphertext-when-stored and plaintext-in-the-worktree, the only shape where both of
the asserts that depend on it can be green at once.
"""

import posixpath
import shlex

import pytest

from e2e_probe_stub import Answer, FakeArtifacts, FakeGuest, Recording, load_harness, shown

stages = load_harness()

ROOT = "/var/tmp/kmt-e2e-config"
KEYPAIR_NAMES = ("deployer_ssh_key", "deployer_ssh_key.pub")
CONFIG_INIT_TELLING = ("Environment 'dev' is configured, but it does not address a cluster yet: "
                       "CLUSTER_KUBE_CONTEXT is null in %s/dev/static.json." % ROOT)
VALIDATE_WARNING = ("WARNING: environment 'dev' is configured, but it does not address a cluster yet: "
                    "CLUSTER_KUBE_CONTEXT is null in %s/dev/static.json. Name the context there." % ROOT)

# The helpers split by argument the way the stage itself splits them, and getting the split wrong
# would assert about a call the stage never makes.
TAKES_ROOT = ("_expect_the_keypair_belongs_to_the_directory",
              "_expect_the_encrypted_file_was_stored_encrypted",
              "_expect_the_layout_and_no_markers",
              "_expect_exactly_one_generated_credential",
              "_expect_no_plaintext_credential_in_the_history")
TAKES_INIT = ("_expect_the_first_commit", "_expect_the_cluster_is_still_unaddressed",
              "_expect_validate_says_only_that")


@pytest.fixture(autouse=True)
def _no_wall_clock_in_a_unit_test(monkeypatch):
    monkeypatch.setattr(stages, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(stages.time, "sleep", lambda seconds: None)


def a_stat_answer(command):
    """What `stat` really does with a format, decided by the command's own spelling.

    This exists because a fake that answered "600 and the path" for anything containing `stat -c`
    would let a wrong spelling pass, and the wrong spelling is a real bug the lane found on a VM.
    `stat -c %a %n FILE` is *one* format argument (`%a`) followed by *two* file operands, so `%n` is
    looked up as a filename. The mode still prints, the exit code is failure, and an assert that
    trusts the exit code reads that as "the key is missing, with mode 600" -- a state no tree is
    ever in, and the reason the first bootstrap run failed a row that had nothing wrong with it.

    So the answer turns on whether the format arrives as one quoted argument. If it does, mode and
    name come back together and the command succeeds; if it does not, the mode still comes back and
    the command fails, which is the exact shape that fools a reader.
    """
    after = command.split("stat -c", 1)[1]
    one_quoted_format = after.lstrip().startswith("'")
    if one_quoted_format:
        return Answer(0, "600 %s\n" % posixpath.join(ROOT, "git_crypt_symmetric.key"), "")
    return Answer(1, "600\n", "stat: cannot statx '%n': No such file or directory\n")


def a_prepared_tree():
    """The bootstrap stage against a guest whose tree is correct in every read."""
    guest = FakeGuest()
    recording = Recording()
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds})
    runner = stages.LaneRunner(stages.config.LANES["bootstrap"], options, FakeArtifacts(), None, {},
                               recording, lambda message: None)
    runner.guest = guest
    runner.stage = "02-bootstrap"

    # `config init`, and the second call the idempotence assert drives. It skips, because repeating
    # the answers is what makes a second run skip rather than refuse. These are matched on the whole
    # verb and not on the word "config": the lane's own root ends in `-config`, so a command like
    # `git -C /var/tmp/kmt-e2e-config show HEAD:...` contains the substring "config show" and a loose
    # pattern would have the fake answer a git read with the output of a different verb.
    guest.respond("kmt config init", Answer(0, "skipped the commit: HEAD already matches\n"
                                              "skipped the keypair: deployer_ssh_key is already there\n",
                                           CONFIG_INIT_TELLING))
    # Both cluster keys answer null, and `show` separates value from layer with three spaces.
    guest.respond("kmt config show", shown(**{stages.config.CLUSTER_KUBE_CONTEXT_KEY: "null",
                                             stages.config.CLUSTER_KUBECONFIG_PATH_KEY: "null"}))
    # Usable for everything except a cluster: no fault, one warning, and that warning is the cluster.
    guest.respond("kmt config validate", Answer(0, "no errors for environment dev (1 warnings)\n",
                                                VALIDATE_WARNING))
    # One commit and only one, over a worktree the second init can compare against.
    guest.respond("rev-parse --git-dir", Answer(0, ".git\n"))
    guest.respond("rev-list --count HEAD", Answer(0, "1\n"))
    guest.respond("status --porcelain", Answer(0, ""))

    # git-crypt's three artifacts. The `stat` answer is what that command really prints: mode, a
    # space, then the path.
    guest.respond("test -e %s" % shlex.quote(posixpath.join(ROOT, ".git", "git-crypt", "keys",
                                                            "default")), Answer(0, ""))
    guest.respond("keys/default/0", Answer(0, "9BC458E878A11D244E196E192369B025DF7E772C.gpg\n"))
    guest.respond("stat -c", a_stat_answer)
    # The keypair is in the configuration directory and nowhere under the guest checkout.
    for name in KEYPAIR_NAMES:
        guest.respond("test -s %s" % shlex.quote(posixpath.join(ROOT, name)), Answer(0, ""))
    guest.respond("-o -name ", Answer(0, ""))

    # The encrypted file: plaintext in the worktree, ciphertext in the object store, marker present.
    guest.respond('has("GRAFANA_ADMIN_PASSWORD")', Answer(0, "true\n"))
    guest.respond("show HEAD:", Answer(1, "jq: invalid JSON text passed to --argjson\n"))
    guest.respond("grep -a -c GITCRYPT", Answer(0, "1\n"))
    # A marker could only survive a tree assembled out of templates, so the search finds none.
    guest.respond("grep -r -l REPLACEME_", Answer(1, ""))
    # The encrypted document answers exactly the one key the generation table names.
    guest.respond("jq -r 'keys | .[]'", Answer(0, "GRAFANA_ADMIN_PASSWORD\n"))
    # The history scan, answered as a clean history that really counted something.
    guest.respond("git log --all", Answer(0, "REVISIONS=1 HITS=0\n"))

    # Everything else this stage asks is a `test` against a file that is present, and present is the
    # case under test, so the catch-all says so rather than pretending to be an answer.
    guest.default = Answer(0, "")
    return runner, recording


def every_assert_the_bootstrap_stage_makes():
    """Each helper, in the order the stage calls it."""
    return [
        ("bootstrap-made-one-commit-and-only-one", "_expect_the_first_commit"),
        ("git-crypt-is-live-in-that-directory", "_expect_git_crypt_initialised_there"),
        ("keypair-lives-in-the-configuration-directory", "_expect_the_keypair_belongs_to_the_directory"),
        ("encrypted-files-are-encrypted-where-they-are-stored",
         "_expect_the_encrypted_file_was_stored_encrypted"),
        ("prepared-tree-is-complete-and-placeholders-are-gone", "_expect_the_layout_and_no_markers"),
        ("bootstrap-generates-exactly-one-credential", "_expect_exactly_one_generated_credential"),
        ("a-new-environment-addresses-no-cluster-and-says-so", "_expect_the_cluster_is_still_unaddressed"),
        ("validate-says-the-prepared-tree-is-usable-for-everything-except-a-cluster",
         "_expect_validate_says_only_that"),
        ("a-second-init-changes-nothing-and-says-which-steps-it-skipped",
         "_expect_a_second_init_writes_nothing"),
        ("no-plaintext-credential-in-any-commit-ever", "_expect_no_plaintext_credential_in_the_history"),
    ]


@pytest.mark.parametrize("assert_name,helper", every_assert_the_bootstrap_stage_makes())
def test_a_correctly_prepared_tree_is_read_as_correct_by_that_assert(assert_name, helper):
    runner, recording = a_prepared_tree()
    prepared = runner.km("config.init", timeout=600)
    if helper in TAKES_ROOT:
        getattr(runner, helper)(ROOT)
    elif helper in TAKES_INIT:
        getattr(runner, helper)(prepared)
    else:
        getattr(runner, helper)()
    assert recording.passed("assert:%s" % assert_name), "%s did not pass a tree that is correct: %s" % (
        assert_name, recording.detail("assert:%s" % assert_name))
