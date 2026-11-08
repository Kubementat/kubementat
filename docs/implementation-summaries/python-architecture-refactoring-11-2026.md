# What changed — the Python architecture refactoring (arch-refactoring v2)

Base: `299a69d` → `8acfaa0`  |  52 commits, 2026-11-05 → 2026-11-07  |  1785 passed, 1 skipped  |  Lane `--lane full --scope full`: 128/128

## The one change everything else follows from

Every god object is gone and replaced by three layers, and the rule is that nothing below may reach up:

    src/kubementat/core/            21 files, 4,528 LOC   constants, assets, config/, identity/
    src/kubementat/infrastructure/  24 files, 7,567 LOC   kubernetes/, tekton/, grafana/, helm.py, shell.py, user_management.py
    src/kubementat/automations/     23 files, 4,777 LOC   install/, uninstall/, tunnels/, accounts/, routing/, ci.py, ingress.py
    src/kubementat/                  8 files              errors.py, the flat config verb modules, crypto_utils.py

`python3 tools/import_graph.py src` walks the imports and fails on a cycle or a layer violation.

## What was removed

| Gone | Instead |
| --- | --- |
| `Automation` (54 methods) | module-level functions taking `ctx`, one verb group per `automations/` slice |
| `KubernetesUtils` (77 public) | nine area modules under `infrastructure/kubernetes/` |
| `TektonUtils` (18 methods + 27 functions) | five modules under `infrastructure/tekton/` |
| `cluster.py` | `core/identity/` |
| the `config.py` monolith | `core/config/` (nine named modules) |

All of these were public API, so `CHANGELOG.md` records each as a removal, not a move. There are no shims and
no re-exports of old paths.

## Two rules moved to where they belong

- **The team refusal.** It used to live in `TektonUtils.__init__`, so constructing the object earned a team for
  every path, including ones that never read a team-scoped namespace. `ctx.require_team(...)` is now called at
  the four sites that do (`triggers.setup_triggers`, `runs.run_pipeline`, `runs.setup_cleanup_job`, and inside
  `install_kubementat`'s pipeline branch). A library caller holding a team-less `Context` is refused before the
  first apply.
- **The team `static.json` reader.** It sits at `core/config/teams.py`, not `automations/teams.py`. Three slices
  read it, so putting it in `automations/` would have made three upward edges.

## New code worth reading first

1. `core/identity/context.py` — the `Context` every command carries, and `require_team`.
2. `core/config/config.py` — layered config loading; `core/config/derived.py` — rules that answer keys the
   configuration did not state.
3. `infrastructure/kubernetes/clients.py` — how a client is bound to the cluster its environment declares.
4. `infrastructure/tekton/generation.py` — the placeholder table and the two generators.

## What to know before you touch it

- A command resolves its cluster once, through `Context`, and passes it to both channels: the Python client
  gets a bound `ApiClient`, kubectl and helm get argv flags. There is no process-wide "current cluster".
- `infrastructure/tekton/__init__.py` deliberately re-exports nothing; import the module that owns the work.
- The api-version cache in `tekton/preflight.py` is keyed by cluster address, so a second cluster asks its own
  question instead of trusting the first one's answer.

## The two things this report would be thin without

- **A test that cannot fail is a decoration.** Two trigger tests were `assert operations == operations` and
  passed for a whole phase; they only failed when `setup_triggers` was made to do nothing. Mutation is the
  cheap fix.
- **A stage that cannot run is a lie.** `stages.py:2082` referenced a name that did not exist, which aborted
  the entire `05-features` stage while the lane reported a healthy shape. The evidence is arithmetic: the
  failing lane reported 122 assertions, the fixed lane reports 128.

## Where the detail is

- `docs/plans/architecture-refactoring-plan.md` §10 — what the tree looks like now; §4–§7 are the plan, dated.
- `docs/lessons-learned/2026-11-07-architecture-refactoring.md` — what it cost and what to do differently.
- `docs/diagrams/src-class-diagram.excalidraw` (and `.png`) — the class diagram, redrawn from the tree.
- `.agent-work/e2e-gates.md` — every lane run, with artifact paths.
