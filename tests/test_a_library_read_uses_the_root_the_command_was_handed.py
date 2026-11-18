"""Three library reads that used to answer "which root?" for themselves, and now do not.

The subject here is not the flag — `Config` already took a root and
`core/config/paths.py:120` (`resolve_platform_config_dir`) already
outranked the variable with it. It is the three constructions that built a `Config` out of an
`(environment, team)` pair and nothing else, so a command that had been *told* which tree to read
went and asked the process's own environment instead. That is the failure class this ticket is
judged on: it is invisible until two trees exist, and then it reads the wrong one.

What changed, site by site:

* `automations/routing/routing.py:_configuration` — used to build `Config(ctx.environment,
  ctx.team)`; it now reads the `Config` the `Context` carries. Same accessor, same keys, and no
  second instance beside the one that resolved the cluster.
* `core/config/teams.py:declared_team_namespaces` — a per-team `Config` inside a sweep that belongs to
  one command. A sweep is one root, decided once upstream.
* `infrastructure/grafana/client.py:from_environment`'s `else` branch — reached only when a caller had no
  `Config`; the one caller in `src/` (`infrastructure/grafana/accounts.py`'s `_client_for`) hands its
  `Context`'s down,
  which is what the test below asserts rather than a new path. That branch is deleted rather
  than guarded, so the subject of the grafana half below is "this cannot happen": the read is
  still asserted, and the impossibility is asserted next to it instead of by a factory that raises
  when the module builds one.

Each case is built the same way: two trees that answer the same key differently, `KUBEMENTAT_HOME`
pointed at the decoy so a re-derived root finds something to be wrong about, and the read made from
the tree the object was handed. No cluster is contacted — none of these three reads needs one.
"""

import json


from cluster_fixture import configuration_only_context, redirect_checkout
import kubementat.infrastructure.grafana.accounts as grafana_accounts
from kubementat.core.config.config import Config


ENVIRONMENT = "dev"
TEAM = "dev1"


def a_root(tmp_path, name, *, environment_values, team_values=None, encrypted_values=None):
    """One tree, named *name*, carrying the environment and team files this subject reads.

    The pair is written on every call on purpose: `Config` is lazy about team files and loud about
    missing environment ones, so a decoy that is missing a file would fail for a reason no test
    is about.
    """
    root = tmp_path / name
    environment_dir = root / "platform_config" / ENVIRONMENT
    environment_dir.mkdir(parents=True)
    (environment_dir / "static.json").write_text(json.dumps(environment_values), encoding="utf-8")
    (environment_dir / "static.encrypted.json").write_text(
        json.dumps(encrypted_values or {}), encoding="utf-8")
    if team_values is not None:
        team_dir = environment_dir / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "static.json").write_text(json.dumps(team_values), encoding="utf-8")
    return root


def handed_down(root):
    """The `Config` an object would be handed: a tree, and the pair that lives in it."""
    return Config(ENVIRONMENT, TEAM, kubementat_main_dir=root,
                  platform_config_dir=root / "platform_config")


def test_the_grafana_credential_a_command_opens_with_is_the_one_its_context_handed_down(
        tmp_path, monkeypatch):
    """A `Context` that was told which tree to read reads that tree, and no other.

    The read is unchanged and so is the decoy: the decoy's encrypted file
    declares **no** credential, so a client that resolved its own root would have refused with
    "no credential to reach grafana" and the assertion below would have been an exception rather
    than a credential.

    The spy that used to sit in this test (`grafana_client.Config` swapped for a raising factory)
    is gone, and deliberately so: it guarded a branch that no longer exists, and a test that
    guards a deleted branch passes for the wrong reason. Its job moved to the test below, which
    asserts the impossibility rather than the guard.
    """
    handed = a_root(tmp_path, "root-the-command-was-handed",
                    environment_values={"ENVIRONMENT": ENVIRONMENT},
                    encrypted_values={"GRAFANA_ADMIN_USER": "admin@handed.test",
                                      "GRAFANA_ADMIN_PASSWORD": "handed-password"})
    redirect_checkout(monkeypatch, a_root(tmp_path, "root-the-process-would-find",
                                          environment_values={"ENVIRONMENT": ENVIRONMENT},
                                          encrypted_values={}))

    ctx = configuration_only_context(ENVIRONMENT, TEAM, config=handed_down(handed))

    client = grafana_accounts._client_for(ctx, "https://grafana.test", None)

    assert client.credential_source == "GRAFANA_ADMIN_USER", \
        "the pair was read out of a tree this command never pointed at"
