# Kubementat tests

There is one way to run the suite:

```bash
uv run python tests/e2e/suite.py --lane smoke   # ~9 min (541 s measured, 66 asserts)
uv run python tests/e2e/suite.py --lane full    # ~29 min (1724 s measured, 88 asserts)
```

Everything lives in [`e2e/`](e2e/README.md). The legacy `tests/*.sh` scripts
(`test_installation.sh`, `uninstall.sh`, `reset_configuration.sh`) are deleted: they
prompted on stdin, gated on `kubectl config current-context`, and hand-deleted three
PVCs by hardcoded name. Their replacement proves the same lifecycle on a real
throwaway VM.

## Unit tests: `tests/test_*.py`

Everything directly under `tests/` is pytest and needs no cluster:

```bash
uv run pytest -q
```

Each file covers one contract of the code it is named after — running scripts out of the
checkout, the `--team` option, loading a config that is missing, the command-execution
primitive and the rule that only that primitive starts programs.

**Parity.** `test_parity_*.py` compares a Python path against the bash script that did the same
job. When the script is still in the tree both halves run, through the same fake binaries, and
`cluster_parity.py` reduces what each of them asked the cluster to do. When the deletion ledger
has taken the script, the comparison does not go away with it: what the script sent is recorded
in the test file instead, and the Python side still runs for real. A record without a name — "what
it did" — is not evidence, so every recorded row says which script it came from.

**Naming.** Say what the test proves, not which ticket asked for it. A file and a case
name must be readable by someone who has never seen the plan that motivated the change:
`test_relative_script_path_is_resolved_against_the_checkout`, not
`test_run_script_fin46_relative_path`. Plan and ticket ids belong in the *body* of a
docstring, where they point at evidence, never in the name. Same rule for parametrized
ids — if a test runs against five commands, the ids are the command names, not
`command0`…`command4`.

For what belongs in the VM suite rather than here, see
[docs/README_E2E_TESTS.md](../docs/README_E2E_TESTS.md).

## What this suite proves

`create VM -> provision single-node k3s -> install kubementat -> exercise the main
features -> re-run (full) -> uninstall -> assert absence -> destroy VM`, in a VM that
the run creates and destroys on your own machine. It is not a CI gate: it runs when a
human decides to run it, and its `result.json` (which names the git SHA it tested) is
what you cite on a ticket.
