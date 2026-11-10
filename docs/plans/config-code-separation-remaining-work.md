# Plan: config/code separation — the remaining work

Source spec: [`docs/specifications/config-code-separation-spec.md`](../specifications/config-code-separation-spec.md)
(rev 2 + owner decisions N-1..N-10, 2026-11-05).
Parent queue: [`config-code-separation-epic.md`](config-code-separation-epic.md) — read its "Decided —
do not re-ask" section before touching anything. Base: `303c049` (`v2` branch). The unit suite is green
at this plan's drafting (`uv run pytest -q`); the e2e suite is not a CI gate — attach its `result.json`
(which names the git SHA) to every lane run you report.

What this plan covers is the residue of the epic: five tickets, ordered. Everything else in the spec
landed — verify that assumption with `git log --oneline | grep -o "T-[0-9]*[a-c]*"` before re-implementing
anything the rows below say is done.

---

## 0. Delete `config explain`, `config diff` and `config migrate` — owner instruction, 2026-11-05, runs first

**The instruction:** the `config` group carries ten verbs today and seven of them are wanted. Delete the
other three — the verbs, what only they reach, and the tests that cover them. They are code this project
will not require, so they are not to be frozen, deprecated, feature-flagged, or left "dead but harmless" —
and the first standing rule below leaves no shim to hide behind.

**Three questions came back answered on 2026-11-05, so nothing below is still open:**

| Question | Answer | What it settles |
|---|---|---|
| Keep `config_renames.py` for its history and its census gate, or delete it? | **Delete it.** | The rename table goes with the verbs. R-13.3's gate is withdrawn out loud in the spec, the census test goes, and no part of the table survives as "history only". |
| Does `searched` (the files precedence consulted) need a home in `show`? | **No.** | That assertion dies with `explain`. `show` is not extended to carry it. |
| Run the lane before ticket 0, or after? | **After, reading through `show`.** | No lane run on `f2d5f1a`; the first VM evidence after this ticket exercises the re-pointed read-back, and that is also what proves it. |

### The surface after this ticket

`kmt config` keeps exactly seven subcommands, and nothing else:

| Verb | Why it stays |
|---|---|
| `init` | the one thing that creates a configuration directory |
| `add-environment` | creates a layer |
| `add-team` | creates a layer |
| `set` | the one write |
| `unset` | the one removal |
| `show` | reads the whole effective tree back, with the layer and the file for every key |
| `validate` | the verdict, and now the only strict reader of the version window |

`explain`, `diff` and `migrate` go. Everything a reader needed from `explain` is in `show`'s line
(`KEY = value   (<layer> from <path>)`); everything `diff` reported is a fact `validate` already reads from
the stamp; and `migrate`'s table is deleted with it, which means kubementat records no renames and applies
nothing — the spec says that, not a comment.

**Check the deletion against the guard that guards this set.** `tests/test_cli_surface.py:279` holds
`EXPECTED_CONFIG_COMMANDS` — the exact verb set, on purpose, so a verb cannot arrive or leave quietly.
Dropping the three names is the commit's own record that this was decided and not drifted.

### What goes, measured and not estimated

**`config explain`** — `explain_command` (`src/cli/commands/config.py:457-483`), then the four helpers
only it reaches: `_explanation_lines` (:701), `_default_line` (:724), and in `config_answers.py`
`explain_answer` (:185) and `AnswerExplanation` (:149). `_searched_paths` and `_searched_sentence` are
reached from `explain_answer` and nothing else, so they go too.

| Site | Consumers |
|---|---|
| `src/cli/commands/config.py` — the verb and its two printing helpers | the CLI only |
| `src/kubementat/config_answers.py` — `explain_answer`, `AnswerExplanation`, `_searched_paths`, `_searched_sentence` | the CLI verb, **and 13 direct calls from four test files** |
| `tests/e2e/harness/config.py` — the `config.explain` (:483) and `config.explain.team` (:489) capability rows | `stages.py`'s `_config_explain` (:2063), called from 5 sites: :511, :581, :1542, :1589, :1921 |
| Four test files that use `explain_answer` as their oracle | `test_the_defaults_initialize_seeded_have_a_single_owner.py` (6 calls), `test_the_eight_former_environment_variables_arrive_only_through_the_layers.py` (3), `test_the_effective_configuration_says_which_layer_answers_each_key.py` (2 + 6 CLI runs), `test_the_ingress_class_the_manifest_names_is_the_one_the_configuration_answers.py` (2) |

**`config diff` and `config migrate`** — the verbs, the modules behind them, and what only they reach:

| Site | Size | Why it is the last consumer |
|---|---:|---|
| `src/cli/commands/config.py` — `diff_command` (:540), `migrate_command` (:594), the import (:54), the calls (:563, :612) | two verbs | `grep -rn "config_diff\|config_migrate" src tests` answers nothing outside these lines and the two modules below |
| `src/kubementat/config_diff.py` | 263 lines | imported only by the `diff` verb |
| `src/kubementat/config_migrate.py` | 303 lines | imported by the `migrate` verb and by `config_diff.py` |
| `src/kubementat/config_renames.py` | 111 lines | imported by those two modules and by one census test, which goes with it — see the rename table below |

Nothing else touches `diff` or `migrate`: no `.yml`, no `.sh`, no shipped Tekton asset, and no capability
row in the harness (checked). `build/lib/` mirrors this and is gitignored (`build/`, `.gitignore:41`); it
rebuilds. **`explain` is the exception** — it is the one deleted verb with a working consumer, which is
where all of this ticket's real risk sits.

### What deleting `explain` actually costs

**The harness reads its configuration back through `explain` right now.** Five stage sites call
`_config_explain`, and ticket 3 put them there deliberately: `explain` answers a key's value on stdout and
its provenance on stderr, so a stage asserts the answer and the layer it came from in one command instead
of pairing a value read with a guess about where the value came from. R-16.1's *Done when* names it. So
this is not a deletion of an unused verb — it is a re-pointing of a lane mechanism that landed five days
ago.

What it becomes is `config show`, and the substitution is better than it sounds: `show` prints every key
the tree answers as `<KEY> = <value>   (<layer> from <path>)`, so the layer and the file are on the same
line the value is on. The harness rows become `config.show` (and `config.show.team`), `_config_explain`
becomes a line parser for one key out of `show`'s listing, and the five call sites keep asserting the same
two facts — value and layer — from one run. What is lost is the *stdout/stderr split*: `show` puts the tag
on the value's line, so a lane that greps the value must cut at the three-space separator. Say that in the
helper's comment, because it is exactly the place a later edit goes wrong.

**`explain_answer` becomes library code that no command reaches.** Its 13 test call sites are the layering
oracle for the four files above, and `effective_answers` answers every fact they assert — key, value,
origin, source, default — from the same resolution. So the tests move to `effective_answers` plus a key
lookup rather than losing the assertion.

**One fact has no home after the deletion, and it is not a loss you can paper over:** `searched` — the list
of files precedence consulted, highest layer first. It exists only on `AnswerExplanation`, and exactly one
test asserts it (`test_the_effective_configuration_says_which_layer_answers_each_key.py:406`). "I looked in
these four places and none of them answered" is a fact no surviving verb prints, so that assertion goes
with the verb. Record it in the commit message rather than replacing it with a `show` assertion that cannot
say it; a test that claims to prove what the file no longer prints is worse than no test.

**The sentences that name `explain`.** These are user-visible or load-bearing, so they change in the same
commit:

| Where | Says today | Says now |
|---|---|---|
| `src/kubementat/config_answers.py:259` | "config show and config explain answer a tree that exists and write nothing into one" | `show`, alone |
| `src/kubementat/config_validate.py:30` | a dead key "is a question for `config explain`, which answers it from the…" | the finding names the key and says no code reads it. There is no verb to send the reader to |
| `src/kubementat/config_reads.py:41`, `config_init.py:849` | name `explain` as a reader of the tree | name `show` |
| `tests/e2e/harness/config.py:216`, `:334` | "read it … through `kmt config explain`" | through `kmt config show` |

(While touching `config.py:472`'s docstring: it cites "§12" for the read-back rule, and §12 of the spec is
product packaging. The read-back ruling is R-16.1. The citation is wrong as well as obsolete, and this
ticket deletes the sentence anyway — just do not carry the wrong section number into the replacement.)

### What deleting `diff` and `migrate` costs

**A deleted command leaves sentences behind, and those sentences are user-visible.** They change in the
same commit as the verbs, or the product spends the interval pointing a reader at a command that does not
exist:

| Where | Today it says | It must say |
|---|---|---|
| `src/kubementat/config.py`, `ConfigurationStamp.version_gap_sentence` (~:536) | "…and config diff is what says what changed in between." | the two versions, and nothing else |
| `src/kubementat/config.py`, `check_configuration_stamp`'s schema refusal (~:567) | "`kmt config migrate {root} --dry-run` is the command that closes the gap." | bump `schema_version` in `kubementat.yaml`. No verb will do it, so stop naming one |
| `src/kubementat/config_validate.py` — the stamp findings, their messages, and the comments that pair the two verbs | "validate and diff are the two verbs that get to be strict" | validate, alone, is the strict home |
| `src/kubementat/config_init.py`, `src/kubementat/tekton_utils.py` | point at "the `config migrate` rename table" | tell the reader what to do; which verb used to do it is not an answer |

**Ticket 3 is partly undone, and says so.** That ticket put the lane's read-back on `explain` and its
*Done when* names the verb. Ticket 0 moves the same five reads to `show`; the rule (the lane reads through
a verb and never through `jq`) is untouched, and the evidence is unchanged. Ticket 3's prose should carry
that sentence rather than read as though a landed requirement was quietly dropped.

**Ticket 2 is partly undone too.** Its *Done when* demanded that validate and `config diff` never disagree
about one window, and it landed a test proving two verbs read one `ConfigurationStamp`. With one verb there
is nothing to agree *with*. The window check survives untouched — validate is the strict home now — so what
comes out is the cross-verb comparison and not the finding.

**`ConfigurationStamp` is not part of this deletion.** Do not remove it with the modules: validate, every
cluster command's `check_configuration_stamp`, and `config init`'s stamp all read it.

**The upgrade lane goes with the instrument it measured with.** It existed to run `config diff` between `N`
and `N+1` and assert the component set survived. With `diff` deleted there is nothing between the two
installs to assert, so the lane has no subject and comes out. It is spec prose, not code: no lane, scope or
stage by that name exists anywhere in `tests/e2e/`, so deleting it costs no code — and §18's test strategy
names it, phase 9 gates "TD-1 measured end to end" (~:904), and this plan's own definition of "the epic is
finished" all name it. What replaces it is nothing. The honest sentence is that the upgrade path is now
*unmeasured*, and that is a decision, not an oversight. If it is ever to be measured again the instrument
has to be a statement of what changed between versions, not a re-implementation of the verb.

### The specification was edited first, on 2026-11-05 — read this before touching it again

**The spec is already done.** `docs/specifications/config-code-separation-spec.md` was corrected before any
code moved, because the spec is the source document and code follows it, not the reverse. The table below is
that completed edit, kept here so the change is legible and so nobody redoes it — the verbs are deleted from
the spec, and every place that was only true because they existed is withdrawn out loud rather than
annotated. If you find a row here that does not match the spec as it stands now, **the spec is the one to
trust**, and the row should be corrected here, not there.

What was verified before editing: 13 mentions of the three verbs in the spec, each read in place rather than
by grep alone, plus the three things grep would have missed — §1's summary sentence, the goal sentence that
ends with "reading a diff", and the "migration shape" clause in the review record. The unit suite was green
before and after (1746 passed, 1 skipped), because nothing in `tests/` parses the spec's text.

| Spec site (section, not line number) | What it said before | What it says now |
|---|---|---|
| §7's verb table  | three rows: `config explain`, `config diff`, `config migrate` | all three rows deleted, and the paragraph under the table names the deletion, what survives of each verb's information, and the one fact that survives nothing |
| §7's `explain` row  | "the answer to one key **and why it is that answer**… same shape as v2's resolution report" | gone with the row. The precedence answer survives as `show`'s per-line tag, which is where it has always lived |
| §16.1's opening  | "changes it with `kmt config set` / `unset` / `explain`" | changes it with `set` / `unset`, reads it back with `show` |
| §16.1's *Done when*  | the collision and refusal lanes "read their state back through `kmt config explain`" | through `config show`, whose per-key line carries the layer the way `explain`'s stderr did. Same rule, different verb |
| §18's unit list  | "the `config` verbs of §7 (`explain`'s provenance answer, `unset`'s fallback-to-default)" | `show`'s per-line tag, `unset`'s fallback-to-default |
| Phase 3's deliverable  | "`config init` … + `config show/validate/explain/unset` (§7)" | `explain` dropped; the row reads `show/validate/unset` |
| Phase 9's row  | "`config diff/migrate` + release-pipeline bump PRs (§13)", gated on "TD-1 measured end to end" | revised and not deleted: the row is bump PRs (R-13.4) only, and its gate no longer claims a measured bump, because nothing left measures one |
| Phase 4-8's note  | "one minor-version line with `config diff` as the documented migration tool" | the clause is gone and what is true in its place is written: the release note and the CHANGELOG say what changed, and `schema_version` says when to expect a break |
| §2 Scope  | "the version contract and the `diff`/`migrate` machinery (§13)" | the version contract only. The machinery is gone |
| §9's rename sentence  | "a path rename with a migration entry and a `config diff` line, not a file-layout preference", plus the bullet about `config diff` reporting generated manifests | the rule stays and is put more sharply than before: a moved path is refused by name when the old directory survives, and that refusal is now the whole of the safety, because nothing rewrites the operator's tree. Both tooling references deleted |
| R-11.8  | lists `config migrate` among the verbs that generate nothing | the name is gone from the list; the rule about which verbs never generate is otherwise untouched |
| R-13.2  | "warns once, with both versions and the `config diff` hint … `config validate` and `config diff` are the commands that exit non-zero" | warns once with both versions, and continues. `config validate` is the command that exits non-zero about it — one verb, not a pair |
| R-13.3  | the table, `config migrate` applies it, dry-run prints what it would write | **withdrawn out loud**, with the census gate named as gone and *Done when* rewritten so it no longer demands a complete table — the owner's answer of 2026-11-05, written into the spec and not left for the code to discover |
| R-13.5  | "after a migrate, the one before", and the done-when's "two-step upgrade (`migrate`, `migrate`, `install`)" | the window is `N`'s version, one step. Where it names its remedy it names a deliberate `schema_version` bump, because no command will do it, and *Done when* now says the bump is a human decision and not some verb's side effect |
| TD-1  | "running `kmt config diff` and `kmt install dev`" | running `kmt config validate` and `kmt install`, with a sentence naming that the test lost its instrument on 2026-11-05 and saying which half is still measured. The spec's own "test that decides the whole spec" says what is measurable now, not what used to be |
| §18's lane list  | **Upgrade lane** — configure at `N`, `config diff`, install again, assert the set survived | the lane's sentence is replaced by a note that it was deleted, that no lane, scope or stage of that name ever existed in `tests/e2e/`, and that the upgrade path is therefore unmeasured |
| §22.2  | "the migration shape" survived the review | moved out of the surviving list and named as the one design that did not survive, with the reason |
| §7's verb table — `config teams`  | a row for a verb nothing implemented and no ticket ever commissioned | deleted by owner instruction of 2026-11-05, with the reason written beside it. `Config.teams()` stays, because `kmt uninstall` sweeps through it, so this is a surface decision and not a code deletion. The table is now the seven verbs and it agrees with `kmt config --help` after ticket 0's `explain`/`diff`/`migrate` commit |
| §15.3 checklist row 6  | "the bootstrap invents no credential", asserted by `test_the_bootstrap_invents_no_credentials.py` | corrected twice over. **The claim was false:** `constants.GENERATED_ENCRYPTED_ANSWERS = ("GRAFANA_ADMIN_PASSWORD",)` means `config init` generates one credential, and only where nothing answers it (`config_init.py:441-449`). **And the named test does not exist.** Row 6 now says what the code does and cites the test that actually covers it, so nothing in ticket 4 builds a lane assert against a claim the product contradicts |

How the edit was written, so the same standard is available to whoever fixes the README and the parent plan.
**Rows are deleted, never struck** — a document with struck-through rows reads as a promise that the command
comes back. **And where a requirement existed only as some tool's reason for being, it is withdrawn out
loud** instead of leaving the section to describe machinery nothing ships. R-13.3 is the whole of that
problem, and the rename table below is its answer.

### The deletion lands in two other documents, and one of them is dangerous

The spec is not the only place that describes these verbs, and the second one will get this ticket's work
re-implemented behind its back.

**`docs/README_PLATFORM_CONFIG.md` — operator-facing, and wrong the moment the verbs go.** Its "How a tree
stays current" section (:60-87) is written around `config diff` as the pre-upgrade command and `config
migrate` as what then applies the table:

```
kmt config diff <dir> <environment>
```

That section then explains what the command reports, why `config validate` and `config diff` are "the two
verbs allowed to be strict", and how `config migrate --apply` is the only thing that writes a byte. Four
sites, :69, :76, :81, :85. An operator who reads it will look for a command that is not there, so this is
not ticket 5's README work deferred — ticket 5 fixes the README's install-and-usage story; this rewrites
the section in the same commit as the deletion, or the docs will be wrong for four tickets. What replaces
it: the stamp is read and stated by every verb, `config validate` refuses what is out of the window and
warns about a version string, and there is no migration to run — so the operator's pre-upgrade ritual is
`kmt config validate <environment> --config-dir <configuration-root>`, then the bump, then validate
again.

**`docs/plans/config-code-separation-epic.md` — the parent queue, which is the one that can undo this.**
It still commissions two phase-9 rows (`:52` "Rename table + `config migrate --dry-run`", `:53` "`config
diff`"), and its rules section (`:64`) says what the project does **not** delete: "`config migrate`'s rename
table … which is how *this* version stays current". That sentence is now false, and it is the reason to
edit this file: a later ticket read from the parent queue will build `config diff` exactly as specified
there, because the parent looks authoritative and this plan looks like a branch of it. Strike both rows and
the "does not delete" sentence, with the reason and the date beside each, so the parent reads the same way
this plan does. Phase 9's remaining row — the stamp window — is untouched.

**Nothing in the suite catches a stale document.** I looked, because it would have made this section
optional: `tests/test_an_instruction_that_names_a_command_is_one_that_parses.py` runs commands that *refusals*
print, and it only covers the `config set` shape; `tests/test_the_docs_that_point_at_tekton_ci_point_at_what_stays_there.py`
checks that doc references point at files that still exist, which is about paths and not about verbs. So
neither the unit suite nor a lane run fails when `README_PLATFORM_CONFIG.md` advertises a command that no
longer exists. That makes the two documents above the only part of this ticket that depends on a human
reading them, and the reason to do them in the deletion's own commit where the reviewer is looking, rather
than in ticket 5 where nobody is.

**`docs/research/*` — leave it, and say so rather than by omission.** The two research files dated 29 and
30 October are dated evidence of what the project considered before it decided; they are not the source
document and correcting them would make the record lie about what was known when. The spec is the source.

### The rename table: deleted, with the requirement withdrawn beside it

`src/kubementat/config_renames.py` (111 lines) goes with the verbs — that is the answer, not an
approximation of one. The module is deleted, so is
`tests/test_a_key_that_leaves_the_product_without_a_rename_entry_fails_the_suite.py` (10 tests, 208 lines),
which is the live gate enforcing R-13.3's done-when ("deleting a default key without a table entry fails
the suite"), with its mutation check that proves the gate bites.

**Deleting the gate is not deleting the rule quietly.** R-13.3 in the spec says so out loud in the same
commit: the rule stops being enforced, and the sentence explains that it is withdrawn because the verb that
would have applied the table no longer exists. Three sentences make that clean and one makes it false:

* R-13.3's body becomes a note that kubementat records no rename table and applies no migration — which is
  what the product now does.
* The done-when becomes "there is no table, so there is no gate to fail", not "the table must be complete",
  which is a rule about code that is not in the tree.
* The CHANGELOG names it as a withdrawn guarantee, because an operator who read R-13.3 once and depended on
  a key being migrated on `install` has to learn otherwise somewhere.

Four places in `src/` send a reader to the table and become false on the same commit, including one that is
user-visible: `config_init.py:326` ("the `config migrate` rename table that will do this for you is phase
9's"), `config_init.py:312`, `tekton_utils.py:366`, and `config.py:859`. All four say what the reader must
do instead, which here is: nothing, because nothing migrates.

### Every test that goes obsolete, measured rather than guessed

The rule is *remove what measured deleted behaviour*, not *edit assertions until they pass*. What follows is
the whole list, read off the tree at `c03b08e`; the counts are `grep -c '^def test_'` and line counts, so
the next agent can check them against `git show --stat`.

**Deleted whole — 33 tests over 724 lines.**

| File | Tests | Lines | Why the whole file |
|---|---:|---:|---|
| `tests/test_config_diff_says_what_a_bump_would_cost.py` | 10 | 218 | The file is `config diff` |
| `tests/test_config_migrate_writes_nothing_until_it_is_told_to.py` | 13 | 298 | The file is `config migrate` |
| `tests/test_a_key_that_leaves_the_product_without_a_rename_entry_fails_the_suite.py` | 10 | 208 | The file is the census gate, and the table it censuses is deleted |

**Edited — 17 tests across 6 files: 4 die, 13 change shape.** Deleting a file is cheaper
than editing one, and these files must survive because most of what they assert belongs to verbs that do:

| File | What comes out | What changes |
|---|---|---|
| `test_the_effective_configuration_says_which_layer_answers_each_key.py` (13 tests) | **3 die**: `test_explain_on_a_defaulted_key_says_default_names_no_file_and_names_the_default` (:393), `test_explain_on_a_layered_key_prints_the_answer_on_stdout_and_the_why_on_stderr` (:415), `test_explain_of_an_undeclared_key_is_refused_and_lists_the_files_it_searched` (:441) — those three *are* the verb, and 73 of the file's 584 lines leave with them. Two more lose part of their name: `test_show_and_explain_write_nothing_and_move_no_mtime` (:466) becomes `…_writes_nothing_and_moves_no_mtime` and drops the `explain` invocation at :481, and `test_neither_verb_addresses_a_cluster` (:487) is now about one verb — its assertion already runs through `show` alone, so only the name and the docstring move. The comment at :390 ("explain, in the two halves the lane reads separately") is the last thing in the file naming the verb. | `test_a_declared_value_that_answers_nothing_is_tagged_default_and_named_by_the_file` (:565) keeps its assertion and swaps `explain_answer`/CLI `explain` (:577, :579) for `effective_answers`; the section comment at :390 naming "explain, in the two halves the lane reads separately" goes with those two claims; the `searched` assertions at :406 and :437 die, as decided. |
| `test_the_configuration_validator_is_strict_about_the_version_window.py` (9 tests) | **1 dies**: `test_validate_and_config_diff_report_the_same_window_for_the_same_tree` (:225) — it imports `config_diff.diff` (:38) to compare two verbs about one window. | `test_a_stamp_two_steps_behind_refuses_the_run_and_names_the_migrate` (:103) loses the migrate half of its name and its `kmt config migrate … --dry-run` assertion (:117) — the refusal of a two-step gap is not this ticket's subject. `test_a_constraint_this_build_falls_outside_refuses_the_run` (:142) drops `assert "config diff" in finding.message` (:156). Every validate-only assertion stays exactly where it is. |
| `test_a_prepared_tree_says_which_product_it_was_made_for.py` (11 tests) | nothing | `test_a_tree_two_schemas_behind_is_refused_and_the_refusal_names_both_numbers` (:64) keeps "names both numbers" and drops `assert "config migrate" in said` (:71). The sentence the message is asserted against is rewritten in `src/kubementat/config.py`, so this test follows it, and *the remedy must still be named* — a refusal without one is the defect this test already objects to. |
| `test_the_defaults_initialize_seeded_have_a_single_owner.py` (19 tests) | nothing | 3 tests (:249, :265, :354) read through `explain_answer` as their oracle. Each asserts an origin and a value; `effective_answers` gives both, so the assertion stands with a lookup instead of a call. |
| `test_the_eight_former_environment_variables_arrive_only_through_the_layers.py` (6 tests) | nothing | 2 tests (:98, :117), same swap. |
| `test_the_ingress_class_the_manifest_names_is_the_one_the_configuration_answers.py` (8 tests) | nothing | 2 tests (:184, :329), same swap. |

**The two files that pin the CLI surface, because this is where the deletion gets recorded:**

* `tests/test_cli_surface.py` — `EXPECTED_CONFIG_COMMANDS` (:279) loses `explain`, `diff` and `migrate`, and
  the parameter table loses their three rows (:332 and the two after it). This file's whole purpose is to
  refuse silent change to the verb set, so forgetting it is the only way this ticket drifts unnoticed.

**The e2e harness, which is not "a test" in the above sense and still has to be edited:**

* `tests/e2e/harness/config.py` — `config.explain` (:483) and `config.explain.team` (:489) become
  `config.show` rows; the comments at :216 and :334 that name `explain` as the read-back verb follow.
* `tests/e2e/harness/stages.py` — `_config_explain` (:2063) becomes a reader for one key's line out of
  `config show`, and the 5 call sites (:511, :581, :1542, :1589, :1921) keep what each one asserts. Two of
  those failures quote `kmt config explain` in their own message (:1545, :1926), so a failing lane would
  today blame a command that no longer exists; those sentences change with it.
* Anything in the harness that quoted `config diff` / `config migrate` — checked: there is no capability
  row and no stage for either, so nothing.

**Sanity check before you call it done.** Thirty-seven tests leave the tree: 33 with the three deleted
files, the three that `explain` *is* in the layering file, and the one cross-verb comparison in the
validator file. So `uv run pytest -q` should come back green at **1709 passed, 1 skipped** against today's
1746, and the *reason* is the check: the count moves because files left the tree, not because assertions
were loosened. If a file still collects what it used to and used to touch `explain`, it was edited into
passing rather than edited into truth. (`parametrize` can move this number; if it does not match, recount
the collected tests rather than assuming the arithmetic.)

### How to do it, and how to tell it is done

**The specification is not part of these commits — it landed before them, on 2026-11-05.** What is left is
two commits, split by risk: **the `explain` commit moves a working lane mechanism** and is the one that
needs the VM run after it; **the `diff`/`migrate`/`renames` commit deletes code with no live consumer at
all**, and is prose plus removal.

Order within each: delete the verb, delete what only it reaches, re-point the sentences, then run
`uv run pytest -q` and let it name every test that measured deleted behaviour. The census above predicts
that output, so anything unexpected is a consumer the grep missed — read it, do not skip it.
`git diff --stat` should be removals, prose and the re-pointed harness read; a new branch or helper anywhere
means the ticket stopped being a deletion.

**The lane run belongs to this ticket and not before it.** Nothing at `f2d5f1a` gets a VM: the first lane
evidence after the epic started is the smoke lane on the tree that reads back through `config show`, which
is simultaneously the proof that the re-pointing works. Budget for a real VM and a generous timeout, and
attach `result.json`, which names the SHA.

**Done when:** `kmt config --help` offers exactly the seven verbs, and `EXPECTED_CONFIG_COMMANDS` says the
same; `grep -rn "config_explain\|config_diff\|config_migrate\|config_renames\|explain_answer" src tests`
answers nothing; the unit suite is green at the collected count above with those files absent rather than
neutered; the harness reads back through `config show` and a smoke lane is green after the re-pointing;
every user-visible sentence names something that exists (`README_PLATFORM_CONFIG.md`'s "stays current"
section and the parent epic plan's two phase-9 rows still need it; the spec already has it); and
`CHANGELOG.md` records three verb deletions, the withdrawn rename table and its census gate, and the fact
that the upgrade path is no longer measured.

**Landed (2026-11-05) in two commits, exactly as split here.** `2f47365` deletes `explain` and re-points
the harness; `77132ba` deletes `diff`, `migrate` and the table, together with the sentences. The census was
right: thirty-seven tests left the tree and the suite came back green at **1709 passed, 1 skipped**, which
is the number this section predicts, so no file was edited into passing. Three things the plan did not name
and a successor should know:

* `_config_explain` became `_config_shown`, not because the verb changed but because `stages.py` already
  had a `_config_answer(field, value)` that spells a `KEY=VALUE` for `config set`, and two methods with one
  name in one class means the first one stops running. It was the mock test
  `test_the_lane_asserts_go_red_when_the_product_did_nothing.py` that caught it, on the first run.
* The value the harness parses is cut at `   (` and not at a single space, so the tag never enters the
  answer; the helper's comment says so because that is the line a later edit would get wrong.
* Six sentences in `src/` and five in `tests/` named the deleted verbs in prose that was not on the plan's
  tables (`config_reads.py`, `config_init.py:312`, `tekton_utils.py:366`, three test docstrings and two
  CHANGELOG entries that stated present-tense rules). They were found by reading and not by the census, and
  they all say what the reader must do now, which is the sentence the plan asks for.

**The lane run happened.** It is the first VM evidence after the epic started, it proves tickets 1 and 3,
and it proves the harness can read its own state out of `config show` — 57/57 on
`20261105T174849Z`, re-run on the current tree at 57/57 on `20261105T204729Z`.

---

## Standing rules that outlive every ticket

- **No backwards compatibility. No shims, no aliases, no deprecation branches.** If behaviour changes,
  the behaviour changes and `CHANGELOG.md` records it.
- **`kmt config import` is withdrawn (N-10, 2026-11-05).** Do not build it, do not "restore" it, do not
  write a migration verb. A combined tree is *addressable* with `--config-dir`, not migratable.
- **Configuration is written only by `config init` / `config add-environment` / `config add-team` /
  `config set` / `config unset`** (R-15.1). Any new writer — including a harness stage that "just" edits
  a JSON file — is a defect, not a shortcut.
- **Test names describe behaviour, never ticket ids** (`tests/test_test_name_conventions.py` enforces it).
- **No spec references in comments.** No `§x.y`, `R-x.y`, `FR-nn`, `TD-n`, ticket id or plan filename in a
  comment or docstring — a comment is written for a reader who has never seen the specification, and the
  rule is written in prose instead (owner instruction, 2026-11-05; it is in `AGENTS.md` too, because it
  outlives this plan). **The codebase violates this everywhere — 1769 occurrences across 197 files, measured
  in ticket 6.** New or changed code follows the rule immediately; do not mix a comment sweep with a
  ticket that changes behaviour, because the sweep touches every file and every other ticket in this queue
  is a writer.
- **The suite tests kubementat and nothing else.** No self-audits of the harness or of the suite's own
  hygiene (owner instruction, 2026-11-05): a check that the lane does not write configuration with `jq`,
  that a module declares no constant, or that the docs are consistent with the code, is not a fact about
  kubementat. Those things are checked **once, by reading**, when the change lands — they do not become
  permanent tests. The line is: a test may assert what a *stage asserted about the product*, and may read
  a capability row's argv; it may not police the lane's own source. Ticket 3's *Done when* is written
  with this rule applied — the `grep` is the audit, the test is not.
- **One writer in the tree at a time.** Read-only scouts may run alongside.
- e2e lane runs need a human and a real VM; budget generously for timeouts (most inference is local,
  see AGENTS.md), and never restart a stalled lane without reading its last output first.
- **This file is updated by the session that took the step, and the update lands with the step's own
  commit or the one right after it.** Every step of the queue — a ticket, one half of a ticket, a lane
  run — writes the same three things: the **State** cell of its row in the ticket table (with the commit
  SHA, and with "lane run outstanding" whenever that is the truth), a row in **State of the tree** for
  what changed and what a successor has to know, and the **outstanding order** below it, so the next
  agent's first job is the next thing and not a guess about what already happened. Do not save this for
  the end of the session, and do not trust a table written after the fact: a plan that is updated only
  when someone remembers is a plan a successor reads as a guess. The update is prose-only — if the diff
  is longer than that, the step did not land the way the plan thought.

## Tickets (in execution order)

| # | Ticket | Spec | Breaks tree | Effort | lane | State |
|---|---|---|---|---|---|---|
| 0 | Delete `config explain`, `config diff`, `config migrate` and the rename table | R-13.3 withdrawn; upgrade lane out | **yes** | medium | **yes — the run waits for it** | landed `2f47365` + `77132ba`, lane run `20261105T204729Z` (57/57) |
| 1 | The stated-cluster check in CI pods | R-10.4, N-3 | no | medium | no (unit) + one lane run | landed `6018902`, lane run `20261105T174849Z-smoke-install-49d31b2` (57/57) |
| 2 | `config validate` learns the version window | R-13.2 | no | small | no | landed `cb8731c` |
| 3 | The harness stops writing configuration itself | R-16.1, R-16.4 | no | high | yes | landed `f2d5f1a` + `be3e3be` + `d9649f9` + `d341af1`, lane run `20261105T174849Z-smoke-install-49d31b2` (57/57) |
| 4 | The bootstrap lane | R-15.3, R-16.2, R-16.3 | no | medium | yes | landed `5a59421` + `d917a5d` + `e989756`, **green** |
| 5 | The README tells the current truth, last | R-7.1, R-7.2 | no | small | no | **landed** |
| 6 | Comments stop citing the specification | — | no | mechanical | no | **landed — 1713 citations, 8 left by design** |
| — | The full lane needs the ACME contact routing reads | R-9.2 | no | small | **yes** | landed `9f07052`, full lane `20261105T203304Z` (61/61) |

All six tickets are landed. The tree is green on both lanes that exist, on `9f07052`:
`--lane smoke --scope install` at 57/57 (`20261105T204729Z`) and `--lane full --scope install` at
61/61 (`20261105T203304Z`) — the full lane's first green run on this branch. Ticket 4's bootstrap lane
(44/44, `20261105T174105Z`) is green on its own lane and unaffected by the rest.

**The full lane's first green is a finding and not a milestone.** The lane had been failing at
`cap:routing.install:routing` since this branch began, and it failed for a reason no lane could see:
`full` installs the routing group, `routing` is the only thing that reads `CLUSTER_MANAGER_EMAIL`, and
`config init` writes no address because it cannot know anybody's domain. Both ClusterIssuers register an
ACME account with that key, so the product refused — correctly. What made the miss invisible is that the
address was in the harness's constants *and* in the table the suite exports into every guest command, so
the tree looked answered from both ends, and the product reads neither: it reads the environment's
`static.json`, which is the one file the key never reached. A value that is declared, exported, and read
by nobody is indistinguishable from a value that was never declared. The fix is one `config set` on the
layer the key is read from, in the same shape every other stage already uses.

The gate needed a second attempt and is recorded that way, because the first shipped a change that
did nothing. The routing *steps* gate on a feature and the routing *group* arrives on a lane; `full`
installs the group without running the feature and `routing` does the reverse, so naming the same
predicate twice would have left this lane failing. The tell was a green run whose SHA did not contain the
fix — a fix that is not executed by the thing it fixes looks identical to a fix that works, and the only
difference is which revision the `result.json` names.

**Ticket 0 changed that order, and it landed first.** It removed three verbs, which is the one kind of
change that makes an earlier ticket's *Done when* read differently: ticket 2's agreement between validate
and `config diff`, ticket 3's read-back through `config explain`, and R-13.3's rename gate all talked about
code it deleted. **The smoke lane is the next thing that happens on this tree, and nothing after ticket 0
waits for it.** It is owed to tickets 1 and 3 and to ticket 0's own `explain` half at once, because reading
back through `config show` is a live lane mechanism and only a VM run says it works.

Explicitly **not** scheduled (owner decisions, 2026-11-05): the lane-runs-from-a-built-wheel row (later),
the SP-3 spike (later), R-13.4 bump PRs and OQ-1 (owner not ready), OQ-6 (the code already ruled "no
prompt"; close it in the spec prose only if you are already in that file). Anything that says otherwise
about the verbs ticket 0 deletes is stale: `config diff --to V` was an omission, and the omission is
moot now that the verb is gone.

---

## State of the tree (2026-11-05, after ticket 0)

Eight commits on `v2`, base `303c049`. Unit suite: **1709 passed, 1 skipped** — the thirty-seven that left
with ticket 0, counted as files and not as loosened assertions. No lane has been run since `303c049`, and
three of the commits change what a lane executes: tickets 1 and 3 changed what stages do, and ticket 0
re-pointed a read-back they depend on.

| Commit | Ticket | What a successor needs to know |
|---|---|---|
| `9a41099` | N-10 | docs-only: `config import` is withdrawn from spec and plan prose |
| `6018902` | 1 | `StatedClusterMismatchError` in `cluster.py`, checked inside `Context.build` **before** any kubeconfig opens. `cluster-kubeconfig` is a **no-default param** on `run-tekton-pipeline.yml`, `create-secret-from-json.yml` and the example pipeline, so a TaskRun that omits it now fails Tekton's own param validation. Three precedence tests were rewritten because a *contradicting* lower layer is no longer an observable shape |
| `cb8731c` | 2 | `ConfigurationStamp` (`config.py`) is the window's only owner; `config_validate._stamp_findings` turns it into four finding codes. Ticket 0 removed `_stamp_lines` with `config_diff.py`, which leaves validate as the strict home alone — the finding codes and the shared stamp are untouched, and the cross-verb comparison test went with the verb |
| `f2d5f1a` | 3 | the harness, plus **`config set --from-file`** (see §3's *Landed*). 8 new capability rows, 3 new helpers in `stages.py`, `suite.py` exports no credential |
| `2f47365` + `77132ba` | 0 | `kmt config` is seven verbs. The harness reads one key's answer and its layer out of `config show` (`_config_shown`, cut at the three-space separator); `config_diff.py`, `config_migrate.py`, `config_renames.py` and their 33 tests are deleted; the version-window refusals name a `schema_version` bump and no longer a command. The spec edit came first and is its own commit (`4af0338`), because code follows the spec and not the reverse |

**What is outstanding, in the order it should be done.**

Nothing. Every ticket is landed and both lanes are green on `9f07052`. The order below is kept as what
was planned rather than what remains, because a successor reading it should be able to tell that the
tickets were done in the order they were sequenced and not in the order they were written.

1. **Ticket 5, the README.** Done — the first ticket where a reader, and not an agent, was the one harmed.
2. **Ticket 6, the comment sweep.** Done last, as planned, running alone against a quiet tree. It touched
   191 files and nothing that could break, which is exactly why it needed a machine-checkable guarantee.
3. **One more, added by the last lane run.** The full lane had been failing since the branch began and
   nobody had a lane that could see why. See the note above the ticket table.

**One decision this plan did not record, and that must not be re-litigated:** `kmt config set
--from-file` was not in §3, was in the spec (R-16.1's own evidence row, and R-16.3), and had to be
built for ticket 3 to be doable without putting a generated password in an argv. It landed in `f2d5f1a`
with its own behaviour tests. §6's "T-22d still unimplemented" row is now false — the flag exists.

**And one thing the newest commits are guilty of.** `f2d5f1a` and `5116224` were written before the
no-citations rule existed, so their comments cite `R-16.1`, `R-7.2`, `§4.3`, `T-22d` and more. They are
not exempt: ticket 6 starts with `tests/e2e/harness/` and `src/cli/commands/config.py`, which is where
the freshest prose is. Cleaning the legacy code and leaving the newest code citing would be the sweep
done backwards.

## Handover: what the session that scoped ticket 4 learned (2026-11-05)

No code was written in that session. It read the harness and the bootstrap path, ran `kmt config init`
against a real directory on a machine with real `git`, real `git-crypt` and real `gpg`, and measured what
the lane will have to assert against. Everything below is measured, not inferred, and it is the shortest
route to ticket 4 that does not re-derive it.

**State of the tree at the handover.** HEAD `c03b08e` (the two rules from the owner, in `AGENTS.md`).
Ticket 3 is `f2d5f1a`. Unit suite: **1746 passed, 1 skipped** in ~52 s. Still no lane run since
`303c049` — tickets 1 and 3 both moved behaviour a stage asserts, so the outstanding smoke run is the
first job, before any new stage code is trusted.

### What ticket 4 hooks into, and the trap in it

- `tests/e2e/harness/config.py`: `LANES` (~:165), `SCOPES` (~:168), `CAPABILITIES` (~:380). The
  everyday identity is `ENVIRONMENT = "dev"`, `TEAM = "smoke"`, guest tree `/var/tmp/kmt-e2e`.
- `tests/e2e/harness/stages.py`: `stages()` at **:3939**, `stage_teardown`'s scope branch at **:2523**.
- **The trap:** `stages()` builds all six `(name, function, seconds)` tuples *before* it filters by
  scope, so every one of the six `self.lane.timeouts[name]` lookups runs for every lane. Adding a stage
  name to that literal without giving `smoke`, `full` and `routing` the same key is a `KeyError` before
  stage one. Fix it before adding anything: select the stage names first, then look up the timeout for
  the selected ones only. A lane should pay a budget only for a stage it actually runs.
- **The shape choice.** Today the scope picks the stage list and the lane picks the VM shape, and the two
  are independent. A bootstrap lane wants a different stage list. Either give `Lane` a `stages` field (the
  lane names its own lifecycle) or add a `bootstrap` scope and run `--lane bootstrap --scope bootstrap`.
  The recommendation is the scope, so "how much of the lifecycle" stays one concept, but note what each
  costs before choosing — this is the one decision in ticket 4 that is not forced by anything.
- **Do not touch `stage_teardown`.** The cheapest bootstrap scope is `("01-provision", "02-bootstrap")`:
  no teardown stage, because the VM is destroyed in a `finally` by INV-2 either way and row 10's claim
  ends at "installed", not "uninstalled". The everyday uninstall assertions already belong to the install
  scope. Reaching for teardown means editing the `scope == "install"` branch for a claim nobody is making.

### Capability rows: name the root once, do not duplicate rows

Every config row today hardcodes `{workdir}/platform_config` in its argv (13 rows, `config.py:380-508`).
A lane that prepares a directory of its own has two options, and only one is survivable:

* *Bad:* `.bootstrap` variants of the same verbs. Two rows for one verb, and they will drift the first
  time somebody changes the verb.
* *Good:* one placeholder. `LANE_PLACEHOLDERS` gains `"config_dir": f"{GUEST_TREE}/platform_config"`, the
  rows say `{config_dir}`, and a lane that wants a different root says so once in its own shape. Add a
  `config.validate` row while you are there — `config validate` has no row at all, and row 8 of the
  checklist needs it.

`install` and `uninstall` take no `--config-dir` today; they get the root from the exported
`KUBEMENTAT_PLATFORM_CONFIG_DIR`. Putting `--config-dir {config_dir}` on those two rows is a no-op for
every lane that exists (same value, one from the flag, one from the environment) and is what a lane that
writes somewhere else needs. It is also the more honest spelling: a configuration root is named and never
located.

**The row 9 assert has a trap of its own.** A second `config init` with *identical* arguments prints
`skipped <path>` for every write target it touched, exits 0, leaves `git status --porcelain` inside the
tree empty, and leaves the commit count where it was. A second `init` that omits an answer the first run
passed refuses instead of skipping: `…/dev/static.json already exists and is not what kubementat would
write there`. So the lane's second call must repeat every `--set` the first call got, or the assert measures
a refusal and not the skip it means to measure.

### What `config init` actually prints, measured (git 2.43, git-crypt 0.7.0, real gpg)

One run into an empty directory returned 0 and, in this order: `created <path>` lines for each write,
then `Environment 'dev' is configured, but it does not address a cluster yet: CLUSTER_KUBE_CONTEXT is
null in cfg/dev/static.json.` and two more lines, then three notes (the symmetric key is the ability to
read every credential; the commit is local and unpushed; the deployer public key lives in the directory),
then `then kmt install dev smoke --config-dir cfg, which is the whole of ready to use`. Worth knowing for
the assert prose: the *root* is reported twice (`created /tmp/bootcfg/cfg` appears twice in a first run,
and `skipped /tmp/bootcfg/cfg` twice in a second). Nothing in a lane should count those lines as a step
count; assert the paths, not the tally.

The read-back verbs answer the way the lane needs. This was measured through `config explain`, which
ticket 0 deletes: the same facts come back through `config show`, which tags every key's line with its
layer and file. What has no surviving form is `searched` (the files precedence consulted), which ticket 0
notes and accepts.

| Read on a freshly prepared tree | Answer |
|---|---|
| `config show dev --config-dir <configuration-root>`, the `CLUSTER_KUBE_CONTEXT` line | `CLUSTER_KUBE_CONTEXT = null   (default from …)` — the value is named and `null`, not absent |
| `config show dev --config-dir <configuration-root>`, the `CLUSTER_KUBECONFIG_PATH` line | the same shape |
| `config show dev --config-dir <configuration-root>`, the `GRAFANA_ADMIN_USER` line | `grafadmin` (the seeded default) |
| `config show dev --config-dir <configuration-root>`, the `GRAFANA_ADMIN_PASSWORD` line | a 43-character generated token, tagged `generated` |
| `config validate dev smoke --config-dir <configuration-root>` | exit 0; stdout `no errors for environment dev (1 warnings)`; stderr exactly one `WARNING: … does not address a cluster yet: …` |

Row 8 is satisfiable as the spec wants it, today, with no code change.

### The prepared tree, exactly as `config init` leaves it

Worth having spelled out because four of the asserts read these paths and one reads the absence of a path.
Measured on a run into an empty directory (`--environment dev --team smoke --set
DOCKER_REGISTRY_BASE_URL=docker.io`):

```
cfg/.gitattributes            # one line: *.encrypted.* filter=git-crypt diff=git-crypt
cfg/.gitignore                # the five key-material names, each gitignored
    deployer_ssh_key / deployer_ssh_key.pub / gpg_private.key / gpg_public_key.gpg
    git_crypt_symmetric.key
cfg/kubementat.yaml           # kubementat: ">=0.4,<2", schema_version: 1
cfg/dev/static.json           # CLUSTER_KUBE_CONTEXT: null, CLUSTER_KUBECONFIG_PATH: null
cfg/dev/static.encrypted.json # GRAFANA_ADMIN_USER: grafadmin, + the generated password
cfg/dev/smoke/static{,.encrypted}.json
cfg/dev/tekton/tasks/  cfg/dev/tekton/pipelines/
cfg/dev/smoke/tekton/{tasks,pipelines,pipeline-runs,triggers}/
cfg/deployer_ssh_key{,.pub}   cfg/gpg_private.key   cfg/gpg_public_key.gpg
cfg/git_crypt_symmetric.key   # mode 0600
cfg/.git/git-crypt/keys/default                       # the keystore, step 6's evidence
cfg/.git-crypt/keys/default/0/<keyid>.gpg              # the registered collaborator
```

`git -C cfg log --oneline | wc -l` answers **1**: the whole preparation is one commit, and that is assert
row 1's whole content — "the first commit exists" is not a vague claim, it is a count.

### The ten properties: one of them read false until 2026-11-05

Rows 1, 2, 3, 5, 7, 8 and 9 all hold on the tree measured here. What the asserts have to look at:

| Row | Read it here |
|---|---|
| 1 | `git -C <dir> rev-parse --git-dir` answers, and the commit count is 1 — `config init` commits exactly once |
| 2 | the keystore at `.git/git-crypt/keys/default`, a collaborator at `.git-crypt/keys/default/0/<keyid>.gpg`, and `git_crypt_symmetric.key` at the root, mode 0600. All three appeared in the measured run |
| 3 | `deployer_ssh_key`, `deployer_ssh_key.pub`, `gpg_private.key`, `gpg_public_key.gpg` inside the config dir — and absent from the checkout, which is the half R-11.4 is about and the half a present-tense assert misses |
| 5 | the environment's `static.json` + `static.encrypted.json`, the team's two, `kubementat.yaml`, and the `tekton/` layer directories — `dev/tekton/{tasks,pipelines}` and `dev/smoke/tekton/{tasks,pipelines,pipeline-runs,triggers}` — and no `REPLACEME_` anywhere. The marker's absence is a fact about the shipped seed and not about this run, so say that in the assert's prose rather than letting it read like an achievement |

**Row 6 is false as written.** It says the bootstrap invents no credential and that the only verb that
generates is `config set --generate`. The shipped product generates during `config init`: see
`constants.GENERATED_ENCRYPTED_ANSWERS = ("GRAFANA_ADMIN_PASSWORD",)` and `config_init.py:444`, and the
measured token above, which is not an answer anybody gave. The assert should say what is true — the
encrypted document carries the seeded default, the keys the lane answered with `--encrypted-set`, and the
one key the product's own generation table names; nothing else — and the assert's prose should name the
exception, because a reader who finds `GRAFANA_ADMIN_PASSWORD` in a tree that was "invented nothing" will
trust the wrong sentence.

**The spec row was corrected on 2026-11-05**, so the disagreement this handover warned about no longer
exists: §15.3's row 6 now says the bootstrap generates exactly one credential and only where nothing
answers, and it names a test that exists (the row used to name `test_the_bootstrap_invents_no_credentials.py`,
which never did). That does not retire the assert — it settles what the assert must say. Write the property
the corrected row states, and do not weaken it back into "invents nothing".

### The two git-crypt asserts, measured so they are not guessed

Both were reproduced with real `git` 2.43 and real `git-crypt` 0.7.0. Read this before writing either one,
because the instrument the plan names for the second assert is weaker than the sentence around it sounds,
and a lane that asserts through it would be green on a tree that really did leak.

**How the mechanism actually works.** `git-crypt init` writes its driver into **`.git/config`** —
`[filter "git-crypt"]` with `clean`/`smudge`/`required = true`, and `[diff "git-crypt"] textconv = git-crypt
diff` — and it writes nothing into `.git/info/attributes`, which keeps holding only `exclude`. So `init`
registers the *ability* to encrypt and never decides *which files* it applies to: that decision belongs
entirely to the repository's own `.gitattributes`. The ordering rule is therefore load-bearing in the real
way — a file staged before `.gitattributes` exists goes into the object store unfiltered — and nothing
git-crypt does can be counted on to cover for a late `.gitattributes`.

**Assert 1 (worktree plaintext while `git show HEAD:<path>` is ciphertext) is the right instrument, and it
is narrower than it looks.** `git cat-file blob <rev>:<path>` and `git show HEAD:<path>` both read the
stored object and apply no filter, so on a correctly ordered tree they answer the byte string that starts
`\0GITCRYPT\0` while the worktree answers plaintext. Reproduced broken ordering: `git init`,
`git-crypt init`, write `dev/static.encrypted.json`, `git add`, commit — then write `.gitattributes` and
commit again. Measured on that tree: the first commit's blob is the JSON verbatim, the worktree is
plaintext, and `git-crypt status` reports the path as encrypted **without a warning**, because by then the
file *is* encrypted. Which means:

* if the file is later re-added (any stage, any edit, any `git add -A`), `git show HEAD:<path>` answers
  ciphertext and assert 1 goes **green while the history still holds the plaintext**;
* the pure form of the defect — attributes late, file never re-added — does leave a plaintext blob at
  `HEAD`, and assert 1 catches that one directly.

Both shapes are worth naming in the mock test. A mock that only makes `git show` answer plaintext proves
the assert can fail; a mock that plants "plaintext early, ciphertext now" proves it measures what it claims.

**Assert 2, as the plan writes it, cannot see the leak.** `git log --all -p -- …/static.encrypted.json` run
against the tree that really did store plaintext in an early commit printed **no plaintext lines at all**,
and wrote `git-crypt: Warning: file not encrypted` to stderr. Once `.gitattributes` is live the textconv
driver owns how that file is displayed, and what it displays is not the stored bytes. On a clean tree the
same command is equally quiet, so the grep answers zero either way: the command measures the diff driver,
not the repository. (It does leak plaintext in other shapes — a tree whose blobs were never re-filtered
after the attributes arrived — so "it printed nothing" is not evidence of safety; it is evidence that the
answer is not under control here.)

**Write assert 2 against the object store, where the question is asked of bytes that were actually written.**
For every revision the path ever had, from `git log --all --format=%H -- <path>`, read
`git cat-file blob <rev>:<path>` and grep the result for the credential. Verified: the same tree that left
`git log -p` silent answers the plaintext JSON at the late commit and ciphertext everywhere after it. That
is the honest form of "no plaintext credential, ever". Say so in the assert's prose and in the ticket
report — the spec's sentence states the property correctly and names an instrument that cannot see it, and
that is a fact about the document worth recording rather than quietly routing around.

**Read the credential without giving it a second home.** Do not put a generated value in the lane's own
argv — that is the defect `config set --from-file` exists to close. Resolve it inside the guest: one command
that reads the plaintext answer out of the working tree and greps the revisions against that shell variable.
That is a read, which R-16.1 permits, and there is nothing to log. Pulling the value back through a
read-back verb and passing it over ssh instead puts it in the guest's process list, and the lane should
not choose where a credential lives when the guest already knows it.

**The lane must not run `git-crypt lock` or `unlock`.** The product only reads; the worktree is plaintext by
design and stays that way, and both asserts work in the state `config init` leaves the tree in. Locking it
would measure a state the product never creates, and it would be a write.

**The mock test** has to be built from two independent reads — the worktree bytes, and `git show
HEAD:<path>` — because `FakeGuest` answers per command, and a single command that hands back a verdict
word would make the test measure its own fixture. Follow
`tests/test_the_lane_asserts_go_red_when_the_product_did_nothing.py`: `load_harness()`, `a_runner()`,
`Answer`, `Recording`.

### What the guest already has

`tests/e2e/stages/01-provision.sh:53-55` installs `git-crypt`, `yq`, `python3-pip`, `curl`,
`ca-certificates` and `jq`, and :208-211 installs `gnupg` if `gpg` is missing. The lane needs no new
package step, and its real `git`, real `git-crypt` and real `gpg` are already there. The slow part is
generating a GPG key inside a VM (the entropy), so give `02-bootstrap` a budget wider than the one that
looks obvious and let the first real run tighten it. Row 10 needs `k3s`, so the lane cannot be cheaper than
`smoke` at the VM-shape level; what is cheaper is the stage list — no rerun, no features, no pipeline.

One correction to §4's prose: this lane is *not* the cheapest in the suite in the way the sentence reads.
It is cheaper per stage and it touches no cluster until its last assert, but its VM still has to run
`k3s`, so nothing about its shape should shrink below `smoke`.

### Ticket 5, the exact places to edit

| Where | Why it is wrong |
|---|---|
| `README.md:168-182` | the eight `export …` lines plus `uv run kmt initialize dev dev1` — a command the CLI no longer has (T-28). Replace with `kmt config init --environment … --team … --set … --config-dir …`; check the flags against `src/cli/commands/config.py` and not against this table |
| `README.md:184-201` | *right* about `CLUSTER_KUBE_CONTEXT` being null, but it tells a human to "edit that file" by hand, which the five-verbs-only rule forbids. The section's job is to send the reader to `kmt config set` and then to `kmt environments list` |
| `README.md:150-166` | the `AUTOMATION_GIT_*` / `KUBERNETES_DEFAULT_STORAGE_CLASS` / `DOCKER_REGISTRY_BASE_URL` environment-variable block. Nothing in `src/` reads `KUBERNETES_DEFAULT_STORAGE_CLASS`; `BASE_DOMAIN` exists as a configuration *key* (`tekton_utils.py:742`), not as a variable. Presenting keys as variables is the same defect R-7.2 closed |
| `docs/README_PLATFORM_CONFIG.md:41`, `docs/index-overview.md:47,174` | these name `kmt initialize` in past-tense narrative, which may still be true prose. Re-read before deleting anything: the ticket's rule is *no document sends a reader to a deleted command*, not *no document mentions one* |

The plan's other candidates (`docs/README_TEKTON.md`, `docs/index-overview.md:103,153-154`) are already
clean — re-grep rather than re-fix. The only doc-guard-shaped test in the suite is
`tests/test_the_docs_that_point_at_tekton_ci_point_at_what_stays_there.py`; if a README guard is wanted,
follow that file's shape and no other.

### Ticket 6, re-measured at `c03b08e`

The plan's numbers hold. `grep -rnE '§[0-9]|R-[0-9]+\.|FR-[0-9]|TD-[0-9]+|N-[0-9]+|T-[0-9]+[a-c]?|INV-[0-9]' --include='*.py'`:
**580 in `src/` (40 files), 1189 in `tests/` (157 files)**. Heaviest first:
`src/kubementat/cluster.py` 103, `tests/e2e/harness/stages.py` 97, `tests/test_cli_surface.py` 78,
`tests/e2e/harness/config.py` 61, `src/kubementat/config.py` 56, `src/kubementat/config_init.py` 37,
`tests/conftest.py` 36. Order of work is unchanged (`src/kubementat/`, `src/cli/`, `tests/e2e/harness/`,
then `tests/`), and the audit stays `git diff --stat` per commit looking prose-only: any control flow in
this ticket's diff is the ticket failing.

**What to do, in order**

1. **Ticket 0 first, alone**: delete `config explain`, `config diff`, `config migrate` and the rename
   table (§0). Two commits inside it — the `explain` half re-points a live lane mechanism, the rest is
   prose and deletion — and every later ticket reads a tree that no longer carries those verbs.
2. **Run the outstanding smoke lane** (`--lane smoke --scope install`) **on ticket 0's tree**, and attach
   its `result.json`. Tickets 1 and 3 both moved behaviour a stage asserts and nothing has touched a VM
   since `303c049`; this run is also the first evidence that the harness's `config show` read-back works,
   so it is doing two jobs and neither is optional. A new stage written on top of an unverified harness is
   a new stage whose failure you cannot attribute.
3. **Fix `stages()` before adding a stage**, so the lookup happens for selected stages only. That change
   is testable in the unit suite and it is the only edit ticket 4 makes to code that existing lanes run.
4. **Add the `{config_dir}` placeholder** and thread it through the config rows, then `--config-dir` on
   `install` and `uninstall`. Run `--list-capabilities`, or resolve the rows in Python, and confirm every
   row still produces the argv it produced before for the lanes that exist — the value is unchanged, so a
   difference here is the placeholder and not the product.
5. **Write the stage, one assert per property**, in the order the checklist lists them, and let the
   `result.json` names be the sentence for the property rather than a label. The lane's last assert is the
   install reaching the cluster; name it so.
6. **Write the mock test** for the ordering assert, including the shape where the plaintext blob is older
   than the assert's view of `HEAD`, for the reason given in the git-crypt section above.
7. Then the unit suite, then a real bootstrap-lane run, and only then ticket 5. Ticket 6 runs alone,
   afterwards, because it rewrites the prose of every file this ticket adds.

### Standing things that cost the next agent time

- A capability row can be resolved without a VM: `python3 -c "import tests.e2e.harness.config as c; \`
  print(c.argv_for('config.set', env='dev', answer='X=1'))"` (re-locate the accessor by name). Every argv
  bug in ticket 3's aftermath answered like that, and it answers before a stage fails on a real VM.
- `tests/test_test_name_conventions.py` will reject a test name that carries a ticket id, and that
  includes any name written while ticket 6 is still citing things.
- A lane run is not reported without its `result.json` naming the git SHA.
- One writer in the tree: the comment sweep touches 197 files, so ticket 6 runs alone and after ticket 4's
  stage code exists.

---

## 1. The stated-cluster check (`R-10.4` / N-3)

**The problem, plainly.** A pipeline pod has two answers to "which cluster" and no referee. The cloned
config repo declares `CLUSTER_KUBE_CONTEXT` in `<env>/static.json`, but the kubeconfig file that context
names is a *machine-local* path that does not exist in a pod (R-6.1) — the declaration is a dead
reference. The pod's own mounted service-account credentials would answer, but v2's resolution order
puts the config declaration (layer 3) ahead of the service account (layer 4), and `ClusterTarget.
from_kubeconfig` refuses outright when the named file is missing (`src/kubementat/cluster.py:177-199`)
instead of falling through. Today the CI templates state no cluster at all: the `kmt` steps in
`src/kubementat/assets/tekton_ci/tasks/deploy-local-helm-chart.yml` name the *root* (T-71) but not the
cluster.

**The rule to implement** (spec §10.4, the most important line of the revision):

1. The pipeline **states** its cluster with v2's layer-1 dials — `--kubeconfig` / `--context` / the
   `CLUSTER_KUBECONFIG_PATH` variable — on every `kmt` step that reaches a cluster.
2. kubementat compares that statement with the cloned config's declared `CLUSTER_KUBE_CONTEXT`, and a
   mismatch is a refusal that names both claims and both their sources. The config repo's claim is about
   *which environment*; the TaskRun's claim is about *where the work runs*; when they disagree,
   kubementat must not be the thing that decides.
3. v2's resolution ordering stays untouched. Do **not** teach layer 3 to fall through when its file is
   unreadable — the spec forbids that explicitly ("a configuration that cannot find the cluster it
   declares does not get to discover a different one").

**Where to put it:** the comparison belongs on the read path that resolves the root and the target
together (`src/kubementat/cluster.py`, `Context.build`, and/or `src/kubementat/automations/ci.py:143`'s
`require_target` site), reached whenever a layer-1 dial was *stated*; the TaskRun change belongs in the
shipped templates under `src/kubementat/assets/tekton_ci/`.

**Done when:**
- a unit test states a cluster that matches the clone's declaration and asserts the run proceeds, and a
  second test states a *different* cluster and asserts a refusal naming both claims (the spec's wording:
  the statement and the declaration, each with the file or flag that wrote it);
- a unit test hands a stated cluster plus a declaration whose kubeconfig file does not exist and asserts
  the refusal is the stated-vs-declared comparison, not the `from_kubeconfig` file-not-found error;
- every `kmt` invocation in the shipped deploy templates names its cluster in v2's resolution-report
  shape (§9.5), and a guard test reads that agreement off the templates the way T-71's guard reads the
  root agreement;
- no `kmt` invocation in the pipeline execs `git-crypt` (already true — this is a regression guard).

## 2. `config validate` learns the version window (`R-13.2`)

**The problem.** The stamp check (`check_configuration_stamp` in `src/kubementat/config.py`) runs on
every verb through `kmt_context`: version string warns and continues, `schema_version` two steps behind
refuses. But §13.2 said the strict home for version drift was `config validate` together with
`config diff` — a pair, and ticket 0 deletes the second half of it, leaving validate alone — and
`src/kubementat/config_validate.py` says out loud that it deliberately skips the window (lines ~33 and
~187: "the version window is not checked here, on purpose ... belongs to phase 9"). Phase 9 landed
without picking it up. So `config validate` currently passes a tree whose stamp is three versions stale,
which is the exact state §13.5 calls a refusal.

**Done when:** validate collects the stamp findings the way it collects every other fault (one pass,
then one exit code), the deliberate-skip comments in `config_validate.py` are replaced by what the rule
now says, and tests cover: window outside → non-zero; schema too old → non-zero with the bump sentence;
current → no finding.

**What ticket 0 removes from this landed ticket.** Its *Done when* asked that validate and `config diff`
never disagree, because two verbs read one `ConfigurationStamp`; it landed
`test_validate_and_config_diff_report_the_same_window_for_the_same_tree` for that. With `config diff`
deleted there is nothing to agree *with*, so that comparison goes (ticket 0 lists it) and validate becomes
the strict home alone. The four finding codes and the shared `ConfigurationStamp` are untouched — the
finding is not what came out, the cross-verb proof was.

## 3. The harness stops writing configuration itself (`R-16.1`, `R-16.4`)

**The problem.** The suite creates its tree with `kmt config init` (capability `config.init`,
`tests/e2e/harness/config.py`) — that part is right. But it then *edits* that tree behind kubementat's
back with four `jq` writes, and injects one credential through the ambient environment. A suite that
configures its cluster with `jq` proves that `jq` works; none of these assertions are about kubementat.
Each of the four retires as follows (line numbers at `303c049`, re-locate by function name):

| Where | Today | Becomes |
|---|---|---|
| `stages.py:1992` `_set_config_field` — a `jq --arg` write, used by `_declare_the_cluster` (`stages.py:528`) for `CLUSTER_KUBE_CONTEXT` | the §4.2-layer-3 assertion is about a file `jq` wrote | `kmt config set <env> CLUSTER_KUBE_CONTEXT=... --config-dir <configuration-root>` — and the assertion `environment-names-its-cluster-in-static-json` becomes evidence about kubementat |
| `stages.py:751` `_null_config_field` — `jq` sets a key to `null` for the §4.3 refusal test | `jq` cannot express the not-yet-named state, so the test mutates a shape `jq` made | `kmt config unset` — that is the real state the product leaves behind |
| `stages.py:491` `_point_pipeline_at_a_resolvable_image` — one `jq` rewriting `DOCKER_REGISTRY_BASE_URL`, `TEKTON_CI_IMAGE_NAME`, `TEKTON_CI_IMAGE_TAG` after init | three edits after init where init could have answered | init-time answers: `--set` on the `config.init` capability, per R-16.4. Rule for which identity keys get a `--set`: only the ones whose value differs from what the layers already answer; identity that needs no answer stays in `config.py` for the lane's own assertions, not in an argument nobody asked for. |
| `stages.py:2659` `_seed_grafana_account_row` — a `jq --slurpfile` merging `GRAFANA_USERS` + `GRAFANA_SERVICE_ACCOUNT_TOKEN` into the encrypted file | the lane declares a credential through a path no operator has | `kmt config set --encrypted` (per item; the row is one answer per key) |
| `suite.py:65` — `GRAFANA_ADMIN_PASSWORD` randomized into the guest's environment every run | a credential entering through the ambient environment, which R-7.2/R-11.8 delete | `config init`'s seeded answer or `config set --encrypted` per run, read back through `kmt config show` |

**Also required by R-16.1/R-16.4:** the collision and §4.3-refusal lanes read state back through
`kmt config show` — ticket 0 re-points that read-back, because it deletes `explain` and `show`'s per-key
line carries the layer the way `explain`'s stderr used to; `INTEGRITY_FILES` byte-identity asserts keep their assertions and move their paths
to the config directory (R-11.4 already moved the files). Do not weaken any assertion to make a verb
fit: where a verb cannot express exactly what a stage needed, the verb is the gap — report it, do not
work around it.

**Done when:** `grep -n "jq" tests/e2e/harness/stages.py` shows reads only (the seven diagnostic reads
may stay); `suite.py` injects no configuration or credential variables at all; a full smoke-lane run is
green with its `result.json` attached to the report; `git status` inside the guest tree after a run is
clean for the config directory (everything the lane changed, it changed through a verb that committed
or said why not).

**Landed (`f2d5f1a`).** The `grep` is clean of writes: every remaining `jq` in `stages.py` is a read
(`-r` / `-c`, one value, no redirection, no `--slurpfile`), and `suite.py` exports nothing but lane
identity plus `KUBEMENTAT_PLATFORM_CONFIG_DIR`. Six corrections to the table above, because the table
was written from `303c049` and under-counted:

* **There were seven write sites, not four.** The plan listed four. Also gone: `cp -a` of `dev`'s
  directory for the collision fixture, `mkdir -p` + two `write_file`s for the unresolvable-sibling
  fixture, `mkdir -p` + two more `write_file`s for the foreign-team scope fixture, and two `mv`s of the
  team's `static.json` in the config-negative stage. All of them are `config add-environment` /
  `config add-team` / `config set` / `config unset` now.
* **The config-negative stage lost its claim twice, and the second time lost the row.** `config unset`
  clears both files of a pair, so no file goes missing and the row could no longer assert that a
  *missing file* is named with a path. What was left -- that a layer whose key was removed is refused --
  turned out to be unreachable too: the key the probe used is answered by a rule over the team's name,
  so a layer that declares nothing still resolves, and no refusal ever happens. The row is deleted
  rather than left red against a correct tree, and the claim is not silently dropped: it is asserted by
  `tests/test_config_missing_team_config.py`, which needs no lane to make a layer unrippable. The
  feature keeps the half that is reachable and measures something -- which team directory a scoped
  command writes into.
* **The §4.3 refusal asserts `is not set` and not `is null`.** `unset` deletes the key; `null` was the
  shape `jq` left behind and `config init` writes only at the moment of preparation. The refusal's other
  three parts (environment, file path, key) are unchanged.
* **Two more credentials left the lane than the plan named.** `GRAFANA_ADMIN_USER` as well as
  `GRAFANA_ADMIN_PASSWORD`, because both were exported and neither is an answer: the shipped layer names
  the admin user and `config init` invents the password.
* **A generated credential could not travel through `config set --encrypted` as written.** An argv is
  visible in `ps`, so the grafana row and the foreign-team deploy key go through `config set --from-file`,
  which is why that flag exists today (see **State of the tree**, and the flag's own tests at
  `tests/test_an_answers_document_answers_every_key_it_carries.py`). The capability rows that name a
  document take a new placeholder, `answers_document`.
* **`_point_pipeline_at_a_resolvable_image` became an assert and not a write.** Per R-16.4's rule it
  reads the three answers `config init` gave (`--set` for two, the shipped layer for the tag) and says
  whether the reference they compose into is the one the run pulls.

**Done on the machine, not just in the unit suite.**
`~/.kmt-e2e/runs/20261105T174849Z-smoke-install-49d31b2/result.json`: 57 assertions, 0 failed, 295
seconds, against the SHA that carries the change. Every configuration read in the stage list goes
through a `kmt config` verb, so the run proves the rule the ticket was about rather than proving it
in a fixture. The two things that failed on the way -- a namespace a lane had never told anything,
and a deploy-key round trip that was green for a command that had not run -- were both found by this
lane and are recorded in the CHANGELOG, which is the point of running it at all.

## 4. The bootstrap lane (`R-15.3`, `R-16.2`, `R-16.3`)

**The problem.** No lane proves "one command → ready → installed" as one measured sequence; the ten
properties of R-15.3's checklist exist as unit tests against fixtures, which cannot prove real
git-crypt ordering. R-16.2's owner ruling says per-lane config directories (OQ-7 decided), so this lane
is the cheapest in the suite until its last assert.

**The lane:** `config init` into a fresh, empty, lane-owned directory in the guest (real `git`, real
`git-crypt`, real gpg — no mocks), then assert all ten rows of R-15.3's checklist against the real
filesystem, then `kmt install` against it and confirm it reaches the cluster with no hand-written file
in between (TD-1's first half). The two asserts that decide whether git-crypt is *live*, not just
configured (R-16.3):

1. the working-tree content of `static.encrypted.json` is plaintext while `git show HEAD:<path>` of the
   same file is ciphertext — the assert that catches a `.gitattributes` written too late;
2. `git log --all -p -- …/static.encrypted.json` contains no plaintext credential, ever.

Wire it as a new lane in `tests/e2e/harness/config.py`'s `LANES` (name it for the behaviour, e.g.
`bootstrap`) plus the stage(s) in `stages.py`, and make its `result.json` carry the ten properties as
ten named asserts. Row 10 (`init → install`) is the lane's, not the unit suite's, and that is the point.

**Read the Handover section before writing this ticket.** It carries what a session that ran
`config init` for real found: the `stages()` KeyError trap, the `{config_dir}` decision, the row-9 trap
where a second `init` without the same answers refuses instead of skipping, the fact that checklist row 6
is stale because `config init` does generate `GRAFANA_ADMIN_PASSWORD`, and the measured git-crypt
behaviour both asserts rest on.

**Done when:** the lane's `result.json` names all ten properties; a deliberately broken ordering
(mock: write `.gitattributes` after the first encrypted file, in a unit-level harness test) turns assert
1 red; the lane is green on a real run with the `result.json` reported.

**Landed, and the last clause is the only thing still owed.** The lane exists as `--lane bootstrap
--scope bootstrap`, its stage runs the ten properties in checklist order against the real filesystem,
and `tests/test_the_bootstrap_lane_reads_the_stored_bytes_and_not_the_displayed_ones.py` shows assert 1
going red against the shape a late `.gitattributes` leaves. It has never executed on a VM. Two
differences from the ticket's text are deliberate and stated rather than left for whoever trips over
them: assert 2 is written against the object store and not against `git log -p`, for the reason the
Handover measures and this file records above, and the lane prepares its directory outside the guest's
copy of the checkout because the keypair property has an absence in it that a tree inside the checkout
cannot be read for.

**Closed on the machine, and it took three runs to get there.** `~/.kmt-e2e/runs/
20261105T174105Z-bootstrap-bootstrap-e989756/result.json`: 44 assertions, 0 failed, 246 seconds, on
git SHA `e989756`, with all ten properties named as asserts and green. The first two runs failed, and
both failures were the harness reading its own work wrongly rather than the product doing anything
different -- one over a `stat` format written as two file operands, one over the word "errors" inside
the line that says there are none. The measured cost is 4 vcpu, 6 GiB and 25 GiB for the whole lane,
against roughly nine minutes for the smoke lane, which is what R-16.2's cheapest-lane ruling
predicted; the budgets this lane was given were guesses and came out roughly right.

## 5. The README tells the current truth, last

`README.md`'s quickstart (`~:165-210`) still tells a reader to export the eight deleted
`KUBEMENTAT_*` variables and run `uv run kmt initialize dev dev1` — both deleted by this epic, the
command unknown to the CLI (T-28). `7d5fbdd` fixed other prose and missed the quickstart. Rewrite the
section as the actual bootstrap: `kmt config init --environment ... --team ... --set ... --config-dir ...` (the
flags the command actually takes, checked against `src/cli/commands/config.py`), the cluster-naming
sentence `init` prints (R-6.3), `kmt environments list`, then `kmt install`. Sweep the rest of the docs
the same way (the epic's docs row named `docs/README_PLATFORM_CONFIG.md:88-123`, `docs/README_TEKTON.md`,
`docs/index-overview.md:103,153-154` as candidates — re-grep, they are partly fixed already).

**Done when:** no document, README or otherwise, sends a reader to a deleted command or a deleted
variable; a guard test for the README quickstart exists if one fits the codebase's existing doc-guard
pattern (`tests/test_*documentation*` — look, then follow it); a human reading only the README can go
from zero to `kmt install` on a real machine using commands that parse.

**Landed.** The pattern to follow turned out to be
`test_an_instruction_that_names_a_command_is_one_that_parses.py`, which runs a printed instruction and
grades it on Click's usage error rather than on whether it mentions a command name. That test is about
what a refusal prints; `test_the_commands_the_readme_prints_are_commands.py` is the same rule applied to
what a document prints, and both directions are demonstrated by editing the README and watching the
guard go red. The quickstart was run by hand as a check of the *last* clause — `config init` with
`--set` and `--encrypted-set`, then `config set` for the cluster, then `environments list` — and reached
the point where `install` would address a real cluster, which is as far as it can be taken without a
machine. What the sweep found is that most of the documentation had already been corrected by the
earlier tickets, and the one exception is a specification that describes a deleted command in the
present tense while the rest of what it settles still holds; it now says which of its claims survived
instead of being rewritten where a reader could not tell.

---

## 6. Comments stop citing the specification

**The problem.** The rule is now in `AGENTS.md`: a comment is written for a reader who has never seen
the specification. The codebase predates it and cites everything. Measured at `5116224` with
`grep -rnE '§[0-9]|R-[0-9]+\.|FR-[0-9]|TD-[0-9]+|N-[0-9]+|T-[0-9]+[a-c]?|INV-[0-9]' --include='*.py'`:

| Area | Occurrences | Files |
|---|---:|---:|
| `src/`, `src/cli/` | 580 | 40 |
| `tests/` | 1189 | 157 |

`src/kubementat/config_init.py`, `src/kubementat/cluster.py` and `src/cli/commands/config.py` carry most
of the `src/` total, and `tests/` is the bigger half because every test file's module docstring opens by
naming the rule it came from. The `tests/` number also includes `tests/e2e/harness/`, whose stages cite
the plan they were written against; that is the same defect in the harness, not a different one.

**What replaces each citation.** There is no table of codes to look up and no translation table to keep
— that would be a second source of truth and would age exactly as badly as the citations do. Each
reference becomes the sentence it stands for: `§4.2's layer 3` becomes *the environment's
`static.json`, which is the layer an operator edits*; `R-6.3` becomes *a tree `config init` made
addresses no cluster until a human names one*; `TD-1` becomes *one directory is exactly one cluster*.
Where a citation is doing real work — the reason a refusal is shaped the way it is — the reason goes in
the comment. Where the citation was decoration, it goes away.

**How to do it without wrecking the tree.** Mechanical, one directory per commit, starting at
`src/kubementat/` and finishing with `tests/`, because that is the order where a bad rewrite hurts most
and the diff stays reviewable. Run the unit suite after every commit; nothing here may change a byte of
behaviour, so **no test assertion is edited to pass**, and if a docstring rewrite makes a test's prose
contradict its own assert, the prose was wrong and the assert is the fact. `git diff --stat` should be
prose-only: any line that changes control flow is this ticket failing.

**Why it runs alone.** It touches 197 files and every other ticket in this queue is a writer in some
part of that set. A comment sweep that lands beside ticket 4 will either lose ticket 4's stages or
confuse them, and the plan's one-writer rule exists for exactly this shape of change.

**Done when:** the grep above is empty for `src/` and for `tests/e2e/harness/`, and empty in `tests/`
except where a citation is quoting a document on purpose (`docs/` itself is out of scope — the spec and
the plans are the documents being cited *from*, and they may cite each other); the unit suite is green;
and `git log -p` shows no control-flow change anywhere in the ticket.

**Landed.** Measured before: 1713 citations across 191 files. After: 8, all of them deliberate. They are
the fixture strings `tests/test_a_sweep_that_changed_code_is_not_a_sweep.py` feeds to the sweep verifier
as its own input, and one expected value in
`tests/test_the_docs_that_point_at_tekton_ci_point_at_what_stays_there.py` that is matched as a substring
against documentation. Both quote documents on purpose, so deleting them changes what the test is about
and not how it reads — which is the exception the *Done when* already anticipated and which the verifier
does not decide, because a syntax tree cannot tell a printed string from a tested one.

`tools/prose_only_sweep.py` is what proves no behaviour moved, and it is worth knowing that it was wrong
twice before it was right, because both failures are instructive rather than incidental. The first
version reported clean for a rewritten condition inside a function whose name had not changed, because it
compared names instead of bodies. The second had the subtler bug: a 191-file diff is not reviewable by
reading, and treating string-value edits as either prose or logic is a coin flip, so the tool now says
"string values moved, read these units" and refuses to guess. That answer found a real loosened gate in
my own edit — the `(FIN-52)` that looked like a dangling reference and was actually an expected value.

One assertion was removed and is recorded here rather than left for a reader of the diff: a refusal's
message had to contain a document identifier, uniquely in the suite, and the same claim was already
asserted two lines above against the message's actual wording.

---

## Definition of "the epic is finished"

TD-1 measured end to end, in the half that is still measurable: a tree configured at `N` and installed
against that tree — `config init`, one line naming the cluster, `kmt install` — with no copied files, no
merge, and nothing in the config directory that kubementat generated for anyone to edit. That half is
ticket 4's bootstrap lane.

**The upgrade half is no longer measured, and that is a decision and not an oversight.** It used to run
`kmt config diff` between `N` and `N+1`; ticket 0 deletes that verb and takes the upgrade lane with it, so
nothing left in the suite or the CLI says what a bump would cost, and validate only says whether the tree
survives it. Say it plainly wherever this definition gets quoted. If it is ever to be true in the strong
sense again the missing piece is a statement of what changed between versions — not a re-implementation of
a verb this project decided it did not want.

Tickets 0-4 above are what stand between today and being allowed to say the first half. When they are
green, update this file's table to "landed" with the commit SHAs and close the epic row by row in the
parent queue.
