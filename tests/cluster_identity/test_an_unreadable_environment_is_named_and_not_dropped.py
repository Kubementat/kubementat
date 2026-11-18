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

from cluster_fixture import (
    DEV_SERVER,
    PROD_SERVER,
    STAGING_SERVER,
    checkout,
    redirect_checkout,
    write_environment,
    write_kubeconfig,
)

from kubementat.core.identity.context import Context

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
