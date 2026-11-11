"""Tests for `kubementat.shell.run` — the one command-execution primitive.

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

import json
import os
import sys

import pytest

from kubementat import shell

# A program that prints what it was handed, so argv corruption is visible as a diff.
PRINT_ARGV = "import json,sys; print(json.dumps(sys.argv[1:]))"


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


# ---------------------------------------------------------------------------
# what the child process actually receives
# ---------------------------------------------------------------------------
def test_argv_tokens_arrive_intact():
    """Nothing between here and `execve` splits a token.

    This is the single assertion that D8 is gone: `;`, `$`, whitespace and a literal
    newline all travel as one argument each, which no shell-backed call could keep true.
    """
    hostile = ["hello world", "a;b;echo pwned", "$HOME", "* ? *", "line\nbreak", "--flag=with=equals"]

    result = shell.run([sys.executable, "-c", PRINT_ARGV] + hostile, capture=True)

    assert json.loads(result.stdout) == hostile


def test_a_glob_is_not_expanded_by_the_call():
    """`*` reaching a shell means the filesystem leaks into the command line."""
    result = shell.run([sys.executable, "-c", PRINT_ARGV, "*"], capture=True)
    assert json.loads(result.stdout) == ["*"]


def test_cwd_is_an_argument_and_not_a_process_wide_side_effect(tmp_path):
    before = os.getcwd()
    result = shell.run([sys.executable, "-c", "import os; print(os.getcwd())"],
                       capture=True, cwd=str(tmp_path))

    assert os.path.realpath(result.stdout.strip()) == os.path.realpath(str(tmp_path))
    assert os.getcwd() == before, "run() must not move the calling process"


def test_the_environment_is_inherited_unless_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("FIN02_PROBE", "from-the-parent")
    argv = [sys.executable, "-c", "import os; print(os.environ.get('FIN02_PROBE', '<unset>'))"]

    assert shell.run(argv, capture=True).stdout.strip() == "from-the-parent"
    # An explicit env replaces the child's environment; PATH is what the interpreter needs.
    assert shell.run(argv, capture=True, env={"PATH": os.environ["PATH"]}).stdout.strip() == "<unset>"


def test_stdin_can_be_answered_without_a_shell_pipe():
    """`kubectl auth can-i` used to need `input=` plus `text=True` at every call site."""
    result = shell.run([sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"],
                       capture=True, input="yes")

    assert result.stdout.strip() == "yes"


def test_output_reaches_the_terminal_when_not_captured(capfd):
    """The `port-forward` bug: capturing an interactive command makes it look frozen.

    `capfd`, not `capsys`: the child writes to the real file descriptor, which is what a
    terminal is. `capsys` would only see output that came back through Python.
    """
    shell.run([sys.executable, "-c", "print('Forwarding from 127.0.0.1:3001')"], capture=False)

    assert "Forwarding from 127.0.0.1:3001" in capfd.readouterr().out


def test_output_is_held_back_when_captured(capfd):
    shell.run([sys.executable, "-c", "print('not for the terminal')"], capture=True)

    assert "not for the terminal" not in capfd.readouterr().out


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------
def test_a_non_zero_exit_raises_and_names_the_whole_command():
    with pytest.raises(shell.CommandExecutionError) as excinfo:
        shell.run([sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"],
                  capture=True)

    error = excinfo.value
    assert error.returncode == 3
    assert "exit code 3" in str(error)
    assert "boom" in str(error), "the operator should not have to re-run the command to learn why"
    assert error.argv[0] == sys.executable


def test_check_false_hands_the_verdict_back_instead_of_raising():
    result = shell.run([sys.executable, "-c", "import sys; sys.exit(7)"], capture=True, check=False)

    assert result.returncode == 7
    assert result.ok is False
    assert result.argv[0] == sys.executable


def test_a_missing_binary_is_reported_as_such():
    with pytest.raises(shell.CommandExecutionError) as excinfo:
        shell.run(["/definitely/not/here"])

    assert "not found" in str(excinfo.value).lower()
    assert excinfo.value.returncode == 127


def test_a_missing_working_directory_blames_the_directory(tmp_path):
    """`execve` reports both misses as the same errno; only one means "install something".

    The old code `os.chdir`'d first, so this failure surfaced as a confusing
    `FileNotFoundError` from the CLI rather than from the command.
    """
    missing = tmp_path / "never-created"

    with pytest.raises(shell.CommandExecutionError) as excinfo:
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


def test_retries_are_not_attempted_when_not_asked_for(tmp_path):
    script, counter = _flaky_script(tmp_path, successes_after=2)

    with pytest.raises(shell.CommandExecutionError):
        shell.run([str(script)])

    assert _attempts(counter) == 1


def test_retries_are_exhausted_then_the_failure_is_reported(tmp_path):
    script, counter = _flaky_script(tmp_path, successes_after=9)

    with pytest.raises(shell.CommandExecutionError):
        shell.run([str(script)], retries=2, retry_backoff=0.01)

    assert _attempts(counter) == 3, "the 3-attempt ceiling the install scripts had"


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------
def test_dry_run_reports_success_without_running_anything(tmp_path):
    marker = tmp_path / "marker"

    result = shell.run(["touch", str(marker)], dry_run=True)

    assert result.ok and result.dry_run
    assert not marker.exists()
