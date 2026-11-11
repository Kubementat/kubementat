"""An environment kubementat cannot read is named in the report, not dropped from it.

The line this file tests is about honesty rather than about error
handling:

* an environment whose context is missing, whose kubeconfig is gone, or whose file cannot be parsed
  **does not block** the command — otherwise `kmt routing status dev` would depend on `staging`'s
  configuration being valid, which is a dependency nobody asked for;
* but it appears in the report, by name, with the reason. A check that silently omits environments
  is a check that lies about its coverage.

So the report line is allowed exactly two shapes, and the tests below check that no third one can
appear:

```
cluster dev: https://10.0.1.9:6443 (platform_config/dev/static.json) — verified against 1 other environment
cluster dev: https://10.0.1.9:6443 (flag --context) — not verified: staging could not be resolved (no such context 'staging-eu' in ~/.kube/config)
```

and *verified* is not said about an environment that was not compared, which is why a root with
nothing else in it gets a "not verified" line too.

"""

from kubementat.cluster import Context, EnvironmentRegistry

from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    STAGING_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

VERIFIED = "verified against"
NOT_VERIFIED = "not verified"


def healthy_root(tmp_path, monkeypatch, extra=()):
    """`dev` and `prod` on their own clusters, plus whatever `extra` environments the test adds.

    The extras are the point: each one is a way an environment can exist and still be unreadable.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(
        root / "kubeconfig",
        {"dev-cluster": DEV_SERVER, "prod-cluster": PROD_SERVER, "staging-cluster": STAGING_SERVER},
        current_context="dev-cluster",
    )
    write_environment(root, "dev", context="dev-cluster", kubeconfig_path=kubeconfig)
    write_environment(root, "prod", context="prod-cluster", kubeconfig_path=kubeconfig)
    for environment, context in extra:
        write_environment(root, environment, context=context, kubeconfig_path=kubeconfig)
    return root


def report_of(root, environment="dev"):
    return Context.build(environment, "the-team").resolution_report[0]


def test_an_environment_that_cannot_be_read_does_not_stop_the_command_that_could(tmp_path, monkeypatch):
    """The command runs, and the coverage of its own check is stated in the same line.

    `staging` declares nothing. `dev` is fine, resolves, and gets its answer — the refusal to be
    blocked by someone else's configuration is the whole trade the rule makes.
    """
    root = healthy_root(tmp_path, monkeypatch, extra=[("staging", None)])

    line = report_of(root)

    assert "cluster dev: https://10.0.1.9:6443" in line
    assert VERIFIED not in line
    assert NOT_VERIFIED in line


def test_the_environment_that_was_not_compared_is_named_with_the_reason(tmp_path, monkeypatch):
    """The second shape, line for line: the name, then the reason in parentheses.

    The reason is the part an operator can act on without opening anything, so it names the key
    and the file it is missing from.
    """
    root = healthy_root(tmp_path, monkeypatch, extra=[("staging", None)])
    static_path = root / "platform_config" / "staging" / "static.json"

    line = report_of(root)

    assert "staging could not be resolved" in line
    assert "CLUSTER_KUBE_CONTEXT" in line
    assert str(static_path) in line


def test_a_sibling_whose_kubeconfig_moved_is_named_by_the_file_that_is_not_there(tmp_path, monkeypatch):
    """The same report line, for the reason "the file it pointed at is gone".

    A kubeconfig that moved is not the same failure as a key that was never written, and the report
    says which, because they are fixed differently.
    """
    root = healthy_root(tmp_path, monkeypatch)
    write_environment(root, "staging", context="staging-cluster", kubeconfig_path=root / "old-kubeconfig")

    line = report_of(root)

    assert "staging could not be resolved" in line
    assert "old-kubeconfig" in line


def test_a_sibling_whose_file_cannot_be_parsed_is_named_as_unreadable_and_not_as_unset(tmp_path, monkeypatch):
    """A `static.json` that is not valid JSON is a file that broke, not a file that said no.

    Reporting it as "no context set" would be a small lie about coverage, which is exactly what
    this whole report is there to avoid.
    """
    root = healthy_root(tmp_path, monkeypatch, extra=[("staging", "staging-cluster")])
    broken = root / "platform_config" / "staging" / "static.json"
    broken.write_text('{"CLUSTER_KUBE_CONTEXT": "staging-cluster"', encoding="utf-8")

    line = report_of(root)

    assert "staging could not be resolved" in line
    assert "valid JSON" in line


def test_the_registry_reports_every_environment_by_name_and_not_only_the_ones_that_resolved(
    tmp_path, monkeypatch
):
    """The enumeration itself is not a filter.

    Three directories declare a cluster and one declares nothing, and the answer names all four.
    An environment that drops out of this list would drop out of every check that reads it, which
    is the lie this rule is written to prevent.
    """
    root = healthy_root(tmp_path, monkeypatch, extra=[("staging", None)])
    (root / "platform_config" / "notes").mkdir()  # depth 1, no static.json: not an environment
    team = root / "platform_config" / "dev" / "the-team"
    team.mkdir(parents=True)
    (team / "static.json").write_text("{}", encoding="utf-8")  # depth 2: a team, not an environment
    registry = EnvironmentRegistry(root / "platform_config")

    records = registry.all()

    assert [record.environment for record in records] == ["dev", "prod", "staging"]
    assert [record.environment for record in records if record.resolved] == ["dev", "prod"]
    unresolved = [record for record in records if not record.resolved]
    assert len(unresolved) == 1
    assert unresolved[0].reason


def test_a_verified_line_says_how_many_it_compared_against(tmp_path, monkeypatch):
    """The first shape, with its coverage. A number, because "verified" alone says nothing.

    The count is the part that survives the reader's disbelief: two environments resolved and
    compared, and the line is allowed to claim exactly that much.
    """
    root = healthy_root(tmp_path, monkeypatch)

    line = report_of(root)

    assert f"{VERIFIED} 1 other environment" in line
    assert NOT_VERIFIED not in line


def test_three_resolving_environments_are_reported_as_two_comparisons_not_three(tmp_path, monkeypatch):
    """The count is of the *others*, so it can never be read as "this many clusters exist".

    `staging` joins the root as a fully declared environment; the line has to count the two it
    compared against and not itself.
    """
    root = healthy_root(tmp_path, monkeypatch, extra=[("staging", "staging-cluster")])

    line = report_of(root)

    assert f"{VERIFIED} 2 other environments" in line


def test_a_root_with_one_environment_in_it_claims_nothing(tmp_path, monkeypatch):
    """The edge case that would be an overstatement, and the shape it must not take.

    Nothing else was declared, so nothing was compared, so the word *verified* may not appear. This
    is the test for the rule rather than for a code path.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(root / "kubeconfig", {"dev-cluster": DEV_SERVER})
    write_environment(root, "dev", context="dev-cluster", kubeconfig_path=kubeconfig)

    line = report_of(root)

    assert VERIFIED not in line
    assert NOT_VERIFIED in line
    assert "no other environment" in line


def test_the_line_carries_the_environment_the_address_and_the_file_the_claim_came_from(tmp_path, monkeypatch):
    """The line is readable on its own, which is what makes it safe to print before a write.

    All four things are in it: which cluster, at which address, declared where, and checked against
    how many.
    """
    root = healthy_root(tmp_path, monkeypatch)
    static_path = root / "platform_config" / "dev" / "static.json"

    line = report_of(root)

    assert line.startswith("cluster dev: https://10.0.1.9:6443 (")
    assert str(static_path) in line
    assert "—" in line


def test_a_directory_that_is_not_an_environment_is_not_enumerated_as_one(tmp_path, monkeypatch):
    """The enumeration is depth 1 with a `static.json`, and that is the whole rule.

    A team directory is depth 2, and `kubementat_components/` carries no environment-level file, so
    neither appears — without a name list anywhere that would have to be kept up to date.
    """
    root = healthy_root(tmp_path, monkeypatch)
    team = root / "platform_config" / "dev" / "the-team"
    team.mkdir(parents=True)
    (team / "static.json").write_text("{}", encoding="utf-8")
    components = root / "platform_config" / "dev" / "kubementat_components"
    components.mkdir(parents=True)

    registry = EnvironmentRegistry(root / "platform_config")

    assert [record.environment for record in registry.all()] == ["dev", "prod"]
