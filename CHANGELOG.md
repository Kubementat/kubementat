# Unreleased

- **`ClusterTarget` left `cluster.py` and became `core/identity/target.py`.** The class moves whole —
  223 lines, all 10 members and the decorators — and the only lines that differ from its source at the
  previous commit are the 16 pairs that had to gain a qualifier: `SOURCE_FLAG` becomes
  `constants.SOURCE_FLAG`, `_kubeconfig_files(...)` becomes `_kubeconfig._kubeconfig_files(...)`. No
  expression changed meaning; normalized for the added qualifiers, the two sources are line for line the
  same, and that is the honest way to say "verbatim" when a body is allowed to reach across modules.
  `cluster.py` falls from 1 217 lines to 953, `kubeconfig.py` grows from 301 to 356, and
  `kubementat.cluster.ClusterTarget` raises `AttributeError`.
  **One decision inside the move is worth knowing.** `cluster.py` still reads the class at 11 places, and
  importing the *name* there would have kept `cluster.ClusterTarget` resolvable — a second binding of a
  frozen class whose patches a test puts on the class object. So `cluster.py` binds the module and reads
  `_target.ClusterTarget`, and `cluster._target.ClusterTarget is target.ClusterTarget` is `True`, which is
  what keeps the three `setattr(ClusterTarget, "client", …)` sites load-bearing: removing one fails 14 of
  29 tests across three files. The symptom that would have appeared otherwise — patching a copy and
  watching the real transport get built anyway — was reproduced in a scratch file and then removed.
  **Two things this ticket caught in the census, on top of the four P2-3a found:**
  `_connection_document` and `_as_pem_base64` also live in `kubeconfig.py` (their own home assignment says
  they sit with the connection field, not beside the class), so the row is 23 names and not 21; and one
  coupling outside both the 21 import bindings and the 13 test couplings — the
  `EXEMPT[("kubementat/cluster.py", "from_service_account")]` key in
  `test_a_locked_configuration_file_refuses_by_name_and_not_by_codec.py:434` — is keyed by file and
  function and had to follow the function, not the file.
  21 import repoints: `kubernetes_utils.py:11`, `helm_utils.py:12`, and 19 test files, three of which are
  the patch sites above. Suite unchanged: **1775 passed, 1 skipped**, cycles none, layer violations none.

- **The kubeconfig half of the cluster module became the leaf of `core/identity/`, and the leaf turned
  out to be four names bigger than the map said.** `core/identity/kubeconfig.py` is 301 lines and holds
  21 module-level names — 11 constants and 10 functions, each byte-identical to its source at the
  previous commit — and `cluster.py` drops from 1 474 lines to 1 217. The census's table said 17, and the
  same census assigned `_service_account_files`, `_service_account_address`, `_library_config_file` and
  `_CONNECTION_DOCUMENT_CONTEXT` here elsewhere, so the worker was handed two answers for four names.
  It stopped and asked rather than picking one, and this is the answer it was given: move them down,
  because the leaf is only worth having first if it is the real leaf — leaving them behind means the
  next ticket arrives to a `kubeconfig.py` that some other module still has to descend into, and the
  edge list the census computed (`target → kubeconfig`, `comparison → kubeconfig`) is only true once
  they actually live there. **Seven shared key and source-label constants also went below all five
  files**, into `core/constants.py`, which keeps zero import lines by construction and so sits under
  everything: a key that three files name cannot live in one of them. `kubementat.cluster` answers
  `AttributeError` for every moved name. Of the 13 test couplings the census counted, **10 needed a
  change and 3 were already right**: the 6 attribute reads off a module alias became imports of the
  constants that now live in `constants.py` (5 in `config_validate.py`) or of the new home (1), and the 4
  `monkeypatch.setattr` on the module moved to `kubeconfig_module`. The 3 `monkeypatch.setattr` calls on
  `ClusterTarget` stayed exactly where they were, because that class did not move — a fact worth stating
  plainly, since moving a patch that tracks a name that stayed is the mistake this class of ticket makes.
  Zero patch-target strings named `kubementat.cluster`. The `(file, function)` table in
  `test_the_eight_former_environment_variables_arrive_only_through_the_layers.py:188` follows the path,
  and `core/identity/kubeconfig.py` imports stdlib, `yaml`, `core.config.reads` and `errors` —
  `kubementat.cluster` is not one of them. Suite unchanged: **1775 passed, 1 skipped**, import cycles
  none, layer violations none.

- **The layering moved out of `config.py`, and this is the split the other three were ordered for.**
  `LAYER_TEAM`, `LAYER_ENVIRONMENT`, the two pairs, `CATEGORY_LAYERS`, `COMPONENT_LAYERS`,
  `_NOTHING_DECLARED`, `_answers_from_a_team_layer`, `_declare_in_layers`, `_layered_view` and
  `component_config_key` are the whole of `src/kubementat/core/config/keys.py`: the 11 names of the
  census's `keys.py` row, each one byte-identical to its source at the previous commit. `keys.py` is
  207 lines and `config.py` ends the split at 905, from 1 446; `NON_TEAM_ENVIRONMENT_DIRECTORIES` and
  `TEAM_SCOPED_CATEGORIES` are the two names that stayed, because the only methods that read them
  stayed too. Why this one is the hard ticket is that
  `_declare_in_layers` is *two-homed* — `keys.py:110` calls it, `Config._resolved` calls it — so
  only one definition is allowed, and the sentinel compared with `is` beside it has three readers
  and one importer (`Config._release_knob`, `_layered_view`, `config_answers.py:61`). The proof the
  ticket asked for is that identity: `keys._NOTHING_DECLARED is config_answers._NOTHING_DECLARED`
  prints `True`, and `config.py` holds no copy of it because it reaches all seven names through the
  module rather than binding them — `config.CATEGORY_LAYERS`, `config.component_config_key` and
  `config._declare_in_layers` all raise `AttributeError`. **`component_config_key` is the largest
  repoint in the epic: 13 import statements**, 3 in `src/` and 10 in `tests/`, not the 5/8 the
  census table splits them into, and none is left behind — the grep for the old home is empty and
  the grep for the new one counts 13. None of the 11 is a `patch(...)` target anywhere in the
  suite, so nothing needed a name-level indirection; `config.py` still calls through `keys` because
  that is what the `AttributeError` gate demands. 11 files carry a repointed import and one test
  file changed shape: `test_the_layering_lives_in_config_and_left_every_call_site_alone.py` asserted
  that *only* `config.py` names the layering, which the move contradicts, so it now asserts that
  exactly two files do — `keys.py` as owner and `config.py` as its one reader — and its 13 module
  attribute reads moved to `keys`. Suite unchanged: **1775 passed, 1 skipped**, cycles none, layer
  violations none; the graph still points one way (`config → keys → {derived, constants}`, and
  `keys → config` is not an edge).

- **The tree's stamp moved out of `config.py`, and the module that stayed does not need it.**
  `ConfigurationStamp` and its ten members, `read_configuration_stamp`, `check_configuration_stamp` and
  `SCHEMA_VERSION_WINDOW` now live in `src/kubementat/core/config/stamp.py`, and they are the four
  definitions of that file — 192 lines, 149 of them moved, each one byte-identical to its source at the
  previous commit. What the move proved about `config.py` is the part worth reading: `config.py` imports
  nothing from `stamp`, because every caller of those four names was inside the block that moved, and it
  lost four imports the move left behind. `ConfigurationStamp` reads the version comparison through
  `versions` exactly as before, so the five patches that P2-2b moved to `versions` still land where the
  stamp reads, and `kubementat.core.config.config.ConfigurationStamp` now raises `AttributeError`
  instead of quietly agreeing with the wrong copy. **One constant could not be proved by the suite:**
  `SCHEMA_VERSION_WINDOW` is read by exactly one line in `src/` — `stamp.py:111`, inside
  `ConfigurationStamp.window` — and no test covers it, so the proof is that its assignment
  (`SCHEMA_VERSION_WINDOW = 1`) is byte-identical to the one that was there, that the window answers
  from nothing else, and that nothing outside reads it. 6 product files and
  2 test files, 9 of them attribute reads on the module object; the tests bind the module as
  `stamp_module` because two tests in the same file bind a local and a parameter named `stamp`, which
  shadowed the module. Suite unchanged: **1775 passed, 1 skipped**, cycles none, layer violations none.

- **The version answers and the root answers left `config.py`, and the five tests that patch a version
  had to move with them.** This is Phase 2's first split inside `core/config/`:
  `default_kubementat_main_dir`, `default_platform_config_dir`, `resolve_platform_config_dir`,
  `_directory_from_environment` and the two environment-variable names now live in
  `core/config/paths.py`, and `product_version`, `satisfies_version_constraint`, `_version_parts`,
  `_compare_versions` and the two operator tables in `core/config/versions.py`. `Config` and
  `ConfigurationStamp` stay, so nothing about what kubementat answers changes, but `from
  kubementat.core.config.config import resolve_platform_config_dir` is now `...paths`. **The interesting
  part of the move is a test hazard rather than a path.** Five tests patch `product_version` with
  `setattr(config, ...)`: `test_a_prepared_tree_says_which_product_it_was_made_for.py:86,97,105,165` and
  `test_the_configuration_validator_is_strict_about_the_version_window.py:172`. The code that answers —
  `ConfigurationStamp.running_version` — now asks `versions.product_version()`, so a patch left on
  `config.py` would have landed on a binding nothing reads while the stamp answered from the real
  function. Had `config.py` re-imported the name to keep those five sites resolving, every one of them
  would have stayed green while asserting a warning the product no longer emits. They patch `versions`
  now, `config.py` calls the moved names through their module instead of importing them, and no
  compatibility import exists: `kubementat.core.config.config.product_version` raises `AttributeError`,
  as does every other moved name. Proved the way this ticket had to be proved: the eight moved
  functions are byte-identical to their old source, `paths.py` and `versions.py` import no part of
  `kubementat` and so cannot reach back into the module that stays, and `default_kubementat_main_dir()`
  still answers the checkout's root — its `parents[4]` arithmetic is only correct while the function
  sits one directory below the package root, which is where `paths.py` sits. 15 files
  (2 import statements in the CLI, 4 internal call sites routed, 5 patch sites, 3 module-attribute
  reads, 1 `(file, function)` table row, 5 prose lines). Suite unchanged: **1775 passed, 1 skipped**,
  cycles none, layer violations none.

- **The four configuration modules moved under `kubementat.core.config`.** Phase 2's second pure
  move, so the dotted path is what changes and nothing else is: `kubementat.config` is now
  `kubementat.core.config.config`, `kubementat.config_derived` is `kubementat.core.config.derived`,
  `kubementat.config_reads` is `kubementat.core.config.reads` and `kubementat.config_reads_index` is
  `kubementat.core.config.release_index`. `git mv` measured it honestly: three of the four crossed
  the tree with **0 lines changed**, the fourth with 6 in and 4 out, and all ten of those lines are
  its own import lines and the one arithmetic line named below. No shim, no re-export and no alias
  stands at the old path — `python -c "import kubementat.config"` raises `ModuleNotFoundError` —
  and `core/config/__init__.py` is a docstring and nothing else, because a re-export would be the
  convenience that costs the suite: a patch string applied to one attribute while the call runs
  through the other is a green test and a wrong product. **What it cost:** 64 files rewrote a path
  (15 under `src/`, 46 under `tests/`, 3 in the operator docs), 5 test files carried the path as a
  *string* rather than as an import line — 10 strings, of which 5 name the module path and fail
  silently and 5 name a file and fail loudly — and 183 `setattr` patch sites moved without being
  touched, because they patch the module object the caller imported and that object is the same one
  either way. Suite unchanged: **1775 passed, 1
  skipped**, import cycles none, layer violations none.

  **Two things the Phase 2 text said that the tree did not, and one of them changed behaviour.**
  First: "pure move, no line inside changes" did not survive the file it described.
  `default_kubementat_main_dir()` answers the checkout by counting levels off `__file__`, and the
  destination sits *two* directories below the package root, not one, so
  `Path(__file__).resolve().parents[2]` had to become `parents[4]`. Two tests caught it in the first
  run — `tests/test_import_hygiene.py::test_the_root_the_cli_drives_is_the_checkout` and
  `tests/test_config_path_resolution.py::test_the_project_root_is_the_checkout_only_when_kubementat_home_is_unset`
  — and both now resolve the checkout, so `KUBEMENTAT_HOME` unset still answers this checkout and
  nothing else. It is the only line in the ticket that changed what kubementat does, and it is a
  line no grep could have found. Second: the rewrite list a grep of import statements draws is 7
  files short of the tree — the comments in `src/cli/kmt.py` and `src/cli/commands/utilities.py`, the
  docstring in `core/assets/__init__.py`, three `from kubementat import config as config_module`
  bindings in `tests/`, and `REPO_ROOT / "src" / "kubementat" / "config.py"` in the test that parses
  this module's own source. **Measured in the artifact, not argued:** a clean
  wheel built outside this checkout ships 111 entries, 5 of them `kubementat/core/config/*.py` and 0
  under any old path. That measurement is only honest outside the checkout: the `build/` directory
  tracked here is an additive cache that setuptools never prunes, and `uv build --wheel` inside it
  produces 177 entries including `kubementat/config.py`, `kubementat/config_diff.py` and
  `kubementat/assets/` — files this tree no longer has. A wheel built here is a second way to reach
  the same object, and unlike the `__init__.py` it is invisible until someone installs it.

- **The declared values, the version and the shipped data moved under `kubementat.core`.** This is
  the first of Phase 2's pure moves, so it can honestly say *nothing about kubementat changed*; what
  changed is the dotted path you import from. `kubementat.constants` is `kubementat.core.constants`,
  `kubementat.version` is `kubementat.core.version`, `kubementat.assets` is `kubementat.core.assets`.
  Nothing is left at the old paths to catch you — no shim, no re-export, no alias — so a stale import
  fails once, loudly, instead of quietly resolving against a copy that will be gone next release.
  **Two things this could have broken in a way the test suite cannot see, and both were measured rather
  than argued.** First, the data still ships: `assets/` is data and not code, and it travels inside the
  wheel only because `core/assets/` is a *package*, which is what the `__init__.py` marker buys —
  without it, `packages.find` would install code and no templates, and every one of those templates is
  read from the installed tree, not from a checkout. Proved from the artifact: `uv build --wheel` then
  reading the archive gives 57 entries under `kubementat/core/assets/`, 7 of them `templates/*.template`,
  and 0 under `kubementat/assets/`; the resolver answers `src/kubementat/core/assets` from a checkout and
  a `templates/` directory that exists. Second, `kmt --version` still gets its string: that module has
  exactly one importer, `src/cli/kmt.py:18`. **What it cost:** 12 product files and 37 test files rewrote
  a path in the same commit, and two of `config.py`'s `KeyError` sentences now name
  `kubementat.core.constants.DEFAULT_STATIC_ANSWERS` because telling the operator to edit a table in a
  module that no longer exists is how an error message turns into a wrong instruction. Suite unchanged:
  **1775 passed, 1 skipped**, import cycles none, layer violations none.

- **`config init`'s cluster warning is logged now, and the console shows it differently.** This is
  the first change in the epic that says *this changes what you see*, because the rest of it can
  honestly claim the opposite. `config_init.warn_cluster_not_addressed` — the sentence that fires
  when `config init` or `config add-environment` prepares an environment that addresses no cluster
  — was the one place under `src/kubementat/` that wrote operator-facing text with `click.echo`.
  It now uses `logging.warning`, which is what the other 437 output sites use (`config.py:905`,
  `automations/routing.py:320`). Every sentence is word for word the same, and the comparison was
  made by calling both implementations and diffing the text: `word-for-word identical: True`.

  **What the operator sees.** Before: five stderr lines — one blank, then one per sentence. After:
  one stderr record carrying all four sentences, prefixed with the CLI's logging format, so
  `2026-11-06 11:52:43,815 - WARNING - Environment 'dev' is configured, but it does not address a
  cluster yet: …`. The leading blank line is gone and the four paragraphs became one wrapped
  paragraph; that is the whole visible delta, plus the timestamp and level. `kmt config init <dir>
  --environment dev --gpg-email <email>` on a tree with `CLUSTER_KUBE_CONTEXT: null` is how to see
  it. It is still stderr and still guidance — `cli.kmt.configure_logging` sends the root handler
  there — so nothing that reads a script's stdout changes.

  **The tests moved because the channel moved, and that was a decision.** Four tests asserted the
  sentence through `result.stderr` on `CliRunner`, and `CliRunner` never sees a log record: the
  assertions are now against `caplog`, which is what this suite already uses for warnings. Two
  assertions that the sentence is *not* printed over a tree that was just handed its cluster keep
  both halves — `caplog` and the empty `result.stderr` — so a verb that echoed it back as a stream
  would still fail. `test_every_bootstrap_assert_passes_a_tree_that_is_correct.py` needed nothing:
  its copy of the sentence is a fixture for the e2e harness, and the harness's own assert reads the
  real CLI's stderr, where a warning still lands. No `click.echo` remains in `config_init.py`; the
  `import click` stays, because 25 `click.ClickException` raises and one `ClickException` subclass
  still use it — Phase 1's standing decision, untouched here.
- **The eleven refusals `tekton_utils.py` raises moved to `kubementat.errors`.**
  `TektonControllerWithoutConfiguredApiError`, `PipelineNamespaceNotConfiguredError`,
  `TektonReleaseVersionNotConfiguredError`, `TektonWebhookNotReachableError` and
  `CleanupJobImageNotConfiguredError` were written in that module's install path, and
  `GenerationTemplateNotFoundError`, `GenerationPlaceholderNotConfiguredError`,
  `GenerationArgumentNotSuppliedError`, `GenerationInputNotFoundError`,
  `GenerationInputNotUsableError` and `GenerationTargetExistsError` in the generators that live
  below it. All eleven were bare `Exception` classes with no parent, which is the whole content of
  this ticket's edit: the classes, their messages and their reasons are unchanged, and each now
  answers to `KubementatError`.

  **The hierarchy, as decided and not re-opened.** Every one of the eleven descends from
  `KubementatError`, in two commented sections that say what each group has in common — five
  refusals to install without a value in a file, six refusals to write a generated file. No
  `GenerationError` base was invented, and the census is the reason rather than the taste: not one
  `except` clause in `src` or in `tests` names any of the eleven, so nothing catches the generation
  family as a family, and a base nobody catches is a promise about behaviour that does not exist.
  Each of the six already answers to `KubementatError`, so `except KubementatError` reaches them
  today, and adding the base later is a one-line change with no call site to repoint.

  **What did not change.** No message, no raise, no catch, no assertion text. 17 import statements,
  24 `pytest.raises` sites and one `module.ClassName` attribute reference
  (`tekton_utils.TektonReleaseVersionNotConfiguredError`, in a test) were repointed across
  `tekton_utils.py` and 9 of the 10 test files that mention them, and the 28 raise sites there now
  write
  `raise errors.X(...)`, the way `shell.py`, `ci_utils.py` and `ingress_generator.py` already do, so
  no old address is reachable. Seventeen docstring `Raises:` lines were qualified to
  `kubementat.errors.X` while they were being repointed; none of them spelled the old module address
  the way the plan expected, which is the only place the plan and the tree disagreed. The two
  assertions that compare `type(error.value).__name__` to `"GenerationPlaceholderNotConfiguredError"`
  were left alone: they read the class name, and the class name is unchanged.

  **What stayed put.** `ObsoleteTriggerDirectoryError`, which is Phase 1's standing decision and
  descends from `click.ClickException` rather than from anything here, and `tekton_utils.py`
  itself — no function body, no split, no `infrastructure/`, no new `click` import under `src/`.

  **The census said 102 rows for these eleven, and the split it offered was not needed.** 57 were
  in `tekton_utils.py` — 11 definitions, 28 raises, 17 docstring lines and one comment — and 45 were
  spread over 10 test files. Under the 150 the plan set as the point where the generation family
  would become its own ticket, so this is one change and not two.

  **One thing the census did not say, and it changed nothing.** `CleanupJobImageNotConfiguredError`
  names the same three `static.json` keys as `CiImageNotConfiguredError`, which this module already
  owned. They are not folded together: two different images, and the sentence an operator acts on is
  not the same sentence.

  **Evidence.** `uv run pytest -q` reports 1775 passed, 1 skipped (54 s);
  `git grep -n "^class \(Tekton\|Pipeline\|CleanupJob\|Generation\)[A-Za-z]*Error" -- src/kubementat`
  answers only `src/kubementat/errors.py`, and the same search for any
  `kubementat.tekton_utils.XError` reference answers nothing; `python3 .agent-work/import_graph.py
  src` prints `none` under both cycles and layer violations.

- **The nine refusals the grafana, Ingress, CI-image and uninstall commands raise moved to
  `kubementat.errors`.** `GrafanaError`, `GrafanaConfigurationError` and `GrafanaUnauthorizedError`
  were written in `grafana_client.py`, `IngressTargetExistsError`, `IngressArgumentNotSuppliedError`
  and `IngressPortNotUsableError` in `ingress_generator.py`, `CiImageNotConfiguredError` in
  `ci_utils.py`, and `UninstallIncompleteError` and `MysqlNotConfiguredError` in `automation.py`.
  All nine are written once in `src/kubementat/errors.py` now, in four commented sections that say
  what each group has in common — three answers about one HTTP endpoint, three refusals to write one
  manifest, two "the file did not answer" refusals, and one that says the sweep left residue — and
  every docstring travelled with its class verbatim, payloads included.

  **The hierarchy, as decided and not re-opened.** Every one of the nine descends from
  `KubementatError`; `GrafanaError` stays the middle base of `GrafanaConfigurationError` and
  `GrafanaUnauthorizedError`, so `except GrafanaError` still means "grafana said no or said nothing"
  and nothing wider, and `except GrafanaConfigurationError` still means "open the file". The eight
  that arrived flat on `Exception` were re-hung onto the base rather than left beside it: no
  `except KubementatError` exists anywhere in `src` or `tests`, so the widening that concern protects
  against is a catch nobody has written, while the base's own docstring promises that one catch means
  one kubementat refusal. No `NotFoundError`-style family was invented, and no family the tree
  already had was dissolved.

  **What did not change.** No message, no raise, no catch, no assertion text. None of the four
  modules carried an `__all__`, so there was no list to prune — instead each module raises through
  the `errors` module, the way `shell.py` has since the previous ticket, so no old address is
  reachable. 21 import sites, 7 `except` clauses, 24 raises, one `module.ClassName` attribute
  reference (`ci_utils.CiImageNotConfiguredError` in a test) and one such string label were repointed
  across 9 source modules and 8 test files; seven docstring lines that spelled the old address now
  spell `kubementat.errors.X`, since prose is invisible to the suite. The three
  `click.ClickException` subclasses stayed put, as Phase 1 decided, and `tekton_utils.py` was not
  touched — none of these nine is raised from it.

  **One thing the census did not say, and it changed nothing.** `MysqlNotConfiguredError` is caught
  in `cli/commands/tunnels.py:50` and raised nowhere in `src`: it is a refusal that survived the
  `tunnel-polaris`/`tunnel-mysql` retirement still holding its `except`. It moved with the rest
  because deleting a class is a different ticket's decision, and the suite is silent about a class
  with no raise.

  **Evidence.** `uv run pytest -q` reports 1775 passed, 1 skipped (53 s);
  `git grep -n "^class \(Grafana\|Ingress\|CiImage\|UninstallIncomplete\|MysqlNotConfigured\)[A-Za-z]*Error" -- src/kubementat`
  answers with `errors.py` and nothing else, and the same grep without the `Error` suffix also names
  the non-error classes that were never candidates (`GrafanaClient`, `GrafanaUser`, `GrafanaAccount`,
  `GrafanaAccountRow`, `IngressGenerator`, `IngressClass`); no qualified
  `kubementat.grafana_client.X`/`ingress_generator.X`/`ci_utils.X`/`automation.X` error address
  survives in `src` or `tests`; and `python3 .agent-work/import_graph.py src` prints `none` under
  `== CYCLES ==` and `== LAYER VIOLATIONS ==`, with `grafana_client`, `ingress_generator`, `ci_utils`
  and `automation` each gaining one edge to `kubementat.errors`.

- **The five refusals that name a thing which is not there moved to `kubementat.errors`.**
  `PodNotFoundError`, `ServiceAccountTokenNotFoundError` and `NamespaceNotFoundError` were written in
  `kubernetes_utils.py`, `ResourceWithoutNameError` in `k8s_apply.py`, and `CommandExecutionError` in
  `shell.py`; all five are written once in `src/kubementat/errors.py` now, under a commented section
  that says what they have in common — each names an object the reader was told to expect and the
  cluster, the file, or the `PATH` does not have — and every docstring travelled with its class
  verbatim.

  **The one deviation from the census.** The census said `shell.py`'s `__all__` had to keep
  `CommandExecutionError` bound so the 16 `module.ClassName` attribute references keep resolving.
  Repointing them is better, so this does: `__all__` is `["CommandResult", "run"]` now, and every
  reference that read the class off the `shell` module — 6 in `src/` and 8 in `tests/`, the census's
  16 minus two that belong to `ci_utils` and `tekton_utils` — reads `errors.CommandExecutionError`
  instead. `shell.py` no longer binds the class under any name, so the name is not reachable from two
  modules, and an `__all__` that listed a class the module stopped owning is exactly the re-export the
  project rule forbids. No reference was dynamic: the two `REPORT_ONLY_CALLS` sets in the kubectl and
  helm argv scans read a call's AST name, and they match `errors.CommandExecutionError` as happily as
  they matched the bare one.

  **What did not change.** No message, no raise, no catch, no assertion text. The five classes stayed
  flat on `Exception` and got no base — nothing in the tree catches this group as a family, so a
  `NotFoundError` under them would be a comment about a hierarchy nobody uses, and re-hanging them off
  `KubementatError` would widen what `except KubementatError` takes without a caller asking for that.
  The three `click.ClickException` subclasses stayed put, as Phase 1 decided. 12 test files and 11
  modules got import addresses and nothing else, and `kubernetes_utils.py` gained no package, no split
  and no helper — that is Phase 2's work. Nine `Raises:` lines and two `:class:` mentions that spelled
  the old address were repointed by hand, since prose is invisible to the suite; eight of the nine were
  in other modules' docstrings and the ninth is `shell.run`'s own. One paragraph of
  `k8s_apply.py`'s module docstring said the class "lives here", which stopped being true the moment it
  moved, so it now says where it lives and why the raise still speaks from here.

  **Evidence.** `uv run pytest -q` reports 1775 passed, 1 skipped (53 s);
  `git grep -n "class .*NotFoundError\|class CommandExecutionError\|class ResourceWithoutNameError" -- src/kubementat`
  answers with `errors.py` plus the two `tekton_utils` generation classes, which are another group's
  still-to-move work and not a leak from this one; the same grep with the qualified forms
  `kubementat.shell.X`/`kubementat.kubernetes_utils.X`/`k8s_apply.X` names no error class at all;
  `git grep -n -A6 "__all__" -- src/kubementat/shell.py` shows no error class listed; and
  `python3 .agent-work/import_graph.py src` prints `none` under `== CYCLES ==` and under
  `== LAYER VIOLATIONS ==`, with `kubementat.shell -> kubementat.errors` the only new edge.


- **The seven cluster refusals moved out of `kubementat.cluster` and into `kubementat.errors`.**
  `ClusterResolutionError`, `ClusterNotConfiguredError`, `ClusterContextNotFoundError`,
  `ClusterFileUnreadableError`, `ServiceAccountNotMountedError`, `StatedClusterMismatchError` and
  `ClusterIdentityCollisionError` are written in `src/kubementat/errors.py` now, under a commented
  cluster section, and the prose that says why each refusal exists travelled with the class. The
  shape stayed the one readers need: `ClusterResolutionError` is still the middle base of the five
  resolution failures, so `except ClusterResolutionError` still means "no cluster was named" and
  nothing wider, and `ClusterIdentityCollisionError` sits beside it rather than under it, because a
  caller that carries on past a resolution failure must not carry on past a collision. Both, plus the
  five, now descend from `KubementatError`.

  **What did not change.** Nothing an operator can feel: `cli/context.py` still converts
  `(ClusterResolutionError, ClusterIdentityCollisionError)` into one `Error: …` line on stderr and
  exit 1, no message gained or lost a word, and no class name is printed anywhere it was not printed
  before. Two refusals — the not-configured case and the context-not-found case — were run against
  the same throwaway configuration root from HEAD's tree and from this one, and both stderr lines and
  both exit codes came back byte-identical. `cluster.py` raises every one of these by the same name it
  always raised; only where that name comes from moved, which is why the move is 14 import statements
  (2 in `src/`, 12 in tests), one `Raises:` line, and zero changed assertions: the 40 `pytest.raises`
  calls and the `issubclass` check that pin these refusals kept their text and changed their address.
  No shim, no alias, no re-export and no deprecated name — `kubementat.cluster.ClusterNotConfiguredError`
  is simply gone, and the two references that spelled it that way (`traefik_presence.py`'s `Raises:` line
  and one sentence of a test docstring that called the class "the module's own type") followed the class.
  The three `click.ClickException` subclasses stayed exactly where they are, because moving them would
  make `errors.py` import `click` and would change what `config_validate.py:285` and `:470` catch.

  **Evidence.** `uv run pytest -q` reports 1775 passed, 1 skipped (55 s);
  `git grep -n "^class Cluster[A-Za-z]*Error" -- src/kubementat` answers with `errors.py` only;
  `git grep -n "kubementat.cluster.Cluster" -- src tests` is empty; `python3
  .agent-work/import_graph.py src` prints `none` under `== CYCLES ==` and under
  `== LAYER VIOLATIONS ==`.

- **`apply_resources` is a primitive in `kubementat.k8s_apply`, and `Automation` and `TektonUtils`
  stopped importing each other.** `Automation.apply_resources` moved verbatim out of the class — the
  walk, the three helpers that decide which document goes where, the log lines, the return value and
  both refusals — and `kubementat.tekton_utils` stopped reaching for `Automation` to get it. That
  deferred import inside `_apply_team_trigger_resources` was the cycle: `automation.py` imports
  `tekton_utils.py` at module level, so the only way a trigger directory could be applied was to
  import the class on the way past the import-time deadlock. Nothing is deferred now, because nothing
  is left to defer, and the comment that explained the deferral is gone with it.

  **The shape of the function.** `apply_resources(kubernetes_utils, resource_dir, target_namespace,
  kind, group, version)` takes the object that answers `create_or_replace_custom_resource` and not a
  union with anything that might hand it over. A union gets unwrapped on every call and answers
  nothing: a caller already holds its own utils object, and a test that wants to watch this primitive
  hands it the recorder it built. `setup_pipelines` passes `self.kubernetes_utils` through its layer
  helper, and `setup_triggers` builds one from the `Context` it already had, exactly as
  `Automation(self.ctx)` built it before.

  **What did not change.** Nothing a cluster or a log sees: the same files are walked in the same
  sorted order, `templates/` is still skipped, the same number is returned, and an absent directory
  still raises `FileNotFoundError` while a layer that is not there still logs one line and carries
  on. `ResourceWithoutNameError` travels with the code that raises it, so it is now
  `kubementat.k8s_apply.ResourceWithoutNameError` — the same class, one owner, no alias, and the two
  tests that caught it read the new address. The apply test drives the function through a recorder
  instead of through `Automation.__new__`, and every assertion in it is the assertion it had.

  **Evidence.** 1775 passed, 1 skipped (`uv run pytest -q`, 57 s), and
  `python3 .agent-work/import_graph.py src` prints `none` under `== CYCLES ==` for the first time on
  this branch. The shipped product roots still apply 29 documents — 20 Tasks and 9 Pipelines — read
  off the walk and not off a target.

- **Routing's component name and its ingress-class default are owned by `constants.py`, and no
  routing import reaches the ingress generator.** `constants.COMPONENT` is the one place
  `"traefik"` is written as a component name, and `constants.DEFAULT_INGRESS_CLASSES` is the fallback
  table both routing reads go through. `automations/routing/routing.py` and
  `automations/routing/traefik_presence.py` keep their own spellings no longer; `ingress_generator.py`
  imports both from `constants` and imports nothing from `automations`, which is the whole point of
  the move — the manifest generator used to depend on the command module to learn a word.

  **Why the table is indexed and not written again.** The value of `DEFAULT_INGRESS_CLASSES` is read
  out of `constants.DEFAULT_STATIC_ANSWERS["TRAEFIK_INGRESS_CLASS"]` rather than spelled a second
  time, so `"traefik"` as the default of that key is still written once. The key itself is spelled as
  a literal, and that is `constants.py`'s own idiom rather than a shortcut: the rule that gives the
  key its format is `config.component_config_key`, and `config.py` imports `constants.py`, so
  importing it back is the cycle this phase exists to remove. The invariant survives the spelling —
  one place states the value — and the agreement between the two spellings of the key is pinned in
  `tests/test_the_defaults_initialize_seeded_have_a_single_owner.py`, where a key written in two
  places would be two owners of one naming rule.

  **What did not move.** `component_config_key` stays in `config.py`; it has 104 references across 20
  files and its target home is `core/config/keys.py`, which does not exist yet. `constants.py` gained
  no import line — it is pure data, and that is the invariant the cycle-free phase depends on.
  `ingress_generator.py`'s manifest, warnings, and read order are unchanged, and
  `DEFAULT_INGRESS_CLASSES["traefik"]` is `"traefik"` exactly as it was before the move.

- **`helm_utils` gave up its fourth spelling of the same component name.** It carried its own
  `INGRESS_CLASS_COMPONENT = "traefik"` with a comment that the duplicate was forced: routing owned the
  name, and routing imports `helm_utils`, so importing it back would be a cycle. That reason died with
  this change — every routing module now reads `constants.COMPONENT` — and a duplicate that justifies
  itself with a cycle that no longer exists is a bug waiting to be believed. `export_ingress_class`
  builds its key from `constants.COMPONENT` now, and the registry row in the environment-variable test
  says so the way its two sibling rows already did, naming the provenance rather than folding a
  constant that is no longer in the file.

- **Reading the shipped manifest is a configuration read, and `config_answers` no longer imports
  `helm_utils` to make it.** `kubementat.config_reads_index` answers three questions about the
  packaged helmfile with nothing but a `Config` and the filesystem: which directory the manifest
  sits in (`helmfile_paths_for`), which file it is (`helmfile_state_path_for`), and which releases
  it labels (`read_release_index`). `HelmUtils.helmfile_paths`, `helmfile_state_path` and
  `component_release_index` keep their signatures and call it, so the class the CLI and `helmfile_apply`
  use is unchanged — the delegation is not a shim, because every method still does its own work of
  handing those paths to the apply and to the cluster-side checks that follow.

  **What this removes.** `class _ManifestReader(HelmUtils)` in `config_answers.py` is gone. It existed
  only to inherit a filesystem read through a class that builds a Kubernetes client, and its
  docstring claimed the helm layer was what made the release index true. It is not: the index is
  parsed out of the manifest the product ships, and the only reason the config layer needed
  `HelmUtils` was that this file had not been written yet. The parser is one function now, reached
  from both layers, so a release added to the manifest appears in `kmt component install` and in
  `kmt config show`'s universe from the same one read and no list in between.

  **What did not move, and why.** Anything a binary answers stays in `helm_utils.py`: the
  `helm list` behind `_installed_release_names` and `_cluster_release_locations`, `helm show chart`
  behind `get_latest_chart_version`, the apply itself, and `refuse_renaming_a_deployed_release`,
  which needs the manifest *and* the cluster and so needs both halves. The split line is "needs a
  subprocess or a `Context`" versus "needs a `Config` and the files" — nothing was moved across it,
  and the same dicts, keys, order and warnings come back either way. A missing manifest still raises
  `FileNotFoundError`, which is still what `refuse_renaming_a_deployed_release` catches and the CLI
  turns into a sentence. The module's eventual home is `core/config/`; that directory does not exist
  yet and is a later phase's move, so this landed flat.

- **`kubementat.errors` is the module that names what kubementat refuses with, and
  `TeamNotSuppliedError` moved into it.** `class KubementatError(Exception)` is the base every domain
  error descends from, so one catch can mean "a kubementat refusal" instead of one type at a time. It
  carries no behaviour and no message convention; the reason it exists at all is that an error class
  which has to be caught by more than one module cannot live inside either of them without the other
  importing it.

  **The move is a move, not an alias.** `kubementat.config.TeamNotSuppliedError` is gone, and so is the
  function-level import inside `config_derived.require_team` that existed only because that class sat in
  a module importing the one that needed it. There is no compatibility alias, no re-export, no
  deprecation branch: every `raise`, every `except`, and every import — the command layer's and the
  suite's — names `kubementat.errors` now, and a caller that still imports the old path gets an
  `ImportError` rather than the right class from the wrong address. `kubementat.cluster` still raises
  it, but it imports it rather than re-exporting it, so nothing in the tree keeps its identity through
  a module that was never its home.

  **The other 35 exception classes are where they were.** They are a decision of their own — several of
  them are `click.ClickException` subclasses, which is a question about who renders the refusal rather
  than about where a class is defined — and dragging them along here would have made a two-line move
  into a rewrite nobody could read.

- **An environment's own kubeconfig is read ahead of `$KUBECONFIG`, and the advice is to put it inside
  the configuration directory.** `platform_config/<environment>/kubeconfig.encrypted.yaml` is now consulted
  when nothing declared a path — after `--kubeconfig`, `$CLUSTER_KUBECONFIG_PATH` and the environment's own
  `CLUSTER_KUBECONFIG_PATH`, and before Kubernetes' own rule. A shell variable that outvotes an environment
  you named is the defect: a stray `export KUBECONFIG`, or a different laptop, answers for `dev` and a
  deployment meant for one cluster lands on another while reporting success.

  **The filename is the encryption.** A prepared tree declares `*.encrypted.*` for git-crypt, so a kubeconfig
  placed in that directory under any other name arrives as plaintext and *nothing complains about it* —
  git-crypt simply stops touching the file, and the failure is a secret sitting in a config repo rather than
  an error anyone sees. The name was chosen to match the filter that is already installed there, which is why
  it is asserted in a test and not left in documentation: a rename would silently un-protect every kubeconfig
  that followed the advice.

  **Two claims worth checking rather than trusting.** An installation that never heard of this is unaffected,
  because the file is *optional* and its absence is the ordinary case — the change landed against a tree that
  does not have one, and every existing test still resolves through the declared path or `$KUBECONFIG`. And
  `config init` had to be told this file is expected: its guard refuses *any* file kubementat did not write,
  which was right until the resolution order started reading one, and following the advice would otherwise
  have produced the sentence "move the file kubementat told you to put there". A genuinely foreign file is
  still refused, in its own test.

  **What this does not fix.** `~/.kube/config` is still the last answer, and `$KUBECONFIG` still answers for
  an environment that carries no file of its own. Making those impossible is a stricter change than this one,
  and it would break a tree that is working today. The advice closes the hole by making the common case
  explicit; the refusal to do it for you is a separate decision and is still open.

- **`kmt config` keeps seven verbs, and `explain`, `diff` and `migrate` are deleted (owner instruction,
  2026-11-05).** `kmt config --help` now offers `init`, `add-environment`, `add-team`, `set`, `unset`,
  `show` and `validate`, and nothing else. They were not deprecated, hidden behind a flag, or left dead
  but harmless: the verbs, the modules behind them, and the tests that covered them are all gone.

  **`explain` answers nothing that `show` does not already print.** `show` prints every key the tree
  answers as `KEY = value   (<layer> from <path>)`, so the answer and the layer that answered it arrive on
  the same line the value is on. The e2e harness read its state back through `explain` in five places and
  those reads are line-parses of `show`'s listing now, so the rule that a lane configures through
  `kmt config` and never with `jq` is untouched and each of the five sites asserts what it asserted before.
  `explain_answer` and `AnswerExplanation` leave the library with it; the four test files that used them as
  their layering oracle read `effective_answers` and a key lookup instead, which answers key, value,
  origin, source and default from the same resolution.

  **One fact survives nothing, and it is stated rather than worked around.** `searched` — the list of
  files precedence consulted, highest layer first — existed only on `AnswerExplanation`, and `show` does
  not print it. The single assertion that named it was deleted with the verb instead of being replaced by
  a `show` assertion that could not say it.

  **The rename table is withdrawn, and so is the guarantee its census gate enforced.**
  `src/kubementat/config_renames.py` is deleted with the two verbs that consulted it, which means
  kubementat records no renames and applies no migration: it reads what it now reads, refuses a moved path
  by name when the old location still exists, and rewrites no operator tree. **The gate that used to fail
  the suite when a default key left the product without a table entry is gone**, so nothing in the suite
  will complain when that happens; the CHANGELOG and a release note are the only channel left that says
  what changed. Where an earlier entry of this file pointed at `config migrate` or at the table, the
  sentence now says what the operator does instead, which is nothing.

  **The upgrade path is no longer measured, and that is a decision.** It used to be measured by running
  `kmt config diff` between `N` and `N+1` and asserting the component set survived. Nothing replaced that
  instrument, so no command and no lane says what a bump would cost; `config validate` only says whether
  the tree survives one. A `schema_version` two steps behind is still refused before anything touches the
  cluster, and the refusal now names the line that closes the gap (`schema_version` in
  `<dir>/kubementat.yaml`) instead of naming a command that would do it, because no command will. If this
  ever becomes measurable again the instrument is a statement of what changed between versions, not a
  re-implementation of a verb this project decided it did not want.


- **The e2e lane configures through `kmt config`, and no longer through `jq` (T-33 / R-16.1).** R-15.1's
  sentence is blunt — "an e2e lane that writes configuration files directly is a defect, not a shortcut"
  — and the harness had fourteen `jq` invocations that did exactly that, in four shapes, none of them new:
  a `jq --arg` per key, a `jq '.[key]=null'` for the state `unset` is the only way to leave, a
  `jq --slurpfile` that merged a grafana row into `static.encrypted.json`, and `mkdir -p` plus two
  `write_file`s for a fixture environment or team. Every one of them is now a row in the capability
  table (`config.set` in four shapes, `config.unset` in two, `config.show` for read-back, and
  `config.add-environment` / `config.add-team` for fixtures) and the lanes go through `stages.py`'s three
  helpers, which is where the layer and the file get named once instead of per stage.

  **Three things the lanes used to prove are gone, and what they prove instead.** `environment-names-
  its-cluster-in-static-json` read back through `config explain` — through `config show` since that verb was
  deleted on 2026-11-05 — so the claim about *layer 3 of §4.2* now comes out of the tag kubementat puts on
  the key's own line and not out of the harness pointing at a path and calling it that layer. The §4.3 refusal is built by `config unset`, which *deletes* the key: the message it asserts
  is `is not set` and not `is null`, because the state `unset` leaves is the state a human reaches, and a
  `jq` to `null` was measuring a file no product verb writes. The collision and unresolvable-sibling
  fixtures are built by `config add-environment` and answered by `config set` instead of `cp -a`, which
  says something about the check: the second environment is now an environment, and not dev's directory
  wearing another name.

  **Two things stopped being necessary rather than being replaced.** The `GRAFANA_ADMIN_PASSWORD` that
  `suite.py` randomized into the guest's environment on every run is gone, because `config init` generates
  that credential itself and an exported answer is a second authority over a key the tree already answers
  (R-7.2). And the CI-image rewrite that stage 02 ran immediately after the preparation is gone: §13.4's
  rule is that a lane answers only what the layers do not, so `DOCKER_REGISTRY_BASE_URL` and
  `TEKTON_CI_IMAGE_NAME` are `config init --set` arguments and `TEKTON_CI_IMAGE_TAG` is left alone because
  the shipped layer already answers `latest`. The row still reports and now reads the tree to say so.

  *The suite gained no self-checks for this.* Whether `jq` writes configuration anywhere is a question
  answered once by reading, not a fact about kubementat, so it is not encoded as a test; the owner's
  standing rule is that this suite covers kubementat's behaviour and does not audit its own harness.
  What the changed harness tests assert is unchanged in kind: which credential a login carries, which row
  the accounts stage was told to create, which file a refused rename edits.

- **`kmt config set --from-file PATH` answers a whole JSON document in one run (T-22d / R-16.3).** The
  flag was already on this verb's stated surface — R-16.1's own evidence row spells it as `config set
  KEY=VALUE` **and** `--from-file` — and it exists for the two answers an argv cannot carry: a value too
  long to paste, and a credential that would otherwise sit in the process table, where every `ps`, every
  shell history line and every captured command would have it. The document is a JSON object and nothing
  else; `--team` and `--encrypted` still decide the layer and the file, so a document never re-decides
  where the answer belongs.

  What makes it more than a shell loop a caller could write is the order of its refusals: everything is
  decided before the first byte. A document that answers one of its keys with `null` — `null` being the
  absence of an answer, and `unset`'s business, exactly as for one answer spelled on the command line —
  is refused along with every key in it, and a tree that received a partial document is a tree no operator
  meant. Same for an unreadable path, JSON that is not JSON, JSON that is not an object, and an empty
  object, which is the case that would otherwise exit 0 while answering nothing. `--from-file` beside an
  answer on the command line or beside `--generate` is a command-line fault: `UsageError`, tree untouched.
  Nothing generated is printed, because the whole point is that the value does not come back out. (The
  harness uses it for exactly this reason: its grafana row and its foreign-team deploy key are staged
  documents, and the password never reaches a command line.)

- **`config validate` now checks the version window it used to disclaim.** The stamp check ran on every
  cluster verb through `kmt_context`, but `config validate` said out loud that the window "belongs to
  phase 9" — and phase 9 had landed without picking it up, so a tree whose `schema_version` was three
  steps stale came back **clean**, which is the state R-13.5 calls a refusal. §13.2 first named two verbs
  as the strict home for version drift; `config diff` was deleted on 2026-11-05, so `config validate` is
  that home alone, and it reads one object (`config.ConfigurationStamp`) instead of doing the arithmetic
  beside its own sentence. In
  validate the facts are findings like every other fault — collected in one pass, decided by one exit
  code:
  * `schema-version-too-old` (error) — more than the window behind, naming both versions and the
    `schema_version` line in `<root>/kubementat.yaml` that closes the gap; no command raises it for you;
  * `schema-version-behind-window` (warning) — one step behind, which R-13.5 counts as nothing to do;
  * `version-outside-window` (error) — the tree's `kubementat:` constraint against this build, naming
    what it compared and pointing at R-13.4's bump PR for the line that closes it; nothing says what the
    gap would cost, because nothing measures that any more;
  * `version-undeterminable` (warning) — a build that cannot name its own version, which is never the
    tree's fault.

  The severities did not move: a cluster command still warns about a version string and continues, and
  one object owns the window, so a cluster command and this verb cannot disagree about where a tree sits in
  it. No new flags,
  no fallback, and nothing that writes a stamp — R-15.1 says a `config` verb does not touch
  `kubementat.yaml`, and one test edits it by hand precisely so it stays that way.

- **A pipeline pod that states a cluster is now compared with the cluster its configuration declares
  (§10.4 / owner decision N-3).** A Tekton pod had two answers to "which cluster" and no referee: the
  cloned configuration declares `CLUSTER_KUBE_CONTEXT` in `<env>/static.json`, and the file that key
  names is machine-local (R-6.1), so inside a pod it is a dead reference — while the pod's own mounted
  service account would have answered for the cluster it is standing on. v2's order puts the
  declaration (layer 3) **ahead of** that service account (layer 4), and layer 3 refuses instead of
  falling through when its file is missing, so a pod that said nothing died on somebody's
  `~/.kube/config` instead of quietly installing into the cluster it was scheduled onto.

  The rule is a comparison, asked before anything resolves: a run that states its cluster through the
  dials above the file (`--kubeconfig`, `--context`, `CLUSTER_KUBECONFIG_PATH`) is now checked against
  the clone's declaration, and a disagreement is a refusal naming both claims and the flag, variable,
  or file that wrote each of them. Two claims that mean different things — the declaration says
  **which environment**, the statement says **where the work runs** — and kubementat no longer gets to
  settle it. `Context.build` gained `StatedClusterMismatchError` for the purpose; §4.2's ordering is
  untouched, nothing falls through a bad read, and a statement still answers for an environment that
  declared no cluster of its own.

  The shipped side of it: `run-tekton-pipeline` and `create-secret-from-json` take a
  **`cluster-kubeconfig` param with no default**, and the `kmt` line in each passes it as
  `--kubeconfig`. The TaskRun therefore has to state its cluster; an empty value is an answer and not
  an omission ("the environment's own declaration answers"), which is what a laptop has and a pod does
  not. A guard reads both halves off the manifests (`--kubeconfig` on every `uv run … kmt` step, the
  param never defaulted, and no `kmt` step execing git-crypt), and the example pipeline passes the
  statement through.

  **Two behaviour changes a caller has to know.** A `--context` that contradicts the environment's
  declaration is refused instead of resolving to the flag's cluster, so the three layer-precedence
  tests that made a lower layer say something *wrong* were rewritten to the shapes a precedence is
  still observable in — which file gets opened, and which name answers when the file has none. And a
  TaskRun of either Task that omits `cluster-kubeconfig` now fails Tekton's own param validation.
  No fallback, no alias, no way to ask kubementat to pick one.

- **A team that declares no SSH deploy key is answered instead of crashing `setup-secrets`.**
  `setup_secrets` looped over `SSH_DEPLOY_KEYS` unconditionally, so a tree from `config init` — which
  declares no deploy key, because that is the operator's key material and nothing here invents one —
  died with `TypeError: 'NoneType' object is not iterable`, naming no key, no layer and no team. This
  is the same bug the `install` entry below closes for `DOCKER_REGISTRY_CREDENTIALS` and the one
  `tekton_utils` closes for the cleaner's pull secret; this call site was the last of the three, and
  the smoke lane found it where no unit test could, because a fixture tree always carries a key.
  Absence now means no Secret written, a warning that names the key and says no layer declares it, and
  exit zero — the shape `create-grafana-accounts` already gives `GRAFANA_USERS`, so the two commands
  that act on an operator-declared list answer the same way when it is empty. What the command does
  when a key *is* declared is unchanged and asserted, because a guard that returns without writing is
  otherwise indistinguishable from one that was never reached.

- **The lane reads what the tree answers, and no longer what one file declares.** The same lane run
  proved this in the other direction: `releases-landed-where-the-environment-says` came out red against a
  cluster that had installed grafana into `grafana`, because the harness read
  `GRAFANA_DEPLOYMENT_NAMESPACE` out of `<environment>/static.json` with `jq` and that file had been told
  nothing since `kmt initialize` stopped copying a template into it. The tree answers the key from the
  shipped defaults, so the harness reported a value the product never chose and blamed a correct install.
  The keys that read that way now go through `config show`, which prints the key's own line and tags it
  with the layer that answered, so the answer and its provenance arrive together and a blank means the
  tree really answers nothing — the rule T-33 / R-16.1 states and this site had not caught up with. The
  other sites reading that way were broken in the same manner and had not been reached, because nothing
  but a namespace rename had ever asked them.

  **Two reads kept the file, and the reason is different in each case.** A credential is not derived and
  carries no default, so an encrypted layer file is the only place `config show` could have found it and
  the two reads answer the same question — but `show` prints the whole tree, so reading two credentials
  through it would sweep every other secret in the environment into a command the harness then has to be
  careful not to log. One `jq` of the file named is narrower, and narrower is what a diagnostic read of a
  credential is for. And whether the team layer *declares* a key is not the question its answer states:
  `config unset` refuses a key no layer declares, so a stage that wants to take a team's answer away has
  to know which case it is in before it calls the verb, and asking `show` would have answered
  `smoke-pipelines` from a rule over the team's name while nothing was declared anywhere near it.

- **The lane stops claiming a state its own verbs cannot reach, and the claim moves to the unit
  suite.** `config-negative-names-the-file` asked that a team layer which cannot be read be refused by
  path. Ticket 3 had already replaced the `mv` that made the file go missing with `config unset`, and
  that turned out to have been the end of the row and not a new spelling of it: `unset` clears a pair of
  files, so nothing is ever missing for a reader to name, and the key the probe used is answered by a
  rule over the team's name, so a layer that declares nothing still has an answer to resolve and no
  refusal follows. The row went red against a tree that was correct. It is deleted, not weakened: the
  claim is asserted by `tests/test_config_missing_team_config.py`, which proves the same thing without
  needing a lane to make a layer unrippable. What replaces the feature's name is what it still measures
  — which team directory a command's writes land in — and the row that measures that is unchanged.

- **The README's quickstart is rewritten around the command that replaced `initialize`, and a test
  runs it.** The section told a reader to `export` eight variables and then run
  `uv run kmt initialize dev dev1`. The command is deleted, so that line returns
  `No such command 'initialize'`, and the advice under it was wrong in a way that no single fix to the
  prose would have caught: `BASE_DOMAIN`, `DOCKER_REGISTRY_BASE_URL` and the rest are configuration
  keys, answered by the tree, and kubementat reads nothing out of `os.environ` except
  `KUBEMENTAT_HOME`, `KUBEMENTAT_PLATFORM_CONFIG_DIR`, `KUBECONFIG` and `ENVIRONMENT` — four names that
  say where something is, not what something is. So exporting `BASE_DOMAIN` answered nothing and said
  nothing about why, which is the quiet half of the deletion: the command that used to read the
  environment went away, and the sentence that told you to set it stayed behind.

  It now says `kmt config init <directory> --environment ... --team ... --gpg-email ...`, with
  `--set` and `--encrypted-set` for the keys that genuinely have no default, then `kmt config set` for
  the cluster, then `kmt environments list`, then `kmt install ... --config-dir`. Two asymmetries the
  rewrite states instead of leaving to be discovered: the `config` verbs take the directory
  positionally while the verbs that address a cluster name it with a `--config-dir`, and the deployer
  key is now a file that belongs to that directory (`deployer_ssh_key.pub` beside it, gitignored) and
  not a key inside `static.json`, so the paragraph telling the reader to register
  `GIT_DEPLOYER_PUBLIC_KEY` from `static.json` pointed at a key no version of this product writes.
  `KUBERNETES_DEFAULT_STORAGE_CLASS` was never a key at all; the one that exists is
  `TEKTON_KUBERNETES_STORAGE_CLASS`.

  **The guard is that this is tested and not asserted, and it is the pattern the codebase already had.**
  `test_an_instruction_that_names_a_command_is_one_that_parses.py` runs what a refusal prints and looks
  for Click's usage error, because a printed instruction that exits 2 is the same class of bug as a
  printed sentence that is false. The same applies to the document, so
  `test_the_commands_the_readme_prints_are_commands.py` extracts every `uv run kmt` line from the
  README — joining backslash continuations, because the fence holds one command broken over four lines
  and reading them separately would verify four commands nobody wrote — substitutes the placeholders it
  recognises, and fails if any of them is an unrecognised blank rather than guessing at what the reader
  was meant to type. A command that parses and then refuses about the directory is a pass: the reader's
  command worked, and what it objected to is their tree. One check that runs the CLI cannot do is
  stated as a separate test, because `kmt config diff` *prints help* when a verb is gone and would
  otherwise be read as a success.

  **The rest of the documentation was already telling the truth, and one file was not.** The only stale
  command reference left outside the README is `cluster-config-v2.md`, which describes `kmt initialize`
  in the present tense in two places while the rest of what it settles still holds — that an environment
  is one cluster, that a kubeconfig is the credential store, that a context is named per command. It now
  says so at the top and points at the verb that took over, rather than being rewritten in place where a
  reader would have no way to tell which of its claims survived. Research and plan documents are dated
  records and are not touched: a file that says what was true on 2026-10-27 is not a defect. `setup-secrets` writes one Secret per entry of
  `SSH_DEPLOY_KEYS` and says so when it finds none, and the row that checked the round trip read an empty
  file, got an empty list, and had no assertion left to make — a green row for a command that had never
  run. The lane gives the team one entry through `config set --team --encrypted --from-file`, the same way
  the grafana account row is seeded, so the round trip measures a key's bytes arriving in the namespace
  that entry names and the row that refuses to guess says so when they do not.

- **Specification citations stop being comments' source of truth, and 1713 of them go away.**
  `AGENTS.md` now states the rule — *a comment is written for a reader who has never seen the
  specification* — and the codebase predates it, which is why it cites everything: `§4.2's layer 3`,
  `R-6.3`, `TD-1`. Measured before the change: 1713 citations across 191 files, 575 of them in `src/`
  and the bigger half in `tests/`, where every test file's docstring opened by naming the rule it came
  from. Each reference became the sentence it stands for — `§4.2's layer 3` is *the environment's
  `static.json`, the layer an operator edits* — and where the citation was only a pointer to where a
  rule was written down, it went away.

  **The guarantee had to be checked rather than claimed, and the check was wrong twice.** A 191-file
  prose change cannot be reviewed by reading it: a rewritten comment and a rewritten statement are
  indistinguishable in a diff, and a diff that long is the one a reviewer stops reading. So the guarantee
  is structural — comments never reach a parse tree, docstrings are removed by position, and the code that
  remains is compared per file. The first version of that tool reported *clean* for a rewritten condition
  inside a function whose name had not moved, because it compared function names instead of their bodies,
  which would have blessed the whole sweep on the strength of nothing. The second version had a hole of a
  different kind: an assertion's failure text lives in a string literal, so rewriting it does reach the
  tree, and calling that a behaviour change would have filed around forty lines of every batch as an
  accusation — while blanking strings makes a reworded message and an edited comparison look identical.
  The verifier answers three ways now, logic moved / string values moved and must be read / prose only,
  and it refuses to guess which kind a string is.

  **That third answer is what caught the two things that would otherwise have been missed.** A worker
  stopped and asked how to satisfy "no citation survives" and "no behaviour moves" at once for citations
  inside assert messages, having measured that editing one really does move `LaneRunner` in the tree; that
  question is why the three-answer shape exists. And in the last eleven citations, a dangling `(FIN-52)`
  looked like a textbook deletion and was not — it is a member of a tuple matched as a substring against
  documentation, so shortening it makes the test forgive more than it should. The diff shows a comment
  touched and says nothing about a gate being loosened. Both cases are now in the tree as tests.

  One assertion did have to go: a refusal's message was asserted to contain a document identifier, the
  only such assertion in the suite, and the same claim was already made two lines above by checking that
  the message *states the rule*. The identifier was removed and the sentence check left exactly as strict,
  because putting a number back into a message no operator can look up is what this ticket exists to stop.

  **Nothing cites the specification any more, except where citing it is the point.** Eight citations
  survive, all in two files: fixture strings a test feeds to the verifier as its own input, and one
  expected value matched against a document. They are data, they quote deliberately, and deleting them
  would change what those tests are about rather than how they read. Code-line pointers of the form
  `cluster.py:737` remain — measured at 57 before this ticket and 57 after, so this ticket introduced none
  of them — and that is a different kind of staleness, for a ticket that has not been raised.

- **A bootstrap lane proves "one command, ready, installed" on a real machine.** The ten properties that
  define a prepared directory existed only as unit tests against fixture trees, which cannot show whether
  git-crypt's ordering step was executed in the right order — and that is the one property that decides
  whether a tree is safe rather than merely configured. The lane prepares a fresh directory with real
  `git`, real `git-crypt` and real `gpg`, asserts every property against that filesystem, then installs
  into a cluster with no hand-written file anywhere in between, so the last row is a command that ran and
  not a list of paths that exist. Its shape is the smoke lane's, because that last assert needs k3s; what
  is cheaper is the stage list — no pipeline run, no rerun, no feature stage, no uninstall.

  **The second git-crypt assert is written against the object store, and the specification names an
  instrument that cannot see the leak.** The checklist's sentence is right and its command is not.
  `git log --all -p -- <encrypted path>` was run against a tree that really did hold plaintext in an early
  commit and it printed no plaintext lines at all, because once `.gitattributes` is live the diff driver
  owns how that path is displayed — and the same command on a clean tree is equally quiet, so the grep
  answers zero either way. What replaces it asks every revision the path ever had for its stored blob and
  greps that, which is the only place the bytes were actually written. The credential is resolved inside
  the guest and never crosses the wire, so the assert does not give a generated secret a second home in an
  argv. Both asserts are stated in the same run and the reason is that the first one is correct when the
  file was later re-added, and re-adding is exactly the shape that hides the leak.

  **The lane's own directory is elsewhere on purpose.** One property says the deployer keypair is a
  property of the configuration directory and not of the program, and that has a second half — absent
  from the checkout — which no assert about a tree living inside the checkout can read. So the root is a
  field of the lane shape now, named once, and `install` and `uninstall` say `--config-dir` for it
  instead of inheriting it from an exported variable: a configuration root is named and not located. Ten
  capability rows changed to the placeholder and nine of them resolved to the argv they already produced;
  the two that changed gained the flag, and their value is the one the variable had been carrying. The
  same rule applied to the paths the stages build themselves, where reading the wrong tree is invisible
  in the only direction that matters.

  **Two harness rules changed with it.** Stage budgets are read only for the stages a scope actually
  selects, so a lane that legitimately skips the run, rerun and feature stages no longer carries their
  numbers and dies on a missing key before stage one — and the pair of lane and scope is checked at the
  command line, because discovering it in stage two means creating and destroying a VM to learn the
  harness's own shape was wrong. A lane that reports what it wrote now derives that name from the root it
  reads rather than spelling `platform_config/...` in place, so a lane that prepared elsewhere cannot keep
  reporting a file it never touched.

  **Green on a real machine, after two runs that were not.**
  `~/.kmt-e2e/runs/20261105T174105Z-bootstrap-bootstrap-e989756/result.json` — 44 assertions, 0 failed,
  246 seconds, four vcpu and six gigabytes, all ten properties named as asserts. Both earlier runs failed
  inside the harness's own reads, and neither is a mistake a unit fixture could have caught on its own:
  `stat -c %a %n FILE` is one format and two file operands, so `%n` is looked up as a filename and the
  read fails while still printing the mode; and the validator assert looked for the word "error", which
  appears in the sentence that says nothing is wrong.

- **A team that names no private registry no longer crashes `install`.** `setup_docker_registry_access`
  looped over `DOCKER_REGISTRY_CREDENTIALS` unconditionally, so a tree from `config init` — which declares
  no registry, because there isn't one yet — died with `TypeError: 'NoneType' object is not iterable`,
  naming no key, no file and no verb. `tekton_utils` had already been fixed for the identical bug on the
  cleaner's pull secret; this call site never got the same treatment, and no unit test could see it
  because `tests/conftest.py` puts a credentials block into every fixture tree. Absence now means no
  Secret and no service-account patch, with a log line naming the file that would change that. An empty
  `imagePullSecrets: []` patch is the quieter version of the null-filled Secret this replaces, so it is
  skipped rather than applied.

- **Grafana's admin password is generated at `config init`, because helmfile makes it required.**
  `helmfile.yaml.gotmpl` renders both halves of the admin pair with `requiredEnv`, so a tree that does not
  name one does not install grafana badly — it installs nothing, and aborts with a message naming a
  variable and a line number rather than the `static.encrypted.json` that would have answered it.
  `helm_utils` was warning and continuing, on the sentence "the password is not rendered from the
  environment, so it is only warned about when it is absent", which the template had long since made
  false. A warning that describes an outcome the code prevents is a bug and not a nicety, and this is what
  the first real VM run tripped over after `config init` got far enough to try.

  Generation and not seeding, because a constant admin password in shipped code is a credential every
  installation in the world shares — strictly worse than the absence it replaced. What answers first wins:
  a named value, then a seeded default, then something generated, and the read of the existing file is
  what keeps a second `config init` byte-identical instead of asking for a different password every run.
  Six tests fail when generation is removed.

  The same correction is applied to the shipped template's own header comment, which named only the user as
  required while the body asked for both.

- **The lane pins its cluster and checks it, instead of recording it.** D8 said "k3s version is recorded,
  not pinned", and the record was the whole implementation: `01-provision.sh` installed whatever
  `get.k3s.io` served that day, the version landed in `result.json`, and nothing read it. A stable-channel
  promotion then landed on this host as a guest running `v1.37.2+k3s1` whose
  `helmcharts.helm.cattle.io` never finished establishing; k3s fataled every sixty seconds, zero pods came
  up, and the suite died in `01-provision` without reaching `02-install`. The evidence pointed at a version
  change nobody had chosen. `01-provision.sh` now pins `v1.36.5+k3s1` — the release the last green full run
  recorded, not the newest one — reinstalls a guest that arrived with something else, and `02-install`
  asserts the guest's own answer against that pin, so a promotion is a named failure rather than a
  mystery. The version lives in the script that execs the installer and not in a Python constant that
  cannot, which keeps it one number in one place.

- **The checkout ships no configuration directory.** `platform_config/dev/.gitkeep` and
  `platform_config/prod/.gitkeep` were tracked, the lane copies the checkout into the guest, and R-3.2's
  foreign-material guard refused a tree containing files the product had shipped itself. They go because
  the reason they existed is gone: a `.gitkeep` carries an empty directory, and these were placeholders
  for configuration that used to live inside the product's tree. §10's premise is that configuration is a
  separate repository read from a root that is not the code checkout, so a checkout holding `dev/` and
  `prod/` at its top level is not empty scaffolding — it is an old shape still on disk. Nothing in the
  product read them, and `docs/README_TEKTON.md` said the opposite, so that paragraph now says what is
  true: a working tree is a directory you point `config init` at, and there is nothing here to copy out
  of.

- **A tree you get from `config init` can be installed (§16, from the first real VM run).**
  `helmfile_apply` requires `GRAFANA_ADMIN_USER` in `<environment>/static.encrypted.json` and refuses
  before applying anything, which is correct and deliberate. What stopped being correct is that nothing
  wrote the key: it lived only in `assets/templates/environment/static.encrypted.json.template`, and
  `kmt initialize` — deleted in T-28 — was that template's only reader. A tree from `config init` carried
  `{}` and its first `kmt install` died on a key nobody had been given the chance to set.
  `config init` now seeds `constants.DEFAULT_ENCRYPTED_ANSWERS`, which is exactly one key and says in the
  table why only one: the template's four `REPLACEME_` sentinels are not seeded, because a placeholder
  that reads as a chosen value in a credential file is worse than the absence it replaced. An operator's
  `--encrypted-set` still wins over the seed.

  This was invisible to 1687 passing tests because `tests/conftest.py` seeds the key into every fixture
  tree. The hazard is not fixtures that are wrong, it is **fixtures that are richer than the product**.


- **A cloned configuration is opened by a step of its own, and the key to open it is something kubementat
  creates (§10 R-10.3).** `git_crypt_symmetric.key` is gitignored, which is the whole problem in one word:
  a clone of the configuration repository arrives **locked**, and every step that reads `platform_config`
  after `git-clone-configuration` sees ciphertext with no sentence to say so. Nothing in the lane had an
  answer for that until now.

  The shape is the one that was ruled: **a `git-crypt-unlock` step, run after the clone and before anything
  that reads configuration**, wired by `runAfter` in the deploy template and both cloning examples. A step
  and not part of the clone, because a clone step that also unlocked reported success on a repository with
  no keystore, which is the failure an existing guard already forbids.

  A pod has no filesystem to find a key on, so the key arrives by `secretKeyRef`, from a
  **`git-crypt-configuration-key` Secret that `kmt install` creates** in the pipeline namespace — convenience
  that is part of the UX, per the ruling. Its value is the file's bytes base64-encoded once, and the step
  decodes once; both ends are measured against git-crypt 0.7.0 against a real encrypted repository rather
  than asserted, because a doubled layer presents exactly as a wrong key (`not a valid git-crypt key file`)
  and would have read as somebody's bad key rather than our bug.

  **The Secret is created only for a directory that declares `filter=git-crypt` in its `.gitattributes`.**
  That marker is chosen because it is the git-crypt fact that *travels* — the keystore lives under `.git/`,
  which is not cloned, and the key is gitignored, so a predicate built on either would answer "no" to the
  exact tree needing unlocking. This was a bug found after the first shape: requiring the key unconditionally
  made `setup_pipelines` refuse an install for a file that was never part of a tree that never used
  git-crypt, which is a regression this ticket would have shipped. `uses_git_crypt` is the single marker and
  the step's own grep reads the same literal from the clone, so the two decisions cannot disagree.

  Measured, and written down where an operator will read it (`docs/README_TEKTON.md`): an empty key and a
  missing Secret are indistinguishable at the step and are the same fix; a key from another repository says
  `encrypted file has been tampered with!` and leaves the tree configured-but-undecrypted, so retrying
  without changing the key reproduces it; and already-unlocked is silent, so a green step proves the tree is
  readable and nothing more.

- **A pipeline now clones its configuration instead of borrowing it from the code checkout (§10 R-10.1,
  T-71's second half).** `deploy-local-helm-chart.yml`'s `path-to-configuration` defaulted to
  `$(workspaces.workspace.path)/kubementat`, which is the directory `git-clone-automation` creates out of
  `automation-git-project-name` — so the step read configuration from wherever the **code** had been
  fetched, and it was right for one reason: the fork carried `platform_config` inside it. That is the
  accident §10 exists to retire, and it was measured rather than reasoned: neither `kmt` step in CI passed
  a root at all until `4c1889f`, and `KUBEMENTAT_PLATFORM_CONFIG_DIR` appeared in zero shipped files, so
  naming the root was already a rule with no rule-follower.

  The shape is the one that was ruled: **one workspace, two clones, two paths.** The deploy pipeline gained
  a `git-clone-configuration` step that fetches `automation-git-url` into `configuration-git-project-name`
  (default `configuration`), landing beside the code clone rather than in a second mount. That is fewer
  moving parts for a lane already made of Tasks, and it costs one thing the spec names honestly: **two
  params now have to agree on one path**, and Tekton does not check. The clone step binds a directory
  name, the reader binds a path, and if they drift the clone succeeds, the step succeeds, and the failure
  is a missing file twenty minutes into a run. So the shipped templates state the root instead of letting
  the Task default infer it, `configuration-git-project-name` is the one place to change, and a guard test
  reads the agreement off the shipped templates and examples.

  What was **not** added, deliberately: no `CONFIG_GIT_URL`. R-10.2 is the sentence that decides what
  `automation-git-url` means — it stopped describing where the product lives and started describing the
  configuration repository — and inventing a key would be the gap-filling that rule refuses. So for an
  operator whose configuration still lives in their fork the two clones fetch the same URL twice, which is
  wasteful and correct, and when the configuration genuinely separates no step changes: only the URL does.
  The code clone stays too, and not by oversight: the CI image ships `uv` and does not install kubementat,
  so `uv run kmt` still resolves the entry point from a checkout (§12's wheel lane would retire it, and
  that lane was parked on 2026-11-03 and is not part of this change).

  Two contracts were changed on purpose and are named where they are pinned, because both are things a
  calling pipeline binds to: `run-tekton-pipeline.yml` and `create-secret-from-json.yml` gained
  `configuration-root`, so the root a command *reads* is no longer spelled from the checkout the step
  *starts* in, and `create-secret-from-json`'s `cd` and its `--config-dir` are now two different
  directories by statement rather than by luck. Both param-set tests say so in the assertion instead of
  allowing the addition silently.

- **One stale param was carried by the shipped examples since T-70 and is gone.** T-70 deleted the
  `git-crypt-unlock` param from `git-clone-with-ssh-auth.yml` (§10 R-10.3: unlocking is not kubementat's
  job, and the fork has had nothing encrypted in it since §11.1), but two example pipelines kept passing
  `git-crypt-unlock` to that Task and the matching pipeline-run kept setting `automation-git-crypt-unlock`.
  A param whose consumer is deleted does not fail loudly: it stops meaning anything, and the pipeline that
  passes it reads as though an unlock were still configured. **Whether Tekton 1.17.1 rejects the apply was
  not established** — no cluster was reachable while this was written. What is measured is smaller and
  firmer: three steps elsewhere in the tree hand Tasks params those Tasks never declare, so undeclared
  params are either tolerated by Tekton or three shipped pipelines are broken, and that question is left
  open rather than answered by assumption. The measured count for this one: 4 `crypt` mentions under
  `examples/` before, 0 after, 0 under `assets/`, where `a46a7ad` removed both the step and the param.

  A new guard, `tests/test_a_step_only_passes_params_the_task_it_calls_declares.py`, pins the whole measured
  set of steps that pass a param their `taskRef` does not declare — the three above recorded as *known and
  unresolved*, so the set cannot grow quietly and the record of them does not depend on someone
  remembering. Re-adding `git-crypt-unlock` to either example turns both of its tests red (checked by
  mutation, not by reading).

- **`kmt config diff` exists, and the sentence that pointed at it is finally true (§13.2, T-92).**
  *(Deleted 2026-11-05, by owner instruction; see the entry at the top of this file. Nothing reads what
  this verb reported, and no command replaces it.)*
  `check_configuration_stamp`'s version-string warning has been telling a human that *"config diff is what
  says what changed in between"* since `37a2613` wrote it, and `kmt config diff` answered **`Error: No such
  command 'diff'`** (measured; the group registered nine verbs, none of them this one). That is the same
  bug class this epic has corrected three times now, and it is the fourth instance of it, which is why this
  ticket's first sentence is about the message and not about the verb.

  What the verb reports is four things and no fifth: **the stamp** as a fact, with the window this build
  reads spelled rather than implied; the **answers that come from built-in defaults**, which is §13.5's real
  risk but is not a loss; the **paths the product moved**, taken from `config_migrate.plan`'s own predicate
  rather than a second walk of the tree; and the **keys the tree declares that this build applies by
  nothing**, which is the only finding that fails the run. The exit rule is derived from that
  classification and nothing else: a default answering for a tree that named nothing exits **0**, a tree one
  schema step behind exits **0**, a version string outside the constraint exits **0** — R-13.2 is explicit
  that a working cluster must not be bricked by one, and `config validate` and `config diff` are the two
  verbs licensed to be strict about *answers*, not about numbers. One dead key exits **1** and names the
  `kmt config unset` that removes it, spelled with the root and the environment asked about, so the
  instruction runs instead of being completed by the reader.

  Two absences are stated rather than left to drift. **`--to V` is not registered**, and §7's row spells it:
  nothing in this repository carries a per-version history and every entry in the rename table sits at
  `schema_version: 1`, so a flag that accepts a version and then prints this build's answer regardless would
  make a release decision look checked when it was not. The verb answers *this tree against this build* —
  which is already what §13's release procedure does, since it edits the stamp by hand — and the flag's
  absence is pinned by a test that hands it `--to 2` and asks for Click's usage error, so the next person to
  read §7's row does not add it back as a harmless convenience. **Generated manifests are not classified**,
  and that is a gap in the product and not a shortcut taken here: measured, no generator output in this
  repository writes a generated-by header, so §9's rule that generated files be reported *as generated*
  separately from operator edits is currently unanswerable from the file, and guessing would be worse.
  Making the generator write one is a second ticket.

  Measured evidence: `uv run pytest -q` → **1658 passed, 1 skipped** (1648 before, 10 cases added). The
  census that makes a dead key visible goes through `shell.run` and not a `subprocess.run` of its own —
  FIN-02's one-module execution policy is enforced by a test, and this file broke it on the first draft and
  was corrected by that test rather than by reading it. Mutation-checked: widening the census so the planted
  dead key looks read turns three of the ten cases red, and returning an empty census turns six red, so the
  guard is not passing because the census is vacuous.

- **A key that leaves the product is now required to be written down, and there is a verb to move what
  the old spelling left behind (R-13.3, T-91).** Two halves, one table and one command. The table is
  `src/kubementat/config_renames.py`: three entries, each with the commit that moved it, what the verb
  is allowed to do about it (`rename`, `move`, or `report`), and the schema version it is current
  under — **1**, bumped by nothing, because R-13.4 makes bumping a release action and this epic moved
  no key's meaning. The two path entries are not an accident of wording: §9's trigger-directory move
  and §4.4's entry-point change are both things that happened to files, and neither is a key rename
  that could be spelled one way. The guard is
  `tests/test_a_key_that_leaves_the_product_without_a_rename_entry_fails_the_suite.py`, and its live
  case is a measurement and not a fixture: `git grep -h -o -E '"[A-Z][A-Z0-9_]{3,}"'` over
  `src/kubementat` answers **162 keys at `131aff5`, 161 at `HEAD`**, and the one key in the first set
  that is not in the second is `KUBERNETES_DEFAULT_STORAGE_CLASS`. Census S's honest answer — that the
  gate R-13.3 promises had never had a live case to bite on — is now closed, and deleting an entry
  from the table turns the suite red without anyone having to delete a key first.

  The verb is `kmt config migrate` *(deleted 2026-11-05 with the rename table it applied; kubementat
  records no renames and applies no migration)*. **Dry-run is the default and `--apply` is the only thing that
  writes a byte**, proved by hashing every file in the tree on both sides of the run; measured on a
  prepared tree, a dry run leaves `prod/static.json` holding the old key and prints what it would have
  done. It never commits: OQ-3 (owner, 2026-11-04) puts generated output and the commit for it with
  the operator, so every line it prints says what is owed instead of pretending it is paid. A `report`
  entry is answered in words and never written, because §4.4 made the operator's copy of
  `helmfile.yaml.gotmpl` unreadable and deleting a file somebody else owns is a decision.

  Two things this verification found and fixed rather than left as claims.

  * **`--apply` used to be able to migrate half a tree.** Measured on a tree where the key rename was
    clean and the trigger move was blocked by a destination that already held a manifest: the rename
    landed, the move raised, and the file came back with `TEKTON_KUBERNETES_STORAGE_CLASS` while the
    old `triggers/` was still in place. The module's own docstring said the ordering made that
    impossible.
    It was true of the refusals before the write and false of the ones inside it. Every planned write
    is now asked of `_refusal` first, so a run that refuses leaves the tree byte-identical, and
    `test_apply_refuses_everything_it_cannot_do_before_it_writes_any_of_it` says so by fingerprint. The
    same fix covers a file that declares both spellings of a key: neither wins by default.
  * **The remedy must stay reachable.** `migrate` asks `read_configuration_stamp` — the question "is
    this a prepared tree" — and deliberately not `check_configuration_stamp`, whose §13.5 window is
    what sends an operator here in the first place. A verb that ran its own gate would refuse the only
    tree that needs it. The cost is visible and is stated rather than hidden: `migrate` is quieter
    about the product version than every cluster verb, and prints no version-string warning.

  The sentence this verb exists for, `config.py`'s refusal of a tree two schemas behind, is unchanged
  and now true. Out of scope and said rather than discovered: `config diff` is still not there, and
  `check_configuration_stamp`'s version-string warning points a reader at it (§13.2 names it, §13's
  own release procedure runs it, and phase 9's next ticket is the one that ships it). The verb set and
  each command's exact flags are pinned in `tests/test_cli_surface.py`.

- **Both CI `kmt` invocations now name their configuration root (§10 R-10.1's second clause, T-71).**
  `run-tekton-pipeline.yml` and `create-secret-from-json.yml` are the only two shipped Tasks that
  execute `kmt` — a claim worth restating because "there are zero `kmt` invocations in CI" was once
  reported here on a grep that filtered its own output, and both of these were hiding inside it. That
  finding was corrected by reading the two lines rather than counting them, and this entry is the
  consequence of the correction. Neither Task passed `--config-dir`, and
  `KUBEMENTAT_PLATFORM_CONFIG_DIR` appears in **zero** places under `src/kubementat/assets/`,
  `examples/` and `tekton_ci/`, while §8 had already deleted the fallback
  that used to answer the root for a command that said nothing. So both Tasks were not merely
  drifting from R-10.1: their `kmt` step could not run at all. Measured on this checkout, that
  shape answers
  `Error: no configuration root is named. … it has no default root to fall back to.`

  Each step now names the root on its own `kmt` line, derived from the parameter the Task already
  declares: `$(params.path-to-configuration)/platform_config` where there is a directory param,
  `/workspace/$(params.automation-git-project-name)/platform_config` where the step rebuilds the
  checkout path with a `cd` instead. That is the convention the Tasks reading configuration by path
  already spell (`deploy-local-helm-chart.yml:128`, `execute-mysql-file.yml:70`,
  `create-mysql-database.yml:59`), and it is deliberately a **separate value** from `workingDir` and
  `KUBEMENTAT_HOME`: those two exist so `uv` can find the *project* whose entry point it resolves,
  which is a different directory from the one a command *reads*. Collapsing them would have been the
  easy edit and would have cost the one line §10's source B needs to move the root to its own cloned
  workspace without moving the step.

  What changed in the prose is not cosmetic. `run-tekton-pipeline.yml`'s `path-to-configuration`
  description said the step "starts in the same directory it reads its configuration from" and that
  `KUBEMENTAT_HOME` was where the command read that configuration. Both sentences stopped being true
  the moment §8 deleted the checkout fallback, and they are gone. The guard is
  `tests/test_every_ci_kmt_invocation_names_its_configuration_root.py`, and the rule is enforced in
  the shipped YAML rather than in `Config` on purpose: most of this suite's trees are fixtures and not
  prepared directories, and putting a tree-shape rule in the wrong layer is how a previous ticket
  broke 227 tests. Out of scope, and said rather than discovered: `deploy-local-helm-chart.yml`'s
  `path-to-configuration` **default** is untouched here — pointing CI at a cloned configuration
  workspace is §10's source B, and census R measured that no such clone, workspace, or config-repo key
  exists yet. One collateral: `tests/test_cli_surface.py` had counted every token after a command name
  as a positional, which was invisible until a shipped Task passed a flag for the first time.

- **The version stamp is read, and it gates (R-13.1's read side, R-13.2, R-13.5).** `config init`
  has been writing `kubementat.yaml` with `kubementat: ">=0.4,<2"` and `schema_version: 1` since the
  preparation existed, and **nothing in `src/` ever read it**. A half-built version gate is worse than
  no gate, because someone skimming the tree finds one and assumes it gates. It gates now.

  Two checks with two severities, and the split is the requirement and not a taste. A **version
  string** outside the declared range warns once and continues: a running cluster must not be bricked
  by a range that drifted, and strictness belongs to `config validate` (§13.2), the only verb that has
  been strict about it since `config diff` was deleted on 2026-11-05. A
  **schema version** two or more steps behind refuses before anything reaches a cluster, because then
  every answer that is not named in its own layer is arriving from a built-in default and the cluster
  is being configured by kubementat's assumptions instead of the operator's (§13.5). One step of slack
  is accepted, because §13.5 says N reads N and N-1.

  The refusal of an unstamped tree is a decision and not a default: rescuing it by assuming
  `schema_version: 1` would trust a tree that was never prepared, and this epic carries no backwards
  compatibility anywhere.

  Two things worth knowing before this surprises anyone. The check runs **before** `Context.build`,
  resolving the root the same way the builder will — the first version of this change put it after
  the build and the ordering test caught that resolution had already happened. And the check lives in
  the CLI's `kmt_context`, not in `Config`: wiring it into `Config` made 227 tests fail, all of them
  meaning "this fixture is a hand-built directory and not a prepared tree", so the three tree
  builders the suite shares (`conftest.py`'s fixture class, `cluster_fixture.checkout`, and the
  inline roots three files built themselves) stamp their trees now. `packaging` is not used for the
  range check — it is only ever present transitively and
  `test_the_declared_dependencies_are_what_the_tree_needs.py` would rightly refuse the dependency for
  two operators — so the parser is about thirty lines that raise on an operator it does not
  understand rather than reading it as a pass.

- **The clone step stopped unlocking the repository it had just cloned (T-70, R-10.3).** Both
  `git-clone` Tasks had a branch that decoded the deployer's **GPG private key** from a Secret,
  imported it into the pod's keyring, and ran `git-crypt unlock` on that checkout — and that checkout
  is the product fork, which since §11.1 has nothing encrypted in it at all. The reason to delete it
  and not document it is that in a pod the failure is invisible: a repository with no keystore
  unlocked by a ring holding a secret key exits **0** and changes nothing. The step imported a private
  key into shared infrastructure for no purpose and reported success. The param, the `secretKeyRef`,
  and six pipeline-side forwarding params are gone, 118 lines of pure deletion.
  `test_no_clone_step_unlocks_a_repository_it_never_encrypted.py` guards it, and is verified to bite
  rather than merely pass — reverting the removal fails all four cases. The guard is deliberately
  narrower than *no git-crypt in CI*: §10.3 still wants a future step that pipes
  `git_crypt_symmetric.key` into `git-crypt unlock -`, and that one legitimately execs git-crypt.
  What cannot come back is the clone step unlocking what it cloned, and a **private** GPG half
  entering a pod. Neither §10.3's step nor `deploy-local-helm-chart.yml`'s `path-to-configuration`
  default is touched here, because both need a configuration-repo clone that census R measured does
  not exist in these assets yet. What that leaves behind is a decision and not an oversight:
  `GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64` now has no reader in CI, while surviving in the encrypted
  template, the e2e harness, `tests/conftest.py` and a comment — it is how an operator would seed the
  Secret §10.3 reads, so removing it is ruling on that design and not tidying.

- **Generated triggers moved under `tekton/`, and the old directory is refused by name (T-60).**
  `Config.tekton_team_trigger_dir` resolves `<team>/tekton/triggers/` now, beside the team's other
  three generated-output directories, which is where §9 said generation lands. Generation and
  reading already agreed with each other — both go through the property — so the change is the one
  path, not a read/write reconciliation. What is new is the refusal: a tree that still has the
  pre-move `<team>/triggers/` directory holds manifests nothing reads, and both points that would
  make that silent now stop and name both directories — `_apply_team_trigger_resources` at the
  point of reading, `generate_trigger_configs` before it writes, and `config init` / `config
  add-team` before either scaffolds the new layer beside the old one. Nothing is renamed, migrated
  or deleted: the content is the operator's to move by hand, and it will not be moved by anything the
  product builds — kubementat records no rename table and rewrites no operator tree. Two adjacent things were
  deliberately left alone, and both are worth saying out loud: `<environment>/tekton/triggers` and
  `<environment>/tekton/pipeline-runs` are still not created, because no property names them and no
  reader opens them — §9 lists them, but a directory nothing reads is the shape this epic deleted,
  and the guard in `test_config_init_creates_the_directories_a_reader_looks_in.py` is right until a
  reader exists. And generated output is untracked after generation (`git status` shows
  `?? <env>/<team>/tekton/`) — **owner ruling 2026-11-04: the operator commits it, the generator does
  not.** That is what the code already does; generation writes and stops. It settles OQ-3 without
  closing §9's verification bar, because that bar asks for `git status` to be clean after a *fresh
  clone with no generation step*, and regeneration is byte-identical, so the clean tree is the
  operator's habit and not a property kubementat can assert.

- **The deployer's key stopped being registered by email address.** `config init`'s step 7 used to ask
  `git-crypt add-gpg-user --no-commit <email>`, and ask the question of the running user's keyring.
  Two things were wrong about that, and only one of them produced an error message. The visible one:
  key generation ran `gnupg.GPG()` with no home, so the deployer's key — **private half included** —
  landed in the operator's own keyring, and once that had happened twice gpg answered `more than one
  public key matches` and step 7 failed, six steps after the tree was already written. The dangerous
  one is that the same situation on a tree pair can *not* fail: the code took `fingerprints[0]` of an
  email's key list, git-crypt accepted whichever key it picked, and the second directory installs,
  prints `created`, and cannot decrypt its own secrets. There is no error on screen anywhere.

  Step 7 now reads the fingerprint out of the directory's own `gpg_public_key.gpg` — the file that is
  definitionally *this* tree's key — and registers that, so two trees under one email get two
  different keystore files and a collision is not something the code has to remember to avoid.
  Generation goes into a keyring created for the call and deleted in a `finally`, which retires the
  self-granted exemption `config_keys.py` used to carry for it, and `--trusted` is passed every time.
  It is free: across a `--trusted` registration the ring's ownertrust listing is byte-identical
  before and after and `gpg --list-keys` still reports `unknown`, because the flag is git-crypt
  handing gpg a per-invocation trust-model override and not a write to somebody's keyring. An
  earlier draft of this change treated that as a side effect worth avoiding and made the flag
  conditional; measurement retired that, and `config init --gpg-key` is what proves it — the imported
  public half is the unusable shape, so without the flag that path fails with `Unusable public key`
  and the feature would not work at all.

  One consequence worth knowing before you rely on this: step 7 no longer declines to encrypt to a
  key the operator has not signed. That is what `--trusted` buys and what it costs. The recorded-
  binary suite still patches the keyring question, so it cannot see this class of bug — that is the
  reason it survived, and the live checks for it are in the commit.

- **`kmt initialize` is deleted, and R-15.4 says why that is a mapping exercise and not a removal.**
  The command is unknown to the CLI, no test invokes it, and no `src/` path execs `git-crypt` outside
  the preparation module that `kmt config init` calls. The table below is R-15.4's, and the right-hand
  column of each row is what was measured at this HEAD rather than what the spec predicted when the
  row was written; two of its rows had no successor when the spec was written and do now.

  | `initialize` did | Where it went | Measured here |
  |---|---|---|
  | Nineteen template copies into `platform_config/` | **Nowhere** — product defaults resolve at read time (R-3.2) | **Overstated: 7 paths, not 19.** Four `static*.json`, one by-name `mirrored_docker_images.json`, and two team `values.encrypted.yaml` copies. The two `static*.json.template` files sitting at the team directory's root are never copied, because the walk only descends into subdirectories. `CHANGELOG.md:919` ("19") and the "11 templates" sentence in `docs/index-overview.md` are both stale and both corrected here |
  | Four `static*.json` writers, values from exported variables | `config init`'s layout + `config set`'s answers (R-15.2) | **Covered, and narrower than it was.** Same four paths; the variables are gone. A key is answered by `--set`, by `config set`, or by a shipped default, and no key arrives from the process environment |
  | Deployer SSH/GPG keypair generation into the checkout | `config init` into the config directory (R-11.4) | **Has a successor.** `config_keys.require_deployer_keypair` reads the directory it is given, and `setup_pipelines` builds the two deployer Secrets from it. Live: a tree prepared by `config init` resolves all five paths of the keypair, and a directory holding one half of the pair is refused |
  | `git-crypt init` / `add-gpg-user` / `export-key` in the checkout | `config init`'s §11.3 dance, in the config directory | **Has a successor.** The only `git-crypt` execs left in `src/` are the preparation module's, and they run against the prepared directory. Live: the committed blob of `dev/static.encrypted.json` begins `GITCRYPT`, the working tree shows `{}` because the smudge filter decrypts it, and `config set` twice in a row preserves both keys — which is the read-modify-write this repo had never done through a git-crypt filter before |
  | `uv sync --all-extras` in the checkout | Deleted (R-12.2) | Already deleted before this ticket |
  | The `CLUSTER_KUBE_CONTEXT: null` stderr sentence | `config init` prints it (R-6.3) | **One implementation, not two.** The old call site is gone and the sentence is asserted by the `config init` tests, so it survives as an API |
  | Dependency check | Per-verb guard (R-7.3) | Covered by the gate `kmt install` shares; the command no longer needs its own preamble |
  | Eight required variables | Configuration (R-7.2) | **Covered, but the action is different and a reader should know which one.** `initialize` refused and printed the example `export` block; `config init` answers the same keys with `--set` and says nothing about their absence, because a key that has a shipped default does not need answering. What no longer exists is the refusal |

  **What was deleted, measured and not estimated.** `git collect` went from **1596 to 1565**: **38**
  collected tests disappeared and **7** appeared, so the delta is **−31**. That is 32 functions in the
  three guards that existed to watch `initialize` write (`test_everything_initialize_writes_is_a_key_the_
template_declares.py`, `..._adds_the_deployer_without_committing_into_the_checkout.py`,
`..._writes_nothing_where_configuration_already_exists.py`), 3 new ones that say the deployer Secrets
are built from the configuration directory's keypair, and 6 renames. Nothing was deleted to make the
number move; each disappeared test named a behaviour that is no longer constructible, and the guard
that replaced the write-set derivation is `config init`'s own plan.

  **Two claims that did not survive contact with `src/`, corrected in the open.** The lane's cluster
  declaration and the deployer's public key were both described as things a command writes into
  `static.json`. `GIT_DEPLOYER_PUBLIC_KEY` is read by nothing — it appears in no `src/` file and no
  test — and the deployer's public half is `deployer_ssh_key.pub` in the prepared directory, which is
  what the README now points at. And the shipped `values*templates` number in
  `docs/index-overview.md` was **11**; it is **2** team values templates plus five `static*.json`
  templates and one mirrored-images file, which is the 7 above. The templates stay in the tree:
  deleting a template is a decision, and this ticket deletes a command.

  **What the live check caught that the suite could not.** A suite running on recorded binaries cannot
  see a failure only a real binary produces, so the whole flow was run under `/tmp` with real `git`,
  `git-crypt` and `gpg`: `config init` refused a directory with no identity and left no `.git` behind,
  prepared one when given `--gpg-email`, and on the second run skipped all 20 steps, wrote no byte
  and made no second commit — `git rev-list --count` is 1. `environments list` reads the tree, and
  `install` gets all the way past configuration to the one refusal that belongs there, which is the
  cluster. The finding worth the exercise: a first run does not work with the flags the README used to
  show, because `--gpg-email` is required and there is no `--remote`; both are now documented as they
  actually are.

- **A guard that only passed on this machine.**
  `test_the_readme_directory_list_names_directories_that_exist` fails on a clean checkout at the
  previous commit, because README's directory list promised `__scripts__` and `scripts/` is in no
  tree of this repository's history. It passed here for as long as it did because empty directories
  survive a `git rm` and git does not track them, so the list was pointing at this machine's disk and
  not at the repository. A reader who `cd`s into it gets a shell error and no hint, which is the defect
  this file already calls a direction. The bullet and its two sub-bullets are deleted, and
  `docs/index-overview.md`'s "empty — 0 shell scripts" heading becomes what it is now: no directory.
  The `jq` line in README's install list stays, because four shipped Tekton Tasks still parse with it
  — it is the *reason* that was wrong, naming a script deleted in `504af9a`.

- **`kmt config init` now prepares the directory it is handed, and that is a change of verb.** `config
  init` calls `config_bootstrap.prepare_configuration_directory` instead of `config_init.init_configuration`
  and prints what comes back, so the command is now the whole of R-11.3: `git init`, `.gitattributes`,
  `.gitignore`, the layout, the deployer keypair, `git-crypt init`, the collaborator registered with
  `--no-commit`, the symmetric key exported at `0600`, and one commit of kubementat's own. The row
  before this one is the library half of this one, and its queue row said what it still lacked — "no
  flag, no printing, no closing prose" — and **that is superseded**: the library still decides
  everything, and `config init` is now the thing that asks. Measured on a fresh tree: stdout goes from
  the 5 lines `init_configuration` returned to the **16** the preparation applies in order, a second run
  prints the same 16 as `skipped`, writes no byte outside `.git`, and makes no commit (`1583 passed, 1
  skipped` at the start of this ticket, **1594** passed at the end). `init_configuration` is unchanged
  and stays the library-level sibling — the verb changed, not the decision-maker. **The flag set is now
  10** where it was 6 (`test_cli_surface.py`'s table is the claim): `--gpg-key` and `--gpg-email`,
  `--git-url`,
  `--no-commit`, and `--force`, whose wording changed. Nothing is passed to the library that it does
  not take a keyword for.

  **What the ordering is for, and why the print is that order.** `init_configuration`'s encrypted layer
  is written *after*
  `.gitattributes` exists, so the first `git add` encrypts instead of shelving the plaintext into the
  object store forever. That rule is the reason the preparation exists, and the print does not reorder
  it: the stdout is exactly the order the library applied the plan, asserted as one list, because a
  reordered print is a second account of a write that happened some other way. `unlock`, `status`, and
  `check` still appear nowhere (R-11.7), and no line of the closing prose carries key material — paths
  only, which is asserted and not assumed.

  **A refusal that can be refused is refused before the first byte.** A bare `config init <dir>
  --environment E` with no identity used to decide that four steps in: `git init` had already made a
  repository, `.gitattributes` and `.gitignore` had been written, and the operator was left with a
  directory that reads as prepared and is not, which is also the state the next run reads as
  "already initialized". `config_keys` promises every refusal is decided before a file is written, and
  that promise was being broken from outside `config_keys`. The identity question is now answered ahead
  of step 1, so that run leaves no `.git` and the refusal names the two flags that answer it. A
  directory holding *one* half of a pair is still `config_keys`' own refusal, and `--gpg-key` pointed
  at a missing file still gets its own sentence: both of those are about what is in the directory and
  not about what the operator forgot to type, so neither moves. `initialize` is not deleted by this row
  — that is T-28 — and nothing here is its removal.

  **What `--force` does not do, because that half of its wording is what the flag is for.** It accepts
  material kubementat did not write and touches none of it. It is not an overwrite: a file that already
  differs from what this verb would write is refused either way, decided by byte-identity. And it does
  **not** lift the refusal of a `.gitattributes` or a `.gitignore` somebody else configured. That
  refusal is about a rule a human chose and not about material, and `force` was never its remedy — the
  test that proves it runs `force=True` over a foreign filter rule, refuses, writes nothing, commits
  nothing, and leaves the keystore uninitialized (`test_config_init_prepares_the_directory_it_is_handed.py`,
  in the case named `test_an_attributes_file_somebody_else_wrote_is_refused_and_force_does_not_lift_it`).

  **The closing prose is on stderr, and stdout is the file list only.** Guidance about state is what
  `warn_cluster_not_addressed` is on stderr for, and everything after the step lines is that: the
  symmetric-key sentence, printed once and only when this run exported the file, saying that the path it
  names is the ability to read every credential in the cluster and is `0600` and gitignored; the push, a
  named URL when `--git-url` gave one and "the commit is local, add a remote" when it did not — no URL
  is invented and no remote is named that kubementat did not add; the deployer's public key the git host
  needs read access to; then the install command, spelled the way `kmt install` takes it — the team as
  its second positional and the directory as `--config-dir`, because install reads a root it is told and
  locates none. A run that created no team says so and names `kmt config add-team`, because printing
  `kmt install dev` for that tree prints a command that exits 2. `--git-url` is a flag about the summary
  and not about the repository: it configures nothing.

  **A prepared tree is named absolutely, which is what makes a relative `root` work.** Steps 1, 8 and 9
  exec with `cwd=root`, so an argv path spelled against the calling directory resolves twice: measured
  here, `git-crypt export-key cfg/git_crypt_symmetric.key` answered `System error: open:` and the run
  died four steps after it had already made the repository. Nothing in the suite could see it, because
  every fixture root is absolute, and nothing in the lane could see it, because no harness stage called
  the verb yet — it is reachable the moment `config init` is the thing that prepares a directory, which
  is this row. `prepare_configuration_directory` resolves `root` once, on the way in, and so does
  everything it prints. The alternative was spelling the export argv relative to `cwd=root`, which fixes
  one exec and leaves the next one to break the same way.

  **Two tests' fixtures are now one module.** `tests/bootstrap_tools.py` holds the recorded `git` and
  `git-crypt`, the patched generators, the patched keyring, the directory under preparation, and the
  log-reading helpers, in `tests/asset_paths.py`'s shape, and both `config init` test files use it. The
  reason is not tidiness: a fake kept in two files is a fake that can drift, and a drift in the
  git-crypt shim silently changes what "idempotent" is measured against. 11 tests are new (9 at the
  verb, 2 at the library) and 11 pre-existing verb tests were re-pointed at the prepared command; their
  claims about what is printed survived and their step lists grew. Two claims had to be rewritten, and
  they are not cosmetic: a missing `--environment` and a duplicated `--set` key are still refused by
  `init_configuration`, four steps in, so those runs now leave the git half behind while writing no
  tree. Which names a question this ticket did not answer: **the decisions that belong ahead of step 1
  are the keypair's and the layer names' and the answers'**, and today only the first is there.

- **`config init` can now *prepare* a directory, not just fill one in.** `config_bootstrap.
  prepare_configuration_directory` makes a configuration directory that a first `kmt install` can
  actually use: `git init`, then `.gitattributes` and `.gitignore`, then `config init`'s own layout,
  then the deployer keypair, then `git-crypt init`, the collaborator registered with `--no-commit`,
  the symmetric key exported at `0600`, and one commit of kubementat's own (R-11.3). Nothing here
  re-decides a name: the layout comes from `config_init.init_configuration` and the keys from
  `config_keys.ensure_deployer_keypair`, so a second run reports `skipped` per step instead of starting
  a second history. The ordering is the product: writing `.gitattributes` after the first encrypted file
  still produces a correct-looking working tree and still puts that file in the object store in
  plaintext, so the claim is asserted by reading the tree's state while step 4 runs, which is the only
  assertion that can see it. `unlock`, `status`, and `check` appear nowhere (R-11.7).

  **The `own_paths` seam, and why it is not `force`.** `init_configuration` grew a keyword defaulting to
  empty, so the git files and keypair files a prepared tree accumulates are *declared* to its
  foreign-material guard instead of forced past it. `force` means "accept what I cannot account for",
  which would silently overwrite an operator's own filter rules, and the refusal a prepared tree would
  otherwise get gets louder the more successfully init has already run. Byte-identity still decides each
  declared path (`created` / `skipped` / refusal), and a plain `config init` is byte-for-byte phase 3.
- **`TRAEFIK_INGRESS_CLASS` has one owner now, and a tree that declared a class gets that class on the
  cluster.** The key was answered in `constants.py` (as `L0`, the row before this one) and spelled a
  second time inside `helmfile.yaml.gotmpl`, where the traefik release configured its
  `ingressClass.name` from `env "TRAEFIK_INGRESS_CLASS" | default "traefik"`. Measured before this row:
  **nothing in `src/` exported that variable** — `_export_component_namespaces` exports the namespaces
  and `export_component_release_knobs` the two release knobs, and neither touches this key — so `env`
  found no variable, the manifest's `default` fired on every apply, and the routing release was
  configured with `traefik` no matter what the operator had written, while the Python side named the
  declared class in every generated Ingress, in the cert-manager solver and in the EventListener
  ingresses. One line each for what reaches helmfile: *before*, a tree declaring `"public"` reached the
  release as `traefik` and every manifest as `public`; *after*, it reaches both as `public`. What made
  this a bug and not cosmetics is that the two sides were each internally correct — a rerun of
  `kmt routing install` was the only way to find out which class the cluster's controller actually
  served.

  **The half that changed the code and not the key.** `helm_utils.export_ingress_class` is the export,
  called from `HelmUtils.helmfile_apply` beside the namespaces and the knobs, and the read underneath
  it is `Config.component_ingress_class`, which walks the component layers the way the namespace
  accessor does. Two decisions in that function are load-bearing: it does **not** skip a key no layer
  declared — the neighbours' skip is right for a knob whose answer belongs to the manifest and wrong
  here, where `constants.py` answers — and it carries **no fallback of any kind**, because an answer
  that is genuinely missing is refused where it is read (`KeyError`, naming the table that stopped
  answering) rather than exported as the word that happens to be right. The manifest's `default` is
  gone and nothing replaced it: a second one there would fire only when the export breaks, which is the
  failure that looks like success. With no variable and no export, what `helmfile build` renders is an
  empty class name and not a silent `traefik` — measured in
  `tests/test_the_ingress_class_the_manifest_names_is_the_one_the_configuration_answers.py`, which also
  asserts the built release says what the layer said.

  **Which trees this changes, and what they have to do about it.** Any tree whose environment-level or
  team-level `static.json` declares a `TRAEFIK_INGRESS_CLASS` other than `traefik`: the next
  `kmt helm apply` or `kmt routing install` moves that release's `ingressClass.name`, and on a cluster
  that has been serving inlets under `traefik` all along that is a class change and not a cosmetic one
  — existing `Ingress` objects keep naming the old class unless they are rewritten, and the controller
  stops serving what still names it. Move the workloads or rename the class first; this product will
  not silently keep applying `traefik` to make that go away. A tree that declares nothing is
  unaffected: `traefik` still answers, from `constants.py` through the export instead of from the
  manifest, and the report tags it `default` exactly as it always did.

- **Ten defaults `initialize`'s templates carried are now read from `constants.py`, and the values did
  not change.** `TEKTON_VERSION_PIPELINE` (`1.17.1`), `TEKTON_VERSION_DASHBOARD` (`0.73.0`),
  `TEKTON_VERSION_TRIGGERS` (`0.37.2`), `TEKTON_CI_IMAGE_NAME` (`ubuntu-ci-minimal`),
  `TEKTON_CI_IMAGE_TAG` (`latest`), `TEKTON_KUBERNETES_STORAGE_CLASS` (`local-path`),
  `HELM_DEPLOYER_SERVICE_ACCOUNT_NAME` (`helm-deployer-sa`), `AUTOMATION_GIT_PROJECT_NAME`
  (`kubementat`), `AUTOMATION_GIT_REVISION` (`master`) and `TRAEFIK_INGRESS_CLASS` (`traefik`) live in
  one table, `constants.DEFAULT_STATIC_ANSWERS`, and `config.py`'s one resolution walk consults it
  after the layers have had their say. That is R-3.2's "resolved at read time": `L2 > L1 > L0`, a
  value an operator wrote still outranks the table, and `Config.declared()` never sees it, because a
  default is an address and not a deed. Census L had already measured the hole this closes — the keys
  were answered only by the template copies `initialize` wrote, and `config init` writes no such copy,
  so the tree that exists today answers almost none of the set.

  **The part that was not the values.** Wiring them into the *view* and not only into the single-key
  walk is what made this a fix and not a rename: most of these keys are read as
  `config.get('env_static').get(…)` — `install`'s release list, the CI image, the substitution table a
  generated PipelineRun is filled from — and a default that answered only the walk would have been
  reported as working and then failed in the guest. Two consequences had to be said out loud:
  `kmt config show` answers fifty keys on a fresh tree and not forty, and a pin that is *omitted* now
  installs the template's version while a pin that is *declared and answered nothing* still stops the
  install before anything is sent, which is the half of the guard that keeps "fail before the cluster"
  whole. `kmt create-ingress` keeps its warning sentence too: the answer moved out of
  `routing.DEFAULT_INGRESS_CLASSES`' own literal and into the table, and `config.py` still says when an
  answer did not come from a file.

  **What this ticket did not do.** The templates are still there and `initialize` still copies them —
  they die with `initialize`, which is a later ticket — and the values are byte-identical to them.
  `PIPELINE_NAMESPACE` and `APP_DEPLOYMENT_NAMESPACE` are deliberately **not** in this table: their
  template strings were placeholders for the team substitution and R-3.5 answers them as rules, so a
  default would be a second owner of a computed value. `MYSQL_HOST` / `REDIS_MASTER_HOST` are derived
  for the same reason and the encrypted set is generated, never defaulted. **`TRAEFIK_INGRESS_CLASS`
  had two owners and one of them was in the manifest**: `helmfile.yaml.gotmpl` spelled
  `env "TRAEFIK_INGRESS_CLASS" | default "traefik"` while the template and now `constants.py` say
  `traefik`. Both agreeing today is exactly when drift starts, and the ground it was not settled on —
  that whether that inline `default` should read from configuration is a decision about the whole
  `env`/`default` style of that file and not this ticket's — is recorded in `.agent-work/t30b-report.md`.
  **That sentence is not true of this tree any more.** T-31 settled it one owner later: the manifest
  half is gone, the answer is exported, and what that changed about the cluster is the first row of this
  file and `.agent-work/t31-report.md`, not a footnote here.

- **Five answers are rules about the team, and one line now separates where things go from what may be
  deleted.** `config_derived.py` answers `APP_DEPLOYMENT_NAMESPACE` (the team),
  `PIPELINE_NAMESPACE` (`<team>-pipelines`), `MYSQL_HOST` (`mysql-<team>`), `REDIS_MASTER_HOST`
  (`redis-<team>`) and the namespace inside an SSH deploy-key entry. They resolve at read time as `L0`, so
  a tree that answered nothing but a team name is still askedable, and an operator who writes the key in
  `static.json` is believed — the override half is tested first, because a rule that answers ahead of what
  the operator wrote is a rule that became a lie and would pass every derivation test there is. This
  replaces what `initialize` did by `.replace("REPLACEME_TEAM", team)` on a template, and it is not
  generation: `PIPELINE_NAMESPACE` has one correct answer, `MYSQL_ROOT_PASSWORD` has many, and only the
  second one goes through `config set --generate`.

  **The consequence that needed a ruling, and got one.** Derivation lands in the read path, so it also
  changed what `kmt uninstall` deletes: a team directory that declared no namespaces used to be skipped,
  and after the derivation it would have been swept, because the namespaces had become answerable. That was
  accepted as *wrong* — a rule names an address, not a deed, and this cluster may already hold a namespace
  called `pandas` that kubementat never made, which is not recoverable by editing a file. So `Config` has
  two doors now: `get()` for "where does this go", `declared()` for "what does the file say". The teardown
  deletes `entry["deletable"]` and uses the derived list only to say what it declined, `uninstall_pipelines`
  refuses when `PIPELINE_NAMESPACE` is answered only by the rule, and `Config.get`/`Config.declared` raise
  the same errors for the same missing file so the two doors cannot describe one problem two ways.
  `kmt users` keeps deriving, because binding a Role is placement. Say it plainly: **a namespace no file
  named is no longer deleted, even though kubementat can now tell you what its name would be.**

- **The deployer's keypair is now something a configuration directory has.** `kubementat.config_keys`
  arrived with two verbs and one refusal: `ensure_deployer_keypair(config_dir, gpg_key=..., gpg_email=...)`
  makes the pair exist and reports its work in `config_init`'s own step record (`created`, `skipped`,
  `existed`) rather than inventing a second shape for the same question, and
  `require_deployer_keypair(config_dir)` hands the pair to a caller or refuses. What it refuses is the
  finding census L measured: `setup_pipelines` used to hand both deployer keys to the Kubernetes client
  without asking, so a tree prepared without a keypair died three layers down in a serializer instead of
  naming the file. The pair lives **inside** the configuration directory and not beside the product
  (R-11.4) — the five names were already reserved there for environments — and
  `git_crypt_symmetric.key` is deliberately **not** this module's to write. Two decisions stated once: an
  `--gpg-key` that is supplied is **imported** and invents nothing, and a generated private key is
  unencrypted on purpose, because a passphrase in a non-interactive path is what this module exists to
  avoid and the file sits in a directory that is gitignored and about to be git-crypt-protected. The module
  reads no `os.environ`: `initialize` derived the deployer's GPG identity from `BASE_DOMAIN`, and a keypair
  module that guesses at an identity registers the wrong collaborator. **Not wired yet** — `config init`
  does not call it, so nothing changes for a run today.

- **The product's default values files are named for what they are, and git-crypt's declaration has left
  the checkout.** `src/kubementat/assets/values/` now ships
  `logging/{alloy,loki}_values.yaml`,
  `monitoring/{grafana,prometheus,prometheus_blackbox_exporter,prometheus_helm_exporter}_values.yaml`,
  `openbao/values.yaml`, `polaris/values.yaml`, `routing/cert_manager.yaml` and `traefik/values.yaml`,
  with nothing written inside any of them and the manifest's ten `values:` entries pointing at those ten
  paths. Census K had already measured why this is a naming fix and not a migration: all ten were
  plaintext in the blobs before the change (`git crypt status` says of them "staged/committed version is
  NOT ENCRYPTED!"), they hold placeholders and comments rather than credentials, and the real values
  reach the releases through `platform_config` and the exported environment. So nothing was decrypted,
  moved or rotated — the false half of the name was removed, and `.encrypted` was the only part that was
  false. `cert_manager` kept its prefix, because `cert_manager_values.yaml` would add a word no commit
  has ever used for the file. The resulting set is not uniform, and that is the arithmetic of removing
  `.encrypted` from ten differently-shaped names rather than an inconsistency to tidy: six come out
  `<component>_values.yaml`, three come out the bare `values.yaml` that names their release directory,
  and the tenth is `cert_manager.yaml`. Uniformity would have needed a rule over files whose names
  were already correct. The root `.gitattributes` is deleted and replaced by nothing: its first line
  (`*.encrypted.* filter=git-crypt diff=git-crypt`) has nothing left to declare once the product ships no
  encrypted files, and its second (`*.template !filter !diff`) exists only to keep templates out of a
  filter that is no longer declared.

  **One correction to make here, because `5c91e47`'s own message is wrong and a reader will go looking.**
  That message says the declaration "moved to" `src/kubementat/assets/gitattributes.tpl`. It did not go
  there: the `git mv` in that batch failed and **no such path has ever existed in this repository** —
  `find src -name '*.tpl'` returns nothing, nor does `git log --all -- 'src/kubementat/assets/gitattributes.tpl'`
  name a commit. What replaced the deleted file is a one-line constant, `GITATTRIBUTES_BODY`, written by
  `config_bootstrap._git_attributes`. This row is where the epic records the change, so the correction

[Showing lines 1-565 of 2567 (50.0KB limit). Use offset=566 to continue.]