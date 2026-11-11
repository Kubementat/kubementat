# Specification: separating kubementat's code from its configuration

**Date:** 2026-11-02 · **Revision 2** (the 2026-10-30 document, revised against v2)
**Status:** specification for review. Not scheduled.
**Supersedes:** [`../research/specification-externalize-configuration-10-30-2026.md`](../research/specification-externalize-configuration-10-30-2026.md),
which stays in the tree as the record of what was decided on 2026-10-30 and of the measurements behind it.
**Background:** [`../research/research-config-code-separation-10-29-2026.md`](../research/research-config-code-separation-10-29-2026.md) —
the coupling survey. Design of this revision: layered configuration, delivered as a separate git
repository that uses git-crypt for its secrets — that choice is unchanged.
**Reconciles with:** [`cluster-config-v2.md`](cluster-config-v2.md) (v2, merged `08d513d`…`eca4c31`), which
changed four things this spec depends on and which changed one thing it said. §22 is that account; §22.1
is the review the 2026-10-30 text did not survive.

**Owner decisions carried forward from 2026-10-30, unchanged:**
layering not copying (§4); kubementat does not manage operator services (§7); overrides matched by name;
version drift warns and continues while `config validate` refuses it (§13.2); `kmt config init` performs
the whole git-crypt dance (§11.3); git-crypt leaves the product repository (§11).

**Decisions this revision adds or reverses**, each expanded where it is written:

| # | Decision | Where |
|---|---|---|
| N-1 | An environment is exactly one cluster, so the config repository's layout is that rule made visible: one directory per cluster, and the 1:1 check is a filesystem property of the config repo, not of a checkout | §5.1 |
| N-2 | The config repository names a cluster; it does not carry the kubeconfig that reaches it. `CLUSTER_KUBECONFIG_PATH` is machine-local and is not part of a shared config repo's truth | §6 |
| N-3 | In a Tekton pod, cluster resolution comes from the pipeline's own `--context`/`--kubeconfig`, never from the cloned config's `CLUSTER_KUBE_CONTEXT`, because v2's ordering makes the declared key beat the mounted service account and the file it names is not in the pod | §10.4 |
| N-4 | Multi-cluster is **not** out of scope. The 2026-10-30 text's §2.2 row is retracted; it was written two days before v2 made the model true | §2.2 |
| N-5 | `--config-dir` is a flag on every command, including `kmt environments list`, and the 1:1 check is scoped to exactly one config root per invocation | §8.3 |
| N-6 | Generated Tekton output moves to `tekton/triggers/<app>/` **and** `tekton/pipelines/` in the layer that owns it, tracked by git; the trigger move is a key rename with a migration entry, not a silent path change | §9 |
| N-7 | kubementat reads configuration, it never unlocks it (unchanged), and the locked-file seam now covers the cluster reader too | §11.7 |
| N-8 | **One command creates the whole directory, git-crypt included, and it is the only thing that materializes configuration** — humans, CI and the end-to-end suite all call it | §15 |
| N-9 | **The end-to-end suite is a consumer of `kmt config`, not a writer of configuration.** Its config comes from `config init` and `config set`; the jq edits and the marker-deletion the harness does today go away | §16 |
| N-10 | **`kmt config import` is withdrawn, together with §14's migration requirements.** A combined tree is *addressable*, not *migratable*: `--config-dir` at a `platform_config` is the whole path for an existing tree, and a tree that wants §5's layout is rebuilt with `config init` + `config set`. No command moves an operator's tree (owner, 2026-11-05) | §14 |

---

## 1. Problem and goal

One checkout is still the product, the product's assets, and the operator's cluster configuration. v2
changed *which cluster* a run means; it did not change *where the configuration lives*, and the two
questions are now tangled in a new way: `platform_config/<environment>/static.json` is simultaneously the
operator's answer about namespaces and the declaration of which cluster the 1:1 check compares (§5.1).

Measured at v2 HEAD (`e9968c2`, 2026-11-02), all four still true:

| Coupling | Where it lives today |
|---|---|
| The config root is a subdirectory of the product unless a variable says otherwise | `config.py:87-105` (`default_platform_config_dir`), read at `config.py:218` |
| The distribution ships no assets | `unzip -l dist/kubementat-0.4.0-py3-none-any.whl` → **37 files**, all `.py` + `dist-info`, `templates/` 19 files and `tekton_ci/{tasks,pipelines}` 29 files are not in it |
| The product still initializes itself in the checkout | `automation.py:388` (`uv sync --all-extras` in `kubementat_main_dir`), `automation.py:735`/`:753` (`git-crypt init` / `export-key` in `kubementat_main_dir`) |
| The product still writes operator-editable product shape | `initialize` copies its template roots and writes four `static*.json`; the write set is derived in `automation.py:_initialization_write_targets` |

**Goal.** kubementat's code and assets evolve without touching anyone's tree, and an operator's cluster
stays current by changing one line and reading `config validate`'s verdict.

**Test that decides the whole spec (TD-1).** A cluster configured against kubementat `N` is upgraded to
`N+1` by editing **one line** in `<config>/kubementat.yaml`, running `kmt config validate` and
`kmt install dev` — with no file copied from the product, no merge, and no file in the configuration
directory that kubementat generated for someone to edit. **This test lost its measurement on
2026-11-05** and still states the goal: `config diff` was deleted with the verb group it served, and no
other command says what a bump would cost. `validate` says only whether the moved tree survives. What is
measured is the second half — `config init` builds a tree that installs with nothing to merge (phase 3b,
§16.2) — and the `N` → `N+1` transition is unmeasured until an instrument for it exists.

**TD-2 is this revision's addition, and it comes from v2.** A config repository that describes two
clusters is a machine with two `platform_config` roots' claims *inside one directory*: `kmt install dev`
and `kmt install prod`, run from the same config root on two different machines, each name the cluster it
chose, and the collision check compares every environment **in that root** and refuses a duplicate claim.
It must not need a second checkout, and it must not resolve the other environment's kubeconfig to do it —
which is exactly what the environment enumeration of §5.1 and the resolution order of v2 §4.2 already make
possible.

---

## 2. Scope

### 2.1 In scope

The layering model and precedence (§3, §4); the config repository layout and file semantics (§5); how
cluster addressing survives the split (§6); the `kmt config` verb group and `--config-dir` (§7, §8);
git-crypt moved out of the product (§11); operator-owned Tekton objects (§9); the version contract
(§13); product packaging of assets (§12); CI/Tekton against two sources (§10);
that existing combined trees remain usable by being pointed at (§14); **the bootstrap workflow that creates the directory, and the
end-to-end suite as its first machine consumer (§15, §16)**.

### 2.2 Out of scope (explicit)

* Flux/ArgoCD or any in-cluster GitOps controller. The value in that idea — an automated commit keeps
  config current, not a human — arrives as release-pipeline bump PRs (§13.4).
* SOPS, age, Sealed Secrets, or a secret-provider interface. git-crypt is v1's mechanism; §11.5 records
  the one seam a provider would go into.
* The operator's own deployment mechanism (§7).
* Deleting Tekton as the CI engine.
* Any backwards-compatibility alias, shim, or deprecation branch. Where behaviour changes, this spec
  changes it and the CHANGELOG records it.
* ~~Multi-cluster / cluster-context support~~ — **retracted 2026-11-02** (N-4). v2 shipped it
  (`cluster-config-v2.md`), and this spec's whole model of "an environment is a directory" depends on it.
  The config repository is where v2's 1:1 rule stops being a convention.

---

## 3. Architecture: three layers, one precedence order

```
L0  product layer        installed kubementat (wheel + CI image)
                         defaults in constants.py, packaged templates/, tekton_ci/, helm_charts/
        ↓ overridden by
L1  environment layer    <config>/<environment>/…
        ↓ overridden by
L2  team layer           <config>/<environment>/<team>/…
```

**R-3.1 (precedence).** For every configuration answer, `L2 > L1 > L0`; a key absent at `L2`/`L1` resolves
from the product default. This is today's `Config._component_value` rule (`config.py:522`, warn-once,
measured) promoted from namespaces to every configuration surface, including values files and Tekton
manifests.
*Done when:* a test seeds the same key at all three layers and asserts the team value wins, and a second
test deletes it from `L1`/`L2` and asserts the product default answers with one warning.

**R-3.2 (no product shape in the configuration).** kubementat writes no product-shaped file into the
configuration. `initialize`'s template copy goes away (§7.1) and what remains is: the layout of §5 plus the
operator's answers as data. One named exception, so it cannot spread: §9's generated Tekton manifests land
in the layer's `tekton/` directory and are tracked by git.
*Done when:* `kmt config init` in an empty directory writes the layout of §5 and `kmt config validate`
reports **zero** files that duplicate a product asset, and a second `kmt config init` writes nothing.

**R-3.3 (answers are data).** Every operator answer is JSON or YAML values. The `REPLACEME_*` pattern
goes away: kubementat asks for what it needs and writes the answer.
*Measured so the rule has a size:* **31** `REPLACEME_*` placeholders across **4** template files
(`static.json.template` 8, `team/static.encrypted.json.template` 15, `static.encrypted.json.template` 4,
`mirrored_docker_images.json.template` 4). `automation.py` substitutes only three of them
(`MYSQL_HOST`, `REDIS_MASTER_HOST`, `SSH_DEPLOY_KEYS[0].TARGET_NAMESPACE`, in
`_write_team_static_encrypted_json`); the other 28 are written into an operator's tree verbatim, which is
the mechanism R-3.3 exists to delete.
*Done when:* no `REPLACEME_` string appears in a file kubementat writes, and a missing required answer is a
refusal that names the key and the command that sets it.

**R-3.4 (one root, one resolution, no default).** The configuration root resolves exactly once per run:
`--config-dir` → `KUBEMENTAT_PLATFORM_CONFIG_DIR` → **error**. `kubementat_main_dir` is never consulted.
`kubementat.yaml` cannot be the fallback because it lives *inside* the directory it would have to name.
This is v2's rule about clusters applied to the tree: kmt does not guess what a command means, and a
directory located by proximity is the D11 failure class. Today's `default_platform_config_dir`
(`config.py:87-105`) is the function that changes shape: it keeps the variable's precedence and loses its
third term.
*Done when:* a test covers the argument, the variable, the refusal with neither, and the set-but-empty
case (same rule as `_directory_from_environment`, `config.py:51`).

**R-3.5 (some answers are rules, not preferences).** Three answers are neither secrets the operator
supplies nor defaults kubementat ships; they are consequences of which team is being talked about, and
they used to be produced by substituting the team's name into a `REPLACEME_TEAM` placeholder in a template
(`automation.py:451-453`, `:472-477`):

| Answer | Its rule |
|---|---|
| `APP_DEPLOYMENT_NAMESPACE` | the team's own name |
| `PIPELINE_NAMESPACE` | `<team>-pipelines` |
| `MYSQL_HOST` | `mysql-<team>` |
| `REDIS_MASTER_HOST` | `redis-<team>` |
| `SSH_DEPLOY_KEYS[0].TARGET_NAMESPACE` | the team's own name |

*Owner ruling 2026-11-03:* **kubementat derives them.** They are convention, and a convention an operator
has to retype is strictly worse than one the code applies: the value is 100% predictable from an answer the
operator already gave, so asking for it adds a failure mode and no freedom. What this means concretely is
that the derivation is a **function of the team name that already exists in code and resolves at read time
as `L0` (R-3.1)**, and it is asserted by a test per key — a rule with no test is the same rule that
`REPLACEME_TEAM` was, wearing a better name.

The distinction this rule is careful about: **derived is not generated.** `MYSQL_ROOT_PASSWORD` is generated
because two correct answers differ and nobody can tell which (`config set --generate`, R-11.8). This is the
opposite: `PIPELINE_NAMESPACE` has exactly one correct answer, so generating it would be inventing something
the tree already knows. The line is stated because the two live in the same code path, and the sentence
"kubementat fills in the value" means something different in each case.
*Done when:* each of the five resolves for a `config init`-created directory that answered nothing but the
team name, no template contains `REPLACEME_TEAM` any more, and deleting `initialize` changes what a reader
of a team's `static.json` sees: the key may be absent from the file and still answered.

**And a second line, because the first one was not enough.** Owner ruling, 2026-11-03, after the derivation
landed and changed what `kmt uninstall` deleted: **derive for placement, delete only what a file named.**
The same rule that says where `pandas` team's pipelines live must not be read as permission to remove that
namespace, because a rule produces an **address** and not a **deed** — a cluster can already hold a namespace
called `pandas` that this configuration never created, and deleting it because a directory had that name is
the one failure in this epic that is not recoverable by editing a file. So `Config` answers through two
doors: `get()` for "where does this go", `declared()` for "what does the file say", and the destructive
paths are required to ask the second question. `namespace_sweep` deletes `entry["deletable"]` and uses
`entry["namespaces"]` only to say what it left alone; `uninstall_pipelines` refuses when
`PIPELINE_NAMESPACE` is answered only by the rule. The log has to carry the difference too, because
"answered" and "swept" are different verbs and an operator reads one of them as "the cluster is clean".
*Done when:* a team directory that declares no namespace contributes nothing to a sweep and is named in the
summary as declined rather than empty, `Config.get` and `Config.declared` return the same thing for
anything a file actually wrote, and no destructive call site obtains a namespace from the derived answer.

---

## 4. Layering semantics per surface

**R-4.1 (JSON).** Shallow key-by-key merge, `L2 > L1 > L0`. `L0` is `constants.py` plus the packaged
`defaults/static.json` (today's `templates/environment/static.json.template`, repositioned as a default
rather than a template to copy).
*Done when:* `kmt config show` tags each key `default` / `environment` / `team`.

**R-4.2 (helm values).** A release's values list is `[<product default>, <environment override>,
<team override>]` with `missingFileHandler: Warn` on the override entries. **The three knobs and no
fourth:** values (this file), `components.<name>.enabled`, `components.<name>.version`. No operator ever
copies kubementat's helmfile, so no product helmfile ever lands in the configuration and there is no
`--template`.
*Done when:* an environment with zero value files applies the product defaults; an override key wins;
`enabled: false` removes the release; `config validate` refuses a `helmfile*.gotmpl` inside the config.

**R-4.3 (Tekton manifests, by name).** The product applies `<product>/tekton_ci/{tasks,pipelines}`, then
`<config>/<environment>/tekton/{tasks,pipelines,triggers}`, then the same under
`<config>/<environment>/<team>/`. Same `metadata.name` replaces; a new name adds. `apply_resources` keeps
skipping directories named `templates/` (generator input, never applied). Today's environment layer of
`tekton/` does not exist (`config.py` builds only team-scoped `tekton/` paths); both halves of a name
match have to exist for precedence to mean anything.
*Done when:* a product Task plus a team Task of the same name resolves to the team one, and the
environment version loses to the team version.

**R-4.4 (the product's helmfile is the entry point).** `HelmUtils.helmfile_paths()`
(`helm_utils.py:289-307`, today returning the operator's copy) points at the packaged manifest; the
configuration contributes values and the §4.2 per-release overrides. `component_release_index` keeps
reading whatever manifest is actually applied.
*Why load-bearing:* this is the shape of v2 §11's "which file did this apply from" question, and plan row
13's stale-copy bug cannot be constructed once the entry point is product-owned.

**R-4.5 (values live in the layer, not beside it).** `values/<component>.yaml` and
`values.encrypted/<component>.yaml` in both layers. Encrypted values keep the `*.encrypted.*` filter.
*Done when:* a component's value list is ordered default → environment → team, and an encrypted
override is applied without anyone exporting anything.

---

## 5. Configuration repository layout

```
<config>/
  kubementat.yaml                      # exactly two keys: schema_version, kubementat (§13.1)
  .gitattributes                       # written by init: *.encrypted.* filter=git-crypt diff=git-crypt
  .gitignore                           # git-crypt working material, helm artifacts, deployer keys
  <environment>/                       # one directory == exactly one cluster (N-1)
      static.json                      # namespace/domain/registry/cluster keys, incl. CLUSTER_KUBE_CONTEXT
      static.encrypted.json            # environment credentials — git-crypt protected
      values/<component>.yaml          # optional overrides (§4.2)
      values.encrypted/<component>.yaml
      tekton/{tasks,pipelines,triggers,pipeline-runs}/
      <team>/                          # a directory that carries its own static.json
          static.json
          static.encrypted.json
          <app>/values.encrypted.yaml  # the operator's app values — unchanged
          tekton/{tasks,pipelines,triggers,pipeline-runs}/
  secrets/                             # only what is not a component answer (§11)
  deployer_ssh_key{,.pub}              # §11.4, config material, not product
  gpg_private.key / gpg_public_key.gpg
  git_crypt_symmetric.key              # exported, gitignored, never read back
```

**R-5.1 (the layout is v2's 1:1 rule made visible).** An environment is a directory at depth 1 of the
config root that carries `static.json`; a team is a directory inside it that carries `static.json`. That
is `EnvironmentRegistry.environments()` (`cluster.py:682-697`) and `Config.teams()` (`config.py:476-510`)
applied one directory higher than today — same rule, same enumeration, no registry file. The root's own
names are therefore reserved (`kubementat.yaml`, `secrets/`, §11.4's key files) and an environment that
wants to be called `secrets` is a refusal.
*Done when:* `kmt environments list --config-dir <dir>` prints every environment in the root and the
cluster each declares, without contacting a cluster (v2's TD-2, which needs no new mechanism), and the
collision check's scope is one config root (N-5).

**R-5.2 (names survive).** `environment` and `team` stay the CLI arguments; `static.json` /
`static.encrypted.json` keep their names. The rename is the root, not the files, so an existing
`static.json` answers with the same keys under the new root.

**R-5.3 (the config is a git repo).** `kmt config init` requires git (git-crypt is a git filter).
A read-only working tree handed to kubementat need not be a checkout (§11.7).
*Done when:* `config init` refuses without git and names `git init`; `kmt install` against a plain
directory of files works.

**R-5.4 (one manifest, two keys, nowhere else).** `kubementat.yaml` exists once at the root and holds
exactly `schema_version` and `kubementat`. No `environments:`, no `teams:`, no secrets provider, no
per-environment manifest: a second answer to "which kubementat must this be" is what R-3.4 forbids for the
root and v2's registry rule forbids for the team list. Environments and teams come from the filesystem.
*Done when:* `config validate` errors on `environments:` or `teams:` inside `kubementat.yaml` as keys
kubementat does not know, and no second `kubementat.yaml` exists anywhere in a config tree.

---

## 6. How cluster addressing survives the split (new in this revision)

v2 made a cluster a value that is resolved before the first write, from `CLUSTER_KUBE_CONTEXT` (+ optional
`CLUSTER_KUBECONFIG_PATH`) in the environment's `static.json`. A config repository is shared between
machines; a kubeconfig path is not. So:

**R-6.1 (the config repo declares the cluster, the machine declares how to reach it).**
`CLUSTER_KUBE_CONTEXT` is a declaration and belongs in the config repo. `CLUSTER_KUBECONFIG_PATH` is
machine-local and is **not** part of a shared config repo's truth: writing `/home/verfeinerer/.kube/config`
into a file three people clone is a claim that breaks on two of them. The machine-local dials are v2's
existing layers 1 and 2 — `--kubeconfig` and the `CLUSTER_KUBECONFIG_PATH` process variable.
*Done when:* a config repo whose `CLUSTER_KUBECONFIG_PATH` is `null` runs on a machine that supplies the
path by either dial, and `kmt config validate` warns (does not refuse) when a config repo that declares
more than one environment also hard-codes an absolute `CLUSTER_KUBECONFIG_PATH` — that combination is
almost certainly the mistake of one machine written into shared history.
This is v2 §17 Q3, answered within this spec's subject: credential material lives in the kubeconfig, not
in the config repo; §11.6 says what to do if an operator puts it in the repo anyway.

**R-6.2 (the check is a property of the config root).** Two environments that address one cluster are a
hard block (§1 of v2, no override flag). Under separation that comparison becomes *more* useful, not less:
the environments in question are now obviously one operator's tree rather than one machine's checkout
history. `EnvironmentRegistry` takes one root; a run resolves that root (N-5) and compares the claims in
it.
*Done when:* a test runs two environments that reach one address out of one config root and asserts the
refusal names both, and `kmt environments list` on the same root prints both claims side by side — the
command and the guard read the same enumeration.
**What this does not buy:** v2 §17 Q5 is still open and this spec does not close it — two *different*
config roots that name one cluster are two claims nothing compares. Say so in the CHANGELOG row; do not
let the split be described as closing it.

**R-6.3 (`kmt config init` cannot name the cluster for you).** Cluster identity is never guessed by
kubementat, so `init` writes `"CLUSTER_KUBE_CONTEXT": null` — the decision v2 §4.4 made for `initialize`,
moved to the command that creates configuration — and says, on stderr, that the environment exists but
addresses nothing, which file to edit, and that `kmt environments list` shows the current state.
*Done when:* `kmt config init` on a fresh tree leaves both keys `null` and prints that sentence, and the
first command that needs a cluster refuses with v2's §4.3 failure text.

---

## 7. CLI surface

New group, registered like `kmt routing` / `kmt users`:

| command | contract |
|---|---|
| `kmt config init <dir> [--environment E --team T] [--set KEY=VALUE …] [--encrypted-set …] [--git-url U] [--gpg-user E] [--no-commit]` | Creates §5's layout, runs git-crypt §11.3, generates keys §11.4, writes `kubementat.yaml`, leaves both `CLUSTER_*` keys `null` (R-6.3). Refuses a non-empty dir without `--force`; never overwrites a file it did not write. **The full contract, including the answers and the ready-to-use checklist, is §15.** |
| `kmt config add-environment <dir> E` | A new cluster's layer. No product templates. |
| `kmt config add-team <dir> E TEAM` | Team layer. |
| `kmt config set <dir> E KEY=VALUE [--team T] [--encrypted] [--from-file PATH]` | Writes one answer to the right layer. Replaces "export eight variables and re-run initialize". Its sibling spelling `config set <dir> E --generate KEY [--team T]` (R-11.8) invents the value when the key answers nothing and refuses when it already answers. `--from-file` answers every key of a JSON object in one run, and it is on this surface for the same reason the verb exists at all: a value that is too long to paste, or a credential the operator does not want in the process table, cannot be a command-line argument. The document chooses no layer and no file — `--team` and `--encrypted` still do — and it is decided whole before the first byte, so a document that answers one key with `null` is refused along with every key in it and the tree is not left in a state nobody wrote. |
| `kmt config unset <dir> E KEY [--team T]` | Removes one answer from that layer so the next one up answers it (`L2 > L1 > L0`, R-3.1). This is the only way to express "back to kubementat's default", which is otherwise an edit of JSON, and it is what the lane needs when it puts a key back into its not-yet-named state (§16.1). |
| `kmt config show <dir> E [TEAM]` | Effective config, each key tagged `default`/`environment`/`team`. Read-only. |
| `kmt config validate <dir> E [TEAM]` | Schema, unresolved `SETME` values, git-crypt unlocked or not, layering cycles, duplicate names across layers, absolute `CLUSTER_KUBECONFIG_PATH` in a shared tree (R-6.1). |

The group is **seven verbs** and the table above is all of them: `init`, `add-environment`, `add-team`,
`set`, `unset`, `show`, `validate`. **`config teams` was deleted here on 2026-11-05 by owner decision.** It
was never implemented and never commissioned; `Config.teams()` stays, because `kmt uninstall` sweeps through
it, and that is where the answer lives. A team list read off a directory listing is the human's way, and a
verb for it was judged not worth its surface.

Everything a reader needed from the deleted `explain` is in `show`'s line: `show` already tags every key
with the layer and names the file that declared it, which is the same two facts, printed for the whole tree
instead of one key. **Deleted 2026-11-05 by owner instruction:** `explain` (its answer and its provenance
moved to `show`; the one fact with no surviving home is the list of files precedence *consulted*, which
nothing prints any more), `diff` (nothing says what a version bump would cost — §13), and `migrate` (the
rename table is deleted, so kubementat records no renames and applies no migration — R-13.3). None of the
three is deprecated or hidden behind a flag; all three were deleted, and their absence is the surface.

**Three verbs that were considered and declined**, because a surface is defined by what is not in it: `config unlock` (kubementat never unlocks, R-11.7; a locked tree is validate's problem, §11.7.1),
`config secrets` (what exists and what is missing is `validate`'s job and R-11.8's refusal — a second verb
for the same answer is how two answers appear), and `config generate` (§9's generation already has
`kmt tekton-generate-*`; a config-side alias would be a second name for one mechanism).

Every existing `kmt` command gains `--config-dir`, with no default (R-3.4). Where v2 dropped `--team` from
eight commands, `kmt config` does not put it back: `config set --team` writes to a layer, it does not
pretend the command underneath needs one.

**R-7.1 (`kmt initialize` goes away).** Configuration creation is `config init`; cluster installation is
`kmt install`. `initialize`'s remaining work — dependency check (`KUBEMENTAT_DEPENDENCIES`,
`automation.py`), key generation, template copy, git-crypt, the eight-variable requirement check, and the
stderr sentence about `CLUSTER_KUBE_CONTEXT` — is redistributed across §7/§11/§12. It is deleted, not
deprecated.
*Done when:* `kmt initialize` is unknown to the CLI, the CHANGELOG names where each of its write targets
went, and no test invokes it.

**R-7.2 (the eight environment variables go away).** `KUBEMENTAT_ENVIRONMENT_CONFIGURATION`
(`automation.py:244-…, 8 keys: BASE_DOMAIN`, `AUTOMATION_GIT_URL`, `AUTOMATION_GIT_SERVER_HOST`,
`AUTOMATION_GIT_SERVER_PORT`, `AUTOMATION_GIT_SERVER_SSH_USER`, `KUBERNETES_DEFAULT_STORAGE_CLASS`,
`DOCKER_REGISTRY_BASE_URL`, `CLUSTER_MANAGER_EMAIL`) becomes configuration read from
`<environment>/static.json`. The `CLUSTER_*` keys stay out of that dictionary (v2 §4.4's decision, still
correct: requiring a cluster when a config tree is created makes `init` the strictest command for the
least benefit) and they stay out of the variable list too.
*Done when:* `_check_required_environment_variables` is gone and a missing answer arrives as one refusal
naming the key, the file, and the command that sets it.

**R-7.3 (git-crypt is a dependency of the verbs that exec it).** The guard is per-verb: `config init`
checks `git-crypt` because it runs it; `install` and every read path do not run it (§11.7)
and therefore need a readable directory, not the binary.

---

## 8. The `--config-dir` contract

`KUBEMENTAT_PLATFORM_CONFIG_DIR` exists (`config.py:36`) and wins over `<main_dir>/platform_config`
(`config.py:87-105`, read at `:218`). It is not honoured everywhere it matters:

* `initialize` runs `uv sync --all-extras` in the product directory (`automation.py:388`).
* `initialize` runs `git-crypt init`/`export-key` in the product directory (`automation.py:735`, `:753`).
* The Tekton deploy task reads config from a path built out of the cloned fork
  (`tekton_ci/tasks/deploy-local-helm-chart.yml:23-28`, default
  `$(workspaces.workspace.path)/kubementat`).
* `kmt environments list` builds its registry through `resolve_platform_config_dir`
  (`src/cli/commands/environments.py:65`), which is the one rule the flag and the variable share: it has
  carried `--config-dir` since T-10, so a listing can be pointed at a tree, and when neither the flag
  nor `KUBEMENTAT_PLATFORM_CONFIG_DIR` names one it refuses rather than answering from wherever the
  wheel happens to sit.

**R-8.1.** No code path in `src/` derives a configuration path from `kubementat_main_dir`.
*Done when:* no `src/` path reads configuration out of `kubementat_main_dir`, proven the way every
root rule is proven now — a `kmt install` driven at a named configuration root that holds no
environment refuses and never reaches a cluster, and a handed root is not outvoted by
`KUBEMENTAT_HOME`.
The original bar for this was `grep -rn "kubementat_main_dir.*platform_config" src/` empty
(amended 2026-11-02, owner: state the rule, not the search). It returns exactly one line today, and
that line is `Config.__init__`'s parameter list naming both roots as *arguments* — which is the
threading R-8.2 requires, not a path derivation. Emptying the grep would mean deleting an
operator-facing sentence from `version.py` or reformatting a signature to fool a search, so the rule
is checked by the tests above and the grep is a known false positive.

**R-8.2 (the contract is mandatory, not optional).** One of `--config-dir` or the variable must name the
configuration for every command that reads it. This is the phase-1 behaviour break, stated plainly: today
`kmt install dev dev1` inside a checkout answers from where the checkout sits; after this it refuses. One
line of environment or one flag is the fix, it belongs in the CHANGELOG, and it gets no shim.
*Done when:* the refusal test exists and a `kmt install` that resolves nothing never reaches `kubectl`.

**R-8.3 (the flag reaches the registry).** `--config-dir` is threaded to `EnvironmentRegistry`, so the
1:1 check, the collision report, `kmt environments list`, and every write all look at the same root.
Today's `environments.py:51` reads the env var, so a `--config-dir`-only run would list the wrong tree —
that is the D11 failure class wearing a listing command.
*Done when:* a test points the flag at root A while the variable points at root B and asserts both the
listing and the collision check used root A.

---

## 9. Operator-owned Tekton objects

* `<config>/<environment>/tekton/{tasks,pipelines,triggers,pipeline-runs}/` is the cluster's half;
  `<config>/<environment>/<team>/tekton/…` the team's half. Both layers exist so a name match has
  somewhere to lose (§4.3).
* Custom PipelineRuns live in the config, not `tekton_ci/pipeline-runs`; the product's `examples/` stay
  product-side.
* **Generated output is written into the layer's `tekton/` directory and is tracked by git.**
  `kmt tekton-generate-deploy-pipeline` → `<team>/tekton/pipelines/deploy-pipeline-<app>.yml`;
  `kmt tekton-generate-triggers` → `<team>/tekton/triggers/<app>/` (binding, trigger template, event
  listener, event listener ingress); `kmt tekton-generate-pipeline-run` →
  `<layer>/tekton/pipeline-runs/<pipeline>-run.yml`. Generation and reading stay in one layer — that is
  the FIN-22/23 requirement, and §13.3 records no table that could carry the entry any more.
  **N-6's honesty:** today's triggers are written to `<team>/triggers/`
  (`config.py:307-309`, `automation.py:496`), not under `tekton/`. Moving them is a path rename, not a
  file-layout preference: a config that keeps the old directory must be refused by name rather than
  silently applying nothing. That refusal is the whole of the safety now, because nothing rewrites the
  operator's tree to find the new location — kubementat states the rename and stops.
* Generated manifests count as **generated**, separately from operator edits: a bump that touches a
  generator template is "these regenerate", not "the cluster changed". What no longer exists is a command
  that reports the distinction before the run; the distinction itself is still the rule.
* kubementat applies what it ships and does **not** prune operator objects it no longer ships.
*Done when:* a lane that runs `kmt tekton-setup-pipelines` reports both a product Task and the operator's
own by name; the unit suite proves name-match precedence without a cluster; `git status` in a config repo
after a full generation is clean except for what the operator changed.

---

## 10. CI / Tekton: two sources instead of one fork

Today a pipeline clones `AUTOMATION_GIT_URL` — the operator's fork of the product — and reads code and
configuration from one workspace.

```
source A: product  = the pinned kubementat image (§12.3)
source B: config   = git clone of the config repo, ref = the commit being deployed
workspace: /workspace/config   ← --config-dir
           kubementat binary + assets, from the image
```

**R-10.1.** `deploy-local-helm-chart.yml`'s `path-to-configuration` default becomes the cloned config
workspace, not `<clone>/kubementat`, and every `kmt` invocation in the TaskRun passes `--config-dir`.
**R-10.2.** The `AUTOMATION_GIT_*` keys stop describing where the *product* lives:
`AUTOMATION_GIT_URL`, `AUTOMATION_GIT_SERVER_HOST/PORT/SSH_USER`, `AUTOMATION_GIT_REVISION`,
`AUTOMATION_GIT_PROJECT_NAME` describe the **config** repository; `GIT_DEPLOYER_PUBLIC_KEY` /
`GIT_DEPLOYER_GPG_PUBLIC_KEY` are §11.4's deployer keys.
**R-10.3.** Unlocking is not kubementat's job (§11.7): a step after `git-clone` takes the symmetric key
from a Kubernetes Secret via `secretKeyRef` and pipes it into `git-crypt unlock -`; every `kmt` step after
that reads a directory.

**R-10.4 (N-3: how a pod knows which cluster it is).** v2's resolution order puts `static.json`
(layer 3) **ahead of** the mounted service account (layer 4), and layer 3 fails rather than falling
through: `ClusterTarget.from_kubeconfig` refuses when the kubeconfig file it resolved does not exist
(`cluster.py:177-199`), so a pod that cloned a config repo declaring `CLUSTER_KUBE_CONTEXT` errors out
instead of quietly using its own credentials. The rule, therefore, is not "the pod resolves in-cluster";
it is:

* the pipeline **states** the cluster with v2's layer-1 dials (`--kubeconfig`, and `CLUSTER_KUBECONFIG_PATH`
  or `--context` on the TaskRun's `kmt` step), so the answer that wins is the one the pipeline declared;
  and
* the clone's declared context is checked against that statement, and a mismatch is a refusal that names
  both. The config repo's claim is a claim about the environment; the TaskRun's claim is about where the
  work runs. When they disagree, kubementat must not be the thing that decides.

Doing this in the CI layer keeps v2's ordering untouched. The alternative — teaching layer 3 to fall
through when its file is unreadable — is the D11 failure class and is refused here explicitly: a
configuration that cannot find the cluster it declares does not get to discover a different one.
*Done when:* a pipeline run proves product and config arrive from separate sources, every `kmt` invocation
names its cluster in the resolution report, and no `kmt` invocation in the pipeline execs `git-crypt`.

---

## 11. git-crypt, moved

**R-11.1 (what leaves the product).** The `.gitattributes` line `*.encrypted.* filter=git-crypt` leaves the
product repository, keeping `*.template !filter !diff` so product templates stay plaintext and reviewable.
`crypto_utils.py`'s keyring/keystore handling stays only for §11.4.
*Measured change since 2026-10-30:* `.git-crypt/` is **no longer tracked** (`git ls-files .git-crypt` →
empty; it left in `c9d2592`, "git-crypt stops committing into the checkout it initializes"), so the old
§8.1 row "18 tracked files" is history.
*Owner ruling 2026-11-03, which is what this requirement was aiming at:* **the product ships no encrypted
files at all**, and the only git-crypt keystore that exists anywhere is the one `config init` creates inside
a generated configuration directory. Two consequences, both measured rather than inferred. The ten
`src/kubementat/assets/values/**/*values.encrypted.yaml` files are **default values files and are renamed**
out of the `*.encrypted.*` pattern: census K found them flagged by `git crypt status` as "staged/committed
version is NOT ENCRYPTED", and reading them found no secrets, only placeholders and comments — the
credentials arrive from `platform_config` through the environment — so this names them correctly and moves
nothing. (Their names also said something false about the tree: a reader could not tell a product default
from the operator's encrypted layer, which shares the `values.encrypted` string in its path.) And the
product's root `.gitattributes` is **deleted**, not edited: with nothing encrypted in the product, the
filter line has nothing to declare and the `*.template` exception exists only to opt out of a filter that
is no longer declared.
The remaining git-crypt calls in `kubementat_main_dir` go with `kmt initialize`: census K counted
**three**, all of them inside `automation._configure_git_crypt` (`automation.py:730` `init`, `:744`
`add-gpg-user --no-commit`, `:748` `export-key`), against the spec's older "two calls" — the same
name-versus-exec confusion R-11.5 had, caught again by counting argv instead of reading about it.
*Done when:* the product repo is usable by someone who has never heard of git-crypt, and no `src/` path
execs it against `kubementat_main_dir`.

**R-11.2 (what init writes into the config).** `.gitattributes` (the filter + diff rules) and `.gitignore`
(`deployer_ssh_key*`, `gpg_*`, `git_crypt_symmetric.key`, `**/.helmfile/`). No `*.kmt-template` rule, no
`--template`: kubementat puts no template of itself into the configuration (§4.2's three knobs), and an
option kept "in case it is needed" is the door plan row 13's stale-copy bug walks back in.

**R-11.3 (the dance, complete, in `init`).** The configuration directory **is a git repository**: git-crypt
cannot exist without one, and the ruling put all of git-crypt here — so `git init` is step one and not a
precondition the operator was expected to have met. Nothing in `src/` assumed this before 2026-11-03; config
trees were explicitly not required to be repositories, which is precisely why an encrypted file inside a
config tree could not have been encrypted by a checkout filter.
Idempotent, each step skipped with a one-line reason when
already true: `git init` → `git-crypt init` → keys (§11.4) → `git-crypt add-gpg-user <email>` (and one per
`--gpg-user`) → `git-crypt export-key <config>/git_crypt_symmetric.key`, chmod 0600, gitignored, printed
once with the sentence that this file is the ability to read every credential in the cluster →
`.gitattributes` + `.gitignore` written **before** any encrypted file exists, so the first `git add`
encrypts → credentials only kubementat can decide (§11.8) written encrypted → commit `chore: initialize
kubementat configuration` (unless `--no-commit`) → summary (file list, then push to `--git-url`, then add
the deployer key to the git host, then `kmt install`).
Exit codes: 0 on success or "nothing to do"; 1 with a named path when a step's precondition fails.
*Done when:* init twice writes nothing the second time and says which steps it skipped; a file written
before `.gitattributes` is *not* encrypted; init twice leaves a generated credential unchanged.

**R-11.4 (keys belong to the configuration).** The deployer SSH and GPG keypairs are generated into the
**config** directory, not `kubementat_main_dir` (today: `automation.py` generates them into the checkout,
which is how cluster credentials ended up inside the product tree). CI still needs them: the pipeline
clones the config repository over SSH, and git-crypt needs a GPG identity.
*Owner ruling 2026-11-03:* key generation is **automated**, not left to the operator — a `config init` on a
brand-new directory invents the deployer's identity itself — and an already-existing GPG key may be supplied
with a flag instead. The second half matters more than it sounds: a `config init` that can only generate
will block forever on `gpg --gen-key` waiting for a passphrase, and a `config init` that can only accept an
imported key is useless to the first run of a brand-new configuration. Both paths therefore exist, and
generation is the default.
*Done when:* `config init` on a directory that already holds a deployer keypair says so and changes
nothing; `config init` with `--gpg-key <path>` registers the key it was handed and invents nothing.

**R-11.5 (the one seam).** One `read_decrypted(path)` function, used by every read of an encrypted
configuration file. **The count is seven, not three, and it is measured rather than listed** — the
2026-11-03 census and the grep that confirmed it found the earlier text's "three sites" to be three names
for fewer reads, and four reads it had never heard of. `Config._load_json_file`, the `helmfile_apply`
Grafana credential read, and everything that reaches `Config.get('…encrypted')` from `helm_utils`,
`grafana_client`, `tekton_utils` or `automation` are **one** read: the credential path never opens a
file, it consults a `Config` that opened one. The seven independent reads of layer material are
`Config._load_json_file` (`config.py:1027`, four files), `config_init._read_document`
(`config_init.py:725`, the answer verbs), `config_init.cluster_is_unaddressed` (`config_init.py:1021`),
`config_validate._read_object_file` (`config_validate.py:420`), `config_validate._cluster_fact_finding`
(`config_validate.py:500`), `cluster._static_declaration` (`cluster.py:808`) and `cluster._read_kubeconfig`
(`cluster.py:218`) — the last one because v2 made cluster resolution read a file, which is where R-11.6
bites. The `kubementat.yaml` read (`config_validate.py:197`) is deliberately out: that file is not
encrypted material, and a seam that wraps it would promise coverage it does not need.
*Reads that are not this seam, said rather than discovered:* the Kubernetes client's own kubeconfig read
and `kubectl config view`, which Python cannot wrap; and `values.encrypted/*.yaml`, which helmfile's
Go template opens only to ask whether the file exists, and helm opens the bytes itself, so a
Python-only seam leaves that surface exactly as locked as it is today and its failure reaches us as a
non-zero `helmfile apply` rather than as this function's refusal, and it is not a place `read_decrypted` may pretend to cover.
*The failure this seam exists to stop* is not the traceback, it is the asymmetry: `UnicodeDecodeError` is a
`ValueError` and not an `OSError`, so four sites catch it and describe it in four different words, two
sites do not catch it at all, and `config_init.cluster_is_unaddressed` swallows it and answers *"this
environment addresses no cluster"* over a file it could not read — the one place in `src/` that prints a
false sentence instead of an error. Every caller keeps its own error type and its own shape (validate
collects a finding; the cluster reader raises its own named error) but the sentence comes from one place.
*Done when:* all seven go through the one function, a locked file is named with its path and asked about
as git-crypt rather than as a codec failure, no site reports a locked file as an empty one, and a provider
other than git-crypt can be added without touching layering.

**R-11.6 (credential material inside the config repo).** A kubeconfig placed inside `<config>/` would be
protected by the `*.encrypted.*` filter if named that way, and unprotected otherwise. v2 §3.3 says the
kubeconfig stays the credential store, and R-6.1 says the path is machine-local, so kubementat neither
encourages nor supports kubeconfig material in the config repo: `config validate` flags a file that parses
as a kubeconfig anywhere under the root, whether encrypted or not.

**R-11.7 (kubementat reads, it never unlocks).** No command runs `git-crypt unlock`, anywhere. The owner
unlocks a workstation checkout; a pipeline unlocks after checkout (§10.3). `config init` is the exception
of one kind: it *creates* the identity and exports the key, which is producing a credential, not unlocking
a tree.
**R-11.7.1 (a locked config is a refusal).** A git-crypt-ciphertext `static.encrypted.json` currently
fails as malformed JSON. The seam detects the marker and refuses with the file, the command that unlocks it,
and the fact that kubementat deliberately does not run it.
*Done when:* no `src/` path execs `git-crypt unlock`, the three read sites share `read_decrypted`, and a
test points a command at a locked config and asserts the refusal names the file and the command rather
than the JSON parser.

**R-11.8 (generation is an option on one answer, not a class of key).**
**No key is special.** kubementat keeps no list of secrets that behave differently from any other
answer: every key in every layer is written the same way (`config set`), read the same way (`Config`),
protected the same way (`--encrypted`, and git-crypt behind it), and refused the same way when it is
missing. What differs is only *who supplied the value*.
*Generated when asked:* `kmt config set <dir> E --generate KEY [--team T]` writes a value kubementat
produced, and only when that key currently answers nothing. The refusal when it already answers is the
center of the rule, not a detail: a verb that generated while it wrote would let the act of touching a
directory be the way a credential is lost, and no layer-creating verb — `config init`,
`config add-environment`, `config add-team` — generates anything at all.
*Supplied by the operator:* registry access, SMTP, git credentials — everything that means a human went
and got it. Absence is R-7.2's refusal.
*Never rewritten in place:* if a value is there, kubementat keeps it. Rotation is a deliberate act, which
is what `--generate` refuses on.
*Not required, as of this revision:* `MYSQL_ROOT_PASSWORD` and `REDIS_PASSWORD` are not required answers.
`REDIS_PASSWORD` is read by nothing in the product, and `create-mysql-database.yml` is the one consumer
`MYSQL_ROOT_PASSWORD` still has — so leaving it unanswered is allowed and that task cannot run, which is
the same refusal shape as any other missing answer and is stated here because the consumer exists.
The two webhook secrets and `GRAFANA_ADMIN_PASSWORD` stay answers like any other key; the reason they may
be generated is that a random value answers them as well as a human-chosen one would, not because they
are privileged.
*Done when:* a config missing a credential it needs refuses with the key, the file, and
`kmt config set --encrypted`; `--generate` on a key that answers is refused and writes nothing; no verb
that lays down or touches a layer generates anything; and no code holds a list of "secret keys".

---

## 12. Product packaging (prerequisite)

**R-12.1.** The distribution ships its assets. `templates/` (19 files) and `tekton_ci/{tasks,pipelines}`
(29) become package data through `importlib.resources`.
*Measured at 0.4.0:* the wheel holds 37 files and **not one asset**, which is why a "tool install" does not
exist and `KUBEMENTAT_HOME` must be a checkout. `scripts/` is empty and `helm_charts/` is a `.gitkeep`, so
neither has anything to ship; both belong in a later row, not this prerequisite.
*Done when:* `uvx --from ./dist/kubementat-*.whl kmt install --config-dir <dir> dev dev1` works with no
checkout, and a test asserts the packaged manifest resolves every asset path `constants.py` names.

**R-12.2.** No runtime dependency installation: `install_python_dependencies()` (`automation.py:388`) is
deleted. A released tool does not `uv sync` itself into its caller's directory.
*Done when:* nothing in `src/` execs a dependency manager, proven by a guard that fails if the argv of
any process a command spawns names `uv`, `pip`, `poetry` or `conda`, and that the checkout's dependency
state survives a run that was told nothing.
The original bar was `grep -rn "uv.*sync" src/` empty (amended 2026-11-02, owner: state the rule, not
the search). It now returns one sentence inside `version.py`'s refusal — telling whoever reads it what
to run *from a source checkout*, which is advice and not a spawn — plus the developer install line in
`src/cli/README.md`. Deleting either would remove help without removing the rule.

**R-12.3.** The product is pinned where it runs: the CI image tag and the Tekton `image` params carry the
kubementat version, so "which kubementat ran this deploy" is answerable from a PipelineRun. **Prerequisite
this revision found:** there is no authoritative version to read. `pyproject.toml` says `0.4.0`, `VERSION`
says `0.3.0`, and `kmt --version` does not exist — so a version-constraint check (§13.1) would be reading
a file nothing validates. One source of the version, one command that prints it, both before §13 is
trustworthy.

---

## 13. Staying current

**R-13.1 (the stamp).** `<config>/kubementat.yaml`:
```yaml
kubementat: ">=0.4,<2"        # the constraint this tree was last validated against
schema_version: 1             # bumps when kubementat changes what a key means
```
Two keys (R-5.4). The example is the current release line, not an aspiration; see §12.3's prerequisite
before this constraint can be enforced honestly.

**R-13.2 (warn and continue).** A version outside the constraint warns once, with both versions, and
continues. `config validate` is the command that exits non-zero about it. A working cluster should not be
bricked by a version string.

**R-13.3 (withdrawn 2026-11-05: there is no rename table and no migration).** kubementat records no
declarative rename/reshape table, ships none, and applies none. When a key is renamed or a path moves, the
product reads what it now reads and refuses the old spelling when the old location still exists (§9); the
operator is told, not migrated. This is a withdrawn guarantee and not an omission, and it is the sharpest
consequence of deleting `config migrate`: **the census gate that used to enforce it is gone**, so nothing in
the suite fails when a default key leaves the product without a word being written about it.

§9's trigger-directory move and §4.4's entry-point change are no longer table entries; they are named
renames that the reader has to be told about.
*Done when:* there is no table to keep complete, so there is no gate to fail. What is enforced instead is
that a **moved path is refused by name** when the old directory survives — the only protection that does
not depend on a verb. The CHANGELOG and a release note are now the only channel that tells an operator what
changed; that is the cost of the deletion, stated rather than worked around.

**R-13.4 (the mechanism, not the intention).** kubementat's release pipeline opens a bump PR (one line)
against known config repositories. Same shape as Flux's image-update automation writing to git, taken
without adopting the controller.

**R-13.5 (the window is one version).** kubementat `N` reads a config whose `schema_version` is `N`'s or
the one before. Older is a refusal naming both versions and the remedy — a deliberate `schema_version` bump
in `<config>/kubementat.yaml`, which is now the only way to close the gap, because no command will do it —
while §13.2's rule about version *strings* stays exactly where it was. Two or more steps behind
means every unrecognised answer comes from a built-in default, and the cluster is configured by
kubementat's assumptions rather than the operator's. There is no per-version fixture lane to make a wider
window real.
*Done when:* a config three `schema_version`s behind is refused before anything touches the cluster, the
refusal names both versions and the bump that closes the gap, and the bump is a human decision rather than
a side effect of some verb nobody asked for.

---

## 14. Existing combined trees

**Owner decision 2026-11-05 — `kmt config import` is withdrawn (N-10).** No downward compatibility was
re-asserted as the governing rule, and the command's reason for existing — *moving* an operator's
combined fork into the §5 layout rather than *running* an old one — was judged not worth its surface.
What replaces it is nothing, as a decision: a combined tree keeps working where it sits (§14.1, the
former R-14.2), and an operator who wants the new layout runs `kmt config init` and moves their answers
by hand with `kmt config set`. The Krone-style fork (63 `platform_config/` files, 205 `tekton_ci/` files,
own code under `utilities/`, 824 commits of merge-the-upstream as the upgrade strategy) is no longer a
reference case for any command; the measurements it stands for survive in §23 for that reason, not as a
migration target.

**R-14.1 (in-place operators).** A combined tree keeps working the way anything else does: point
`--config-dir`/`KUBEMENTAT_PLATFORM_CONFIG_DIR` at its `platform_config`. There is no second resolution
path and nothing to deprecate — the combined checkout stops being a *mode* and becomes a choice about
where a directory sits. What §5's layout costs such an operator is `config init` and the answers, moved
by hand — and that is a *choice*, not a requirement, which is exactly why no verb makes it for them.

## 15. `kmt config init`: one command, one directory, ready to use

Owner decision N-8, 2026-11-02. The bootstrap of a cluster's configuration is one command's job, it is the
only thing that materializes configuration anywhere in the product, and when it finishes the directory is
**ready to use**: git-crypt is live, the keys exist, the layout is complete, and the only thing between
`config init` and `kmt install` is the one sentence it prints (§6.3).

**R-15.1 (there is one implementation of "create a configuration directory").** No script, CI manifest,
harness module or documentation snippet may build a config tree another way. The reason is not tidiness:
`initialize` is what it is today — nineteen copies of product shape plus four hand-written writers
(`_initialization_write_targets`, `_write_environment_static_json`, `_write_team_static_encrypted_json`, …)
— because creation lives in one walker plus four writers, and `initialize`'s guard had to be rewritten
(`_check_is_already_initialized`) to derive "configured" from the write set rather than to remember it. One
command is the rule; the lane, the CI docs and the README all call it (§16).
*Done when:* the four `jq` **writes** into configuration that the lane runs today
(`stages.py:490`, `:748`, `:1907`, `:2610`) are gone — the seven `jq` **reads** stay, because `jq`
reading a file in a diagnostic is not writing configuration — and no `src/` function writes a file
under the config root outside `config init` / `config add-environment` / `config add-team` /
`config set` / `config unset`.

**R-15.2 (the answers arrive as arguments, not as exported variables).**

```
kmt config init <dir> --environment E --team T \
    [--set KEY=VALUE]… [--encrypted-set KEY=VALUE]… \
    [--git-url U] [--gpg-user E] [--no-commit] [--force]
```

Anything R-7.2 requires and `config init` is not told is a refusal that names the key, the file it belongs
to, and `kmt config set` as the command that writes it. A `--yes` that means "fill in the rest yourself"
does not exist: that flag would be R-6.3's cluster guess wearing a convenience label, and `generate_password`
invented by `automation.py:374` is the cautionary example (§11.8). Answers arrive this way for a reason:
today the lane exports nine variables to make `initialize` run (`tests/e2e/harness/config.py:221`), which is
the same interface a human trips over.

**R-15.3 (what "ready to use" means, as a checklist).** After `kmt config init <dir>` finishes, each of
these is true and asserted by the same test that names it:

| # | The property | Where it comes from | Asserted by |
|---|---|---|---|
| 1 | It is a git repository whose first commit exists | R-5.3, R-11.3 step 8 | `test_the_configuration_bootstrap_commits_its_own_layout.py` |
| 2 | git-crypt is initialized **in that directory**, with a collaborator registered and the symmetric key exported | R-11.3 steps 2-5 | the same test, step by step |
| 3 | The deployer SSH and GPG keypairs exist inside the config dir, not beside the product | R-11.4 | `test_the_cluster_keypair_is_a_property_of_the_configuration.py` |
| 4 | `.gitattributes` exists **before** the first encrypted file, so the first `git add` encrypts | R-11.3 step 6 | the ordering test §11.3's *Done when* names |
| 5 | The layout of §5 exists, with `static.json` populated from the arguments and no `REPLACEME_*` left | R-3.2, R-3.3 | `test_the_configuration_bootstrap_writes_answers_and_no_placeholders.py` |
| 6 | The bootstrap generates **exactly one** credential, `GRAFANA_ADMIN_PASSWORD`, and only where nothing already answers it; every other encrypted answer came from a `--set`/`--encrypted-set` or a stated null. Re-running `init` keeps what is there rather than inventing a second value | R-11.8 | `test_config_init_writes_answers_and_no_product_shape.py`, whose answer-layering test asserts the generated value is present and that the encrypted file holds nothing else |
| 7 | Both `CLUSTER_*` keys are `null` and the stderr sentence printed | §6.3, v2 §4.4 | the test v2's §4.4 already writes, re-pointed at `config init` |
| 8 | `kmt config validate` reports nothing except "this environment addresses no cluster yet" | R-3.2 | the same test |
| 9 | A second `init` writes nothing and says which step it skipped | R-11.3 | §11.3's *Done when* |
| 10 | `kmt install <environment>` runs against that directory with no other write step in between | TD-1's first half | §16's bootstrap lane |

Row 4 is the one that decides whether the tree is *safe*, and row 10 decides whether the word "ready" was
earned. Rows 1-9 are unit-testable; row 10 is the lane's.

**R-15.4 (what `initialize`'s nineteen writes became).** This is the table the CHANGELOG row for R-7.1's
deletion must reproduce, and it is why deleting the command is a mapping exercise rather than a removal:

| `initialize` does today | Where it goes |
|---|---|
| Nineteen template copies into `platform_config/` | **Nowhere.** R-3.2 — the product's own defaults, resolved at read time |
| Four `static*.json` writers, values from exported variables | `config init`'s layout + `config set`'s answers (R-15.2) |
| Deployer SSH/GPG keypair generation into the checkout | `config init` into the config dir (R-11.4) |
| `git-crypt init` / `add-gpg-user` / `export-key` in the checkout | `config init`'s §11.3 dance, in the config dir |
| `uv sync --all-extras` in the checkout | Deleted (R-12.2) |
| The `CLUSTER_KUBE_CONTEXT: null` stderr sentence | `config init` prints it (R-6.3) |
| Dependency check | Per-verb guard (R-7.3), not init's preamble |
| Eight required variables | Configuration (R-7.2) |

*Done when:* the CHANGELOG row for the deletion carries this table, `kmt initialize` is unknown to the CLI,
and no test invokes it (R-7.1).

---

## 16. The end-to-end suite is a consumer of `kmt config`, not a writer of configuration

Owner decision N-9, 2026-11-02. Today the lane is a second implementation of configuration creation, and
everywhere it differs from the product is a place the suite cannot prove anything.

**R-16.1 (the lane's config comes from kubementat's own verbs).** The harness creates its tree with
`kmt config init` and changes it with `kmt config set` / `unset`, reading it back with `kmt config show`. That retires four things the
suite does today, all of which are `jq` writing configuration the product's writers would have refused:

| What the lane does today | Why it is a problem | Becomes |
|---|---|---|
| `rm -f` four marker files (`INITIALIZE_MARKERS`, `stages.py:1039-1050`) so `initialize` will run at all | The lane has to *unmake* the product's refusal to overwrite before it can use the product | Init into a fresh directory; nothing to delete |
| `_set_config_field` — `jq --arg … CLUSTER_KUBE_CONTEXT` (`stages.py:743-751`, `:1907`) | The assert that proves layer 3 of §4.2 is about a file `jq` wrote, not a key kubementat would have written | `kmt config set` |
| `_null_config_field` (`stages.py:743-751`, the `null` the §4.3 refusal test needs) | `jq` cannot express the state the refusal is about, so the test mutates a shape `jq` produced rather than the state the product leaves behind | `kmt config unset` (the not-yet-named state is the real state) |
| `_seed_grafana_account_row`, which writes a `GRAFANA_USERS` row the accounts stage refuses to act without (`stages.py`, config.py:204-212) | The lane declares a credential through a path no operator has | `kmt config set --encrypted` |
| `suite.py:65`, which randomises `GRAFANA_ADMIN_PASSWORD` into the guest's environment for every run | A credential that enters the run through the ambient environment is exactly what §11.8 and R-7.2 delete | `config init`'s generated credential, or `config set --encrypted` per run |
| `_expect_initialized_tree` (`stages.py:1052+`) expects `platform_config/dev/kubementat_components/helmfile.yaml.gotmpl`, `deployer_ssh_key`, `git_crypt_symmetric.key` in the **checkout** | Two of those three files leave the product tree in this spec (§4.4, §11.4), so the assert is written against artifacts this change deletes | Asserted against the config directory, minus the helmfile copy |

The argument for this is not style. **A suite that configures its cluster with `jq` proves that `jq`
works.** v2's own lessons file had to record, twice, what it costs when the rig and the product disagree about
what a fixture claimed: a fixture whose cluster claim degraded silently from an address into a name
(`2c28eca`), and two halves of one comparison addressing two clusters with the record seeing neither
(`8fe2b68`). Neither is this bug, and both are the same species: the rig states a fact, the product never
agreed to it. Routing every write through `config` means the lane exercises the code an operator uses,
and the assertion `environment-names-its-cluster-in-static-json` (`stages.py:559`) becomes evidence about
kubementat instead of about `jq`.

**R-16.2 (the bootstrap lane).** One new lane — the cheapest in the suite, because it needs no cluster
until its last assert — that measures R-15.3's checklist and TD-1's first half: `config init` into a fresh
empty directory in the guest, assert the ten properties against the real filesystem and real `git-crypt`
(real binaries, not mocks, which is the property a unit suite structurally cannot have), then
`kmt install` and confirm it reaches a cluster without a single hand-written file in between.
*Owner ruling 2026-11-03, which closes open question 7:* **one configuration directory per lane.** That is
the answer R-3.4 makes the cheapest instead of merely the safest: a config root has no default and is named
every run, so a lane that wants isolation does not have to invent a mechanism, it just names a directory no
other lane was told about. It also buys the assert R-15.3 row 9 is actually about — "a second `init` writes
nothing and says which step it skipped" means one real thing per lane, on a tree that lane created and
nobody else touched, rather than something measured on a shared root that an earlier lane already
configured. What the ruling costs is a `git init`, a keypair and a keystore per lane, which is the
expensive half of `config init` and is still cheaper than the cluster the other lanes need. This is the
suite's *only* place where "config init → ready → install" is one measured sequence rather than a document
that asserts it. What the documents say today is `uv run kmt initialize dev dev1` (`README.md:174`) followed
four lines later by `uv run kmt install dev dev1` (`README.md:203`), and `docs/index-overview.md:15`'s
`uv sync` → `initialize dev smoke` → `install dev smoke`; `54c40e5` had to fix the quickstart's ending for
exactly this reason, and a bootstrap lane is the only place the pair is actually run.

**R-16.3 (the two assertions that prove git-crypt is genuinely live).** These are worth the lane's time
because they are the difference between "init ran git-crypt" and "the tree is usable":

1. **The first-commit assert:** after `config init`, the encrypted file's *working tree* content reads as
   plaintext while its `git show HEAD:<path>` form reads as ciphertext. One file, two versions, and the
   filter did its job — which is precisely what ordering step 6 of R-11.3 exists to guarantee, and the only
   assertion that catches a `.gitattributes` written too late.
2. **The never-plaintext-history assert:** `git log --all -p -- …/static.encrypted.json` contains no
   plaintext credential. Cheaper to run than it sounds, and it is the assert that would have caught the
   failure mode where a credential gets committed once before the filter is applied and stays there forever.

**R-16.4 (the harness's own configuration becomes a fixture, not a fixture of JSON).**
`tests/e2e/harness/config.py`'s `DUMMY_ENV` block (nine variables) turns into the argument list passed to
`config init` — one place where the lane's identity (`BASE_DOMAIN`, `AUTOMATION_GIT_*`,
`CLUSTER_MANAGER_EMAIL`, the CI image coordinates) is declared, in the form kubementat actually takes them.
The lane's current jq rewrite of the CI image fields (`stages.py:485-506`, three keys in one call) becomes
two `--encrypted-set`/`--set` answers, which means the lane no longer edits config *after* init in that
area. `INTEGRITY_FILES` (`config.py:856-861`, the deployer keys and `git_crypt_symmetric.key`) keep their
byte-identity assert; only their paths move, because R-11.4 moves the files.
*Done when:* the four config-writing `jq` calls in `tests/e2e/harness/stages.py` are gone, the
bootstrap lane's
`result.json` carries R-15.3's ten properties as ten named asserts, and the collision and §4.3-refusal
lanes read their state back through `kmt config show`, whose per-key line carries the layer and the file
the way the deleted `explain`'s stderr did.

---

## 17. Spike status

Both of the 2026-10-30 spikes stay answered — measured against helmfile 1.7.0:

* **SP-1, path resolution:** a relative path inside a `bases:`-referenced base resolves against the
  **entry** state's directory (`base/base.yaml` with `values: [./values/default.yaml]`, applied through a
  root `entry.yaml`, failed with `does not exist in "."`), while `helmfiles:` composition resolves against
  the child's own directory. So §4.2/§4.4 reference the product's state as a `helmfiles:` child and
  **no absolute-path product-directory injection is needed.**
* **SP-2, `missingFileHandler`:** per-release; `Warn` lets a missing file in that release's `values:` list
  pass and the default handler errors. `Warn` cannot distinguish "no override" from "the default is gone" —
  not a risk here, because the default ships inside the wheel and only operator paths can be missing,
  which is why §12.1 is a prerequisite of §4.2 rather than a parallel track.

**SP-3 is new, and it is this revision's spike** — the one that must not become a ticket that discovers it
late: *does the 1:1 check + `EnvironmentRegistry` behave correctly when one root is a config repository and
the kubeconfigs it names live on two machines?* Concretely: two environments, one config repo, two
`CLUSTER_KUBE_CONTEXT` values, resolved on one machine that only has one of the kubeconfigs. What does
`kmt environments list` print, and does the collision check see a claim it cannot resolve? Today's
`EnvironmentRegistry.all()` resolves every sibling and tolerates a failure as a *reason string* (v2 §9.4's
"report, don't drop"), which probably makes this fine — measure it, and write the answer into R-6.2 rather
than assuming it.

---

## 18. Test strategy

* **Unit (`uv run pytest -q`, no cluster).** Precedence per surface (§4); config-root resolution and the
  refusal (R-3.4, R-8.2); flag-to-registry threading (R-8.3); layout/validate (R-3.2, R-5.4); collision
  scope in a config root (R-6.2); Tekton name-match precedence (§9); version warn-vs-refuse (R-13.2,
  R-13.5); git-crypt idempotence (§11.3); packaged assets (R-12.1); the `config init` checklist of R-15.3
  rows 1-9, and the `config` verbs of §7 (`show`'s per-key layer and file tag, `unset`'s
  fallback-to-default).
* **Lane (`uv run python tests/e2e/suite.py`).** The **upgrade lane** that this section used to list is
  deleted: it ran `config diff` between `N` and `N+1` and asserted the component set survived, and with the
  verb gone there is nothing between the two installs to assert. It was spec prose only — no lane, scope or
  stage by that name ever existed in `tests/e2e/` — so deleting it costs no code, and the honest consequence
  is that the upgrade path is unmeasured. **Bootstrap lane** (§16.2, new) — `config init` into a fresh
  directory, R-15.3's ten properties asserted against real `git-crypt` and real git, then `kmt install`, so
  "one command → ready → installed" is one measured sequence. **Two-environments-
  one-cluster lane** — v2's existing collision assertion, re-run against a config root instead of a
  checkout's `platform_config/`, so TD-2's second half is measured and not inherited.
* Test names describe behaviour, never ticket ids (`tests/test_test_name_conventions.py`).

---

## 19. Phasing, with gates

| # | Deliverable | Gate | Breaks a tree? |
|---|---|---|---|
| 0 | Version made honest (§12.3) + assets in the wheel, `importlib.resources`, `uv sync` gone (R-12) | `uvx … kmt install --config-dir` runs without a checkout; one version source | no |
| 1 | `--config-dir` everywhere and mandatory, no path from `kubementat_main_dir`, registry threaded through it (R-3.4, R-8) | the edge is gone (see R-8.1's amended bar — one signature line remains in the grep), the refusal test, the listing-uses-the-flag test | **yes** — one refusal naming both spellings, no shim |
| 2 | Layering read path: JSON, values, Tekton name-match, both `tekton/` layers (R-3, R-4) | precedence tests green; a config with zero files installs the product defaults | no — copies keep working as overrides |
| 3 | `config init` as the **only** command that creates the layout and the answers (R-15.2) + `config show/validate/unset` (§7) | R-15.3 rows 5, 7, 8 green; TD-1's "no file kubementat wrote for editing" holds | no |
| 3b | *(lands after 5)* The lane builds its config with `config init`/`config set` and the bootstrap lane appears (§16) | the bootstrap lane's ten asserts green on real `git-crypt`; the harness's config-writing `jq` gone | no |
| 4 | Product-owned helmfile entry point + values layering (R-4.4, R-4.2) | plan row 13 cannot be constructed | **yes** |
| 5 | git-crypt out of the product, the dance **and the keys into the config directory**, the `read_decrypted` seam (R-11) | product passes a clean-clone test with no git-crypt, **and R-15.3's checklist rows 1-4 and 9 are green — that is the moment `config init` becomes "ready to use"** | **yes** where repo-scoped git-crypt was assumed |
| 6 | Tekton directory move + generated manifests tracked (§9) | generation and reading agree in one layer; the old path is a named refusal | **yes** |
| 7 | CI two-source model + §10.4's stated cluster (R-10) | §10's provenance test | **yes** |
| 8 | `kmt initialize` deleted, `config init` its replacement, eight variables deleted (R-7.1, R-7.2) | the eight keys are answered through the layers, not the environment; `initialize` unknown | yes |
| 9 | *(revised 2026-11-05)* Release-pipeline bump PRs (§13.4) only. `config diff`/`config migrate` were deleted before this phase was reached, so the version contract is enforced by the stamp and `validate` alone, and TD-1's bump assertion is unmeasured (see TD-1 and R-13.3). | release-pipeline bump PR green; the stamp's window refused where it must be | no |

Phases 4-8 are one minor-version line; they do not become eight deprecation branches. What is documented
beside that line is no longer a migration tool, because there is none: the release note and the CHANGELOG
say what changed, and `schema_version` says when to expect a break. **What v2 changed about this table:** phase 1 got harder (the registry
and the collision check have to follow the flag, R-8.3 — it was not a `Config` constructor question at
all), phase 5 got two-thirds smaller (the tracked `.git-crypt/` is already gone), and phase 6 is new.

Two dependencies this table has to state rather than discover:

* **`config init` cannot be "ready to use" before phase 5.** Rows 1-4 and 9 of R-15.3 are git-crypt and
  key facts, and phase 5 is what moves them into the config directory. So phase 3 delivers the command
  with a tree that is *complete but not usable*, and the bootstrap lane (3b) is gated by phase 5. That
  is a real sequencing fact, not a gate that slipped — and it is why "init → install works" is a
  phase-5 assertion, not a phase-3 one.
* **Between phase 3 and phase 8, a tree can be created two ways.** `config init` exists first and
  `kmt initialize` is deleted later. The rule during that window is that `config init` is authoritative
  and `initialize` is a legacy path that writes a worse tree — and the CHANGELOG row says so in the same
  release, because a reader who picks the wrong one gets an operator tree full of product copies.

---

## 20. Requirements traceability

| Coupling, measured at v2 HEAD | Removed by |
|---|---|
| Config root is `main_dir/platform_config` (`config.py:87-105`, `:218`) | R-3.4, R-8.1, R-8.2 |
| The distribution ships no assets (wheel = 37 `.py`/`dist-info`) | R-12.1 |
| Product initializes itself in the checkout (`automation.py:388`, `:735`, `:753`) | R-11.1, R-12.2 |
| Operator edits copied product shape (`_initialization_write_targets`) | R-3.2, R-3.3, R-7.1 |
| Answers from eight exported variables (`automation.py:244`) | R-7.2 |
| Helmfile lives in the config dir (`helm_utils.py:289-307`) | R-4.4 |
| CI clones the fork for code and config (`deploy-local-helm-chart.yml:23-28`) | R-10.1, R-10.2 |
| git-crypt's filter in the product's `.gitattributes`; product still execs it | R-11.1 |
| Cluster credentials in `kubementat_main_dir`; six generated secrets in place | R-11.4, R-11.8 |
| Triggers generated to `<team>/triggers/`, not under `tekton/` (`config.py:307`) | §9 |
| Environment enumeration keyed off a checkout-relative root (`EnvironmentRegistry`, `environments.py:51`) | R-5.1, R-8.3 |
| A declared `CLUSTER_KUBE_CONTEXT` beats a pod's own credentials and fails on an absent file (`cluster.py:177-199`) | R-10.4 |
| Configuration created in three places that can drift (`_initialization_write_targets` + four writers) | R-15.1, §15.4 |
| The lane writes its own configuration with `jq` (`stages.py:485-506`, `:743-751`, marker deletion at `:1039`) | R-16.1, §15.1 |
| git-crypt initialised in the checkout, so a config dir is never "ready to use" on its own (`automation.py:735`, `:753`) | R-11.3, R-15.3 |

---

## 21. Open questions

1. **Where do the bump PRs get written?** R-13.4 needs a list of known config repositories. Nothing
   inventories them, and the release pipeline must not guess.
2. **Is one `kubementat.yaml` at the root enough when a config repo is shared between two products?** Two
   kubementat installs pointing at one tree is legal today (both read `KUBEMENTAT_PLATFORM_CONFIG_DIR`).
   R-5.4 says one manifest per root; nothing says one root per product.
3. **Do generated manifests get committed by `kmt tekton-generate-*`, or does the operator commit after
   generating?** §9 says tracked, not that kubementat commits; the same question §11.3 answers for `init`
   in the other direction.
4. **`kmt config set --generate`** (R-11.8's team-level secrets): a verb, or an option on `config init`'s
   team branch? A refusal that tells someone to run a command that does not exist yet is worse than the
   `generate_password` today.
5. **Phase 6's ordering against v2's own CI** — §10.4's rule is stated as a CI requirement, but the
   pipeline manifests live in the product tree, so the TaskRun change and the config-repo split land in
   the same phase-7 commit. Is that acceptable, or does phase 7 need its own spike?
6. **Does `kmt config init` ever prompt?** R-15.2 gives the lane a fully non-interactive command, and R-3.3
   says kubementat "asks for what it needs" — which, read literally, means a human running `config init
   /path` with no `--set` should be asked. That is a second path through the same command which the suite
   can never take, and it is the half of `initialize` people actually liked. Strict-plus-documented, or
   interactive-when-a-tty, is a real fork and §15.2 chose neither.
7. **How many config directories does the lane own?** §15's second-init assert (§15.3 row 9) wants a fresh
   directory; the lane today carries one `GUEST_TREE/platform_config` through every stage and asserts
   byte-identity over the keys inside it (`INTEGRITY_FILES`). Per-stage freshness and that assert are not
   the same test, and only one of them can be true of one directory.

---

## 22. What this revision corrected (the review record)

The 2026-10-30 document was reviewed against v2 HEAD (`e9968c2`). Four claims were stale, one was
contradicted by code shipped after it was written, and three gaps only exist *because* v2 happened. The
second pass (§15, §16) added what the bootstrap workflow and the end-to-end suite needed from this spec, at
the owner's request on the same date.

### 22.1 Findings

| # | The 2026-10-30 text said | Measured at v2 HEAD | Disposition |
|---|---|---|---|
| 1 | §8.1: `.git-crypt/` is 18 tracked files, 17 collaborator identities | `git ls-files .git-crypt` → **empty**; it left in `c9d2592` | R-11.1 rewritten; phase 5 shrinks |
| 2 | §2.2: multi-cluster is out of scope | v2 shipped it two days later (`cluster-config-v2.md`, `eca4c31`), and that spec retracted this row | Retired here too (N-4) |
| 3 | §9/§4.3: generated triggers live in `tekton/triggers/<app>/` | `config.py:307-309` writes `<team>/triggers/`; `automation.py:496` scaffolds it | N-6: it is a named rename refused at the old path (§9), with no table and no migration to apply it |
| 4 | §8.8: only Grafana generates a credential | `automation.py:439,472-475` generate **five** (Grafana + MySQL + Redis + two webhook secrets) | R-11.8 rewritten; team-level generation must not be a side effect of touching a team |
| 5 | §12.3/§18 Q1: CI unlocks and then reads, which settles it | Correct as far as it goes, but it says nothing about *which cluster*. v2's ordering makes the cloned config's `CLUSTER_KUBE_CONTEXT` beat the pod's own credentials and fail on the missing file (`cluster.py:177-199`, `cluster.py:1046-1066`) | **Gap.** R-10.4 (N-3) is new, and it is the most important line in this document |
| 6 | §7: the env var is not honoured where it matters, and `Config` is the fix | `default_platform_config_dir` is now the whole rule, but `kmt environments list` builds the registry from the variable alone (`environments.py:51`) and `EnvironmentRegistry` owns root scope | R-8.3 (N-5) is new: `--config-dir` must reach the registry and the 1:1 check |
| 7 | The old §5's `R-5.0` wants "the same guard `NON_TEAM_ENVIRONMENT_DIRECTORIES` applies one level down" | v2 already has that rule one level *up*, as `EnvironmentRegistry.environments()` (`cluster.py:682-697`) | R-5.1 re-pointed: the enumeration exists; only the root moves |
| 8 | §10.1's version stamp, `kubementat: ">=0.6,<1"` | `pyproject.toml` = 0.4.0, `VERSION` = 0.3.0, no `kmt --version` | §12.3 prerequisite: make one version source, and stop quoting an unenforced constraint |
| 9 | §6's `kmt config` table takes `--team` | v2 deleted `--team` from eight commands and `team_option` from 16 uses to 4 | §7's table keeps `config set --team` as a *layer writer*, not as a re-add of the option v2 justifiably deleted |
| 10 | Line references: `config.py:36,166-173,405`; `automation.py:215,278-295,308-313,315-352,613-625,908-911`; `helm_utils.py:74,96,213-235,442` | v2's rewrite moved every one of them: `:87-105`, `:218`, `:522`; `automation.py:244`, `:388`, `:735`/`:753`; `helm_utils.py:139-147`, `:289-307` | Re-measured throughout; §20's table replaces them |
| 11 | §14's SP-1/SP-2 close the spike list | A new question exists: enumeration and collision across machines sharing one config repo | SP-3 (§17), measured, not assumed |
| 12 | The command list named `config init` but never said that it is the *only* thing that creates configuration, nor how the end-to-end suite gets its tree | The suite writes configuration itself, in four `jq` places (`stages.py:490`, `:748`, `:1907`, `:2610`) and deletes four markers to make `initialize` run (`:1039`) | **R-15.1 and §16 (N-8, N-9) are new** — the bootstrap workflow and the suite's relationship to it |

### 22.2 What survived the review

Everything about the mechanism. Layering (R-3.1), "no shape in the configuration" (R-3.2), "answers are
data" (R-3.3), the three knobs and no `--template` (§4.2), the operator-services rule (§7), name-matched
Tekton ownership (§9), warn-while-schema-refuses (R-13.2/R-13.5), the one-seam secret design, and the two
helmfile spikes. One design that did not survive was the migration shape: the rename table and the verb that
applied it were deleted on 2026-11-05 (R-13.3), and what remains of staying current is the stamp, `validate`,
and a human reading a release note. Nothing in this revision changes the chosen design — the config
repository, layered on product defaults, with git-crypt as its secrets mechanism, delivered as a separate
git repository. What changed is that v2 walked into the middle of it in the meantime, and three of its
provisions were written for a tree where `platform_config` was a checkout's directory.

---

## 23. Sources

**In-tree, measured 2026-11-02 at `e9968c2` (v2 HEAD):** `src/kubementat/cluster.py` (ClusterTarget,
EnvironmentRegistry, Context.build, `_kubeconfig_files`); `src/kubementat/config.py`;
`src/kubementat/automation.py`; `src/kubementat/helm_utils.py`; `src/cli/commands/environments.py`;
`src/cli/commands/helm.py`; `templates/environment/static.json.template`;
`templates/environment/kubementat_components/helmfile.yaml.gotmpl`;
`tekton_ci/tasks/deploy-local-helm-chart.yml`; `pyproject.toml`; `VERSION`;
`unzip -l dist/kubementat-0.4.0-py3-none-any.whl`; `git ls-files .git-crypt`; `find templates -type f`
(19); `ls tekton_ci/{tasks,pipelines}` (19/10); `uv run kmt --help` (27 entries); `docs/README_PLATFORM_CONFIG.md`;
`docs/specifications/cluster-config-v2.md`; `docs/implementation-summaries/cluster-config-v2.md`;
`docs/lessons-learned/2026-11-02-cluster-addressing.md`;
`docs/plans/finish-python-migration-last-lap.md` rows 8/11b/13/14; `TODOs.md`
("Blocked / out of scope for this migration", "Refactoring platform config structure").

**Read for §16, the end-to-end suite:** `tests/e2e/harness/stages.py` (`remove_initialize_markers:1039-1050`, `_expect_initialized_tree:1052+`, `_point_pipeline_at_a_resolvable_image:485-506`, `_declare_the_cluster:519+`, `_set_config_field`/`_null_config_field:743-751`, `:1907`, `_seed_grafana_account_row:2610`, and the seven config reads at `:713`, `:736`, `:1069`, `:1919`, `:1933`, `:1941`, `:2565`); `tests/e2e/harness/config.py` (`DUMMY_ENV:221-231` — nine variables; `INITIALIZE_MARKERS:235-240`; `CAPABILITIES` row `config.initialize:354-356`; `INTEGRITY_FILES:855-861`; the `GRAFANA_USERS` requirement at `:204-212`); `tests/e2e/suite.py:65` (the per-run `GRAFANA_ADMIN_PASSWORD`); `tests/e2e/stages/01-provision.sh:44-46` (`git-crypt` is installed on the guest, which is what makes §15.3 measurable there) and `:181-183` (gpg present). Measured: **four** config writes through `jq`, **seven** reads.

**Prior art:** [helmfile writing guide](https://raw.githubusercontent.com/helmfile/helmfile/main/docs/writing-helmfile.md)
· [helmfile configuration (remote states)](https://github.com/helmfile/helmfile/blob/main/docs/configuration.md) ·
[shared configuration across teams](https://helmfile.readthedocs.io/en/latest/shared-configuration-across-teams/) ·
[helm values best practices](https://github.com/helm/helm-www/blob/main/version-3/chart_best_practices/values.md) ·
[helm-migrate-values](https://github.com/OctopusDeployLabs/helm-migrate-values) ·
[kubeadm upgrade](https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-upgrade/) ·
[kubespray getting started](https://github.com/kubernetes-sigs/kubespray/blob/master/docs/getting_started/getting-started.md) ·
[Google GitOps best practices](https://cloud.google.com/kubernetes-engine/enterprise/config-sync/docs/concepts/gitops-best-practices) ·
[Flux image update automation](https://fluxcd.io/flux/guides/image-update/) ·
[git-crypt](https://github.com/AGWA/git-crypt)
