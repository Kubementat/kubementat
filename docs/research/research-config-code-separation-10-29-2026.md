# Research: Separating kubementat's code from cluster configuration

**Date:** 2026-10-29
**Question:** How do we stop shipping kubementat as "clone this repository, administer it, and
it doubles as your cluster config", so that (a) kubementat/KMT can develop independently and
(b) users of the kubementat automations keep their clusters up to date with the improvements?
**Method:** the tree read as it stands (`python_refactor_2` @ `fac4ed4`), one real customer fork
measured, and the documented prior art of six projects that already split these two things.
**Status:** research. Nothing here is scheduled; `TODOs.md` line 1061 already asks for this change
and line 937 puts it out of scope for the Python migration ("decision 4: do not design for it yet").

---

## 1. What the tree does today, measured

One checkout is three different things:

| | What it holds | Who is supposed to own it |
|---|---|---|
| `src/`, `scripts/` | the product (CLI + automation) | upstream |
| `templates/`, `tekton_ci/tasks/`, `tekton_ci/pipelines/`, `helm_charts/` | product *assets* — defaults, Tasks, Pipelines, charts | upstream |
| `platform_config/` | the user's cluster: environment/team config, credentials, their app deployments, their pipeline customisations | the operator |
| `templates/` → copied into `platform_config/` at `initialize` | product assets that *become* operator working material | **both, which is the bug** |

The mechanism is `Automation.initialize()` (`src/kubementat/automation.py:222`): it copies template
trees into the config directory (`:278-285`), writes four `static*.json` files out of eight exported
environment variables (`:288-291`, `:315-352`, list at `:215`), and runs `git-crypt` in the checkout (`:293`). Per the
CHANGELOG row 11b ledger, a single `initialize` writes **19 paths** under `platform_config/`.

Everything is resolved from one root — `kubementat_main_dir` (`config.py:79`, `KUBEMENTAT_HOME`,
defaulting to the checkout) — and `platform_config` is a subdirectory of it unless
`KUBEMENTAT_PLATFORM_CONFIG_DIR` says otherwise (`config.py:36`, `:165-173`). **Those two variables are
the seam this change is built on; the rest is plumbing.**

### The six couplings that have to break

1. **Config is a subdirectory of the product.** `Config.platform_config_dir = kubementat_main_dir/platform_config`
   (`config.py:166`). The env var already overrides it, but `initialize` will not let you use that: it
   runs `uv sync --all-extras` **in the product directory** (`automation.py:311`) and `git-crypt init`
   in it (`automation.py:613-625`). Both need a checkout.
2. **Product assets are not shipped.** Measured: `uv build --wheel` produces 37 entries, **all of them
   `.py`** — the 19 `templates/` files, 41 `tekton_ci/` files, 2 `scripts/`, 1 `helm_charts/` are not in
   the distribution. `[tool.setuptools.packages.find] includes kubementat* and cli* only (pyproject.toml).
   So "install kubementat as a tool" is impossible today; every `kmt` needs the full checkout.
3. **The environment's own helmfile lives in the config dir and is edited by the operator**
   (`helm_utils.py:96`, `:229` → `platform_config/{env}/kubementat_components/helmfile.yaml.gotmpl`).
   Plan row 13 is the symptom: `kmt component install dev traefik` refused a real component because the
   *local copy* of that file was a year old. The product cannot evolve its release list while the answer
   to "what does kubementat install?" is read from a file someone hand-edited in 2025.
4. **The CI workspace is the fork.** Every pipeline threads `automation-git-url`; deploy tasks read
   `${PATH_TO_CONFIGURATION}/platform_config/${ENV}/${TEAM}/${APP}/values.encrypted.yaml` out of that
   clone (`tekton_ci/tasks/deploy-local-helm-chart.yml:128`). So CI pulls *the product* from
   `AUTOMATION_GIT_URL`, which is the same repo the operator edits. Eight of the 37 keys in
   `templates/environment/static.json.template` (`AUTOMATION_GIT_*`, `GIT_DEPLOYER_*`) exist only to
   answer "where is the repo that holds the code that runs this".
5. **Secrets are welded to the product repository.** `.git-crypt/` is *tracked in the product repo* — 18
   files, 17 of them collaborator GPG identities (`git ls-files .git-crypt`), `.gitattributes` filters
   `*.encrypted.*`, and `initialize` exports a symmetric key. The credential store of a cluster is
   entangled with the ACL of a code repository.
6. **Configuration answers arrive from eight exported shell variables** at `initialize` time
   (`automation.py:215`), which is why the README makes the first step an `export` block.

### What the evidence from a real fork says

`/home/verfeinerer/dev/freelancer/krone/dev_kubementat` — 824 commits, and it is the honest test case for
both axes of the requirement:

- It is a **fork of the product plus the customer's config in one tree**: 63 `platform_config/` files,
  205 `tekton_ci/` files, and their own code under `utilities/custom/` (jumpbox tunnels, image copies,
  a `vte/` subdirectory).
- Upstream improvements arrive as **`Merge remote-tracking branch 'upstream/master'`** commits, and their
  own messages show the cost: *"adjusted open database tunnel script"*, conflicts in `utilities/`, plus
  product fixes they had to carry themselves (*"fix helmfile to work with the latest helm and helmfile
  cli versions"*).
- Their `platform_config/dev/` directory contains `terraform/` and `gcloud/`. That is what happens when
  "the config directory" is also "the thing we do not know where else to put".

**Axis A fails** because the product cannot move without breaking 824 commits of a customer's tree.
**Axis B fails** because the only way improvements reach that cluster is a merge conflict.
The two axes are not independent problems; they are one coupling read twice.

---

## 2. The two axes, stated as requirements

| | Requirement | What makes it true |
|---|---|---|
| **A. Independent development** | kubementat + `kmt` change without editing anyone's tree, and without the user forking | product ships as a versioned artifact; user edits only their own config |
| **B. Users stay updated** | a cluster gets kubementat's improvements without the user re-reading the source | the config holds *deviations*, not copies; upgrades are computed, not merged |

The single design fact that serves both: **a copy of a default is the worst of both worlds** — it is
operator-owned (so the product cannot change it, which kills B) and it is product-shaped (so the operator
must merge it, which kills A). Everything below is a way to stop copying.

---

## 3. Options surveyed

### Option 1 — "inventory directory": `kmt config init <dir>` (kubespray's model)

The tool is the product (a checkout, a wheel, or a pinned container image); the cluster description is a
directory the operator owns. Kubespray's whole workflow is this:
`inventory/mycluster/{inventory.ini,group_vars/**}` is the user's, and the playbooks are theirs —
`ansible-playbook -i inventory/mycluster/inventory.ini cluster.yml`, and the Docker route binds the
inventory in and pins the product with the image tag (`quay.io/kubespray/kubespray:v2.31.0`)
([getting started](https://github.com/kubernetes-sigs/kubespray/blob/master/docs/getting_started/getting-started.md)).
Upgrading = pulling the product / bumping the tag and re-running; the inventory is never touched.

- **A:** yes — product and config are different paths and different lifecycles.
- **B:** weak-by-default. Kubespray stays current because `group_vars` are *data* with stable names. If
  kubementat's config directory is full of copied manifests (today's 19 paths), B does not improve.
- **Cost:** the `kmt config init` verb plus the "defaults come from the product" rule. Cheap, but only
  half the answer.

### Option 2 — separate config *repository*, product as pinned artifact (GitOps repo separation)

The standard recommendation is to split repos by the kind of content they hold — package/platform/app-config/app-code
([Google's GitOps best practices](https://cloud.google.com/kubernetes-engine/enterprise/config-sync/docs/concepts/gitops-best-practices),
[gnunn-gitops standards](https://github.com/gnunn-gitops/standards/blob/master/folders.md),
[cloudogu's pattern catalogue](https://github.com/cloudogu/gitops-patterns)). Applied here: `kubementat`
(releases, docs) and `mycompany-kubementat-config` (the operator's `platform_config`, and the CI runs
against *that* repo). This is what `TODOs.md:1061` asks for: "the DevOp … will only need to manage the
platform_config git repositories for his installations and use the kmt tooling".

- **A:** yes, fully. **B:** yes, if the config repo carries a kubementat version that a bump can change.
- **Cost:** new secrets story (Option/Section 5), and the CI workspace changes (Section 4.4).
- **Note:** a config repo that still contains copies of product manifests just moves the merge conflicts
  to a different repository. **Option 2 only works layered on Option 3.**

### Option 3 — layering: product defaults + operator overrides, nothing copied

This is the load-bearing one, and there is direct tooling for it that kubementat *already uses*.

- **helmfile layering** — `bases:` merges shared state files, arrays are *replaced* not merged
  ([writing-helmfile](https://raw.githubusercontent.com/helmfile/helmfile/main/docs/writing-helmfile.md)),
  and **sub-helmfiles can be remote with a pinned ref**:
  `git::https://github.com/kubementat/kubementat.git@templates/environment/kubementat_components/helmfile.yaml.gotmpl?ref=v0.5.0`
  ([configuration.md](https://github.com/helmfile/helmfile/blob/main/docs/configuration.md)).
  `missingFileHandler: Warn` makes an optional operator values file optional instead of fatal, and
  `inherits:` lets a child state opt into the parent's `repositories:`/`helmDefaults:`
  ([shared config across teams](https://helmfile.readthedocs.io/en/latest/shared-configuration-across-teams/)).
  **Consequence:** the helmfile that declares what kubementat installs stays in the product and is read at
  a pinned version; the operator's config supplies `environments: {env}: values: [cluster.yaml]` and
  optional per-release overrides. This deletes coupling #3 above outright and fixes plan row 13's class of bug.
- **helm library charts / values layering** — chart `values.yaml` is the product's, `-f values.yaml` is
  the user's, and the recommendation is to design values for override
  ([values best practices](https://github.com/helm/helm-www/blob/main/version-3/chart_best_practices/values.md)).
  A `values.schema.json` is how you make a bad override fail loudly.
- **kustomize base + overlay** — same shape, same rule: the base is upstream, the overlay is yours.
- **kubeadm** — the binary is upgraded by the package manager while the cluster's configuration lives in
  a `kubeadm-config` ConfigMap; `kubeadm upgrade plan` compares desired against current before anything
  is applied ([upgrading kubeadm clusters](https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-upgrade/)).

**The good news is that kubementat already has this pattern in Python.** `Config._component_value()`
(`config.py:405`) is "config first, `constants.py` second, warn once per key" for namespaces and
selectors, and `docs/README_PLATFORM_CONFIG.md` documents it. **The product already knows how to be a
defaults layer; it just does not get to be one for anything it materialises into `platform_config/`.**

### Option 4 — vendor the product into the config repo (carvel vendir / OCI bundles)

`vendir` declares "which parts of which repos/releases/charts I want" with a lock file
([carvel-dev/vendir](https://github.com/vmware-tanzu/carvel-vendir)); `imgpkg` packages files as an OCI
image whose digest *is* the identity
([carvel imgpkg](https://carvel.dev/imgpkg/)). So the config repo holds `vendor/kubementat/` + a lock,
and an upgrade is a one-line digest bump.

- **A:** yes. **B:** yes, and the bump is a single reviewable diff.
- **Cost:** a vendoring step in the CLI, a lock file to maintain, and two trees on disk. It buys
  reproducibility (air-gapped installs) that Option 3 does not, at the price of an extra moving part.
  This is also how Flux ships manifests as OCI artifacts
  ([OCIRepository](https://fluxcd.io/flux/components/source/ocirepositories/),
  [OCI cheatsheet](https://fluxcd.io/flux/cheatsheets/oci-artifacts/)) — the same "pin by digest" idea.

### Option 5 — let a GitOps controller own the config (Flux/ArgoCD)

Already logged as an open item (`TODOs.md` last line). It solves B elegantly: Flux's image-update
automation scans a registry, rewrites the tag in the Git repo, commits, and reconciles
([image updates to Git](https://fluxcd.io/flux/guides/image-update/)). But it does not solve A by itself
(a controller watching a repo full of copied manifests still has the merge problem), and it replaces the
`kmt`+Tekton execution model rather than separating code from config. **Deferred, and this research does
not require it.** It is worth taking one idea from: *the mechanism that keeps config current is an
automated commit, not a human.*

**Recommendation:** Option 3 (layering: defaults never copied) as the core, packaged through Option 2
(config repository + versioned product artifact), with Option 4's digest pinning as the optional
reproducibility layer and Option 1's `config init` verb as the user-facing entry point.

---

## 4. What that means concretely

### 4.1 Product

`kubementat` becomes a real artifact: wheel + a CI container image, semver. Prerequisite: ship the
assets (`templates/`, `tekton_ci/tasks|pipelines`, `helm_charts/`, `scripts/`) as package data and resolve
them through `importlib.resources` instead of `kubementat_main_dir`, and delete the `uv sync` call
(`automation.py:311`) — a released tool does not install its own dependencies at run time.

### 4.2 Configuration

A directory the operator owns (git or not — kubementat should not care). **The hierarchy is not up for
revision: the config directory is one installation, an environment is a cluster, and a team is nested
inside the environment it belongs to.** This is what every read site already assumes
(`config.py:195-203`), and it is what the measured customer fork shows (780 of its own commits land in
`platform_config/{env}/{team}/{app}/values.encrypted.yaml`). A top-level `teams/` directory is therefore
*out*: team namespaces, domains, and registries are environment-specific, so hoisting teams would put a
lie in the layout that the data would contradict on first write.

The app layer is named explicitly for the same reason — it is where the operator's real content lives,
and an override-only tree that cannot see apps has no chance of being a deviation measure:

```
<config-dir>/                           # one installation; KUBEMENTAT_PLATFORM_CONFIG_DIR
  kubementat.yaml                       # schema_version + kubementat version constraint
  {environment}/                        # == one cluster
    cluster.yaml                        # namespaces, domains, registry, storage class (was static.json)
    secrets.encrypted.json              # (was static.encrypted.json) provider-owned, opaque to layering
    kubementat_components/**            # overrides ONLY: values files for what kubementat installs
    {team}/                             # a team = a directory with its own static.json (config.py:38-43)
      team.yaml
      secrets.encrypted.json
      applications/{application}/       # values.encrypted.yaml: pure operator content
      tekton/{tasks,pipelines,triggers}/ # already the layout today (config.py:195-198), unchanged
```

Two things this fixes by being stated: `kubementat_components/` stops being a copied tree and becomes the
overrides directory it is in practice (the reserved-name set at `config.py:43` is exactly this directory),
and the operator's CI manifests keep the home they already have instead of being invented into a new
`pipelines/` directory that no code path would read.

`kmt config init <dir>` writes the *minimum*: the manifest plus empty files. `initialize` stops copying
templates. Deleting those 19 copies is the whole point: **the config directory's size becomes a measure
of how much the cluster deviates from kubementat.**

### 4.3 New verbs (the "keep current" machinery, which does not exist today)

| verb | what it answers | precedent |
|---|---|---|
| `kmt config init <dir>` | scaffold a config dir for env/team | kubespray's `cp -r inventory/sample inventory/x` |
| `kmt config show` | the effective config, tagged `default`/`config`/`override` per key | `helm get values` vs chart defaults |
| `kmt config diff --from 0.4 --to 0.5` | what a new kubementat changes here: keys renamed, new defaults, values files whose template drifted | `kubeadm upgrade plan`, `helm diff` |
| `kmt config migrate` | rewrite the operator's own files for a renamed key, `--dry-run` first | the `helm-migrate-values` plugin, which "does not apply any changes to the release … simply outputs the values to override" ([repo](https://github.com/OctopusDeployLabs/helm-migrate-values)) |
| `kmt config validate` | schema check, plus "this key is a kubementat default; delete the line" | `values.schema.json` practice |

The version contract belongs in `kubementat.yaml` (`kubementat: ">=0.5,<1"`) — helm's `kubeVersion`/
chart-version discipline is the model. Then an upgrade is: bump one line → `config diff` → `migrate` →
`kmt install`. And the product's release pipeline should open that one-line bump PR against known config
repos (the mechanism Flux uses; Renovate's `git-submodules`/`helm-values` managers do the same), so
staying current is a notification the operator accepts, not a task they discover.

### 4.4 CI/Tekton (the part people underestimate)

Today's pipeline clones `AUTOMATION_GIT_URL` (the operator's fork) and treats it as both code and config.
The replacement is two sources in the workspace: **product from the pinned image/tag, config from the
config repo.** Concretely: pipeline `image:` params and `TEKTON_CI_IMAGE_TAG` pin the product; a
`git-clone` of the *config* repo becomes the `path-to-configuration` that
`deploy-local-helm-chart.yml:128` already reads; and the eight `AUTOMATION_GIT_*` / `GIT_DEPLOYER_*` keys
can be deleted or reduced to "which config repo, which ref". The `kmt` in that image needs its assets,
which is 4.1.

### 4.5 Git deployer keys stop meaning anything

The deployer SSH/GPG keys exist to let CI clone the automation repo. Once CI's product source is an
image, `kmt initialize`'s key generation (`automation.py:247-268`) is only needed if CI clones config
over SSH. Worth deciding early, because it removes ~150 lines of initialize and two README sections.

---

## 5. Secrets after git-crypt

`TODOs.md:999` and `:1062` already say git-crypt leaves the product repo and becomes a config-repo tool,
and float the idea of **pluggable secret providers with git-crypt as one implementation**. The survey:

| pattern | where the ciphertext lives | who holds the key | fit |
|---|---|---|---|
| **git-crypt** | the repo, transparently via a git filter | a symmetric key, or GPG identities committed to the repo's `.git-crypt/` | the incumbent; forces the repo to be a git repo and the keys to be repo-scoped. `git-crypt export-key` hands "the ability to read every credential" to anyone you send it to ([git-crypt](https://github.com/AGWA/git-crypt)) |
| **SOPS** (+ age / cloud KMS / Vault) | the repo, but per-value; keys stay readable so diffs work | your key material, out of band | **best drop-in for kubementat's current shape**: a `static.encrypted.json` that decrypts at read time, one recipient list per environment, no git filters, works with a non-git config dir |
| **External Secrets Operator / Vault references** | nothing in git | an external store | the security-recommended direction ("referencing secrets in an external key vault is the recommended approach" — [Microsoft playbook](https://microsoft.github.io/code-with-engineering-playbook/CI-CD/gitops/secret-management/), [OWASP DevSecOps guideline](https://owasp.github.io/DevSecOpsGuideline/2-Process/2-6-Deploy/2-6-2-Secrets-in-Kubernetes-and-GitOps/)); kubementat already ships Vault |
| **Sealed Secrets** | the repo, sealed per-cluster | the controller | good for teams' app secrets, wrong shape for kubementat's platform config |

Two notes that change the design:
1. kubementat does not just *pass through* secrets — `helmfile_apply` reads
   `platform_config/{env}/static.encrypted.json` and exports `GRAFANA_ADMIN_USER`/`PASSWORD` before
   helmfile runs (`helm_utils.py:74`, `:442`). So decryption must happen **inside the CLI**, which rules
   out "git filter only" solutions and points at a provider interface with SOPS/age as the default.
2. `initialize` generates random credentials into those files (`generate_password`). Under the new model
   that is a `kmt config set-secret`/provider call, not a file edit — and it is a good forcing function
   for the interface.

Keep `git-crypt` as a selectable provider for existing installs (the owner's own suggestion), delete
`.git-crypt/` and the GPG keyring setup from the product repo.

---

## 6. Migration path (no big bang)

| phase | work | breaks the krone-style fork? |
|---|---|---|
| 0 | ship assets in the wheel + `importlib.resources` resolution; drop `uv sync` | no — checkout keeps working |
| 1 | `KUBEMENTAT_PLATFORM_CONFIG_DIR` / `--config-dir` everywhere, including `initialize` (today it forces the checkout) | no |
| 2 | introduce the layer: `Config` reads product defaults first, config dir second (extend `_component_value` to values files and helmfile), config dir values win | no — copies still work, now as overrides |
| 3 | `kmt config init/diff/validate` (read-only diff first) | no |
| 4 | helmfile: product manifest referenced at the pinned ref, operator's `helmfile.yaml` reduced to `bases:`+values+overrides | **yes** — needs the `missingFileHandler` behaviour and a diff command |
| 5 | secret provider interface, SOPS default, git-crypt as a provider; `.git-crypt/` out of the product | yes for users who were relying on repo-scoped git-crypt |
| 6 | CI: product-from-image, config-from-repo; delete `AUTOMATION_GIT_*` | yes |
| 7 | `initialize` stops copying templates; `kmt config migrate` for renames | yes, and this is the one that makes B true |

The project's rule of "no aliases, one name per deletion" (AGENTS/README) is a real constraint here:
phases 4-7 are behaviour breaks and belong in one minor-version line with `config diff` as the migration
tool, not in seven deprecation branches.

**The test that decides whether we are done** (and the one worth writing first): a cluster configured
against kubementat `N` upgrades to `N+1` by changing **one line** and running
`kmt config diff && kmt install` — with no file copied from the product, no merge, and a green
`kmt install` on a config directory that only ever contained overrides.

---

## 7. Decisions this needs from the owner

0. **The config directory's hierarchy** — *decided 2026-10-30:* keep `environment (cluster) → team →
   application` inside one directory per installation; no top-level `teams/`, apps are first-class.
   See §4.2.
1. **Does the product stay a checkout, or become a wheel + image?** (Phases 0-1 are cheap insurance
   either way; Option 2 and 4.4 assume yes.)
2. **Is the config directory expected to be a git repository, or just a directory?** Everything works
   either way, but "git" buys the update-bot story and the SOPS-in-git story; "directory" is what a
   Raspberry Pi setup wants.
3. **Which layering mechanism for the helmfile** — `bases:` inside the config dir plus the product
   defaults read in Python, or helmfile's remote `git::…?ref=` (which makes `git` a runtime dependency
   of a `helmfile apply`, and needs a ref kubementat injects).
4. **Secret provider default** — SOPS+age, or "git-crypt until 1.0, SOPS after"?
5. **How strict is the version contract** — a hard refusal when `kubementat.yaml`'s constraint is
   unsatisfied, or the `_component_value` pattern (warn once and use the product default)?
6. **Are Tekton pipelines still the execution engine**, or is this the moment for Option 5? Affects
   how much of §4.4 is thrown away.
7. **What happens to `kmt initialize`'s credential/key generation** — deleted, or re-expressed against
   the secret provider?

---

## 8. Sources

**Measured in-tree:** `src/kubementat/config.py:35-36,79,165-173,405`;
`src/kubementat/automation.py:202-215,222-295,311,315-352,613-625,908-911`;
`src/kubementat/helm_utils.py:74,96,229,442`; `templates/environment/static.json.template`;
`tekton_ci/tasks/deploy-local-helm-chart.yml:128`; `pyproject.toml`; `docs/README_PLATFORM_CONFIG.md`;
`docs/plans/finish-python-migration-last-lap.md` rows 11b/13/14; `TODOs.md:937,999-1013,1061-1062`;
`git ls-files .git-crypt` (18 files); `uv build --wheel` → 37 entries, no data files;
customer fork `/home/verfeinerer/dev/freelancer/krone/dev_kubementat` (824 commits, 63 config files,
upstream/master merge commits).

**Prior art:** [kubespray getting started](https://github.com/kubernetes-sigs/kubespray/blob/master/docs/getting_started/getting-started.md)
· [helmfile writing guide (bases, release templates, missingFileHandler)](https://raw.githubusercontent.com/helmfile/helmfile/main/docs/writing-helmfile.md)
· [helmfile configuration (remote `git::…?ref=` states)](https://github.com/helmfile/helmfile/blob/main/docs/configuration.md)
· [helmfile shared configuration across teams (`inherits:`)](https://helmfile.readthedocs.io/en/latest/shared-configuration-across-teams/)
· [helm values best practices](https://github.com/helm/helm-www/blob/main/version-3/chart_best_practices/values.md)
· [helm-migrate-values](https://github.com/OctopusDeployLabs/helm-migrate-values)
· [upgrading kubeadm clusters](https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-upgrade/)
· [Google GitOps best practices](https://cloud.google.com/kubernetes-engine/enterprise/config-sync/docs/concepts/gitops-best-practices)
· [gnunn-gitops standards](https://github.com/gnunn-gitops/standards/blob/master/folders.md)
· [cloudogu gitops-patterns](https://github.com/cloudogu/gitops-patterns)
· [Flux image update automation](https://fluxcd.io/flux/guides/image-update/) ·
[Flux OCIRepository](https://fluxcd.io/flux/components/source/ocirepositories/)
· [carvel vendir](https://github.com/vmware-tanzu/carvel-vendir) ·
[carvel imgpkg](https://carvel.dev/imgpkg/)
· [git-crypt](https://github.com/AGWA/git-crypt) ·
[OWASP secrets in Kubernetes & GitOps](https://owasp.github.io/DevSecOpsGuideline/2-Process/2-6-Deploy/2-6-2-Secrets-in-Kubernetes-and-GitOps/) ·
[Microsoft playbook: GitOps secret management](https://microsoft.github.io/code-with-engineering-playbook/CI-CD/gitops/secret-management/)
