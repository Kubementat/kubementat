"""Which helm a run is on is answered here, and answered as a warning rather than a refusal.

`Automation.check_dependencies_are_installed` used to ask only *"is there a helm?"* — `shutil.which`
against each declared dependency, with `helm diff` excused by a special case. Nothing asked *which*
one, so the product's behaviour was defined by whatever the operator happened to have installed, and
when it went wrong there was no way to say why. That failure is `e57fed3`: `helm list --all` is
accepted by helm 3 and **rejected** by helm 4, so `uninstall_release` worked on one major and died on
the other, and a presence-only check cannot see a semantics difference.

The other option — refuse to run outside a declared range — is the one this file deliberately does
**not** implement, and the reason is recorded in that commit rather than invented here. `e57fed3` made
the release listing portable across helm 3 and helm 4 *on purpose*: the six statuses were chosen so the
product would work on both. A hard pin would take a working helm 3, or a helm 4 newer than anything
measured, and block `kmt install` and `kmt initialize` with it — safety the code already provides, paid
for in operator downtime. So the version path warns and continues. What ends the run is a binary that
is *absent*, and that behaviour is asserted below unchanged, because the way this kind of gate gets
worse is by quietly softening the half that already worked.

Everything here is hermetic on purpose: `shell.run` and `shutil.which` are replaced in the module that
decides, so no test needs a helm, a helmfile, a cluster, or any particular version on this host. That
is the point rather than tidiness — a guard that passes only because this machine runs v4.3.1 is not a
guard, and the two pinned majors are simulated here rather than assumed.
"""

import logging
from types import SimpleNamespace

import pytest

from kubementat.core import constants
from kubementat.automations.install import dependencies as dependencies_slice
from kubementat.automations.install.dependencies import check_dependencies_are_installed

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
    """The declared dependencies, present on `PATH`, answering a version probe that is never sent to a real binary.

    An answer may also be an exception, which is how a tool that errors when probed is modelled
    without a mock that blurs "raised" and "returned nothing".
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
    """Run the gate the way `kmt initialize` and `kmt install` do, without running either command."""
    return check_dependencies_are_installed(list(DEPENDENCIES))


def warnings_about(records, binary):
    """Warnings naming *this* binary, and not its neighbour whose name contains it."""
    return [record.getMessage() for record in records
            if record.levelno == logging.WARNING
            and record.getMessage().split(maxsplit=1)[:1] == [binary]]


def no_errors(records):
    return [record.getMessage() for record in records if record.levelno >= logging.ERROR]


def test_the_pinned_version_is_declared_once_and_the_served_range_is_that_number_derived():
    """The pin and the range cannot disagree, because there is only one of them.

    Two constants that agree by luck are how this gate starts lying quietly, so the range is a
    function of the pinned version and the only fact written beside it is *how far below* the pin a
    run may be — which is a portability decision rather than something a binary can be measured to
    say. The argv is pinned in the same place for the same reason.
    """
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
    """Move the pin, and the verdict moves with it. That is the proof they are one value.

    If a range were duplicated anywhere inside the check, this answers on the stale copy: `9.9.9`
    would still warn and `4.3.1` would still pass quietly. That is the mutation this file exists to
    make impossible, so it is tested as behaviour rather than as a grep.
    """
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
    """`helm version --short` exploding is a warning and not a decision.

    `shell.run` raises for an unusable command line even when the caller asks it not to check, so the
    tolerance has to be explicit rather than an accident of the wrapper.
    """
    tools.answers = {"helm": CommandExecutionError(
        CommandResult(dependencies_slice.BINARY_VERSION_ARGV["helm"], 1, stderr="helm: signal 11"))}
    with caplog.at_level(logging.WARNING):
        check_dependencies(tools)
    assert warnings_about(caplog.records, "helm"), caplog.text
    assert not no_errors(caplog.records), caplog.text
    assert ["helmfile", "--version"] in tools.asked, tools.asked


def test_a_binary_that_is_not_there_still_ends_the_run(monkeypatch):
    """The half that already worked, kept exactly as it was.

    A version check added on top of a presence check must not make the whole gate gentler than it is,
    so a missing binary is asserted to exit rather than to warn. If this ever passes quietly, the
    warning has been applied to the wrong question.
    """
    absent = FakeInstalledTools(present=set())
    monkeypatch.setattr("kubementat.automations.install.dependencies.shell.run", absent.run)
    monkeypatch.setattr("kubementat.automations.install.dependencies.shutil.which", absent.which)
    with pytest.raises(SystemExit):
        check_dependencies(absent)
    assert absent.asked == [], "nothing is probed for a binary that is not installed"
