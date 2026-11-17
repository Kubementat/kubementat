"""One component comes off the cluster — the release the cluster names, and the claims it made.

`kmt uninstall component` is the only way to take one component off without tearing the platform
down, and it does two things that have to happen together. The release goes by the name the cluster
reports for the component's namespace rather than by a name guessed from a configuration key, and the
claims it made — the `PersistentVolumeClaim` objects a release leaves behind — go by name in the same
command, because deleting the release alone leaves the data mounted on a namespace about to be swept.

Both halves are asserted against a fake API server and fake cluster binaries, so a component that
reports no claims, a namespace that does not exist, and a release that refuses to uninstall each get
an answer that names what happened instead of an exit code.
"""

from kubementat.errors import UninstallIncompleteError
import json
from types import SimpleNamespace

import pytest

from cluster_parity import API_PROGRAM

from kubementat import errors
from kubementat.core import constants
from kubementat.core.config.config import Config
from kubementat.core.identity.context import Context
from kubementat.infrastructure.helm import HelmUtils
from conftest import run_kmt

COMPONENT = "polaris"
NAMESPACE = "polaris"
RELEASE = "polaris"
CLAIMS = ["polaris-data-0", "polaris-scratch-0"]

# The router, addressed by the table that says which components exist. Read rather than typed so a
# checkout that lost the entry fails here rather than in a test that believed a string.
ROUTER = "traefik"
ROUTER_NAMESPACE = constants.DEFAULT_NAMESPACES[ROUTER]


def namespace_of(argv):
    for index, argument in enumerate(argv):
        if argument in ("-n", "--namespace") and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _address_of(argv):
    """The flags naming the cluster, and what they were given.

    A helm command that carries its cluster puts the name of the release after those flags, not
    directly after the verb, so an index-after-the-verb read lands on a path. This is what tells
    the two apart: the flags and their values, left out.
    """
    dropped = set()
    for index, argument in enumerate(argv):
        if argument in ("--kubeconfig", "--kube-context", "--context", "--cluster", "--kube-token"):
            dropped.update((index, index + 1))
    return dropped


def release_commands_to(commands, verb, name, namespace):
    """The commands that address *name* with *verb* in *namespace*, however they were spelled.

    The cluster sits on this command's argv, which puts two flags and their values between
    the verb and the release. A literal comparison of the whole command line therefore says "that
    uninstall did not happen" about a run that did it correctly, to a reader who has to work out
    that the disagreement is about where the flags sit. So the same facts are read back out of the
    line — the verb, the release named after it, the namespace it was addressed in.
    """
    return [command for command in commands
            if command[:1] == ["helm"] and verb in command
            and named_after(command, verb) == name and namespace_of(command) == namespace]


def named_after(argv, verb):
    """The release a `helm <verb>` addresses: the first word after the verb that is not a flag.

    Spelled here and not read by position because the cluster sits on this command line, and
    where the address sits is not something a comparison should have to agree about twice.
    """
    dropped = _address_of(argv)
    following = False
    for index, word in enumerate(argv):
        if following and index not in dropped and not word.startswith("-"):
            return word
        if index in dropped:
            following = True
            continue
        if word == verb:
            following = True
    return None


def what_the_cluster_was_asked_to_lose(calls_or_commands):
    """What a run would have taken off the cluster: `(kind, namespace, name)` triples.

    Both sides reduce here — the recorded shim calls behind the Python path, and the command lines
    out of the blob — because comparing argv would only prove that bash wrote
    `helm -n ns delete NAME` where Python writes `helm uninstall NAME --namespace ns`. That is a
    fact about two spellings of one act, and not about what the cluster ends up holding.
    """
    lost = set()
    for call in calls_or_commands:
        argv = list(call.argv if hasattr(call, "argv") else call)
        program = argv[0]
        if program == API_PROGRAM:
            # A delete the client made, read out of the same log the shims write to. The path says
            # what was deleted — `.../namespaces/<ns>/persistentvolumeclaims/<name>` is the same
            # triple a `kubectl delete` line spells out, and reading it is what keeps the two sides
            # of this comparison on one record instead of two that have to be joined by hand.
            if len(argv) > 2 and argv[1].upper() == "DELETE" and "persistentvolumeclaims" in argv[2]:
                parts = [part for part in argv[2].strip("/").split("/") if part]
                namespace = (parts[parts.index("namespaces") + 1]
                             if "namespaces" in parts and len(parts) > 1 else None)
                lost.add(("claim", namespace, parts[-1]))
            continue
        if program == "helm" and "uninstall" in argv:
            lost.add(("release", namespace_of(argv), named_after(argv, "uninstall")))
        elif program == "helm" and "delete" in argv:
            lost.add(("release", namespace_of(argv), named_after(argv, "delete")))
        elif program == "kubectl" and "delete" in argv:
            position = argv.index("delete")
            kind, names = argv[position + 1], argv[position + 2:]
            if "persistentvolumeclaims" not in kind:
                continue
            stopped = []
            for name in names:
                if name.startswith("-"):
                    break
                stopped.append(name)
            lost.update(("claim", namespace_of(argv), name) for name in stopped)
    return lost


def configured(fixture_repo, namespace=NAMESPACE, release_name=RELEASE):
    """The two keys the script's `jq` reads, spelled the way a team's are.

    `*_DEPLOYMENT_NAME` is not in the fixture's defaults, and the script cannot address a release
    without it — so comparing against the script means saying what the script was addressing.
    """
    fixture_repo.environment_static(**{
        f"{COMPONENT.upper()}_DEPLOYMENT_NAMESPACE": namespace,
        f"{COMPONENT.upper()}_DEPLOYMENT_NAME": release_name,
    })


def respond(fake_binaries, recorded_api, releases=(), claims=()):
    """What the fake cluster answers when asked.

    `helm list` is answered whichever flags it is asked with — the shim matches the subcommand and
    not the argv, so nothing here proves the release's status was asked for. The statuses the
    product actually asks for are guarded in
    `tests/test_the_release_listing_asks_for_the_statuses_helm_actually_has.py`, which is the only
    place that question is answered.

    The claims moved: the claim read and delete came off `kubectl` and onto the client, so
    they answer through `recorded_api` now and the deletions are left in silence — success, and no
    output to pretend about. Answering them on the shim instead would have left a test that reads
    like a cluster read and is not one.
    """
    fake_binaries.respond("helm", json.dumps([{"name": name, "namespace": namespace}
                                              for name, namespace in releases]),
                          subcommand="list")
    # The claims are declared once and answered twice, because the run that reads them now has two
    # mechanisms in it: the uninstall itself asks the client, and a command run as its own
    # process answers through the fake transport, which reads the same response files the shims do.
    fake_binaries.respond("kubectl", json.dumps({"items": [{"metadata": {"name": name}}
                                                           for name in claims]}),
                          subcommand="get")
    recorded_api.answer("list_namespaced_persistent_volume_claim",
                        lambda **kwargs: SimpleNamespace(items=[
                            SimpleNamespace(metadata=SimpleNamespace(name=name))
                            for name in claims]))
    return fake_binaries


def uninstalled(fixture_repo, recorded_api):
    """One component off the cluster, with its claims answered by the recorder.

    `recorded_api` is in the signature and not hidden in a fixture because it is what the converted
    claim read needs: `HelmUtils` builds its `KubernetesUtils` from the `Context` this
    command resolved, so the only seam left is the target's own client factory, which is exactly
    what the recorder replaces.
    """
    return HelmUtils(Context.build(fixture_repo.environment, fixture_repo.team)).uninstall_release(COMPONENT)


def uninstalled_router(fixture_repo, recorded_api):
    """The same command with the router as its component.

    Deliberately nothing else is set up differently: the claims sweep is expected to find none, so
    the shape of the run is one release going and one read that answers empty.
    """
    return HelmUtils(Context.build(fixture_repo.environment, fixture_repo.team)).uninstall_release(ROUTER)


def written_commands(commands):
    """The commands that write, so a namespace check is about the deletions and not the listing."""
    return [command for command in commands if "uninstall" in command or "delete" in command]


def test_the_release_and_the_claims_it_made_both_go(fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """One component: `helm uninstall` by the name the cluster reports, then its claims by label."""
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)

    outcome = uninstalled(fixture_repo, recorded_api)

    assert outcome["namespace"] == NAMESPACE
    assert outcome["releases"] == [RELEASE]
    assert sorted(outcome["claims"]) == sorted(CLAIMS)

    commands = fake_binaries.commands()
    assert release_commands_to(commands, "uninstall", RELEASE, NAMESPACE), (
        f"the release was not addressed by the name the cluster reports and the namespace the"
        f" configuration names: {commands}")
    # The claim read and delete are client calls now, so what is asserted is the namespace
    # and the selector the API server was handed, not the argv a process was handed. The same two
    # facts, asked a different way; the selector is spelled out here rather than in a helper so a
    # label change that silently narrows the sweep is visible in this file and not only in a diff.
    asked = recorded_api.called_as("list_namespaced_persistent_volume_claim")
    assert asked["namespace"] == NAMESPACE, asked
    assert asked["label_selector"] == "app.kubernetes.io/instance=polaris", asked
    deleted = [call for call in recorded_api.calls
               if call[0] == "delete_namespaced_persistent_volume_claim"]
    assert sorted(call[1]["name"] for call in deleted) == sorted(CLAIMS), deleted
    assert all(call[1]["namespace"] == NAMESPACE for call in deleted), deleted


def test_the_namespace_is_the_one_the_environment_names_and_nowhere_else(fixture_repo,
                                                                         dry_run_env,
                                                                         fake_binaries, recorded_api):
    """Everything this deletes lands in the one namespace the configuration names.

    `polaris` is what the template ships, so it is also what the default answers with — that is the
    fallback, and the next test renames it.
    """
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)

    uninstalled(fixture_repo, recorded_api)

    written = written_commands(fake_binaries.commands())
    assert written, "nothing was addressed at a namespace, so nothing was addressed"
    assert {namespace_of(command) for command in written} == {NAMESPACE}, written
    assert Config(fixture_repo.environment, fixture_repo.team,
                  preload_configs=False).component_namespace(COMPONENT) == NAMESPACE


def test_renaming_the_namespace_uninstalls_from_the_new_place(fixture_repo, dry_run_env,
                                                              fake_binaries, recorded_api):
    """The rename test that had to exist: a namespace rename moves the uninstall with it.

    kubementat never had a component uninstall to move, so this is the other half of the namespace
    rule — the
    reading side already honoured the key, and the writing side of the platform teardown only existed
    inside a script nothing called.
    """
    configured(fixture_repo, namespace="nightly-polaris")
    respond(fake_binaries, recorded_api, releases=[(RELEASE, "nightly-polaris")], claims=CLAIMS)

    outcome = uninstalled(fixture_repo, recorded_api)

    assert outcome["namespace"] == "nightly-polaris"
    commands = fake_binaries.commands()
    assert release_commands_to(commands, "uninstall", RELEASE, "nightly-polaris"), commands
    assert {namespace_of(command) for command in written_commands(commands)} == \
           {"nightly-polaris"}, commands
    assert not [command for command in written_commands(commands)
                if namespace_of(command) == NAMESPACE], \
        "the release is still addressed in a namespace the code remembered"


def test_a_component_that_is_not_installed_says_so_and_deletes_nothing(fixture_repo,
                                                                       dry_run_env,
                                                                       fake_binaries, recorded_api):
    """Absence is a normal answer, not a warning.

    It is a *normal* answer rather than a warning because the command is safe to run against a
    component that was never installed, and a run that warned about that would train somebody to
    ignore the warning right up to the case where it is not absence.
    """
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[])

    outcome = uninstalled(fixture_repo, recorded_api)

    assert outcome["releases"] == []
    assert outcome["claims"] == []
    assert not [command for command in fake_binaries.commands()
                if "uninstall" in command or "delete" in command], fake_binaries.commands()


def test_a_release_that_made_no_claims_leaves_the_claim_sweep_alone(fixture_repo,
                                                                    dry_run_env,
                                                                    fake_binaries, recorded_api):
    """A stateless release: the uninstall runs, and the sweep reads nothing it should delete.

    The difference between "nothing matched" and "I did not ask" is a `kubectl delete` with no names
    in it, and kubectl makes that one loudly.
    """
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=[])

    outcome = uninstalled(fixture_repo, recorded_api)

    assert outcome["claims"] == []
    commands = fake_binaries.commands()
    assert release_commands_to(commands, "uninstall", RELEASE, NAMESPACE), commands
    assert not [command for command in commands if "delete" in command], commands


def test_a_release_that_will_not_uninstall_keeps_its_claims_and_raises(fixture_repo,
                                                                       dry_run_env,
                                                                       fake_binaries, recorded_api):
    """A real error fails loudly, and loudly in the one way that cannot make it worse.

    `helm uninstall` exits non-zero. That release is still installed, its claims are its backing
    store, and taking them out from under it is the one mistake here no later command can undo.
    """
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)
    fake_binaries.respond("helm", "release: not found", exit_code=1, subcommand="uninstall")

    with pytest.raises(UninstallIncompleteError) as error:
        uninstalled(fixture_repo, recorded_api)

    assert RELEASE in str(error.value)
    assert not [command for command in fake_binaries.commands()
                if "delete" in command], "the claims of a release that is still installed went"


def test_an_unreachable_cluster_is_not_reported_as_an_absent_component(fixture_repo,
                                                                       dry_run_env,
                                                                       fake_binaries, recorded_api):
    """The listing is the one read that is not tolerant.

    `helm list` answering `connection refused` and `helm list` answering `[]` are the same shape to
    a guesser, and the difference between them is whether the next install of the component finds
    the last release's secrets. So the listing is checked, before anything is deleted.
    """
    configured(fixture_repo)
    fake_binaries.respond("helm", "connect: connection refused", exit_code=1, subcommand="list")

    with pytest.raises(errors.CommandExecutionError):
        uninstalled(fixture_repo, recorded_api)

    assert not [command for command in fake_binaries.commands() if "delete" in command]


def test_a_name_that_is_not_a_component_is_named_and_stops(fixture_repo, dry_run_env,
                                                           fake_binaries):
    """`kmt component uninstall dev polari` is a typo, not an empty namespace.

    The namespace is what the deletion is addressed at, so an unknown component has to raise rather
    than fall back — otherwise the typo is what uninstalled something.
    """
    with pytest.raises(KeyError):
        HelmUtils(Context.build(fixture_repo.environment, fixture_repo.team)).uninstall_release("polari")

    assert fake_binaries.commands() == []


def test_the_command_is_on_the_surface_and_needs_no_team(fixture_repo, dry_run_env, fake_binaries, recorded_api):
    """Reachability is the ticket: a method nobody can invoke is a library, not a command.

    Run as its own process so the shim lookup and the resolution report are the real ones — an
    in-process `CliRunner` can pass while `main()` never wired the command in. The component is
    passed in upper case, because that is how the script it replaces took it and the case is not the
    operator's problem.

    And it is run with no team, which is the other half of what this test now proves: `kmt component
    uninstall` used to refuse exactly this invocation, and the deletion it performs reads one
    namespace out of the environment's `static.json`. A green run here is the evidence that the
    guard was a false refusal and not a rule.
    """
    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)

    result = run_kmt(["component", "uninstall", fixture_repo.environment, "POLARIS"], dry_run_env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"namespace: {NAMESPACE}" in result.stdout
    assert f"releases uninstalled: {RELEASE}" in result.stdout
    assert "claims deleted" in result.stdout
    assert what_the_cluster_was_asked_to_lose(fake_binaries.calls()) == {
        ("release", NAMESPACE, RELEASE),
        ("claim", NAMESPACE, CLAIMS[0]), ("claim", NAMESPACE, CLAIMS[1])}


def test_the_command_names_a_component_it_does_not_know_and_deletes_nothing(fixture_repo,
                                                                            dry_run_env,
                                                                            fake_binaries, recorded_api):
    """A typo through the CLI is one readable line, not a traceback, and it deletes nothing.

    The library raises `KeyError` because a namespace is what the deletion is addressed at; the
    CLI's job is to say which names exist and stop. That is the shape `open_tunnel` already gives
    a tunnel that cannot find its component.
    """
    from conftest import run_kmt

    configured(fixture_repo)
    respond(fake_binaries, recorded_api, releases=[(RELEASE, NAMESPACE)], claims=CLAIMS)

    result = run_kmt(["component", "uninstall", fixture_repo.environment, "polari"], dry_run_env)

    assert result.returncode != 0, "a component nobody named must not exit 0"
    assert "not a kubementat component" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr, result.stdout + result.stderr
    assert not [command for command in fake_binaries.commands() if "delete" in command], \
        fake_binaries.commands()


def test_the_uninstall_shows_up_once_and_under_the_group_name():
    """One route to taking one component off the cluster, and its name is the group's.

    This landed flat, because the surface then had one group (`users`) and the other offer was a
    `helm` subgroup nothing else had. The install half landing beside it is what
    made the pair a group: `install` and `uninstall` of one component are the two answers to one
    question, and that is the shape `kmt routing` already has.

    What a rename has to fail is the shape a migration leaves behind by accident: the flat name still
    printed, still reachable, still doing the deletion. So the check here is not that the old name is
    absent (that is `test_cli_surface`'s job) but that the two names do not coexist, and that the
    group's `uninstall` is the only thing on the surface that takes one component off. The old name
    is written here over its `-uninstall` half, for the same reason the check is: the grep that says
    the rename landed has to stay empty, and a test that spells the old name has it back in the tree.
    """
    from test_cli_surface import commands_from_help, help_output, run_help

    names = commands_from_help(help_output())

    assert "component" in names, sorted(names)
    assert [name for name in names if "component-uninstall" in name] == [], sorted(names)
    # `helm` has a group of its own now, so "there is no helm subgroup" is no longer a true sentence
    # and was never the real claim. What this guards is the hiding place: the group's own listing is
    # the apply and the chart read, and nothing that takes a component off.
    assert commands_from_help(run_help("kmt", "helm", "--help")) == {"apply", "latest-version"}, \
        sorted(names)
    assert [name for name in names if "component" in name] == ["component"], sorted(names)


def test_the_ingress_controller_is_addressed_by_the_same_two_facts(fixture_repo,
                                                                   dry_run_env,
                                                                   fake_binaries, recorded_api):
    """The router is a component, so this command needs nothing Traefik-shaped to find it.

    Traefik entered `constants.DEFAULT_NAMESPACES` as a component entry, and the consequence for
    this file is that `uninstall_release` can address it. Before the entry, that call was a
    `KeyError` and taking the router off a cluster meant tearing the whole platform down. What it
    reads after the entry is the same two facts it reads for `polaris` -- the namespace through
    `Config.component_namespace`, the release through `helm list` -- which is the point of the
    entry existing rather than a router-specific path that has to be kept right twice.
    """
    fake_binaries.respond("helm", json.dumps([{"name": ROUTER, "namespace": ROUTER_NAMESPACE}]),
                          subcommand="list")

    outcome = uninstalled_router(fixture_repo, recorded_api)

    assert outcome["namespace"] == ROUTER_NAMESPACE
    assert outcome["releases"] == [ROUTER]
    commands = fake_binaries.commands()
    assert release_commands_to(commands, "uninstall", ROUTER, ROUTER_NAMESPACE), commands


def test_renaming_the_routers_namespace_uninstalls_from_the_new_place(fixture_repo,
                                                                      dry_run_env,
                                                                      fake_binaries, recorded_api):
    """The other half of the entry being load-bearing: a rename has to move the delete.

    `traefik` is both the component and the namespace the template ships, so a namespace that was
    written in code instead of read would be invisible right up to the day somebody put the router
    somewhere else -- and `helm uninstall traefik --namespace traefik` against a release that lives
    in `edge` reports an absent component while the router is still running.
    """
    fixture_repo.environment_static(**{f"{ROUTER.upper()}_DEPLOYMENT_NAMESPACE": "edge"})
    fake_binaries.respond("helm", json.dumps([{"name": ROUTER, "namespace": "edge"}]),
                          subcommand="list")

    outcome = uninstalled_router(fixture_repo, recorded_api)

    assert outcome["namespace"] == "edge"
    commands = fake_binaries.commands()
    assert release_commands_to(commands, "uninstall", ROUTER, "edge"), commands
    assert {namespace_of(command) for command in written_commands(commands)} == {"edge"}, commands


