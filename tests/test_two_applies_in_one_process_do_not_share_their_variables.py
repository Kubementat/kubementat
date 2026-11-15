"""Two `helmfile_apply` traces in one process: what each child is told is only what *it* decided.

The channel this file guards used to be `os.environ`. `helmfile_apply` published five groups of decided
values -- grafana's admin pair, every component namespace, the ingress class, the configuration
coordinates, and the release knobs -- by writing them into the process environment six times per apply,
so an apply that was still on its way to the spawn could be overtaken by another one, and the child it
then started was templated out of somebody else's configuration. `helmfile_apply` now collects the same
values into one dictionary and hands it to `shell.run(..., env=)`, so a decision lives and dies with
the child it was made for.

**Why this is an interleaving and not two applies in a row.** One apply is started, and *while it is
on its way to spawning helmfile* -- after its values have been collected, before argv is handed over --
the other apply runs to completion inside that gap. A test that ran the two one after the other could
only prove the second saw the first one's leftovers, which is the weaker claim; it would also keep
passing if the change were reverted and an `os.environ.pop()` glued on at the end. That is why the
interleaving is asserted from the recorder's own trace and not taken on faith.

**The claim stops at re-entrancy.** Two applies interleaved in one process, on one thread, do not share
their variables. **That is not the same as thread-safe**, and nothing here should be read as if it were:
cluster reads, the packaged manifest's path and the logging stream are still process-wide, and no item
in this plan adds a thread, a pool or a lock. The docstring in `src/kubementat/infrastructure/helm.py`
and `CHANGELOG.md` say the same thing in the same words.
"""

import json
import os
import types

import pytest

from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.config.keys import component_config_key
from kubementat.core.identity.context import Context
from kubementat.core.identity.target import ClusterTarget
from kubementat.core.constants import SOURCE_CONNECTION
from kubementat.infrastructure import helm as helm_utils
from kubementat.infrastructure.helm import HelmUtils

# The two applies are two real environments in two real configuration roots, not two spellings of one
# tree. A shared root would let the coordinates agree by accident, and "which root is this run" is
# exactly the kind of answer a process-global channel gets wrong.
FIRST = "alpha"
SECOND = "beta"

# A team name per environment. They are spelled without punctuation because a team name is a path
# component, and a tree whose team directory is `alpha-team` would be refused for the shape of its
# name before anything about the environment was decided.
TEAMS = {FIRST: "alphateam", SECOND: "betateam"}

# A knob only one of the two trees declares. It is what catches the leak that a value-by-value
# comparison cannot see: an apply that declares *less* must not inherit what the other one declared,
# and under the old channel it inherited it straight out of the process.
SILENT_KNOBS = {component_config_key("prometheus", "VERSION"): "1.0.0-alpha",
                component_config_key("alloy", "ENABLED"): "false"}


def seed(root, environment: str, team: str, *, declared: dict) -> Config:
    """A `platform_config` tree that declares everything one apply can be told, in its own spelling.

    The namespaces come from `constants.DEFAULT_NAMESPACES`, the same set the export iterates, so a
    component added there lands in this fixture and in the assertions without an edit to either.
    """
    environment_dir = root / "platform_config" / environment
    team_dir = environment_dir / team
    environment_dir.mkdir(parents=True, exist_ok=True)
    team_dir.mkdir(parents=True, exist_ok=True)
    static = {"ENVIRONMENT": environment,
              "CLUSTER_MANAGER_EMAIL": "cluster-manager@example.test",
              "CLUSTER_KUBE_CONTEXT": environment,
              component_config_key(constants.COMPONENT, "INGRESS_CLASS"): f"{environment}-class"}
    for component in sorted(constants.DEFAULT_NAMESPACES):
        static[component_config_key(component, "DEPLOYMENT_NAMESPACE")] = f"{environment}-{component}"
    static.update(declared)
    (environment_dir / "static.json").write_text(json.dumps(static, indent=2, sort_keys=True),
                                                 encoding="utf-8")
    (environment_dir / "static.encrypted.json").write_text(
        json.dumps({"GRAFANA_ADMIN_USER": f"{environment}-admin",
                    "GRAFANA_ADMIN_PASSWORD": f"{environment}-secret"}, indent=2, sort_keys=True),
        encoding="utf-8")
    (team_dir / "static.json").write_text("{}\n", encoding="utf-8")
    (team_dir / "static.encrypted.json").write_text("{}\n", encoding="utf-8")
    return Config(environment, team, kubementat_main_dir=str(root),
                  platform_config_dir=str(root / "platform_config"))


def utils_for(root, environment: str, declared: dict, monkeypatch) -> HelmUtils:
    """One `HelmUtils`, over its own tree, with every variable it decides cleared from the process.

    Clearing them is not decoration: a value the *test* leaves in the environment would look
    identical to a value an earlier apply left there, and this file's whole subject is telling those
    two apart.
    """
    team = TEAMS[environment]
    config = seed(root, environment, team, declared=declared)
    for key in sorted(decided_values(environment, root, team, declared)):
        monkeypatch.delenv(key, raising=False)
    return HelmUtils(Context(environment=environment, team=team, config=config,
                              target=ClusterTarget(environment, environment, None,
                                                   "https://interleaves-address-no-cluster.invalid:6443",
                                                   SOURCE_CONNECTION)))


def decided_values(environment: str, root, team: str, declared: dict) -> dict:
    """Everything one apply decides for its own child, spelled the way the templates read it.

    Derived from the tables the library itself uses -- `constants.DEFAULT_NAMESPACES` and
    `component_config_key` -- so this is not a second list of names someone has to keep in agreement.
    """
    decided = {"GRAFANA_ADMIN_USER": f"{environment}-admin",
               "GRAFANA_ADMIN_PASSWORD": f"{environment}-secret",
               component_config_key(constants.COMPONENT, "INGRESS_CLASS"): f"{environment}-class",
               "KUBEMENTAT_CONFIG_ROOT": str((root / "platform_config").absolute()),
               "KUBEMENTAT_ENVIRONMENT": environment,
               "KUBEMENTAT_TEAM": team}
    for component in sorted(constants.DEFAULT_NAMESPACES):
        decided[component_config_key(component, "DEPLOYMENT_NAMESPACE")] = f"{environment}-{component}"
    decided.update(declared)
    return decided


class InterleavingShell:
    """`shell.run` that runs the second apply inside the first one's gap, and records both handovers.

    A cluster read by the outer apply is the trigger, because it is the last thing `helmfile_apply`
    performs before it assembles the child's environment: the inner apply gets the exact window in
    which the old code had already written its exports into the process. Everything the child was
    handed is kept per call, so the trace can be read back as an ordering and not only as a mapping.
    """

    def __init__(self, trigger_environment: str, inner_apply):
        self._trigger = trigger_environment
        self._inner = inner_apply
        self._inner_ran = False
        self.trace = []
        self.environments = {}

    def run(self, argv, **kwargs):
        argv = [str(entry) for entry in argv]
        environment = argv[argv.index("--kube-context") + 1] if "--kube-context" in argv else "?"
        if argv and argv[0] == "helmfile":
            # None is recorded as None and not folded into the test's own environment: "the child
            # inherited the process" is the answer the old code gave, and it has to be distinguishable
            # from "the child was handed a mapping" when the failure is read.
            handed = kwargs.get("env")
            self.environments[environment] = dict(handed) if handed is not None else None
            self.trace.append(f"{environment}:helmfile-apply")
        else:
            self.trace.append(f"{environment}:{argv[0]}-{argv[1] if len(argv) > 1 else 'call'}")
            if not self._inner_ran and environment == self._trigger:
                self._inner_ran = True
                self._inner()
        return types.SimpleNamespace(returncode=0, ok=True, argv=argv, stdout="[]", stderr="")


def an_interleaved_pair(tmp_path, monkeypatch, outer: str, inner: str,
                        outer_declared: dict, inner_declared: dict):
    """Both applies, wired so `inner` runs inside `outer`'s last read before its spawn."""
    inner_utils = utils_for(tmp_path / inner, inner, inner_declared, monkeypatch)
    recorder = InterleavingShell(outer, lambda: inner_utils.helmfile_apply(
        environment_name=inner, helmfile_label_filter="group=standard"))
    outer_utils = utils_for(tmp_path / outer, outer, outer_declared, monkeypatch)
    # Installed last, and it is the same module global both `HelmUtils` objects read: that is what
    # makes one recorder see both traces, and it is also what makes the interleaving possible at all
    # -- the inner apply's calls arrive inside the outer apply's own, through one patched door.
    monkeypatch.setattr(helm_utils, "shell", recorder)
    return recorder, outer_utils


@pytest.mark.parametrize("outer,inner", [(FIRST, SECOND), (SECOND, FIRST)],
                         ids=["alpha-interleaved-by-beta", "beta-interleaved-by-alpha"])
def test_two_interleaved_applies_neither_of_which_sees_the_ones_variables(tmp_path, monkeypatch,
                                                                          outer, inner):
    """The Done-when, in one trace: every child gets its own apply's decisions and nothing else.

    The parameters swap which environment is the outer one, so the claim is not the accident of a
    convenient order, and an implementation that only worked in one direction is caught.
    """
    outer_declared = SILENT_KNOBS if outer == FIRST else {}
    inner_declared = SILENT_KNOBS if inner == FIRST else {}
    recorder, outer_utils = an_interleaved_pair(tmp_path, monkeypatch, outer, inner,
                                                outer_declared, inner_declared)

    outer_utils.helmfile_apply(environment_name=outer, helmfile_label_filter="group=standard")

    for environment in (outer, inner):
        handed = recorder.environments.get(environment)
        assert handed is not None, (
            f"{environment}'s helmfile was started without an environment of its own, so it inherited "
            "the process one. That is the channel this change removes: whatever an earlier or an "
            "interleaved apply decided is sitting in that environment, and it is not this apply's "
            "answer."
        )
    for environment, declared in ((outer, outer_declared), (inner, inner_declared)):
        handed = recorder.environments[environment]
        want = decided_values(environment, tmp_path / environment, TEAMS[environment], declared)
        wrong = {key: {"child": handed.get(key), "this-apply-decided": value}
                 for key, value in sorted(want.items()) if handed.get(key) != value}
        assert not wrong, (f"{environment}'s child was handed the wrong answer for {len(wrong)} "
                           f"variable(s): {wrong}. The value the child got is on the left and what "
                           "this apply decided is on the right.")

    # The half a value-by-value comparison cannot see: a key one tree declared and the other never
    # mentioned. Under the old channel the child of the quieter apply inherited it from the process.
    quiet, loud = (inner, outer) if not inner_declared else (outer, inner)
    for key in sorted(SILENT_KNOBS):
        assert key not in recorder.environments[quiet], (
            f"{key}={recorder.environments[quiet].get(key)!r} reached {quiet}'s child, and only "
            f"{loud}'s tree declared it. A variable that outlives the apply which decided it is the "
            "channel this change removes."
        )


def test_the_two_traces_were_actually_interleaved_and_not_two_applies_in_a_row(tmp_path, monkeypatch):
    """The guarantee that keeps the test above honest: there was a gap, and the second apply ran in it.

    If the inner apply's spawn does not sit between the outer apply's cluster read and the outer
    apply's spawn, the isolation assertion above would be measuring two sequential applies, which is
    a fact about patience and not about the environment.
    """
    recorder, outer_utils = an_interleaved_pair(tmp_path, monkeypatch, FIRST, SECOND,
                                                SILENT_KNOBS, {})

    outer_utils.helmfile_apply(environment_name=FIRST, helmfile_label_filter="group=standard")

    outer_spawn = recorder.trace.index(f"{FIRST}:helmfile-apply")
    inner_spawn = recorder.trace.index(f"{SECOND}:helmfile-apply")
    assert 0 < inner_spawn < outer_spawn, (
        f"the second apply was not interleaved inside the first one's gap (trace: {recorder.trace}). "
        "Two applies that run one after the other do not demonstrate isolation; one running inside "
        "the other's last read before its spawn does."
    )
    assert recorder.trace[0].startswith(f"{FIRST}:") and recorder.trace[outer_spawn + 1:] == [], \
        recorder.trace


def test_neither_apply_writes_into_the_environment_the_next_apply_will_read(tmp_path, monkeypatch):
    """The measure the item states as `6 writes per apply -> 0`, asserted on the process itself.

    Both applies run; not one of their variables is left behind in the interpreter's environment,
    which is what the next apply in this process -- in whichever order it arrives -- would otherwise
    read back as an answer its own tree never gave.
    """
    recorder, outer_utils = an_interleaved_pair(tmp_path, monkeypatch, FIRST, SECOND,
                                                SILENT_KNOBS, {})
    watched = sorted(set(decided_values(FIRST, tmp_path / FIRST, TEAMS[FIRST], SILENT_KNOBS))
                     | set(decided_values(SECOND, tmp_path / SECOND, TEAMS[SECOND], {})))
    untouched = {key: os.environ.get(key) for key in watched}

    outer_utils.helmfile_apply(environment_name=FIRST, helmfile_label_filter="group=standard")

    assert recorder.environments, "nothing ran helmfile, so there was nothing to leave behind"
    assert {key: os.environ.get(key) for key in watched} == untouched, (
        "helmfile_apply wrote decided values into os.environ; the isolation the two tests above "
        "assert is only true while the process environment is not where those values live"
    )
