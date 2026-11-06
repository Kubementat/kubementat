"""Where kubementat's two roots come from, and the one case where it refuses to have one.

Two roots and one precedence. `default_kubementat_main_dir` is the **output** root: the tree a run
writes `tekton_ci/pipeline-runs/` into, answered by `KUBEMENTAT_HOME` when the operator set it and by
the location of this file otherwise. `default_platform_config_dir` is the **configuration** root, and
it is named or it is refused — the `--config-dir` flag or the variable a container already sets, and
with neither, one sentence and no run. `resolve_platform_config_dir` is the precedence between the
argument and the variable, spelled once here because `kmt environments list` used to spell it a second
time beside its own `EnvironmentRegistry`; agreement between two owners is not one rule, and it rots
the day either spelling is edited.

`_directory_from_environment` is the rule both roots share and the reason this is a module and not two
one-liners: a variable that is set to nothing is not "unset", and falling back to the built-in default
at that point reads configuration out of site-packages while saying nothing about it. So it raises, and
the empty case is refused in one place instead of in each root's own words.

What this module is not. It holds no `Config` and reads no layered configuration. The only fact it
carries about the filesystem is the depth of this file, which is the whole arithmetic
`default_kubementat_main_dir` answers with and which travels with it — `Path(__file__).parents[4]`, the
same number the function had beside it, because this module sits in the directory the function left.
"""

import os
from pathlib import Path

import click


# `Path(__file__).parents[4]` is the repository root for a checkout and *site-packages*
# for an installed wheel, so it is only a default and never the answer on its own (D11).
# Four, not two, because this module sits two directories below the package root: the
# count is the whole arithmetic the function depends on, and it travels with the file.
# The environment variables below are the override: the same tree can be read from
# anywhere, which is what the CI container needs (`kmt` in site-packages, checkout in
# /workspace).
KUBEMENTAT_HOME_ENV_VAR = "KUBEMENTAT_HOME"
KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR = "KUBEMENTAT_PLATFORM_CONFIG_DIR"


def _directory_from_environment(name):
    '''
    Reads the directory `name` names from the environment.

    Returns
    -------
    Path or None
        None when the variable is not set at all, meaning "use the default".

    Raises
    ------
    click.ClickException
        If the variable is set but empty. Falling back to the default there would be the
        D11 failure again, silently: an empty `KUBEMENTAT_HOME` would read config out of
        site-packages and say nothing about it.
    '''
    value = os.environ.get(name)
    if value is None:
        return None
    if not value.strip():
        raise click.ClickException(
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
    The configuration root when no caller named one: `KUBEMENTAT_PLATFORM_CONFIG_DIR`, and
    nothing else.

    A configuration root is **named, never located**. Two spellings name it — the `--config-dir`
    flag a command carries and the variable a container already sets — and when neither is
    present this function refuses. It used to answer with `platform_config` under
    `default_kubementat_main_dir()`, which meant a command that had been told nothing still
    answered, from wherever the wheel happened to be installed. That answer is the D11 bug in its
    last remaining form, and v2 is a rewrite rather than a migration, so there is no
    compatibility branch behind it: the operator gets one line naming the two spellings, and the
    run stops before it reaches a cluster.

    `kubementat_main_dir` is not consulted. It names the tree a checkout hangs its *output* off
    (`tekton_ci/pipeline-runs/`); everything the product ships — `templates/`, the Tekton Tasks and
    Pipelines, the generator templates — is package data and reaches no root at all.
    An asset root is a different question with a different override;
    letting it stand in for the configuration root is how one command ended up reading two trees
    while believing it read one.

    Returns
    -------
    Path
        The root the variable names, resolved.

    Raises
    ------
    click.ClickException
        If the variable is unset, or set but empty. The empty case is
        `_directory_from_environment`'s rule verbatim: falling back when someone set the variable
        to nothing is the silent default again, just further from view.
    '''
    declared = _directory_from_environment(KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR)
    if declared is not None:
        return declared
    raise click.ClickException(
        f"no configuration root is named. Give this command --config-dir PATH, or set "
        f"{KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR} to the directory that holds the "
        f"environments' static.json. kubementat does not read configuration out of the directory "
        f"it was installed from, and it has no default root to fall back to."
    )


def resolve_platform_config_dir(config_dir=None) -> Path:
    '''
    The configuration root a caller gets, spelled once: argument, then variable, then refusal.

    `default_platform_config_dir` is the whole rule below the argument, and this function is the
    one place that decides whether an argument was handed at all. Earlier the same precedence
    had two owners in `src/` — here, and a hand-written `default_platform_config_dir() if
    config_dir is None else Path(config_dir).resolve()` inside `kmt environments list`. The two
    agreed, which is exactly why nobody noticed the duplication: an agreement can rot, and it rots
    the day one spelling is edited and the other is not. Two owners of one rule is how a listing
    and a command's 1:1 check start to disagree about which tree this operator configured.

    `config_dir` is `None` for "the operator named no root", which is the same meaning
    `Context.build` gives the value it threads, so a CLI flag and a library argument are one dial
    here and not two.

    Parameters
    ----------
    config_dir : Path or str, optional
        The root this caller was handed: a `--config-dir` flag, or the `platform_config_dir`
        argument of `Config`. `None` means nothing was handed, and then the variable answers.

    Returns
    -------
    Path
        The root the argument names, resolved; or the variable's, resolved.

    Raises
    ------
    click.ClickException
        When neither names one, with `default_platform_config_dir`'s sentence verbatim.
    '''
    if config_dir is not None:
        return Path(config_dir).resolve()
    return default_platform_config_dir()
