# Specification: Externalizing kubementat's cluster configuration

**Date:** 2026-10-30
**Status:** specification for review. Not scheduled; supersedes the "do not design for it yet"
gate on this topic (`TODOs.md:937`) by the owner's decisions of 2026-10-30.
**Background:** [`research-config-code-separation-10-29-2026.md`](research-config-code-separation-10-29-2026.md) —
measured the coupling and surveyed the alternatives. This document is the chosen design: **layered
configuration (Option 3) delivered as a separate git repository that uses git-crypt for its secrets.**
**Owner decisions this spec encodes:**
1. Layering, not copying, is the mechanism.
2. Operators may put **their own** Tekton Tasks/Pipelines/Triggers/PipelineRuns **and their own
   helmfiles** in the configuration — the latter for sets of services kubementat does not install.
3. Overrides are matched **by name** only. No override annotations in v1.
4. Version drift **warns and continues**; only `kmt config validate` fails the run.
5. `kmt config init` performs the **complete** git-crypt dance and creates the directory structure and
   the required files.
6. git-crypt stays kubementat's secret mechanism and **leaves the kubementat repository**.

---

## 1. Problem and goal

Today one checkout is the product, the product's assets, and the operator's cluster configuration, and
`kmt initialize` copies 19 paths of product shape *into* the configuration directory. That single
mechanism breaks both requirements the owner stated: kubementat cannot develop independently (any change
to an asset lands in a tree that is also someone's working material), and operators cannot stay current
(improvements reach them as merge conflicts — measured in the Krone fork: 824 commits, `Merge remote-tracking
branch 'upstream/master'` as the upgrade strategy).

**Goal.** kubementat's code and assets evolve without touching anyone's tree, and an operator's cluster
stays current by changing one line and reading a diff.

**Test that decides the whole spec (TD-1):** a cluster configured against kubementat `N` is upgraded to
`N+1` by editing **one line** in `kubementat.yaml`, running `kmt config diff` and `kmt install` — with no
file copied from the product, no merge, and no file in the configuration directory that kubementat
generated for the operator to edit.

---

## 2. Scope

### 2.1 In scope
* The layering model and its precedence rules (§4).
* The configuration repository layout and file semantics (§5).
* The `kmt config` verb group (§6) and the `--config-dir` / `KUBEMENTAT_PLATFORM_CONFIG_DIR` contract (§7).
* git-crypt moved out of the product repository and into the config repository, including `kmt config
  init`'s initialization sequence (§8).
* Operator-owned Tekton objects and helmfiles (§9).
* Version contract and the `diff`/`migrate` machinery (§10).
* Product packaging: assets shipped inside the distribution (§11).
* CI/Tekton execution against two sources instead of one fork (§12).
* Migration of existing combined trees (`kmt config import`) (§13).

### 2.2 Out of scope (explicit)
* Flux/ArgoCD or any in-cluster GitOps controller replacing `kmt`. The idea worth keeping — *an
  automated commit keeps config current, not a human* — is delivered by the release pipeline opening
  bump PRs (§10.4), not by a controller.
* SOPS, age, Sealed Secrets, or a secret-provider plugin interface. git-crypt is the mechanism for v1;
  §8.5 records where the one seam goes, so a provider can appear later without redesigning layering.
* Multi-cluster / cluster-context support. **Corrected 2026-11-02, because a reader has to know which way
  this cuts.** It is right for this document — nothing about externalizing `platform_config` needs a
  multi-cluster answer, and a GitOps-shaped mechanism is a poor place to put one — and wrong for the product,
  which was already carrying the claim in `TODOs.md` Features and in
  [`../plans/finish-python-migration.md`](../plans/finish-python-migration.md)'s `Non-goals` row. Both of
  those pointed at a future plan; [`../specifications/cluster-config-v2.md`](../specifications/cluster-config-v2.md)
  is that plan, and it is implemented through `eca4c31`. So: do not read this line as "multi-cluster was
  decided against". It says *not here*, and the place it means is the spec above.
* Deleting Tekton as the CI engine.
* Any backwards-compatibility alias, shim, or deprecation branch (project rule: one deletion, one name).
  Where behaviour changes, this spec changes it and the CHANGELOG records it.

---

## 3. Architecture: three layers, one precedence order

```
L0  product layer        installed kubementat (wheel + CI image)
                         defaults in constants.py, templates/, tekton_ci/, helm_charts/
        ↓ overridden by
L1  environment layer    <config>/environments/<env>/…
        ↓ overridden by
L2  team layer           <config>/teams/<team>/…
```

**R-3.1 (precedence).** For every configuration answer, `L2 > L1 > L0`. A key absent at `L2`/`L1` resolves
from the product default. This is the existing `Config._component_value` rule (`config.py:405`) promoted
from namespaces to every configuration surface, including values files and Tekton manifests.
*Done when:* a test seeds the same key at all three layers and asserts the team value wins, and a second
test deletes it from `L1`/`L2` and asserts the product default answers with one warning.

**R-3.2 (no shape in the configuration).** kubementat writes no product-shaped file into the
configuration. The 19 write targets of `initialize` (`automation.py:278-285`, ledger row 11b) reduce to:
the layout of §5, plus the operator's answers as data. Nothing in the configuration may be a
`helmfile.yaml.gotmpl`, a `*_values.encrypted.yaml.template`, or a Task/Pipeline kubementat also ships.
*Done when:* `kmt config init` in an empty directory writes the layout of §5 and then
`kmt config validate` reports **zero** files that duplicate a product asset, and a second
`kmt config init` writes nothing.

**R-3.3 (answers are data).** Every operator answer is JSON or YAML values, not a template kubementat
renders and then hands over. The `REPLACEME_*` placeholder pattern
(`templates/environment/static.json.template`) goes away: kubementat asks for what it needs and writes
the answer, instead of writing a file with placeholders and a README telling someone to find them.
*Done when:* no `REPLACEME_` string appears in a file kubementat writes, and a missing required answer is
a refusal that names the key and the command that sets it.

**R-3.4 (one root, one resolution).** `Config` resolves the configuration root exactly once, in this
order: `--config-dir` argument → `KUBEMENTAT_PLATFORM_CONFIG_DIR` → `kubementat.yaml`'s default →
error. `kubementat_main_dir` is never consulted for configuration.
*Done when:* `tests/test_the_configuration_root_is_resolved_from_the_config_dir_argument.py` covers all
three and the empty-value case (same "set but empty is an error" rule as `_directory_from_environment`,
`config.py:46-70`).

---

## 4. Layering semantics per surface

### 4.1 JSON configuration (`static.json` / `static.encrypted.json`)
Merged shallowly, key by key, `L2 > L1 > L0`. The product layer is `constants.py` plus the packaged
`defaults/static.json` (the current `templates/environment/static.json.template`, repositioned as
defaults rather than a template to copy).
*Done when:* `kmt config show` prints each key tagged `default`, `environment`, or `team`.

### 4.2 Helm values
A release's values list is `[<product default>, <environment override>, <team override>]`, with
`missingFileHandler: Warn` on the override entries so an absent override is not an error
([helmfile writing guide](https://raw.githubusercontent.com/helmfile/helmfile/main/docs/writing-helmfile.md)).
*Done when:* a test asserts an environment with zero value files applies the product defaults, and a test
asserts an override file's key wins against the default.
**Blocked on SP-1 (§14).**

### 4.3 Tekton manifests — operator-owned, matched by name
The product applies `<product>/tekton_ci/tasks` then `<product>/tekton_ci/pipelines` (existing
`automation.py:908-911`), then applies `<config>/environments/<env>/tekton/{tasks,pipelines}` and
`<config>/teams/<team>/tekton/{tasks,pipelines}` in that order. A manifest with the same `metadata.name`
as a product manifest replaces it
(Layering Rule R-3.1, name match per owner decision 3); a new name adds one.
`apply_resources` continues to skip directories named `templates/` (`automation.py:64-69`), so generator
input is never applied.
*Done when:* a test applies a product Task plus a team Task of the same name and asserts the applied
object is the team one, and asserts the environment version loses to the team version.
**Note:** today the environment layer of `platform_config/{env}/tekton/` exists only for the team layer
(`config.py:195-198`). Spec: `<config>/tekton/` gets both an environment and a team half
(`<config>/environments/<env>/tekton/…` and `<config>/teams/<team>/tekton/…`), because the README's
documented order is default → environment → team and a team-only layer is half of what it promises.

### 4.4 kubementat's component helmfile
The **product's** file is the entry point; the configuration contributes environment values
(`environments: <env>: values: [<config>/environments/<env>/values.yaml]`) and the per-release overrides
of §4.2. `helmfile_paths()` (`helm_utils.py:213-235`) stops pointing at the operator's copy and points at
the packaged manifest.
*Why this is load-bearing:* plan row 13 is the bug where `kmt component install dev traefik` refused a
real component because the *operator's copy* of the manifest was stale. With a product-owned entry point
that state cannot exist. `Config.component_release_index` (`helm_utils.py:237`) keeps reading the manifest
helmfile actually applies, so the two stay honest.

### 4.5 Operator-owned helmfiles (owner decision 2)
A configuration may contain helmfiles for services kubementat does not install. Rules:
* Location: `<config>/environments/<env>/helm/<name>/helmfile.yaml` (and `helmfiles:` children). A service
  set is scoped to the cluster it serves, the same way `static.json` is; sharing one set across two
  environments is a `helmfiles:` reference, not a second copy.
* They are applied by `kmt helm apply <config-relative-path>` — a new, explicit verb — and **never** by
  `kmt install`, `kmt routing install`, or `kmt component install`, so no `kmt install` can surprise an
  operator by touching services kubementat was never told about.
* They live in their own working directory, so their relative value paths resolve inside the config.
* kubementat does not apply label filters to them and does not read their releases: `kmt component
  install` continues to answer from the product manifest only (§4.4), so a custom helmfile cannot make a
  component appear or vanish.
*Done when:* a test asserts `kmt install` applies nothing from `helm/`, and `kmt helm apply` applies
  exactly the named one.

---

## 5. Configuration repository layout

```
<config>/
  kubementat.yaml                      # version constraint, environments, teams, secrets stanza
  .gitattributes                       # written by kmt config init: *.encrypted.* filter=git-crypt
  .gitignore                           # git-crypt working material, helm artifacts
  environments/<env>/
      static.json                      # env answers (namespaces, domains, registry, storage class)
      static.encrypted.json            # env credentials — git-crypt protected
      values/<component>.yaml          # optional helm overrides, §4.2
      values.encrypted/<component>.yaml# optional encrypted overrides
      tekton/{tasks,pipelines,triggers,pipeline-runs}/     # env layer, §4.3
      helm/<name>/helmfile.yaml        # operator's own services, §4.5
      kubementat.yaml                  # environment-level override of the root manifest, optional
  teams/<team>/
      static.json
      static.encrypted.json
      <app>/values.encrypted.yaml      # the operator's app deployment values (unchanged semantics)
      tekton/{tasks,pipelines,triggers,pipeline-runs}/
  secrets/                             # only what is not a component answer; §8
  deployer_ssh_key / deployer_ssh_key.pub / gpg_*.key|gpg   # §8.4 — config material, not product
```

**R-5.1 (team identity).** A team is a directory under `teams/` that carries `static.json` — the existing
rule (`config.py:43`, `Config.teams()`), unchanged, so `kmt uninstall` still sweeps every real team and
ignores `helm/`, `tekton/`, and `values/` the same way it ignores `kubementat_components` today.
*Done when:* the existing `test_the_teams_are_the_directories_the_environment_configures.py` still passes
against the new layout.

**R-5.2 (backwards compatible names).** `environment` and `team` remain the CLI arguments, and file names
`static.json` / `static.encrypted.json` survive verbatim. The rename is the *root*, not the files, so an
operator's existing `static.json` is portable by `kmt config import` without editing keys.

**R-5.3 (the config is a git repo).** `kmt config init` requires it to be one (or initializes it).
This is a consequence of git-crypt, not a preference: git-crypt is a git filter, so §8's whole model needs
git. kubementat must still work when the directory is *not* a git checkout during a read — a read-only
deployment hands kubementat an unlocked working tree — but `init`, `import`, and `config set-secret`
require git and say so.

---

## 6. CLI surface

New group, registered like `kmt routing` and `kmt users` (`src/cli/kmt.py:144-151`):

| command | contract |
|---|---|
| `kmt config init <dir> [--environment E --team T] [--git-url U]` | Creates layout §5, runs git-crypt §8.3, generates keys §8.4, writes `kubementat.yaml`. Refuses a non-empty dir without `--force`, and never overwrites a file it did not write. One warning naming what it skipped. |
| `kmt config add-environment <dir> E` | Environment layer for a second cluster. No product templates. |
| `kmt config add-team <dir> E TEAM` | Team layer. |
| `kmt config set <dir> E key=value [--team T]` | Writes one answer to the right layer. Replaces the "export eight variables and re-run initialize" flow. |
| `kmt config show <dir> E [TEAM]` | Effective config, each key tagged `default`/`environment`/`team`. Read-only. |
| `kmt config diff <dir> E [--to <version>]` | What a different kubementat changes here: keys added/removed/renamed in the product defaults, values files whose product template drifted, Tekton objects the product now ships under a name the config also declares. Exit 1 when something would be lost. |
| `kmt config validate <dir> E [TEAM]` | Schema check, unresolved `SETME` values, git-crypt unlocked or not, layering cycles, duplicate names across layers that cannot both win. |
| `kmt config migrate <dir> E [--dry-run] [--to <version>]` | Applies the renames §10.2 lists. `--dry-run` is the default, and the default output is the values it *would* write, not a mutation. |
| `kmt config import <dir> <old-fork-dir>` | §13. |

Every existing `kmt` command gains `--config-dir`, defaulting per R-3.4.
*Done when:* `tests/test_the_cli_surface.py` asserts the group exists, that every command takes the
option, and that `kmt install --help` still does not offer a `--config`-shaped way to pass a file.

**R-6.1 (`kmt initialize` goes away).** Configuration creation is `kmt config init`; cluster installation
is `kmt install`. `initialize`'s remaining work — dependency check, key generation, template copy,
git-crypt — is redistributed across §6/§8/§11. It is deleted, not deprecated (project rule).
*Done when:* `kmt initialize` is unknown to the CLI, the CHANGELOG names where each of its nineteen
writes went, and no test invokes it.

**R-6.2 (the eight environment variables go away).** `KUBEMENTAT_ENVIRONMENT_CONFIGURATION`
(`automation.py:215`, 8 keys: `BASE_DOMAIN`, `AUTOMATION_GIT_URL`, `AUTOMATION_GIT_SERVER_HOST`,
`AUTOMATION_GIT_SERVER_PORT`, `AUTOMATION_GIT_SERVER_SSH_USER`, `KUBERNETES_DEFAULT_STORAGE_CLASS`,
`DOCKER_REGISTRY_BASE_URL`, `CLUSTER_MANAGER_EMAIL`) becomes configuration read from
`environments/<env>/static.json`. The only env vars kubementat continues to read are documented overrides
(`KUBEMENTAT_HOME`, `KUBEMENTAT_PLATFORM_CONFIG_DIR`) and what helmfile already exports for itself.
*Done when:* `_check_required_environment_variables` is gone and a missing required answer arrives as one
refusal naming the key, the file, and `kmt config set`.

**R-6.3 (git-crypt is a declared dependency of the verbs that use it).** `KUBEMENTAT_DEPENDENCIES`
(`automation.py:202-213`) names `git` and `gpg` but not `git-crypt`, while `initialize` execs it. The
dependency guard is per-verb from here: `config init`/`import`/`set` check `git-crypt`; `install` does not
care whether the config happens to be unlocked. This follows the rule the ledger row 8 established —
the list describes what the command runs.

---

## 7. The `--config-dir` contract

`KUBEMENTAT_PLATFORM_CONFIG_DIR` already exists (`config.py:36`) and already wins over the
`kubementat_main_dir/platform_config` default (`config.py:166-173`). It is not honoured where it matters:
* `initialize` runs `uv sync --all-extras` **in the product directory** (`automation.py:311`) — so a run
  with the variable set still needs a checkout, which is exactly the D11 failure the code comments say to
  avoid.
* `initialize` runs `git-crypt init`/`export-key` in the product directory (`automation.py:613-625`).
* Tekton deploy tasks read config from a path built from the cloned fork
  (`tekton_ci/tasks/deploy-local-helm-chart.yml:128`).

**R-7.1.** After this spec, no code path in `src/` derives a configuration path from `kubementat_main_dir`.
*Done when:* `grep -rn "kubementat_main_dir.*platform_config" src/` is empty, and a unit test drives a
whole `kmt install` with `kubementat_main_dir` pointing at a directory that contains **no**
`platform_config/`.

---

## 8. git-crypt, moved

### 8.1 What leaves the product repository
* `.git-crypt/` — 18 tracked files, 17 of them collaborator GPG identities (`git ls-files .git-crypt`).
* The `.gitattributes` line `*.encrypted.* filter=git-crypt` (`.gitattributes` keeps excluding `*.template`
  so product templates stay plaintext and reviewable).
* `src/kubementat/crypto_utils.py`'s keyring/keystore handling — retained only for what §8.4 needs.

*Done when:* `git ls-files .git-crypt .gitattributes` in the product shows no git-crypt filter and no key
files, and the product repository is usable by someone who has never heard of git-crypt.

### 8.2 What `kmt config init` writes
```
<config>/.gitattributes      *.encrypted.* filter=git-crypt diff=git-crypt
                             *.kmt-template !filter !diff
<config>/.gitignore          deployer_ssh_key*, gpg_*, /helm/**/.helmfile/, kubementat.yaml.lock
```
`*.kmt-template` is the product-template escape hatch: kubementat writes a template into the config **only**
when the operator explicitly asks (`--template`), and the negation keeps it out of the filter so the
template stays diffable. Per R-3.2 the default is that kubementat writes none of these.

### 8.3 The initialization dance (owner decision 5: complete, in `init`)
Idempotent, in this order, each step skipped with a one-line reason when already true:
1. `git init` (if `<config>/.git` absent) — R-5.3.
2. `git-crypt init` in `<config>`.
3. Key generation per §8.4.
4. `git-crypt add-gpg-user <deployer-email>` (and `--gpg-user E` for each `--gpg-user`).
5. `git-crypt export-key <config>/git_crypt_symmetric.key`, chmod 0600, added to `.gitignore`, and printed
   once with the instruction that this file is the ability to read every credential in the cluster.
6. `.gitattributes` + `.gitignore` written **before** any encrypted file exists, so the first `git add`
   applies the filter.
7. Commit `chore: initialize kubementat configuration` (unless `--no-commit`).
8. Summary: file list, what to do next (push to `--git-url`, add the deployer key to the git host, run
   `kmt install`).

Exit codes: 0 on success or on "nothing to do", 1 with a named path when a step's precondition fails.
*Done when:* a test runs `init` twice on the same directory and the second run writes nothing and says
which step it skipped, and a test asserts a file written before step 6's `.gitattributes` is *not* encrypted.

### 8.4 Keys belong to the configuration
The deployer SSH keypair and the deployer GPG keypair are generated into the **config** directory, not
`kubementat_main_dir` (`automation.py:283-294` does the latter today, which is how cluster credentials
ended up inside the product tree). They are still needed: CI clones the config repository over SSH, and
git-crypt needs the GPG identity.
*Done when:* `kmt install` on a config with no deployer key refuses with the path it looked in and the
`kmt config` command that creates it.

### 8.5 The one seam (recorded, not built)
`Config._load_json_file` and `HelmUtils.helmfile_apply`'s credential read (`helm_utils.py:74`,
`:442` — `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`) are the two places a secret is decrypted and used.
The only requirement on future work is that both go through **one** `read_decrypted(path)` function, so a
provider other than git-crypt can be added without touching layering.

### 8.6 What the operator's `.gitattributes` must keep working
Files named `*.encrypted.yaml`/`*.encrypted.json` continue to be the secret format. Product templates
named `*_values.encrypted.yaml.template` stay templates and stay excluded, so an operator's decrypted
working tree and the product's plaintext templates do not disagree about what is encrypted.

---

## 9. Operator-owned Tekton objects — detail

* `<config>/environments/<env>/tekton/{tasks,pipelines,triggers,pipeline-runs}/` and
  `<config>/teams/<team>/tekton/…` per §4.3.
* Custom PipelineRuns live in the config, not `tekton_ci/pipeline-runs` (`TODOs.md:1007`).
  `Config.tekton_team_pipeline_run_dir` moves accordingly; the product's `examples/` stay product-side.
* Generated manifests (the trigger/pipeline generators, FIN-22/23) write into the config and read from
  the same layer, so generation and reading cannot disagree — the requirement `docs/plans/finish-python-migration-last-lap.md`
  puts on FIN-22/23.
* kubementat applies what it ships; it does **not** prune operator objects it no longer ships. (The
  "no pruning" ruling from ledger row 11b carries over: deleting something the operator wrote is not a
  kubementat decision.)
*Done when:* the lane that runs `kmt tekton setup-pipelines` reports both a product Task and a config
Task of the operator's own by name, and the unit suite proves the name-match precedence without a cluster.

---

## 10. Staying current (the B axis)

### 10.1 The version stamp
`kubementat.yaml`:
```yaml
kubementat: ">=0.6,<1"        # the constraint the config was last validated against
config_schema: 1              # bumps when kubementat changes what a key means
environments:
  dev: { teams: [dev1] }
secrets:
  provider: git-crypt         # v1: git-crypt only
```
**R-10.1 (warn and continue, owner decision 4):** when the installed version is outside the constraint,
every command warns once with both versions and the `kmt config diff` hint, and continues.
`kmt config validate` and `kmt config diff` are the commands that exit non-zero. This mirrors
`_component_value`'s warn-once philosophy (`config.py:405`) and avoids a tool that cannot run a working
cluster because a version string drifted.

### 10.2 Migrations
`config_schema` bumps carry a declarative rename/reshape table shipped with the product. `kmt config
migrate` applies it, `--dry-run` default, printing the file diff. Precedent for the shape of the tool:
the values-migration plugin that "does not apply any changes to the release … simply outputs the values
to override" ([helm-migrate-values](https://github.com/OctopusDeployLabs/helm-migrate-values)), and
`kubeadm upgrade plan` comparing desired against current before touching anything.
*Done when:* a test bumps `config_schema`, runs `migrate --dry-run`, asserts the file is unchanged, then
runs `migrate` and asserts the key renamed.

### 10.3 What makes most upgrades free
R-3.2: with no copies in the config, a product change that adds a default, adds a Tekton Task, adds a
release, or changes a namespace default needs **no operator action at all** — layering applies it.
`config diff` then exists to surface the cases that do need a decision: renames, deletions, and name
collisions where the operator's override silently wins forever.

### 10.4 The mechanism, not the intention
The kubementat release pipeline opens a bump PR (one line: the version constraint) against known
configurations. That is the piece that makes "stay updated" an inbox item rather than a discovery
problem — same shape as Flux's image update automation writing to Git
([flux image updates](https://fluxcd.io/flux/guides/image-update/)), taken without adopting the
controller.

---

## 11. Product packaging (prerequisite)

**R-11.1.** The distribution ships its assets. `templates/` (19 files), `tekton_ci/tasks|pipelines` (41),
`scripts/` (2), `helm_charts/` (1) are package data, resolved through `importlib.resources` — today
`uv build --wheel` yields 37 entries and **every one of them is `.py`**, which is why a "tool install" does
not exist and `KUBEMENTAT_HOME` must be a checkout.
*Done when:* `uvx --from ./dist/kubementat-*.whl kmt install <config-dir> dev dev1` works in a directory
with no checkout, and a test asserts the packaged manifest resolves every asset path that `constants.py`
names.

**R-11.2.** No runtime dependency installation. `install_python_dependencies()` (`automation.py:308-313`)
is deleted: a released tool does not `uv sync` itself into its caller's directory.
*Done when:* `grep -rn "uv.*sync" src/` is empty.

**R-11.3.** The product is pinned where it runs: the CI container image tag and the Tekton `image`
params carry the kubementat version, so "which kubementat ran this deploy" is answerable from a
PipelineRun.

---

## 12. CI / Tekton: two sources instead of one fork

Today a pipeline clones `AUTOMATION_GIT_URL` — the operator's fork of the product — and reads code and
configuration from that one workspace (`tekton_ci/tasks/deploy-local-helm-chart.yml:9-26,128`). After this
spec:

```
source A: product  = the pinned kubementat image (§11.3)
source B: config   = git-clone of the configuration repo, ref = the commit being deployed
workspace: /workspace/config   ← path-to-configuration
           kubementat binary + assets, from the image
```

**R-12.1.** `deploy-local-helm-chart.yml`'s `path-to-configuration` default becomes the cloned config
workspace, not `<clone>/kubementat`.
**R-12.2.** The eight `AUTOMATION_GIT_*` / `GIT_DEPLOYER_PUBLIC_KEY` keys in
`templates/environment/static.json.template` (8 of its 37 keys) stop describing where the *product* lives.
`AUTOMATION_GIT_URL`, `AUTOMATION_GIT_SERVER_HOST/PORT/SSH_USER`, `AUTOMATION_GIT_REVISION`,
`AUTOMATION_GIT_PROJECT_NAME` describe the **configuration** repository and its git ref;
`GIT_DEPLOYER_PUBLIC_KEY`/`GIT_DEPLOYER_GPG_PUBLIC_KEY` are §8.4's deployer keys.
**R-12.3.** CI unlocks the config with the symmetric key exported in §8.3 step 5, passed as a credential
to the git-clone/`git-crypt unlock` step. It is not stored in the workspace.
*Done when:* a pipeline run proves product and config arrive from different sources — the deployed
component set comes from the image's manifest, and the values come from the cloned repo.

---

## 13. Migration of existing trees

**R-13.1 `kmt config import <target> <source>`** moves a combined checkout (an operator's fork) into a
configuration repository:
1. `git init` + git-crypt dance (§8.3) in `<target>`.
2. Move `platform_config/{env}/{team}` → `teams/<team>`, `platform_config/{env}/static*.json` →
   `environments/<env>/`, and the operator's `tekton/`, `triggers/` under the layer dirs.
3. Move operator content that is not kubementat's (`utilities/custom/`-style scripts, `terraform/`, and
   any other directory that appears only in the fork) into `<config>/custom/<original-path>` **and report
   it**, because kubementat does not know what it is and must not silently decide it is configuration.
4. Delete every file that is byte-identical to the product's default of the same purpose (the copies
   R-3.2 forbids) — **only if unmodified**; anything edited lands in a report with a `--keep` opt-out.
   Deleting a year-old hand-edited helmfile is not kubementat's decision to make unasked.
5. Report: what moved, what stayed, what is still a copy, what must be decided.
*Done when:* run against a real fork it reports the number of files deleted as redundant copies versus
kept as overrides, and `kmt install` on the imported directory applies the same component set it applied
from the fork.
**Evidence target:** the Krone-style fork (63 `platform_config/` files, 205 `tekton_ci/` files, own code
under `utilities/`) is the reference case; the migration is only "done" when such a tree imports and
installs without a merge.

**R-13.2 (in-place operators).** A tree that stays one repository keeps working through the existing
default (`kubementat_main_dir/platform_config`) — R-3.4 preserves it. Deprecating that default is a
separate, later decision and is not implied by this spec.

---

## 14. Spike required before tickets

**SP-1 — helmfile path resolution across a product base and a config overlay.** Does a relative path
inside a `bases:`-referenced base resolve against the base's directory or the entry state's directory?
The layout of §5 and §4.2 depends on the answer; if it resolves against the entry point, the product's
helmfile needs an absolute-path variable (`KUBEMENTAT_PRODUCT_DIR`) injected by `kmt`, which is fine but
must be known before the helmfile rewrite is written. Half a day, one experiment.

**SP-2 — `missingFileHandler` scope** (per-release vs templates vs global) and whether `Warn` silences a
genuinely missing product default. Same experiment.

---

## 15. Test strategy

* **Unit (default `uv run pytest -q`, no cluster).** Precedence per surface (§4), config-root resolution
  (R-3.4), layout/validate (R-3.2), name-match Tekton precedence (§4.3, §9), version warn-vs-fail (R-10.1),
  migration renames (R-10.2), import reports (R-13.1), git-crypt idempotence (§8.3), packaged assets
  (R-11.1).
* **Lane (`tests/e2e/suite.py`).** The two that matter: **upgrade lane** — configure at `N`, install,
  switch to `N+1` with a one-line change, `config diff`, install, assert the component set and that the
  operator's own files are untouched; **fork-import lane** — clone a combined tree, `config import`,
  install, deploy one app through Tekton, assert §12's two-source provenance.
* Test names describe behaviour, never ticket ids (`tests/test_test_name_conventions.py`).

---

## 16. Phasing, with gates

| # | Deliverable | Gate to move on | Breaks an existing tree? |
|---|---|---|---|
| 0 | Assets in the wheel, `importlib.resources`, `uv sync` deleted (R-11) | `uvx … kmt install` runs without a checkout | no |
| 1 | `--config-dir` honoured everywhere, no path derived from `kubementat_main_dir` (R-7) | the `grep` of R-7.1 is empty + the "no platform_config next to the binary" test | no |
| 2 | Layering read path: JSON, values, Tekton name-match (R-3, R-4) | precedence tests green; a config with zero files installs the product defaults | no — copies still work, as overrides |
| 3 | `kmt config init/show/validate` + layout (R-3, §5, §6) | TD-1's "no file kubementat wrote for editing" holds | no |
| 4 | Product-owned helmfile entry point, values layering (R-4.4, §4.2) | plan row 13's scenario can no longer be constructed | **yes** |
| 5 | git-crypt out of the product, keys into config (R-8) | product repo passes a clean-clone test with no git-crypt | **yes** for config repos that relied on repo-scoped git-crypt |
| 6 | CI two-source model, `AUTOMATION_GIT_*` reshaped (R-12) | §12's provenance test | **yes** |
| 7 | `kmt initialize` deleted, `config init` its replacement, env vars deleted (R-6.1, R-6.2), `kmt config import` (R-13) | a fork imports and installs; `kmt initialize` unknown | yes |
| 8 | `kmt config diff/migrate` + release-pipeline bump PRs (§10) | TD-1 measured end to end | no |

Phases 4-7 are one minor-version line with `config diff` as the documented migration tool; they should not
be split across seven deprecation branches (project rule: no aliases, one name per deletion).

---

## 17. Requirements traceability

| Coupling (measured) | Removed by |
|---|---|
| Config is a subdir of the product (`config.py:166`) | R-3.4, R-7.1 |
| Product assets not shipped (wheel = `.py` only) | R-11.1 |
| Helmfile lives in the config dir (`helm_utils.py:96,229`) | R-4.4 |
| CI clones the fork for code and config (`deploy-local-helm-chart.yml:128`) | R-12.1, R-12.2 |
| git-crypt keys tracked in the product (18 files) | R-8.1 |
| Answers from eight exported vars (`automation.py:215`) | R-6.2 |
| Operator edits copied product shape (19 paths) | R-3.2, R-3.3 |
| Product runs `uv sync` / `git-crypt` in the checkout (`automation.py:311,613`) | R-11.2, R-8 |
| Cluster credentials in `kubementat_main_dir` (`automation.py:283-294`) | R-8.4 |
| Operator cannot own pipelines/tasks/triggers | §4.3, §9 |

---

## 18. Open questions

1. **Phase 5's unlock contract in CI** — symmetric key as a Tekton secret, or a `git-crypt unlock` step
   that reads it from the cluster's Vault (§18 candidate)? The former is what today's
   `git_crypt_symmetric.key` practice supports.
2. **Does `kubementat.yaml` live at the config root, per environment, or both** (root defaults, environment
   override, merged)? §5 shows root plus optional per-environment; needs a decision on whether `teams:`
   is declared there or derived by directory scan as `Config.teams()` does today.
3. **Custom helmfile discovery** — explicit `kmt helm apply <path>` (spec default), or also an
   `enabled: true` registry so `kmt install` can apply an operator's service set as part of one command.
   The owner's answer to Q1 ("simplifying installation of a set of services") may want the registry.
4. **Are `helmfile.yaml.gotmpl` overrides ever required for the product components**, or can every
   operator need be expressed as values + the `installed:`/`version:` knobs? If overrides are ever
   mandatory, R-3.2 needs a documented exception with a `config diff` warning.
5. **Phase 5's fate for existing fork users** — do we offer `config import` as the only path, or also keep
   the single-repo mode alive indefinitely (R-13.2)?

---

## 19. Sources

**In-tree:** `src/kubementat/config.py:36,41,79,166-173,405`;
`src/kubementat/automation.py:215,278-295,308-313,315-352,613-625,908-911`;
`src/kubementat/helm_utils.py:74,96,213-235,442`; `src/cli/commands/kubementat.py:7-12`;
`src/cli/kmt.py:144-151`; `templates/environment/static.json.template`;
`tekton_ci/tasks/deploy-local-helm-chart.yml:9-26,128`; `pyproject.toml`; `docs/plans/finish-python-migration-last-lap.md`
rows 8/11b/13/14; `TODOs.md:937,999-1013,1061-1062`; `git ls-files .git-crypt` (18 files);
`uv build --wheel` → 37 entries, all `.py`; asset counts 19/41/2/1 (templates/tekton_ci/scripts/helm_charts).

**Prior art:** [helmfile writing guide](https://raw.githubusercontent.com/helmfile/helmfile/main/docs/writing-helmfile.md)
· [helmfile configuration (remote states)](https://github.com/helmfile/helmfile/blob/main/docs/configuration.md) ·
[shared configuration across teams](https://helmfile.readthedocs.io/en/latest/shared-configuration-across-teams/) ·
[helm values best practices](https://github.com/helm/helm-www/blob/main/version-3/chart_best_practices/values.md) ·
[helm-migrate-values](https://github.com/OctopusDeployLabs/helm-migrate-values) ·
[kubeadm upgrade](https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-upgrade/) ·
[kubespray getting started](https://github.com/kubernetes-sigs/kubespray/blob/master/docs/getting_started/getting-started.md) ·
[Google GitOps best practices](https://cloud.google.com/kubernetes-engine/enterprise/config-sync/docs/concepts/gitops-best-practices) ·
[Flux image update automation](https://fluxcd.io/flux/guides/image-update/) ·
[git-crypt](https://github.com/AGWA/git-crypt) ·
reference fork `/home/verfeinerer/dev/freelancer/krone/dev_kubementat` (824 commits).
