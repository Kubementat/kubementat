"""The deployer's keypair, as a property of a configuration directory.

`kmt initialize` made these four files as a side effect of its first run, and it made them in the
checkout — `platform_config/deployer_ssh_key` and its three companions. Census L measured what that
leaves behind when the verb goes: the two cluster Secrets that `setup_pipelines` builds are made from
values that only these files carry, so deleting `initialize` without a successor would delete the
deployer along with it. This module is that successor: **the pair is made when the configuration
directory is prepared, and it lives inside that directory.** Nothing here is a command; the verb is
`config init`, and the caller owns the dance that calls this.

**The files, and nothing else.** Inside a configuration directory the names are exactly four:
`deployer_ssh_key`, `deployer_ssh_key.pub`, `gpg_private.key`, `gpg_public_key.gpg`. They are already
named in `config_init.RESERVED_ROOT_NAMES` (`config_init.py:117`), which is what makes them
known-good rather than merely convenient: that guard is what refuses an *environment* that would wear
one of these names, so no layer can ever be written where the keypair lives. The fifth name that sits
beside them in the same tuple — `git_crypt_symmetric.key` — is git-crypt's and is **not** this
module's to write, name as a constant, or imply anything about.

**The step record is `config_init`'s, and so is its vocabulary.** Two "what did this step do" shapes
in one epic is one too many, so every return is a tuple of `InitStep` and every action is `CREATED`,
`SKIPPED`, or `EXISTED` (`config_init.py:101-105`): `CREATED` for a file that was not there and now
is, `SKIPPED` for material this module was asked to ensure and that was already the kind of thing it
would have written, `EXISTED` for material it was not told to make and must not touch. Nothing here
overwrites, in any of the three states.

**The generated private key is unencrypted, and that is the design.** `CryptoUtils.generate_gpg_key`
can only be non-interactive with an empty passphrase (`crypto_utils.py:42-56`), so the file it writes
is a private key that opens without a passphrase. Inside a configuration directory — which is
gitignored and is about to be git-crypt-protected, which is the whole point of phase 5 — an
unencrypted private key *file* is what makes automation possible without holding a passphrase in a
process. It is not a missing `passphrase=`; a reader who looks for one is reading the right code. The
corollary is also true and is worth saying once: because `CryptoUtils` now generates into a ring it
creates and deletes around the `gen_key` call, nothing this module generates reaches the keyring of
whoever ran the command. The private key that used to land there alongside the file is the leak, and
the file is all that was ever meant to hold it.

**No environment reads, and no defaults that come from outside these arguments.** `initialize`
derived the deployer's identity from `BASE_DOMAIN` — a `Config` read inside the class that used to
own it, which is exactly the
answer that belongs to a caller and not to a library. So this module reads no `os.environ`
anywhere, and a generation that is not given an email is refused rather than guessed at — refusing
with the command and flag that would supply the answer is the house rule, and inventing
`deployer@localhost` would be an answer nobody asked for. `require_deployer_keypair`'s email is not a
guess either: it is read back out of the key itself, because the directory has no fifth name to keep
the answer in and a key that cannot say who it belongs to is not a complete pair.

The `gpg` binary is this module's to call and the directory's not to contain: every question this
module and `config_bootstrap`'s step 7 ask gpg about a key file — whose user ID is in this key, what
is a secret key file's public half, and what fingerprint is in this file — is asked against a keyring
created for the call and thrown away in the same `finally`, so a configuration directory never
becomes somebody's `GNUPGHOME` and no key is added to the operator's own keyring. Generating is not
exempt from that promise, because there is no longer an exemption to grant: `CryptoUtils` builds its
own ring around `gen_key` for the same reason the questions are asked there.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click
import gnupg

from kubementat.config_init import CREATED, EXISTED, SKIPPED, InitStep
from kubementat.errors import DeployerKeyMissingError
from kubementat.crypto_utils import CryptoUtils

# The four names, spelled once here for the same reason `config_init` spells the manifest's filename
# once: they are the contract with `RESERVED_ROOT_NAMES`, and a typo in one of them is a keypair that
# is half-read rather than missing. `git_crypt_symmetric.key` is deliberately absent.
SSH_PRIVATE_NAME = "deployer_ssh_key"
SSH_PUBLIC_NAME = "deployer_ssh_key.pub"
GPG_PRIVATE_NAME = "gpg_private.key"
GPG_PUBLIC_NAME = "gpg_public_key.gpg"

KEYPAIR_FILE_NAMES = (SSH_PRIVATE_NAME, SSH_PUBLIC_NAME, GPG_PRIVATE_NAME, GPG_PUBLIC_NAME)

# What `initialize` asked for, carried forward so that a pair made by a verb is the pair a Secret
# was being built from.
SSH_KEY_SIZE = 2048

# One name per write target, in the order the files are laid down. The step carries the path; these
# say which of the four jobs was done.
SSH_PRIVATE_STEP = "deployer ssh private key"
SSH_PUBLIC_STEP = "deployer ssh public key"
GPG_PRIVATE_STEP = "deployer gpg private key"
GPG_PUBLIC_STEP = "deployer gpg public key"

# The private halves are readable by their owner and nobody else, which `CryptoUtils` does not do
# (it writes through `open(path, "w")`, so the mode is the umask's). Setting it here is this module's
# half of the promise; a file this module did not write is left exactly as it was found.
PRIVATE_FILE_MODE = 0o600

# The command that fixes every refusal this module makes, spelled once. It is `config init` and not
# the verb being deleted; an operator pointed at `initialize` by one of these sentences would be
# pointed at a command that is on its way out.
CREATE_COMMAND = "kmt config init"

# Flags that land with the verb half of phase 5. The sentences that refuse name them, so the flag
# names are a claim about the command that uses them and not about this file.
GPG_EMAIL_FLAG = "--gpg-email"
GPG_KEY_FLAG = "--gpg-key"

_SSH_PUBLIC_ALGORITHMS = ("ssh-rsa", "ssh-ed25519", "ecdsa-sha2-", "sk-ssh-ed25519@", "ssh-dss")

# Bytes read from the head of a present file to decide what kind of thing it is. Every marker below
# sits inside the first line, so this is a sniff and not a parse.
_SNIFF_BYTES = 64

_EMAIL_PATTERN = re.compile(r"[^<>@\s,]+@[^<>@\s,]+")


@dataclass(frozen=True)
class DeployerKeypair:
    """The four files, and the identity they are one pair under.

    The paths are absolute-by-construction: they hang off the directory the caller handed, which is
    the answer the caller owns. `gpg_email` is the user ID the key carries, not a guess, so two
    readers of one directory can never disagree about which deployer they are holding.
    """

    ssh_private: Path
    ssh_public: Path
    gpg_private: Path
    gpg_public: Path
    gpg_email: str


def ensure_deployer_keypair(config_dir: Path, *, gpg_key: Path | None = None,
                            gpg_email: str | None = None) -> tuple[InitStep, ...]:
    """Make the deployer keypair exist inside `config_dir`, and say what each step did.

    Each of the two pairs is handled as one unit, because half a keypair is a lie: a pair is produced
    when both of its files are missing and touched not at all when both are already there. A directory
    that holds one file of a pair and not its partner is refused rather than finished, because the
    only way to finish it is to overwrite or discard the half that is there — and kubementat cannot
    tell an operator's file from a half-written one.

    Parameters
    ----------
    config_dir : Path
        The configuration directory that is being prepared. It must already exist: preparing the tree
        is `config init`'s job, and this module's write set is four files and no directories.
    gpg_key : Path, optional
        The deployer's **secret** key file, in the shape `gpg_private.key` takes. When given, that
        file is imported: its bytes become `gpg_private.key` and its public half is exported to
        `gpg_public_key.gpg`. Nothing is generated, and the key is named back to the caller in the
        steps it reports.
    gpg_email : str, optional
        The identity to generate the GPG pair under. Required when a GPG pair has to be made and
        nothing was imported; refused when it is missing, because the answer belongs to the caller.
        Ignored when `gpg_key` is given, because then the answer is inside the key that was handed
        over and `require_deployer_keypair` reads it back out.

    Returns
    -------
    tuple of InitStep
        Four steps, in the order the files are laid down: the private ssh half, the public ssh half,
        the private gpg half, the public gpg half. A second call on a directory that still holds what
        the first one wrote returns `SKIPPED` four times and moves no byte; one that holds material
        kubementat did not make returns `EXISTED` and moves no byte either.

    Raises
    ------
    click.ClickException
        The directory is not there; a pair is half there; `gpg_key` names a file that is not there or
        holds no key; a GPG pair is needed and `gpg_email` is missing. Every one of these is decided
        before a single file is written, so a refusal always leaves the directory as it was found.
    """
    directory = Path(config_dir)
    if not directory.is_dir():
        # Not "the directory is missing and here is how to make it": a keypair cannot be written into
        # a tree that does not exist, and `config init` is what lays a tree down.
        raise click.ClickException(
            f"{directory} is not a directory, so there is nothing here to hold a deployer keypair. "
            f"{CREATE_COMMAND} <configuration directory> --environment <environment> prepares a tree "
            f"and the keypair that belongs to it.")

    # Decided before any write: a flag that points at a missing file is a typo and must not become a
    # generated key, which is the version of this bug that reports itself as success.
    source = _importable_key(gpg_key)

    steps: list[InitStep] = []
    steps.extend(_ssh_steps(directory))
    steps.extend(_gpg_steps(directory, source=source, gpg_email=gpg_email))
    return tuple(steps)


def require_deployer_keypair(config_dir: Path) -> DeployerKeypair:
    """The pair, for a caller that is about to hand it to something else.

    This is the guard census L found missing: `setup_pipelines` reads both deployer keys out of the
    encrypted layer and passes them to `create_or_replace_secret` without asking whether the pair
    exists, so a tree that was prepared without a keypair dies inside the Kubernetes client instead
    of here. Nothing in this function invents a path, creates anything, or execs git-crypt: it checks
    that the four files are there, reads the identity back out of the key, and hands the paths over.

    Raises
    ------
    DeployerKeyMissingError
        One of the four files is absent, or a file that is there carries no deployer identity that
        the module can read. The message names the path it looked in, the environment that path
        configures when it can tell which one that is, and `kmt config init` as the command that
        creates the pair.
    """
    directory = Path(config_dir)
    paths = {name: directory / name for name in KEYPAIR_FILE_NAMES}
    missing = [name for name in KEYPAIR_FILE_NAMES if not paths[name].is_file()]
    if missing:
        raise DeployerKeyMissingError(_missing_pair_message(directory, missing))

    email = _deployer_email(paths[GPG_PUBLIC_NAME], paths[GPG_PRIVATE_NAME])
    if not email:
        raise DeployerKeyMissingError(
            f"{directory / GPG_PUBLIC_NAME} carries no deployer identity that kubementat can read, so"
            f" the keypair in {directory} is not a pair anyone can use: the git-crypt collaborator and"
            f" the git-deployer Secret are both named after that identity. {CREATE_COMMAND}"
            f" {directory} --environment {_environment_words(directory)} --gpg-key <key file> puts a"
            f" key that knows its own name here.")
    return DeployerKeypair(paths[SSH_PRIVATE_NAME], paths[SSH_PUBLIC_NAME],
                           paths[GPG_PRIVATE_NAME], paths[GPG_PUBLIC_NAME], email)


def _ssh_steps(directory: Path) -> tuple[InitStep, ...]:
    """One ssh pair, in the two names the directory has for it. Generated only when both are missing.
    """
    private = directory / SSH_PRIVATE_NAME
    public = directory / SSH_PUBLIC_NAME
    already = _pair_steps("ssh", (private, public), (SSH_PRIVATE_STEP, SSH_PUBLIC_STEP),
                          (_is_private_key, _is_ssh_public_key))
    if already is not None:
        return already

    # The generator writes both halves into the directory it is handed, and `key_file_base_name` is
    # what makes those two halves the names the directory is read by.
    CryptoUtils.generate_ssh_key_pair(key_size=SSH_KEY_SIZE, key_file_base_name=SSH_PRIVATE_NAME,
                                      target_directory=str(directory))
    os.chmod(private, PRIVATE_FILE_MODE)
    return (InitStep(SSH_PRIVATE_STEP, private, CREATED),
            InitStep(SSH_PUBLIC_STEP, public, CREATED))


def _gpg_steps(directory: Path, *, source: Path | None, gpg_email: str | None) -> tuple[InitStep, ...]:
    """One gpg pair, imported when the caller supplied a key and generated when it did not.

    The two halves are one unit for the same reason the ssh halves are: an imported secret key and an
    already-present public file would be a pair that does not belong to itself, so both files are
    replaced by the import or neither is.
    """
    private = directory / GPG_PRIVATE_NAME
    public = directory / GPG_PUBLIC_NAME
    already = _pair_steps("gpg", (private, public), (GPG_PRIVATE_STEP, GPG_PUBLIC_STEP),
                          (_is_gpg_private_key, _is_gpg_public_key))
    if already is not None:
        return already

    if source is not None:
        _import_gpg_key(source, private, public)
    else:
        if not gpg_email:
            raise click.ClickException(
                f"making a deployer gpg key needs an email and none was given: {private} is not here"
                f" and nothing was imported. {CREATE_COMMAND}'s {GPG_EMAIL_FLAG} <email> (or"
                f" {GPG_KEY_FLAG} <file>, to skip generation entirely) is the answer this module"
                f" cannot give for itself — deriving it from an environment variable is what"
                f" initialize did, and the caller owns that answer.")
        # No passphrase: see the note in the module docstring. An empty one is what makes this
        # non-interactive, and the file it writes is what the git-crypt-protected directory keeps.
        CryptoUtils.generate_gpg_key(name_email=gpg_email,
                                     private_key_filepath=str(private),
                                     public_key_filepath=str(public))
    os.chmod(private, PRIVATE_FILE_MODE)
    return (InitStep(GPG_PRIVATE_STEP, private, CREATED),
            InitStep(GPG_PUBLIC_STEP, public, CREATED))


def _import_gpg_key(source: Path, private_path: Path, public_path: Path) -> None:
    """Put the caller's own key in the directory, in both of the names the pair is read under.

    The secret half travels byte for byte — an operator who handed over a file should find that file,
    and not a re-encoding of it — and the public half is exported from it. Both questions go to gpg
    against a keyring that dies in this function's `finally`.
    """
    material = source.read_bytes()

    def import_and_export(gpg: gnupg.GPG) -> str:
        imported = gpg.import_keys(material)
        fingerprints = list(imported.fingerprints)
        if not imported or not fingerprints:
            raise click.ClickException(
                f"{source} is not a gpg key kubementat can import, so {private_path} stays as it is."
                f" {GPG_KEY_FLAG} takes a file `gpg --import` would accept — an armored secret key is"
                f" what this module writes {private_path.name} from. Nothing was generated in its"
                f" place, and {CREATE_COMMAND} is the verb that runs the import it could not run.")
        armor = gpg.export_keys(fingerprints[0])
        if not armor.strip():
            raise click.ClickException(
                f"{source} imported as a key but exported no public half, so {public_path} cannot be"
                f" written. {CREATE_COMMAND} needs a key that carries its own public key; nothing was"
                f" generated in its place.")
        return armor

    public_path.write_text(_in_disposable_keyring(import_and_export), encoding="ascii")
    private_path.write_bytes(material)
    return None


def _importable_key(gpg_key: Path | None) -> Path | None:
    """`gpg_key` as a path that is there, refused with the path when it is not.

    The decision belongs here and not at the write: a flag that names a missing file is a mis-typed
    path, and answering it by generating a key is how this ticket would be reported as done while the
    operator's own key was still sitting unimported where they put it.
    """
    if gpg_key is None:
        return None
    source = Path(gpg_key)
    if not source.is_file():
        raise click.ClickException(
            f"{source} was given as the deployer's gpg key and is not a file there, so nothing is"
            f" imported — and a generated key would be a key nobody asked for, wearing the name of the"
            f" one that is missing. Point {GPG_KEY_FLAG} at a real file, or leave it out and give"
            f" {GPG_EMAIL_FLAG} if a key should be generated; either way it is {CREATE_COMMAND} that"
            f" puts the pair into the directory.")
    return source


def _pair_steps(kind: str, paths: tuple[Path, Path], step_names: tuple[str, str],
                recognizers) -> tuple[InitStep, ...] | None:
    """Steps for a pair that is at least partly there already, or `None` when neither half is.

    Two files, two answers: both present means the pair is kept and reported per file (`SKIPPED` for
    material of the kind this module writes, `EXISTED` for material that is not), and neither present
    means there is nothing here for this function to say. Half a pair is the third case, and it is a
    refusal rather than a step — see `_half_pair_message`.
    """
    present = [path for path in paths if path.exists()]
    if not present:
        return None
    if len(present) != 2:
        there = present[0]
        missing = [path for path in paths if not path.exists()][0]
        raise click.ClickException(_half_pair_message(kind, there, missing))
    return tuple(InitStep(step_name, path, _action_of(path, recognize))
                 for step_name, path, recognize in zip(step_names, paths, recognizers))


def _half_pair_message(kind: str, there: Path, missing: Path) -> str:
    """Why a directory with one of a pair's two files in it stops the run.

    Both ways of continuing are false: generating the missing half overwrites the file that is there,
    because both generators write two files and not one, and leaving it out hands the operator a
    deployer whose halves came from different keys — which fails later, somewhere else, and with a
    sentence about signatures or permissions rather than this one.
    """
    return (f"{there} is here and {missing} is not, so {there.parent} holds half a {kind} keypair."
            f" Half a pair is not a pair: kubementat will not overwrite {there.name} to finish it and"
            f" will not make a second half that belongs to a different key. Put {missing.name} beside"
            f" it, or move {there.name} out and let {CREATE_COMMAND} make the pair in its place.")


def _missing_pair_message(directory: Path, missing: list[str]) -> str:
    """The sentence a caller of the guard puts in front of somebody who can fix it.

    Two shapes, because this message is read by a person who is about to go look for a file. When the
    directory holds **none** of the four, naming them twice — once as the missing set, once as the set
    they are missing out of — is a sentence that only looks informative, and it is what a live run of
    the guard printed before this shape existed. Name the set once, and say it is empty.
    """
    names = ", ".join(missing)
    if len(missing) == len(KEYPAIR_FILE_NAMES):
        where = (f"none of {', '.join(KEYPAIR_FILE_NAMES)}, the four files the deployer is made of, "
                 f"is there")
    else:
        where = (f"{names} {'is' if len(missing) == 1 else 'are'} missing out of the four files the "
                 f"deployer is made of ({', '.join(KEYPAIR_FILE_NAMES)})")
    return (f"{directory} has no deployer keypair: {where}. The git-deployer"
            f" Secrets and the git-crypt collaborator are both built from that pair, so the run stops"
            f" here and not three layers down in a client serializer. Prepare the pair with"
            f" {CREATE_COMMAND} {directory} --environment {_environment_words(directory)}"
            f" {GPG_EMAIL_FLAG} <email>, which is what puts these four names in a configuration"
            f" directory.")


def _environment_words(directory: Path) -> str:
    """The environment this directory configures, as words in a sentence.

    `require_deployer_keypair` is handed a directory and no environment, so the environment is either
    the one the directory itself declares — a layer is a directory that answers `static.json` — or it
    is a placeholder. Naming it from `directory.name` would be a guess with a plausible look: a tree
    called `platform_config` would be told it configures an environment called `platform_config`.
    """
    environment = _only_environment_inside(directory)
    return environment if environment else "<environment>"


def _only_environment_inside(directory: Path) -> str | None:
    """One name if exactly one environment layer sits inside this directory, otherwise `None`."""
    try:
        layers = sorted(entry.name for entry in directory.iterdir()
                        if entry.is_dir() and (entry / "static.json").is_file())
    except OSError:
        return None
    return layers[0] if len(layers) == 1 else None


def _in_disposable_keyring(work):
    """Run `work(gpg)` against a keyring built for this call and thrown away in the same breath.

    Reading a key's user ID and turning a secret key back into its public half are questions only gpg
    can answer, and both are answered by importing into a ring. The ring is a fresh temporary
    directory for the same reason the configuration directory is not one: a keypair that lands in the
    operator's own `~/.gnupg` is a side effect they did not ask for, and a `.gnupg` inside a
    configuration directory is a fifth name this module is not allowed to have.

    `gnupg.GPG` is told where the ring is rather than left to find one, so no call ever consults
    `GNUPGHOME` — which is the module's own version of the no-environment-reads rule.
    """
    home = tempfile.mkdtemp(prefix="kubementat-keypair-")
    os.chmod(home, 0o700)  # gpg refuses a world-readable home, loudly, on every call.
    try:
        try:
            gpg = gnupg.GPG(gnupghome=home, verbose=False)
        except (OSError, ValueError) as error:
            # `CryptoUtils` needs this binary too, so this is not a new dependency — it is the first
            # place that says so out loud when the binary is not on the machine.
            raise click.ClickException(
                f"the deployer keypair needs the `gpg` binary, which is not runnable here ({error})."
                f" {CREATE_COMMAND} writes the four keypair files through it.") from error
        return work(gpg)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _deployer_email(public_path: Path, private_path: Path) -> str | None:
    """The identity the pair carries, read from the public half and tried in the private one.

    The public file is asked first because it is the file whose whole job is to be handed to git and
    to git-crypt; the private half answers the same question about the same key, and is tried when a
    directory happens to hold only what an import left behind.
    """
    for path in (public_path, private_path):
        if not path.is_file():
            continue
        emails = _user_id_emails(path)
        if emails:
            return emails[0]
    return None


def _user_id_emails(path: Path) -> list[str]:
    """Every email among the key file's user IDs, in the order gpg lists them."""
    def read(gpg: gnupg.GPG) -> list[str]:
        imported = gpg.import_keys(path.read_bytes())
        if not imported:
            return []
        return [found.group(0)
                for key in gpg.list_keys()
                for uid in key.get("uids", [])
                for found in [_EMAIL_PATTERN.search(uid)]
                if found]

    return _in_disposable_keyring(read)


def _action_of(path: Path, recognized) -> str:
    """`SKIPPED` for the module's own kind of material, `EXISTED` for somebody else's.

    One line of the file is the whole test, because the difference between the two is not what is on
    disk — neither case is touched, and both move no byte — but whether this module was asked to
    ensure it. A rerun over material the first run wrote is `SKIPPED`; material that is not the kind
    of thing these four names take is foreign, and is reported as foreign rather than counted as
    work done or fixed by writing over it.
    """
    return SKIPPED if recognized(path) else EXISTED


def _is_private_key(path: Path) -> bool:
    head = _head_of(path)
    return head.startswith(b"-----BEGIN") and b"PRIVATE KEY" in head


def _is_ssh_public_key(path: Path) -> bool:
    head = _head_of(path)
    return any(head.startswith(algorithm.encode("utf-8")) for algorithm in _SSH_PUBLIC_ALGORITHMS)


def _is_gpg_private_key(path: Path) -> bool:
    return _head_of(path).startswith(b"-----BEGIN PGP PRIVATE KEY BLOCK-----")


def _is_gpg_public_key(path: Path) -> bool:
    return _head_of(path).startswith(b"-----BEGIN PGP PUBLIC KEY BLOCK-----")


def _head_of(path: Path) -> bytes:
    """The first bytes of a file, or nothing at all when the path is not a readable file.

    Every marker this module sniffs sits inside a file's first line, so the sniff is cheap and never
    claims that foreign material is its own. A directory wearing one of the four names reads as
    nothing, which is correct: it is `EXISTED`, and it is untouched.
    """
    if not path.is_file():
        return b""
    try:
        with open(path, "rb") as handle:
            return handle.read(_SNIFF_BYTES)
    except OSError:
        return b""
