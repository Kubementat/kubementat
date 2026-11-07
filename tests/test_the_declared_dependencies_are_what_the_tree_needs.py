"""`KUBEMENTAT_DEPENDENCIES` names what the tree actually runs, and the tree is what says so.

`check_dependencies_are_installed` (`src/kubementat/automations/install/dependencies.py:138`) is the gate in
front of `kmt initialize` and `kmt install`: a name it cannot find on `PATH` ends the run. Before
this file existed, nothing anywhere asserted that the list and the tree agreed, and the only hit
for the list in `tests/` was a lambda stubbing the check out of a live-install test. So the list
drifted the way an unchecked list drifts: `yq` was declared for scripts that parse config with it,
the scripts moved to Python, `run_pipeline.sh` -- the last `yq` caller -- was deleted (`85f1ea8`),
and the declaration outlived all of it. Removing it is what this guard exists to keep true, and the
tree here shows the same: `yq` appears in no `src/` argv and in no shipped `scripts/**` file.

`jq` is the interesting half, and the deletion of the last script that ran it is where its story
ends. It was execed by nothing
in `src/`, but `retrieve_node_addresses.sh` -- two lines, the last script that shipped under
`scripts/utilities/kubernetes/` -- piped `kubectl get nodes -o=json` through it, and that script was
part of the distribution. That is why the list describes the distribution and not merely this
package's imports, and why the rule below kept the pair pinned together: either the script ships
and `jq` is declared, or the script goes and `jq` goes with it. That second branch was taken, so
neither half of that pair is true of this checkout any more -- and both directions are still
asserted, because the state that reads as consistency and is neither is a declared tool nothing
runs or a shipped script whose requirement the check stopped verifying.

**How the sets are derived, because a hard-coded list would only repeat the claim it checks.**

* `src_commands` -- the first token of every argv the Python library hands to a command-execution
  call (`shell.run` and friends), plus every argv-like module or local binding (`*_ARGV`,
  `*_CMD`, `argv`, `cmd`), plus the constant argument of any `shutil.which(...)`. It is read off
  the AST of `src/**/*.py`, with the `KUBEMENTAT_DEPENDENCIES` assignment itself excluded so the
  list can never attest itself.
* `script_commands` -- the command-position word of every line of every `scripts/**/*.sh`, after
  comments and shell keywords and `VAR=` prefixes are stripped, split on the usual connectives.
  Since row 21 this can legitimately be empty, because the tree ships no `scripts/**/*.sh` at all;
  the derivation itself is checked on a scratch script,
  `test_the_scripts_derivation_reads_a_command_from_a_scratch_script`.

**What this guard cannot see, said plainly so nobody mistakes a green run for proof.**

* A binary execed through a name that is never a string literal at command position -- built from
  an f-string, picked out of a dict, or handed to a helper that assembles argv itself. Those need
  a human; the `KNOWN_UNDECLARED` table is where such a case gets written down.
* What the CI image installs. `docker_files/ci_images/ubuntu_ci_minimal/Dockerfile` is a different
  workstream's inventory and it *does* install `yq`, so counting it here would let a dead entry
  declare itself alive. `src/` is deliberately the only Python tree scanned.
* Runtime truth. It reads argv, so it cannot tell that a declared tool works, that a tool is
  present on this host, or that a plugin is installed. The "helm diff" entry is the one name that
  is not a program: `check_dependencies_are_installed` runs `helm diff version` for it, so this
  file checks its *base* command (`helm`) and leaves the plugin check to the code.
"""

import ast
import re
from pathlib import Path

from kubementat.automations.install import dependencies as dependencies_slice

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
SCRIPT_ROOT = REPO_ROOT / "scripts"

# Functions that take an argv as their first positional argument.
COMMAND_FUNCTIONS = {"run", "run_argv", "check_call", "check_output", "Popen"}
# A binding named like this is treated as an argv because that is all the AST can tell us.
ARGV_LIKE = re.compile(r"(?:^|_)(?:argv|cmd|command)s?(?:_|$)", re.I)
# Words that are not programs. `[[` and `[` need the escape; the rest are shell control words.
SHELL_NON_COMMANDS = {
    "if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done", "case", "esac",
    "in", "function", "time", "exec", "source", "exit", "return", "break", "continue",
    "local", "export", "declare", "readonly", "unset", "shift", "set", "unset", "echo",
    "printf", "true", "false", "test", "[[", "]]", "[", "]",
}
COMMAND_WORD = re.compile(r"[A-Za-z][A-Za-z0-9._+-]*$")

# Names the list carries that this derivation cannot attest, each with the reason it is still
# carried. The table is part of the guard: a name may not sit in the list without a reason here,
# and a name the derivation *does* attest may not hide in here. If the next dead entry lands, it
# fails for that name and says so.
CARRIED_WITHOUT_A_PROOF = {
    "gpg": "Execed through python-gnupg rather than through a `shell.run` argv, which is why the "
           "scan cannot see it: `CryptoUtils.generate_gpg_key` and the disposable keyrings "
           "`config_keys` asks its two questions against both drive the `gpg` binary, and "
           "`kmt config init` cannot prepare a deployer keypair without it. Kept; a library's child "
           "process is still a dependency the machine has to have.",
    "python3": "The interpreter `kmt` itself runs on -- `src/cli/kmt.py:1` says so in its shebang -- "
               "so it is a precondition rather than a tool kubementat execs.",
    "pip": "Nothing execs it: `kmt` installs no dependencies at runtime (the install step that was "
           "the last thing in the product to touch a package manager is deleted), and no argv names "
           "pip. Whether pip still belongs in the check is a question this guard raises and does not "
           "answer.",
    "tkn": "Reached only by the E2E harness and the CI image, never by `src/` or `scripts/`. Reported "
           "as unproven by this derivation, kept because Tekton setup still assumes a tkn-capable pod.",
}

# Execed by `src/` but absent from the check. A gap, written down rather than asserted away.
#
# `uv` is not here, and is not one of the reasons this guard was written. Until the install step went
# away it was: the derivation attested it because `install_python_dependencies` handed it an argv, and
# the check did
# not name it. That call is deleted, so the derivation no longer sees `uv` at all -- and a row here
# would be a claim about an exec that does not exist, which is exactly what the rule below refuses to
# let a stale justification age into. `uv` is still needed, but as what `python3` above is: the way
# the product is *run* from a checkout -- `src/cli/README.md`'s installation section, and the
# `uv run ...` hints `src/` prints for the operator (`infrastructure/tekton/runs.py`,
# `automation.py:716`, `:996`, `:999`, `:1003`, all log lines and none of them a spawn). That is a
# precondition of a
# source tree, not a tool kubementat execs, and nothing in this file's derivation can attest it, so
# the honest place for it is this comment and not a table.
KNOWN_UNDECLARED = {
    "git-crypt": "Used by `kmt config init`'s keystore setup (config_bootstrap.py) and never "
                 "checked. Recorded as a finding in config_bootstrap's own docstring rather than "
                 "added to the gate `kmt install` runs, where the binary is not needed.",
    "/bin/bash": "Used as the command inside pods and pods-only tunnels; an image requirement, not a "
                 "host one, which is why it is absent from the list on purpose.",
}

# A name that used to be in the list and that the tree no longer needs at all.
FORBIDDEN = {"yq": "no `src/` argv names it, and no shipped `scripts/**` file does either"}


def _declared() -> list:
    return list(dependencies_slice.KUBEMENTAT_DEPENDENCIES)


def _dependency_list_assignment_lines(tree: ast.AST) -> set:
    """Line numbers of `KUBEMENTAT_DEPENDENCIES = [...]`, so the list cannot vouch for itself."""
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "KUBEMENTAT_DEPENDENCIES" for t in node.targets
        ):
            lines.update(range(node.lineno, node.lineno + 16))
    return lines


def src_commands() -> dict:
    """{binary: [where]} -- every program `src/**/*.py` puts in command position.

    An argv head spelled as a module-level constant (`GIT_CRYPT_BINARY = "git-crypt"`, then
    `shell.run([GIT_CRYPT_BINARY, ...])`) is resolved through the module's own assignment table:
    spelling the binary once is how a call site stays honest, and a scan that cannot see it would
    report a gap the tree does not have.
    """
    found = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "egg-info" in path.parts or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        skip_lines = _dependency_list_assignment_lines(tree)

        constants = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            for target in node.targets
            if isinstance(target, ast.Name)
            and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        }

        def record(binary, where):
            if binary:
                found.setdefault(binary, []).append(where)

        for node in ast.walk(tree):
            # argv as the first argument of a command-execution call.
            if isinstance(node, ast.Call):
                callee = node.func
                name = (callee.attr if isinstance(callee, ast.Attribute)
                        else callee.id if isinstance(callee, ast.Name) else "")
                if name in COMMAND_FUNCTIONS and node.args:
                    first = node.args[0]
                    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
                        head = first.elts[0]
                        if isinstance(head, ast.Name):
                            head_value = constants.get(head.id)
                            if head_value:
                                record(head_value, f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                        elif isinstance(head, ast.Constant) and isinstance(head.value, str):
                            record(head.value, f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                if name == "which" and node.args and isinstance(node.args[0], ast.Constant):
                    if isinstance(node.args[0].value, str):
                        record(node.args[0].value, f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            # argv assembled first and handed over later.
            if isinstance(node, ast.Assign) and node.lineno not in skip_lines:
                for target in node.targets:
                    if isinstance(target, ast.Name) and ARGV_LIKE.search(target.id):
                        value = node.value
                        if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
                            head = value.elts[0]
                            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                                record(head.value, f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return found


def _script_command_words(text: str) -> set:
    words = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        for segment in re.split(r"\||;|&&|\|\||\$\(|`", line):
            tokens = segment.split()
            # `FOO=bar cmd ...` runs cmd, not the assignment.
            while tokens and "=" in tokens[0] and not tokens[0].startswith(("-", "--")):
                tokens.pop(0)
            if not tokens:
                continue
            word = tokens[0].strip("\"'")
            word = word.rsplit("/", 1)[-1]
            if COMMAND_WORD.fullmatch(word) and word not in SHELL_NON_COMMANDS:
                words.add(word)
    return words


def script_commands(root: Path = SCRIPT_ROOT) -> dict:
    """{binary: [where]} -- every program a `root/**/*.sh` puts in command position.

    The default root is the shipped tree, which is what the rules below read; the argument exists
    so the walk itself can be exercised on a directory that is not the distribution.
    """
    found = {}
    for path in sorted(root.rglob("*.sh")):
        for word in sorted(_script_command_words(path.read_text())):
            # Reported relative to whatever root was walked, so the scratch canary above and the
            # shipped tree below both produce a path a reader can open.
            found.setdefault(word, []).append(str(path.relative_to(root)))
    return found


def _bases(declared: list) -> set:
    """`helm diff` is not a program, so its check stands or falls with `helm`."""
    return {name.split()[0] for name in declared}


def test_the_declared_dependency_list_is_not_a_law_it_is_a_quote_from_the_tree():
    declared = _declared()
    executed_by_code = src_commands()
    executed_by_scripts = script_commands()

    # Guard against a silent scan failure: if the derivations go quiet, every rule below would
    # pass for the wrong reason. `kubectl` is execed by `src/`, so it is the canary there. The
    # scripts half cannot use the shipped tree any more -- the last `.sh` file is deleted, and an
    # empty tree is the correct answer rather than a broken scan -- so the same
    # question is asked of a scratch script, below.
    assert "kubectl" in executed_by_code, "the src/ scan found no command position at all -- it is broken"

    attested = set(executed_by_code) | set(executed_by_scripts)
    carried = set(CARRIED_WITHOUT_A_PROOF)
    base = _bases(declared)

    # 1. Nothing in the list may name a program that neither the library nor a shipped script runs.
    unaccounted = base - attested - carried
    assert not unaccounted, (
        "KUBEMENTAT_DEPENDENCIES names "
        + ", ".join(sorted(unaccounted))
        + " but no argv in src/ and no command in scripts/** names it, and "
        "CARRIED_WITHOUT_A_PROOF has no reason for it. Either the tool is a dead dependency and "
        "belongs out of the list, or it is a real one and needs a reason written there."
    )

    # 2. Every program a shipped script runs must be declared, or kubementat hands the operator a
    #    script whose requirements it stopped checking. This is the half that keeps `jq` in.
    undeclared_scripts = set(executed_by_scripts) - base
    assert not undeclared_scripts, (
        "scripts/ runs "
        + ", ".join(sorted(undeclared_scripts))
        + " (see "
        + "; ".join(sorted(
            f"{f}: {sorted(executed_by_scripts[f])}" for f in sorted(executed_by_scripts) if f in undeclared_scripts))
        + ") but KUBEMENTAT_DEPENDENCIES no longer checks it. Either declare it, or delete the "
        "script that needs it -- keeping both is the state that reads as consistency and is neither."
    )

    # 3. A reason may not shelter a name the tree already proves, or the table becomes a dump.
    assert not (carried & attested), (
        "CARRIED_WITHOUT_A_PROOF carries "
        + ", ".join(sorted(carried & attested))
        + ", which the tree does attest; drop those rows, they are not a gap any more."
    )
    assert carried <= base, (
        "CARRIED_WITHOUT_A_PROOF describes "
        + ", ".join(sorted(carried - base))
        + ", which is no longer declared."
    )
    for name, reason in CARRIED_WITHOUT_A_PROOF.items():
        assert reason.strip(), f"the carried entry {name} has no reason written"

    # 4. The mirror gap, written down rather than ignored.
    undeclared = set(executed_by_code) - base
    assert undeclared <= set(KNOWN_UNDECLARED), (
        "src/ execs "
        + ", ".join(sorted(undeclared - set(KNOWN_UNDECLARED)))
        + " without checking for it. Declare it, or write it into KNOWN_UNDECLARED with a reason."
    )
    assert set(KNOWN_UNDECLARED) <= set(executed_by_code), (
        "KNOWN_UNDECLARED says src/ execs "
        + ", ".join(sorted(set(KNOWN_UNDECLARED) - set(executed_by_code)))
        + " without checking for it, but no argv in command position names it any more; the gap "
        "is gone, so the row goes too."
    )


def test_yq_is_out_of_the_dependency_list_because_the_tree_says_so():
    """The measurement behind the removal, stated as a fact and re-derived every run.

    Checked as a string *constant*, not as a substring: prose about `yq` is what tells a reader why
    the entry is gone, and a guard that failed on its own explanation would be a guard nobody
    could keep. A bare `"yq"` in the AST can only be an argv or a `which` argument.
    """
    for name, why in FORBIDDEN.items():
        assert name not in _declared(), f"{name} is back in KUBEMENTAT_DEPENDENCIES: {why}"
        named = []
        for path in sorted(SRC_ROOT.rglob("*.py")):
            if "egg-info" in path.parts or "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and node.value == name:
                    named.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert not named, f"{name} is handed to a command again: {named}"
        scripts = [str(path.relative_to(REPO_ROOT)) for path in sorted(SCRIPT_ROOT.rglob("*.sh"))
                   if name in _script_command_words(path.read_text())]
        assert not scripts, f"{name} is called by a shipped script again: {scripts}"


def test_the_scripts_derivation_reads_a_command_from_a_scratch_script(tmp_path):
    """The scripts half of the derivation, asked of a file that is not part of the distribution.

    The canary used to be `kubectl` in the shipped `scripts/utilities/kubernetes/` one-liners. The
    last of them is deleted, so a rule that read the tree alone would go quiet for the
    right reason and be indistinguishable from a walk that stopped working. This is the same walk
    pointed at a script no operator ever gets: if the file walk or the command-position rule
    breaks, `undeclared_scripts` below reads empty for the wrong reason and this is what says so.
    """
    (tmp_path / "utilities").mkdir()
    (tmp_path / "utilities" / "scratch.sh").write_text(
        "#!/usr/bin/env bash\n\n# a comment naming curl is not a command\n"
        "NODES=1 kubectl get nodes -o wide | jq .\n")

    found = script_commands(tmp_path)
    assert "kubectl" in found, f"the walk read nothing from a script that runs kubectl: {found}"
    assert "jq" in found, f"the command-position rule missed the word after a pipe: {found}"
    assert "curl" not in found, f"a comment was counted as a command: {found}"


def test_jq_stays_declared_only_while_a_shipped_script_runs_it():
    """The pairing, asserted from both ends.

    `jq` came out of `KUBEMENTAT_DEPENDENCIES` when the last script that execed it came out of the
    tree. Both directions are still rules: a declared `jq` with no script is a dependency no part
    of the product runs -- the `yq` mistake -- and a script that needs `jq` while the check does not
    name it is kubementat handing the operator a requirement it stopped verifying, which lands as
    `bash: jq: command not found` inside a script the operator was told to run.
    """
    jq_files = [path for path in sorted(SCRIPT_ROOT.rglob("*.sh"))
                if "jq" in _script_command_words(path.read_text())]
    if "jq" in _bases(_declared()):
        assert jq_files, ("jq is declared but no shipped script execs it, so the only reason this "
                          "dependency is kept has gone away; remove it from the list, as `yq` went")
    else:
        assert not jq_files, (
            "jq is no longer declared and "
            + ", ".join(str(p.relative_to(REPO_ROOT)) for p in jq_files)
            + " still runs it; delete the script or declare the tool"
        )
