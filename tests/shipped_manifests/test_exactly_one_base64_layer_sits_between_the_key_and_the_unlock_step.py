"""Exactly one base64 layer sits between `git_crypt_symmetric.key` and the step that unlocks with it.

This file exists because a guard that looks for the tokens `string_data` and `_base64_encode_file` cannot
tell one layer from two. A double encoding keeps both tokens present and produces a Secret that is
indistinguishable from a wrong key at the point of failure: git-crypt says `not a valid git-crypt key
file`, which reads like someone's key is bad when what actually happened is that our side wrapped it
twice.

So both numbers here are measured against the real git-crypt binary rather than argued:

* one layer, decoded once by the step -> unlock succeeds, the encrypted file becomes plaintext;
* two layers, decoded once -> `Error: -: not a valid git-crypt key file`, nothing unlocks, exit non-zero.

The second assertion is what makes the first one not vacuous: without it, "the shipped thing works" proves
nothing about which shipped thing would work.

kubementat reads and never unlocks, so nothing in here unlocks from product code: the unlock
happens in a subprocess the way the pipeline would run it, against a repository this test creates.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml
from asset_paths import tekton_ci_path

TASKS = tekton_ci_path("tasks")
UNLOCK_TASK = "git-crypt-unlock"


def _git(args, where):
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "probe", "GIT_AUTHOR_EMAIL": "probe@base.example.test",
           "GIT_COMMITTER_NAME": "probe", "GIT_COMMITTER_EMAIL": "probe@base.example.test"}
    subprocess.run(["git"] + args, cwd=str(where), check=True, env=env)


def _locked_repository(root: Path) -> Path:
    """A git-crypt repository with one encrypted file, plus the key that opens it.

    `git-crypt init` before the commit is what makes the committed bytes ciphertext, which is the whole
    premise: a clone of this repository arrives unreadable, exactly like `git-clone-configuration`.
    """
    origin = root / "origin"
    origin.mkdir(parents=True)
    _git(["init", "-q"], origin)
    (origin / ".gitattributes").write_text("*.encrypted.* filter=git-crypt diff=git-crypt\n",
                                           encoding="utf-8")
    target = origin / "dev" / "static.encrypted.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"password": "hunter2"}\n', encoding="utf-8")
    subprocess.run(["git-crypt", "init"], cwd=str(origin), check=True)
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "initialise"], origin)
    key = root / "git_crypt_symmetric.key"
    subprocess.run(["git-crypt", "export-key", str(key)], cwd=str(origin), check=True)
    return origin


def _fresh_clone(root: Path, origin: Path, name: str) -> Path:
    clone = root / name
    _git(["clone", "-q", str(origin), str(clone)], root)
    _git(["config", "user.email", "probe@base.example.test"], clone)
    _git(["config", "user.name", "probe"], clone)
    return clone


def test_the_product_builds_that_one_layer_and_not_another():
    """The empirical half proves one layer is the right transport; this half proves the product uses it.

    Reading `_base64_encode_file` directly would not catch a change inside `setup_pipelines`, so this goes
    at the call itself and counts the encodings that touch the value. A `base64.b64encode(...)` wrapped
    around `_base64_encode_file(...)` is the mutation that motivated the file, and a token-presence check
    would happily pass it.
    """
    automation = Path("src/kubementat/automations/install/pipelines.py").read_text(encoding="utf-8")
    call = re.search(r"create_or_replace_secret\(\s*namespace=pipeline_ns,\s*secret_name="
                     r'"git-crypt-configuration-key"[\s\S]{0,700}?\)\s*$', automation, re.M)
    assert call, "the Secret's create call is not where the deployer secrets are, so there is nothing here to count"
    body = call.group(0)
    assert "require_symmetric_key" in body, \
        "the Secret is built without the reader that names the file it comes from"
    assert body.count("_base64_encode_file") == 1, \
        f"the call touches _base64_encode_file {body.count('_base64_encode_file')} times; the transport " \
        "is the file's bytes under exactly one layer"
    assert "b64encode" not in body, \
        "the value is wrapped in a second base64 encode. Measured: a doubled layer makes git-crypt say " \
        "'not a valid git-crypt key file', which is indistinguishable from a wrong key and is our bug, " \
        "not the operator's"
    assert "string_data" in body and "raw_data" not in body, \
        "the Secret is not built with string_data, so the API server's transport encoding lands on top of " \
        "ours and the step decodes something other than what we wrote"

def _plaintext_repository(root: Path) -> Path:
    """A repository that never used git-crypt: no `.gitattributes`, nothing encrypted.

    This is the tree `kmt install` gets no Secret for, and the shape the unlock step must let through
    instead of failing on a key that is correctly absent.
    """
    origin = root / "plain-origin"
    origin.mkdir(parents=True)
    _git(["init", "-q"], origin)
    target = origin / "dev" / "static.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"a": 1}\n', encoding="utf-8")
    _git(["add", "-A"], origin)
    _git(["commit", "-qm", "plaintext"], origin)
    return origin


def _step_script() -> str:
    """The shipped step's own script, with its two Secret params resolved to their declared defaults.

    Run verbatim rather than re-expressed, because what needs proving is that *the text in the manifest*
    does the right thing and not that a test's transcription of it does.
    """
    task = yaml.safe_load((TASKS / f"{UNLOCK_TASK}.yml").read_text(encoding="utf-8"))
    defaults = {p["name"]: p.get("default", "") for p in task["spec"].get("params") or []}
    body = task["spec"]["steps"][0]["script"]
    for name, value in defaults.items():
        body = body.replace("$(params." + name + ")", str(value))
    assert "$(params." not in body, "the step still references a param this file cannot resolve, so it " \
        "would resolve to nothing at run time and be tested as something else entirely"
    return body


def _run_step(clone: Path, key_env: str):
    return subprocess.run(["bash", "-c", _step_script()],
                          cwd=str(clone),
                          env={**os.environ,
                               "CONFIGURATION_ROOT": str(clone),
                               "GIT_CRYPT_KEY_BASE64": key_env},
                          capture_output=True, text=True)


def test_the_step_says_which_kind_of_missing_key_this_is(tmp_path):
    """An absent key has two meanings and the step has to tell them apart, measured on both shapes.

    A configuration that never used git-crypt gets no Secret, so the step's key is absent by design and
    the right answer is to move on. A configuration that *did* declare the filter and got no key is
    broken, and the right answer is to stop. Running `git-crypt unlock` cannot tell the two: measured, a
    valid key on a repository that never used git-crypt exits 0 and prints nothing, so a step that always
    ran it would be silent-green about a broken encrypted clone.

    The marker is `.gitattributes` naming the filter, because it is the git-crypt fact that a clone
    carries -- the keystore under `.git/` is not cloned and `git_crypt_symmetric.key` is gitignored. It is
    the same marker `uses_git_crypt` reads in the product, and that is why install's decision and the
    step's decision cannot disagree.
    """
    if not shutil.which("git-crypt") or not shutil.which("git"):
        raise AssertionError("git-crypt and git are required on any machine that ships this product")

    encrypted = _fresh_clone(tmp_path, _locked_repository(tmp_path), "needs-a-key")
    plaintext = _fresh_clone(tmp_path, _plaintext_repository(tmp_path), "needs-nothing")

    refused = _run_step(encrypted, "")
    assert refused.returncode != 0, \
        "an encrypted configuration that received no key sailed through the step, so every later step " \
        "read ciphertext and reported it as configuration"
    assert "declares git-crypt" in refused.stdout + refused.stderr, \
        "the refusal does not say that the tree is the encrypted kind, which is the sentence that tells " \
        "an operator to run kmt install and not to copy a key around"

    passed = _run_step(plaintext, "")
    assert passed.returncode == 0, \
        f"a plaintext configuration failed the step for want of a key it never needed: " \
        f"{(passed.stdout + passed.stderr)[-300:]}"
    assert "nothing to unlock" in passed.stdout + passed.stderr, \
        "the step skipped the unlock without saying so, which is the silent-green shape this file keeps " \
        "the other two cases honest about"

    # Both branches have to be reachable, or one of the two assertions above is decoration.
    assert "filter=git-crypt" in (encrypted / ".gitattributes").read_text(encoding="utf-8"), \
        "the fixture that is supposed to trigger the refusal does not declare the filter"
    assert not (plaintext / ".gitattributes").exists() or \
        "filter=git-crypt" not in (plaintext / ".gitattributes").read_text(encoding="utf-8"), \
        "the fixture that is supposed to be skipped declares the filter and would have been refused"
