"""Running the lane stages without a VM: a guest that answers by pattern.

`tests/e2e/harness/stages.py` talks to the machine under test through exactly one primitive
(`Guest.run`, `Guest.write_file`), which is what makes a stage runnable in pytest: keep the
stage, substitute the answers. This module is that substitution, plus the loader that makes
`import harness.stages` work and the runner-shaped object a stage method needs.

Nothing here tries to model a cluster. A rule is a substring of the command and an answer, and
an answer may be a callable so a test can make the guest *change* — the difference between
"killed" and "still listening", which is the whole content of a lifecycle assert.
"""

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parent / "e2e"


@contextmanager
def _harness_on_path():
    """`stages.py` imports its siblings as `harness.*`, so the harness root has to be a path."""
    sys.path.insert(0, str(HARNESS_ROOT))
    try:
        yield
    finally:
        try:
            sys.path.remove(str(HARNESS_ROOT))
        except ValueError:
            pass


def load_harness():
    """Import `harness.config`, `harness.asserts`, `harness.stages` without installing them."""
    with _harness_on_path():
        importlib.import_module("harness.asserts")
        importlib.import_module("harness.config")
        return importlib.import_module("harness.stages")


def shown(**answers):
    """What `kmt config show` would print for the keys a test is asking the harness to read back.

    A stage reads a configuration answer through the verb, so a fake guest has to answer that verb and
    not the `jq` some earlier revision of the stage happened to run. The shape is what makes that safe:
    `show` separates the value from its layer tag with **three** spaces and names the file that
    answered, and a test that wrote its own shorter line would exercise a reader that nothing in the
    product produces. So a value here is either a string, which is tagged as the harness's own fixture
    answering, or a `(value, provenance)` pair for a test that wants to assert about where the answer
    came from.

    Pass it to `FakeGuest.respond` under the pattern both show commands share:

        guest.respond("config show", shown(GRAFANA_DEPLOYMENT_NAMESPACE="grafana"))

    One set of answers per fake guest, and it answers the environment listing and the team listing
    alike. That is deliberate: which layer a stage asked about is a fact about the capability table it
    went through, not about what the answer should be, and a test that could give different bytes to
    the two listings could hide a stage reading the wrong layer. A test that genuinely needs the two
    to disagree answers the command itself.
    """
    def answer(command):
        lines = []
        for key, given in answers.items():
            value, provenance = given if isinstance(given, tuple) else (given, "harness fixture")
            lines.append("%s = %s   (%s)" % (key, value, provenance))
        return Answer(0, "\n".join(lines) + "\n", "")
    return answer


class Answer:
    """What `Guest.run` returns: an exit code and two streams, never an exception."""

    def __init__(self, exit_code=0, stdout="", stderr=""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.command = ""

    @property
    def ok(self):
        return self.exit_code == 0

    @property
    def output(self):
        return self.stdout + self.stderr

    def excerpt(self, limit=600):
        text = self.output.strip().replace("\n", " | ")
        return text[:limit] + ("..." if len(text) > limit else "")

    def __repr__(self):
        return "Answer(exit=%s, out=%r)" % (self.exit_code, self.excerpt(120))


class FakeGuest:
    """A guest that matches command substrings, in order, and records everything it ran.

    `default` answers the commands nobody scripted, and defaults to success — a stage that only
    needs "that worked" must not be forced to script it. A response is a string (stdout), an
    `Answer`, or a `callable(command)` returning either; the callable is how a rule reads or
    writes the fake's `state`, so the fake can behave differently the second time it is asked.
    """

    def __init__(self, answers=(), default="", state=None):
        self.answers = list(answers)
        self.default = default
        self.state = state if state is not None else {}
        self.commands = []
        self.files = {}

    def respond(self, pattern, response):
        self.answers.append((pattern, response))
        return self

    def run(self, command=None, timeout=120, env=None, cwd=None, stdin=None, retry=True):
        text = command if command is not None else "bash -s <<stdin"
        self.commands.append(text)
        return self._respond(text)

    def run_argv(self, argv, **kwargs):
        return self.run(command=" ".join(str(token) for token in argv), **kwargs)

    def write_file(self, path, content, timeout=30):
        self.files[path] = content
        return self.run(command="cat > %s" % path, stdin=content, timeout=timeout)

    def _respond(self, command):
        for pattern, response in self.answers:
            if pattern not in command:
                continue
            if callable(response):
                response = response(command)
            return response if isinstance(response, Answer) else Answer(0, str(response), "")
        return Answer(0, str(self.default), "")

    def saw(self, *needles):
        """True when any recorded command contains every needle — the order-independent check."""
        return any(all(needle in command for needle in needles) for command in self.commands)

    def commands_matching(self, needle):
        return [command for command in self.commands if needle in command]


class FakeArtifacts:
    """The artifact writer as a dict; a log written here is what a test can read back."""

    def __init__(self):
        self.logs = {}

    def log(self, stage, name, text):
        self.logs["%s/%s" % (stage, name)] = text
        return "artifacts/%s/%s" % (stage, name)

    def snapshot(self, name, payload):
        self.logs[name] = repr(payload)
        return name

    def envelope(self, name, payload):
        self.logs[name] = repr(payload)
        return name

    def read(self, stage, name):
        return self.logs.get("%s/%s" % (stage, name), "")


class Recording:
    """Everything the stage asserted, in order — the unit under test for an assert change."""

    def __init__(self):
        self.entries = []

    def __call__(self, stage, name, cls, passed, detail="", attempts=1, artifacts=None):
        self.entries.append({"stage": stage, "name": name, "class": cls,
                             "passed": bool(passed), "detail": detail})

    def named(self, name):
        return [entry for entry in self.entries if entry["name"] == name]

    def entry(self, name):
        found = self.named(name)
        assert found, "nothing recorded for %s, which asserted: %s" % (
            name, ", ".join(entry["name"] for entry in self.entries))
        return found[-1]

    def passed(self, name):
        return self.entry(name)["passed"] is True

    def detail(self, name):
        return self.entry(name)["detail"]
