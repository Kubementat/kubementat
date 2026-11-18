"""The deployer SSH keypair comes from `CryptoUtils`, and the script that used to make it stays gone.
"""

import base64

from kubementat.automations.install import pipelines as pipelines_slice
from kubementat.crypto_utils import CryptoUtils

SCRIPT = "scripts/secret_management/generate_ssh_key.sh"
KEY_NAME = "deployerkey1"

# Where a caller of a one-argument key generator could have been hiding.
CALLER_ROOTS = ("src", "tekton_ci", "examples")  # "templates" is gone from this tuple: templates/ moved inside the package, so "src" sweeps it and listing the old root would sweep nothing.


def _is_packaging_leftover(path):
    """Build droppings only. `__pycache__` and `*.egg-info` are copies of this checkout, not callers.
    """
    parts = [part.lower() for part in path.parts]
    return any(part == "__pycache__" or part.endswith(".egg-info") for part in parts)


def generate_into(directory):
    """The library call, exactly as `config_keys` reaches it."""
    return CryptoUtils.generate_ssh_key_pair(key_size=2048, key_file_base_name=KEY_NAME,
                                             target_directory=str(directory))
def test_the_keypair_lands_in_the_target_directory_with_both_names(tmp_path):
    """`deployerkey1` and `deployerkey1.pub`, written where the caller said to write them."""
    generate_into(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([KEY_NAME, f"{KEY_NAME}.pub"])


def test_the_stored_blob_is_the_key_encoded_once(tmp_path):
    """The last thing the script did was print a blob, and the contract behind it is still one pass.
    """
    generate_into(tmp_path)

    encoded = pipelines_slice._base64_encode_file(tmp_path / KEY_NAME)

    assert base64.b64decode(encoded.encode()) == (tmp_path / KEY_NAME).read_bytes()
