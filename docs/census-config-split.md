# Census: `src/kubementat/core/config/config.py` before a five-way split

Compiled read-only from the subject file and greps over `src/` and `tests/`; no source file was edited and no git state was touched.

Subject file: `src/kubementat/core/config/config.py`, 1 446 lines (`wc -l`), 34 module-level
names — 18 module constants, 14 module functions, 2 classes (`ConfigurationStamp`, `Config`) —
of which 25 carry no leading underscore. The branch is `v2-arch-refactoring`.

Two facts stated up front because they disagree with the numbers this census was handed: HEAD here is `2aa1224`
(`git rev-parse HEAD`), not `f334ca8`, and the file is 1 446 lines, not 1 444. The suite was not
run, so "1775 passed, 1 skipped" is unverified at this SHA.

Method, verified two ways: the section map is from `ast.parse` on the file (spans from
`lineno`/`end_lineno`, so it does not depend on where a reader thinks a section ends), cross-checked
against `grep -n` for `^(class |def |=)`. The cross-file numbers are from `git grep -n` over `src/`
and `tests/`, cross-checked by walking `ImportFrom` nodes in every `.py` under `src/` and `tests/`
so a comment that merely names the module cannot inflate an import count.

## 1. Section map

Five target files, assigned by the section a name already sits in. **LOC** counts the name's own
statement block, comments included, no double-counting; 1 293 of 1 446 lines are attributed, the
153 unattributed lines are header comments and blank separators above the first section rule.

### paths.py — 6 names, 111 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `KUBEMENTAT_HOME_ENV_VAR` | module const | 46-46 | 1 | `_directory_from_environment` via `default_kubementat_main_dir:684`; `Config.__init__` indirectly |
| `KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR` | module const | 47-47 | 1 | `default_platform_config_dir:298` |
| `_directory_from_environment` | module function | 229-253 | 25 | `default_kubementat_main_dir:262`, `default_platform_config_dir:298` |
| `default_kubementat_main_dir` | module function | 256-262 | 7 | `Config.__init__` (684); 2 imports outside |
| `default_platform_config_dir` | module function | 265-306 | 42 | `resolve_platform_config_dir:343` |
| `resolve_platform_config_dir` | module function | 309-343 | 35 | `Config.__init__` (698); `cli/commands/environments.py:29,65`, `cli/context.py:49,199` |

### versions.py — 6 names, 67 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `_PRODUCT_DISTRIBUTION` | module const | 351-351 | 1 | `product_version:364` |
| `_CONSTRAINT_OPERATORS` | module const | 352-352 | 1 | `satisfies_version_constraint:412` |
| `product_version` | module function | 355-366 | 12 | `ConfigurationStamp.running_version:515`; patched 5× in tests |
| `_version_parts` | module function | 369-387 | 19 | `satisfies_version_constraint:412,418` |
| `_compare_versions` | module function | 390-395 | 6 | `satisfies_version_constraint:417` |
| `satisfies_version_constraint` | module function | 398-425 | 28 | `ConfigurationStamp.version_is_outside_constraint:533` |

### stamp.py — 3 names + 10 class members, 149 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `SCHEMA_VERSION_WINDOW` | module const | 350-350 | 1 | `ConfigurationStamp.window:499` (only reader in `src/`) |
| `read_configuration_stamp` | module function | 428-457 | 30 | `ConfigurationStamp.read:487`, `check_configuration_stamp:565` |
| `ConfigurationStamp` | class (frozen dataclass) | 461-539 | 79 | `check_configuration_stamp:566`, `config_validate.py:62,284`; members `read:485`, `current:491`, `window:496`, `oldest_accepted:502`, `schema_is_too_old:506`, `schema_is_behind_window:510`, `running_version:514`, `version_is_determinable:518`, `version_is_outside_constraint:522`, `version_gap_sentence:536` |
| `check_configuration_stamp` | module function | 542-580 | 39 | `cli/context.py:49,199`; 2 imports |

### keys.py — 11 names, 108 LOC

| name | kind | span | LOC | read by / reads |
|---|---|---|---|---|
| `LAYER_TEAM` | module const | 98-98 | 1 | `TEAM_PAIR:102`, `CATEGORY_LAYERS:120`, `_answers_from_a_team_layer:143` |
| `LAYER_ENVIRONMENT` | module const | 99-99 | 1 | `ENVIRONMENT_PAIR:103`, `CATEGORY_LAYERS` |
| `TEAM_PAIR` | module const | 102-102 | 1 | `COMPONENT_LAYERS:130`; asserted as an attribute in a test |
| `ENVIRONMENT_PAIR` | module const | 103-103 | 1 | `COMPONENT_LAYERS:130`, `Config._component_layers:1385` |
| `CATEGORY_LAYERS` | module const | 119-125 | 7 | `Config._load_config:950`, `Config.declared:1044` |
| `COMPONENT_LAYERS` | module const | 130-130 | 1 | `Config._component_layers:1385` |
| `_NOTHING_DECLARED` | module const (sentinel) | 133-133 | 1 | `_declare_in_layers:188`, `_layered_view:225`, `Config._release_knob:1224`, imported by `config_answers.py:61` |
| `_answers_from_a_team_layer` | module function | 136-143 | 8 | `_declare_in_layers:173`, `_layered_view:218` |
| `_declare_in_layers` | module function | 146-188 | 43 | `_layered_view:224`, `Config._resolved:1084` |
| `_layered_view` | module function | 191-226 | 36 | `Config._load_config:950`, `Config.declared:1044` — no other caller |
| `component_config_key` | module function | 583-596 | 14 | 13 imports outside the file: 5 in `src/`, 8 in `tests/`; used inside by `component_release_enabled:1157`, `component_release_version:1189`, and `_release_knob`'s callers |

### config.py — what stays: 6 names + 29 class members, 858 LOC

`TEKTON_API_VERSION:27`, `TEKTON_API_GROUP:28`, `TRIGGERS_API_VERSION:36`, `TRIGGERS_API_GROUP:37`,
`NON_TEAM_ENVIRONMENT_DIRECTORIES:61`, `TEAM_SCOPED_CATEGORIES:66`, and `Config:599-1446` (29 members:
`__init__:637`, `_team_scoped_path:771`, 8 path properties 793-853, `_load_config:855`, `get:959`,
`declared:1009`, `_ensure_config_loaded:1047`, `_config_path_of:1058`, `_resolved:1073`,
`component_namespace:1087`, `component_release_enabled:1117`, `component_release_version:1159`,
`_release_knob:1191`, `teams:1247`, `component_service_selector:1284`, `component_ingress_class:1293`,
`_component_value:1329`, `_warn_once_about_default:1366`, `_component_layers:1379`,
`_layers_to_check:1387`, `_defaulted_warning:1404`, `_load_json_file:1440`).

`Config` is 848 of those 858 lines, so the split is dominated by 6 classes-and-helpers moved out of a
file that is 59% one class.

### Sections that are not a section — 4 findings, listed rather than guessed

1. **The four API constants have no section.** `config.py:27-37` carries a per-constant rationale
   comment and no rule block. They are read only by `Config.__init__:659-662`, which copies them onto
   `self`, and by `automation.py` through `self.config.TEKTON_API_VERSION`. Nothing in `paths/stamp/
   versions/keys` wants them, so by elimination they stay; a split that drops a `# --- keys ---` rule
   header above them will not carry them, and a reader will look for them in `keys.py` because they
   look like API vocabulary.
2. **`NON_TEAM_ENVIRONMENT_DIRECTORIES` (`config.py:61`) is a directory rule with no directory code
   around it.** Its only read in `src/` is `Config.teams:1280`, and its only other mention is a
   comment (`config_init.py:135`). It is paths-shaped but keys/config-read, so any assignment is a
   guess; the constant's own comment (`config.py:53-60`) already argues it is decorative.
3. **`TEAM_SCOPED_CATEGORIES` (`config.py:66`) is asserted from outside the file and never imported.**
   Readers are `Config.get:991` and `Config.declared:1030`; the only other mention is
   `test_the_layering_lives_in_config_and_left_every_call_site_alone.py:192` reading it off the module.
   It belongs with the category table by meaning and with the refusal by use — two defensible homes.
4. **The `# ---` rule banners exist twice** (`config.py:68`, `config.py:97`). Everything after line 133
   is separated only by prose comments, so "walk the file's own section comments" is not a procedure
   past line 133 and the assignment of the version/stamp blocks rests on content, not on a marker.

## 2. The names that leave the file

54 import statements name `kubementat.core.config.config` from 53 files (12 in `src/`, 41 in `tests/`)
— re-measured by grepping the import line itself and confirming each hit is a real statement and not a
string: **55 statements across 54 files**, 12 of them in 12 `src/` files and 43 of them in 42 test
files, so the module-object import count is two higher than the table above the work list says. What a
split acts on is not the total but the subset below, and that subset reproduces exactly by either
method.
and pull 66 names. **19 of those statements, across 19 files, pull a name that moves** — 21 names,
6 of them in `src/`. This is the repointing work list; every one is a loud break (ImportError) the day
the name leaves.

| name | import sites | sites |
|---|---|---|
| `component_config_key` | 13 | `src/kubementat/automations/routing/traefik_presence.py:52`, `src/kubementat/config_answers.py:61`, `src/kubementat/helm_utils.py:13`, `tests/test_a_release_knob_nobody_declared_exports_nothing.py:31`, `tests/test_command_execution_policy.py:32`, `tests/test_component_namespaces_come_from_configuration.py:31`, `tests/test_every_release_the_manifest_names_carries_both_knobs.py:35`, `tests/test_grafana_is_not_installed_without_an_admin_user_in_the_config.py:25`, `tests/test_helmfile_apply_asks_only_on_a_terminal.py:26`, `tests/test_the_defaults_initialize_seeded_have_a_single_owner.py:44`, `tests/test_the_effective_configuration_says_which_layer_answers_each_key.py:51`, `tests/test_the_eight_former_environment_variables_arrive_only_through_the_layers.py:312`, `tests/test_the_ingress_class_the_manifest_names_is_the_one_the_configuration_answers.py:48` |
| `check_configuration_stamp` | 2 | `src/cli/context.py:49`, `tests/test_the_configuration_validator_is_strict_about_the_version_window.py:38` |
| `resolve_platform_config_dir` | 2 | `src/cli/commands/environments.py:29`, `src/cli/context.py:49` |
| `default_kubementat_main_dir` | 2 | `tests/test_config_path_resolution.py:36`, `tests/test_import_hygiene.py:22` |
| `ConfigurationStamp` | 1 | `src/kubementat/config_validate.py:62` (line also imports `Config`) |
| `_NOTHING_DECLARED` | 1 | `src/kubementat/config_answers.py:61` (line also imports `Config`, `component_config_key`) |

Nothing else moves and is imported: `read_configuration_stamp`, `satisfies_version_constraint`,
`product_version`, `_layered_view`, `_declare_in_layers`, `default_platform_config_dir`, the layer
constants, `_answers_from_a_team_layer`, `_version_parts`, `_compare_versions`,
`_PRODUCT_DISTRIBUTION`, `_CONSTRAINT_OPERATORS`, `SCHEMA_VERSION_WINDOW` and
`_directory_from_environment` reach no other module — they are reached off the module object or not
at all, which is exactly the class of thing that keeps passing (see section 3).

`Config` (44 imports) does not move, so the split is safe as long as `config.py` keeps a `Config`
name that resolves.

## 3. The patch-target strings

`git grep -n "kubementat.core.config.config" tests` → **52 lines**. Split by how they fail:

**Breaks silently — 38 sites (73%).** Attribute access *through* the module object, where the name is
read as `config.NAME`, `config_module.NAME`, `config_lib.NAME`, or patched by object-and-string-name:

- 33 reads/asserts of a moving name on the module object (`grep` for
  `\b(config|config_module|config_lib)\.<moving name>`): 9 `check_configuration_stamp`,
  6 `CATEGORY_LAYERS`, 4 `resolve_platform_config_dir`, 2 `ConfigurationStamp`,
  2 `satisfies_version_constraint`, 2 `TEAM_PAIR`, 2 `ENVIRONMENT_PAIR`, 1 each
  `component_config_key`, `read_configuration_stamp`, `product_version`, `_layered_view`,
  `TEAM_SCOPED_CATEGORIES`, `COMPONENT_LAYERS`. Concentrated in
  `tests/test_the_layering_lives_in_config_and_left_every_call_site_alone.py:175-211`,
  `tests/test_a_prepared_tree_says_which_product_it_was_made_for.py:61-165`,
  `tests/test_the_configuration_validator_is_strict_about_the_version_window.py:157`.
- 5 patches that name the target by object and string:
  `test_a_prepared_tree_says_which_product_it_was_made_for.py:86,97,105,165` and
  `test_the_configuration_validator_is_strict_about_the_version_window.py:172`, all
  `setattr(config, "product_version", ...)`.

  This is the failure the census exists to pre-empt. If `versions.py` is the home and `config.py` re-imports the
  name so these 5 sites keep resolving, the patch lands on `config.py`'s copy while
  `ConfigurationStamp.running_version` (`config.py:515`, moving to `stamp.py`) calls the name from
  `versions.py`'s globals. Every one of those 5 tests then asserts a warning that is no longer the
  one the code produces. Today they raise `AttributeError` if `product_version` is simply gone, so
  the *dangerous* state is a compatibility re-export, not a missing name.
- One further object patch: `tests/asset_paths.py:111` sets `config_module.asset_path`. `asset_path`
  is imported into `config.py:13` and is consumed by `Config.__init__`, which stays, so this one is
  inert under the split.

**Breaks loudly — 17 sites.**

- 54 import statements (the 52-line grep also counts 3 `run_python("import ...")` strings and 1
  docstring mention). Of the 55 statements the import line yields, 19 name a moving name (section 2)
  and 36 name only `Config` — verified with `git grep` over the import line and the six names that
  move, intersected.
- 3 of the 3 `run_python` import strings name this module:
  `tests/test_import_hygiene.py:43`, `tests/test_import_hygiene.py:60` (the second asserts
  `m.__file__` lands under `src/`), `tests/test_config_path_resolution.py:369`. All three keep
  passing because `config.py` survives as a module, so they test a dotted path and not a home.
  `tests/test_import_hygiene.py:49` asserts the *negative* (`lib.kubementat...`), also unaffected.
- 2 `(file, function)` path maps: `test_a_locked_configuration_file_refuses_by_name_and_not_by_codec.py:446`
  (`kubementat/core/config/config.py`, `_load_json_file`) and
  `test_the_eight_former_environment_variables_arrive_only_through_the_layers.py:202`
  (`src/kubementat/core/config/config.py`, `_directory_from_environment`). Both consume the path by
  collecting misses and asserting, so a moved function shows up as a failed assert rather than
  silence. Note the second map's row also names `KUBEMENTAT_HOME_ENV_VAR` and
  `KUBEMENTAT_PLATFORM_CONFIG_DIR_ENV_VAR`, so it is a *paths.py* row twice over.

- 0 `mock.patch("kubementat.core.config.config.<name>")` dotted-string patches exist anywhere in
  `tests/`. The failure mode this maps for does not arrive as a patch string; it arrives as the
  module-attribute reads above.

**Verdict on counts:** 38 silent vs 17 loud, and the 38 are all one pattern — a moving name read off
the module object. The repointing work is not string edits, it is deciding whether `config.py` gets
to keep forwarding names. If it does not, all 38 go red on the first run, and if it does, 5 of them
go green and wrong.

## 4. Private helpers with two homes

**2 strict, 3 counting one kind of orphan.** Decides cycles because it fixes the direction of every
edge between the five new modules.

- `_declare_in_layers` — lives in `keys.py`, called from `_layered_view` (`keys.py:224`) **and** from
  `Config._resolved` (`config.py:1084`). One function, both homes.
- `_NOTHING_DECLARED` — a sentinel defined in `keys.py`, compared inside `keys.py` twice (`188`,
  `225`) **and** inside `Config._release_knob` (`config.py:1224`) **and** in
  `src/kubementat/config_answers.py:61`. A sentinel compared with `is` must be one object, so the
  only correct move is one definition in `keys.py` and imports everywhere else; a `from
  .config import _NOTHING_DECLARED` shim recreates exactly the two-objects bug it would be a
  convenience for.
- `_layered_view` is the orphan, not a two-homed helper: it moves to `keys.py` and **every one of its
  callers stays** (`config.py:950`, `1044`). It calls `_declare_in_layers` and takes `path_of` as an
  argument (`config.py:1084` passes `self._config_path_of`), so the handoff is by callable and the
  dependency edge never needs `keys.py → config.py`.
- Single-home privates: `_answers_from_a_team_layer` (keys only), `_directory_from_environment` (paths
  only, and referenced by a *file-path string* in section 3), `_PRODUCT_DISTRIBUTION` and
  `_CONSTRAINT_OPERATORS` (versions only).

**Cycle verdict:** none of the five need a back-edge. `Config` → paths (2 calls in `__init__`), `Config`
→ keys (`_declare_in_layers`, `_layered_view`, the tables), `Config` → stamp (`ConfigurationStamp` is
not touched by `Config`), `stamp.py` → versions.py (`running_version:515`,
`version_is_outside_constraint:533`) and → `constants`. That is a DAG: config → {paths, keys, stamp}
→ versions → constants. The two places it could be inverted are `_NOTHING_DECLARED` (a re-export shim
would make `config.py` and `keys.py` each hold a sentinel) and `_config_path_of`, a `Config` method
that builds file paths (`config.py:1058-1071`) and is passed *into* `keys.py` as a callable — if a
follow-up "tidies" it into `paths.py`, `paths.py` then needs `Config`, and that is the cycle.

## 5. What cannot move

- **`Config` has no class-level attributes.** An AST walk of the class body finds only function
  definitions: every piece of state is instance state built in `__init__` (`config.py:659-769`) —
  `TEKTON_API_VERSION`/`TEKTON_API_GROUP`/`TRIGGERS_API_VERSION`/`TRIGGERS_API_GROUP` copied from the
  module constants, `environment`, `team`, `kubementat_main_dir`, `platform_config_dir`,
  `tekton_global_pipeline_run_dir`, `helmfile_asset_dir`, `tekton_task_asset_dir`,
  `tekton_pipeline_asset_dir`, `templates_dir`, `tekton_pipeline_template_dir`,
  `tekton_trigger_template_dir`, `env_static_config_path`, `env_static_encrypted_config_path`,
  `missing_team_config_files`, `config_sources`, `defaulted_config_keys`, and `config` (`769`). None of
  it can be hoisted, because two of these are derived from the arguments that were not handed.
- **The constructor's assumptions.** `__init__` (133 LOC) refuses when
  `resolve_platform_config_dir` names no root, so a `Config` never exists with an unstated root; it
  calls `default_kubementat_main_dir()` only when the argument is `None` (`config.py:683-685`); it builds
  `self.tekton_global_pipeline_run_dir` from `kubementat_main_dir` as *output* and the four other
  `tekton_ci/**` dirs from `asset_path`, which is an imported name and a patch target
  (section 3). Any of those three edges is `__init__`'s contract with `paths.py`, not a detail of it.
- **`__file__`-dependent behaviour: one line.** `default_kubementat_main_dir` returns
  `Path(__file__).resolve().parents[4]` (`config.py:262`). The `4` is a statement about this file's
  depth, and the constant's own comment says so. Moving the function into `paths.py` in the same
  directory keeps the answer identical; moving it anywhere else silently resolves one level off, and
  `tests/test_import_hygiene.py:100` asserts the value equals `REPO_ROOT` while
  `test_config_path_resolution.py:224` asserts it under a redirected `KUBEMENTAT_HOME` — neither
  fails when the arithmetic is wrong *and* the variable is set.
- **Import order.** `config.py:12-17` imports `derived`, `constants`, `asset_path`, `read_decrypted`
  and `TeamNotSuppliedError`; the split copies these into whichever new file needs them.
  `config_derived.RULES` is consumed inside `Config._resolved` (`config.py:1085`), not by the keys
  helpers, so `derived` is reached from the module that stays. `core/config/__init__.py` re-exports
  nothing on purpose, so a new module `paths.py`/`stamp.py`/`versions.py`/`keys.py` becomes
  importable only through its own dotted path — which is what makes the 38 module-attribute sites in
  section 3 meaningful.
- **Constants read by both a mover and a stayer.** `CATEGORY_LAYERS` (defined in keys, read by
  `Config._load_config:950` and `Config.declared:1044`), `ENVIRONMENT_PAIR` (read by
  `Config._component_layers:1385`), `COMPONENT_LAYERS` (same, `1385`), `_NOTHING_DECLARED` (both plus
  `config_answers.py`), and the four API constants (defined in config, read only by the constructor
  that stays). `SCHEMA_VERSION_WINDOW` is the exception worth naming: it is stamped by `stamp.py` and
  read only by `ConfigurationStamp.window`, so it moves cleanly with the window and has no stayer —
  but no test anywhere covers it, so its correct move is unproven.
