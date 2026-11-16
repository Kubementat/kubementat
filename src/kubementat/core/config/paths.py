"""Where kubementat's two roots come from, and the one case where it refuses to have one.

Two roots and one precedence. `default_kubementat_main_dir` is the **output** root: the tree a run
writes `tekton_ci/pipeline-runs/` into, answered by `KUBEMENTAT_HOME` when the operator set it and by
the location of this file otherwise. `default_platform_config_dir` is the **configuration** root, and
it is named or it is refused — the `--config-dir` flag a command carries, or the variable a container
already sets, or the one tree `kmt config init` prepared for this user, and with none of them, one
sentence and no run. `resolve_platform_config_dir` is the precedence between the argument and the
variable, spelled once here because `kmt environments list` used to spell it a second time beside its
own `EnvironmentRegistry`; agreement between two owners is not one rule, and it rots the day either
spelling is edited.

The third layer is what lets the flag be typed once instead of eleven times, and it is the reason this
module carries a record. `kmt config init` prepares a tree that nothing else in the product prepares,
so it is the one verb that may say "this directory is the configuration root" — and it says it by
writing a line under the user's home, where the fact is **recorded** and never **located**. That
distinction is the whole safety argument: nothing here walks up a directory tree looking for a
`platform_config`, nothing reads the checkout the wheel came from, and a machine that has prepared two
trees gets the refusal it got before this layer existed. A remembered root with a rival is not a
default, and the failure mode a recorded default would otherwise create is a command silently reading
the wrong tree — so the record sits *below* both spellings that already outrank it, never above them,
and the ambiguity and the nothing-recorded cases both still answer with
`ConfigurationRootNotNamedError`'s own sentence.

`_directory_from_environment` is the rule both roots share and the reason this is a module and not two
one-liners: a variable that is set to nothing is not "unset", and falling back to the built-in default
at that point reads configuration out of site-packages while saying nothing about it. So it raises, and
the empty case is refused in one place instead of in each root's own words. The record file's own
override is refused by the same rule, for the same reason.

What this module is not. It holds no `Config` and reads no layered configuration. The only fact it
carries about the filesystem is the depth of this file, which is the whole arithmetic
`default_kubementat_main_dir` answers with and which travels with it — `Path(__file__).parents[4]`, the
same number the function had beside it, because this module sits in the directory the function left.
"""

import json
import os
from pathlib import Path

from kubementat.errors import ConfigurationRootNotNamedError, RootDirectoryVariableIsEmptyError

# The file that remembers which configuration trees `kmt config init` prepared for this user, and the
# override that says where it is. It is a **file** under the user's own home and not a directory that
# gets discovered: a recorded root is a fact somebody stated once, while a located root is a guess
# about where a wheel happened to land, which is the D11 bug `default_platform_config_dir` deleted. An
# unset override therefore means "this user's record is where it always is", and setting it to nothing
# is refused by the same rule `KUBEMENTAT_HOME` and `KUBEMENTAT_PLATFORM_CONFIG_DIR` answer with — a
# variable set to nothing is not a variable left unset.
CONFIGURED_ROOTS_ENV_VAR = "KUBEMENTAT_CONFIGURED_ROOTS"
CONFIGURED_ROOTS_RECORD = Path(".config") / "kubementat" / "configuration_roots.json"

# `Path(__file__).parents[4]` is the repository root for a checkout and *site-packages*
# for an installed wheel, so it is only a default and never the answer on its own (D11).
# Four, not two, because this module sits two directories below the package root: the
# count is the whole arithmetic the function depends on, and it travels with the file.
# The environment variables below are the override: the same tree can be read from
# anywhere, which is what the CI container needs (`kmt` in site-packages, checkout in
# /workspace).
KUBEMENTAT_HOME_ENV_VAR = "KUBEMENTAT_HOME"
KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR = "KUBEMENTAT_PLATFORM_CONFIG_DIR"

# What `default_platform_config_dir` answers when nothing at all names a root — the sentence every
# version of this rule has had, spelled once because the record layer under it has to be able to say
# more than that without restating it. It is still exactly true on a machine that has recorded
# nothing, and the record cases below quote it first and then add what the record had to do with it.
_NO_ROOT_SENTENCE = (
    f"no configuration root is named. Give this command --config-dir PATH, or set "
    f"{KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR} to the directory that holds the "
    f"environments' static.json. kubementat does not read configuration out of the directory "
    f"it was installed from, and it has no default root to fall back to."
)


def _directory_from_environment(name):
    '''
    Reads the directory `name` names from the environment.

    Returns
    -------
    Path or None
        None when the variable is not set at all, meaning "use the default".

    Raises
    ------
    RootDirectoryVariableIsEmptyError
        If the variable is set but empty. Falling back to the default there would be the
        D11 failure again, silently: an empty `KUBEMENTAT_HOME` would read config out of
        site-packages and say nothing about it.
    '''
    value = os.environ.get(name)
    if value is None:
        return None
    if not value.strip():
        raise RootDirectoryVariableIsEmptyError(
            f"{name} is set but empty. Give it the path of the directory it names, "
            f"or unset it to use the built-in default."
        )
    return Path(value).resolve()


def default_kubementat_main_dir() -> Path:
    '''
    The project root when no caller asked for a particular one: `KUBEMENTAT_HOME` if it is
    set, the checkout this module lives in otherwise. An explicit `kubementat_main_dir`
    argument outranks both — see `Config.__init__`, which is where that precedence lives.
    '''
    return _directory_from_environment(KUBEMENTAT_HOME_ENV_VAR) or Path(__file__).resolve().parents[4]


def default_platform_config_dir() -> Path:
    '''
    The configuration root when no caller named one: the variable, then the one tree this user has
    recorded, and otherwise nothing.

    A configuration root is **named, never located**. Three things name it — the `--config-dir` flag a
    command carries, the variable a container already sets, and the tree `kmt config init` prepared for
    this user — and when none of them is present this function refuses. It used to answer with a
    `platform_config` directory located under `default_kubementat_main_dir()`, which meant a command
    that had been told nothing still answered, from wherever the wheel happened to be installed. That
    answer is the D11 bug in its last remaining form, and v2 is a rewrite rather than a migration, so
    there is no compatibility branch behind it: the operator gets one line naming the spellings, and
    the run stops before it reaches a cluster.

    **The recorded layer is what makes the flag typeable once, and it is deliberately the last
    thing consulted.** `kmt config init` is the only verb that lays a tree down, so it is the only verb
    allowed to say afterwards which tree it made; the record is that sentence, kept in one file under
    the user's home (`record_configuration_root`). It is not a search: this function never walks up a
    directory looking for a `platform_config`, and a machine that has recorded two live trees gets the
    same refusal it got before the record existed, because choosing between two plausible trees by
    whichever entry was written first is the silent-default bug wearing a memory instead of a wheel.

    `kubementat_main_dir` is not consulted. It names the tree a checkout hangs its *output* off
    (`tekton_ci/pipeline-runs/`); everything the product ships — `templates/`, the Tekton Tasks and
    Pipelines, the generator templates — is package data and reaches no root at all.
    An asset root is a different question with a different override;
    letting it stand in for the configuration root is how one command ended up reading two trees
    while believing it read one.

    Returns
    -------
    Path
        The root the variable names, resolved; or the single recorded root that still exists.

    Raises
    ------
    ConfigurationRootNotNamedError
        If nothing names a root, with the sentence above verbatim; if more than one recorded root is
        still there, which is the ambiguity this layer must not resolve; or if the only recorded root
        is gone, which is not "nothing recorded" and says so.
    RootDirectoryVariableIsEmptyError
        If `KUBEMENTAT_PLATFORM_CONFIG_DIR` is set but empty. The empty case is
        `_directory_from_environment`'s rule verbatim: falling back when someone set the variable to
        nothing is the silent default again, just further from view.
    '''
    declared = _directory_from_environment(KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR)
    if declared is not None:
        return declared

    # Nothing was named, so the only root this run may read is the one somebody already stated. One
    # live tree answers; two or more is the case this layer exists to refuse, and it must not be
    # resolved by preferring whichever entry was written first.
    recorded, gone = _recorded_configuration_roots()
    if len(recorded) > 1:
        raise ConfigurationRootNotNamedError(
            f"{_NO_ROOT_SENTENCE} {len(recorded)} configuration trees are recorded for this user — "
            f"{' and '.join(str(root) for root in recorded)} — so naming one is this command's job "
            f"and not this function's. Give --config-dir whichever of those this run means."
        )
    if len(recorded) == 1:
        return recorded[0]
    if len(gone) == 1:
        raise ConfigurationRootNotNamedError(
            f"{_NO_ROOT_SENTENCE} The one tree recorded for this user is not there: "
            f"{gone[0]}. kmt config init records a tree when it prepares one; name the directory that "
            f"replaced it with --config-dir, or prepare one again."
        )
    if gone:
        raise ConfigurationRootNotNamedError(
            f"{_NO_ROOT_SENTENCE} None of the {len(gone)} trees recorded for this user are there "
            f"anymore: {', '.join(str(root) for root in gone)}. kmt config init records a tree when "
            f"it prepares one; name the directory that replaced it with --config-dir."
        )
    raise ConfigurationRootNotNamedError(_NO_ROOT_SENTENCE)


def configured_roots_record_path() -> Path:
    '''
    Where the record of prepared configuration trees is kept, whether or not it exists yet.

    `KUBEMENTAT_CONFIGURED_ROOTS` overrides it for the cases where the user's own home is not the
    right place: a test suite that must not read a developer's own record, a container that wants the
    record inside a scratch tree, a scheduled run that wants it somewhere else. It names a file and
    not a directory, because what it holds is one JSON array and not a tree, and the empty case is
    refused for the same reason an empty `KUBEMENTAT_PLATFORM_CONFIG_DIR` is — refused before anything
    is read, so an override set to nothing cannot quietly fall back to somebody else's memory.

    Returns
    -------
    Path
        Where the record is. Nothing in this function creates it; only `record_configuration_root`
        writes, and it writes when a tree has just been prepared.

    Raises
    ------
    RootDirectoryVariableIsEmptyError
        If the variable is set to nothing, which is a different claim from leaving it unset.
    '''
    declared = os.environ.get(CONFIGURED_ROOTS_ENV_VAR)
    if declared is None:
        return Path.home() / CONFIGURED_ROOTS_RECORD
    if not declared.strip():
        raise RootDirectoryVariableIsEmptyError(
            f"{CONFIGURED_ROOTS_ENV_VAR} is set but empty. Give it the path of the file that holds "
            f"the recorded configuration roots, or unset it to use ~{CONFIGURED_ROOTS_RECORD}."
        )
    return Path(declared).expanduser()


def _read_recorded_roots() -> list[str]:
    '''
    The record as it stands, in the order it was written, with a live-or-dead verdict left to the
    caller.

    A missing file, an unreadable one and one that is not a JSON array of strings all answer as the
    same thing — *nothing is recorded* — and not as a crash. That is deliberate: the worst thing a
    corrupt record can do is refuse a run exactly as a machine with no record does, so a broken memory
    can never read a tree it was never told about.
    '''
    try:
        entries = json.loads(configured_roots_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, str) and entry.strip()]


def _recorded_configuration_roots() -> tuple[list[Path], list[Path]]:
    '''
    The recorded roots, split into the trees that are still there and the ones that are not.

    A record is a memory and not a claim about the present, so both halves come back: "the tree
    recorded here is gone" is a sentence the operator has to act on, while "nothing is recorded" is the
    ordinary case. Returning both is what lets `default_platform_config_dir` tell the two refusals
    apart, and returning the live half in written order is what keeps a resolution from depending on
    which directory was touched last.

    Returns
    -------
    tuple of two lists of Path
        The recorded roots that are directories now, in the order they were recorded, and the recorded
        roots that are not.
    '''
    present, missing = [], []
    for entry in _read_recorded_roots():
        root = Path(entry).expanduser()
        (present if root.is_dir() else missing).append(root)
    return present, missing


def record_configuration_root(root) -> bool:
    '''
    Record `root` among the trees this user has prepared, and say whether it is the one a later
    command will read without being told.

    Called by `kmt config init` and by nothing else: init is the only verb that lays a tree down, and a
    verb that merely *reads* a tree does not get to promote it into a default for the user's next
    command. The verdict is not "did this run add a line" — it is "is this tree, and only this tree,
    what `default_platform_config_dir` falls back to" — because that is the sentence the verb has to
    print correctly: a tree recorded by an earlier run answers the same way, and a second live tree on
    the same machine makes every later command ask which one it means, which is exactly why the verb
    keeps `--config-dir` in the command it prints in that case.

    A record this function cannot parse is replaced and not appended to — it is machine-written, and a
    run that could not read it could not honour it either. A record that cannot be written at all (a
    read-only home) is not an error: the tree is still prepared, the flag still works, and the verb
    says nothing about a remembering that did not happen rather than pretending it did.

    Parameters
    ----------
    root : Path or str
        The directory just prepared.

    Returns
    -------
    bool
        True when this tree is the recorded root — in the record, and alone among the trees there that
        still exist. False when the record is empty, unreadable, unwritable, or holding a rival.

    Raises
    ------
    RootDirectoryVariableIsEmptyError
        If `KUBEMENTAT_CONFIGURED_ROOTS` is set to nothing, which is a refusal and not an absence.
    '''
    path = configured_roots_record_path()
    entry = str(Path(root).resolve())
    recorded = _read_recorded_roots()
    if entry not in recorded:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps([*recorded, entry], indent=2) + "\n", encoding="utf-8")
        except OSError:
            return False
    live, _ = _recorded_configuration_roots()
    return live == [Path(entry)]


def resolve_platform_config_dir(config_dir=None) -> Path:
    '''
    The configuration root a caller gets, spelled once: argument, then variable, then record, then
    refusal.

    `default_platform_config_dir` is the whole rule below the argument, and this function is the
    one place that decides whether an argument was handed at all. Earlier the same precedence
    had two owners in `src/` — here, and a hand-written `default_platform_config_dir() if
    config_dir is None else Path(config_dir).resolve()` inside `kmt environments list`. The two
    agreed, which is exactly why nobody noticed the duplication: an agreement can rot, and it rots
    the day one spelling is edited and the other is not. Two owners of one rule is how a listing
    and a command's 1:1 check start to disagree about which tree this operator configured.

    `config_dir` is `None` for "the operator named no root", which is the same meaning
    `Context.build` gives the value it threads, so a CLI flag and a library argument are one dial
    here and not two. It is also the reason the recorded layer needs no CLI plumbing of its own: every
    verb that passes no flag arrives here with `None`, so resolving a default is one change in one
    function and not 39.

    Parameters
    ----------
    config_dir : Path or str, optional
        The root this caller was handed: a `--config-dir` flag, or the `platform_config_dir`
        argument of `Config`. `None` means nothing was handed, and then the variable and the record
        answer in that order.

    Returns
    -------
    Path
        The root the argument names, resolved; or the variable's, resolved; or the single recorded root.

    Raises
    ------
    ConfigurationRootNotNamedError
        When none of the three names one, with `default_platform_config_dir`'s sentence verbatim
        (plus what the record had to add, when it had something to add), and
        `RootDirectoryVariableIsEmptyError` when the variable was set to nothing.
    '''
    if config_dir is not None:
        return Path(config_dir).resolve()
    return default_platform_config_dir()
