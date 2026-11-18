"""A team that declares no SSH deploy key is answered, not crashed."""

import logging

from conftest import FIXTURE_KUBE_CONTEXT
from recorded_operations import ClusterOperation, cluster_named

# The cluster the fixture environment declares, so the recorded write is compared against the
# cluster it claims and not against a bare operation that would match any of them.
CLUSTER = cluster_named(context=FIXTURE_KUBE_CONTEXT)


def test_the_stated_answer_names_the_key_the_layer_and_the_team(fixture_repo, dry_run_env,
                                                                recorded_api, caplog):
    """The sentence has to be actionable, because the run exits zero."""
    fixture_repo.team_static_encrypted(SSH_DEPLOY_KEYS=[])

    from click.testing import CliRunner

    from cli.commands import kubementat

    with caplog.at_level(logging.WARNING):
        CliRunner().invoke(kubementat.setup_secrets, ["dev", "fixture"])

    stated = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "SSH_DEPLOY_KEYS" in stated
    assert "no layer declares" in stated.lower()
    assert "fixture" in stated


def test_a_declared_deploy_key_still_lands_in_the_namespace_it_names(fixture_repo, dry_run_env,
                                                                     recorded_api):
    """The unchanged half: one declared key still becomes one Secret."""
    from click.testing import CliRunner

    from cli.commands import kubementat

    CliRunner().invoke(kubementat.setup_secrets, ["dev", "fixture"])

    assert ClusterOperation("ensure", "secret", "fixture-pipelines", "deploy-key-one-secret",
                            (("data", ("ssh-privatekey",)), ("type", "kubernetes.io/ssh-auth")),
                            cluster=CLUSTER) in recorded_api.operations()
