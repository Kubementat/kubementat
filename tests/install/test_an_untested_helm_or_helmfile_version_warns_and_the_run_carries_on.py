"""Which helm a run is on is answered here, and answered as a warning rather than a refusal."""

import logging
from types import SimpleNamespace

import pytest

from kubementat.automations.install import dependencies as dependencies_slice
from kubementat.automations.install.dependencies import check_dependencies_are_installed
from kubementat.core import constants
from kubementat.errors import CommandExecutionError
from kubementat.infrastructure.shell import CommandResult

# What the fake answers when a test does not say otherwise: the shapes measured here for the pinned
# versions, so a test that is not about the version is not accidentally about the version too.
PINNED_ANSWERS = {
    "helm": "v4.3.1+gc0d08d0",        # `helm version --short`
    "helmfile": "helmfile version 1.7.0",  # `helmfile --version`
}

# What the fake is asked about, so "the run carried on" means the list was walked to the end and not
# abandoned after the interesting entry.
DEPENDENCIES = ["helm", "helmfile", "kubectl"]


class FakeInstalledTools:
    """The declared dependencies, present on `PATH`, answering a version probe that is never sent to a
    real binary.
    """

    def __init__(self, answers=None, present=None):
        self.answers = dict(PINNED_ANSWERS, **(answers or {}))
        self.present = present if present is not None else set(["helm", "helmfile", "kubectl", "jq", "git",
                                                                "gpg", "python3", "pip", "tkn"])
        self.asked = []

    def which(self, name):
        return f"/usr/local/bin/{name}" if name in self.present else None

    def run(self, argv, **kwargs):
        self.asked.append(list(argv))
        if not kwargs.get("capture"):
            # The `helm diff version` call is a presence probe and not a version read: an installed
            # plugin answers that by succeeding quietly.
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        answer = self.answers.get(argv[0], "")
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(stdout=answer, stderr="", returncode=0)


@pytest.fixture
def tools(monkeypatch):
    """Patch the two seams in the module that decides, rather than in a copy of it."""
    installed = FakeInstalledTools()
    monkeypatch.setattr("kubementat.automations.install.dependencies.shell.run", installed.run)
    monkeypatch.setattr("kubementat.automations.install.dependencies.shutil.which", installed.which)
    return installed


def check_dependencies(installed):
    """Run the gate the way `kmt initialize` and `kmt install` do, without running either command.
    """
    return check_dependencies_are_installed(list(DEPENDENCIES))


def warnings_about(records, binary):
    """Warnings naming *this* binary, and not its neighbour whose name contains it."""
    return [record.getMessage() for record in records
            if record.levelno == logging.WARNING
            and record.getMessage().split(maxsplit=1)[:1] == [binary]]


def no_errors(records):
    return [record.getMessage() for record in records if record.levelno >= logging.ERROR]


def test_the_pinned_version_is_declared_once_and_the_served_range_is_that_number_derived():
    """The pin and the range cannot disagree, because there is only one of them."""
    for binary, pinned in constants.PINNED_BINARY_VERSIONS.items():
        major = int(pinned.split(".")[0])
        served = constants.binary_majors_served(binary)
        assert major in served, f"{binary} is pinned to {pinned}, which is not inside its own range"
        assert max(served) == major, f"{binary} claims to serve a major above its own pin"
        assert min(served) == major - constants.PORTABLE_MAJORS_BELOW_THE_PIN[binary]
        assert served == constants.binary_majors_served(binary), "the range is not a pure function of the pin"

    # What is asked of the binary must be what answers it. `helm --version` is *rejected* by helm 4
    # (`unknown flag`), so a check written against it would be blind on exactly the major this product
    # ships, while `helm version --short` reads the same on 3 and 4. That was measured here before the
    # parser was written, not guessed.
    assert set(dependencies_slice.BINARY_VERSION_ARGV) == set(constants.PINNED_BINARY_VERSIONS), (
        "the check must ask about exactly what is pinned, or a pinned tool goes unchecked and an "
        "unpinned one gets a verdict nothing declared")
    assert dependencies_slice.BINARY_VERSION_ARGV["helm"] == ["helm", "version", "--short"]
    assert dependencies_slice.BINARY_VERSION_ARGV["helmfile"] == ["helmfile", "--version"]
    for binary, argv in dependencies_slice.BINARY_VERSION_ARGV.items():
        assert all(isinstance(token, str) for token in argv), f"{binary} is asked with a string, not an argv"


def test_the_range_the_check_compares_against_moves_with_the_pin(tools, monkeypatch, caplog):
    """Move the pin, and the verdict moves with it. That is the proof they are one value."""
    monkeypatch.setitem(constants.PINNED_BINARY_VERSIONS, "helm", "9.9.9")
    with caplog.at_level(logging.WARNING):
        tools.answers = {"helm": "v9.9.9+gcafe"}
        check_dependencies(tools)
        assert not warnings_about(caplog.records, "helm"), \
            "the check is still comparing against a range it wrote down somewhere else"

        caplog.clear()
        tools.answers = {"helm": "v4.3.1+gc0d08d0"}
        check_dependencies(tools)
        assert warnings_about(caplog.records, "helm"), \
            "the version this tree used to be pinned to must warn once the pin has moved"


def test_a_pinned_tool_that_errors_when_asked_does_not_stop_a_run_that_was_going_to_succeed(tools, caplog):
    """`helm version --short` exploding is a warning and not a decision."""
    tools.answers = {"helm": CommandExecutionError(
        CommandResult(dependencies_slice.BINARY_VERSION_ARGV["helm"], 1, stderr="helm: signal 11"))}
    with caplog.at_level(logging.WARNING):
        check_dependencies(tools)
    assert warnings_about(caplog.records, "helm"), caplog.text
    assert not no_errors(caplog.records), caplog.text
    assert ["helmfile", "--version"] in tools.asked, tools.asked


def test_a_binary_that_is_not_there_still_ends_the_run(monkeypatch):
    """The half that already worked, kept exactly as it was."""
    absent = FakeInstalledTools(present=set())
    monkeypatch.setattr("kubementat.automations.install.dependencies.shell.run", absent.run)
    monkeypatch.setattr("kubementat.automations.install.dependencies.shutil.which", absent.which)
    with pytest.raises(SystemExit):
        check_dependencies(absent)
    assert absent.asked == [], "nothing is probed for a binary that is not installed"
