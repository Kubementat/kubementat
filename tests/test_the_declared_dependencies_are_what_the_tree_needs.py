"""The tool list `kmt install` refuses to run without, held to what the gate actually checks.

`check_dependencies_are_installed` is the gate in front of `kmt initialize` and `kmt install`: a name
it cannot find on `PATH` ends the run. The question this file answers is the one a list like that
always raises -- is it still the list of what kubementat needs?

It used to be answered by deriving the answer from the tree: parse every `shell.run` argv in `src/`
and every command line in `scripts/**`, and reconcile the two against the declaration. That is a
statement about this repository's own source, and the plan's rule for it is that a test may not
police the tree. It also drifted in both directions: `yq` outlived the script that ran it, and the
scan needed a canary row of its own to notice when the scan itself went quiet.

So the guard is now the one a suite can honestly run: **ask the gate**. A name on the list must be a
name the gate can check and would refuse if it were missing -- that is what makes the list an
interface and not a comment. What the derivation used to catch, and what is still asserted here in a
narrower form, is the dead-entry case: a tool that is declared and that nothing runs any more. The
pair that taught this suite that lesson (`jq`, `yq`) is asserted directly against the declaration,
which is where it would be wrong. The reason both were removed is recorded in the changelog, not
here: the scripts they were execed by left the tree, and a declared tool nothing runs is a
requirement the operator is asked to install for a program that does not exist.
"""

import pytest

from kubementat.automations.install import dependencies as dependencies_slice
from kubementat.automations.install.dependencies import check_dependencies_are_installed


def declared() -> list:
    """The list as the product declares it -- the only owner, read rather than copied.

    It is read rather than written down here on purpose: a second copy in the suite would be a second
    place a tool is added, and the whole point of the gate is that there is one.
    """
    return list(dependencies_slice.KUBEMENTAT_DEPENDENCIES)


class ToolchainOnPath:
    """The tools the gate may look for, present unless a test says otherwise.

    `which` is the presence check and `run` is the version probe, so both halves of the gate answer
    without a host that happens to have `helm` installed. The version probe returns nothing readable,
    which is the state the version check warns about and carries on from -- a dependency test that
    failed on a version string would be testing the wrong half.
    """

    def __init__(self, present):
        self.present = set(present)
        self.asked = []

    def which(self, name):
        return f"/usr/local/bin/{name}" if name in self.present else None

    def run(self, argv, **kwargs):
        self.asked.append(list(argv))
        return type("Answer", (), {"stdout": "", "stderr": "", "returncode": 0})


@pytest.fixture
def toolchain(monkeypatch):
    """Patch the two seams inside the gate, not a copy of the module."""
    installed = ToolchainOnPath(declared())
    monkeypatch.setattr(dependencies_slice.shell, "run", installed.run)
    monkeypatch.setattr(dependencies_slice.shutil, "which", installed.which)
    return installed


def test_every_declared_dependency_is_a_name_the_gate_can_actually_check(toolchain):
    """Nothing on the list is decoration: with the tool present the gate passes, without it the run ends.

    A declaration is only worth making if the gate that reads it can act on it. The `helm diff` entry
    is the one name that is not a program of its own -- the gate runs `helm diff version` for it, which
    is why the pair is declared as one string and not as two.
    """
    names = declared()
    assert names, "the dependency list is empty, so `kmt install` checks nothing"

    assert check_dependencies_are_installed(list(names)) is None

    for name in names:
        if " " in name:
            # Not a program of its own: the gate probes it through the base command, so there is no
            # `PATH` entry to remove. What is asserted is that the probe happens at all.
            toolchain.asked.clear()
            assert check_dependencies_are_installed([name]) is None
            assert toolchain.asked, f"the gate declares {name!r} and never asks anything about it"
            continue
        present_now = set(toolchain.present)
        toolchain.present.discard(name)
        try:
            with pytest.raises(SystemExit):
                check_dependencies_are_installed(list(names))
        finally:
            toolchain.present = present_now


def test_a_tool_nothing_in_the_product_runs_stays_out_of_the_list():
    """The pair this suite learned about twice: declared, needed by nothing, still installed.

    Asserted against the declaration and not against a scan of `src/` for the string. The failure mode
    that matters is a tool coming *back* into the list because some script somewhere started needing
    it again: the guard here is that the product's own list of what it requires names neither, and if
    one is added the next change has to say why a `kmt` run needs a JSON tool at all.
    """
    for retired in ("jq", "yq"):
        assert retired not in declared(), \
            f"{retired} is declared again. It left when the last script that execed it left; a " \
            "requirement kubementat no longer runs is a package the operator installs for nothing."
