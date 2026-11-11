"""The deployer SSH keypair comes from `CryptoUtils`, and the script that used to make it stays gone.

`scripts/secret_management/generate_ssh_key.sh` is deleted. It had no caller: nothing in `src/`,
`tests/`, `tekton_ci/`, `templates/`, `examples/`, or any sibling script named it — a script that
takes one argument and writes two files into whatever directory it was run from, reachable only
through `kmt run-script --args .../generate_ssh_key.sh`, which is the shape no verb takes any more. What the
product actually does with a deploy key is `kmt config init`.

The implementation is `CryptoUtils.generate_ssh_key_pair` (`src/kubementat/crypto_utils.py:14`) and
it is reached once, by `config_keys._ssh_steps` (`src/kubementat/config_keys.py`), which is the
half of `kmt config init` that makes the pair. The script was
shortest in the ledger and the pair most likely to be quietly wrong: `ssh-keygen -b 2048 -t rsa` and
`cryptography`'s RSA generator both leave you a `KEYNAME` and a `KEYNAME.pub`, which is a claim
anyone could make with `assert path.exists()`, so what is asserted below is what the earlier parity
comparison used to measure — the key size, the container each side wrote, and the base64 line the
operator pastes into `static.encrypted.json`.

Two things the ledger said were blockers, and what they turned out to be:

* *"add `kmt generate-ssh-key` first — still not there"*. It is still not there, and it is not
  needed: `kmt config init` is the command that generates a deployer key, and the script was never
  the implementation of anything that command called. No verb was lost, because no verb existed.
* *"bash skips an existing key, the library overwrites it"*. True of the library alone, and already
  closed where it matters: the one call site asks `_pair_steps` before it generates, so the live
  path leaves a key that is there where it is. `test_an_existing_key_is_left_where_it_is` measures
  that guard on bytes rather than trusting the sentence about it.

One difference survives the deletion as a fact and not as a bug, so it is recorded here: bash wrote
the private half in OpenSSH's own container, the library writes a classic RSA PEM (`TraditionalOpenSS
L`). Both are 2048-bit RSA and both parse; anything that reads the key by handing it to `ssh` will
not notice. `src/kubementat/automation.py` only ever base64-encodes what the library returned.
"""

import base64
from pathlib import Path

from cluster_parity import operations_from_calls
from kubementat import automation, config_init, config_keys
from kubementat.crypto_utils import CryptoUtils

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/secret_management/generate_ssh_key.sh"
KEY_NAME = "deployerkey1"

# Where a caller of a one-argument key generator could have been hiding.
CALLER_ROOTS = ("src", "tekton_ci", "examples")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.


def _is_packaging_leftover(path):
    """Build droppings only. `__pycache__` and `*.egg-info` are copies of this checkout, not callers."""
    parts = [part.lower() for part in path.parts]
    return any(part == "__pycache__" or part.endswith(".egg-info") for part in parts)


def generate_into(directory):
    """The library call, exactly as `config_keys` reaches it."""
    return CryptoUtils.generate_ssh_key_pair(key_size=2048, key_file_base_name=KEY_NAME,
                                             target_directory=str(directory))


def test_the_script_is_not_in_the_tree():
    """The deletion itself, asserted where a re-added file would land."""
    assert not (REPO_ROOT / SCRIPT).exists(), \
        f"{SCRIPT} is back; CryptoUtils.generate_ssh_key_pair is the implementation, not a second one"


def test_no_code_root_names_a_script_that_is_not_there():
    """No caller, in the only directories a caller could live in.

    `tests/test_command_execution_policy.py` fails if `src/` names a `.sh` the ledger deleted, so
    `src/` is covered by a rule that already exists. What this adds is the other three roots and
    the sibling scripts: a sibling script that named this generator would be a live caller, and the
    reason `scripts/` is searched rather than trusted is the helper library that lived beside it and
    `source`d its consumers instead of the other way round. That library is deleted, along with
    the last script that sourced it, so nothing can be sourced from it now — but the direction it
    ran in is why this root is read and not assumed.
    """
    offenders = []
    for root in CALLER_ROOTS:
        for path in (REPO_ROOT / root).rglob("*"):
            if not path.is_file() or _is_packaging_leftover(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if SCRIPT in line or "generate_ssh_key.sh" in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")
    for path in (REPO_ROOT / "scripts").rglob("*.sh"):
        if SCRIPT in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert offenders == [], f"a caller is still here: {offenders}"


def test_the_keypair_lands_in_the_target_directory_with_both_names(tmp_path):
    """`deployerkey1` and `deployerkey1.pub`, written where the caller said to write them.

    An empty directory first, so the assertion is about the files this call made and not about
    what a previous test left lying around.
    """
    generate_into(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([KEY_NAME, f"{KEY_NAME}.pub"])


def test_the_private_and_public_halves_are_a_2048_bit_rsa_pair(tmp_path):
    """The claim `-b 2048 -t rsa` carries, measured on what the library wrote.

    Both halves are loaded rather than eyeballed: a `key_size` that drifted to 4096 would still
    pass a "the files exist" test, and nobody would notice until something behind the key refused
    it. The public half is read with `load_ssh_public_key`, which is what an OpenSSH client does.

    What is also written here, because nothing else records it: the private half is a classic RSA
    PEM and not the OpenSSH container the deleted script produced. Both parse, both are the same
    kind of key, and `setup_pipelines` only ever encodes the bytes it is given.
    """
    from cryptography.hazmat.primitives import serialization

    generate_into(tmp_path)
    private_bytes = (tmp_path / KEY_NAME).read_bytes()
    public_bytes = (tmp_path / f"{KEY_NAME}.pub").read_bytes()

    private_key = serialization.load_pem_private_key(private_bytes, password=None)
    public_key = serialization.load_ssh_public_key(public_bytes)

    assert private_key.key_size == 2048
    assert public_key.key_size == 2048
    assert b"BEGIN RSA PRIVATE KEY" in private_bytes, \
        "the container this replaced is OpenSSH's; if the writer changed, this line changes with it"

    # The pair really is a pair, and not two keys that happen to agree on their length.
    assert public_key.public_bytes(serialization.Encoding.OpenSSH,
                                   serialization.PublicFormat.OpenSSH) == public_bytes.rstrip(b"\n")


def test_the_private_key_returned_is_the_file_written(tmp_path):
    """What a caller stores is the string the call returns, and what it returns is the file.

    The library both returns and writes, and the two have to agree — `setup_pipelines`
    base64-encodes the file, so a return value that drifted from the file would be a key nobody
    has.
    """
    private_key, public_key = generate_into(tmp_path)

    assert private_key.strip() == (tmp_path / KEY_NAME).read_text(encoding="utf-8").strip()
    assert public_key.strip() == (tmp_path / f"{KEY_NAME}.pub").read_text(encoding="utf-8").strip()


def test_the_stored_blob_is_the_key_encoded_once(tmp_path):
    """The last thing the script did was print a blob, and the contract behind it is still one pass.

    `base64_encode "$private_key_contents"` was the script's own last line, and what it fed was the
    Secret's `ssh-privatekey` field. `automation._base64_encode_file` is the same act on the Python
    side — it is what `setup_pipelines` builds both deployer Secrets from — so it is asserted here:
    decode the stored blob and you get the key back, byte for byte — not the key's base64, twice
    encoded, which is the failure mode that produces a Secret that decodes to `QlZOV1p...` instead
    of a key.
    """
    generate_into(tmp_path)

    encoded = automation._base64_encode_file(tmp_path / KEY_NAME)

    assert base64.b64decode(encoded.encode()) == (tmp_path / KEY_NAME).read_bytes()


def test_an_existing_key_is_left_where_it_is(tmp_path, monkeypatch):
    """The branch the ledger said was not closed, measured against the caller that exists.

    bash checked `[[ ! -f $KEYNAME ]]` and said "Skipping generation"; the library writes
    unconditionally, so a bare call *would* regenerate a deploy key in place. `config_keys` is the
    caller now, and `_ssh_steps` asks `_pair_steps` before it generates: a pair that is already
    there is reported and not rewritten. Asserted on bytes, because a step that says SKIPPED while
    the file changed is exactly the agreement that hides a bug.
    """
    CryptoUtils.generate_ssh_key_pair(key_size=2048, key_file_base_name=config_keys.SSH_PRIVATE_NAME,
                                      target_directory=str(tmp_path))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    calls = []
    monkeypatch.setattr(CryptoUtils, "generate_ssh_key_pair",
                        staticmethod(lambda *args, **kwargs: calls.append(kwargs)))
    steps = config_keys._ssh_steps(tmp_path)

    assert calls == [], "the library was asked to regenerate a keypair that exists"
    assert [step.action for step in steps] == [config_init.SKIPPED, config_init.SKIPPED]
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_generating_a_key_talks_to_no_cluster_tool(tmp_path, fake_binaries):
    """The other half of "this is not a cluster command", now that the script cannot contradict it.

    The comparison used to run both sides through the shims and assert the log was empty on each.
    The Python side still does: generating a key is `cryptography` and a `write`, so an empty log
    after a real call is the honest form of the claim, and anything that ever adds a `kubectl` to
    this path fails here instead of in a pipeline.
    """
    generate_into(tmp_path)

    assert operations_from_calls(fake_binaries.take_calls()) == []
