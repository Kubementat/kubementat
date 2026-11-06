"""The last layer of the resolution order, and the rule that keeps it from blocking its own cluster.

Two claims live here, and they look like opposites:

* **In-cluster resolution is last, and it is never a silent winner.** A process running inside a
  cluster can answer "which cluster" from the material the kubelet mounts, but only when layers 1-3
  said nothing. An environment that declares `CLUSTER_KUBE_CONTEXT` uses what it declared, and a
  `--context` beats the pod the command line happens to be standing in.
* **In-cluster is the only resolution path where the target is the cluster the code is running on.**
  Combined with the hard block that refuses a collision, a check that compares against that
  environment is comparing
  against itself, so it would refuse kubementat's own install. The rule: the running cluster is
  excluded from the comparison, *and the report says so* — the "not verified: <reason>" shape,
  never the word "verified", and never a subtraction that leaves no trace.

A test that only sets `KUBERNETES_SERVICE_HOST` proves nothing about this path: the constructor's
answer comes from the mounted files, so every test here fabricates them under `tmp_path`.

"""

import pytest

from kubementat.core.identity import kubeconfig as kubeconfig_module
from kubementat.cluster import (
    Context,
    EnvironmentRegistry,
)
from kubementat.core.identity.target import ClusterTarget
from kubementat.core.constants import (
    SOURCE_FLAG,
    SOURCE_SERVICE_ACCOUNT,
    STATIC_SOURCE_PREFIX,
)
from kubementat.errors import (
    ClusterIdentityCollisionError,
    ClusterNotConfiguredError,
    ClusterResolutionError,
    ServiceAccountNotMountedError,
)

from cluster_fixture import (
    DEV_SERVER,
    MOUNTED_TOKEN,
    PROD_SERVER,
    STAGING_SERVER,
    checkout,
    mounted_service_account,
    redirect_checkout,
    service_account_mount,
    write_environment,
    write_kubeconfig,
)

# The cluster the fabricated pod is running on. Deliberately *not* one of the fixture servers, so a
# test that wants the running cluster and a declared environment to be the same cluster asks for it
# by handing over that server's address.
POD_SERVER = "https://10.96.0.1:6443"


def pod_root(tmp_path, monkeypatch, *, host="10.96.0.1"):
    """A checkout whose `dev` declares nothing, and a kubeconfig with four contexts in it.

    `dev` is left undeclared because both halves of this rule start there: the layer that resolves it is
    the one under test. The four contexts are there so a layer above the one under test always has
    something it *could* have said — an inverted order then shows up as a wrong address instead of
    a passing test.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    kubeconfig = write_kubeconfig(
        root / "kubeconfig",
        {
            "dev-cluster": DEV_SERVER,
            "prod-cluster": PROD_SERVER,
            "staging-cluster": STAGING_SERVER,
            "unclaimed": "https://10.0.9.9:6443",
        },
        current_context="dev-cluster",
    )
    write_environment(root, "dev", context=None, kubeconfig_path=kubeconfig)
    return root, kubeconfig


# --------------------------------------------------------------------------- the layer itself


def test_the_cluster_this_process_is_running_on_answers_when_nothing_above_it_declared_one(
    tmp_path, monkeypatch
):
    """Layer 5, on its own terms: no declaration anywhere, a real mount, and a named address.

    The fixture kubeconfig has a perfectly usable `current-context` and a `dev-cluster` context in
    it, so the answer must come from neither — the mount is the only source that speaks here, and it
    says which one it was.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, "dev", context=None)
    write_kubeconfig(root / "kubeconfig", {"dev-cluster": DEV_SERVER}, current_context="dev-cluster")
    running = mounted_service_account(monkeypatch, root, host="10.96.0.1", port="6443")

    ctx = Context.build("dev", "the-team")

    assert ctx.target.address == running == POD_SERVER
    assert ctx.target.source == SOURCE_SERVICE_ACCOUNT
    assert ctx.target.context is None
    assert ctx.target.kubeconfig_path is None
    assert "service-account" in ctx.resolution_report[0]


def test_a_context_a_command_or_the_environment_declared_outranks_the_pod_its_running_in(
    tmp_path, monkeypatch
):
    """Layers 1 and 2, against layer 5. The flag wins over the mount, and so does the variable.

    The whole point of placing in-cluster last is that a command can be told where to go. Nothing
    here is a fallback that quietly wins because nobody was looking: the flag's file and the
    process's own declaration both beat a complete mount, and the report says which answered.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, "dev", context=None)
    flag = write_kubeconfig(root / "flagged", {"from-a-flag": "https://10.0.9.3:6443"})
    env_var = write_kubeconfig(root / "from-ci", {"from-ci": "https://10.0.9.4:6443"})
    mounted_service_account(monkeypatch, root, host="10.96.0.1")

    by_flag = Context.build("dev", "the-team", context="from-a-flag", kubeconfig=flag)
    assert by_flag.target.address == "https://10.0.9.3:6443"
    assert by_flag.target.source == SOURCE_FLAG

    monkeypatch.setenv("CLUSTER_KUBE_CONTEXT", "from-ci")
    monkeypatch.setenv("CLUSTER_KUBECONFIG_PATH", str(env_var))
    by_process = Context.build("dev", "the-team")
    assert by_process.target.address == "https://10.0.9.4:6443"
    assert "env CLUSTER_KUBE_CONTEXT" in by_process.resolution_report[0]


def test_the_environment_configuration_beats_the_cluster_the_process_is_standing_on(
    tmp_path, monkeypatch
):
    """Layer 3, the case that decides whether in-cluster is a fallback or a default.

    `dev` declares a context, the mount is complete, and the two disagree about which cluster. If
    the mount ever won from here, an environment that says `dev` would install into whatever pod
    ran the command — which is §RC-1's failure class wearing a valid-looking credential. The
    address and the `source` both say the declaration did, and the mount never entered the answer.
    """
    root, kubeconfig = pod_root(tmp_path, monkeypatch)
    write_environment(root, "dev", context="dev-cluster", kubeconfig_path=kubeconfig)
    mounted_service_account(monkeypatch, root, host="10.96.0.1")

    ctx = Context.build("dev", "the-team")

    assert ctx.target.address == DEV_SERVER
    assert ctx.target.context == "dev-cluster"
    assert ctx.target.source.startswith(STATIC_SOURCE_PREFIX)
    assert "service-account" not in ctx.resolution_report[0]


# ---------------------------------------------------------------------- the self-address rule


def test_an_environment_that_is_the_running_cluster_is_excluded_and_the_report_says_so(
    tmp_path, monkeypatch
):
    """The sharp edge, turned into the line an operator reads.

    `dev` has no declaration, so it resolves in-cluster; `prod` declares the very same address. One
    cluster, two names, and the check that protects operators from the wrong cluster would have
    refused to install at all — comparing an environment against the cluster standing underneath
    it. The pair is not compared, and the line says why instead of pretending it checked.
    """
    root, kubeconfig = pod_root(tmp_path, monkeypatch, host="10.0.5.1")
    write_environment(root, "prod", context="prod-cluster", kubeconfig_path=kubeconfig)
    mounted_service_account(monkeypatch, root, host="10.0.5.1")

    ctx = Context.build("dev", "the-team")

    assert ctx.target.address == PROD_SERVER
    line = ctx.resolution_report[0]
    assert f"not verified: prod is the cluster this command is running on ({PROD_SERVER})" in line
    assert "verified against" not in line


def test_the_same_address_does_not_make_a_self_collision_when_the_pod_is_on_another_cluster(
    tmp_path, monkeypatch
):
    """The exclusion is keyed to the running cluster's address, not to the fact of being in a pod.

    One lever changes from the test above: the process is inside a cluster, but a *different* one.
    Now nothing is the running cluster, so the two declarations that address one cluster collide
    exactly as they did before this exclusion existed. A rule that excluded whenever a mount
    happened to be present would pass both tests, and it would be a way to install into someone
    else's cluster.
    """
    root, kubeconfig = pod_root(tmp_path, monkeypatch, host="10.96.0.1")
    write_environment(root, "dev", context="dev-cluster", kubeconfig_path=kubeconfig)
    write_environment(root, "prod", context="prod-cluster", kubeconfig_path=kubeconfig)
    write_kubeconfig(root / "shared", {"both": DEV_SERVER})
    write_environment(root, "dev", context="both", kubeconfig_path=root / "shared")
    write_environment(root, "prod", context="both", kubeconfig_path=root / "shared")
    mounted_service_account(monkeypatch, root, host="10.96.0.1")

    with pytest.raises(ClusterIdentityCollisionError) as refused:
        Context.build("dev", "the-team")

    assert "'prod'" in str(refused.value)
    assert DEV_SERVER in str(refused.value)


def test_the_registry_holds_the_same_rule_as_the_check_and_not_a_weaker_one(tmp_path, monkeypatch):
    """One question, one answer. The registry's own collision list excludes what `Context` excludes.

    The check is the thing that refuses, and `collision_check` is the thing `kmt environments list`
    could one day be built from; if the two applied the rule differently, the listing would say
    "collides" about an environment a command would happily install into. The same target, asked
    about in-cluster and off-cluster, has to give two different answers.
    """
    root, kubeconfig = pod_root(tmp_path, monkeypatch, host="10.0.5.1")
    write_environment(root, "prod", context="prod-cluster", kubeconfig_path=kubeconfig)
    registry = EnvironmentRegistry(root / "platform_config")
    running = mounted_service_account(monkeypatch, root, host="10.0.5.1")
    assert running == PROD_SERVER
    target = ClusterTarget.from_service_account("dev")

    on_this_cluster = registry.collision_check(target)
    assert on_this_cluster.collisions == ()
    # The exclusion is not a fact the reporter has to guess at: the comparison says which address
    # it subtracted, which is what lets `Context.build` report the environments it held back without
    # asking the question a second time.
    assert on_this_cluster.self_address == running

    # The same registry, asked about the same address, but from a process standing on another
    # cluster: now `prod` is somebody else's cluster and the answer has to say so.
    mounted_service_account(monkeypatch, root, host="10.96.0.2")
    from_elsewhere = ClusterTarget.from_kubeconfig(str(kubeconfig), "prod-cluster", "dev")
    assert [record.environment for record in registry.collision_check(from_elsewhere).collisions] == ["prod"]


# ------------------------------------------------------------------- the material and the repr


def test_the_absence_of_a_mounted_service_account_is_a_named_refusal_and_not_a_missing_file(
    tmp_path, monkeypatch
):
    """Not in a pod is a fact about configuration, so it gets an exception that says so.

    `FileNotFoundError` from the kubelet's own directory names a file the operator cannot create
    and explains nothing. The named error says this process is not inside a cluster, names the path
    that is not there, and stays a resolution failure — because layer 5 is a layer, and a caller
    that catches "could not resolve the cluster" catches this too.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    absent = tmp_path / "not-mounted"

    with pytest.raises(ServiceAccountNotMountedError) as refused:
        ClusterTarget.from_service_account("dev", mount_root=absent)

    assert not isinstance(refused.value, FileNotFoundError)
    assert isinstance(refused.value, ClusterResolutionError)
    assert str(absent / "token") in str(refused.value)
    assert "not running inside a cluster" in str(refused.value)
    assert refused.value.reason


def test_a_mounted_token_that_is_empty_authenticates_nothing_and_is_named_as_the_failure(
    tmp_path, monkeypatch
):
    """A half-mounted pod, which is a different failure and gets its own sentence.

    A zero-length token is not "no token"; it is a rotated Secret that has not been rewritten yet,
    and an error that said "the file is missing" would send someone looking for a volume mount.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")
    mount = service_account_mount(root, token="   \n")

    with pytest.raises(ServiceAccountNotMountedError) as refused:
        ClusterTarget.from_service_account("dev", mount_root=mount)

    assert "empty" in str(refused.value)
    assert str(mount / "token") in str(refused.value)


def test_a_command_on_a_machine_that_is_not_a_pod_still_gets_the_configuration_sentence(
    tmp_path, monkeypatch
):
    """The fold that keeps layer 5 from becoming a fifth way to fail.

    Nothing declared a cluster, and there is no mount: the operator's answer is still the refusal that
    names the file, the key, and what was found there — with the in-cluster fact appended as a clause
    rather than a headline. A message that led with "am I a pod?" would be a second, louder answer
    to a question one `static.json` already answers.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    write_environment(root, "dev", context=None)
    static_path = root / "platform_config" / "dev" / "static.json"
    # Not a pod, in the only sense the code can check: no material where the kubelet would have put it.
    # Patched on the module that owns the root, which is where the code that reads it looks.
    monkeypatch.setattr(kubeconfig_module, "SERVICE_ACCOUNT_ROOT", tmp_path / "not-a-pod")

    with pytest.raises(ClusterNotConfiguredError) as refused:
        Context.build("dev", "the-team")

    assert str(static_path) in str(refused.value)
    assert "current-context" in str(refused.value)
    assert "In-cluster resolution was considered last" in str(refused.value)


def test_a_target_built_from_the_mount_carries_the_token_to_a_client_and_nowhere_else(
    tmp_path, monkeypatch
):
    """The only behaviour this object is allowed to own, exercised on the material a pod is handed.

    `client()` is what makes the mount useful, and the two things it must carry out of it are the
    server and the authority: the token authenticates, the mounted `ca.crt` is what makes the
    connection trustworthy, and both come from this target and nothing else. The address is not
    reachable by construction, so a green test here proves nothing was dialled.
    """
    from kubernetes import client as kubernetes_client

    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")
    mounted = service_account_mount(root)

    target = ClusterTarget.from_service_account("dev", mount_root=mounted)
    api_client = target.client()

    assert isinstance(api_client, kubernetes_client.ApiClient)
    assert api_client.configuration.host == POD_SERVER
    assert api_client.configuration.ssl_ca_cert == str(mounted / "ca.crt")
    assert api_client.configuration.api_key.get("authorization")


def test_the_mounted_token_never_reaches_the_repr_of_the_target_it_was_read_for(tmp_path, monkeypatch):
    """The redaction discipline, applied to a constructor that reads the credential from disk.

    A `ClusterTarget` ends up in log lines and in failure messages. This one holds a bearer token
    read straight out of the pod's own file, so the assertion is about the string: it appears
    nowhere in what the object prints, while the address and the provenance that make it useful do.
    """
    root = checkout(tmp_path)
    redirect_checkout(monkeypatch, root)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "6443")
    mounted = service_account_mount(root)

    target = ClusterTarget.from_service_account("dev", mount_root=mounted)
    printed = repr(target) + " " + str(target)

    assert MOUNTED_TOKEN not in printed
    assert "redacted" in printed
    assert POD_SERVER in printed
    assert "service-account" in printed
