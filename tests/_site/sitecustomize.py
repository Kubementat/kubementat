"""Point the Kubernetes client at the test's fake, for a process the test cannot patch.

`tests/apiserver_fake.py` holds the substance; this is only the hook. It is put on `PYTHONPATH`
for every `kmt` a test runs (`conftest.run_kmt`), and it installs nothing unless it was told to:
no `SHIM_RESPONSE_DIR`, no fake. A developer who runs the module by hand is left with the real
transport, which is the point of hanging the patch off the environment instead of the CLI.
"""
import os

if os.environ.get("SHIM_RESPONSE_DIR"):
    try:
        from apiserver_fake import install

        install()
    except ImportError:
        pass
