"""No step clones a repository and then unlocks it, and no step receives a secret GPG key.

Two measured facts and one rule, in that order.

**The product repository has nothing encrypted in it.** `git ls-files .git-crypt .gitattributes` at
this HEAD returns empty, and that is the owner's ruling: the product ships no encrypted
files, the root `.gitattributes` was deleted rather than edited, and the git-crypt keystore that
exists anywhere is the one `config init` creates inside a generated *configuration* directory. So the
step this file guards used to unlock a repository that had nothing to unlock.

**Doing that in a pod is a silent success and not an error.** Two branches, run against real
git-crypt 0.7.0 on this machine:

```
repo with no keystore, empty keyring        → Error: no GPG secret key available…   rc=1
repo with no keystore, secret key in ring   → (nothing)                             rc=0
```

The second branch is the pod. `git status` afterwards is clean, `git-crypt status` says
`not encrypted: <every file>`, no `.git-crypt/` appears — and the step exits 0 having imported a
private key into the pod's keyring for nothing. A step that reports success while doing nothing is
the kind of thing that survives a review, so it is removed here rather than annotated.

**The rule outlives the ticket.** Unlocking belongs outside kubementat, and no `kmt` invocation in
the pipeline execs `git-crypt`. What is *not* refused is a future step that
pipes `git_crypt_symmetric.key` from a `secretKeyRef` into `git-crypt unlock -`, which is the design
that was chosen and which needs a configuration repository to unlock — the clone this phase does not
have yet, per census R. That step would exec `git-crypt`; the thing that cannot come back is *the clone
step unlocking the repository it just cloned*, and *a GPG private half entering a pod at all*.

Those two are what the guards below assert, and they are deliberately narrower than "no git-crypt
anywhere in CI", which would fail for the wrong reason the day that step is built. A symmetric key
and a GPG private key are different files with different blast radii; only the second one is
prohibited here.
"""

import yaml

from asset_paths import tekton_ci_path

CLONE_TASKS = ("git-clone.yml", "git-clone-with-ssh-auth.yml")

# Every name the pod used to be handed the deployer's secret half under, and the param that turned
# the unlock on. A symmetric key is named `git_crypt_symmetric.key` and is NOT on this list.
_SECRET_GPG_PROHIBITIONS = (
    "GIT_DEPLOYER_GPG_PRIVATE_KEY",
    "git-private-gpg-key-secret-name",
    "git-private-gpg-key-secret-key-name",
)
_UNLOCK_SWITCH = "git-crypt-unlock"


def _load(name: str) -> dict:
    return yaml.safe_load(tekton_ci_path("tasks", name).read_text(encoding="utf-8"))


def _all_step_scripts(task: dict) -> list[str]:
    """Every shell body in a Task, across every step, including env values and echoes."""
    chunks: list[str] = []
    for step in task.get("spec", {}).get("steps", []) or []:
        chunks.append(str(step.get("script", "")))
        for entry in step.get("env", []) or []:
            chunks.append(str(entry.get("value", "")))
            # A secretKeyRef is how a key would arrive, so it is part of the script for our purposes.
            if "valueFrom" in entry:
                chunks.append(str(entry["valueFrom"]))
    return chunks


def _task_is_clean(name: str) -> tuple[bool, str]:
    task = _load(name)
    haystack = "\n".join(_all_step_scripts(task))
    params = "\n".join(str(p.get("name", "")) for p in task.get("spec", {}).get("params", []) or [])
    body = haystack + "\n" + params

    if "git-crypt" in haystack:
        return False, f"{name} execs git-crypt from inside the clone step"
    if _UNLOCK_SWITCH in params:
        return False, f"{name} still declares the {params!r} switch param"
    for forbidden in _SECRET_GPG_PROHIBITIONS:
        if forbidden in body:
            return False, f"{name} still names {forbidden}, which carries a secret GPG half into a pod"
    return True, ""


def test_the_clone_steps_do_not_unlock_what_there_is_nothing_to_unlock():
    """A clone step that runs `git-crypt unlock` reports success on a repository with no keystore."""
    for name in CLONE_TASKS:
        clean, why = _task_is_clean(name)
        assert clean, why


def test_the_unlock_switch_param_is_gone_from_both_clone_tasks():
    """The parameter is removed and not repurposed, so nothing can switch the behaviour back on."""
    for name in CLONE_TASKS:
        task = _load(name)
        names = {p.get("name") for p in task.get("spec", {}).get("params", []) or []}
        assert _UNLOCK_SWITCH not in names, f"{name} still declares {_UNLOCK_SWITCH!r}"


def test_no_pipeline_param_can_ask_a_step_to_unlock_on_clone():
    """A param on the pipeline is a way back in, so the shipped pipelines declare none either.

    Checked as a *param name* and not as a substring of the file. The switch was a param called
    `git-crypt-unlock`, and the shipped pipelines legitimately have a step called
    `git-crypt-unlock-configuration` that runs git-crypt after the clone, in the open, and not inside
    the clone step. A substring test
    cannot tell those two apart, so it was about to start failing on correct pipelines -- and a guard that
    fails on correct code is a guard somebody disables.

    The rule this keeps is narrower and truer than the sentence it replaced: nothing may *parameterise*
    unlocking-on-clone, because the clone step cannot honour it. So every param a pipeline declares, and
    every param it passes to a step, is collected and checked. A mutation that adds the switch back is
    caught by name, which is where it would live.
    """
    for pipeline in sorted(tekton_ci_path("pipelines").rglob("*.yml")):
        doc = yaml.safe_load(pipeline.read_text(encoding="utf-8"))
        spec = doc.get("spec", {}) if isinstance(doc, dict) else {}
        names = {p.get("name") for p in spec.get("params", []) or [] if isinstance(p, dict)}
        for step in spec.get("tasks", []) or []:
            names.update(p.get("name") for p in step.get("params", []) or [] if isinstance(p, dict))
        assert _UNLOCK_SWITCH not in names, (
            f"{pipeline.name} declares or passes {_UNLOCK_SWITCH!r}; the clone step cannot honour it any"
            " more, so a pipeline that threads it is declaring an answer nothing reads. Unlocking is a step"
            " of its own now, which is a name for an action and not a switch on somebody else's step")
        # And the stronger statement: no step may ask the *clone* task to unlock by any param it passes.
        for step in spec.get("tasks", []) or []:
            if (step.get("taskRef") or {}).get("name") in CLONE_TASKS:
                for param in step.get("params", []) or []:
                    assert _UNLOCK_SWITCH not in str(param.get("name", "")), \
                        f"{pipeline.name}: {step.get('name')} passes {param.get('name')!r} to a clone " \
                        "task, which is the switch re-entering through the door it was removed from"


def test_the_secret_half_of_a_key_never_enters_a_pod():
    """The prohibition is on the *private GPG half*, which is a different file from the symmetric key."""
    for task_file in sorted(tekton_ci_path("tasks").rglob("*.yml")):
        text = task_file.read_text(encoding="utf-8")
        for forbidden in _SECRET_GPG_PROHIBITIONS:
            assert forbidden not in text, (
                f"{task_file.name} names {forbidden}: the deployer's secret half belongs in a "
                "gitignored file inside the configuration directory, and importing it into a pod "
                "keyring is the behaviour this removal took away, not a knob to keep")
