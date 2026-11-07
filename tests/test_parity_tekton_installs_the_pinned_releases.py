"""Parity: the Tekton installer script, and `TektonUtils.install()` that replaced it.

The bash half of every comparison here is a **record**, not a run: the script is deleted, and what
it sent to the cluster is written down below. `git show 3f9d80b --stat | grep -i tekton` still
finds the blob it was deleted from, and the Python half is executed for real against the recording
fakes, so the comparison is still a comparison and not a claim about itself.

What the script did was seven `kubectl` calls and nothing else:

    apply -f <pipeline>/release.yaml            (no namespace — it brings the control plane)
    apply -f -                                  (the helm-deployer ClusterRole, on stdin)
    describe clusterrole helm-deployer-…        (printed what it had just written)
    get sc                                      (printed the storage classes)
    apply -n tekton-pipelines -f <dashboard>/release-full.yaml
    apply -n tekton-pipelines -f <triggers>/release.yaml
    apply -n tekton-pipelines -f <triggers>/interceptors.yaml

The two shapes that differ are both explained in the tests that cover them: the ClusterRole is a
*route* change (the client writes the object instead of a heredoc, so the object is what gets
compared) and the two reads printed things rather than changing anything, which is the same
decision `kmt uninstall`'s `kubectl api-resources` dump already made.

The release URLs are built from the pins in `static.json`, which is what the code reads too, so the
record below stays true when a version moves instead of freezing a number here.
"""

import json

from cluster_parity import ClusterOperation, cluster_named, compare
from kubementat.infrastructure.kubernetes import KubernetesUtils
from kubementat.infrastructure.tekton import install as tekton_install
from kubementat.infrastructure.tekton.install import (HELM_DEPLOYER_CLUSTER_ROLE_NAME,
                                                     TEKTON_RELEASE_BASE_URL)
from conftest import FIXTURE_ENVIRONMENT, is_apply
from cluster_fixture import (configuration_only_cluster,
                              configuration_only_context)
from asset_paths import template_path

# The cluster the install's client addressed, in the record's spelling — the same target the
# service class is handed, so that the ClusterRole this comparison pins to one object says which
# cluster it was applied to. The script's side of the row is a record, not a run: it carried no
# flags for the reducer to read, and a declared cluster is how that gap is closed honestly.
CLUSTER = configuration_only_cluster(FIXTURE_ENVIRONMENT)

# The pinned triplet, read from the template that ships it — the same file `initialize` writes a
# team's `static.json` from, and the same file the deleted script's `jq` read pointed at.
PINNED_VERSIONS = json.loads(
    template_path("environment", "static.json.template").read_text())
VERSION_KEYS = ("TEKTON_VERSION_PIPELINE", "TEKTON_VERSION_TRIGGERS", "TEKTON_VERSION_DASHBOARD")

NAMESPACE = "tekton-pipelines"

# The four release manifests, as the deleted script assembled them.
PIPELINE_RELEASE = (f"{TEKTON_RELEASE_BASE_URL}/pipeline/previous/"
                    f"v{PINNED_VERSIONS['TEKTON_VERSION_PIPELINE']}/release.yaml")
DASHBOARD_RELEASE = (f"{TEKTON_RELEASE_BASE_URL}/dashboard/previous/"
                     f"v{PINNED_VERSIONS['TEKTON_VERSION_DASHBOARD']}/release-full.yaml")
TRIGGERS_RELEASE = (f"{TEKTON_RELEASE_BASE_URL}/triggers/previous/"
                    f"v{PINNED_VERSIONS['TEKTON_VERSION_TRIGGERS']}/release.yaml")
TRIGGERS_INTERCEPTORS = (f"{TEKTON_RELEASE_BASE_URL}/triggers/previous/"
                         f"v{PINNED_VERSIONS['TEKTON_VERSION_TRIGGERS']}/interceptors.yaml")

# Every `kubectl apply` the script made, in the order it made them. One of them — the ClusterRole
# arriving on stdin — is not a URL fetch, so it is called out separately: it is the call the client
# replaced, and it is compared as an *object* in the second test rather than as an argv list.
HEREDOC_APPLY = ["kubectl", "apply", "-f", "-"]
APPLIED_BY_THE_SCRIPT = [
    ["kubectl", "apply", "-f", PIPELINE_RELEASE],
    HEREDOC_APPLY,
    ["kubectl", "apply", "-n", NAMESPACE, "-f", DASHBOARD_RELEASE],
    ["kubectl", "apply", "-n", NAMESPACE, "-f", TRIGGERS_RELEASE],
    ["kubectl", "apply", "-n", NAMESPACE, "-f", TRIGGERS_INTERCEPTORS],
]

# The record's URL fetches, which is the half every argv comparison below is about.
RELEASE_FETCHES_BY_THE_SCRIPT = [call for call in APPLIED_BY_THE_SCRIPT if call != HEREDOC_APPLY]

# What the ClusterRole is, reduced to the claim `kubectl apply` made about it. The rules are what
# the helm deployer is authorised to do, and they are what a parity failure has to be about.
HELM_DEPLOYER_ROLE_ASKED = ClusterOperation(
    "ensure", "clusterrole", None, HELM_DEPLOYER_CLUSTER_ROLE_NAME,
    (("rules", ((("", "rbac.authorization.k8s.io", "networking.k8s.io", "batch", "extensions",
                 "apps", "autoscaling", "tekton.dev", "resolution.tekton.dev"),
                ("*",), ("*",)),)),
     ("labels", ("managed-by",))), cluster=CLUSTER)

# The two reads, declared as things only the bash did. `kubectl describe` proved the role was
# readable and `kubectl get sc` told the operator what storage the cluster had; both printed,
# neither decided. What replaced them is `preflight_installed_tekton_api_versions`, which asserts
# something a describe could not: that the controller really serves `tekton.dev/v1`.
PRINTED_ONLY_READS = [
    ClusterOperation("read", "clusterrole", None, None, cluster=CLUSTER),
    ClusterOperation("read", "sc", None, None, cluster=CLUSTER),
]

# The read only the Python does, and it is the one place this comparison is *not* a parity claim:
# the deleted script applied the Triggers release straight after the control plane, which is the
# race the E2E suite caught as `no endpoints available for service "tekton-pipelines-webhook"`. So
# the code asks whether the webhook resolves before it applies anything that gets validated through
# it. There is no bash half to compare against because bash simply did not ask the question.
PYTHON_ONLY_READS = [
    ClusterOperation("read", "endpoint", NAMESPACE, None, cluster=CLUSTER),
]


def configured_environment(fixture_repo, **overrides):
    """The fixture's `static.json`, with the pinned versions in it.

    Every install test needs them, and the fixture would otherwise write a config file with no
    Tekton versions at all — which is a real state a real checkout can be in, and is the first
    test of the unit file rather than an accident here.
    """
    return fixture_repo.environment_static(
        **{key: PINNED_VERSIONS[key] for key in VERSION_KEYS}, **overrides)


def run_the_python_install(fixture_repo, **env_static_overrides):
    """`tekton_install.install()`, the way `kmt install` runs it.

    The function rather than the command, because `kmt install` is the only caller and it is a
    fifteen-step flow that needs a cluster to get this far. Everything under the call — the shims,
    the fake client, the fixture's `platform_config` — is the real thing.
    """
    configured_environment(fixture_repo, **env_static_overrides)
    # The same `Context`, and the three arguments the function takes come out of it: the class that
    # used to resolve the client is gone (P4-6), and the install never needed the team its
    # constructor refused a run over.
    context = configuration_only_context(fixture_repo.environment, fixture_repo.team)
    tekton_install.install(KubernetesUtils(context), context.config, context.environment)


def without_addressing(argv):
    """The same command line with the cluster flags taken back out.

    The exception is that the kubectl calls which stay carry their cluster, so this side runs
    `kubectl apply --context dev -f URL` where the script ran `kubectl apply -f URL`. The parity
    claim is about which manifests, from which addresses, into which namespace — and the addressing
    is the one place the two sides are meant to differ, so it is removed here instead of being
    declared as a difference a test has to tolerate.
    """
    # Only the addressing flags and their values go; `-n <namespace>` stays, because the namespace
    # is part of the parity claim and `--context` is not.
    ADDRESSING = ("--context", "--kubeconfig", "--cluster", "--user")
    kept, skip_next = [], False
    for argument in argv:
        if skip_next:
            skip_next = False
            continue
        if argument in ADDRESSING:
            skip_next = True
            continue
        kept.append(argument)
    return kept


def apply_commands(fake_binaries):
    """The `kubectl apply` calls that asked for a URL, in order."""
    return [without_addressing(call) for call in fake_binaries.commands()
            if is_apply(call) and call[-1].startswith("https://")]


def test_the_install_applies_the_same_manifests_from_the_same_addresses(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """Same four URLs, same order, same namespace flags — the release surface of the install.

    The order is part of the record: the control plane first, then the things that read from it.
    A reordered install that happened to work on one cluster would be a difference the tests below
    could not see, so it is compared as a list and not as a set.
    """
    run_the_python_install(fixture_repo)

    assert apply_commands(fake_binaries) == RELEASE_FETCHES_BY_THE_SCRIPT, \
        "the release manifests kubementat installs are not what the deleted script installed"


def test_the_cluster_role_that_replaced_the_heredoc_is_the_same_object(
        fixture_repo, dry_run_env, recorded_api):
    """Two routes to one ClusterRole, compared as the operation it performs.

    bash piped a manifest into `kubectl apply -f -`; the client sends the same object through the
    RBAC API. The harness folds create-and-replace into `ensure` for exactly this reason, so what
    is left to compare is the object: its name, its label, and the nine API groups it grants.
    """
    run_the_python_install(fixture_repo)

    compare([HELM_DEPLOYER_ROLE_ASKED], recorded_api.operations(),
            bash_only=PRINTED_ONLY_READS,
            python_only=PYTHON_ONLY_READS,
            context="the ClusterRole kmt install writes against the one the deleted installer "
                    "applied by hand")


def test_the_namespace_the_releases_land_in_is_the_configured_one(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The one literal the script hardcoded, measured against the config key that replaced it.

    `TEKTON_NAMESPACE="tekton-pipelines"` was a script assignment; the code asks
    `Config.component_namespace("tekton")`, which answers from `TEKTON_DEPLOYMENT_NAMESPACE` and
    falls back to `constants.py`. On this configuration the two answers are the same string — and
    that equality is the whole claim, because if they had disagreed the releases would have landed
    next to a controller in a namespace nothing tunnels to.
    """
    run_the_python_install(fixture_repo)

    addressed = [call[call.index("-n") + 1] for call in fake_binaries.commands()
                 if is_apply(call) and "-n" in call]
    assert addressed == [NAMESPACE] * 3, \
        "three release manifests are addressed at three different namespaces"
    assert fixture_repo.pipeline_namespace != NAMESPACE, (
        "the fixture's pipeline namespace and the tekton namespace are the same string, which "
        "would make this comparison say nothing about where the releases land")


def test_a_renamed_tekton_namespace_is_the_namespace_the_releases_are_applied_to(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """The part where Python is not equivalent, and the improvement is the point.

    The script could only ever write into `tekton-pipelines`. The code asks the configuration, so
    renaming the component's namespace moves the install's `-n` flags with it. That is a deliberate
    difference from the record above, and this is where it is stated rather than hidden.
    """
    run_the_python_install(fixture_repo, TEKTON_DEPLOYMENT_NAMESPACE="team-mesh")

    assert [call[call.index("-n") + 1] for call in fake_binaries.commands()
            if is_apply(call) and "-n" in call] == ["team-mesh"] * 3


def test_the_install_runs_kubectl_and_nothing_else(fixture_repo, dry_run_env, fake_binaries,
                                                   recorded_api):
    """A migration is allowed to change *how*, not what is executed.

    The script called `kubectl` and read two JSON files; it had no `helm`, `tkn` or `git-crypt` in
    it. Anything new in this log is a new binary the operator has to install, which is a change
    nobody asked for and the exact failure mode the dependency rows of the plan keep naming.
    """
    run_the_python_install(fixture_repo)

    assert {call.program for call in fake_binaries.calls()} == {"kubectl"}, \
        fake_binaries.commands()


def test_the_install_does_not_apply_a_release_manifest_twice(fixture_repo, dry_run_env,
                                                             fake_binaries, recorded_api):
    """The failure the record's order protects: the same manifest fetched twice is still wrong.

    A retried loop, or a component listed twice in the table, would fetch more than four times and
    look like a working install while doing the cluster's work twice. Nothing in bash's seven calls
    repeated a URL, so neither does this.
    """
    run_the_python_install(fixture_repo)

    fetched = [call[-1] for call in apply_commands(fake_binaries)]

    assert len(fetched) == len(set(fetched)) == 4, fetched


def test_the_urls_the_install_builds_are_the_urls_the_pinned_versions_point_at(
        fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """Pins and URLs in one place now, which is what the bash half used to be able to miss.

    The pins lived in `static.json` and the URLs were assembled in bash; that split is why the old
    test ran the script. Here both halves are Python, so the assertion is simply that a version
    read out of the config ends up inside the address that gets fetched — for every component, and
    through the one base URL and nothing else.
    """
    run_the_python_install(fixture_repo)

    assert apply_commands(fake_binaries) == [
        ["kubectl", "apply", "-f", PIPELINE_RELEASE],
        ["kubectl", "apply", "-n", NAMESPACE, "-f", DASHBOARD_RELEASE],
        ["kubectl", "apply", "-n", NAMESPACE, "-f", TRIGGERS_RELEASE],
        ["kubectl", "apply", "-n", NAMESPACE, "-f", TRIGGERS_INTERCEPTORS],
    ], "the URLs kubementat fetches are not what its pins mean"
    for url in [call[-1] for call in apply_commands(fake_binaries)]:
        assert "storage.googleapis.com/tekton-releases" not in url, \
            "that bucket serves only what it kept, and the pins have moved past it"
