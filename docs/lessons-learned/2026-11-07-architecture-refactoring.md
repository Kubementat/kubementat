# Lessons — the architecture refactoring, Phases 0–5 — 2026-11-07

Base: `299a69d` → `ac31f53`  |  Commits: 52 (2026-11-05 23:32 → 2026-11-07 18:25, ~43 h elapsed)  |  Briefs
written: 46, in `.agent-work/`  |  Lane runs recorded: 7 (`1` failed, at `9134d14`; the failure was in the
harness)  |  Green: **1785 passed, 1 skipped**, from 1180 at the cluster-addressing baseline
(`docs/lessons-learned/2026-11-02-cluster-addressing.md`)

## What changed

The tree stopped having gods in it. `Automation` (54 methods), `KubernetesUtils` (77 public), `TektonUtils`
(18 public methods and 27 module functions), `cluster.py` and the `config.py` monolith are gone, replaced by
three layers where nothing below may reach up: `core/` (21 files, 4,528 LOC), `infrastructure/` (24 files,
7,567), `automations/` (23 files, 4,777), with eight files flat in `src/kubementat/` and `src/cli/` above the
whole thing. Every verb that used to construct an object and read a `Config` built into it now takes a `Context`.

Two rules moved into where they belong. The team refusal — a team a command was never given — lived in
`TektonUtils.__init__`, so merely constructing the object earned it for a code path that would never read a
team-scoped namespace; it is now refused at the four call sites that do. And the team `static.json` reader
landed in `core/config/teams.py` rather than `automations/teams.py`, because three slices read it and the layer
rule is what decided its home, not the shape of the old monolith.

## What it cost

Four of them died of their own kind of work rather than its difficulty, and the pattern is worth naming:
the census that could not stop reading, the DAG that re-derived the mapping five times, the documentation pass
that re-measured instead of writing, the diagram that built an esbuild bundle to render a PNG and then had no
minutes left. In three cases I committed the worker's work rather than lose it, and in one I did not get to.
What was missing each time was a reading budget and an explicit "stop and report what you have" line.

The expensive lesson was a cost I did not pay for a day. `stages.py:2082` had `NameError: name 'explained' is
not defined`, live in HEAD since it landed. It did not fail an assertion; it aborted the entire `05-features`
stage, and the lane reported a healthy shape around the hole. **The evidence is the count: the failing lane
reported 122 assertions, the passing lane after the fix reported 128.** Every full-scope lane between that
line and here was quietly shorter than it claimed.

## Project lessons

A test whose job is to prove that something happens has to be mutation-tested, or it is decoration. Two
trigger tests were `assert operations == operations` and comparisons of empty recordings. They passed for
the whole duration of Phase 4 and failed only when I made `setup_triggers` do nothing. The mutation is ten
seconds; finding it later costs the confidence of the entire suite.

A checker that reports "none" is not evidence that it is looking. The import walk is blind inside a single
layer and blind to `cli/`, which is how a real layer violation survived unnoticed for a phase. The only proof
that an edge is watched is planting the reverse edge and watching it fail. Same for the lane: the assertion
that failed Gate 6 had been matching the tree path in the command line of its own probe, because `DUMMY_ENV`
exports a path inside the checkout into every guest command. It reported a residue that was the asking of the
question, while the same assertion's own `deletable=True` said the uninstall had worked.

Never carry a number into a commit. This epic put a wrong line count into git history twice, and both times
the number had been measured by someone else in a session that then kept editing source. The rule that finally
stuck was boring: whoever writes the commit message runs `wc -l`. The same distinction governs prose, and it
took me three phases to learn to state it once: **a sentence about where something lives is a claim and must
be true now; a sentence about what happened is a record and stays dated.** A shipped trigger template was
telling a human on a cluster to read a file deleted two phases earlier, and that is what makes this distinction
worth more than tidiness.

Delegation worked exactly as well as its acceptance criteria. A report of "23 names moved" is worthless; the
reports I trusted were a table of name/moved-by-call-site plus the command that printed them. And one thing I
would not do the same way: two workers committed against an explicit "do not commit" instruction, because my
brief said it once inside a long paragraph. Both commits were correct and I accepted them, but commit ownership
belongs in a rule line a worker cannot miss, not in prose.

## What I would not repeat

Re-deriving a mapping that already exists. The Phase 3 salvage began with a census proving that 33 names moved
by name, 20 renamed, 0 dropped, with bodies 0.87–1.0 similar to their originals — which meant the shape was
plan-conformant and the honest fix was to finish the repointing, not to revert and redo. I nearly reverted.

Letting a docs ticket run 45 minutes because it is "cheap". Documentation work is not cheap, it is *shallow*,
and shallow work is the one shape that produces a timeout: nothing is wrong, the worker just keeps reading.
The next time: a hard reading list of files, a stated count of sites, and a stop at the first undecided line.
