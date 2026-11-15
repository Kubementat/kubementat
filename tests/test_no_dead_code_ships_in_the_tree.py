"""Nothing ships that no one can reach, measured off what the package actually is.

A migration leaves corpses behind, and the one this file still answers for is the dead module: the
zero-byte `utils` module that once sat in `src/kubementat`, imported by nothing, exported by nothing,
kept alive because deleting it is not glamorous. Dead code is worse than absent code -- it reads as
though the helpers it is named after live somewhere -- so the guard stays.

What changed is the *mechanism*. This file used to reconstruct the import graph by parsing every
`.py` file in the checkout and asking whether any import statement named each module. That is a
statement about this repository's own source, which is the one thing the developer guide says a test
may not police, and it failed on every move of a file whether or not the move was wrong. The guard
now asks the package's own index -- `pkgutil` enumerates what the installed `kubementat` package
ships, and the import answers for itself -- so a module that ships and cannot be imported, or ships
and contains no definition at all, fails here without a single file being read as text.

The rest of what this file used to assert has been retired to the behaviour it was standing in for,
and the handles are written down so the deletion is auditable rather than folklore:

* *the team-account scripts stay gone* -- the commands are exercised end to end by
  `tests/test_a_team_account_run_names_the_cluster_and_checks_the_verbs_it_needs.py`, and the
  confirmation that used to exist twice is a gate that test names and runs.
* *the pvc-usage and ingress-printer scripts stay gone* -- `kmt kubernetes pvc-usage` is read under
  `tests/test_pvc_usage_reads_the_kubelets_and_names_the_nodes_that_stay_silent.py` and
  `kmt kubernetes create-ingress` under
  `tests/test_generated_ingress_manifests_name_the_service_the_class_and_the_issuer.py`. A script
  coming back is caught by the command's own surface, because the command is the only way in.
* *no `.py` file under `scripts/`* -- the checkout ships no `scripts/` directory at all, so that
  guard was reading a directory that is not there. The rule that replaced it is behavioural: no
  `kmt` verb takes a script path, asserted over the registered CLI in
  `tests/test_no_cli_command_starts_a_script_by_path.py` and over the execution policy in
  `tests/test_command_execution_policy.py`.
"""

import importlib
import pkgutil

import kubementat

# The package markers are allowed to hold nothing of their own: a package is reached by importing its
# parent, by definition, so "this module defines nothing" is not a finding about one. Every other
# module has to define something, which is exactly the claim the zero-byte corpse used to dodge.
PACKAGE_MARKERS = set()


def shipped_modules() -> list:
    """Every module the installed `kubementat` package ships, by import name.

    This is the artefact's own index rather than a directory walk: `pkgutil` asks the same importer
    that `import kubementat` asks, so a module that is not reachable through the package at all is
    not enumerated, and a module that is enumerated is one a user's `kmt` run could reach.
    """
    return sorted(entry.name for entry in pkgutil.walk_packages(kubementat.__path__, "kubementat."))


def test_every_module_the_package_ships_can_be_imported():
    """Nothing unreachable ships. The import is the measurement; a corpse fails it on its own.

    A module that imports nothing and is imported by nothing still appears here, which is the point:
    what the guard catches is the state where the package ships something that cannot be brought in,
    or a module that arrives and stays empty. Emptiness is read off the module's own namespace, so a
    module that exists to re-export or to hold constants is not mistaken for a corpse.
    """
    modules = shipped_modules()
    assert modules, "the package enumerated nothing, so this guard is not reading a package"

    failed = []
    empty = []
    for name in modules:
        try:
            module = importlib.import_module(name)
        except Exception as error:  # a module that cannot be imported is dead on arrival
            failed.append(f"{name}: {type(error).__name__}: {str(error)[:120]}")
            continue
        if hasattr(module, "__path__"):
            continue  # a package marker is reached by importing its parent, by definition
        declared = [name for name in vars(module) if not name.startswith("__")]
        if not declared:
            empty.append(name)

    assert failed == [], "these shipped modules do not import: " + "; ".join(failed)
    assert empty == [], "these shipped modules define nothing of their own: " + ", ".join(empty)


def test_creating_a_team_account_is_a_python_feature_now():
    """All three team-account verbs exist as functions in the accounts slice.

    Before the Python flow existed this read `assert not hasattr(Automation, "create_team_account")`,
    and that was the honest assertion at the time: the Python version was not written, so a reader had
    to be told not to mistake a deleted corpse for a finished feature. It is finished now, and what is
    worth saying is that the entry points exist -- the behaviour they owe is asserted where it runs.
    """
    from kubementat.automations.accounts import accounts as accounts_slice

    for name in ("create_team_account", "create_team_account_read_only", "delete_team_account"):
        assert callable(getattr(accounts_slice, name, None)), f"accounts.{name} does not exist"
