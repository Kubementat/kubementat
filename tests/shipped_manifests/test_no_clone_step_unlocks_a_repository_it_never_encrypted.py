"""No step clones a repository and then unlocks it, and no step receives a secret GPG key."""

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
    """A clone step that runs `git-crypt unlock` reports success on a repository with no keystore.
    """
    for name in CLONE_TASKS:
        clean, why = _task_is_clean(name)
        assert clean, why


def test_no_pipeline_param_can_ask_a_step_to_unlock_on_clone():
    """A param on the pipeline is a way back in, so the shipped pipelines declare none either."""
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
    """The prohibition is on the *private GPG half*, which is a different file from the symmetric key.
    """
    for task_file in sorted(tekton_ci_path("tasks").rglob("*.yml")):
        text = task_file.read_text(encoding="utf-8")
        for forbidden in _SECRET_GPG_PROHIBITIONS:
            assert forbidden not in text, (
                f"{task_file.name} names {forbidden}: the deployer's secret half belongs in a "
                "gitignored file inside the configuration directory, and importing it into a pod "
                "keyring is the behaviour this removal took away, not a knob to keep")
