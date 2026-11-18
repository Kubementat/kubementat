"""The deployer SSH keypair comes from `CryptoUtils`, and the script that used to make it stays gone.

`scripts/secret_management/generate_ssh_key.sh` is deleted. It had no caller: nothing in `src/`,
`tests/`, `tekton_ci/`, `templates/`, `examples/`, or any sibling script named it — a script that
takes one argument and writes two files into whatever directory it was run from, reachable only
through `kmt run-script --args .../generate_ssh_key.sh`, which is the shape no verb takes any more. What the
product actually does with a deploy key is `kmt config init`.

The implementation is `CryptoUtils.generate_ssh_key_pair` (`src/kubementat/crypto_utils.py:14`) and
it is reached once, by `config_keys._ssh_steps` (`src/kubementat/config/keys.py`), which is the
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
not notice. `src/kubementat/automations/install/pipelines.py` only ever base64-encodes what the library returned.
"""

import base64
from pathlib import Path

from kubementat.automations.install import pipelines as pipelines_slice
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
def test_the_keypair_lands_in_the_target_directory_with_both_names(tmp_path):
    """`deployerkey1` and `deployerkey1.pub`, written where the caller said to write them.

    An empty directory first, so the assertion is about the files this call made and not about
    what a previous test left lying around.
    """
    generate_into(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([KEY_NAME, f"{KEY_NAME}.pub"])


def test_the_stored_blob_is_the_key_encoded_once(tmp_path):
    """The last thing the script did was print a blob, and the contract behind it is still one pass.

    `base64_encode "$private_key_contents"` was the script's own last line, and what it fed was the
    Secret's `ssh-privatekey` field. `pipelines_slice._base64_encode_file` is the same act on the Python
    side — it is what `setup_pipelines` builds both deployer Secrets from — so it is asserted here:
    decode the stored blob and you get the key back, byte for byte — not the key's base64, twice
    encoded, which is the failure mode that produces a Secret that decodes to `QlZOV1p...` instead
    of a key.
    """
    generate_into(tmp_path)

    encoded = pipelines_slice._base64_encode_file(tmp_path / KEY_NAME)

    assert base64.b64decode(encoded.encode()) == (tmp_path / KEY_NAME).read_bytes()
