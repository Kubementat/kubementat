"""The rule that no `kubectl` call may guess its cluster, held by the tree instead of by a
review comment.

The design's negative claim is short enough to write on one line: *a `kubectl` invocation that is
not given its cluster is a bug, not a shortcut*. Three things in this checkout had been that bug —
`kubectl logs` and `port-forward` inheriting whatever the process's kubeconfig called current, and
`kubectl config view --minify` following `current-context` while the command had resolved a
different environment — and the conversions to the client removed most of the other call sites. What
is left is short and deliberate, so the rule is stated once, here, in one guard.

The guard is a **ratchet**, not a census. A table of the call sites that are allowed to exist is
checked against the tree in both directions: a new unaddressed `kubectl` fails, and so does an
entry in the table whose call site has been converted. A rule that only complained about additions
would keep working after the conversions it was written for and tell nobody that it had gone stale.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

ADDRESSING_FLAGS = ("--context", "--kubeconfig", "--cluster")

# What is left alive deliberately, each with its reason. A session call streams, owns
# a terminal, or reads a process's stdin; a manifest apply splits a multi-document stream that the
# client cannot do by name.
SESSION_VERBS = {"logs", "attach", "exec", "port-forward", "cp", "debug", "run", "wait"}
MANIFEST_VERBS = {"apply", "delete", "patch", "label", "annotate", "rollout", "scale",
                  "create", "replace"}
# A kubeconfig read, which no client method answers: `kubectl config view --flatten --minify`
# resolves exec plugins, file-backed authorities and external clusters into one self-contained
# document. It is still addressed, so what it minifies is the cluster the command named and not
# whatever `current-context` was holding.
KUBECONFIG_VERBS = {"config"}

# Reads that belong on the client and that are not there yet. This is the list of known work,
# not a permission: each entry names the module, the verb and the read, and an entry disappears
# when the read does. `get_pod_status` was already converted; these three are the rest.
PENDING_KUBECTL_READS = {
    "kubementat/kubernetes_utils.py": 0,
    "kubementat/ci_utils.py": 0,
    "kubementat/automation.py": 0,
    "kubementat/helm_utils.py": 0,
    "kubementat/user_management.py": 0,
    "kubementat/grafana_accounts.py": 0,
    "kubementat/tekton_utils.py": 0,
    # The routing lane was the last module carrying debt here, and it is gone: its three discovery
    # reads used to go through a documented `reader=` seam whose records were argv lists, which is
    # how `ProbeOutcome.argv` stayed the command line a status report prints. That seam still exists,
    # but it now answers a *read* and not a command, because `ProbeOutcome` had to say which channel
    # disagreed -- a binary that exited 1 and a cluster that returned 404 are not the same fact, and
    # printing one as the other is the sentence the addressing rule exists to keep the code from
    # writing.
    "kubementat/automations/routing/routing.py": 0,
    "kubementat/automations/routing/traefik_presence.py": 0,
}

# Words that make a `["kubectl", ...]` list not an argv. `KUBEMENTAT_DEPENDENCIES` starts with the
# word and is a list of programs to check for, not a command to run.
NOT_A_KUBECTL_VERB = {"helm", "helmfile", "git", "docker", "gpg", "python3", "pip", "tkn", "bash",
                      "sh", "kubectl"}

# Verbs that belong on the generated client, so that a table entry can exempt them from the
# addressing rule. A `get` in a listed module is the conversion debt; a `get` anywhere else is a
# call that should not exist at all, which the second test says.
CONVERTED_READ_VERBS = {"get", "describe", "version", "cluster-info", "api-resources", "auth"}

# An argv that is *reported* rather than run. A `CommandResult` carries the command line it is
# describing, so reading those as invocations checks the log line twice and finds nothing.
REPORT_ONLY_CALLS = {"CommandResult", "CommandExecutionError", "CommandStatus", "CompletedProcess"}


def python_files():
    return sorted((REPO_ROOT / "src").rglob("*.py"))


def relative(path):
    return str(path.relative_to(REPO_ROOT / "src"))


def reported_argvs(tree):
    """Node ids of argv lists that are reported inside an object rather than executed."""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in REPORT_ONLY_CALLS:
                ids.update(id(child) for child in ast.walk(node))
    return ids


def is_kubectl_argv(node, reported):
    """A `["kubectl", ...]` list, and not a list of program names that happens to start with one."""
    if not isinstance(node, ast.List) or not node.elts or id(node) in reported:
        return False
    first = node.elts[0]
    if not isinstance(first, ast.Constant) or first.value != "kubectl":
        return False
    words = [element.value for element in node.elts[1:]
             if isinstance(element, ast.Constant) and isinstance(element.value, str)]
    return not (words and words[0] in NOT_A_KUBECTL_VERB)


def kubectl_argv_nodes(tree):
    """Every kubectl argv a module can run, wherever it was spelled.

    Two sources, because both hide. The literal passed straight to `shell.run` is the obvious one;
    the module-level `*_ARGV` constant a probe helper hands to its own `reads=` seam is the one a
    guard written from a grep would miss, and this checkout has several precisely because more than
    one call site reads the same command.
    """
    reported = reported_argvs(tree)
    found = []

    for node in ast.walk(tree):
        if is_kubectl_argv(node, reported):
            found.append(node)

    # The named constants. Checked only where a command run reads them, because that is the point
    # the argv becomes a command: a constant assembled with a splat at its call site is addressed by
    # construction, and the same constant spelled on its own line is only a definition.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        name = getattr(target, "id", None)
        value = node.value
        if not name or not isinstance(value, ast.List) or not is_kubectl_argv(value, reported):
            continue
        if not any(isinstance(read, ast.Name) and read.id == name for read in run_names(tree)):
            continue
        if not any(value.lineno == existing.lineno for existing in found):
            found.append(value)

    return found


def run_names(tree):
    """Every name that appears inside an argument to a command run, so a constant's *use* is found."""
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
        if called not in {"run", "run_command"}:
            continue
        ids.update(id(child) for child in ast.walk(node))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Name) and id(node) in ids]


def literal_words(node):
    """The constant words of an argv, with a splat standing in for the word `True`.

    `True` is what `*self.kubectl_args` reads as, and the guard treats it as "the flags may be
    here" because that is exactly what it is: the arguments come from `kubectl_target_args`, whose
    own refusal is tested below rather than assumed.
    """
    return [element.value if isinstance(element, ast.Constant) else True
            for element in node.elts]


def every_argv_in_src():
    for path in python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in kubectl_argv_nodes(tree):
            yield path, node, literal_words(node)


def verb_of(words):
    return next((word for word in words[1:] if isinstance(word, str) and not word.startswith("-")),
                None)


def test_every_kubectl_call_in_src_carries_its_cluster():
    """The addressing rule, on every module in the tree.

    A call whose flags come from a splat is checked where the splat resolves, which is
    `kubectl_target_args` — tested directly below, so the chain is closed either way. What is not
    allowed is a call that carries nothing.
    """
    offenders = []
    for path, node, words in every_argv_in_src():
        addressed = any(word is True or (isinstance(word, str) and word in ADDRESSING_FLAGS)
                        for word in words)
        if addressed:
            continue
        # A read the table declares unconverted is allowed to be unaddressed for exactly one reason:
        # its module passes its argv through a seam that has no target to address it with. The table
        # says which modules those are, so the exemption cannot spread.
        if PENDING_KUBECTL_READS.get(relative(path), 0) and verb_of(words) in CONVERTED_READ_VERBS:
            continue
        offenders.append(f"{relative(path)}:{node.lineno} {[w for w in words if w is not True]}")

    assert offenders == [], (
        "a `kubectl` call in src/ was found with no --context/--kubeconfig in its argv and no splat "
        "that could supply them")


def test_the_kubectl_calls_that_stay_are_the_ones_the_spec_names():
    """Not just *that* they carry a cluster, but *why they are still there*.

    A `kubectl get` that came back would pass the guard above the moment someone added `--context`
    to it, and fail this one. The table is the list of what the conversion still owes, checked per
    in total, so the debt is counted instead of being spread across comments.
    """
    strays = []
    per_module = {}
    for path, node, words in every_argv_in_src():
        verb = verb_of(words)
        if verb is None:
            continue
        key = relative(path)
        if verb in SESSION_VERBS or verb in MANIFEST_VERBS or verb in KUBECONFIG_VERBS:
            continue
        assert key in PENDING_KUBECTL_READS, (
            f"{key}:{node.lineno} is a kubectl read that is neither in the table of reads the "
            f"conversion still owes nor in the set of calls that stay with `kubectl` — it is new, and "
            "it is wrong")
        per_module[key] = per_module.get(key, 0) + 1
        strays.append((key, verb, node.lineno))

    assert per_module == {name: count for name, count in PENDING_KUBECTL_READS.items() if count}, (
        f"the reads the conversion still owes have moved without the table moving with them: found "
        f"{sorted(strays)} against {PENDING_KUBECTL_READS}")


def test_kubectl_target_args_refuses_a_target_that_cannot_be_addressed():
    """The refusal, named — because a guard that only checks the happy path is a formality.

    A target built from a connection document has no kubeconfig and no context, so the session
    calls that survive have nothing to carry. Saying "and then we guess" would be the failure the
    deleted read used to make, arriving from the other side: a `port-forward` against a cluster the
    command was never pointed at.
    """
    from kubementat.core.identity.target import ClusterTarget
    from kubementat.kubernetes_utils import kubectl_target_args

    with pytest.raises(Exception) as raised:
        kubectl_target_args(ClusterTarget(environment="dev", context=None, kubeconfig_path=None,
                                          address="https://example.test:6443",
                                          source="connection"))
    assert "kubecontext" in str(raised.value) or "kubeconfig" in str(raised.value).lower()

    assert kubectl_target_args(ClusterTarget(environment="dev", context="dev",
                                             kubeconfig_path=None, address="https://x:6443",
                                             source="static.json")) == ["--context", "dev"]
    assert kubectl_target_args(ClusterTarget(environment="dev", context=None,
                                             kubeconfig_path="/tmp/x", address="https://x:6443",
                                             source="kubeconfig_file")) == ["--kubeconfig", "/tmp/x"]
