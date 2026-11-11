# Research: Kubementat framework improvements after the Python migration

**Date:** 2026-10-27
**Branch analysed:** `python_refactor_2` @ `ac88d2b` (286 unit tests green, `uv run pytest -q`, 11.8 s)
**Scope:** The framework itself: the `kmt` CLI and `kubementat` package, configuration and secrets model, delivery model, test/CI, docs and project hygiene. It also covers which **optional tool groups** the platform should grow next. The target is the state **after** `docs/plans/finish-python-migration.md` §9 ("Migration exit criteria") is met.
**Not in scope:** Topics covered by sibling reports in this directory. This report references them and does not repeat them:

| Sibling report | Topic | How this report uses it |
|---|---|---|
| `research-log-aggregation-solution-10-27-2026.md` | Loki 3 + Alloy replacing `loki-stack`/Promtail | Assumed done; referenced from the observability extensions (Tempo/OTel) |
| `research-service-mesh-solution-10-27-2026.md` | Service mesh (recommends Istio ambient, Linkerd edge as fallback) | Referenced; mesh is not re-evaluated here |
| `research-component-updates-10-27-2026.md` (in progress by another agent) | Version/replacement audit of every installed component (ingress-nginx, Vault/OpenBao, Prometheus, Grafana, Gitea to Forgejo, Tekton, CI image tools, kaniko…) | Per-component bumps are left to it. This report only covers the **framework mechanisms** that keep versions current (pinning policy, Renovate, lint) |
| `research-concourse-ci-on-kubernetes-10-27-2026.md` (in progress by another agent) | Concourse as an alternative CI | Referenced in the CI-engine abstraction discussion only |

**Evidence labels used throughout:**
- **[Repo]**: observed in this repository, with `file:line` (line numbers measured at `ac88d2b`).
- **[Verified]**: an external fact checked during this research, with the URL inline.
- **[Inference]**: my reasoning or recommendation, not a fact.
- **[Plan]**: already covered by `docs/plans/finish-python-migration.md` (ticket id given). The item is not duplicated, only placed in the roadmap.

---

## 1. Executive summary

The migration is doing what it set out to do. It has one command-execution primitive (`src/kubementat/shell.py`), no `shell=True`, a `src/` layout, namespaces as configuration, Tekton `v1`, a parity harness and a real-VM E2E suite. Once §9 of the plan is met, the remaining weaknesses are structural, not migration debt:

1. **There is no automated feedback loop of any kind.** No repo CI exists (no `.github/`, `.forgejo/` or Tekton pipeline for the repo itself). There is no linter, type checker or pre-commit, and the 286 tests run only when someone remembers. Two upstream repos rotted unnoticed (B1 `twuni`, B2 kubernetes-dashboard, plan §1.8), and the Bitnami images died the same way (CHANGELOG "Unreleased"). The plan deliberately keeps the **E2E lane** manual (decisions 7/8). That is defensible for a 29-minute KVM lane, but not for a 12-second unit suite or for `helm repo update` / `helmfile lint` / `kubeconform`, which cost seconds.
2. **Secrets: one GPG key decrypts everything, and it travels into pipeline pods.** `tekton_ci/tasks/git-clone.yml:100-110` imports the deployer GPG private key into the pod and runs `git-crypt unlock`. That decrypts every environment's and every team's `*.encrypted.*` file. Secrets are also still exported to `os.environ` (`src/kubementat/automation.py:124-129`, `src/kubementat/helm_utils.py:32-33`). That was review finding 3.9/5.1, and it is not fully closed by any FIN ticket.
3. **The config model is an untyped flat dict.** `static.json` is about 40 `UPPER_SNAKE` keys with `REPLACEME_` placeholders (`templates/environment/static.json.template`). Nothing validates the files, and typos surface as cluster errors.
4. **The library still behaves like a script.** It calls `exit(1)` in six places, raises `click.ClickException` from `config.py`, and eagerly runs `load_kube_config()` with no in-cluster fallback (`kubernetes_utils.py:40`). The last point will bite FIN-50 (`kmt` inside a Tekton pod).
5. **Delivery is push-only and unpinned.** Only 1 of 9 active helmfile releases pins a chart version (`helmfile.yaml.gotmpl:112`, grafana). The CI image is `:latest` in 18 Task defaults. Nothing detects drift.
6. **The CLI is flat and has none of the usual ergonomics:** no `--version`, no completion, no `--dry-run`, no `--verbose`, no machine-readable output, and no `doctor`/`status`. Tunnels bind `0.0.0.0` by default, which exposes Grafana/Vault to the LAN.

### Prioritized roadmap

Effort: S < 1 day, M 1-5 days, L > 1 week. "[Plan]" rows are already scheduled and are listed only for ordering.

**Now (directly after the §9 exit criteria; cheap, high leverage)**

| # | Item | Area | Effort | Notes |
|---|---|---|---|---|
| N1 | Repo CI on Forgejo Actions (GitHub Actions mirror): `uv run pytest`, ruff, `helmfile lint`/`template` piped into `kubeconform`, shellcheck on the remaining bash, gitleaks | Testing/CI | S-M | Extends FIN-10 (pytest job) [Plan] to a full static gate; the E2E lane stays manual, as decision 7 says |
| N2 | `ruff` (lint+format) + pre-commit/prek hooks + `requires-python >=3.11` | Code quality | S | Python 3.9 EOL Oct 2025, 3.10 EOL Oct 2026 |
| N3 | Stop putting secrets in `os.environ`; tunnels default to `127.0.0.1` | Security | S | Remaining parts of review 3.9/5.1 |
| N4 | Library stops calling `exit()` and raising `click.*`; one `KubementatError` hierarchy mapped to exit codes in `cli` | Architecture | S-M | Makes FIN-50 and testing cleaner |
| N5 | Pin every helmfile chart version, add Renovate (helmfile + Dockerfile + Tekton image managers) | Delivery | S | The framework-side complement to the component-updates report |
| N6 | Pydantic models + generated JSON Schema for `static.json`/`static.encrypted.json`; `kmt config validate` | Config | M | Turns typos into a one-line error before any cluster call |
| N7 | `load_kube_config()` falls back to `load_incluster_config()`; global `--context`/`--kubeconfig` | Architecture | S | Prerequisite for FIN-50 and the multi-cluster TODO |
| N8 | Click groups (`kmt tekton …`, `kmt tunnel …`) | CLI | M | **[Plan] FIN-62.** Add `--version`, completion and `--dry-run` while touching it |

**Next (one to three months)**

| # | Item | Area | Effort |
|---|---|---|---|
| X1 | SOPS + age as the secrets backend, with per-environment/per-team recipients, replacing git-crypt in `platform_config`. Do it together with the externalized `platform_config` repo (TODOs "VERY IMPORTANT CHANGE") | Secrets | L |
| X2 | Split `automation.py` (1,498 LOC) into `initialize`, `install`, `uninstall`, `pipelines`, `secrets`, `tunnels` service modules | Architecture | M |
| X3 | `kmt doctor` (host tools, kubeconfig, cluster version, served APIs) and `kmt status` (releases, drift via `helmfile diff --detailed-exitcode`) | CLI/Delivery | M |
| X4 | CI-friendly cheap E2E lane on **k3d/kind** (minutes, no KVM), run on PR. Keep the virt-runner lanes as the manual "full" proof | Testing | M |
| X5 | Distribution: `uv tool install kubementat` / `pipx` from a Git tag; OCI image `kmt` (multi-arch); PyPI later | Packaging | M |
| X6 | Docs site (Zensical or MkDocs + Material), quickstart for k3s/Raspberry Pi, architecture diagrams | Docs | M |
| X7 | Optional extension groups, first wave: Kyverno (policy), Velero (backup), Alertmanager rules, a Gateway API controller, Headlamp (the plan's dashboard decision) | Extensions | M each |
| X8 | Release automation: semver tags, git-cliff/Conventional Commits changelog, single version source | Hygiene | S |

**Later (quarter+)**

| # | Item | Area | Effort |
|---|---|---|---|
| L1 | Optional GitOps mode (Flux, rendering helmfile into `HelmRelease`s, or Argo CD), with helmfile as the bootstrap | Delivery | L |
| L2 | External Secrets Operator + OpenBao (or the SOPS controller in Flux) for in-cluster secret delivery and rotation | Secrets | L |
| L3 | SSO group: Dex or Pocket ID/Authelia in front of Grafana, Tekton Dashboard, Forgejo, Headlamp | Extensions | M-L |
| L4 | Supply chain: Tekton Chains + cosign signing, SBOM via syft, Trivy Operator; Kyverno `verifyImages` | Extensions | M-L |
| L5 | Tracing (Tempo + OpenTelemetry Collector/Operator), OpenCost, KEDA/VPA+Goldilocks | Extensions | M each |
| L6 | Plugin/extension contract for component groups (entry points), so optional groups do not live in the core helmfile | Architecture | L |

---

## 2. Baseline: what "done" means and what the plan already covers

[Repo] `docs/plans/finish-python-migration.md` §9 defines the exit: `scripts/` contains no `.sh`, `kmt install`/`uninstall` run with zero bash, `jq`/`yq` leave `KUBEMENTAT_DEPENDENCIES`, `kmt run-script` is gone, the Click groups land (FIN-62), triggers move to `platform_config/{env}/{team}/triggers/` (FIN-23), docs stop saying `cd scripts/…` (FIN-61), a pytest CI job exists (FIN-10), and CHANGELOG + version bump (FIN-63, target `0.5.0`, plan §1.7a). Today: 44 scripts / 3,257 LOC remain under `scripts/` (plan §1.1), 3 commands still shell out (`tekton-setup-triggers`, `run-ci-container`, `run-script`), and src is 18 files / about 3,700 LOC.

**Plan decisions this report respects, with one argued exception:**
- Decision 4: do not design for the externalized `platform_config` *now*. This report puts it in "Next", after the migration, which is where the TODOs put it too.
- Decisions 7/8: no merge gate on lanes and no scheduled lanes. **Exception argued in §5:** a *static* gate (lint, unit, render, schema) is a different thing from a lane gate. FIN-10 already admits the pytest half. I recommend widening it, not changing the lane policy.
- §1.7a: no backwards compatibility. This is a licence to do the config-schema and CLI changes below without shims.
- Non-goals listed by the plan (multi-cluster, Flux/Argo, `platform_config` externalization) are exactly the "Next/Later" items here. They need their own plans, and this report is input to them.

---

## 3. Architecture and code quality of the Python package

### 3.1 Module boundaries

| Finding | Evidence | Recommendation | Effort / Prio |
|---|---|---|---|
| `Automation` is still the god class: init, install, pipelines, secrets, registry access, uninstall (about 370 LOC on its own) and tunnels | [Repo] `src/kubementat/automation.py` is 1,498 LOC, with 50+ methods (`initialize` :102, `install_kubementat` :400, `setup_pipelines` :588, `setup_secrets` :837, `uninstall_kubementat` :1041, `open_tunnel_*` :1405-1478). Review finding 1.3 | Split into `kubementat/services/{initializer,installer,uninstaller,pipelines,secrets,registry,tunnels}.py`, each taking an explicit `Config` and `KubernetesUtils`. Keep `Automation` as a thin facade only if tests need it (they mostly patch `shell.run`). The plan's target architecture (§4) adds `user_management.py`, `grafana_client.py` and `ci_utils.py`, which is the right direction. Do the split right after Wave 1-4 so the new modules are born separate | M / Next |
| Constructing `Automation` eagerly loads a kubeconfig even for `initialize` | [Repo] `automation.py:96` → `KubernetesUtils()` → `kubernetes_utils.py:40` `config.load_kube_config()`. Review 1.3/6.6 | Lazy property `self.k8s` that loads on first use | S / Now |
| No in-cluster config path | [Repo] Only `load_kube_config()` exists in `src/` (grep). FIN-50 wants `kmt` *inside* a Tekton step, where the ServiceAccount token is mounted and there is no kubeconfig | `try: load_incluster_config() except ConfigException: load_kube_config(context=…)`, plus a global `--context`. That is also the first concrete step toward the "separate environments aka clusters" Features TODO | S / Now |
| Library raises CLI exceptions | [Repo] `config.py:66,110,171` raise `click.ClickException`. `automation.py` imports `click` (:12) | Define `kubementat.errors` (`KubementatError` → `ConfigError`, `ClusterError`, `PreconditionError`, `UninstallIncompleteError` …). In `cli/kmt.py`, one wrapper maps them to `click.ClickException` / exit codes. Then `kubementat` no longer depends on Click at all | S / Now |
| Library terminates the process | [Repo] `exit(1)` at `automation.py:284,305,492,513,519,530`; `cli/commands/tekton.py:91`, `utilities.py:42`. Review 3.11/7.8, **not covered by a FIN ticket** | Raise `PreconditionError`. `check_dependencies_are_installed` should collect *all* missing tools and report them in one error (it currently stops at the first and logs "depdencies" with a typo, `:491`) | S / Now |
| `HelmUtils` still points at the bash components directory | [Repo] `helm_utils.py:14` `components_dir = …/scripts/automation/components`, also `automation.py:426` | Disappears with Waves 1-2 [Plan]. Add a unit test asserting no `scripts/` path in `src/` after §9 (the FIN-60 AST guard already covers named scripts) | S / [Plan] |
| Dead/duplicate entry file | [Repo] `src/cli/kmt` (53 LOC, no extension) | [Plan] housekeeping, plan §1.3b row 10 | S / [Plan] |
| CLI commands are registered inside `main()`, not on import | [Repo] `src/cli/kmt.py:41-71`. A `CliRunner().invoke(cli, …)` in a test sees an empty group unless it calls `main()` first, and `python -m` / completion entry points depend on this ordering | Register at module import (`cli.add_command(...)` at top level, or `@cli.command`), with `main()` only configuring logging and calling `cli()`. FIN-62 is the natural moment | S / Now (with FIN-62) |
| `cli.kmt.Config` shadows `kubementat.config.Config` in name and only carries one path | [Repo] `src/cli/kmt.py:26-32` | Rename to `CliContext` and carry the resolved environment/team/context/output format/dry-run flags, so commands stop re-parsing them | S |

### 3.2 Idempotency, converge semantics, dry-run

- [Repo] `shell.run(..., dry_run=False)` exists (`shell.py:76-129`), but **nothing in `src/` passes `dry_run`** (grep: 0 hits outside `shell.py`), and no CLI flag exposes it.
- [Repo] Idempotency is a stated product requirement (plan §1.7a, last row; §10 "Re-running a command must be safe"). It is implemented per helper (`create_or_replace*`, 404-tolerant deletes).
- **Recommendation [Inference]:** Make "plan, then apply" the uniform shape for mutating commands:
  1. Each service method returns a `Plan` (a list of intended actions: `helmfile apply -l group=standard`, `create namespace dev1-pipelines`, `apply Task git-clone` …).
  2. `--dry-run` prints the plan (and for helmfile runs `helmfile diff`). The default executes it.
  3. The uninstall path already does this informally (`_read_uninstall_plan` :1162 / `_print_uninstall_plan` :1200), so generalize that pattern instead of inventing one.
  4. For Kubernetes objects, prefer **server-side apply** (`kubectl apply --server-side --field-manager=kmt`, or `patch` with `application/apply-patch+yaml` in the Python client) over create-then-replace-on-409. SSA is the upstream-recommended converge primitive and gives field ownership for free ([Verified] https://kubernetes.io/docs/reference/using-api/server-side-apply/). The plan §10 already allows `kubectl apply -f -` where the CR client falls short.
  - Effort M, priority Next (after the services split).

### 3.3 Logging and output

- [Repo] Logging is configured once in `cli/kmt.py:18-23` (INFO, timestamp format), which closes D10. Human output is a mix of `logging.info` banners (`automation.py:549-568`, `###` rulers) and `click.echo`.
- **Recommendation:** Separate *diagnostics* (logging → stderr; `-v/-q` sets the level) from *results* (stdout). Add `--output {text,json}` for read commands (`tekton-list`, future `status`/`doctor`), so `kmt` composes with `jq`. That is ironic given the migration removes jq, but users will pipe. `rich` is optional; plain Click output is enough and keeps the dependency footprint small, which matters for the Pi and CI-image use cases [Inference]. Effort S-M, Next.

### 3.4 Typing and static analysis

- [Repo] About 22% of functions carry return annotations (36 of 162 `def`s match `-> …`). There is no `py.typed`, and no mypy/pyright config in `pyproject.toml`.
- **Recommendation:** Add `ruff` (lint + format) now ([Verified] https://docs.astral.sh/ruff/). Add a type checker in CI in *non-blocking* mode first. mypy is the safe choice. Astral's `ty` is in beta (0.0.x as of Aug 2026, stable "targeted for 2026": [Verified] https://astral.sh/blog/ty, https://www.infoworld.com/article/4108979/python-type-checker-ty-now-in-beta.html). Re-evaluate `ty` when 1.0 ships, since it would align with the existing uv toolchain. The kubernetes client has no inline types, so expect `ignore_missing_imports` for it. Effort S, Now (ruff) / Next (types).

### 3.5 Packaging and Python support

| Finding | Evidence | Recommendation |
|---|---|---|
| `requires-python = ">=3.9"`, `.python-version` = 3.9 | [Repo] `pyproject.toml:6`, `.python-version`. [Verified] Python 3.9 EOL Oct 2025, 3.10 EOL Oct 2026 (https://devguide.python.org/versions/, https://discuss.python.org/t/python-3-10-eol-is-there-an-official-end-of-support-date-within-october-2026/108064) | `requires-python >= 3.11` (or 3.12). The CI image already installs Python via uv (`docker_files/.../Dockerfile`, `UV_VERSION`), so the floor costs nothing there. Ubuntu 26.04 ships 3.14 [Inference: per Ubuntu's usual cadence; verify]. Test matrix 3.11 and latest |
| Three version sources disagree | [Repo] `pyproject.toml:3` = `0.4.0`, `VERSION` = `0.3.0`, git tags `0.1.0…0.3.0`, plan targets `0.5.0` | One source: git tag → `hatch-vcs`/`setuptools-scm`, or keep `pyproject` as the source and delete `VERSION`. Expose `kmt --version` via `importlib.metadata` |
| `requirements.txt` duplicates `pyproject` deps, unpinned | [Repo] `requirements.txt` (5 names, no versions) | Delete it. `uv.lock` is the lock and `uv export` can regenerate a requirements file if the CI image ever needs one |
| Package names `cli` and `kubementat` are both top-level | [Repo] `pyproject.toml` `include = ["kubementat*","cli*"]`, entry `cli.kmt:main` | A top-level package named `cli` will collide in any shared environment (pipx isolates it, but `pip install` into a venv with another `cli` package does not). Move to `kubementat.cli` (entry `kubementat.cli:main`). This is a breaking rename, which §1.7a permits |
| `initialize` runs `uv sync --all-extras` in the checkout | [Repo] `automation.py:170-175`; `KUBEMENTAT_DEPENDENCIES` also lists `python3`, `pip` (:46-58) | An installed tool should not resync its own source tree. Drop it once `kmt` is distributed as a tool; drop `python3`/`pip` from dependencies (the interpreter running `kmt` *is* the proof) |
| Build backend | [Repo] setuptools | Fine. `uv_build` or `hatchling` would be simpler for a pure-Python package with `src/` layout [Verified: uv build backend docs https://docs.astral.sh/uv/concepts/build-backend/]; optional |

**Distribution (Next, M):**
1. **`uv tool install git+https://<forge>/kubementat@vX.Y.Z`** and `pipx install …` work as soon as the package is self-contained. Templates, `tekton_ci/` and helmfile templates must then ship as **package data** (`importlib.resources`), not be read from `KUBEMENTAT_HOME`. This is the real prerequisite for the TODOs' "operators only manage their `platform_config` repo plus a pinned kubementat version". The plan's decision 4 says don't make it harder. Moving templates into package data is the step that makes it *possible* [Inference].
2. **OCI image `kmt`** (multi-arch amd64/arm64, distroless or `python:3.x-slim` plus pinned helm/helmfile/kubectl), usable as the Tekton step image instead of the 1 GB+ general `ubuntu-ci-minimal`. That solves FIN-50 ("`kmt` importable inside a CI step") structurally rather than by `uv run` from a clone.
3. **PyPI**: [Verified] Trusted Publishing supports GitHub, GitLab (incl. self-managed beta), Google Cloud and ActiveState, **not Forgejo/Codeberg** (https://docs.pypi.org/trusted-publishers/, https://blog.pypi.org/posts/2025-11-10-trusted-publishers-coming-to-orgs/). With Forgejo as the primary forge, either publish from a GitHub mirror's Actions or use a scoped API token stored as a Forgejo secret. Forgejo also has a built-in PyPI package registry, which is a no-lock-in option ([Verified] https://forgejo.org/docs/latest/user/packages/pypi/).

---

## 4. CLI UX

[Repo] 19 flat commands (`uv run kmt --help`), `environment`/`team` as positional arguments with envvar fallback, `--team` option on tunnels/helmfile (`cli/helpers.py:98-112`, default `dev1`), `--yes/-y` on install/uninstall/helmfile-apply.

| Finding | Evidence | Recommendation | Prio |
|---|---|---|---|
| Flat namespace | `kmt --help` | **[Plan] FIN-62**: `kmt tekton|tunnel|users|helm|component …` with no aliases | [Plan] |
| Inconsistent arg shape: `tunnel-*` take `environment` with `default='dev'`; `initialize/install` require it; `uninstall` takes no team; tunnels take `--team` while others take a positional `team` | `cli/commands/tunnels.py:28,45,59,76`, `kubementat.py:8-9,38` | While regrouping, make `--env/-e` and `--team/-t` **global options on the root group** (envvars `KMT_ENV`, `KMT_TEAM`), stored in the context. Prefix envvars with `KMT_`: plain `ENVIRONMENT`/`TEAM`/`NAMESPACE` collide with unrelated shell variables (and CI systems). Drop the silent `dev`/`dev1` defaults, or move them into a `~/.config/kmt/config.toml` "current env/team" (the kubectl-context pattern) | Now (with FIN-62) |
| No `--version` | `kmt --version` → "No such option" | `@click.version_option(package_name="kubementat")` | Now |
| No shell completion documented | — | Click ships it: `eval "$(_KMT_COMPLETE=bash_source kmt)"` (bash/zsh/fish) ([Verified] https://click.palletsprojects.com/en/stable/shell-completion/). Add `shell_complete=` callbacks that list environments/teams from `platform_config` (`Config.teams()` exists, `config.py` :346) and components from `constants.DEFAULT_NAMESPACES`. Add `kmt completion <shell>` as a discoverable wrapper | Now |
| Tunnels bind all interfaces by default | `tunnels.py:31,48,62,79,93` `--host default='0.0.0.0'`; `kubernetes_utils.py:696` | Default `127.0.0.1`. `0.0.0.0` exposes Grafana admin and the Vault UI to the LAN, and the plan only made it an option by accident. `kubectl port-forward` itself defaults to localhost ([Verified] https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/) | **Now** |
| Tunnel tells the user to read a secret file | `tunnels.py:39` echoes "See GRAFANA_ADMIN_* in …static.encrypted.json" | `kmt tunnel grafana --show-credentials` (prints from config, explicit opt-in) or `kmt secret get grafana-admin` | Next |
| Non-interactive mode | `--yes` on 3 commands; install fails closed off-tty (`automation.py:510-513`) | Good pattern; generalize: every mutating command gets `--yes`; a global `KMT_ASSUME_YES=1` for CI; honor `CI=true` only for *not prompting*, never for auto-confirming destructive actions | Next |
| No inspection commands | — | `kmt doctor` (host tools + versions vs. a supported-matrix, kubeconfig/context, cluster version, served APIs: the existing `preflight_installed_tekton_api_versions` is a building block; `helm repo update` reachability, which plan §10 already wants in the smoke lane) and `kmt status` (helm releases vs. helmfile desired, `helmfile diff --detailed-exitcode` exit 2 = drift ([Verified] https://helmfile.readthedocs.io/en/latest/#diff)) | Next |
| Help texts | Mixed case, `IPIPELINE` typo in `utilities.py:10` comment, `open a network tunnel…` lowercase | Style guide: imperative, sentence case, one-line `short_help`, examples in `epilog` | S, with FIN-62 |

**Click vs Typer:** [Inference] Stay on Click. The plan explicitly says "no new CLI framework" (§4, last bullet), Click already provides completion and groups, and Typer is a layer on Click that mostly saves type annotations. Switching buys little and costs a rewrite of 19 commands plus tests. `rich-click` is an optional, drop-in help-formatting upgrade if prettier help is wanted.

---

## 5. Testing and CI

### 5.1 What exists

- [Repo] Unit/parity: 286 tests, about 12 s, no cluster (`tests/`). Parity via argv-recording shims (`tests/shims/`, `tests/cluster_parity.py`), test-name conventions enforced (`tests/test_test_name_conventions.py`), dead-code guard (`test_no_dead_code_ships_in_the_tree.py`). This is unusually disciplined.
- [Repo] E2E: `tests/e2e/suite.py` on virt-runner/KVM, Ubuntu 26.04 guest, k3s, `smoke`/`full` lanes, `result.json` with SHA. Full lane is about 29 min; last full run 88 asserts / 16 red (TODOs "Now").
- [Repo] **No CI at all**: no `.github/`, `.forgejo/`, `.gitea/`, and no Tekton pipeline that tests the repo itself. FIN-10 (pytest job) is open (plan §1.3b row 10).

### 5.2 Recommendations

**N1: a static gate in repo CI (Now, S-M).** It runs on every push/PR, and none of it needs a cluster:

| Job | Tool | Why it would have caught real defects in this repo |
|---|---|---|
| unit | `uv run pytest -q` (3.11 + latest) | [Plan] FIN-10 |
| lint | `ruff check`, `ruff format --check` | Duplicate imports (review 2.3) and shadowed defs (D3, D5) are ruff `F811` |
| bash | `shellcheck` on what is left of `scripts/` and `tests/e2e/stages/*.sh` | Until §9 is met; delete the job afterwards |
| manifests | `kubeconform -strict -summary` on `tekton_ci/`, `examples/`, plus Tekton CRD schemas from the datreeio CRDs-catalog | The v1beta1→v1 field moves (plan §1.6) are schema facts; [Verified] https://github.com/yannh/kubeconform |
| helmfile | `helmfile lint` and `helmfile template` against the templates, rendered into kubeconform | Renders every release, which needs every repo reachable. **B1 (`twuni` unreachable) and B2 (kubernetes-dashboard 404) fail here within a minute**, which plan §10's "Upstream repo rot goes unnoticed" asks for. Run it on a weekly schedule too (a schedule for a 1-minute *render* is not the full lane that decision 8 refuses) |
| secrets | `gitleaks detect` | The working tree holds `deployer_ssh_key`, `gpg_private.key` and `git_crypt_symmetric.key` (ignored, not tracked: `.gitignore:6-10`, verified with `git check-ignore`). One `.gitignore` edit away from a leak |
| image | `hadolint` + `trivy image` on `ubuntu-ci-minimal` | Supply-chain hygiene for the image every Task runs |

Where to run it, in order of fit with the project values:
- **Forgejo Actions** on the user's Forgejo, with a `forgejo-runner` (docker or host executor, also on arm64). It is "designed to be familiar to users of GitHub Actions, but not designed to be compatible" ([Verified] https://forgejo.org/docs/latest/user/actions/overview/), so keep workflows to plain `run:` steps plus `actions/checkout` to stay portable to a GitHub mirror.
- **Dogfooding on Tekton** (a `kubementat-self-test` PipelineRun) is attractive as a demo but makes "is the platform broken?" and "is CI broken?" the same question. Keep it as an *example*, not the gate [Inference].

**X4: a cheap cluster lane (Next, M).** Add `--provider k3d|kind` to the E2E harness, or a separate `tests/e2e_kind/`, that runs `initialize → install --helmfile-installation-group <minimal> → hello-world PipelineRun Succeeded → uninstall → residue asserts` in about 10 min on a stock CI runner. [Verified] k3d runs k3s in Docker (https://k3d.io/), and kind is the upstream conformance tool (https://kind.sigs.k8s.io/). This does **not** replace virt-runner: the VM lane still proves the real-host story (storage class, systemd k3s, kernel). It shrinks the window in which nobody knows whether `kmt install` works, which TODOs "Now" calls "the single biggest risk on the board". Making it a merge gate stays the owner's decision. Even as a manual one-command lane it is roughly 3x cheaper than the VM lane.

**Chart and template tests (Later):** if kubementat starts shipping its own charts (e.g. the cleanup CronJob as a chart instead of Python-applied YAML, FIN-21), use `helm-unittest` and `ct lint` (chart-testing).

**Renovate (N5, Now, S):** [Verified] Renovate's helmfile manager matches `helmfile.yaml(.gotmpl)` and `helmfile.d/*` by default and updates `releases[].version` (https://docs.renovatebot.com/modules/manager/helmfile/). It also covers Dockerfile `ARG …_VERSION` (via `customManagers` regex), Tekton `image:` and the uv lockfile. Renovate runs self-hosted against Forgejo (platform `forgejo`/`gitea`) ([Verified] https://docs.renovatebot.com/modules/platform/forgejo/). **Prerequisite:** pin versions first (§7.1). Renovate cannot bump a `# version: "18.1.0"` comment.

---

## 6. Configuration and secrets

### 6.1 Config model and validation

[Repo] `templates/environment/static.json.template`: about 40 flat keys, mixing four concerns (git server identity, namespaces, selectors, component versions, CI image) and using `REPLACEME_` placeholders that `initialize` fills from environment variables (`automation.py:60-85, 177-251`). Team `static.json` is 6 keys and still carries `MYSQL_/REDIS_VOLUME_STORAGE_CLASS` for automation that was deleted (CHANGELOG "Unreleased"). Nothing validates any of it. `Config.get()` returns raw dicts (`config.py:280-314`).

**Recommendation (N6, Now/Next, M):**
1. **Pydantic v2 models** (`EnvironmentConfig`, `TeamConfig`, `EnvironmentSecrets`, `TeamSecrets`) in `kubementat/config_model.py`, with `model_json_schema()` exported to `schemas/*.schema.json` and a `$schema` key in generated files. Editors (VS Code YAML/JSON) then autocomplete and flag typos for free ([Verified] https://docs.pydantic.dev/latest/concepts/json_schema/). Pydantic is a pure-Python install with prebuilt arm64 wheels for `pydantic-core` [Inference: true for all current releases; verify on the chosen pin].
   *Lighter alternative* that keeps the dependency set at 5: `jsonschema` + hand-written schema. Pydantic wins because the models also replace the stringly-typed `config.get('env_static').get('KEY')` call sites with attributes.
2. **Nest by concern, rename keys outright** (§1.7a): `git: {url, host, port, ssh_user, revision}`, `components: {grafana: {namespace, selector, chart_version}}`, `tekton: {versions: {pipeline, triggers, dashboard}, storage_class, ci_image: {name, tag|digest}}`. `constants.DEFAULT_NAMESPACES` becomes model defaults, which removes the "constants agrees with template key for key" test (`test_component_namespaces_come_from_configuration.py`) because there is only one place left.
3. `kmt config validate [env] [team]`, `kmt config show --resolved` (defaults applied, secrets masked), and run validation on every `Config` load, so a missing key fails before any cluster call.
4. Replace the `REPLACEME_` / env-var dance in `initialize` with `kmt initialize --interactive` (prompts) or `--from answers.yaml`. A YAML answers file is reviewable and repeatable, and the 8 required env vars (`KUBEMENTAT_ENVIRONMENT_CONFIGURATION`) become fields with example values.
5. **YAML vs JSON:** JSON is fine with a schema. Moving to YAML/TOML buys comments, which matter for operator-facing config. Decide once, during the restructure, never twice [Inference].

### 6.2 Secrets: git-crypt today

[Repo] facts:
- One symmetric git-crypt key per repository. `initialize` creates it plus a deployer GPG key and an SSH key **in the repo root** (`automation.py:108-129, 355-369`; files present in the working tree, ignored by `.gitignore:6-10`).
- 17 GPG collaborators in `.git-crypt/keys/default/0/`. Revoking one means re-keying the whole repo; git-crypt has no per-file or per-path recipients.
- The deployer GPG private key is base64'd into `static.encrypted.json` and imported into **every pipeline pod** that clones with `GIT_CRYPT_UNLOCK=true` (`tekton_ci/tasks/git-clone.yml:100-110`). That pod can then read **every** env's and team's secrets, so team isolation exists only at the namespace level, not the secret level.
- Secrets are pushed into `os.environ` (`automation.py:124-129`, `helm_utils.py:32-33`) because the helmfile template reads `requiredEnv "GRAFANA_ADMIN_PASSWORD"` (`helmfile.yaml.gotmpl:120-121`).

**Options compared** ([Verified] facts linked; fit columns are [Inference]):

| Option | What it is | Fit for kubementat |
|---|---|---|
| **git-crypt** (status quo) | Transparent whole-file encryption in git, GPG | Simple, but all-or-nothing access, GPG UX, no per-team scoping, diffs are opaque blobs. Upstream sees little activity [Inference: last release 0.7.0, 2022; verify] |
| **SOPS + age** | Encrypts *values* inside YAML/JSON (keys stay readable and diffable), multiple recipients per file via `.sops.yaml` `creation_rules` by path regex; age/GPG/KMS/Vault backends; CNCF Sandbox ([Verified] https://getsops.io/docs/, https://github.com/getsops/sops) | **Best fit.** Per-path rules give `platform_config/dev/team-a/**` → team-a + platform admins; diffs show which key changed; age keys are one line, no GPG. Native consumers: helmfile via `helm-secrets` (`secrets:` on a release) and vals `ref+sops://` ([Verified] https://helmfile.readthedocs.io/en/latest/remote-secrets/, https://github.com/helmfile/vals), and Flux kustomize-controller decrypts SOPS natively ([Verified] https://fluxcd.io/flux/guides/mozilla-sops/). Python reads it by shelling out `sops -d` through `shell.run` (one more host binary, multi-arch static Go binary) |
| **Sealed Secrets** | Controller holds a private key; you commit `SealedSecret` CRs encrypted to the cluster ([Verified] https://github.com/bitnami-labs/sealed-secrets) | Good for in-cluster delivery, but **cluster-bound** (re-seal per cluster, and the key must be backed up) and opaque to `kmt` (it cannot read a sealed value back to, e.g., print the Grafana password). Also a Bitnami-org project, the same vendor whose catalog change just broke three automations (CHANGELOG) [Inference: risk, not a defect] |
| **External Secrets Operator + OpenBao/Vault** | ESO syncs from an external store into `Secret`s, with rotation via `refreshInterval` ([Verified] https://external-secrets.io/) | Right for **runtime** secrets and rotation at scale, and Vault is already an optional helmfile release (`helmfile.yaml.gotmpl:195-203`). But a store must exist *before* the platform: a bootstrap chicken-and-egg problem. ESO paused releases in Aug 2025 for maintainer shortage and resumed later ([Verified, secondary] https://infisical.com/blog/external-secrets-operator-paused). Check health before adopting |

**Recommendation:**
- **Now (S):** stop exporting secrets to `os.environ`. Pass them via `env=` to `shell.run` *for that one helmfile process only* (`shell.run` already accepts `env=`), or better, render a temporary values file (mode 0600, deleted in `finally`) and pass it with `--state-values-file`. Same for the `GIT_DEPLOYER_*` keys during `initialize`: write them straight into the JSON, with no env round trip. This closes the remaining part of review 3.9/5.1.
- **Next (L), bundled with the `platform_config` externalization:** make the secrets backend a small interface (`SecretsBackend.decrypt(path)`, `.encrypt(path)`, `.recipients(path)`), with **SOPS+age as the default** and git-crypt kept only as a migration reader during the one-time conversion. The TODOs already anticipate "different secret management options (like extensions/plugins), git-crypt would then be the first implementation" (TODOs "Open TODOs"). This report recommends SOPS be the first implementation instead, because per-team recipients fix the pod-decrypts-everything problem. Pipelines then get a **per-team age key** as a Kubernetes Secret, not the master GPG key.
- **Later (L):** an optional `secrets` extension group with OpenBao + ESO for app runtime secrets and rotation. [Verified] OpenBao is the Linux Foundation fork of Vault after the BSL change (https://openbao.org/). Which one ships is the component-updates report's call.
- **Rotation:** add `kmt secret rotate <name>` for the generated credentials (Grafana admin, deployer SSH key, age key). Each is "generate → re-encrypt → apply → restart consumer", which fits the Plan/apply shape in §3.2.

---

## 7. Delivery model

### 7.1 Push-based helmfile (keep as default, harden it)

[Repo] One environment helmfile (`templates/environment/kubementat_components/helmfile.yaml.gotmpl`) with 15 repositories and 9 active releases, grouped by a `group` label (`standard`, `routing`, `extra`). **8 of 9 active releases have no `version:`**: only grafana is pinned (:112), and the old pins are commented out (:76, :90, :101, :126, :150, :161). Repos for releases that are commented out are still declared (mailu, coredns, wekan, linkerd, kube-state-metrics, :54-69). helmfile processes *all* repositories before applying any label filter, which is exactly how B1 killed every install (plan §1.8).

Recommendations:
1. **Pin every chart** (`version:`) and let Renovate move the pins (N5). Install-time reproducibility is the platform's job. *Which* versions is the component-updates report's job.
2. **Delete unused repositories** and commented releases. Version control remembers them.
3. **Split the helmfile per group**: `helmfile.d/00-routing.yaml.gotmpl`, `10-monitoring…`, `90-extra-*`. Each declares only its repos, so a dead repo only breaks its own group, and optional groups become files you add, not labels you filter. Use `needs:` for ordering (cert-manager before ingress, CRDs before CRs) ([Verified] https://helmfile.readthedocs.io/en/latest/#dag-aware-installationdeletion-with-needs).
4. **Use helmfile `environments:`** for the env dimension (values per env, e.g. `dev` vs `pi` profile with smaller resource requests) instead of copying the whole template into `platform_config/{env}/kubementat_components/` at `initialize` (`automation.py:135`). Today a fixed template bug (FIN-88) silently survives in every already-initialized tree (plan §9 notes this exact caveat). Shipping the helmfile as versioned package data, with only *values* in `platform_config`, removes that class of drift [Inference].
5. **Drift detection:** `kmt status` → `helmfile diff --detailed-exitcode` (exit 2 = drift) per group. It can run as a scheduled Tekton PipelineRun that alerts via Alertmanager, without introducing GitOps.

### 7.2 GitOps as an optional mode (Later, L)

[Repo] TODOs "Open TODOs" lists Argo CD/Flux with the goal "get rid of all deployment pipelines; focus tekton on build and task execution". The plan lists it as a non-goal for the migration.

| | Flux | Argo CD |
|---|---|---|
| Model | Set of controllers, CRDs (`GitRepository`, `HelmRelease`, `Kustomization`), no UI by default | Server + UI + repo-server, `Application` CRDs |
| Footprint | Small. Four core controllers [Inference: about 200-300 MiB total RAM on idle clusters; verify] | Larger (server, repo-server, redis, controller); UI included |
| Secrets | Native SOPS decryption in kustomize-controller ([Verified] https://fluxcd.io/flux/guides/mozilla-sops/) | SOPS via plugins (CMP/helm-secrets); not native |
| Helm | `helm-controller`; Helm v4 support since Flux 2.8 ([Verified, secondary] https://newreleases.io/project/github/fluxcd/flux2/release/v2.8.3) | Renders charts to manifests (`helm template`), so hooks semantics differ |
| ARM64 | Multi-arch images | Multi-arch images |
| Both | CNCF graduated, Apache-2.0 ([Verified] https://www.cncf.io/projects/flux/, https://www.cncf.io/projects/argo/) | |

**Recommendation [Inference]:** Flux is the better fit for kubementat's values: smallest footprint for Raspberry Pi, native SOPS (which pairs with §6.2), no mandatory UI, and the kubementat Grafana/Headlamp can show its status. Design it as **`kmt install --mode gitops`**: `kmt` bootstraps Flux plus a `GitRepository` pointing at the operator's `platform_config` repo (on Forgejo), then generates `HelmRelease` objects from the same component catalog the helmfile mode uses (one catalog, two renderers). Tekton's deploy pipelines (`deploy-pipeline-helm-chart.yml`) then become "commit a values change" instead of "helm upgrade from a pod". That removes cluster-admin-ish `helm-deployer-sa` credentials from CI, a real security gain. Keep push mode as the default for the local/single-node case, where GitOps' reconciliation adds moving parts without much benefit.

### 7.3 Upgrade, rollback, promotion

- [Repo] There is no upgrade story: `initialize` refuses an already initialized env (`_check_is_already_initialized` :274) and the only path is re-initialize (plan §1.7a: "one staging customer, re-initialized").
- **Recommendation (Next, M):** `kmt upgrade [--to vX.Y]` = validate config against the new schema → `helmfile diff` → apply group by group with `--yes` → the existing preflight asserts (Tekton served API versions, `tekton_utils.preflight_*`) → print the rollback hint (`helm rollback <release> <rev>`; helmfile keeps `historyMax: 10`, :32). With config migrations as small Python functions keyed by schema version (`config_version` field). This is where the "no backwards compatibility" licence ends: once there is a second operator, it is needed.
- **Promotion:** with config in its own repo, `dev → prod` is a PR that copies or overlays values. In GitOps mode, Flux `Kustomization` per env path. Do not build promotion tooling into `kmt` before there are two real environments [Inference: YAGNI].

### 7.4 Cluster bootstrap for local and Raspberry Pi

[Repo] The E2E harness provisions k3s in a VM (`tests/e2e/stages/01-provision.sh`). The Features TODOs ask to "provide defaults for raspberry pi setup with k3s installation". The default storage class is `local-path` (`static.json.template`, `TEKTON_KUBERNETES_STORAGE_CLASS`).

**Recommendation (Next, M):** `kmt cluster create --provider k3d|k3s-local` as an *optional convenience*, not a platform dependency. It reuses the harness's provisioning knowledge so the quickstart becomes three commands. For Raspberry Pi: document k3s (single binary, arm64, ships local-path, Traefik and ServiceLB, which should be disabled or kept depending on the ingress decision) ([Verified] https://docs.k3s.io/). Document the known Pi cgroup pitfall (`cgroup_memory=1 cgroup_enable=memory` in `cmdline.txt`; [Verified] https://github.com/k3s-io/k3s/issues/8890). Ship a `pi` values profile (lower requests/limits, Prometheus retention, single replica). **Talos** is excellent for immutable clusters, but Pi 5 support is community-level ([Verified] https://www.talos.dev/v1.12/talos-guides/install/single-board-computers/unofficial), so document it as "supported via kubeconfig", not bootstrapped.

### 7.5 Backup, restore, DR (Next, M)

[Repo] Velero is listed as a "PRO FEATURE" in TODOs, and nothing exists. Stateful components today: Prometheus/Loki/Grafana PVCs, Vault, Gitea/Forgejo if installed, Tekton results.

Recommendation: an optional `backup` group with **Velero** (Apache-2.0, multi-arch, file-system backup via Kopia is the default uploader since 1.17 ([Verified, secondary] https://docs.replicated.com/enterprise/snapshots-velero-upgrading), [Verified] https://velero.io/docs/). Target: S3-compatible storage. MinIO's community edition went source-only in 2025 [Inference: verify current status], so prefer **Garage** or **SeaweedFS** for a FOSS, Pi-friendly S3, or an external bucket. Add `kmt backup create|list|restore` as thin wrappers. A DR runbook in docs: restore order is cluster → `kmt install` (stateless platform from git) → Velero restore of PVC-backed namespaces. That ordering is only possible because the platform is reproducible from `platform_config`, which is the argument for §6/§7.1.

---

## 8. Tool extension recommendations

Principle [Inference]: every extension is an **optional helmfile group** (one `helmfile.d/` file, its values template, `*_DEPLOYMENT_NAMESPACE` in the config model, a `kmt` tunnel/status entry if it has a UI, and an E2E `full`-lane feature). Nothing is added to the default `standard` group unless it is useful on a single Pi.

### 8.1 Table

Licenses and project status: **[Verified]** where a URL is given, otherwise from project knowledge and marked "(verify)". Footprint figures are rough idle numbers [Inference].

| Tool | Purpose | License | Footprint / ARM64 | Fit with values | Priority |
|---|---|---|---|---|---|
| **Kyverno** | Policy as code (validate/mutate/generate, image verification); replaces the Gatekeeper TODO | Apache-2.0; **CNCF graduated Mar 2026** ([Verified] https://www.cncf.io/announcements/2026/03/24/cloud-native-computing-foundation-announces-kyvernos-graduation/) | Medium (admission + background + cleanup controllers; about 300-500 MiB) / multi-arch | YAML policies, no Rego. Note: legacy `ClusterPolicy` is being deprecated in favour of CEL-based policy types (1.17+) ([Verified, secondary] https://jorijn.com/en/blog/kyverno-cncf-graduation-kubernetes-policy/), so author new policies in the CEL types | **Next** |
| Polaris (exists, commented out) | Best-practice audit dashboard | Apache-2.0 | Small / multi-arch | Overlaps with Kyverno's policy reports. [Repo] it is commented out in helmfile (:136-145) but README still lists it and `kmt tunnel-polaris` still exists: **decide**: either re-enable it or delete the tunnel and README line | Now (decide) |
| **Gateway API controller** (Envoy Gateway or Traefik) | Successor to ingress-nginx, which was **retired, no updates after March 2026** ([Verified] https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/, https://kubernetes.io/blog/2026/01/29/ingress-nginx-statement/) | Apache-2.0 / MIT | Envoy Gateway: medium; Traefik: small, ships with k3s / both multi-arch | Framework impact: the trigger EventListener ingress templates (`tekton_ci/triggers/templates/*/template-event-listener-ingress.yml`) and `create_ingress_yml.sh` must emit `HTTPRoute`. Envoy Gateway leads on conformance (Gardener chose it) ([Verified] https://gardener.cloud/docs/proposals/0068-gateway-api-extension/); Traefik is lightest and already on k3s. Selection details: component-updates report | **Next** (the framework side: route templates) |
| **Headlamp** | Kubernetes web UI (replaces the retired dashboard) | Apache-2.0, kubernetes-sigs | Small, one Deployment / multi-arch | Already the plan's recommended candidate (plan §1.8 B2). Plugin system can surface Flux/Tekton | **Next** |
| **Alertmanager + default rules** | Alerting; TODOs "alerting" | Apache-2.0 | Small / multi-arch | Comes with the prometheus chart (`alertmanager.enabled`), or switch to kube-prometheus-stack for the curated rule set. Ship default node/PVC/pod-crashloop and "Tekton PipelineRun failed" rules + a `kmt` config for receivers (email/Matrix/ntfy webhook). `karma` is optional | **Next** |
| **Velero** (+ Garage/SeaweedFS S3) | Backup/restore | Apache-2.0 / AGPL-3.0 (Garage, verify) / Apache-2.0 (SeaweedFS) | Small-medium / multi-arch | See §7.5 | **Next** |
| **Trivy Operator** | In-cluster vuln/config/secret scanning, reports as CRDs, Prometheus metrics | Apache-2.0 (Aqua) ([Verified] https://github.com/aquasecurity/trivy-operator) | Medium (scan jobs spike CPU/RAM; tune concurrency on a Pi) / multi-arch | Pairs with Grafana dashboards. Vendor-backed but OSS | Later |
| **Falco** | Runtime threat detection (eBPF) | Apache-2.0; CNCF graduated ([Verified] https://www.cncf.io/projects/falco/) | Medium, DaemonSet; modern eBPF probe needs kernel ≥5.8 (verify on Pi kernels) | Valuable for "cloud" profile; noisy on dev clusters | Later |
| **Zot** | OCI registry (images, Helm charts, artifacts), single binary, UI, dedupe, GC | Apache-2.0; CNCF Sandbox ([Verified] https://presentations.cncf.io/projects/zot/) | **Small** / arm64 binaries | Fills the "registry solution comes later" gap left by deleting `twuni` (FIN-88). Lets the mirroring pipelines (`sync-docker-images-pipeline.yml`, `mirrored_docker_images.json`) target an in-cluster registry, which removes Docker Hub rate limits and the `docker.io/julianweberdev` default | **Next** |
| Harbor | Full registry (RBAC, scanning, replication) | Apache-2.0; CNCF graduated | Large (6+ components, Postgres, Redis) | Overkill for Pi/single node; document as alternative for multi-team cloud | Later (doc only) |
| **Forgejo + Forgejo Actions** | Git forge + CI runner | GPL-3.0+ (Forgejo, since v9) ([Verified] https://forgejo.org/2024-08-gpl/) | Small-medium / multi-arch | User preference; replaces gitea/gitbucket/gitlab `extra` releases (component report covers the swap). **Framework work:** the trigger generator only knows GitHub/GitLab (`tekton_ci/triggers/templates/{github,gitlab}`); add a `forgejo` template set (Gitea-compatible webhook payload/`X-Gitea-Event`/`X-Forgejo-Event` headers; verify), and make `initialize` able to create the deploy key and webhook via the Forgejo API instead of README step "configure the generated deployer key in your github repository" | **Next** |
| **Dex** | OIDC federation (LDAP/GitHub/Forgejo upstreams) for Grafana, Headlamp, Tekton Dashboard (via oauth2-proxy), kubectl (OIDC) | Apache-2.0; CNCF sandbox | Small / multi-arch | Matches TODOs "LDAP integration / dex" and "OAuth Proxy". Forgejo can itself be the IdP (it is an OAuth2 provider), so for a single-team install, Grafana → Forgejo OAuth needs no extra component at all [Inference] | Later |
| Pocket ID / Authelia | Lightweight OIDC (passkeys) / forward-auth MFA | BSD-2 / Apache-2.0 (verify) | Very small (tens of MB) ([Verified, secondary] https://jacar.es/en/pocket-id-vs-authelia-vs-authentik-comparison/) | Good homelab alternatives to Dex when no upstream directory exists | Later |
| Keycloak / Authentik | Full IdP | Apache-2.0 / MIT (core) | Large (JVM / Postgres+Redis, about 1-2 GiB) | Too heavy for the Pi target; document only | Later (doc only) |
| **OpenTelemetry Collector + Tempo** | Traces (and OTLP ingestion for logs/metrics) | Apache-2.0 / AGPL-3.0 (Tempo) | Medium; Tempo monolithic on filesystem works small / multi-arch | Complements the Loki 3/Alloy report: Alloy can also receive OTLP, so **one collector** (Alloy) for logs+traces avoids a second agent (see `research-log-aggregation-solution-10-27-2026.md`). Tekton emits traces (OTel) since Pipelines 0.4x, so pipeline traces work out of the box [Inference: verify config flag in 1.17] | Later |
| **KEDA** | Event-driven autoscaling (scale to zero) | Apache-2.0; CNCF graduated ([Verified] https://www.cncf.io/projects/keda/) | Small / multi-arch | TODOs "autoscaling / apps" | Later |
| **VPA + Goldilocks** | Right-sizing recommendations | Apache-2.0 | Small / multi-arch | [Repo] namespaces `goldilocks`, `vpa` already exist in `constants.DEFAULT_NAMESPACES` (:34,46) without any release: either add the group or remove the constants | Later (or delete constants Now) |
| OpenCost | Cost/resource allocation | Apache-2.0; CNCF incubating (verify) | Small, needs Prometheus | Limited value on Pi/on-prem (no prices); useful in cloud profile | Later |
| **Tekton Chains + cosign** | Sign images/PipelineRun provenance (SLSA), verify at admission with Kyverno | Apache-2.0 | Small (one controller) / multi-arch | Native to the existing Tekton stack: no new CI engine. ([Verified] https://tekton.dev/docs/chains/signed-provenance-tutorial/) | Later |
| syft / grype | SBOM generation / scanning in the build pipeline | Apache-2.0 | Task-step only | Add as optional Tasks in `docker-build-pipeline.yml`; Trivy can do both if it is already chosen | Later |
| Image builder replacing kaniko | [Repo] `gcr.io/kaniko-project/executor:debug` in `build-and-push-docker-image.yml:15` and 3 pipelines. [Verified] Google archived kaniko June 2025; Chainguard maintains a fork (https://thenewstack.io/kaniko-lives-on-chainguard-forks-googles-dumped-tool/) | — | — | Replacement choice (BuildKit rootless, Buildah, kaniko fork) belongs to the component-updates report; framework-wise make the builder image a **config key**, not a literal in four files | Next (config key) |
| Tilt / Skaffold / DevSpace | Inner-loop dev against the cluster | Apache-2.0 (all) | Workstation tools | [Inference] Out of platform scope; offer **one documented example** (Tilt is the most popular; Skaffold integrates with Tekton-free flows) in `examples/` rather than a `kmt` integration | Later (example only) |
| Backstage | Developer portal | Apache-2.0; CNCF incubating | **Large** (Node app + Postgres, plugins need a TypeScript build/fork) | **Not recommended.** Contradicts "easy, runs on a Pi"; the maintenance cost of a Backstage instance exceeds the rest of the platform. Headlamp + Grafana + a generated docs page per team cover the discovery need | No |
| Concourse | Alternative CI | Apache-2.0 | — | See `research-concourse-ci-on-kubernetes-10-27-2026.md`. Framework consequence: if a second CI engine is supported, the pipeline/trigger concepts in `Config` (`tekton_team_*_dir`) need a `ci_engine` abstraction; don't add it speculatively | — |

### 8.2 Extension mechanism (Later, L)

[Inference] When more than about three optional groups exist, move each into a self-contained **component package**:

```
components/<name>/
  component.yaml        # name, group, namespace default, depends_on, ui {service, port}, arm64: true
  helmfile.yaml.gotmpl  # its releases + repos only
  values/*.template
  policies/, dashboards/ (optional)
```

`kmt component list|enable|disable|status <name>` reads them. Third-party components register via Python entry points (`kubementat.components`). This turns `constants.DEFAULT_NAMESPACES`/`DEFAULT_SERVICE_SELECTORS` (`constants.py:31-58`) and the per-tunnel methods (`automation.py:1405-1498`) into data. Today, `open_tunnel_grafana/tekton/polaris/vault` are four near-identical methods and four near-identical commands (`tunnels.py`), so `kmt tunnel <component>` driven by `component.yaml` replaces all eight.

---

## 9. Documentation and onboarding

[Repo] observations:
- `README.md` (226 lines) still describes `lib/` and `cli/` at top level (:30-46), instructs `pushd scripts/...` (:194-196, :215-217), lists Polaris and Linkerd as "included" although Polaris is commented out, and points the CI image at `docker.io/julianweberdev/...:latest`.
- `docs/index-overview.md` is stale ("lib/ 7 Python modules", "tests/ 4 bash scripts, no Python tests").
- `docs/README_*.md` are uneven (10-434 lines). `README_GIT_CRYPT.md`/`gpg_key_generation.md` document an approach §6.2 recommends replacing.
- Strong internal docs exist for agents/maintainers (`AGENTS.md`, plans, `tests/README.md`), but no `CONTRIBUTING.md`, no architecture diagram, and no quickstart that ends in a working cluster.
- FIN-61 [Plan] covers the "no `cd scripts`" and grouped-commands rewrite.

Recommendations:
1. **Docs site (Next, M).** [Verified] Material for MkDocs entered maintenance mode on 2025-11-05 (critical fixes for at least 12 months, until about Nov 2026). Its team's successor **Zensical** (MIT) builds existing MkDocs/Material projects "with minimal changes" (https://github.com/squidfunk/mkdocs-material/issues/8523, https://zensical.org/). Recommend **Zensical** for a new site, or MkDocs+Material pinned if Zensical's plugin coverage (e.g. for `mkdocs-click`) is not there yet. Generate the CLI reference from Click (`mkdocs-click` or a 30-line script over `cli.commands`) so it cannot drift. Host it on Forgejo Pages / Codeberg Pages, or GitHub Pages on the mirror.
2. **Structure (Diátaxis):** *Tutorials* (Quickstart: k3d on a laptop in 15 min; Raspberry Pi 4/5 with k3s), *How-to* (add a team, add a pipeline, enable a component group, rotate a secret, back up/restore, upgrade), *Reference* (CLI, config schema generated from §6.1, component catalog), *Explanation* (architecture, why push-vs-GitOps, secrets model) ([Verified] https://diataxis.fr/).
3. **Architecture diagrams** as Mermaid in the docs (renders natively on Forgejo/GitHub): the `kmt` → `kubementat` services → `shell.run`/k8s client layering, the install sequence, and the platform_config → helmfile → cluster flow.
4. **`CONTRIBUTING.md`**: dev setup (`uv sync`), the test-name convention, how to run smoke lanes, the no-backcompat rule, commit message convention (§10). Plus `SECURITY.md` (how to report, the key-handling rules).
5. **Examples:** keep `examples/` but make each one runnable through `kmt` and covered by the full lane. `examples/tekton_ci/pipeline-runs/dev1/delete-pipeline-mysql-run.yml` references deleted MySQL automation (CHANGELOG), which is the kind of dead example a kubeconform + reference check in CI catches.

---

## 10. Project hygiene

| Finding | Evidence | Recommendation | Effort / Prio |
|---|---|---|---|
| Version sources disagree | §3.5 | Single source + `kmt --version`; tag `v0.5.0` at migration exit (FIN-63 [Plan]) | S / Now |
| Changelog is hand-written prose (valuable, but long) | `CHANGELOG.md` "Unreleased" entries run to 40+ lines each | Keep the human "breaking" narrative, and adopt Keep a Changelog headings + Conventional Commits so `git-cliff` can generate the skeleton ([Verified] https://git-cliff.org/, https://keepachangelog.com/). Current commit subjects are descriptive sentences ("DONE is earned: …"), which are readable but not machine-parseable, so it is a style change for the maintainer to accept or reject | S / Next |
| Release process | Tags exist (`0.1.0…0.3.0`), no artifacts | Tag → CI builds wheel + sdist + `kmt` OCI image (multi-arch) + CI image, attaches to a Forgejo release. Semver with "0.x = breaking allowed" stated in README | M / Next |
| Secrets material in working tree | `deployer_ssh_key`, `gpg_private.key`, `git_crypt_symmetric.key` at repo root (ignored) | `initialize` should write key material to `~/.config/kubementat/<env>/` (mode 0700) or straight into the encrypted config, never into the checkout. gitleaks in CI as a backstop | S / Now |
| Dead config/constants after the deletions | `MYSQL_/REDIS_VOLUME_STORAGE_CLASS` in team template; `goldilocks`/`vpa` namespaces with no release; 6 unused helm repos; Polaris tunnel without release; `templates/environment/team/nginx-reverse-proxy/` for deleted automation | Extend `test_no_dead_code_ships_in_the_tree.py` to config: every key in the templates must have a reader, and every helm repo must have a release | S / Now |
| Obsolete scripts after §9 | `scripts/` empty by definition; `docker_files/**/build.sh`, `run_locally.sh`, `test.sh`, `examples/kubernetes/*.sh` remain | Keep the image build scripts (or move them into CI), and turn the 2 example scripts into docs snippets | S / with §9 |
| Agent tooling files in repo root | `.cursorignore`, `.pi/`, `.agent-work/`, `.codegraph/` (ignored), `AGENTS.md`, `review-python_refactor_2.md` (untracked) | Move `review-*.md` into `docs/reviews/` or delete it after its findings are mapped to tickets (several are still open: 3.9/5.1, 3.11). Keep `AGENTS.md` | S |
| License | MIT, `Copyright (c) 2021 Julian Weber` (`LICENSE`) | Fine and permissive. Add SPDX headers (`# SPDX-License-Identifier: MIT`) and a `REUSE.toml` if third-party files are vendored ([Verified] https://reuse.software/). Note: Forgejo is GPL-3.0+ and Tempo/Grafana are AGPL-3.0. Deploying them is not affected by MIT, but document that kubementat *installs* copyleft software unmodified | S / Later |
| Repo layout | `helm_charts/` holds only a README (README mentions `nginx-example`, which lives in `examples/helm_charts/`) | Delete `helm_charts/`, or make it the home of kubementat's own charts (cleanup CronJob etc.) | S |
| Default registry is a personal Docker Hub namespace | `DOCKER_REGISTRY_BASE_URL: docker.io/julianweberdev` (template), 18 Task defaults `julianweberdev/ubuntu-ci-minimal:latest` | Publish images under a project namespace (`ghcr.io/kubementat/…`, or the Forgejo registry, or `quay.io`), **pin by digest** in Task defaults via a config key, with Renovate updating the digest | S-M / Now |

---

## 11. Risks and open questions

- **[Inference] Scope creep vs. the "easy, runs on a Pi" promise.** Every extension in §8 is optional for that reason. The default `standard` group should be measured: one Pi 4 with 8 GiB should run `standard` with headroom. Add that as a full-lane assertion (memory requests sum).
- **SOPS migration cost:** converting all `*.encrypted.*` files and re-keying 17 collaborators to age is a one-time event. Do it during the `platform_config` externalization so the pain happens once.
- **Open question for the owner:** is a *static* CI gate (lint/unit/render, about 3 min) compatible with decision 7 ("no merge gate")? This report assumes yes, since decision 7 is phrased about lane results, and FIN-10 already plans a pytest job.
- **Open question:** GitOps mode doubles the delivery surface. It is only worth it if the externalized `platform_config` repo lands first. Otherwise Flux would watch the kubementat repo itself, which is the coupling the TODOs want to remove.
- **Gaps in this research:** I did not benchmark footprints on a Pi. Numbers in §8.1 are indicative. The component-updates and Concourse sibling reports did not exist yet when this was written, so cross-references to them are by filename only.

---

## 12. Sources

**Repository (all at `ac88d2b`, branch `python_refactor_2`)**
- `docs/plans/finish-python-migration.md` (§1.1, §1.3a, §1.3b, §1.7, §1.8, §4, §8, §9, §10)
- `docs/plans/testsuite-requirements.md`, `docs/README_E2E_TESTS.md`, `TODOs.md`, `CHANGELOG.md`, `review-python_refactor_2.md`, `README.md`, `AGENTS.md`, `docs/index-overview.md`
- `src/kubementat/{automation,config,constants,shell,helm_utils,kubernetes_utils}.py`, `src/cli/kmt.py`, `src/cli/helpers.py`, `src/cli/commands/*.py`
- `templates/environment/{static.json.template,static.encrypted.json.template,team/static.json.template}`, `templates/environment/kubementat_components/helmfile.yaml.gotmpl`
- `tekton_ci/tasks/git-clone.yml`, `tekton_ci/tasks/build-and-push-docker-image.yml`, `docker_files/ci_images/ubuntu_ci_minimal/Dockerfile`, `pyproject.toml`, `.gitignore`, `.gitattributes`, `LICENSE`, `.git-crypt/keys/`
- `git log` (last 60 commits), `git tag`, `uv run kmt --help`, `uv run pytest -q` (286 passed)

**Sibling research reports (this directory)**
- `research-log-aggregation-solution-10-27-2026.md`
- `research-service-mesh-solution-10-27-2026.md`
- `research-component-updates-10-27-2026.md` (in progress at time of writing)
- `research-concourse-ci-on-kubernetes-10-27-2026.md` (in progress at time of writing)

**External (accessed 2026-10-27)**
1. Python versions / EOL: https://devguide.python.org/versions/ ; https://discuss.python.org/t/python-3-10-eol-is-there-an-official-end-of-support-date-within-october-2026/108064
2. Ingress NGINX retirement: https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/ ; https://kubernetes.io/blog/2026/01/29/ingress-nginx-statement/
3. Gateway API implementation choice (Gardener GEP-0068): https://gardener.cloud/docs/proposals/0068-gateway-api-extension/
4. Material for MkDocs maintenance mode / Zensical: https://github.com/squidfunk/mkdocs-material/issues/8523 ; https://zensical.org/
5. Kyverno graduation: https://www.cncf.io/announcements/2026/03/24/cloud-native-computing-foundation-announces-kyvernos-graduation/ ; CEL policy deprecation note: https://jorijn.com/en/blog/kyverno-cncf-graduation-kubernetes-policy/
6. SOPS: https://getsops.io/docs/ ; https://github.com/getsops/sops ; Flux SOPS guide: https://fluxcd.io/flux/guides/mozilla-sops/
7. Helmfile remote secrets / vals: https://helmfile.readthedocs.io/en/latest/remote-secrets/ ; https://github.com/helmfile/vals ; helmfile `needs`/diff: https://helmfile.readthedocs.io/en/latest/
8. Flux releases (2.7-2.9, Helm v4 in 2.8): https://newreleases.io/project/github/fluxcd/flux2/release/v2.8.3 ; https://v2-7.docs.fluxcd.io/flux/components/helm/
9. Flux / Argo CNCF pages: https://www.cncf.io/projects/flux/ ; https://www.cncf.io/projects/argo/
10. Kaniko archival and forks: https://thenewstack.io/kaniko-lives-on-chainguard-forks-googles-dumped-tool/ ; https://www.chainguard.dev/unchained/fork-yeah-were-bringing-kaniko-back
11. ty type checker beta: https://astral.sh/blog/ty ; https://www.infoworld.com/article/4108979/python-type-checker-ty-now-in-beta.html
12. Ruff: https://docs.astral.sh/ruff/ ; uv build backend: https://docs.astral.sh/uv/concepts/build-backend/
13. External Secrets Operator pause/resumption (secondary, vendor): https://infisical.com/blog/external-secrets-operator-paused ; project: https://external-secrets.io/
14. Sealed Secrets: https://github.com/bitnami-labs/sealed-secrets ; OpenBao: https://openbao.org/
15. PyPI Trusted Publishing: https://docs.pypi.org/trusted-publishers/ ; https://blog.pypi.org/posts/2025-11-10-trusted-publishers-coming-to-orgs/ ; Forgejo PyPI registry: https://forgejo.org/docs/latest/user/packages/pypi/
16. Forgejo Actions: https://forgejo.org/docs/latest/user/actions/overview/ ; Forgejo GPL relicensing: https://forgejo.org/2024-08-gpl/
17. Renovate helmfile manager: https://docs.renovatebot.com/modules/manager/helmfile/ ; Forgejo platform: https://docs.renovatebot.com/modules/platform/forgejo/
18. Click shell completion: https://click.palletsprojects.com/en/stable/shell-completion/
19. kubectl port-forward: https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/ ; Server-side apply: https://kubernetes.io/docs/reference/using-api/server-side-apply/
20. kubeconform: https://github.com/yannh/kubeconform ; k3d: https://k3d.io/ ; kind: https://kind.sigs.k8s.io/ ; k3s: https://docs.k3s.io/ ; k3s Pi cgroup issue: https://github.com/k3s-io/k3s/issues/8890
21. Talos SBC (unofficial ports): https://www.talos.dev/v1.12/talos-guides/install/single-board-computers/unofficial
22. Zot: https://presentations.cncf.io/projects/zot/ ; Velero: https://velero.io/docs/ ; Kopia default since 1.17 (secondary): https://docs.replicated.com/enterprise/snapshots-velero-upgrading
23. Trivy Operator: https://github.com/aquasecurity/trivy-operator ; Falco: https://www.cncf.io/projects/falco/ ; KEDA: https://www.cncf.io/projects/keda/
24. Tekton Chains: https://tekton.dev/docs/chains/signed-provenance-tutorial/
25. SSO comparisons (secondary): https://jacar.es/en/pocket-id-vs-authelia-vs-authentik-comparison/ ; https://blog.elest.io/authentik-vs-authelia-vs-keycloak-choosing-the-right-self-hosted-identity-provider-in-2026
26. Pydantic JSON Schema: https://docs.pydantic.dev/latest/concepts/json_schema/ ; Diátaxis: https://diataxis.fr/ ; git-cliff: https://git-cliff.org/ ; Keep a Changelog: https://keepachangelog.com/ ; REUSE: https://reuse.software/
