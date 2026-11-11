"""The two rules stated in the negative, read out of `src/` rather than trusted.

Neither half is new information — both became true the moment the two-cluster design landed — which
is exactly why
they belong in a test. A rule that holds today is worth nothing if the next diff can break it in one
line, and both breakages this guards are the quiet kind:

* **`config.load_kube_config()` anywhere in `src/`.** It is the one call that decides which cluster a
  whole process means, so a second use of it anywhere re-introduces the disease the two-cluster
  design names: two
  clusters in one process, the second one winning. It fails loudly on a host with no kubeconfig and
  silently on a host that has one, which is the worst shape a regression can take.
* **`client.<Name>Api()` built with no arguments.** Zero arguments means "whatever this process is
  already pointing at", which is the same statement wearing different clothes, and it is the shape a
  generated client takes when somebody remembers the API object and forgets the client.

**The guard is a walk of the syntax tree, not a substring of the file.** That choice is the part worth
writing down, because a grep-shaped assertion here is quietly wrong in one direction and noisily
wrong in the other:

* `ClusterTarget.client()` calls `kubernetes_config.load_kube_config_from_dict`. That name *contains*
  `load_kube_config`, so a substring rule must either flag the one call this rule wants — the
  loader that takes a document and a `Configuration` of its own, and is what makes
  `from_connection` possible at all — or it has to be loosened until it catches nothing.
* Matching the *called name* is exact in both directions. That is why there is **no exempt list** here
  the way `test_no_cli_command_starts_a_script_by_path.py` declares one: the legitimate call is a
  different name, not a line the rule forgives. The last test says exactly that, on a snippet, so that
  if the matcher ever starts treating it as a violation this file fails instead of the rule dying.

And because a guard that cannot tell a violation from a legal line is not a guard, both rules are
proved against a snippet carrying the very thing they forbid — the way ticket 5's argv guard was
proved by mutation rather than by an absence of failures.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def shipped_python_files():
    """Every `.py` that ships, with build junk left out the way the other tree-walks do it."""
    return [path for path in sorted(SRC.rglob("*.py")) if "__pycache__" not in path.parts]


def calls(source: str):
    """The call expressions in *source*, each as `(called name, line, arguments?)`.

    A call whose function is neither a plain name nor an attribute (a subscript, the result of another
    call) has no single name to compare against, so it is reported with a name of `None` and matches
    neither rule. Nothing here reaches a cluster that way; inventing a name for it would report more
    than the rule knows.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        bound = bool(node.args or node.keywords)
        found.append((name, node.lineno, bound))
    return found


def kube_config_load_lines(source: str):
    """The lines of *source* that load a kubeconfig into the process.

    The exact name only, which is the whole point of walking the tree:
    `load_kube_config_from_dict` is a different function and is not a violation of this rule.
    """
    return [line for name, line, _bound in calls(source) if name == "load_kube_config"]


def unbound_api_lines(source: str):
    """The lines of *source* that build an API object out of nothing.

    The rule is the *shape*, not four names: anything whose called name ends in `Api` and is handed
    neither a positional nor a keyword argument. Listing `CoreV1Api`, `AppsV1Api`,
    `RbacAuthorizationV1Api` and `CustomObjectsApi` would be a rule that expires the first time a
    `BatchV1Api` is needed, and an expiring rule reads like coverage while it is not.
    """
    return [line for name, line, bound in calls(source)
            if name is not None and name.endswith("Api") and not bound]


def violations(finder, what):
    """Every file and line of `src/` where *finder* says the rule is broken.

    The whole set rather than the first hit, because a guard that stops at one offender cannot tell
    "one regression" apart from "the rule is not understood", and the second is how a guard test goes
    green while enforcing nothing.
    """
    found = []
    for path in shipped_python_files():
        for line in finder(path.read_text(encoding="utf-8")):
            found.append(f"{path.relative_to(SRC)}:{line} {what}")
    return found


def test_no_kubeconfig_is_loaded_into_the_process_anywhere_in_src():
    """No `config.load_kube_config()` in `src/`, because that call decides it for everyone.

    There is exactly one place in `src/` that reads credentials from a file and hands them to a client
    — `ClusterTarget.client()` — and the promise that two `Context` objects for two clusters can be
    alive in one process survives only while that is true. A second call anywhere, however
    reasonable it looks at the call site, makes the answer a property of the process.
    """
    assert violations(kube_config_load_lines, "loads a kubeconfig into the process") == []


def test_no_api_object_is_built_without_a_client():
    """Every API object is built from a client, and that client came from a target.

    `client.CoreV1Api()` is not an error the interpreter notices; it silently binds the call to
    whatever the process loaded last, so the only test that can hold the rule is this one, and the
    only shape of it that is worth having is the general one.
    """
    assert violations(unbound_api_lines, "builds an API object with no client") == []


def test_the_kubeconfig_guard_distinguishes_the_loader_it_forbids_from_the_one_it_does_not():
    """The exemption this guard does not need, asserted instead of declared.

    `load_kube_config_from_dict` is the loader `ClusterTarget.from_connection` uses, and it is legal
    for one reason: it fills a `Configuration` the caller already holds rather than the
    process's. A substring guard would have had to forgive the name and lose the rule; this one has
    nothing to forgive, which is the point of testing it against both spellings.
    """
    assert kube_config_load_lines("config.load_kube_config()") == [1]
    assert kube_config_load_lines("from kubernetes.config import load_kube_config\n"
                                  "load_kube_config()") == [2]
    assert kube_config_load_lines(
        "kubernetes_config.load_kube_config_from_dict(doc, client_configuration=cfg)") == []
    # And the comment-shaped case a grep would have flagged, which is not a call at all.
    assert kube_config_load_lines("# config.load_kube_config() would be wrong here") == []


def test_the_api_guard_reports_a_zero_argument_call_and_forgives_a_bound_one():
    """Both directions of item 2, on code that is not in `src/` and could have been.

    `client.CoreV1Api()` is the regression; `client.CoreV1Api(self.api_client)` is what every call
    site in the product looks like now, and `client.ApiClient(cfg)` is a name the rule must not
    mistake for an API object just because it is in the same module.
    """
    assert unbound_api_lines("client.CoreV1Api()") == [1]
    assert unbound_api_lines("self._api(client.AppsV1Api)") == []
    assert unbound_api_lines("client.CoreV1Api(self.api_client)") == []
    assert unbound_api_lines("x = client.RbacAuthorizationV1Api(\n    api_client)") == []
    assert unbound_api_lines("client.ApiClient(configuration)") == []
    assert unbound_api_lines("return self._api('core', client.CoreV1Api)") == []
