"""What `kmt install` refuses to start without, and which version of it it found.

Two questions with different failure policies, which is the whole reason they live together and
read differently. Presence fails the run: a binary that is not there cannot be worked around.
Version only warns, because kubementat is measured against more than one major of helm and
helmfile on purpose, and a version check that exits would be an operator's downtime bought with a
safety the code already has. A binary that answers nothing is still a warning, not a silence.
"""


import logging
import re
import shutil

from kubementat.core import constants
from kubementat.infrastructure import shell


# timestamp in a build banner from being mistaken for the version.
VERSION_TRIPLE = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)")


# How each pinned binary is asked for its version, measured here rather than guessed: `helm
# --version` is *rejected* by helm 4 (`unknown flag`), so it cannot be the probe for a product that
# says it serves both majors, while `helm version --short` answers the same shape on helm 3 and helm 4
# and needs no struct to unwind. helmfile answers on one line with `--version`, and the other shapes
# print a banner a parser would have to step over. Both answers are read by `first_binary_version`,
# which is why one check serves both binaries. This is an argv table and not the version table: the
# versions are `constants.PINNED_BINARY_VERSIONS`, which is what a CI image build reads later.
BINARY_VERSION_ARGV = {
    "helm": ["helm", "version", "--short"],
    "helmfile": ["helmfile", "--version"],
}


# What `kmt install` refuses to start without. The list describes the
# *distribution*, not only this package's code: a binary belongs here when kubementat's own
# Python execs it, or when a `scripts/**` file this repository ships execs it and the operator
# is the one running that file. Row 21 deleted the last script that shipped here, so that
# second half of the rule has no referent today -- it stays written down because a re-added
# script is precisely what the check the rule is pinned to would catch.
#
# `yq` is gone. Nothing in `src/` execs it and nothing under `scripts/` names
# it; the last thing that ran it was the Tekton pipeline-runner, deleted by `85f1ea8`,
# and that work had already moved Python-side to `yaml.safe_load` / `safe_dump` long before the
# deletion. It stayed declared only because it had once been true, and a check that fails for a
# tool no part of the product runs is a check that trains the operator to work around it. (It
# remains in the CI image, which is a different workstream's inventory and not this list's.)
#
# `jq` goes with it. It was kept deliberately rather than left over --
# no `src/` code path execs it, but `retrieve_node_addresses.sh` did, two lines piping
# `kubectl get nodes -o=json` through it, and that file shipped. The row deleted the file, and
# the argument that kept the tool dies exactly when the file that carried it does: there is now
# no shipped script for kubementat to run whose external requirement this check would have to
# stop verifying, and a name kept for a thing that no longer exists is the same mistake `yq`
# was. What answers the surface is Python, and it needs no parser on the way out: the node
# addresses are `KubernetesUtils.get_nodes()`, whose `status.addresses` arrive as
# `V1NodeAddress` objects and never as JSON text, and the cluster's resource list is
# `KubernetesUtils.get_api_resources()`. Pinned both ways by
# `tests/test_the_declared_dependencies_are_what_the_tree_needs.py`, which re-derives this list
# from the tree and fails if a future script re-introduces a tool the check does not name.
KUBEMENTAT_DEPENDENCIES = [
    "kubectl",
    "helm",
    "helmfile",
    "git",
    "gpg",
    "python3",
    "pip",
    "tkn",
    "helm diff"
]


def first_binary_version(output):
    """The version inside a version answer, or None when there is nothing to read.

    The first line carrying a triple wins, because that is where the version lives on both pinned
    binaries and what follows it can be anything at all — an upgrade banner, a plugin list, a
    traceback. Returning None instead of raising is the whole point: a binary that does not answer
    in a recognisable shape is information for the operator, not a reason to stop a run that was
    otherwise going to succeed.
    """
    for line in (output or "").splitlines():
        found = VERSION_TRIPLE.search(line)
        if found:
            return found.group(0)
    return None

def _record_binary_version(dependency):
    """Say which version of a pinned binary this run is on, and warn — never exit — if it is
    not the one kubementat is measured against.

    Three rules, and the middle one is the whole ticket.

    * **This is a version check and not a presence check.** It runs after the binary is known to
      be there, from inside ``check_dependencies_are_installed``, so the entry point --
      ``kmt install`` -- gets it without the logic being forked.
    * **A mismatch warns and continues.** ``e57fed3`` made the release listing portable across
      helm 3 and helm 4 *deliberately*: the six statuses were chosen so the product would work
      on both majors. Refusing to run outside a declared range would take a working cluster and
      block it, paying for safety the code already provides in operator downtime. So the
      failure behaviour of this check is unchanged for a binary that is *absent*, and only the
      version path became a warning. Nothing here may grow an ``exit(1)``.
    * **A binary that cannot answer does not break anything.** No version, an unrecognisable
      answer, or a command that errors out is a warning naming what was seen, and the run goes
      on. Silence would be the same fail-closed mistake wearing different clothes, so the
      warning is unconditional; only the *claim* that the version is tested is withheld.
    """
    if dependency not in BINARY_VERSION_ARGV:
        return  # not a pinned binary: the presence check above is all it gets
    argv = BINARY_VERSION_ARGV[dependency]
    try:
        # Looked up from the table here instead of handed over as `argv`, so that the guard which
        # refuses an argv assembled from a path can read the program name off this call site.
        answer = shell.run(BINARY_VERSION_ARGV[dependency], capture=True, check=False)
        raw = f"{answer.stdout or ''}{answer.stderr or ''}"
        observed = first_binary_version(raw)
        pinned = constants.pinned_binary_version(dependency)
        if observed is None:
            logging.warning(f"{dependency} did not answer {' '.join(argv)} with a readable version; "
                            f"the run continues without claiming which version this is "
                            f"(saw: {raw.strip()[:120]!r}).")
            return
        if int(observed.split(".")[0]) not in constants.binary_majors_served(dependency):
            logging.warning(
                f"{dependency} {observed} is not a version kubementat is tested against (pinned "
                f"{pinned}, majors served {constants.binary_majors_served_label(dependency)}); the "
                "run continues, because portability across majors is a decision and not an "
                "accident (see e57fed3)."
            )
            return
        logging.info(f"{dependency} version {observed} is within the tested range (pinned {pinned}).")
    except Exception as exc:  # a version check that cannot be answered is not a reason to stop
        logging.warning(f"{dependency} could not be asked for its version ({exc}); the run "
                        "continues without claiming which version it is on.")

def check_dependencies_are_installed(dependencies):
    try:
        for dep in dependencies:
            logging.info(f"Checking installation of: {dep} ...")
            if dep == 'helm diff':
                shell.run(["helm", "diff", "version"])
                continue
            if shutil.which(dep) is None:
                logging.info(f"Error: {dep} not installed.")
                raise Exception(f"Installation requirements not met: {dep} is not installed!")
            # Which version, not merely whether it exists. This half warns and carries on; the
            # raise directly above keeps exactly the behaviour it had before it was written.
            _record_binary_version(dep)
    except Exception as e:
        logging.error("Could not find all required dependencies. Please check that all depdencies are installed correctly!")
        exit(1)
