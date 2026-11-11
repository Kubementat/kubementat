# Plan: config/code separation epic

Source: [`docs/specifications/config-code-separation-spec.md`](../specifications/config-code-separation-spec.md)
(rev 2, 2026-11-02). Base commit: `06728e2` (spec + scratch dir, on branch `v2`).
Green bar at base: `uv run pytest -q` → **1180 passed, 1 skipped**.

This is the ticket queue. One writer in the tree at a time; read-only scouts may run alongside.
Effort levels are the sizes this run dispatches with (`:low` mechanical, `:medium` real
implementation, `:high` the shape that is "widen one rule across the whole surface").
`lane` marks a ticket that needs the end-to-end VM suite and therefore a human.

> **2026-11-05:** the queue below is finished except for the residue — five tickets, ordered, in
> [`config-code-separation-remaining-work.md`](config-code-separation-remaining-work.md). The
> `config import` row that used to be here was withdrawn by owner decision N-10 before anyone built
> it; the row above in phase 8 says so. If you are reading this to dispatch, dispatch the residue plan.

## Queue

| # | Ticket | Spec | Breaks tree | Effort | Done when |
|---|---|---|---|---|---|
| 0 | Version made honest: one source, `kmt --version` | R-12.3 | no | low | `kmt --version` exists; nothing else reads `VERSION`/`pyproject` version independently; one test. |
| 0 | Assets in the wheel via `importlib.resources` | R-12.1 | no | medium | wheel ships `templates/` + `tekton_ci/{tasks,pipelines}`; a test asserts every asset path `constants.py` names resolves from the package, not the checkout *(measured 2026-11-02: `constants.py` names no asset path, so the bar is carried by the wheel listing plus the guards reading through `tests/asset_paths.py`)*. |
| 0 | `uv sync` runtime install deleted | R-12.2 | no | low | `grep -rn "uv.*sync" src/` empty *(superseded: see R-12.2's amended bar, 2026-11-02 — the remaining hits are an error message and a developer install line; a guard asserts no run spawns a package manager)*. |
| 10 | `--config-dir` plumbed, fallback still standing (kwarg → `Context.build` → `@kmt_context`, plus `environments.py:51` and the three library-side `Config(...)` calls) | R-8.2 | no | high | every command can be pointed at a root and the suite is unchanged-green; a test proves flag > env for a listing and a `Context`. |
| 11 | The checkout fallback for the **config** root is deleted: `--config-dir` → env var → refusal | R-3.4, R-8.2 | **yes** | high | the refusal test exists, and every test that used to lean on `<main>/platform_config` names its root explicitly. No shim. |
| 12 | One collision implementation, one root: `Context.build`'s inline filter becomes the registry's, and both follow the flag | R-8.3, R-6.2 | yes | medium | flag-at-A-beats-env-at-B for the listing *and* the collision check, in one test; `collision_check` has a `src/` caller. |
| 13 | R-8.1's grep empty + the wrong-root safety tests | R-8.1 | yes | medium | `grep -rn "kubementat_main_dir.*platform_config" src/` empty *(superseded: see R-8.1's amended bar, 2026-11-02 — the one remaining hit is a constructor signature)*; a unit test drives `kmt install` with `main_dir` holding no `platform_config/` *(landed as the root the command was given, since `main_dir` decides nothing a command reads post-T-02/T-13a)*; the `uninstall` team sweep refuses a root it was not given *(landed as reports, not refuses: an empty team half is legal, so the run names the root and says nothing team-scoped was swept)*. |
| 2 | Layered JSON read path: `<config>/<env>/<team>` with `L2>L1>L0` | R-3.1, R-4.1, R-5.2 | no | high | three-layer precedence test + product-default-with-one-warning test. |
| 2 | helm values layering (`values/`, `values.encrypted/`; operator entries gated by `isFile`, because helmfile 1.7.0 has no per-entry `missingFileHandler` — a mapping entry is read as inline values, and a release-level handler would silence a missing **product** default) | R-4.2, R-4.5 | no | high | zero value files → product defaults *(landed 2026-11-03, measured with a real `helmfile build`: export nothing and you get the shipped default and nothing else)*; override wins *(landed: the order handed to helmfile is default → environment plain → environment encrypted → team plain → team encrypted, over all ten releases, reached only through the three exports T-18 installed)*; `enabled:false` removes *(landed 2026-11-03. The owner decided the same day it is a `static.json` answer, so both knobs — `<COMPONENT>_ENABLED` and `<COMPONENT>_VERSION` — ride the layering rule T-15 built, and the component set is the ten `component_name` labels of this manifest rather than `constants.DEFAULT_NAMESPACES`, which does not line up with it in either direction. The half a cluster has to answer is still helmfile's word for it, not kubementat's: see the CHANGELOG row.)* |
| 2 | Tekton manifest layering + name-match precedence, both layers | R-4.3, §9 | no | high | product Task + team Task of the same name resolves to the team's; env version loses to team. |
| 2 | Product-owned helmfile entry point | R-4.4 | **yes** | high | plan row 13's stale-copy bug is not constructible; `helmfile_paths()` points at the package. |
| 2 | Operator docs tell the truth about `templates/` and `tekton_ci/` | R-12.1 | no | low | no document sends a reader to a checkout path that no longer exists; the guard that covers `tekton_ci/**` covers `templates/**` too. Owner decision 2026-11-02: the docs move to the new truth, not to a note about the old one. Census D/census C class 2 called these "no action"; 16 sentences remain, four of them shell commands a human would run (`README.md:320`, `docs/README_PLATFORM_CONFIG.md:88,96,99,123`, `docs/README_TEKTON.md:15,324,325,341,342,365,371`, `docs/index-overview.md:103,153,154`). |
| 2 | The lane runs from a built wheel, not a synced checkout | R-12.1 | no | medium | a stage builds the wheel and `kmt` runs from `site-packages` against a named `--config-dir`; the templates and the shipped Tasks/Pipelines resolve with no checkout present. Until this row lands, R-12.1's outcome is proven by unit tests and manual wheel installs only, because `01-provision.sh:204-206` rsyncs the checkout and `uv sync`s it inside the guest. |
| 3 | `kmt config init` creates §5's layout (no product shape, no `REPLACEME_*`) | R-3.2, R-3.3, R-15.2 | no | medium | init writes the layout, second init writes nothing, zero files duplicate a product asset. *(landed 2026-11-03 as `6856b64` + `25aa9e7` + `78ce9f3` — the library, the verb, and the layer directories. It writes answers and no product shape, it is idempotent, it makes the `tekton/` directories its readers open and leaves alone the ones nothing reads, and it refuses names that are not layers. Not ready to use before phase 5: R-15.3's rows 1-4 and 6 are the git-crypt and keypair step, and §11.4's keys still land beside the product, not in the tree.)* |
| 3 | `config add-environment` / `config add-team` | §7 | no | low | both create the layer and nothing else; no templates copied. *(landed 2026-11-03 as `63be44a` — positionals, no flags, no answers. A new environment is its two static files plus the two `tekton/` directories; a new team is two empty documents plus its four directories. Neither bootstraps: `init` owns the manifest, `add-environment` owns an environment, and each refusal names the verb that does.)* |
| 3 | `config set` / `config unset` | §7, R-3.3 | no | medium | an answer lands on the named layer; `unset` makes the layer above answer. *(landed 2026-11-03 as `d516788` — the first read-modify-write verbs in the codebase, so the claim they carry is "every key we were not told about keeps its value and its position". `unset` clears both files of the layer because the encrypted half is ordered first. The team-addressable key boundary is deliberately not classified here; `config show` answers it, key by key, since `config explain` was deleted on 2026-11-05.)* |
| 3 | `config show` | §7, R-4.1 | no | medium | every key tagged, and the tag names the layer and the file. *(the row used to read `config show` / `config explain`; `explain` was deleted on 2026-11-05 and `show`'s per-key line carries the two facts it printed. Landed 2026-11-03 as `3c93239` — `config_answers.py`, 40 keys on a fresh tree at the time — **fifty** since `714e0f9`+T-30b, and the extra ten are T-30b's `constants.DEFAULT_STATIC_ANSWERS`, which are answered and not stored. Resolved through `Config`'s own read path so the report cannot disagree with a running command. `show` prints the answer and the layer it came from on the same line, which is what makes it the lanes' read-back verb now that `explain` is gone. The selector family is `<NAME>_SERVICE_SELECTOR`, not the spelling the ticket asked for; the guard test is what keeps a derived key honest. The lane's read-back moved off `explain` onto this verb in the same change that deleted it.)* |
| 3 | `config validate` | §7, R-5.4, R-6.1 | no | medium | refuses `environments:`/`teams:` in `kubementat.yaml`, a second manifest, a `helmfile*.gotmpl` in the config, absolute `CLUSTER_KUBECONFIG_PATH` (warn). *(landed 2026-11-03 as `d55be3b` — the first verb that collects every fault and then chooses an exit code. Two rules it decides because nothing else does: what `kubementat.yaml` means, and that a second manifest is a fault. It deliberately does not check git-crypt status, layering cycles, dead keys, or the version window — those belong to phase 5 and to no one; the version window is read by every verb through the stamp and
    refused by `config validate`, and dead keys have no verb that answers them since `config explain` was
    deleted on 2026-11-05.)* |
| 3 | `config set --generate` (open question 4 — **decided 2026-11-03**: option a) | R-11.8 | no | low | `config set <dir> E --generate KEY [--team T]` invents a value only where the key answers nothing and refuses where it answers; generation is not a class of key and not a class of layer, so it works on the environment layer as well as the team's, and no layer-creating verb generates anything. *(landed 2026-11-03 as `84aaf1f` on the spec change `6328eb4`. Owner decisions that came with OQ-4: `MYSQL_ROOT_PASSWORD` and `REDIS_PASSWORD` are no longer required answers, and the webhook secrets + `GRAFANA_ADMIN_PASSWORD` carry no special status. `REDIS_PASSWORD` is read by nothing; `create-mysql-database.yml` is `MYSQL_ROOT_PASSWORD`'s one consumer and is the consequence to keep an eye on. **Phase 3's queue is now closed**; 3b's bootstrap lane is gated by OQ-7.)* |
| 5 | `read_decrypted(path)` — all seven secret read sites | R-11.5 | no | medium | the seven sites share one function; a locked file refuses by name, not by JSON parser. *(landed 2026-11-03 as `6aec622` on the count correction `cdfac43` — `config_reads.py`, seven sites wired, and the false sentence in `cluster_is_unaddressed` gone. The census runs as a test with a reasoned exempt list, so the seam stays one without anyone checking by hand. The spec's "three sites" was two counts too low; the grep corrected my own first correction. Out of scope, said rather than discovered: kubectl's and the client's own kubeconfig reads, and `values.encrypted/*.yaml`, where helmfile's Go template only asks `isFile` and helm opens the bytes — so its failure arrives as a non-zero `helmfile apply`, never as our refusal.)* |
| 5 | Product default values files renamed out of `*.encrypted.*`; git-crypt's declaration leaves the product | R-11.1, R-11.2 | **yes** | small | ten files under `assets/values/` renamed to `values.yaml`; root `.gitattributes` deleted; the operator's `values.encrypted/` **layer directories** in a config tree are unchanged. *(owner ruling 2026-11-03: "the values.encrypted.yaml files are default value files. rename them to values.yaml" — and census K measured that they were already plaintext in the blobs and carry no secrets, so this names things rather than moving any. *(landed 2026-11-03 as `5c91e47`; the tenth file is `routing/cert_manager.yaml`, which keeps its prefix — see the CHANGELOG for why uniformity was not a constraint. The operator's layer directories were left alone deliberately: renaming one of them passes the whole suite while costing helmfile the operator's overrides.)* |
| 5 | The five team-shaped answers are derived, and derivation never deletes | R-3.5 | no | medium | `config_derived.py` answers them as `L0`, an operator's `static.json` still wins, and no destructive path may obtain a namespace from the rule. *(landed 2026-11-03 as `714e0f9`. The ruling it implements came from a consequence: derivation changed what `kmt uninstall` would sweep, and "derive for placement, delete only what a file named" was the answer — so `Config` gained a second door, `declared()`, and the teardown deletes `entry["deletable"]` and says what it declined. `kmt users` keeps deriving: binding a Role is placement.)* |
| 5 | The ten templated defaults get an L0, and the L0 is read at read time | R-3.2 | no | medium | `constants.DEFAULT_STATIC_ANSWERS` answers them, a declared value still outranks it, `declared()` never sees it, and no literal from the set survives anywhere else in `src/`. *(landed 2026-11-03 as T-30b. Census L's other finding: this was not a gap that opens when `initialize` is deleted — a `config init` tree already answered none of it. Two consequences stated rather than smoothed: `config show` answers fifty keys on a fresh tree and not forty, and an **omitted** Tekton pin now installs kubementat's version while a **declared-null** pin still refuses — verified live. Also found a fourth owner of `TRAEFIK_INGRESS_CLASS` inside `routing.py`, which the single-owner rule removed. The manifest's inline `default` was left standing and reported, and that is the one part of this row that is no longer true: on the owner's answer to the finding, T-31 deleted it and exported the key from `helm_utils.export_ingress_class`, so the class helmfile's release names is the class the layers answer — see `CHANGELOG.md`'s first row and `.agent-work/t31-report.md`.)* |
| 5 | `kmt initialize` deleted, with the git-crypt execs and the product-side key material | R-7.1, R-15.4, R-11.3 | **yes** | medium | no `src/` path execs git-crypt; nothing writes a keypair beside the product; the CLI does not know the command. *(owner ruling: initialize is no longer needed — this pulls phase 8's row forward. Census L's finding is why it goes last and not first: deleting it strands ~20 keys its template copies answered, which is why T-30a and T-30b exist. Census L also found the `gpg --import` cwd coupling that would otherwise break a key move, and the unguarded `GIT_DEPLOYER_*` reads in `setup_pipelines` that `config_keys.require_deployer_keypair` now covers. T-28.)* |
| 5 | `config init` **prepares** the directory: git repo, deployer keypairs, git-crypt keystore | R-11.3, R-11.4, R-11.6 | **yes** | high | a brand-new directory comes out as a git repository with `git-crypt init` done, the deployer's SSH+GPG keys generated inside it (or imported by `--gpg-key`), `.gitattributes` written before the first encrypted file, and every step idempotent. *(owner ruling: "the kmt config init process should execute all necessary steps to prepare the config directory for applying it to a cluster" + git-crypt exists only in generated config dirs. T-29b's library half **landed 2026-11-03**: `config_bootstrap.prepare_configuration_directory`, the nine steps, the ordering guard, and `init_configuration`'s `own_paths` keyword. What it does not do yet: no flag, no printing, no closing prose (T-29c). One measured side effect for the lane: `git-crypt add-gpg-user` imports the collaborator's public key into the *running user's* keyring, so repeated runs accumulate identities there until the match is ambiguous (`more than one public key matches`, measured). The key half **landed 2026-11-03 as `1b26643`** — `config_keys.py`, unwired, and it already carries the guard census L found missing at `automation.py:1040-1041`. **The CLI half landed 2026-11-04 as `faa1b87`**: `config init` now calls the preparation, so stdout is the 16-step file list in application order, a second run prints the same 16 as `skipped` and moves no byte, and the flag set is 10 (`--gpg-key`, `--gpg-email`, `--git-url`, `--no-commit`, `--force`). Two measured findings from that run, neither fixed: a relative `root` reached step 8 and asked git-crypt for `cfg/cfg/git_crypt_symmetric.key` (fixed in the same commit by resolving `root` once, and now asserted), and a live `--gpg-key` import fails step 7 against real git-crypt with `Unusable public key` because `add-gpg-user` is run without `--trusted` against a key kubementat imported itself. Both are the keyring question the row above flagged. **A ticket now owns it, and it landed 2026-11-04**: generation runs in a throwaway keyring so nothing lands in the operator's, and the fingerprint is read out of the directory's own `gpg_public_key.gpg` instead of out of a keyring listing, because a listing for one email is a *list* and the wrong element of it registers without erroring. `docs/README_GIT_CRYPT.md` carries the shape step 7 takes now.)** |
| 6 | Generated Tekton manifests under `<layer>/tekton/`, old path refused | §9, R-13.3 | **yes** | medium | generation and reading agree in one layer; `git status` clean after a full generation. |
| 7 | CI: two sources + stated cluster | R-10.1-10.4 | **yes** | high | product and config arrive separately; no `kmt` step execs git-crypt; the stated cluster is checked against the clone's claim. |
| 8 | Eight variables → configuration | R-7.2 | yes | high | `_check_required_environment_variables` gone; a missing answer is one refusal naming key/file/command. |
| 8 | `kmt initialize` deleted | R-7.1, R-15.4 | yes | medium | unknown to the CLI; CHANGELOG carries §15.4's mapping table; no test invokes it. *(pulled forward to phase 5 as T-28 — the owner ruled it unnecessary on 2026-11-03, and phase 5's key work has no other host. What is still owed here is §15.4's mapping table in the CHANGELOG.)* |
| 8 | ~~`kmt config import`~~ — **withdrawn 2026-11-05 by owner decision N-10.** A combined tree is addressable through `--config-dir`, not migratable; the new layout is built with `config init` + `config set` by the operator. Nothing in the queue moves an operator's tree. | ~~§14.1~~ | — | — | nothing to build. |
| 9 | `kubementat.yaml` stamp + warn-vs-refuse window | R-13.1, R-13.2, R-13.5 | no | medium | version warns and continues; three `schema_version`s behind refuses before touching the cluster. |
| 3b | Bootstrap lane: `config init` → 10 properties → `kmt install` | R-15.3, R-16.2, R-16.3 | no | medium | the lane's `result.json` carries R-15.3's ten properties as ten named asserts. |
| 3b | The harness configures with `kmt config`, not `jq` | R-16.1, R-16.4 | no | high | the four config-writing `jq` calls are gone; the marker-deletion stage is gone. |
| 9 | Release-pipeline bump PRs | R-13.4 | no | medium | *gated on open question 1 — needs an owner answer about the repo list.* |

**Phase 9 lost two rows on 2026-11-05, by owner instruction, and they are not owed to anyone.** `config migrate`
and `config diff` are deleted, so the rename table they carried is deleted with them (R-13.3 is withdrawn out
loud, and the census gate that failed the suite when a key left the product without an entry is gone with it)
and nothing says what a version bump would cost. The version contract stays: the stamp is read by every verb,
and `config validate` is the one command that refuses what is out of the window.

## Decided — do not re-ask

- **2026-11-02, owner: no backwards compatibility with any older kubementat version.** v2 is a
  full rewrite; the goal is "better", not "smoothly migrated from 0.x". What this deletes from the
  queue: every shim, alias, deprecation branch, or "keep working for one release" fallback —
  including T-11's checkout-root fallback, the eight `KUBEMENTAT_*` variables (`R-7.2`), and
  `kmt initialize` (`R-7.1`). It deletes `config migrate`'s rename table too, **on owner instruction of
  2026-11-05**: the verb that applied it is gone, so kubementat records no renames and applies no
  migration, and an operator's tree is told about a move rather than moved by a command. What keeps a
  tree current is the stamp window and `config validate`'s verdict, not a table. `kmt config import` (§14) was **withdrawn by owner
  decision N-10 on 2026-11-05**: an existing combined tree is pointed at with `--config-dir` and
  stays where it is; no command migrates it. If a ticket's brief contains a sentence starting
  "for older trees…", that sentence is wrong.
- 2026-11-02, spec N-1..N-9: carried as decided; no worker re-opens them.

## Census anchors (both censuses, 2026-11-02, at `06728e2`)

`.agent-work/census-a.md` (config/asset path derivation) and `.agent-work/census-b.md` (the CLI
surface). The two facts that change how phase 1 is written:

1. **`Config` already takes a `platform_config_dir=` kwarg that outranks the env var**
   (`config.py:219-220`). Phase 1 is plumbing, not a new mechanism — and `Config(...)` is created
   with *no root argument* at `cluster.py:879` (`_load_environment_config`) and `cluster.py:1001`,
   which is the choke point every `@kmt_context` command flows through.
2. **Two root-bypasses survive the plumbing**: `kmt environments list` calls
   `default_platform_config_dir()` bare (`environments.py:51`), and three library-side
   `Config(...)` calls build a second root per run (`automations/routing/routing.py:301`,
   `automation.py:2102`, `grafana_client.py:185`). A flag that reaches `Config` but not these is a
   silent-wrong-root machine, which is exactly the D11 class R-8.3 names.

Also: `EnvironmentRegistry.collision_check` (`cluster.py:741-763`) has **zero `src/` callers** —
`Context.build` re-implements the same filter inline (`cluster.py:1070-1079`). Two copies drift; T-12
is what merges them. And census A's highest-risk site is `automation.py:2070,2195,3011`, the
`kmt uninstall` sweep: a wrong root there *reports coverage it did not earn*, not merely an error.
39 commands, `--config-dir` acceptance today: **0/39**.

## Sequencing rules that are not obvious from the spec

1. **`config init` is not "ready to use" before phase 5.** Rows 1-4 of R-15.3 are git-crypt and
   key facts, so the bootstrap lane lands after the git-crypt move, not after phase 3.
2. **Between phase 3 and phase 8, `config init` is authoritative and `initialize` is legacy**
   — say so in the CHANGELOG row, in the same release.
3. **R-12.1 is a prerequisite of R-4.2**, not a parallel track (SP-2's reasoning).
4. Phase-1's `--config-dir` ticket is the widest shape in the plan; it is briefed from census B,
   not from the spec prose.
5. Phases 4-8 are one minor-version line; no deprecation branches.

## Open questions that gate a ticket

| Q | Blocks | Needed by |
|---|---|---|
| OQ-1 which config repos get bump PRs | R-13.4 | last |
| OQ-3 does `tekton-generate-*` commit, or the operator does | §9 / T-60 | phase 6 |
| OQ-4 `config set --generate` verb or option | R-11.8 | **decided** — the option spelling, on any layer |
| OQ-6 does `config init` prompt | R-15.2 | phase 3 (blocks the layout ticket's UX) |
| OQ-7 how many config dirs the lane owns | R-15.3 row 9 | **decided** — one per e2e lane |

OQ-2, OQ-5 do not gate a ticket in this queue. Ask them batched, once, at the phase-1 checkpoint.
