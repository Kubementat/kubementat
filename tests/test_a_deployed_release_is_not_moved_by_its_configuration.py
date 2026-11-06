"""A deployed release's namespace is convention, not configuration: kubementat refuses the rename.

The owner decision of 2026-10-30 rules this file. `static.json` names the namespace kubementat
deploys *into*, which is the right answer about a release that is not there yet. Once a release
exists, the namespace is a fact about the cluster, and helm says so harder than a config file can:
every cluster-scoped object a release makes is stamped with
`meta.helm.sh/release-namespace=<where it was installed>`, so applying the same release under a
different namespace answers `invalid ownership metadata; ... "meta.helm.sh/release-namespace" must
equal "kmt-e2e-renamed": current value is "grafana"` — a helm traceback, on a release that is still
serving traffic where it was, and the message an operator used to get for doing the obvious thing
with a config key.

So the refusal happens in kubementat, in words, before the write. Two cases matter and both are
here, because they are the two the `full` lane can only read as a pair:

* **renamed.** The release is in `grafana`, the configuration now says `kmt-e2e-renamed`. The apply
  is refused: it names the component, both namespaces and why, and it writes nothing.
* **never-deployed.** The cluster has no release by that name anywhere. That is a normal install,
  not a rename, and the guard must keep its mouth shut — the case most likely to be got wrong, and
  the one that turns a guard into a wall if it is.

The third claim is the one a well-meaning implementation would add: *cleaning up*. This guard
deletes nothing. It offers no uninstall either, because "take it off the old namespace, then apply
it to the new one" is precisely the alternative that decision rejected, and a refusal that mentions
it is a product that supports it. So one test reads the refusal for that word and fails if it is
there.
"""

import json
import logging
from pathlib import Path

import click
import pytest

from conftest import run_kmt
from kubementat.helm_utils import HelmUtils
from kubementat.core.identity.context import Context

# The manifest this environment applies. Two releases with different owners' namespaces, so a test
# can say "this one is renamed" and "this one is not touched" without the vocabulary being fake:
# `grafana` is the component the lane renames, `traefik` is the one that belongs to a different
# group and is only here so a refusal can be proved not to reach it.
HELMFILE = """\
helmDefaults:
  wait: true
releases:
- name: grafana
  chart: grafana/grafana
  namespace: grafana
  labels:
    component_name: grafana
    group: standard
- name: traefik
  chart: traefik/traefik
  namespace: traefik
  labels:
    component_name: traefik
    group: routing
"""

# What the cluster prints for `helm list --all-namespaces --output json`: rows with the two fields
# every reader of this read uses, name and namespace.
RELEASES = [{"name": "grafana", "namespace": "grafana", "status": "deployed"},
            {"name": "traefik", "namespace": "traefik", "status": "deployed"}]

# The commands that would change the cluster. Reads (`helm list`, `kubectl get`, `helmfile
# template`) are deliberately not here: this file's claim is that the refusal decides without
# touching anything, so the write list is what it is checked against.
WRITERS = {("helmfile",), ("helm", "install"), ("helm", "upgrade"), ("helm", "uninstall"),
           ("helm", "rollback"), ("helm", "repo", "update"), ("kubectl", "apply"),
           ("kubectl", "create"), ("kubectl", "delete"), ("kubectl", "patch"),
           ("kubectl", "replace"), ("kubectl", "label"), ("kubectl", "annotate")}


def seeded(fixture_repo, fake_binaries, releases=RELEASES, **namespaces):
    """The two files the check reads, plus the cluster's answer.

    `static.json` is the configuration half (`Config.component_namespace`), the manifest copy is the
    release half (`component_release_index`), and the planted `helm list` is the cluster half
    (`HELM_LIST_RELEASES_ARGV`). Nothing here is mocked inside `src/` — every fact the guard uses is
    where an operator would put it.
    """
    fixture_repo.environment_static(**namespaces)
    manifest = (fixture_repo.platform_config / fixture_repo.environment
                / "kubementat_components" / "helmfile.yaml.gotmpl")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(HELMFILE)
    fake_binaries.respond("helm", json.dumps(list(releases)), subcommand="list")
    return fixture_repo


def applied(fixture_repo, fake_binaries, **flags):
    """The apply, run for real through `HelmUtils`, against the planted cluster."""
    return HelmUtils(Context.build(fixture_repo.environment, fixture_repo.team)).helmfile_apply(
        environment_name=fixture_repo.environment,
        helmfile_label_filter=flags.get("label_filter", "group=standard"))


def writes(fake_binaries):
    """Everything that would have written to the cluster, in order."""
    return [argv for argv in fake_binaries.commands()
            if any(tuple(argv[:len(hint)]) == hint for hint in WRITERS)]


def test_a_release_deployed_in_another_namespace_is_refused_before_anything_is_written(
        fixture_repo, dry_run_env, fake_binaries):
    """grafana is in `grafana`, `GRAFANA_DEPLOYMENT_NAMESPACE` says `kmt-e2e-renamed`: no apply.

    Both halves of the claim are asserted. The message is what an operator reads, so it has to name
    the component and both namespaces rather than report a failure; the command list is the actual
    ticket, because a refusal that comes after `helmfile apply` is a refusal of nothing.
    """
    seeded(fixture_repo, fake_binaries, GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed")

    with pytest.raises(click.ClickException) as refused:
        applied(fixture_repo, fake_binaries)

    said = str(refused.value)
    assert "grafana" in said
    assert "kmt-e2e-renamed" in said, f"the namespace it was moved to is not named: {said}"
    assert "'grafana'" in said, f"the namespace it is already in is not named: {said}"
    assert "convention" in said and "configuration" in said, \
        f"the reason is missing, so this is a failure and not a refusal: {said}"
    assert "meta.helm.sh/release-namespace" in said, \
        f"the reason must say what breaks, not only that something does: {said}"
    assert writes(fake_binaries) == [], \
        f"a refusal that wrote is not a refusal: {writes(fake_binaries)}"


def test_a_release_the_cluster_has_never_seen_is_installed_and_not_read_as_a_rename(
        fixture_repo, dry_run_env, fake_binaries):
    """The case a guard gets wrong: nothing deployed anywhere is a fresh install.

    The same apply, with the same renamed key, against a cluster that answers "no releases". It is
    not a rename of anything, so it goes ahead — the guard's whole value would otherwise be a
    product that cannot install on a clean cluster, which is the opposite mistake.
    """
    seeded(fixture_repo, fake_binaries, releases=[],
           GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed")

    applied(fixture_repo, fake_binaries)

    started = fake_binaries.commands()
    assert [argv for argv in started if argv[:1] == ["helmfile"]], \
        f"a clean cluster was refused a normal install: {started}"
    assert [argv for argv in started if argv[:1] == ["helm"] and argv[1:2] == ["list"]], \
        "the check is not supposed to be silent even when it has nothing to say"


def test_a_release_that_is_where_it_is_told_to_be_costs_nothing(fixture_repo, dry_run_env,
                                                                fake_binaries):
    """The ordinary apply: cluster and configuration agree, so the check costs one read.

    `grafana` in `grafana`, named by the config. Asserting this is what keeps the guard from being
    a `helm list` that only ever says no: the same planted cluster and the same manifest must pass
    when the key is left alone, so a test that breaks this one has added a condition rather than a
    check.
    """
    seeded(fixture_repo, fake_binaries, GRAFANA_DEPLOYMENT_NAMESPACE="grafana")

    applied(fixture_repo, fake_binaries)

    assert [argv for argv in fake_binaries.commands() if argv[:1] == ["helmfile"]]


def test_the_refusal_names_every_component_it_found_a_rename_in(fixture_repo, dry_run_env,
                                                                fake_binaries):
    """One apply, two renames, one message.

    The operator who renamed both keys does not get to learn about the second one next week, so
    every conflict is said at once -- and the traefik line has to be there for the right reason: it
    is deployed, it is somewhere else, and this apply reaches it.
    """
    seeded(fixture_repo, fake_binaries, GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed",
           TRAEFIK_DEPLOYMENT_NAMESPACE="kmt-e2e-traefik-renamed")

    with pytest.raises(click.ClickException) as refused:
        applied(fixture_repo, fake_binaries)

    said = str(refused.value)
    assert "kmt-e2e-renamed" in said
    assert "kmt-e2e-traefik-renamed" in said, f"only the first conflict was reported: {said}"
    assert "traefik" in said
    assert writes(fake_binaries) == []


def test_a_release_the_cluster_puts_in_two_places_is_named_in_both(fixture_repo, dry_run_env,
                                                                    fake_binaries):
    """A helm release name is unique per namespace and not across them, so "where is it" can answer twice.

    The guard is the one place that has to say both, because the ordinary advice -- "put the key back
    to where the release is" -- has no answer when there are two. Refusing is still right: applying
    under either name orphans the cluster-scoped objects the other one stamped.
    """
    seeded(fixture_repo, fake_binaries,
           releases=[{"name": "grafana", "namespace": "grafana", "status": "deployed"},
                     {"name": "grafana", "namespace": "kmt-old-grafana", "status": "deployed"}],
           GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed")

    with pytest.raises(click.ClickException) as refused:
        applied(fixture_repo, fake_binaries)

    said = str(refused.value)
    assert "grafana" in said and "kmt-old-grafana" in said, said
    assert writes(fake_binaries) == []


def test_a_release_in_a_namespace_kubementat_never_addresses_is_not_read_as_a_rename(
        fixture_repo, dry_run_env, fake_binaries, caplog):
    """k3s's bundled controller is a real `traefik` release, in a namespace kubementat never names.

    It is not kubementat's release moved out of its way, and refusing the routing group for it would
    be this guard stopping a command that has its own way of answering the question -- `kmt routing
    install` reads the chart and not just the name, and it is the command that decides whether a
    Traefik on the node is kubementat's. So the apply goes ahead, and the log says why.
    """
    seeded(fixture_repo, fake_binaries,
           releases=[{"name": "traefik", "namespace": "kube-system", "status": "deployed"},
                     {"name": "grafana", "namespace": "grafana", "status": "deployed"}],
           GRAFANA_DEPLOYMENT_NAMESPACE="grafana")

    with caplog.at_level(logging.INFO):
        applied(fixture_repo, fake_binaries)

    assert [argv for argv in fake_binaries.commands() if argv[:1] == ["helmfile"]], \
        "the guard refused a command that had nothing renamed"
    assert any("kube-system" in record.getMessage() for record in caplog.records), \
        "the reason it did not refuse belongs in the log, not in silence"


def test_a_rename_this_apply_cannot_reach_does_not_stop_the_apply(fixture_repo, dry_run_env,
                                                                  fake_binaries):
    """grafana is renamed, but this command is putting traefik on the cluster.

    `-l component_name=traefik` selects exactly one release, so a check that still stopped the run
    would be refusing a command that could not have moved anything. The narrower answer is the
    honest one: it says no about the writes this command is about to make.
    """
    seeded(fixture_repo, fake_binaries, GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed")

    applied(fixture_repo, fake_binaries, label_filter="component_name=traefik")

    selected = [argv for argv in fake_binaries.commands()
                if argv[:1] == ["helmfile"] and "-l" in argv]
    assert selected and selected[0][selected[0].index("-l") + 1] == "component_name=traefik"


def test_a_cluster_that_cannot_say_where_its_releases_are_gets_no_write_either(
        fixture_repo, dry_run_env, fake_binaries):
    """`helm list` exits non-zero, so the check cannot be done and nothing is guessed.

    `helmfile apply` against a cluster this unreachable fails on the same connection, but as a
    traceback; the refusal here is the read saying it could not be made. What must not happen is
    the apply going ahead on an unanswered question.
    """
    seeded(fixture_repo, fake_binaries, GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed")
    fake_binaries.responses.joinpath("helm.list").write_text("connection refused")
    fake_binaries.responses.joinpath("helm.list.exit").write_text("1")

    with pytest.raises(click.ClickException) as refused:
        applied(fixture_repo, fake_binaries)

    assert "connection refused" in str(refused.value)
    assert writes(fake_binaries) == [], f"the apply went ahead on an unread cluster: {writes(fake_binaries)}"


def test_the_refusal_never_offers_to_take_the_release_off_first(fixture_repo, dry_run_env,
                                                                fake_binaries):
    """The rejected alternative must not appear, not even as advice.

    "Uninstall it from `grafana`, then apply it to `kmt-e2e-renamed`" is the shape the owner decision
    of 2026-10-30 turned down, and a message that prints it is a product that supports it. So the
    sentence is searched for that verb rather than judged by eye -- the only way a future reader
    adding a helpful hint has to fail.
    """
    seeded(fixture_repo, fake_binaries, GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed")

    with pytest.raises(click.ClickException) as refused:
        applied(fixture_repo, fake_binaries)

    said = str(refused.value).lower()
    assert "uninstall" not in said, f"the rejected alternative is being offered: {said}"
    assert "namespace delete" not in said and "helm delete" not in said


def test_the_rename_is_refused_by_the_command_an_operator_would_run(fixture_repo, dry_run_env,
                                                                    fake_binaries):
    """`kmt helm apply` exits non-zero with the reason in its error, as a subprocess.

    The check is `helmfile_apply`'s, and four commands reach it; this is the one an operator who
    renamed a namespace types. In-process tests prove the decision; this proves it arrives through
    the CLI, with the exit code and the text a shell shows them.
    """
    seeded(fixture_repo, fake_binaries, GRAFANA_DEPLOYMENT_NAMESPACE="kmt-e2e-renamed")

    result = run_kmt(["helm", "apply", fixture_repo.environment,
                      "--helmfile-label-filter", "group=standard"], dry_run_env)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "kmt-e2e-renamed" in result.stderr + result.stdout
    assert [argv for argv in fake_binaries.commands() if argv[:1] == ["helmfile"]] == []
