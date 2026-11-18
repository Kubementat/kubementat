"""Tests for `kubementat.infrastructure.shell.run` — the one command-execution primitive.

These pin the behaviour the primitive exists to guarantee: command lines are argv, the
working directory is an argument, output is either streamed or captured by choice, and a
failure says which command failed. They also close the defects the primitive replaces
(plan D8, review 1.4 / 3.10):

* `subprocess.check_call(f"{script} {environment} {team}", shell=True)` — a second parser
  on values the operator controls, so a team name with a space or a `;` was an argument
  split, a broken command line, or an injection.
* `subprocess.run([command], check=True, shell=True, capture_output=True)` for
  `kubectl port-forward` — output captured instead of streamed, so the tunnel looked frozen.
* `os.chdir(...)` before exec — a global process mutation that no failed command could undo.

The companion file `test_command_execution_policy.py` proves nothing else in `src/`
estarts commands, which is what makes these guarantees hold everywhere.
"""

import sys

import pytest

from kubementat import errors
from kubementat.infrastructure import shell


# ---------------------------------------------------------------------------
# the rules that make a string command line impossible
# ---------------------------------------------------------------------------
def test_a_command_string_is_refused_rather_than_parsed():
    """`shell=True` took a string; `run` takes argv and says so instead of guessing."""
    with pytest.raises(TypeError) as excinfo:
        shell.run("helmfile apply -f helmfile.yaml")

    assert "argv" in str(excinfo.value)
    assert "shell=True" in str(excinfo.value)


def test_argv_entries_must_be_strings():
    with pytest.raises(TypeError):
        shell.run(["echo", 1])


def test_an_empty_command_line_is_rejected():
    with pytest.raises(ValueError):
        shell.run([])


def test_a_missing_binary_is_reported_as_such():
    with pytest.raises(errors.CommandExecutionError) as excinfo:
        shell.run(["/definitely/not/here"])

    assert "not found" in str(excinfo.value).lower()
    assert excinfo.value.returncode == 127


def test_a_missing_working_directory_blames_the_directory(tmp_path):
    """`execve` reports both misses as the same errno; only one means "install something".

    The old code `os.chdir`'d first, so this failure surfaced as a confusing
    `FileNotFoundError` from the CLI rather than from the command.
    """
    missing = tmp_path / "never-created"

    with pytest.raises(errors.CommandExecutionError) as excinfo:
        shell.run([sys.executable, "-c", "print('unreachable')"], cwd=str(missing))

    assert str(missing) in str(excinfo.value)
    assert "does not exist" in str(excinfo.value)


# ---------------------------------------------------------------------------
# retries (what the Tekton installer used to do with a hand-rolled loop)
# ---------------------------------------------------------------------------
def _flaky_script(tmp_path, successes_after):
    """A script that fails `successes_after` times, counts its own attempts, then exits 0."""
    script = tmp_path / "flaky.sh"
    counter = tmp_path / "attempts"
    script.write_text(
        "#!/bin/sh\n"
        f"n=$(cat {counter} 2>/dev/null || echo 0)\n"
        f"n=$((n + 1)); echo $n > {counter}\n"
        f"if [ $n -le {successes_after} ]; then exit 1; fi\n"
        "exit 0\n"
    )
    script.chmod(0o755)
    return script, counter


def _attempts(counter_path):
    return int(counter_path.read_text().strip()) if counter_path.exists() else 0


def test_retries_keep_trying_until_the_command_works(tmp_path):
    script, counter = _flaky_script(tmp_path, successes_after=2)

    result = shell.run([str(script)], retries=3, retry_backoff=0.01)

    assert result.ok
    assert _attempts(counter) == 3, "one first attempt plus two retries"


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------
def test_dry_run_reports_success_without_running_anything(tmp_path):
    marker = tmp_path / "marker"

    result = shell.run(["touch", str(marker)], dry_run=True)

    assert result.ok and result.dry_run
    assert not marker.exists()
