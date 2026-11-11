"""The lifecycle is two lists and they have to agree, in both directions.

Stage names live in one place (`LaneRunner.STAGES`, as name-and-method pairs) and scope members live in
another (`config.SCOPES`). Nothing imports one from the other, so the drift this guards is quiet in
both directions: a stage no scope contains is dead code carrying a name that reads like a stage, and a
name in a scope that the table does not know is an `AttributeError` that arrives after the VM is up.

The budget rule is the third direction. A lane pays for the stages its scope runs and no others, which
is what lets a lane skip the run, re-run and feature stages without being told its dictionary is
missing keys. A stage that scope *does* select has to have a measured budget from the same lane: an
unmeasured stage with a default would run without a bound and report that as a product timeout.
"""

import pytest

from e2e_probe_stub import Recording, FakeArtifacts, load_harness

stages = load_harness()
config = stages.config


def test_every_stage_the_lifecycle_names_exists_as_a_method():
    missing = [method for _name, method in stages.LaneRunner.STAGES
               if not hasattr(stages.LaneRunner, method)]
    assert not missing, "the stage table dispatches to methods that are not there: %s" % missing


def test_every_stage_name_in_a_scope_is_a_stage_the_table_knows():
    known = {name for name, _method in stages.LaneRunner.STAGES}
    unknown = {scope: sorted(set(members) - known) for scope, members in config.SCOPES.items()
               if set(members) - known}
    assert not unknown, "a scope names stages the lifecycle does not define: %s" % unknown


def test_every_lane_is_runnable_for_the_scope_it_was_measured_for_and_refused_for_the_others():
    """The pair check and the stage dispatcher have to agree, for every lane and every scope.

    `stages()` reads a budget only for the stages its scope selects, so a lane that legitimately
    skips the run, re-run and feature stages is runnable for exactly the scopes that do not select
    them -- and an attempt to run it otherwise has to be answered before a machine exists. The two
    answers are produced in different files, so the only honest check is that they never disagree.
    """
    options = type("Options", (), {"env": {}})
    for lane_name, lane in config.LANES.items():
        for scope, members in config.SCOPES.items():
            # The scope is what the dispatcher reads and the timeout hook is what the suite's own
            # override would supply; both stand in for the real object rather than reusing it, so a
            # pair is checked the way the suite would actually run it.
            options = type("Options", (), {"env": {}, "scope": scope,
                                           "stage_timeout": staticmethod(lambda name, seconds: seconds)})()
            runner = stages.LaneRunner(lane, options, FakeArtifacts(), None, {}, Recording(),
                                       lambda message: None)
            try:
                selected = runner.stages()
                worked = True
            except KeyError:
                selected, worked = [], False
            assert worked == config.is_runnable(lane, scope), \
                "%s/%s: the dispatcher says %s, the pair check says %s" % (
                    lane_name, scope, "runnable" if worked else "missing budgets",
                    "runnable" if config.is_runnable(lane, scope) else "missing budgets")
            if worked:
                assert sorted(name for name, _function, _timeout in selected) == sorted(members), \
                    "%s/%s ran a different stage list than its scope names" % (lane_name, scope)


def test_the_bootstrap_lane_prepares_a_directory_and_installs_into_it_once():
    """One lane answers that question, and its budget is the reason the stage exists."""
    assert config.SCOPES["bootstrap"] == ("01-provision", "02-bootstrap")
    assert config.is_runnable(config.LANES["bootstrap"], "bootstrap")
    assert not config.is_runnable(config.LANES["bootstrap"], "install"), \
        "an install-scope bootstrap run would ask this lane for a stage it never measures"
    assert config.LANES["bootstrap"].timeouts["02-bootstrap"] > \
        config.LANES["smoke"].timeouts["02-install"], \
        "the bootstrap stage runs a second preparation on top of the install it shares with smoke"


def test_the_stage_names_its_own_config_root_because_a_lane_may_prefer_another():
    """The root is a lane answer and not a stage one.

    Thirteen capability rows used to spell `{workdir}/platform_config` inside their argv, which put
    the same fact in thirteen files and gave a lane that wanted its own tree nothing to do except
    grow a second row per verb. It is named once now, and a lane that wants somewhere else says it in
    its own shape.
    """
    assert config.LANES["bootstrap"].config_dir != config.LANES["smoke"].config_dir, \
        "the bootstrap lane asserts that the keypair is absent from the checkout, which it cannot say "\
        "while its own tree is the checkout's"
    assert config.LANE_PLACEHOLDERS["config_dir"] == config.LANES["smoke"].config_dir


def test_a_lane_with_no_budget_for_a_selected_stage_is_refused_before_the_machine_exists():
    """A missing budget is the suite being wrong, and the error has to say so in those words.

    A KeyError naming a dictionary would be the harness reporting a product failure for the harness's
    own gap, which is the kind of red row that costs a day.
    """
    lane = config.Lane(name="incomplete", vcpu=1, ram_gib=1, disk_gib=1, install_groups=("standard",),
                       features=(), timeouts={"01-provision": 60})
    options = type("Options", (), {"env": {}, "stage_timeout": lambda name, seconds: seconds,
                                   "scope": "full"})
    runner = stages.LaneRunner(lane, options, FakeArtifacts(), None, {}, Recording(),
                               lambda message: None)
    with pytest.raises(KeyError) as caught:
        runner.stages()
    assert "incomplete" in str(caught.value) and "budget" in str(caught.value), \
        "the error must name the lane and say it is a budget: %s" % caught.value
