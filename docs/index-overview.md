# Kubementat Codebase Overview

## Project Snapshot

**Kubementat** is an all-in-one open-source platform for enabling dev teams to configure, maintain, build, and deploy on Kubernetes. It combines CLI automation, Tekton CI/CD pipelines, Helm-based platform components, and encrypted platform configuration. (No service mesh: kubementat dropped Linkerd on 2026-10-28, and `kmt uninstall` sweeps a mesh a cluster may still be carrying.)

- **Version:** 0.4.0 | **Python:** ≥3.9 | **Package manager:** uv
- **CLI:** Click-based (`kmt`), 27 entries — 20 flat and 7 groups (`component`, `environments`, `helm`,
  `routing`, `tekton`, `tunnel`, `users`); measured with `uv run kmt --help | grep -oE '^  [a-z][a-z0-9-]*' | sort -u | wc -l`.
  `environments` is the newest of them (`kmt environments list`), and every command takes `environment`
  first — see `docs/README_PLATFORM_CONFIG.md`
- **Core libs:** `kubernetes` client, `cryptography`, `python-gnupg`, `pyyaml`
- **CI/CD:** Tekton (pipelines, tasks, triggers) with a custom CI container
- **Security:** git-crypt for encrypted platform configs, SSH/GPG keys for deployer auth
- **Install:** `uv sync` → `uv run kmt config init <dir> --environment dev --team smoke --gpg-email <you>` → `uv run kmt install dev smoke --config-dir <dir>`

---

## Directory Summaries

### cli/ (1 entry point, 7 Click groups)
Click-based CLI, counted on `uv run kmt --help` — **27 entries**, 7 of which are groups (`component`,
`environments`, `helm`, `routing`, `tekton`, `tunnel`, `users`) and 20 of which are flat commands. The
`environments` group is the new one: `kmt environments list` is what answers "what environments do I have, and
what cluster is each one" from configuration alone. **Every one of those 27 takes `environment` as a
positional or an `ENVIRONMENT` envvar, and nothing about the cluster, the team or the environment has a
default any more** — `--team` is a `team_option()` on four commands and nothing else. **This section once described a 5-module,
18-command, flat CLI that delegated to shell scripts via `subprocess.check_call()`; that is the 2026-06
tree.** Two commands still start a process (`kmt tekton setup-pipelines`, whose `TektonUtils.install()`
applies the release YAMLs, and `kmt run-ci-container`, which runs `kubectl run` against the CI image);
everything else goes through `kubementat.shell.run`, and `subprocess` is imported in no module but
`kubementat/shell.py`. The `tunnel-vault-ui` naming bug is fixed (`def tunnel_vault_ui`, registered) and
the duplicate `kmt` entry point was deleted — `src/cli/kmt.py` is the only one. See
`docs/plans/finish-python-migration-last-lap.md` for the current ticket state.

### docker_files/ (4 files, 1 CI image)
Single Docker image `ubuntu-ci-minimal` — the universal CI worker for all Tekton pipeline tasks. Contains 15+ tools (kubectl, helm, helmfile, tkn, linkerd, yq, skopeo, git-crypt, mysql-client, python3). Multi-arch (amd64/arm64). Deeply integrated with Tekton CI pipelines. No multi-stage builds.

### src/ (31 Python modules, 12,350 LOC — this directory was `lib/` until FIN-01 moved it; measured with `find src -name '*.py' -not -path '*egg-info*' -not -path '*__pycache__*' -exec wc -l {} +`)
Core library. `automation.py` is the largest module at 2,504 lines (`wc -l`), handling initialization, installation, secrets, tunnels, and uninstall. `kubernetes_utils.py` (1,590) wraps the kubernetes client for namespaces, secrets, service accounts, RBAC, custom resources, and port-forwarding. `tekton_utils.py` (2,274) handles pipeline listing, running (with placeholder replacement), and cleanup. `config.py` loads encrypted/encrypted JSON configs from `platform_config/`. `helm_utils.py` wraps helmfile apply. `crypto_utils.py` generates SSH/GPG keys. `utils.py` is deleted (FIN-08) — the body of this section described a 7-module, ~2,000-line `lib/`, and that is the tree before FIN-01.

### platform_config/ (2 environments, team-based hierarchy)
JSON config files organized as `platform_config/{env}/{team}/`. Two levels: environment-level (`static.json`, `static.encrypted.json`) and team-level. Nothing in this tree is a copy of a shipped file: 11
values files and one helmfile manifest ship inside the installed `kmt` under
`src/kubementat/core/assets/values/` and are applied from there. The 7 `.template` files under
`src/kubementat/core/assets/templates/` are **not copied into this tree by any verb** — that copy went with
`kmt initialize`, so a file that differs here is the operator's answer and not a stale copy. They are
not all doing the same thing, and the difference is measured rather than assumed:

* **Two are witnesses.** `environment/static.json.template` and `environment/team/static.json.template`
  are opened by `tests/test_the_defaults_initialize_seeded_have_a_single_owner.py`, which checks each of
  the ten `constants.DEFAULT_STATIC_ANSWERS` against the template's own line for it. Nothing in `src/`
  reads either file, so the test is the only thing that catches `constants.py` drifting from the shape
  it is supposed to describe. That is the reason they stay.
* **Five are unused.** `mirrored_docker_images.json.template` is never written at all (R-3.2 — the
  product resolves image mirrors at read time), and the remaining three are shapes nothing opens. The
  `REPLACEME_` markers they carry are no longer rewritten by anything; `config validate` still
  recognises the prefix, which is what stops a placeholder reaching a manifest if one ever did.

Deleting a template is a decision, not a side effect, so nothing here is deleted. `mirrored_docker_images.json` for image mirroring. git-crypt encrypted. Team configs include MySQL/Redis passwords, SSH deploy keys, webhook secrets, Docker registry credentials.

**No git server is in that surface any more** (2026-10-29): kubementat neither installs nor uninstalls one, and a team that wants one deploys it as a normal helm release, with its own values file and its own name. The `GITLAB_WEBHOOK_SECRET` a team config still carries belongs to the Tekton triggers and is not a component — see `docs/README_TEKTON.md`.

### scripts/ (deleted — no directory, 0 shell scripts)
Measured here: `ls scripts` → **No such file or directory**, and `git ls-files scripts` → nothing, so
it is in no tree of this repository's history. `retrieve_node_addresses.sh` and
`view_cluster_resource_versions.sh` were the last two and are deleted (2026-10-30, `504af9a`, FIN-09 /
D12); the two `kubectl` reads they wrapped are answered by primitives instead —
`KubernetesUtils.get_nodes()` returns `V1NodeAddress` objects rather than JSON text, and
`KubernetesUtils.get_api_resources()` the API list — so `jq` leaves `KUBEMENTAT_DEPENDENCIES` with them
and a guard fails if a `.sh` reappears under `scripts/` under any name.

The directory used to be left on disk empty, which git does not track; a reader following an older
README into it got a shell error and no hint. What is left is nothing.

The paragraph below is the 2026-06 description of this directory and is kept as what it was — a large shell collection that did config parsing with `jq`/`yq` and wrapped `kubectl` by hand. None of it is true of the two files above, and the corrections after it are the record of how they got here.

**2026-10-28 correction.** The two generator scripts this section used to name —
`generate_deploy_pipeline_from_template.sh` (71) and `generate_trigger_config_from_templates.sh` (123) —
are deleted. `kmt tekton-generate-deploy-pipeline` and `kmt tekton-generate-triggers` are their
replacements (`src/cli/commands/tekton.py`, `TektonUtils.generate_deploy_pipeline()` /
`generate_trigger_configs()` in `src/kubementat/tekton_utils.py`), and neither script had a caller
anywhere in `src/`, which is why a command is the replacement and not a library call. The count is now
**the 12 scripts and 717 LOC of that date** under `scripts/`, not ~60. It has moved
twice since: the pair below took one, and `run_pipeline.sh` (FIN-51b, `85f1ea8`) took the rest. The
number to act on is in the "current measure" line further down and in §8 of
[`docs/plans/finish-python-migration-last-lap.md`](plans/finish-python-migration-last-lap.md).

**2026-10-29 correction.** Two more went, and this time they were orphans rather than migrations.
`scripts/automation/components/helmfile_apply.sh` is deleted — nothing execs it, because
`HelmUtils.helmfile_apply` already assembles the `helmfile apply` argv that `kmt install`,
`kmt routing install` and `kmt helm apply` run, and the two scripts that used to `source` it
(`install_routing.sh`, `install_kubernetes_dashboard.sh`) are gone. Its directory is empty and went
with it, along with the `HelmUtils.components_dir` that pointed at it.
`scripts/secret_management/generate_ssh_key.sh` is deleted too: `CryptoUtils.generate_ssh_key_pair`
behind the directory-preparation module is the implementation, and it is the only caller. Both deletions are held by
stays-deleted guards rather than by memory — see
`tests/test_the_helmfile_argv_is_built_in_python_and_the_script_stays_deleted.py` and
`tests/test_the_deployer_ssh_keypair_is_written_by_the_library_and_the_script_stays_deleted.py` —
and the ledger rows that name the tickets are §2.1 and §7 of
[`docs/plans/finish-python-migration.md`](plans/finish-python-migration.md).

`base64_library.sh` and `create_secret_from_json.sh` are both **deleted** (FIN-51). Neither was
an easy deletion and one of them was a claim that needed re-reading: the library had exactly one
consumer left, `create_secret_from_json.sh`, which `source`d it and never called the one function
it defines, so deleting the script orphaned the library rather than the reverse. The script itself
is `kmt create-secret-from-json` now, which is what the Tekton task that ran it calls inside the
pipeline pod. Both deletions are held by stays-deleted guards —
`tests/test_the_secret_json_scripts_are_gone_and_the_task_runs_the_command.py` — and the pair takes
the empty `scripts/secret_management/` directory with them. `user_management/` no longer exists
(FIN-35) and `tunneling/` no longer exists (FIN-46).

**2026-10-29 correction — the script under `scripts/tasks/` went with the Task that ran it, and this
time there is no replacement command behind it.** `scripts/tasks/setup_mysql_databases.sh` and
`tekton_ci/tasks/run-automation-script-for-environment.yml` are both deleted (FIN-51c): the script's
only route to a cluster was that Task, and the Task's whole job was to execute a path a pipeline
handed it, which is the shape kubementat does not offer — in the CLI or in CI. What stays does its own
job, and that is the other half of the same sentence: `create-mysql-database.yml` and
`execute-mysql-file.yml` assemble `mysql -h … -e` inside their own step, so they need nothing handed
to them and they survive. Retired rather than migrated, and said in the present tense because both
halves of it are now absent: **there is no longer any way for kubementat to create databases and
users from configuration** — the key that row configured, `MYSQL_DATABASE_CONFIGURATION`, left
`templates/environment/team/static.encrypted.json.template` and `automation.py` first (FIN-52) and the
script that read it goes here, so no key declares it and nothing reads it. A pipeline that needs
something custom writes a `script:` step inline; the two MySQL Tasks create what their own parameters
name. Both deletions are held by stays-deleted guards —
`tests/test_no_task_takes_a_script_path_and_the_pair_stays_deleted.py` — and the empty
`scripts/tasks/` directory went with the script. Current measure at the time of that correction:
**3 scripts / 151 LOC across 2 subdirectories** (`find scripts -name '*.sh' | wc -l` → 3, in
`automation/tekton/` and `utilities/kubernetes/`); `jq` was named by 2 of them and `yq` by 1. Both
numbers are stale one step further on and were relabelled rather than rewritten: **now** it is
**2 scripts / 4 LOC across 1 subdirectory** (`find scripts -name '*.sh' | wc -l` → 2, in
`utilities/kubernetes/` alone), `jq` named by 1 and `yq` by none. What took the third script was
`run_pipeline.sh`, deleted by FIN-51b (`85f1ea8`), and what took the second subdirectory was the
deletion of `scripts/automation/` with it. So the 151 LOC this line used to call current was a
correct measurement of a smaller tree, not a wrong one.

### docs/ (4 subdirectories, 31 Markdown files — measured with `find docs -name '*.md' | wc -l`)
The design record. Four kinds of document live here, and which one a reader needs depends on whether they are
asking *what happened*, *what we measured*, *what we decided*, or *what we plan to build*:

- **`docs/plans/` — 6 files.** The execution documents: `finish-python-migration.md` (the ticket ledger) and
  its `…-last-lap.md` follow-on are the ones a ticket is read out of.
- **`docs/research/` — 10 files.** Evidence. Each names the date and the sources it measured, and a plan
  contradicts these rather than ignoring them — `research-config-code-separation-10-29-2026.md` and
  `specification-externalize-configuration-10-30-2026.md` are that pair: the measurement, then the chosen
  design built on it.
- **`docs/specifications/` — 1 file, `cluster-config-v2.md` (2026-11-01).** The first document of this kind:
  a *decision* spec rather than a plan, so it records what the owner decided and the evidence for each decision,
  and deliberately does **not** schedule work or size tickets. It is the design for environments-as-clusters and
  multi-cluster addressing, and it supersedes the deferrals `finish-python-migration.md` §1.2 and `TODOs.md`
  Features both carry. Read it before touching anything in `kubernetes_utils.py`, `automation.py`'s cluster
  paths, or any command signature.
- **`docs/finished-tickets/` — 5 files.** `T001`…`T005`, the small hygiene tickets recorded individually.

The seven `docs/README_*.md` files alongside them are operator-facing (configuration, Tekton, E2E lanes,
git-crypt, monitoring, additional tools, development flow), and `gpg_key_generation.md` is a recipe. These are
the ones that drift: `README_PLATFORM_CONFIG.md:3-4` defined an environment as a directory of configuration,
which `cluster-config-v2.md` §15 listed as a known casualty — and the casualty is now paid. That file opens
with the rule instead: an environment is exactly one cluster, enforced, and `CLUSTER_KUBE_CONTEXT` is what
makes it true. Read that paragraph before any other part of the operator docs; the rest of them assume it.

### `tekton_ci/` — split two ways (10 pipelines, 20 tasks, 8 trigger templates, 4 pipeline runs — measured 2026-10-29 by directory: `pipelines/` 10, `tasks/` 19, `triggers/` 8, `pipeline-runs/` 4; re-measured 2026-11-02 with `find src/kubementat/assets/tekton_ci -name '*.yml'` → 37 plus `find tekton_ci -name '*.yml'` → 4; re-measured 2026-11-04 with the same two commands → 38 plus 4 (both of those commands ran against `src/kubementat/assets/`, the path that held these files until `core/` moved them, so the same find under `src/kubementat/core/assets/tekton_ci` answers the same 38 today), the extra one being `tasks/git-crypt-unlock.yml`, which §10 R-10.3 added so a cloned configuration is opened by a step of its own and not by the clone)
The counts held at 37 until §10 R-10.3. `src/kubementat/core/assets/tekton_ci/{tasks,pipelines,triggers}` is product data and ships inside the package (T-02b), while `tekton_ci/pipeline-runs/` stays here, under the checkout, because a pipeline run is output a command writes and reads back and a half-written manifest inside site-packages is the bug R-12.1 names. So `find tekton_ci -name '*.yml'` at the repository root answers 4 and not 42; the other 38 are found under `src/`.
The **tasks** number moved because of this repo's own work: `run-automation-script-for-environment.yml`
was deleted on 2026-10-29 and the directory held 20 until then
(`git ls-tree HEAD~1 --name-only tekton_ci/tasks | wc -l` → 20). The `16 pipelines` in the old line never matched what is on disk — it reads
10 — and `25 tasks` never matched what was on disk, which was 20 before this deletion. `examples/tekton_ci/` is a separate tree with 4 pipelines, 1 task and 8 pipeline-run
files of its own, which is where an inventory that counts both trees has to look if it wants one number.
The two counts that agree with `find` — 8 trigger templates and 4 pipeline runs — are left as they were.
Complete Tekton CI/CD system. Tasks include git-clone (with SSH auth), Docker build/push, helmfile CLI, MySQL operations, skopeo image operations, terraform, and echo. Pipelines orchestrate Docker builds, Helm deployments, image sync, and a hello-world example. Trigger templates for GitHub and GitLab webhooks. Pipeline runs with placeholder replacement. (The MySQL/Redis setup pipelines went with the backing-service install automation, removed 2026-10-27; the two MySQL Tasks remain.)

### `kubementat/core/assets/templates/` (7 template files, measured with `find src/kubementat/core/assets/templates -type f | wc -l`; 19 until T-18 moved the ten files under `values/` out, and 8 until `kubementat_components/` went)
JSON/YAML templates for initialization, and **product data** since T-02a: they ship inside the installed `kmt`, so no checkout carries a directory of that name for a sentence to point at. `environment/static.json.template` and `static.encrypted.json.template` describe a layer's shape. `environment/team/` templates for team configs. `mirrored_docker_images.json.template`. What `kmt initialize` did with them was copy them into `platform_config/{environment}/` — the copy is the operator's working material, the template is what the package ships — **except** the helmfile manifest and its default values, which T-18 moved to `assets/values/` and took out of the copy altogether: they are applied and read from inside the package. The command is deleted and the copy with it, so no verb reads these files any more; deleting a template is a decision and not a side effect. `environment/kubementat_components/` is now absent, not empty: it held one file after T-18, `monitoring/grafana_node_service.yaml.template`, a `Service` that exposes Grafana's UI on a NodePort and was applied by hand with a `kubectl apply` comment. Nothing in `src/`, no manifest, no test and no pipeline ever read it, and `grafana_client.py:521` already names a NodePort of that shape as the address kubementat refuses to guess, so it was deleted on 2026-11-03 rather than shipped as an undocumented manual step. Exposing the UI is a port-forward or the service's ClusterIP.

### `kubementat/core/assets/values/` (11 files, measured with `find src/kubementat/core/assets/values -type f | wc -l`)
The product's helmfile entry point plus the ten `*.encrypted.yaml` defaults its releases name, one directory per component, and **data rather than templates**: there is no `.template` suffix here because nothing renders or copies these files. They sit together because helmfile resolves a release's relative `values:` path against the directory of the manifest it was handed. `HelmUtils.helmfile_paths()` returns this directory and its `helmfile.yaml.gotmpl`, `Config.helmfile_asset_dir` is the accessor, and `tests/test_the_values_files_the_manifest_names_are_the_ones_the_tree_ships.py` is the guard that keeps the reference list and the file list one set.

### tests/ (2 bash scripts, 81 Python test modules)
Manual smoke tests plus the unit/lane suite that the migration built. `find tests -name 'test_*.py' | wc -l` → **81** files, all run by `uv run pytest -q`; the two `.sh` files are `tests/e2e/stages/01-provision.sh` (the lane's VM provision stage) and `tests/shims/_recorder.sh` (the parity shims' command recorder). `test_installation.sh` is gone, and what it asserted moved into the lane harness. The sentence below is kept as the 2026-06 state: manual smoke tests only, no unit tests, no pytest, no mocks — the last three words are now the opposite of the truth.

### examples/ (26 files across 3 categories — `find examples -type f | wc -l`)
Reference implementations: `helm_charts/nginx-example/` (complete Helm chart with deployment, service, ingress, HPA), `kubernetes/` (shell examples for pod exec and node scheduling), `tekton_ci/` (pipeline examples including full deployment orchestrator and Flutter build). Good reference for extending the platform. (The MySQL setup and nginx-reverse-proxy example pipelines were removed 2026-10-27 with the backing-service automation.)

### helm_charts/ (placeholder only)
Contains only `.gitkeep`. All actual Helm chart content lives in `examples/helm_charts/nginx-example/`. The root directory is a placeholder with no charts.

---

## Critical Issues (Top 10)

> **This is the 2026-06 assessment of the tree and most of it no longer describes it.** It is kept
> as the audit that was, not rewritten. Items 1, 2, 3, 5, 6, 7 and 9 are closed by the Python
> migration; current status is in
> [`docs/plans/finish-python-migration-last-lap.md`](plans/finish-python-migration-last-lap.md).
> Read that before starting any item here, because several of these are defects that were already
> fixed and would otherwise come back looking like someone had tried them and given up.

1. **`automation.py` monolith** (2,504 lines, `wc -l`; this line said ~700) — handles initialization, installation, secrets, tunnels, uninstall, Docker registry. Should be split into focused classes (`Initializer`, `Installer`, `SecretManager`, `TunnelManager`, `Uninstaller`). **Still open, and it grew**: 1,800 lines of Tekton, routing and uninstall behaviour landed in it since the line was written.
2. **Seven shell scripts left in the whole tree** — 401 LOC, measured with `find . -name '*.sh' -not -path './.git/*' -not -path './.venv/*' | wc -l` — and **none of them under `scripts/`** since 2026-10-30 (`504af9a`, FIN-09 / D12), which deleted `retrieve_node_addresses.sh` and `view_cluster_resource_versions.sh` and left `jq` out of `KUBEMENTAT_DEPENDENCIES` with them. They are the lane's provision stage (`tests/e2e/stages/01-provision.sh`), the parity recorder (`tests/shims/_recorder.sh`), three CI-image helpers under `docker_files/ci_images/`, and two `examples/kubernetes/` demos. This line used to say ~55, and before that nine.
3. **No unit tests** — entire codebase has zero pytest coverage. Only manual bash smoke tests exist.
   (Reversed, and kept so the reversal is visible: `find tests -name 'test_*.py' | wc -l` → 81 files,
   all of them run by `uv run pytest -q`, which reports the count this file cannot keep current.)
4. **Bug: `tunnel-vault-ui`** in `src/cli/commands/tunnels.py` — decorated function named `tunnel_polaris` that calls `open_tunnel_vault` but the decorator name is wrong; also the entry point `kmt` doesn't register this command.
5. **Duplicate entry points** — `kmt` and `kmt.py` are functionally identical.
6. **`sys.path` manipulation** — each command module manually adds `../../lib` to path instead of using installed package imports.
7. ~~**Flat CLI structure** — no Click groups.~~ **Fixed 2026-10-30 (`02cd932`, FIN-61/62), and this line
   was a fixed defect describing itself as open until 2026-10-30.** `tunnel`, `tekton` and `helm` are
   Click groups now, registered one-per-group in `src/cli/kmt.py` the way `routing`, `users` and
   `component` already were, and the 11 commands that read as flat live under them: `tunnel
   pod|grafana|tekton|vault-ui`, `tekton
   run-pipeline|list|cleanup-pipeline-runs|setup-pipelines|setup-triggers|uninstall-pipelines`,
   `helm apply`. The renames ship **with no aliases and no compatibility shim**: `kmt tekton-list`,
   `kmt tunnel-grafana` and `kmt helmfile-apply` answer `No such command` and exit 2, and a test
   asserts that rather than asserting it informally. What deliberately stays flat is
   `kmt tekton-setup-cleanup-job`, the `kmt tekton-generate-*` commands and `kmt helm-version`: group-looking
   prefixes, no group behind them, so `uv run kmt --help` lists 27 entries and not a smaller,
   coherent number.
8. **Hardcoded config paths** — many paths are relative strings (`scripts/utilities`, `scripts/automation/tekton`) that could break with reorganization.
9. **`utils.py` is empty** — lib has an empty `utils.py` module suggesting planned refactoring that was never completed.
10. **`helm_charts/` is a placeholder** — confusing directory that collects nothing; all Helm content is in `examples/` and `platform_config/`.

---

## Refactoring Opportunities

### CLI/UX
- Group commands into Click subgroups (`kmt tekton run-pipeline`, `kmt tunnel grafana`)
- Replace all shell script delegation with Python library calls
- Remove duplicate entry point (`kmt.py`)
- Add argument validation and error handling with proper exceptions
- Add `--help` descriptions for all options
- Consider Click's `@pass_obj` for consistent config access

### Library Architecture
- Split `automation.py` into focused classes (Initializer, Installer, SecretManager, TunnelManager, Uninstaller)
- Move `crypto_utils.py` into the library properly (currently only used during initialization)
- Complete the empty `utils.py` or remove it
- Add type hints consistently across all modules
- Add docstrings to all public methods

### Test Coverage
- Add pytest unit tests for `kubernetes_utils.py`, `config.py`, `tekton_utils.py`
- Add integration tests using a mock/minikube cluster
- Replace bash smoke tests with pytest-based test suite
- Add test fixtures for Config, Automation, KubernetesUtils

### CI/CD
- Add GitHub Actions or Tekton-based CI for running tests on every PR
- Add linting (flake8/ruff) and type checking (mypy) to CI
- Add pre-commit hooks

### Documentation
- Add API documentation (Sphinx or MkDocs)
- Add architecture diagrams
- Document all environment variables and config fields

---

## Data Flow / Architecture Summary

```
User → kmt CLI → src/kubementat/ → Kubernetes Cluster
                        ↓
              platform_config/{env}/{team}/
              (JSON configs, git-crypt encrypted)
                        ↓
              kubementat/core/assets/templates/ → the shape of a layer (product data, read by nothing)
                        ↓
              (no scripts/ — the last .sh files are deleted, everything is Python)
                        ↓
              kubementat/core/assets/tekton_ci/ → CI/CD tasks & pipelines (product data)
              tekton_ci/pipeline-runs/ → generated runs (output, checkout-relative)
```

1. **Initialization:** `kmt config init <dir>` lays a tree down in the directory it is told, generates
   the deployer's SSH/GPG keypairs there, and configures git-crypt in that directory's own git
   repository. It writes answers and no product shape.
2. **Installation:** `kmt install` validates deps, runs helmfile, sets up Tekton (via lib or shell scripts)
3. **Pipeline execution:** `kmt tekton run-pipeline` loads YAML from `tekton_ci/pipeline-runs/`, replaces placeholders from config, applies via Kubernetes API
4. **Tunneling:** `kmt tunnel <component>` finds pods via selector, opens `kubectl port-forward`
5. **Secrets:** `kmt setup-secrets` reads encrypted SSH keys from config, creates K8s secrets
6. **Helmfile:** `kmt helm apply` runs `helmfile apply` with label filters against `platform_config/` values

---

## Key Files for Deep Dive

| Priority | File | Why |
|----------|------|-----|
| 1 | `src/kubementat/automation.py` | 700+ line monolith — main refactoring target |
| 2 | ~~`src/cli/kmt`~~ | **resolved 2026-10-28** — the pre-FIN-01 CLI entry file is deleted; nothing imported it and nothing could |
| 3 | `src/cli/commands/tunnels.py` | Has the tunnel-vault-ui bug |
| 4 | `src/kubementat/config.py` | Config loading — lazy loading, encrypted JSON |
| 5 | `src/kubementat/kubernetes_utils.py` | K8s API wrapper — solid but could use more type hints |
| 6 | `src/kubementat/tekton_utils.py` | Pipeline execution — placeholder replacement logic |
| 8 | `pyproject.toml` | Project config — entry point, dependencies |
| 9 | `src/cli/commands/tekton.py` | Tekton CLI — still calls shell scripts |
| 10 | ~~`scripts/automation/tekton/generate_trigger_config_from_templates.sh`~~ | **Deleted 2026-10-28 (FIN-23)**, along with `generate_deploy_pipeline_from_template.sh` (FIN-22) — both had no caller in `src/`. The migration targets are now `kmt tekton-generate-triggers` and `kmt tekton-generate-deploy-pipeline`, backed by `TektonUtils.generate_trigger_configs()` / `generate_deploy_pipeline()` in `tekton_utils.py`, which is row 6 above |

---

*Generated: 2026-04-28*
*Source: 10 directory index files totaling ~170 KB of analysis*
