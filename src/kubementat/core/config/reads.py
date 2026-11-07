"""One sentence for every read of a kubementat configuration file.

One name lives here. `read_decrypted` is the read — bytes in, text out. The sentence its refusal
gets, `LockedConfigFileError`, lives in `kubementat.errors` with every other refusal, and the rule
it encodes is **one sentence, many error types**: a caller that owns a named exception re-raises
its own type with this sentence inside it, `config validate` turns it into a finding, and nobody
rewrites the wording locally. Both exist because one failure was being described four different
ways by code that had read the same file.

The reason it took a ticket to say this once is that `UnicodeDecodeError` is a `ValueError` and not
an `OSError`, so every `except OSError` in the read graph walked straight past it. Four sites caught
it and described it in four different words, two did not catch it at all, and
`config_init.cluster_is_unaddressed` swallowed it and answered *"this environment addresses no
cluster"* about a file it had never read — which is a false sentence rather than an error, and the
worst failure in the tree is the one that prints confidently instead of refusing.

**What this seam is not, and which of those three sentences a reader would otherwise guess.** It
does not parse: every caller owns `json.loads` or `yaml.safe_load`, and `validate` owns that sentence
differently on purpose. It does not exec `git-crypt` or any other tool, and it does not check unlock
status, because nothing can know it from a file's contents and kubementat reads and never
unlocks — the checkout's smudge filter is what makes a checked-out file readable in the first place,
so a file that will not decode is refused as git-crypt's and left exactly where it is. It does not
decide whether the file exists: `FileNotFoundError` propagates, because "the team's optional file is
missing" is a state `core/config/config.py`'s `missing_team_config_files` machinery already models into a warning
at load and a named refusal at read, and a helper that raised its own error there would turn a
degradable tree into a hard failure.

**The reads this deliberately does not cover, said rather than discovered.** Seven reads go through
it. Out of its reach, and not a defect in it: `kubementat.yaml`, which `config validate` reads and
which is not encrypted material; the Kubernetes client's own read of the same kubeconfig, and
`kubectl config view`, which leave Python entirely; and `values.encrypted/<component>.yaml`, which
helmfile's Go template only asks `isFile` about them; helm opens the bytes itself, against the packaged
`assets/values/` directory. That last one is the product's second and larger encrypted surface, and no
Python function sees it — and what is worth knowing is *how it fails*: if one of those files were
encrypted, helm would get a non-YAML document and the release would die as a non-zero `helmfile apply`,
so no code of ours would ever say "run git-crypt unlock" about it. A `read_decrypted` that claimed to
cover every secret would be the falsest sentence this ticket could write, so it says the boundary here
instead.

One consequence of wiring it, stated before a test finds it: `Config._load_config` reads all four
layer files on the first answer of any category, so the commands that ask a category (`config show`
and the component accessors) refuse over a locked `static.encrypted.json` even when the
key they were asked for sits in the plain file. The answer verbs do not, because `set_answer` and
`unset_answer` open only the file they were told to write. That is a question about *when* layers load
and not about how one file is read. The latter is what this module states.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from kubementat.errors import LockedConfigFileError

__all__ = ["read_decrypted"]


def read_decrypted(path: Union[str, Path], what: str = "configuration file") -> str:
    """The contents of `path` as text, refusing with `LockedConfigFileError` when they are not.

    Reading and nothing else: no parse, no exec, no decision about existence. Every other exception
    this a file can raise — `FileNotFoundError` above all — propagates untouched, for the reason in
    the module docstring. `what` names what the file is supposed to be, so the same refusal says
    "static layer file" or "kubeconfig" instead of a path the operator has to go and look up.
    """
    try:
        return Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        # Deliberately not `str(exc)`: that sentence belongs to the utf-8 codec, and it is what
        # misled four callers into describing a locked file as a broken one.
        raise LockedConfigFileError(
            path, f"the {what} holds ciphertext and not text"
        ) from exc
