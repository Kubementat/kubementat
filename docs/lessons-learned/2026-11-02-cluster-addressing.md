# Lessons — cluster addressing (cluster-config-v2) — 2026-11-02

Base: `e80ac4b` → `2c28eca`  |  Tickets: 16 executed  |  Dispatches: 17 launches (2 aborted) + 1 scout  |  Wall clock: ~14.7 h elapsed, 787 worker-minutes  |  Green: 1180 passed, 1 skipped (was 1008)

## What changed

Before, `environment` was a directory of configuration and the cluster was decided outside kubementat:
`config.load_kube_config()` with no argument, every API object constructed bare against the process-wide
default, and helm/helmfile addressed by whatever `kubectl` had last been pointed at. After, an environment
**is** exactly one cluster, resolved once per command by `@kmt_context` through a four-layer order
(`--context`/`--kubeconfig` → process variables → `platform_config/<env>/static.json` → the mounted
service-account identity), verified against every other environment in the root before the command writes,
and carried to both channels — the Python client via a bound `ApiClient`, kubectl and helm via argv flags.
`Context` replaced the `(environment, team)` pair plus a `Config` each class built for itself; the ambient
fallback, `DEFAULT_TEAM`, and every hardcoded `dev` are gone. Every other decision in the seventeen commits
follows from the one thing the spec was about: **the tool says which cluster it means, and refuses to act on
one while claiming another.**

## What it cost

All numbers measured from the session logs of the 16 worker runs, not remembered.

* **2,071,710 worker output tokens** for 16 tickets → **≈129,500 per ticket**.
* **1,174,933 reasoning tokens — 57 % of all output.** The healthy marker is ~60 %; two tickets breached it badly:
  T8 (helm argv + guard + the three leftover probe reads) at 115 min and **72 % reasoning share**, and T11b at
  70 min / 75 %. Both were "widen a rule across the whole surface" tickets, which is the shape that eats thinking.
* **Compactions: 5 runs of 16** — T5 (1), T2 (1), T7 (2), T8 (2), T11b (1).
* **Tickets needing a second dispatch: 1** — ticket 2 took three launches: one mis-parameterised (`timeout`
  passed as a top-level field, silently ignored, so it was aborted before it edited anything), one that ran to
  completion but reported "the tree is clean" while the tree was actually red at 66 failures, and a third to
  finish work the second believed it had done.
* **Longest ticket:** T8 at 115 min. **Shortest:** T9 (`initialize` + template + the `KeyError` fix) at 14 min.
* **One scout** (read-only `require_team` census, ~15 min) cost about 1 % of the epic and prevented ticket 12b
  from guessing at nine call sites.
* The e2e lane that proves the whole thing: 973 s, 128 assertions, 0 failed,
  `~/.kmt-e2e/runs/20261102T095130Z-full-full-434cbfb/result.json`, `git_sha = 434cbfb`.

## Project lessons

* **A fixture that builds a cluster claim can degrade silently.** The collision fixture first used
  `kubectl config add-context`, which does not exist in the guest's kubectl 1.35 — the alias context was
  therefore never created, and the fixture's claim became a *name* instead of an *address*, which is the exact
  invariant the assert tests. → **Prove fixture primitives against the guest's own binaries, in the assert.**
  Proof: first lane's `FAIL assert:two-environments-one-cluster-are-refused`, `tests/e2e/harness/stages.py`,
  commit `2c28eca`.
* **A guard applied by instruction rather than by evidence is a false guard.** Six of nine `require_team` sites
  refused work they had no business refusing (`helm_utils:101`, `ingress_generator:134`, `routing:228`,
  `grafana_accounts:204/246/268` — none reads `team_static` anywhere in its file). It made
  `kmt routing install <env>` refuse a team that the command cannot use. → **Enumerate the reads before the
  rule goes in.** Proof: the census table; the fix is `dc28ea2`; the cause is in ticket 3's brief.
* **Two answers to one question outlives the file you notice it in.** `automation.py` deleted PVC claims through
  the client while `helm_utils.py:565-579` did it with kubectl; and three parity files addressed their client
  calls as `cluster_fixture.TARGET_DEV` (context `dev`) while the other half of the same comparison ran against
  a fixture declaring `fixture`. Two clusters, one comparison, invisible to the record for as long as those
  files existed. → **Give the comparison the field it needs and let the harness find the rest.** Proof: `8fe2b68`.
* **A guard clause placed on the wrong branch hides the case that needs naming.** `if not os.environ[x]` sat
  after `os.environ[x]`, so the absent-variable case died as a `KeyError` and only the set-but-empty case ever
  produced a message. Proof: `93db903`.
* **Two guesses that look like different bugs are one bug.** `DEFAULT_TEAM = "dev1"` and
  `argument('environment', default='dev')` were the same decision wearing different flags, which is why
  deleting one and leaving the other would have kept the failure mode. Proof: `3c9ad23`.
* **A lane run is a shared resource for your own edits.** `checkout-untouched` failed the first lane because a
  documentation edit landed mid-run. The guard is correct; the discipline is to finish editing before running.

## Proposed changes to the orchestration skill

* **"Background task completed" is not a turn boundary.** One worker stayed alive through three such notices
  while I believed it finished, and I dispatched a second writer into the same tree on that false premise. The
  second worker stopped, reported the timestamps it had observed, and asked — which is what saved the run.
  → **Check run status before touching the tree or committing, every time.**
  Evidence to justify this: instrument next epic's log for "tree had two writers" events and count how many
  follow a completion notice that was not preceded by a status check. If a status check before every
  commit/tree-write yields zero collisions, the rule holds.
* **A project-wide rule should be preceded by a read-only census, not followed by a repair ticket.** My ticket 3
  instruction ("call `require_team` where a class reads team configuration") was applied to nine call sites and
  six were wrong. The fix cost a whole ticket: 29 min and 80k output tokens for 12b, plus 8 commands' surfaces
  churned once before being churned again.
  → **For any rule that touches every call site of a class, dispatch a census first** (~15 min, read-only,
  can run alongside a writer).
  Evidence: this epic — 15 min of census vs 29 min of repair is a clear win; if the next epic's census still
  turns up a ≥30 % wrong rate on rule application, the census should be mandatory rather than recommended.
* **Reasoning share is the effort signal, and the first dispatch should be sized by it.** T7/T8/T11b ran at
  `medium` and spent 72–75 % of their output tokens thinking. T6 at `high` did a harder job in 34 min at 55 %.
  → **Widen-a-rule-across-the-surface tickets start at `high`**, and a ticket whose first run exceeds ~65 %
  reasoning share is mis-scoped rather than slow.
  Evidence: measured per-ticket reasoning shares above; next epic should record reasoning share per ticket as a
  first-class number so the 60/85 % thresholds stop being anecdotes.
* **Re-measure every reported test delta; never reuse a reported number as a fact.** Three workers reported test
  counts that the tree could not reproduce (29 new where 11 existed; "1075 passed" where the tree said 1067).
  The habit of re-running caught a real spec gap — RC-10's `kmt environments list` module did not exist — that
  a greener-sounding report would have hidden.
  → **The verification step counts assertions in the file, not in the report.** (This one is already in the
  skill; this epic is evidence that it pays for itself.)

## What I would not repeat

The parallel second dispatch onto ticket 2's tree. It was launched on a misread notification, it wrote nothing
only because the worker chose to stop and ask, and the entire value it produced was the analysis that confirmed
my own error. The correct action — status check before any tree write — costs one call and was free.
