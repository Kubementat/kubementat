"""The helm addressing rule, held by the tree instead of by a review habit.

Cluster operations now run over two channels. One is the Python client, which is bound to a
`ClusterTarget` and cannot be asked a question about the wrong cluster by construction. The other
is the `helm` and `helmfile` binaries, and for those the cluster is an argument -- so a call that
does not get one is not a small oversight, it is half a run. **The namespace is created on cluster
A and the release is applied on cluster B**, and nothing in between says so: `kmt install` reports
success, `kmt uninstall` reports residue, and the pair is invisible until somebody asks why the
data is not where the release is. The helm guard is the same sentence the parity record wrote for `kubectl`, in
helm's flag spellings, and this guard is what makes it a test failure rather than a production
surprise.

The rule is argv and never the environment, and that half is not stylistic. `helm.py` still
writes Grafana's credentials into `os.environ` for helmfile's template, so the environment route is
right there and inviting -- and it is the same disease `load_kube_config` had: process-wide state
that makes two clusters in one process impossible, which is what the library contract leaves a door for.
Argv is also what makes the choice visible in the log the operator reads, which an environment
variable never is.

The guard is a **ratchet**, the way `test_no_kubectl_is_addressed_by_the_process_any_more.py` is:
the table of local-only calls is checked against the tree in both directions, so a new
cluster-touching helm call fails, a new local-only call that is not declared fails, and a declared
exemption whose call has gone away fails too. A rule that only complained about additions would
still be passing after the call it was written for had been deleted.
"""

import ast
import shutil
from pathlib import Path

import pytest

# One table for two questions: this guard checks the tree against it, and `cluster_parity` reduces
# every helm call with the same question -- "did this ask a cluster?" -- so a second copy of the
# exemption list is a second answer.
from cluster_parity import LOCAL_ONLY_HELM_CALLS

REPO_ROOT = Path(__file__).resolve().parent.parent

# helm's two spellings of "which cluster". They are not kubectl's: helm and helmfile reject
# `--context`, so a guard that reused kubectl's table would pass a call that cannot work. A splat of
# `helm_target_args` counts as present, because that is exactly what it is.
ADDRESSING_FLAGS = ("--kubeconfig", "--kube-context")

# The binaries whose arguments a cluster is found by. A bare program name in a dependency list is
# not a command, which is why `is_helm_argv` requires a second word.
CLUSTER_BINARIES = ("helm", "helmfile")

# What the rule lets go unaddressed, with the reason. Every entry is a read of *this machine*: helm's
# repository index, its chart cache, its own version. None of them can be answered about the wrong
# cluster, because none of them is answered about a cluster at all.
#
# `helm show crds` is the one that looks like a cluster call, because its output is piped into
# `kubectl apply` -- but the read itself only unpacks the chart, and the write it feeds is addressed
# where the write happens. It is the entry most likely to be argued with, so it is the one the
# ratchet below checks is still actually being made.
#
# The table lives in `cluster_parity` and not here, because the reducer has the same question to ask
# of the same argv: "did this ask a cluster?" Two answers to it is the disease this tree keeps
# naming, and the parity record needs the answer for every helm call it reduces. What stays here is the
# ratchet, which checks the tree against it in both directions.

# The one read that assembles itself. `helm_list_releases_argv` takes a target by signature, so its
# address is invisible in the argv unless the guard resolves it; resolving it through the constant
# it is built from is what lets the same check say "addressed" for it and "not addressed" for the
# same words read bare.
ADDRESSING_BUILDERS = {"helm_list_releases_argv": "HELM_LIST_RELEASES_ARGV"}

# Names that put the flags on the command line rather than spelling them here. Their refusal is
# tested below, so a splat is evidence and not a guess.
ADDRESSING_HELPERS = {"helm_target_args"}

# An argv that is *reported* rather than run. A `CommandResult` carries the command line it is
# describing, so reading those as invocations checks the log line twice and finds nothing.
REPORT_ONLY_CALLS = {"CommandResult", "CommandExecutionError", "CommandStatus", "CompletedProcess"}

# The two kinds of unknown in an argv. A splat the guard does not recognise could be carrying the
# cluster; a bare expression is a value being passed *to* the cluster and tells nothing either way.
MAYBE_ADDRESSED = object()
DYNAMIC = object()


def is_helm_argv(node, reported):
    """A `["helm", ...]` or `["helmfile", ...]` list that is a command and not a program name."""
    if not isinstance(node, ast.List) or not node.elts or id(node) in reported:
        return False
    first = node.elts[0]
    return isinstance(first, ast.Constant) and first.value in CLUSTER_BINARIES


def reported_argvs(tree):
    """Node ids of argv lists that are reported inside an object rather than executed."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in REPORT_ONLY_CALLS:
                ids.update(id(child) for child in ast.walk(node))
    return ids


def run_reads(tree):
    """Every name a command run is handed, so a constant's *use* is found and not its definition."""
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if called not in {"run", "run_command"}:
            continue
        ids.update(id(child) for child in ast.walk(node))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and id(node) in ids}


def module_constants(root):
    """`{name: words}` for the module-level helm argv constants, spelled once anywhere in src."""
    constants = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            name = getattr(target, "id", None)
            if name and isinstance(node.value, ast.List) and node.value.elts:
                words = [element.value for element in node.value.elts
                         if isinstance(element, ast.Constant)]
                if words:
                    constants.setdefault(name, words)
    return constants


def words_of(node, constants):
    """The argv's words as far as they are known, with two kinds of unknown.

    A splat is not a word and neither is a variable, but they fail in different directions: a splat
    of something the guard does not recognise *may* be carrying the cluster (so the guard has to
    give it the benefit of the doubt, which is what a mutation test then takes away), while a bare
    variable is a name the argv was built from and says nothing about the address either way.
    Collapsing the two would let `helm uninstall $release` read as addressed.
    """
    words = []
    for element in node.elts:
        if isinstance(element, ast.Constant):
            words.append(element.value)
            continue
        inner = element.value if isinstance(element, ast.Starred) else element
        called = (getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
                  if isinstance(inner, ast.Call) else None)
        if isinstance(inner, ast.Starred) or isinstance(element, ast.Starred):
            if called in ADDRESSING_HELPERS:
                words.append(ADDRESSING_FLAGS[1])
            elif called in ADDRESSING_BUILDERS:
                words += constants.get(ADDRESSING_BUILDERS[called], [])
            else:
                words.append(MAYBE_ADDRESSED)
            continue
        words.append(DYNAMIC)
    return words


def every_helm_argv(root=None):
    """Every helm/helmfile argv the tree can start: literal, constant read bare, or built.

    Three shapes, because all three hide. A literal at the call site is the obvious one; a
    module-level constant handed straight to `shell.run` is the one a grep-shaped guard misses, and
    the one the rule forbids without a builder to blame; and a builder call is the one where the address
    arrives by signature and is therefore easy to forget to pass.
    """
    root = REPO_ROOT / "src" if root is None else root
    constants = module_constants(root)
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        reported = reported_argvs(tree)
        # A module-level constant's own value is a definition, not a call; the constant pass below
        # decides whether it is being *run*, which is the only question the guard has.
        definitions = {id(node.value) for node in ast.walk(tree)
                       if isinstance(node, (ast.Assign, ast.AnnAssign))
                       and isinstance(node.value, ast.List)}
        for node in ast.walk(tree):
            if is_helm_argv(node, reported) and id(node) not in definitions:
                yield relative(path, root), node.lineno, words_of(node, constants)
        read = run_reads(tree)
        written_here = {node.targets[0].id for node in ast.walk(tree)
                        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                name = getattr(target, "id", None)
                if (name in read and isinstance(node.value, ast.List)
                        and is_helm_argv(node.value, reported)):
                    yield relative(path, root), node.lineno, words_of(node.value, constants)
                continue
            # A constant read here but declared elsewhere is the same command, and the only way to
            # see it: `automation.py` hands `HELM_LIST_RELEASES_ARGV` to `shell.run` without owning
            # it, and a guard that only read assignments would call that read nothing at all.
            if isinstance(node, ast.Name) and node.id in read and node.id in constants \
                    and node.id not in written_here:
                yield relative(path, root), node.lineno, list(constants[node.id])


def relative(path, root):
    return str(path.relative_to(root))


def asked_words(words):
    """What a command asked for, with the address left out.

    `helm list --all-namespaces --deployed ... --kube-context dev` and the same read without the
    flags are one read of one thing, so the exemption table has to be able to name it once and not
    once per way of addressing it. Only the two cluster flags are removed, values included: any
    other flag is part of the question (`--all-namespaces` is, and `--version` is the whole of one
    local read).
    """
    kept, skipping = [], False
    for word in words:
        if word in (MAYBE_ADDRESSED, DYNAMIC):
            continue
        if skipping:
            skipping = False
            continue
        if not isinstance(word, str):
            continue
        if word.startswith("-"):
            if word in ADDRESSING_FLAGS:
                skipping = True
            else:
                kept.append(word)
            continue
        kept.append(word)
    return kept


def is_exempt(words):
    """Whether this argv is one of the declared local-only calls.

    A declaration matches the words that name the operation and nothing else. `helm uninstall` is
    not declared, so `helm uninstall polaris --namespace ns` is not exempt; `helm repo update` is
    declared, and nothing else in the tree starts that way, so the exemption cannot spread.
    """
    asked = asked_words(words)
    return any(asked[:len(declared)] == list(declared) for declared in LOCAL_ONLY_HELM_CALLS)


def addressed(words):
    """True when the address is on the command line, or arrives by a splat that puts it there."""
    return any(word is MAYBE_ADDRESSED or (isinstance(word, str) and word in ADDRESSING_FLAGS)
               for word in words)


def offenders(root=None):
    """The helm calls that carry no cluster, as `file:line argv` strings."""
    found = []
    for name, line, words in every_helm_argv(root):
        if is_exempt(words) or addressed(words):
            continue
        found.append(f"{name}:{line} {[w for w in words if isinstance(w, str)]}")
    return found


def test_every_helm_call_in_src_carries_its_cluster():
    """The guard that walks every module in the tree.

    A call whose flags come from `helm_target_args` is checked where that function refuses, which
    is tested below -- so the chain is closed either way. What is not allowed is a call that carries
    nothing, because that is the call that answers about a cluster nobody chose.
    """
    assert offenders() == [], (
        "a `helm`/`helmfile` call in src/ was found with no --kube-context/--kubeconfig in its argv "
        "and no splat that could supply them. The rule is argv and not the environment: an env var "
        "is process-wide state, which is how one cluster quietly answers for another.")


def test_the_local_only_helm_calls_are_the_ones_the_tree_makes():
    """Not just *that* they carry no cluster, but *why they are still there*.

    A `helm repo list` that came back would pass the guard above for as long as someone believed it
    was local-only, and fail this one. Every declared exemption must be found in the tree, and
    anything the tree makes that is not declared fails too, so the table is the whole list and not
    a list of what someone remembered.
    """
    found = {declared: 0 for declared in LOCAL_ONLY_HELM_CALLS}
    strays = []
    for name, line, words in every_helm_argv():
        if addressed(words):
            continue

        asked = asked_words(words)
        matched = [declared for declared in LOCAL_ONLY_HELM_CALLS
                   if asked[:len(declared)] == list(declared)]
        if not matched:
            strays.append(f"{name}:{line} {list(asked)}")
            continue
        found[max(matched, key=len)] += 1

    assert strays == [], (
        "a helm call was found that asks nothing of a cluster but is not in LOCAL_ONLY_HELM_CALLS. "
        "Either it is a cluster call carrying no address, which is the failure this guard exists "
        "for, or the exemption is real and undeclared: " + "; ".join(strays))

    missing = ["/".join(declared) for declared, count in found.items() if count == 0]
    assert missing == [], (
        f"LOCAL_ONLY_HELM_CALLS declares calls the tree no longer makes: {missing}. A table that "
        "keeps an exemption for a call that has gone lets the next one in through the same door.")


def test_the_guard_catches_a_call_site_that_thinks_the_default_is_enough(tmp_path):
    """The mutation, run against the tree and not described to it.

    One line of a real call site is rewritten in a copy of `src/` -- here the `helmfile apply`,
    where dropping the cluster is invisible until the wrong cluster is noticed -- and the same
    check the tree is checked with is run over the copy. It must say so. A guard that has never been
    watched to fail has not been tested, which is why `test_no_cli_command_starts_a_script_by_path.py`
    does the same thing for its own rule.
    """
    copied = _src_copy(tmp_path)
    assert offenders(copied) == [], "the copy was already offending, so the mutation proves nothing"

    _swap(copied / "kubementat/infrastructure/helm.py",
          '["helmfile", "apply", *helm_target_args(self.target), "--color"',
          '["helmfile", "apply", "--color"')

    caught = offenders(copied)
    assert len(caught) == 1, (
        "removing the cluster from one helm call site was not caught, so the guard checks the tree "
        f"as decoration: {caught}")
    assert "helm.py" in caught[0] and "helmfile" in caught[0], caught


def test_the_guard_catches_a_release_read_that_dropped_its_builder(tmp_path):
    """The same mutation on the read four call sites share, because it hides there differently.

    `helm_list_releases_argv(self.target)` becomes the bare constant. Everything still *runs*, the
    statuses are still asked for, and the answer comes from whichever kubeconfig the process
    happens to have -- which is why this spelling, not `helmfile apply`, is the one the guard
    resolves through the constant instead of trusting the call.
    """
    copied = _src_copy(tmp_path)
    _swap(copied / "kubementat/automations/uninstall/residue.py",
          "shell.run(helm_list_releases_argv(kubernetes_utils.target), capture=True)",
          "shell.run(HELM_LIST_RELEASES_ARGV, capture=True)")

    caught = offenders(copied)
    assert any("residue.py" in one and "list" in one for one in caught), (
        "the shared release read went unaddressed and the guard said nothing: " + str(caught))


def test_helm_target_args_refuses_a_target_that_cannot_be_addressed():
    """The refusal the splat depends on, named.

    A target built from a connection document has neither flag, so a helm call handed one has
    nothing to carry. Saying "and then we read `~/.kube/config`" would be the report's failure arriving
    from the other side: a release applied to whichever cluster the running user last looked at.
    """
    from kubementat.core.identity.target import ClusterTarget, helm_target_args

    with pytest.raises(Exception) as raised:
        helm_target_args(ClusterTarget(environment="dev", context=None, kubeconfig_path=None,
                                       address="https://example.test:6443",
                                       source="connection"))
    assert "kubeconfig" in str(raised.value).lower() or "kubecontext" in str(raised.value)

    assert helm_target_args(ClusterTarget(environment="dev", context="dev", kubeconfig_path=None,
                                          address="https://x:6443",
                                          source="static.json")) == ["--kube-context", "dev"]
    assert helm_target_args(ClusterTarget(environment="dev", context=None,
                                          kubeconfig_path="/tmp/x", address="https://x:6443",
                                          source="kubeconfig_file")) == ["--kubeconfig", "/tmp/x"]


def test_the_release_read_is_the_same_read_whether_it_is_addressed_or_not():
    """The one read four call sites share, spelled once.

    Addressing it must not change what it asks. This is the parity that matters here, not a
    comparison against bash: `HELM_LIST_RELEASES_ARGV` decides which releases count as live, and a
    call site that reached that answer with a narrower read would uninstall less than the residue
    assert then claims.
    """
    from kubementat.core.identity.target import ClusterTarget
    from kubementat.infrastructure.helm import HELM_LIST_RELEASES_ARGV, helm_list_releases_argv

    addressed_argv = helm_list_releases_argv(
        ClusterTarget(environment="dev", context="dev", kubeconfig_path=None,
                      address="https://x:6443", source="static.json"))
    assert [addressed_argv[0], addressed_argv[1]] == ["helm", "list"]
    assert "--kube-context" in addressed_argv
    assert asked_words(addressed_argv) == asked_words(HELM_LIST_RELEASES_ARGV), \
        "addressing the read changed what it asks, which is a different answer about the same cluster"

    with pytest.raises(Exception):
        helm_list_releases_argv(ClusterTarget(environment="dev", context=None,
                                              kubeconfig_path=None,
                                              address="https://x:6443", source="connection"))


def _src_copy(tmp_path):
    """`src/` copied, so a mutation cannot be mistaken for a change to the tree.

    The copy is of `src/` and not the whole repository: nothing the guard reads lives outside it,
    and a copy that dragged in `.git` would be a copy that could be modified by accident.
    """
    copied = tmp_path / "src"
    shutil.copytree(REPO_ROOT / "src", copied)
    return copied


def _swap(path, before, after):
    """One line rewritten in the copy.

    Failing loudly when the line is not there is the point: a mutation that quietly matched nothing
    is a test that passes forever without having tested anything.
    """
    source = path.read_text()
    if before not in source:
        pytest.fail(f"{path.name} no longer contains {before!r}, so this mutation is checking a "
                    "call site that does not exist. Update the mutation with the call site.")
    path.write_text(source.replace(before, after, 1))
