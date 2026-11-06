"""A configuration directory, made *ready* to apply to a cluster, by one library call.

`config_init` lays the files down. `config_keys` makes the deployer's keypair exist. Both landed as
halves of a write pass that was going to replace `kmt initialize`, and both stopped one step short of
what the owner ruled on 2026-11-03: *"the `kmt config init` process should execute all necessary
steps to prepare the config directory for applying it to a cluster."* This module is that sentence.
It is a library function and not a verb — no CLI flag, no printing, no summary block.

**Nine steps, in this order.** The list this module implements, made concrete against the tree:

1. `git init` in `root`, skipped when `root/.git` is already there.
2. `.gitattributes`, with `*.encrypted.* filter=git-crypt diff=git-crypt`.
3. `.gitignore`, with the five key-material names and nothing else.
4. The layout and the answers: `config_init.init_configuration`, which already refuses
   `kubementat_main_dir` and already owns every decision about layers and names.
5. The deployer keypair: `config_keys.ensure_deployer_keypair`, whose own refusal when generation is
   asked for without an identity propagates here unchanged.
6. `git-crypt init` in `root`, skipped when the keystore is already there.
7. `git-crypt add-gpg-user --no-commit --trusted <FINGERPRINT>`, once per collaborator. The
   fingerprint comes out of the directory's own `gpg_public_key.gpg` and never out of a keyring
   listing, and `--trusted` is passed every time for the reason given below.
8. `git-crypt export-key <root>/git_crypt_symmetric.key`, chmod 0600.
9. `git add` and one commit — `chore: initialize kubementat configuration` — unless `commit=False`.

**Step 2 is before step 4, and that ordering is the ticket.** git-crypt encrypts on `git add`, by way
of the filter `.gitattributes` declares. A file added before the attribute exists goes into the
object store as the plaintext that was sitting in the work tree, and it stays there: the working copy
is decrypted later by a checkout filter, the history is never rewritten. The first-commit assert
— work tree plaintext, `git show HEAD:<path>` ciphertext — is the assertion that catches a swap, and
the lane's never-plaintext-history assert is the one that shows what the swap cost. So this is not a
preference between two equal arrangements, and the docstring says so rather than leaving it as a line
in a list. Everything that has to be in place before an encrypted file exists is in steps 2 and 3,
and step 4 stays exactly where `config_init` puts it: reordering the tree to suit the git-crypt steps
would be the change that quietly moves where a layer lives, and that is not this module's to make.

**What is never here: `git-crypt unlock`, `status`, or `check`.** kubementat reads and never
unlocks, and nothing in `src/` runs them. An unlock would be the
most plausible-looking wrong thing this function could do, because it makes the next command work —
while taking the checkout's guarantee away from the tree the operator just created, and while moving
the one place the lane's `git-crypt` clone task belongs. `status` and `check` are out for the same
reason and for one more: they answer a question about the keystore's state that this module answers
by looking at the filesystem, so using them would give the same fact two owners.

**Idempotence is about bytes and not about step counts.** A second call on a tree
nothing touched writes no byte, makes no commit, and registers nobody twice. Four filesystem marks
decide it, none of them a `git-crypt` invocation and none of them a parse of its output:

* `root/.git` — the repository exists, so step 1 did too. Checked as a path and not by asking git,
  because a directory that sits inside *another* repository must not be read as one: a configuration
  directory is exactly one repository, and a nested checkout is the mistake.
* `root/.git/git-crypt/keys/default` — the keystore exists. This is the master key `git-crypt init`
  itself writes (a single 0600 file under `.git/git-crypt/keys/` in git-crypt 0.7.0), so its presence
  is the record that step 6 ran. It is not `git-crypt status` — that would need parsing — and it is
  not `git_crypt_symmetric.key`, which is step 8's own write target and answers about a later moment
  in the dance.
* `.git-crypt/keys/default/0/<fingerprint>.gpg` — that collaborator exists. git-crypt stores a
  registered identity's public key in the work tree under exactly that name, so the file is the
  record step 7 ran *for the identity being asked about*, which is what makes the answer correct once
  a tree has more than one collaborator. The symmetric key's presence would say "someone is
  registered", not "you are".
* `root/git_crypt_symmetric.key` — the export happened. Re-exporting over an identical file is still
  a write, so this step is decided by the file and never by a step count.

**The deployer's fingerprint is read out of the tree, not out of a keyring.** `git-crypt` resolves
what it is told against the keyring of whoever ran the command, so resolving it there is what the
code used to do and that is the bug: `_keyring_fingerprints(identity)` returned a *list* for an
email, and when two configuration trees share an identity that list contains the other tree's key.
Registering `fingerprints[0]` then **succeeds** — git-crypt happily encrypts the symmetric key to a
key that cannot decrypt this directory's files — so the failure is not an error on screen, it is a
tree that installs and then cannot read its own secrets. Step 7 therefore asks `gpg --show-keys`
about `gpg_public_key.gpg`, the file that is sitting in the directory and is definitionally *this*
tree's key, and passes the fingerprint it gets. Two trees under one identity now get two different
keystore files, which is the only outcome that is not silently wrong.

**`--trusted` is passed every time.** A live `git-crypt add-gpg-user --no-commit <fpr>` of a
public-only key says `gpg: [stdin]: encryption failed: Unusable public key`, and the same command
with `--trusted` writes exactly one `.git-crypt/keys/default/0/<fpr>.gpg` and exits 0. Since the key
that reaches step 7 is an imported public half (see the next paragraph), it is the unusable shape in
the normal case, so the flag is not a special path for `--gpg-key` — it is step 7. And it is free:
measured before and after a `--trusted` registration, the ring's ownertrust listing is byte-for-byte
unchanged and `gpg --list-keys` still says `unknown`, because `--trusted` is git-crypt handing gpg a
per-invocation trust-model override and not a write to somebody's keyring. Conditional detection
would have been the clever shape and would have been a second answer to the same question; passing
it always is one answer.

**The key still has to be *in* the keyring for git-crypt to find it, and only the public half goes
there.** `git-crypt` says `Error: public key for '<fpr>' not found in your GPG keyring` when the ring
it reads does not hold the key, and the ring it reads is the operator's. So step 7 imports the
public half the directory already keeps — the file whose whole job is to be handed to git — and
refuses by name if that import is unreadable. What is never imported is the secret half. The
alternative was to refuse always and make `config init --gpg-key` dead on a clean machine; importing
a public key is what an operator would type by hand, and it is the difference between a directory
that is ready and one that merely looks it.

**The vocabulary is `config_init`'s.** `InitStep`, `CREATED`, `SKIPPED`, `EXISTED` are imported and
not copied, because two "what did this step do" shapes in one epic is one too many and the next
ticket then spends itself reconciling them. Steps come back in application order, which is the order
above. The three cases that decide a write target — absent, byte-identical, different — are decided
by the same function `add_environment` and `add_team` use (`config_init._apply_layer_plan`), and that
import is deliberate: the rule that an operator's own `.gitattributes` is refused rather than edited
is worth one owner, and a second implementation of it is how `force` ends up meaning different things
in two files.

**A run that can be refused is refused before the first byte.** `config_keys` promises that every one
of its refusals is decided before a single file is written, and a bare `config init <dir> --environment
E` with no `--gpg-key` and no `--gpg-email` used to break that promise from the outside: the check
landed at step 5, four steps after `git init` had made a repository and two more after the two git
files had been written, so the refusal left a half-prepared tree behind and the next run read that tree
as one that had already been initialized. So the identity question is decided ahead of step 1: neither
flag was given, and neither GPG file is in the directory, means refuse before `git init`, because a
refusal that leaves a tree the operator then has to delete is not a refusal. This is *only* the case
where a pair would have to be generated and no identity was given. A directory holding one half of a
pair is still `config_keys`' own refusal, and `--gpg-key` pointing at a missing file is still its own
sentence: neither of those is this function's to move earlier, and both answer a question about what
is in the directory rather than about what the operator forgot to type.

**One dependency finding, recorded rather than fixed.** `git-crypt` is not in
`automation.KUBEMENTAT_DEPENDENCIES`, which is what census K counted; this function does not add it,
because that list drives a lane that fails differently from the one this function is in. A directory
prepared on a machine without the binary fails at step 6 with git-crypt's own sentence, and the
ticket that puts a dependency check in front of `config init` is where the fix belongs.

"""

from __future__ import annotations

import os
from pathlib import Path

import click
import gnupg

from kubementat import config_init, config_keys
from kubementat.infrastructure import shell
# The same keyring `config_keys` asks its own two gpg questions against: created for the call,
# thrown away in the same `finally`. Rule 1 adds a third question of exactly that shape -- what
# fingerprint is in this file? -- and a second implementation of the ring would be a second owner
# for one fact, which is what this repo's own docstrings keep warning about.
from kubementat.config_keys import _in_disposable_keyring
from kubementat.config_init import CREATED, SKIPPED, InitStep, _apply_layer_plan

# The names of the steps nobody else does. `config_init`'s and `config_keys`' step names are reused
# for the work they do, so a printed line says which job was done and not which program was exec'd.
GIT_REPOSITORY_STEP = "git repository"
GITATTRIBUTES_STEP = "gitattributes"
GITIGNORE_STEP = "gitignore"
GIT_CRYPT_KEYSTORE_STEP = "git-crypt keystore"
GIT_CRYPT_COLLABORATOR_STEP = "git-crypt collaborator"
GIT_CRYPT_SYMMETRIC_KEY_STEP = "git-crypt symmetric key"
GIT_ADD_STEP = "configuration staged"
GIT_COMMIT_STEP = "configuration commit"

# Step 2's whole content. `filter` is what encrypts on `git add`; `diff` is what stops a `git diff`
# from dumping ciphertext at whoever asked. Nothing else belongs in this file: git-crypt writes its
# own `.git-crypt/.gitattributes` to keep its keystore out of the filter, so a rule for the keystore
# here would be a second claim about a file this module does not own. An inline constant rather than
# a template asset, because there is no template to fill and `gitattributes.tpl` is not a path in
# this tree — a string that has to be looked up is a string that can be looked up wrong.
GITATTRIBUTES_BODY = b"*.encrypted.* filter=git-crypt diff=git-crypt\n"

# git-crypt's export target. `config_keys` names the four deployer files and deliberately does not
# name this one: it belongs to git-crypt and to nobody's keypair.
GIT_CRYPT_SYMMETRIC_KEY_NAME = "git_crypt_symmetric.key"

# Step 3's list, and the set of root paths step 4 is told are kubementat's own. Four of the five come
# from `config_keys`, so the ignore rules and the files that get ignored cannot drift apart. No
# rule is added "in case": `**/.helmfile/` and `**/.helmcache` are left out because
# `HelmUtils.helmfile_paths` runs helmfile in the packaged `assets/values/` directory, so the
# configuration tree is never where they land, and `.git-crypt/` is left out because the keystore is
# tracked by design.
KEY_MATERIAL_NAMES = (config_keys.SSH_PRIVATE_NAME, config_keys.SSH_PUBLIC_NAME,
                      config_keys.GPG_PRIVATE_NAME, config_keys.GPG_PUBLIC_NAME,
                      GIT_CRYPT_SYMMETRIC_KEY_NAME)

GITIGNORE_BODY = (
    "# kubementat's own key material: in the configuration directory, never in git.\n"
    + "".join(f"{name}\n" for name in KEY_MATERIAL_NAMES)
).encode("utf-8")

# Where git-crypt keeps what it knows. Every one of these is read as a path, which is the point: the
# alternative is `git-crypt status`, which is about reading and not unlocking, while parsing a
# tool's listing is a softer answer about the same state than the file that proves it.
KEISTORE_KEY_PATH = (".git", "git-crypt", "keys", "default")
COLLABORATOR_KEY_DIRECTORY = (".git-crypt", "keys", "default", "0")

CONFIGURATION_COMMIT_MESSAGE = "chore: initialize kubementat configuration"

# The committer identity `config init` falls back to, and the only place these two strings are written.
# Both are needed because git refuses a commit when either is missing, and it says so with exit 128 and
# a message about `git config --global` that sends the operator to the wrong file.
CONFIG_COMMITTER_NAME = "kubementat"
CONFIG_COMMITTER_EMAIL = "kubementat@localhost"

GIT_BINARY = "git"
GIT_CRYPT_BINARY = "git-crypt"


def prepare_configuration_directory(root, environment, team=None, *, answers=(),
                                    encrypted_answers=(), gpg_key=None, gpg_email=None,
                                    commit=True, force=False) -> tuple[InitStep, ...]:
    """Turn an empty directory into a git repository whose encrypted files are already encrypted.

    Parameters
    ----------
    root : Path
        The configuration directory. Made if it is not there, because `git init` needs a place to
        stand and `init_configuration` would have created it anyway one layer down. Resolved on the
        way in, so every path this function hands back and every path it spells into an argv name
        the same directory once, whatever the operator typed.
    environment, team : str
        Handed to `config_init.init_configuration`, which owns names, layers, and the refusal of
        `kubementat_main_dir`. Nothing here re-checks them.
    answers, encrypted_answers : tuple of (str, object)
        The environment layer's answers, in order. The encrypted ones are what git-crypt is for.
    gpg_key : Path, optional
        The deployer's own secret key file, passed to `config_keys` for import.
    gpg_email : str, optional
        The identity to generate a keypair under. `config_keys` refuses a generation without one and
        this module lets that refusal through as it came — and asks the same question before step 1,
        so a directory that would have to be refused at step 5 is refused while it is still empty.
    commit : bool
        Make the first commit. `False` stages everything and commits nothing, which is what an
        operator who wants to read the tree before the history starts asked for.
    force : bool
        Passed to `init_configuration`, where it means exactly one thing: accept material kubementat
        did not write. It is not a way past a `.gitattributes` somebody else configured, and this
        function never passes it for that.

    Returns
    -------
    tuple of InitStep
        In application order: the repository, the two git files, `init_configuration`'s steps, the
        four keypair steps, git-crypt's three, then the commit.

    Raises
    ------
    click.ClickException
        Everything `init_configuration` and `config_keys` refuse for, plus this module's own three: a
        keypair that would have to be generated and no identity to generate it under, refused before
        `git init` so nothing is left behind; a `.gitattributes` or `.gitignore` that is present and
        says something else (never lifted by `force`); and a step 7 whose keyring question neither the
        running keyring nor the directory's own public half can answer.
    """
    root = Path(root).resolve()

    # Resolved here and once, and the reason is the execs. Steps 1, 8 and 9 run with `cwd=root`, so a
    # path spelled into their argv against the *calling* directory resolves twice: `kmt config init
    # ./cfg` reached step 8 and asked git-crypt for `cfg/cfg/git_crypt_symmetric.key`. A prepared tree
    # is therefore named absolutely everywhere it is read and everywhere it is printed, which is also
    # the only spelling that can be true about a file that is written by a process started inside it.
    # Decided before step 1 and before `root` is made: see the docstring's paragraph on refusing.
    _refuse_without_an_identity(root, gpg_key=gpg_key, gpg_email=gpg_email)

    steps: list[InitStep] = []
    steps.extend(_git_repository(root))
    steps.extend(_git_attributes(root))
    steps.extend(_git_ignore(root))

    # The paths step 4 must not call foreign. On a *prepared* tree they are there — this module wrote
    # the two git files, step 5 wrote the four keypair files, and step 8 wrote the symmetric key, so
    # a second call would otherwise read its own output as somebody else's material and refuse. That
    # refusal gets louder the more successfully init has already run, which is how idempotence dies.
    # Declared rather than `force`d, because `force` accepts anything it cannot name.
    own = tuple(root / name for name in KEY_MATERIAL_NAMES)
    own += (root / ".gitattributes", root / ".gitignore")
    steps.extend(config_init.init_configuration(
        root=root, environment=environment, team=team, answers=answers,
        encrypted_answers=encrypted_answers, force=force, own_paths=own))

    # Unchanged on purpose: the sentence that names `--gpg-email` is already the right sentence, and
    # wrapping it in a new exception is how the command that fixes it gets lost.
    steps.extend(config_keys.ensure_deployer_keypair(root, gpg_key=gpg_key, gpg_email=gpg_email))

    steps.extend(_keystore(root))
    steps.extend(_collaborator(root))
    steps.extend(_symmetric_key(root))
    steps.extend(_commit(root, commit=commit))
    return tuple(steps)


# ---------------------------------------------------------------------------------------------
# The decision made ahead of every write, and the refusal that comes out of it.
# ---------------------------------------------------------------------------------------------

def _refuse_without_an_identity(root: Path, *, gpg_key, gpg_email) -> None:
    """Refuse to prepare a directory that has no deployer identity to be prepared around.

    Two questions decide it, and both are about the directory and the flags and not about any git
    state: would a GPG pair have to be *generated*, and is there an identity to generate one
    under? When both answers are yes there is nothing for step 5 to do except refuse, so refusing now
    is free and refusing there costs the operator a tree. When the directory already holds a GPG file
    the answer is not nothing but `config_keys`' own — it reads the identity back off the key and
    needs no flag for it, which is what makes a second run on a prepared tree quiet.

    The sentence is `config_keys`' and not a new one, because the two flags it names are the fix in
    both cases and a refusal that cannot be typed as an answer is a refusal that gets worked around.
    """
    if gpg_key is not None or gpg_email:
        return
    private, public = (root / config_keys.GPG_PRIVATE_NAME, root / config_keys.GPG_PUBLIC_NAME)
    if private.is_file() or public.is_file():
        return
    raise click.ClickException(
        f"{root} holds no deployer gpg key — neither {config_keys.GPG_PRIVATE_NAME} nor"
        f" {config_keys.GPG_PUBLIC_NAME} is here — and no identity was given, so there is no key to"
        f" generate and none to import. {config_keys.CREATE_COMMAND}'s {config_keys.GPG_EMAIL_FLAG}"
        f" <email> (or {config_keys.GPG_KEY_FLAG} <file>, to skip generation entirely) is the answer"
        f" this module cannot give for itself: deriving it from an environment variable is what"
        f" initialize did, and the caller owns that answer. Nothing was written and no"
        f" `git init` ran — a refusal leaves the directory exactly as it was found.")


# ---------------------------------------------------------------------------------------------
# Steps 1, 8, 9: the three git calls.
# ---------------------------------------------------------------------------------------------

def _git_repository(root: Path) -> tuple[InitStep, ...]:
    """`git init`, once. A directory that is already a repository stays one, quietly.

    Nothing re-inits and nothing complains: a repository that is there was made by an earlier run of
    this function or by the operator, and both are the state this function wants. The check is a path
    and not `git rev-parse`, because asking git answers a different question — "is this inside a
    repository" — and a configuration directory that happens to sit under somebody's checkout is a
    nested repository and not a lane that can clone.
    """
    if not root.is_dir():
        root.mkdir(parents=True)
    git_dir = root / ".git"
    if git_dir.exists():
        return (InitStep(GIT_REPOSITORY_STEP, git_dir, SKIPPED),)
    shell.run([GIT_BINARY, "init"], cwd=root)
    return (InitStep(GIT_REPOSITORY_STEP, git_dir, CREATED),)


def _commit(root: Path, *, commit: bool) -> tuple[InitStep, ...]:
    """Stage, then commit exactly when there is something this run added to the history.

    Two exit-code answers and no output captured: does HEAD exist, and does the work tree differ from
    it. No HEAD means this is the first commit and it is made; a tree that matches HEAD means this run
    wrote nothing and has nothing to say about it, so no `git add` either — running it would rewrite
    `.git/index` for a step that reported `skipped`.

    `--no-commit` is why step 7 does not commit and this step does. `git-crypt add-gpg-user` commits
    what it adds, and letting it is the mistake this project already made once in `initialize`: it
    fails on a tree with no git identity, it lands `Add 1 git-crypt collaborator` commits in a history
    whose own closing instruction is that the operator commits, and it leaves keystore state that
    makes the next run read a step as done that never was. So kubementat commits once here, with a
    message that says what it is.
    """
    first_commit = shell.run([GIT_BINARY, "rev-parse", "--verify", "--quiet", "HEAD"],
                             cwd=root, check=False).returncode != 0
    if not first_commit:
        unchanged = shell.run([GIT_BINARY, "diff", "--quiet", "HEAD"],
                              cwd=root, check=False).returncode == 0
        if unchanged:
            return (InitStep(GIT_ADD_STEP, root, SKIPPED),
                    InitStep(GIT_COMMIT_STEP, root, SKIPPED))
    shell.run([GIT_BINARY, "add", "-A"], cwd=root)
    if not commit:
        return (InitStep(GIT_ADD_STEP, root, CREATED), InitStep(GIT_COMMIT_STEP, root, SKIPPED))
    # This verb runs `git init` and then commits, so a tree with no committer identity anywhere is a
    # tree this function created and cannot finish in. The identity is set *locally* and only when none
    # exists, for two reasons: `--global` would change a machine the operator configured on purpose, and
    # a name that is not the operator's must never silently follow their commits. So this says who
    # kubementat is, on the one commit kubementat makes, and leaves everything after it to whoever the
    # repository belongs to.
    # Both halves have to be present, and asking for one and setting one is how a fallback like this
    # goes quietly wrong: git refuses the commit when either is unset, and a tree that had a name and no
    # address -- or the reverse, which is a real state on a machine someone set up by hand -- would
    # reach the same exit 128 this block exists to prevent.
    missing = [field for field in ("user.name", "user.email")
               if shell.run([GIT_BINARY, "config", field], cwd=root, check=False).returncode != 0]
    if missing:
        if "user.name" in missing:
            shell.run([GIT_BINARY, "config", "user.name", CONFIG_COMMITTER_NAME], cwd=root)
        if "user.email" in missing:
            shell.run([GIT_BINARY, "config", "user.email", CONFIG_COMMITTER_EMAIL], cwd=root)
    shell.run([GIT_BINARY, "commit", "-m", CONFIGURATION_COMMIT_MESSAGE], cwd=root)
    return (InitStep(GIT_ADD_STEP, root, CREATED), InitStep(GIT_COMMIT_STEP, root, CREATED))


# ---------------------------------------------------------------------------------------------
# Steps 2 and 3: the two files that must exist before anything can be encrypted.
# ---------------------------------------------------------------------------------------------

def _git_attributes(root: Path) -> tuple[InitStep, ...]:
    """Declare the filter, before there is an encrypted file to declare it for.

    The same three cases every other write target gets, applied by the same function `config_init`
    uses for a layer's files: absent → `created`, byte-identical → `skipped`, different → refused. The
    third is the one that matters here and the reason this is not a `write_bytes`. A `.gitattributes`
    somebody else configured is refused by name and never edited, because kubementat cannot tell a
    deliberate filter rule from a stale one, and `force` does not lift it: the flag is about foreign
    *material* and never about a file this function was not asked to change.
    """
    return tuple(_apply_layer_plan(
        [(GITATTRIBUTES_STEP, root / ".gitattributes", GITATTRIBUTES_BODY)]))


def _git_ignore(root: Path) -> tuple[InitStep, ...]:
    """The five key-material names, and nothing else.

    Census L's list is the authority, so no rule is added "in case". The two candidates that were
    considered and left out are recorded in the constant above: helmfile's cache directory never
    lands in a configuration tree, and the keystore is tracked on purpose.
    """
    return tuple(_apply_layer_plan([(GITIGNORE_STEP, root / ".gitignore", GITIGNORE_BODY)]))


# ---------------------------------------------------------------------------------------------
# Steps 6, 7 and 8: git-crypt's half, and what each one checks before it execs.
# ---------------------------------------------------------------------------------------------

def _keystore(root: Path) -> tuple[InitStep, ...]:
    """`git-crypt init`, skipped when the master key is already where git-crypt puts it.

    The detection is a filesystem read of the file `git-crypt init` writes, and it is not `git-crypt
    status` for two reasons: kubementat reads and never unlocks, and a listing this
    module has to parse would give the keystore's state a second, softer owner. Neither detection
    uses `git_crypt_symmetric.key` either — that is step 8's write target, and it tells you about a
    later moment in the dance than "was the keystore initialized".
    """
    keystore = root.joinpath(*KEISTORE_KEY_PATH)
    if keystore.exists():
        return (InitStep(GIT_CRYPT_KEYSTORE_STEP, keystore, SKIPPED),)
    shell.run([GIT_CRYPT_BINARY, "init"], cwd=root)
    return (InitStep(GIT_CRYPT_KEYSTORE_STEP, keystore, CREATED),)


def _collaborator(root: Path) -> tuple[InitStep, ...]:
    """Register the deployer once, by the fingerprint this directory's own key file answers.

    No identity is asked of anything here, and that is the whole rule. The flag answers *what to
    generate*, which is a different question that step 5 already answered; a collaborator is named by
    the key that is in the directory. Resolving that key from a keyring **listing** is what broke two
    trees under one email — the listing holds both keys, `fingerprints[0]` is one of them, and the
    one it picks can be the other tree's, which registers without erroring and leaves a directory
    whose secrets it cannot decrypt. So the fingerprint is read from `gpg_public_key.gpg` itself,
    against a keyring that is created for the question and thrown away in the same `finally`, and
    git-crypt is handed that fingerprint.

    The keystore's own file for that fingerprint is what says whether it already happened. Skipping
    is not cosmetic — a re-registered collaborator is a write, and a step that reports `skipped` over
    a key the run added anyway is the kind of agreement that hides the bug. Two trees under one
    identity therefore skip and register on their own fingerprints and cannot collide, because the
    files being checked are named after keys and not after emails.
    """
    key_directory = root.joinpath(*COLLABORATOR_KEY_DIRECTORY)
    fingerprint = _directory_fingerprint(root)

    registered = key_directory / f"{fingerprint}.gpg"
    if registered.is_file():
        return (InitStep(GIT_CRYPT_COLLABORATOR_STEP, registered, SKIPPED),)

    _import_the_public_half(root, fingerprint)
    # `--trusted` here, and always: the key that reaches this line is a public-only import, which
    # gpg will not encrypt to until told to trust it, and the flag is measured to persist nothing.
    # See the module docstring.
    shell.run([GIT_CRYPT_BINARY, "add-gpg-user", "--no-commit", "--trusted", fingerprint], cwd=root)
    return (InitStep(GIT_CRYPT_COLLABORATOR_STEP, key_directory, CREATED),)


def uses_git_crypt(config_dir) -> bool:
    """Whether this directory declares that git-crypt owns some of its files.

    The marker is `.gitattributes` naming the filter, because that is the one git-crypt fact that
    **travels**. `config init` step 2 writes it and commits it — step 2 sits before step 4 precisely so
    `git add` encrypts — so it is in a clone; while `git-crypt init`'s keystore lives under `.git/`, which
    is not cloned, and `git_crypt_symmetric.key` is gitignored, which is what made this ticket necessary.
    A predicate built on the keystore or on the key file would answer "no" to exactly the tree that needs
    unlocking.

    Checked by reading the file and not by asking git-crypt, for the reason this module gives twice
    already: kubementat reads and never unlocks, and `status` or `check` would need parsing and
    would put a second owner on a question this module already answers from the filesystem.

    This answers "is the configuration encrypted", which is not the same question as "is it unlocked".
    A directory can answer yes here and still be unreadable, and the failure for that is the pipeline's
    unlock step, not this predicate.
    """
    attributes = Path(config_dir) / ".gitattributes"
    try:
        body = attributes.read_text(encoding="utf-8")
    except OSError:
        return False
    return "filter=git-crypt" in body


def require_symmetric_key(config_dir) -> Path:
    """The exported key that unlocks this configuration directory, or a refusal naming where it comes from.

    `setup_pipelines` needs the file to build the Secret a pipeline's unlock step reads, and
    this is the first place in the product that reads it back rather than writing it. Nothing else asked:
    what was written about this file called it "exported, gitignored, never read back", and that is true
    of every other caller and false about this one, so say it here where the next reader will find it
    rather than letting
    a docstring elsewhere go stale about a file that now has two consumers.

    The check is that the file exists. It does not exec `git-crypt`, does not unlock anything, and does not
    look at a keyring: kubementat reads and never unlocks, and this function's whole job is to
    hand a file's bytes to a caller about to put them in a Kubernetes Secret. Whether the key is *the* key
    is answered later, by git-crypt, in the step that reads the Secret, and that is not
    kubementat's answer to give.

    The environment is named by the helper `require_deployer_keypair` uses, so the two refusals for two
    missing key files cannot disagree about whether the directory knows which environment it configures.
    When it cannot tell, the sentence says that instead of inventing a name: this is an instruction, and an
    instruction with a hole in it is a bug the operator gets to debug.

    Raises
    ------
    click.ClickException
        The file is absent. A tree prepared before step 8 existed, or a directory someone copied without
        its gitignored material, both present as this one missing file.
    """
    directory = Path(config_dir)
    path = directory / GIT_CRYPT_SYMMETRIC_KEY_NAME
    if not path.is_file():
        environment = config_keys._environment_words(directory)
        which = (f"--environment {environment}" if environment != "<environment>"
                 else "--environment <environment>, which this directory does not declare for itself")
        raise click.ClickException(
            f"{path} is not there, so kubementat cannot give a pipeline the ability to open the "
            f"configuration in {directory}. This is the file `git-crypt unlock` reads, and `kmt config "
            f"init {directory} {which}` exports it as its eighth step and then gitignores it. That is why "
            f"a clone of this repository carries no key, which is why the clone `git-clone-configuration` "
            f"makes arrives locked, and which is why the answer is to run init here rather than to copy a "
            f"key in from somewhere else.")
    return path


def _symmetric_key(root: Path) -> tuple[InitStep, ...]:
    """Export the key that unlocks the directory, readable by its owner alone.

    `chmod 0600` is not decoration: this file is the ability to read every credential in the cluster,
    and git-crypt writes it through whatever umask the run had. Step 3's ignore rule is what keeps it
    out of the commit; the mode is what keeps it out of the next account's way. Skipped on the file,
    because exporting over a file that is already there is a write and not a no-op.
    """
    path = root / GIT_CRYPT_SYMMETRIC_KEY_NAME
    if path.is_file():
        return (InitStep(GIT_CRYPT_SYMMETRIC_KEY_STEP, path, SKIPPED),)
    shell.run([GIT_CRYPT_BINARY, "export-key", str(path)], cwd=root)
    os.chmod(path, 0o600)
    return (InitStep(GIT_CRYPT_SYMMETRIC_KEY_STEP, path, CREATED),)


# ---------------------------------------------------------------------------------------------
# The keyring, which is where git-crypt looks and where `config_keys` deliberately does not.
# ---------------------------------------------------------------------------------------------

def _directory_fingerprint(root: Path) -> str:
    """The one fingerprint this directory is about, read from its own key file.

    `gpg --with-colons --show-keys <file>` answers the question without adding the key anywhere and
    without reading any keyring, so the answer cannot depend on who else has used this machine and
    cannot return two keys because an email is shared. The ring the question is asked against is
    created for the call and deleted in the same `finally`, which is `config_keys`' pattern for its
    two questions and is the reason the answer is never somebody's `~/.gnupg`.

    A file that will not yield a fingerprint is refused by name rather than guessed at, because every
    other thing step 7 could do with a half-read key — register the identity, take the first
    fingerprint from a listing, skip quietly — is the failure this function exists to remove.
    """
    public_path = root / config_keys.GPG_PUBLIC_NAME
    if not public_path.is_file():
        raise click.ClickException(
            f"{public_path} is not there, so step 7 has no fingerprint to register."
            f" {config_keys.CREATE_COMMAND}'s {config_keys.GPG_KEY_FLAG} <file> puts both halves of a"
            f" keypair in the directory, which is what this step reads its collaborator's name from.")

    def read(gpg: gnupg.GPG) -> str:
        # `--import --import-options show-only` is `--show-keys`: gpg reads the file and answers
        # without importing it, so no key lands anywhere and the answer cannot come from a listing.
        answer = gpg.import_keys(public_path.read_bytes(),
                                  extra_args=["--import-options", "show-only"])
        for line in (answer.data or b"").decode("utf-8", "replace").splitlines():
            fields = line.split(":")
            if fields[0] == "fpr" and len(fields) > 9 and fields[9]:
                return fields[9]
        return ""

    fingerprint = _in_disposable_keyring(read)
    if not fingerprint:
        raise click.ClickException(
            f"{public_path} is not a gpg key this run can read a fingerprint from, so git-crypt has"
            f" no collaborator to register. Nothing was registered and nothing else was touched;"
            f" kmt config init --gpg-key <file> is the verb that puts a key which can answer for"
            f" itself into the directory.")
    return fingerprint


def _import_the_public_half(root: Path, fingerprint: str) -> None:
    """Put the directory's own public key into the running keyring, and nothing else.

    `gpg_public_key.gpg` is the file whose job is to be handed to git: the deployer's public half,
    which is the whole thing a collaborator is. Importing it is what an operator would type by hand
    (`gpg --import gpg_public_key.gpg`); importing the *secret* half would be a different act and is
    not done here, for the reason `config_keys` gives about private keys landing in somebody's
    keyring. The public half is the half that is meant to land there.

    The fingerprint is a name and not a lookup: it is what the caller already read out of this same
    file, and it is what the refusal names so the operator can point at the key the run could not
    place rather than at an email that may or may not be the one in the directory.

    A failure is refused and not reported, because the thing that must not happen is step 7 skipping
    quietly: a directory that is ready except for its collaborators still looks ready, and the next
    operator to read that tree in a second checkout is the one who finds out.
    """
    public_path = root / config_keys.GPG_PUBLIC_NAME
    if not public_path.is_file():
        return
    try:
        result = gnupg.GPG(verbose=False).import_keys(public_path.read_bytes())
    except (OSError, ValueError) as error:
        raise click.ClickException(
            f"{public_path} cannot be imported into the keyring this run is using ({error})."
            f" git-crypt resolves the deployer's key there, so step 7 cannot be completed from the"
            f" file that is present. Nothing was registered, and nothing else was touched.") from error
    if not result:
        raise click.ClickException(
            f"{public_path} is not a gpg key the keyring can hold, so {fingerprint!r} cannot be"
            f" registered as a git-crypt collaborator. kmt config init --gpg-key <file> puts a key"
            f" that does import into the directory in the first place.")
