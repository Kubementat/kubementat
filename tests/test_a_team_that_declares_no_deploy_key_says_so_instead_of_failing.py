"""A team that declares no SSH deploy key is answered, not crashed.

`setup-secrets` writes one Secret per entry of `SSH_DEPLOY_KEYS`. Where no layer declares that key
the answer is nothing, and nothing is the correct answer: every field of an entry is the operator's
key material, so there is nothing for kubementat to compose on the operator's behalf. What used to
happen was the loop being handed `None` and raising `TypeError: 'NoneType' object is not iterable`,
which is a Python error that names no key, no file and no team -- the reader has to find the
configuration themselves, and the run reports a crash where the product had a fact to state.

The same shape is already answered well by `create-grafana-accounts`, which reports that
`GRAFANA_USERS` declares no accounts and creates nothing. These two rows make `setup-secrets` say
the same kind of thing, and pin the two halves of it: nothing is written, and the run does not fail.
"""

import logging

from cluster_parity import ClusterOperation, cluster_named

from conftest import FIXTURE_KUBE_CONTEXT

# The cluster the fixture environment declares, so the recorded write is compared against the
# cluster it claims and not against a bare operation that would match any of them.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)


def test_a_team_that_declares_no_deploy_key_writes_no_secret(fixture_repo, dry_run_env,
                                                             recorded_api):
    fixture_repo.team_static_encrypted(SSH_DEPLOY_KEYS=[])

    from cli.commands import kubementat
    from click.testing import CliRunner

    result = CliRunner().invoke(kubementat.setup_secrets, ["dev", "fixture"])

    assert result.exit_code == 0, result.output
    assert [op for op in recorded_api.operations() if not op.is_read] == []


def test_the_stated_answer_names_the_key_the_layer_and_the_team(fixture_repo, dry_run_env,
                                                                recorded_api, caplog):
    """The sentence has to be actionable, because the run exits zero.

    Exit zero with no output is how a lane reads "this team has no deploy keys" as "the command
    worked", so the warning has to name the key, say that no layer declares it, and name the team
    whose layer was searched. An absent key and a written nothing are two facts the reader has to
    be able to tell apart from this line alone.
    """
    fixture_repo.team_static_encrypted(SSH_DEPLOY_KEYS=[])

    from cli.commands import kubementat
    from click.testing import CliRunner

    with caplog.at_level(logging.WARNING):
        CliRunner().invoke(kubementat.setup_secrets, ["dev", "fixture"])

    stated = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "SSH_DEPLOY_KEYS" in stated
    assert "no layer declares" in stated.lower()
    assert "fixture" in stated


def test_a_declared_deploy_key_still_lands_in_the_namespace_it_names(fixture_repo, dry_run_env,
                                                                     recorded_api):
    """The unchanged half: one declared key still becomes one Secret.

    A guard that passes by writing nothing would be indistinguishable from the refusal above if
    this row did not exist, so the declared case is asserted against the same recorder the
    refusal above is asserted empty with.
    """
    from cli.commands import kubementat
    from click.testing import CliRunner

    CliRunner().invoke(kubementat.setup_secrets, ["dev", "fixture"])

    assert ClusterOperation("ensure", "secret", "fixture-pipelines", "deploy-key-one-secret",
                            (("data", ("ssh-privatekey",)), ("type", "kubernetes.io/ssh-auth")),
                            cluster=CLUSTER) in recorded_api.operations()
