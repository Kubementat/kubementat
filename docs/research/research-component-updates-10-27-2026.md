# Research: Component Version Updates and Replacements for Kubementat

- **Date:** 2026-10-27
- **Scope:** Every platform component and tool that Kubementat installs or depends on, checked against upstream status as of late October 2026.
- **Out of scope:** These topics have their own reports and are only referenced here:
  - Log aggregation (Loki / `loki-stack` / promtail): [`research-log-aggregation-solution-10-27-2026.md`](research-log-aggregation-solution-10-27-2026.md)
  - Service mesh (Linkerd): [`research-service-mesh-solution-10-27-2026.md`](research-service-mesh-solution-10-27-2026.md)
- **Method:**
  1. Read the repo (helmfile, values templates, install scripts, CI image, Tekton manifests, Python packaging).
  2. Downloaded each Helm repository's `index.yaml` on 2026-10-27 to get the newest chart and app versions and deprecation flags.
  3. Used the GitHub/Codeberg/Forgejo APIs for latest releases, archive status and licenses.
  4. Ran `skopeo inspect --raw` against image manifests to check ARM64 support.
  5. Fetched upstream docs and announcements for lifecycle and licensing facts.
- **Marking convention:**
  - Claims that rest on secondary sources, or that I did not verify directly, are marked **[unverified]**.
  - My own conclusions are marked **[inference]**.

---

## 1. Executive summary

Kubementat's **CI tooling is current**, because the CI image was just modernized: Ubuntu 26.04, kubectl 1.37, helmfile 1.8.2, helm-diff 3.15.15, tkn 0.46.2, yq 4.54.1 and uv 0.13.6 are all the latest upstream releases. Its **platform layer is not**. Almost every Helm release is unpinned. Two of them point at dead or retired upstreams (ingress-nginx, the int128 GitBucket chart), and one pinned chart lives in a repository that upstream has deprecated (Grafana). The Gitea values no longer match the chart, which silently deploys an HA PostgreSQL plus a Valkey cluster from frozen `bitnamilegacy` images. The default image builder (kaniko from `gcr.io`) is archived. The one secret store offered, HashiCorp Vault, is under the non-open-source BSL, which conflicts with the "free & open source, no vendor lock-in" values.

### Prioritized list

**Must replace / fix (blocking, insecure, or conflicts with the project's values)**

1. **ingress-nginx → a Gateway API controller.** Upstream retired the project. The last release was controller v1.15.1 on 2026-03-19, and the repo is archived [1][2][7]. Recommended: **Traefik v3.7** (chart `traefik/traefik` 42.1.3, MIT). It is the default on k3s, multi-arch down to armv7, and serves Ingress, Gateway API *and* ingress-nginx annotations, so migration can be gradual [4]. **Envoy Gateway v1.9.3** is the "pure Gateway API" alternative.
2. **GitBucket (int128 chart) → remove.** The chart's last release was 4.34.0 on 2020-07-26. Its templates use `apps/v1beta2` and `extensions/v1beta1`, which Kubernetes removed in 1.16 and 1.22, so **it cannot install on any supported cluster** [29][38].
3. **Gitea → Forgejo, and make Forgejo the one git forge.** The user already runs Forgejo and prefers it. Forgejo is GPLv3+ and governed by its community under Codeberg e.V. Its official chart (`oci://code.forgejo.org/forgejo-helm/forgejo`, v17.3.2, defaulting to Forgejo **15 LTS**) has no Bitnami database subcharts [21][22][24]. Kubementat's Gitea values are already broken (see §4.3).
4. **Grafana chart → `grafana-community/grafana`.** `grafana/grafana` in `grafana.github.io` stopped at 10.5.15, which is flagged `deprecated`. Development continues at `grafana-community` (14.2.0 / Grafana 13.3.0, 2026-10-27) [8][9][10]. Kubementat pins **9.0.0 (Grafana 12.0.0, May 2025)**. It also installs AngularJS-era plugins that Grafana 12+ can no longer load [11].
5. **Tekton Pipelines 1.17.1 → 1.15.5 LTS.** 1.17 is a non-LTS release that reaches **end of life on 2026-10-31**, four days from now. 1.15 is LTS until 2027-07-31 [31].
6. **kaniko `gcr.io/kaniko-project/executor:debug` → maintained builder.** Google archived the repo in June 2025, and the image last changed on 2025-05-23 with no more security fixes [34]. Use `ghcr.io/osscontainertools/kaniko:<ver>-debug`, a community fork that publishes free multi-arch images, or BuildKit rootless.
7. **Python 3.9 → 3.12+ and the kubernetes client 33.1.0 → 37.x.** Python 3.9 reached EOL on 2025-10-31 and 3.10 on 2026-10-01 [36]. `kubernetes` 37.0.1 requires Python ≥3.10 [37], so the current `.python-version` of 3.9 holds the client back.
8. **HashiCorp Vault → OpenBao.** Vault (now 2.x, owned by IBM since February 2025) is BSL 1.1 [16][17]. OpenBao is the Linux Foundation fork under MPL-2.0, with chart `openbao/openbao` 0.30.6 / OpenBao 2.7.2 released 2026-10-27 [18]. Effort is low because Vault sits in group `extra` and its values are minimal.

**Should update (maintained, but far behind or unpinned)**

- `prometheus-community/prometheus`: unpinned, and the commented pin 18.1.0 means Prometheus 2.39. Pin **29.39.0 / Prometheus 3.15.1** and work through the Prometheus 3 migration guide [12][13].
- `prometheus-blackbox-exporter`: commented pin 7.1.3. Pin **11.20.2 / v0.29.1** [12].
- `sstarcher/helm-exporter`: still maintained (1.4.0, 2026-10-14). Pin it [14].
- `jetstack/cert-manager`: commented pin v1.10.1. Pin **v1.21.3**, replace the deprecated `installCRDs` with `crds.enabled` + `crds.keep`, and enable `config.gatewayAPI.enabled` [6][15].
- `gitlab/gitlab`: unpinned (latest 10.5.1 / GitLab 19.5.1). It defaults to **EE** images and is too heavy for a Pi. Remove it from the default helmfile, or pin it with `global.edition: ce` (§4.3).
- Remove the dead `kube-state-metrics` repository entry (deprecated since 2021, never used) [45].
- Tekton task images: replace `hashicorp/terraform:light` (Terraform 1.4.2, 2023, amd64-only, BSL) with OpenTofu, and `maven:3.8.4-openjdk-17-slim` (built 2022) with `maven:3.9-eclipse-temurin-21`.
- CI image: pin Helm to the 4.x line (currently installed unpinned from apt; Helm 3 gets security fixes only until 2027-02-10 [35]). Move Node 24 to Node 26 LTS (Node 26 becomes LTS on 2026-10-28; Node 24 is in maintenance) [36].
- Fix obsolete APIs in examples: `examples/helm_charts/nginx-example/templates/hpa.yaml:2` uses `autoscaling/v2beta1`, removed in 1.25 [38].

**Nice to have**

- Renovate (self-hosted; supports helmfile `.gotmpl` and the Forgejo platform) to automate version bumps [39].
- SOPS + age next to or instead of git-crypt, plus External Secrets Operator with OpenBao for in-cluster secrets (§4.5).
- Tekton Operator, whose built-in pruner would replace the custom PipelineRun cleanup CronJob [33].
- Evaluate `kube-prometheus-stack` or VictoriaMetrics single as the metrics backend (§4.4).
- trust-manager, only if Kubementat ever runs an internal CA [15].

**Keep as is**

- CI image tooling: kubectl 1.37, helmfile 1.8.2, helm-diff 3.15.15, tkn 0.46.2, yq 4.54.1, uv 0.13.6, Ubuntu 26.04 LTS, skopeo, git-crypt 0.8.0.
- Tekton Triggers 0.37.2 (LTS until 2027-08-06).
- The `infra.tekton.dev` release URL scheme.
- Commented components (Polaris, Mailu, Wekan, CoreDNS): **delete them from the helmfile** rather than update them. Detail is in §4.8.

---

## 2. Inventory: what Kubementat installs or depends on

| Source | What it installs or uses |
|---|---|
| `templates/environment/kubementat_components/helmfile.yaml.gotmpl` | **Repos (l.39-69):** grafana, prometheus-community, kube-state-metrics, sstarcher, ingress-nginx, jetstack, linkerd, hashicorp, gitlab, int128, gitea-charts, mailu, coredns, wekan; polaris is commented out.<br>**Active releases:** prometheus (l.74), prometheus-blackbox-exporter (l.88), prometheus-helm-exporter (l.99), grafana (l.110, **only pinned release: 9.0.0**), loki/`loki-stack` (l.124), cert-manager (l.148), nginx-ingress (l.159), vault (l.195), gitlab (l.206), gitbucket (l.217), gitea (l.228).<br>**Commented releases:** polaris (l.136), linkerd-crds and linkerd-control-plane (l.172-192), mailu (l.239), coredns (l.250), wekan (l.261). |
| `scripts/automation/components/install_tekton.sh` | Tekton Pipelines, Dashboard and Triggers (plus `interceptors.yaml`), applied with `kubectl apply` from `https://infra.tekton.dev/tekton-releases/...` (l.28, 46, 75, 108-109). Versions come from `templates/environment/static.json.template` l.34-36: Dashboard 0.73.0, Pipeline 1.17.1, Triggers 0.37.2. |
| `scripts/automation/components/install_routing.sh` | Runs helmfile `group=routing`, then two Let's Encrypt `ClusterIssuer`s using `http01.ingress.class: nginx` (l.54, 74). |
| `scripts/automation/components/install_linkerd.sh` | Linkerd via the CLI. Out of scope, see the mesh report. |
| `docker_files/ci_images/ubuntu_ci_minimal/Dockerfile` | `ubuntu:26.04`, kubectl 1.37 (pkgs.k8s.io), helm (buildkite apt, **unpinned**), helm-diff 3.15.15, helmfile 1.8.2, yq 4.54.1, tkn 0.46.2, linkerd edge-26.10.1, node 24.22.1, uv 0.13.6, plus git, git-crypt, jq, gnupg, mysql-client and skopeo from apt (l.33-41, 62-131). The image is built multi-arch (amd64/arm64) by `build.sh`. |
| `tekton_ci/tasks`, `tekton_ci/pipelines` | `tekton.dev/v1` (36 manifests) and `triggers.tekton.dev/v1beta1` (6). Default images: kaniko `gcr.io/kaniko-project/executor:debug` (`tasks/build-and-push-docker-image.yml:15`, `pipelines/build-pipeline-ci-images.yml:65`, `pipelines/docker-build-pipeline.yml:65`, `pipelines/build-pipeline-public-ci-images.yml:31`), `docker.io/hashicorp/terraform:light` (`tasks/terraform-cli.yml:20`), `maven:3.8.4-openjdk-17-slim` (`tasks/mvn-package.yml:13`), and `julianweberdev/ubuntu-ci-minimal:latest`. |
| `templates/environment/mirrored_docker_images.json.template` | Mirrors `ubuntu:24.04` (l.6, while the CI image is now 26.04), `hashicorp/terraform:latest` (l.12) and `gcr.io/kaniko-project/executor:debug` (l.18). |
| `pyproject.toml`, `.python-version`, `uv.lock` | `requires-python = ">=3.9"` (l.6); `.python-version` = `3.9`; locked `kubernetes` 33.1.0 (`uv.lock` l.370-371). Other dependencies: click, cryptography, python-gnupg, pyyaml, pytest 8.1.1. |
| `src/kubementat/config.py:19,27` | `TEKTON_API_VERSION = "v1"`, `TRIGGERS_API_VERSION = "v1beta1"`. Both are correct for the pinned releases. |
| Secrets | git-crypt over `platform_config/**.encrypted.*`, GPG keys. |
| E2E target | k3s (`tests/e2e/stages/01-provision.sh:85-90`), which ships its own **Traefik**, CoreDNS, servicelb and local-path. `docs/plans/virt-runner-setup-research.md:82,265` notes that Traefik runs next to ingress-nginx. |

---

## 3. Master table

Abbreviations:
- **Effort:** S < 1 day, M = 1-3 days, L > 3 days.
- **Priority:** P1 = must, P2 = should, P3 = nice to have, P4 = keep.

Latest versions come from the Helm repo `index.yaml` files and GitHub releases fetched on 2026-10-27 unless a source says otherwise.

| Component | Where in repo | Current pin | Latest (date) | Status | Recommendation | Effort | Prio |
|---|---|---|---|---|---|---|---|
| ingress-nginx | `helmfile.yaml.gotmpl:50-51,159-168`; `routing/nginx_ingress_controller...template`; `install_routing.sh:54,74`; `scripts/utilities/kubernetes/create_ingress_yml.sh:36` | unpinned (commented 4.4.0 = controller 1.5.1, 2022) | chart 4.15.1 / v1.15.1 (2026-03-19), **final** | **Retired**: best-effort maintenance ended March 2026, repo archived [1][2] | **Replace** with Traefik 3.7 (Gateway API + Ingress + nginx-annotation compatibility) or Envoy Gateway 1.9; adopt Gateway API (§4.1) | M-L | P1 |
| GitBucket (int128 chart) | `helmfile.yaml.gotmpl:60-61,217-225`; `gitbucket/values...template` | unpinned | chart 4.34.0 (**2020-07-26**), repo inactive since 2020; GitBucket app itself is alive (4.48.0, 2026-09-20) | **Chart dead**; uses `apps/v1beta2` + `extensions/v1beta1`; `int128/gitbucket` image is amd64-only [29] | **Remove** and consolidate onto Forgejo | S | P1 |
| Gitea | `helmfile.yaml.gotmpl:62-63,228-236`; `gitea/values...template:94-108` | unpinned | chart 12.7.0 / Gitea 1.27.0 (2026-07-19); Gitea itself is at **28.2.0** (2026-10-18; renumbered from 1.27 → 28) [26][27] | Maintained (Gitea Ltd). Chart lags the app; chart defaults pull `bitnamilegacy/*` images | **Replace with Forgejo** (§4.3). `dl.gitea.io` still serves the same index as `dl.gitea.com` [27] | M | P1 |
| Forgejo (new) | – | – | Forgejo 17.0.1 (2026-10-26); LTS 15.0.11; chart v17.3.2 (2026-10-26) → appVersion 15.0.11 [21][22] | Maintained, GPLv3+, community-governed [24] | **Add** as Kubementat's only git forge | M | P1 |
| Grafana | `helmfile.yaml.gotmpl:42-43,110-121`; `monitoring/grafana_values...template:77-104` | **9.0.0** (Grafana 12.0.0, 2025-05-07) | `grafana-community/grafana` **14.2.0 / Grafana 13.3.0 (2026-10-27)**; old repo stops at 10.5.15 (deprecated, 2026-01-30) [8][9] | Chart repo **moved**; Grafana itself is AGPL-3.0 (unchanged since 2021) | **Switch repo** and upgrade through chart majors 10-14; drop Angular plugins (§4.2) | M | P1 |
| Tekton Pipelines | `static.json.template:35`; `install_tekton.sh:46` | 1.17.1 | 1.17.1 (2026-10-23). LTS line 1.15.5 (2026-10-23) | 1.17 **EOL 2026-10-31**; 1.15 LTS EOL 2027-07-31 [31] | **Pin 1.15.5 LTS** | S | P1 |
| Tekton Triggers | `static.json.template:36`; `install_tekton.sh:108-109` | 0.37.2 | 0.37.2 (2026-10-22) | LTS, EOL 2027-08-06 [31] | Keep | – | P4 |
| Tekton Dashboard | `static.json.template:34`; `install_tekton.sh:75` | 0.73.0 | 0.73.0 (2026-10-05); LTS 0.71.0 | 0.73 EOL 2026-11-04. 0.71 LTS only states support for Triggers 0.36, but 0.73 states "Pipelines v1.15.x LTS, v1.17.x, and Triggers v0.37.x LTS" [31] | Keep 0.73.0 with Pipelines 1.15.5; move to the next Dashboard LTS when it appears [inference] | S | P2 |
| tkn CLI | `Dockerfile:35` | 0.46.2 | 0.46.2 (2026-10-23) | Current | Keep | – | P4 |
| Tekton Operator (option) | – | – | tekton-operator 0.81.2 (2026-10-09) | Maintained | Optional replacement for release-YAML installs and the cleanup CronJob | M | P3 |
| HashiCorp Vault | `helmfile.yaml.gotmpl:56-57,195-203`; `vault/vault_values...template` | unpinned | chart 0.34.1 / Vault 2.0.4 (2026-08-13); Vault core v2.2.1 (2026-10-22) | **BSL 1.1** (not OSI), IBM-owned [16][17] | **Replace with OpenBao** chart 0.30.6 / 2.7.2 (§4.5) | S-M | P1 |
| OpenBao (new) | – | – | `openbao/openbao` 0.30.6 / v2.7.2 (2026-10-27); needs K8s ≥1.30 [18] | MPL-2.0, Linux Foundation | Add as the vault replacement | S-M | P1 |
| prometheus | `helmfile.yaml.gotmpl:44-45,74-86`; `monitoring/prometheus_values...template` | unpinned (commented 18.1.0 = Prometheus 2.39.1, 2022) | **29.39.0 / Prometheus v3.15.1 (2026-10-27)** [12] | Maintained, Apache-2.0 | **Pin 29.x**; check Prometheus 3 migration [13] | S-M | P2 |
| prometheus-blackbox-exporter | `helmfile.yaml.gotmpl:88-97` | unpinned (commented 7.1.3 = 0.22.0) | 11.20.2 / v0.29.1 (2026-10-22) | Maintained | Pin 11.20.2 | S | P2 |
| helm-exporter (sstarcher) | `helmfile.yaml.gotmpl:48-49,99-108` | unpinned (commented 1.2.3) | 1.4.0 (2026-10-14) [14] | Maintained; `docker.io/sstarcher/helm-exporter:1.4.0` is amd64/arm64/ppc64le | Keep, **pin 1.4.0+e2eee28** | S | P2 |
| kube-state-metrics repo | `helmfile.yaml.gotmpl:46-47` | (no release uses it) | repo frozen at 2.13.3 (2021, deprecated) [45] | Deprecated; KSM now ships as a subchart of `prometheus` | **Remove** repo entry | S | P2 |
| cert-manager | `helmfile.yaml.gotmpl:52-53,148-157`; `routing/cert_manager...template:4` | unpinned (commented v1.10.1, 2022) | **v1.21.3 (2026-10-23)** [15] | Maintained (CNCF graduated), Apache-2.0 | Pin v1.21.3; `crds.enabled/keep`; Gateway API solver | S | P2 |
| trust-manager | – | – | v0.26.0 (2026-10-27) | Maintained | Only if an internal CA is needed | S | P3 |
| GitLab | `helmfile.yaml.gotmpl:58-59,206-214`; `gitlab/values...template` | unpinned | chart 10.5.1 / GitLab 19.5.1 (2026-10-21) | Maintained; chart default `global.edition: ee` (verified in chart 9.11.14 values); bundles Bitnami Postgres/Redis | **Remove from default helmfile** in favour of Forgejo, or pin with `edition: ce` | S | P2 |
| Polaris (commented) | `helmfile.yaml.gotmpl:40-41,136-145`; `polaris/values...template:1-5` | – (commented 5.6.1) | 6.0.1 / 10.2.4 (2026-09-16) | Maintained; the official image is now multi-arch (amd64/arm64/arm), so the `warpigg/polaris-arm` hint is obsolete | Delete, or re-enable pinned at 6.0.1 without the ARM override | S | P3 |
| Mailu (commented) | `helmfile.yaml.gotmpl:64-65,239-247` | – | 2.8.0 / app 2024.06.58 (2026-09-19); Mailu 2024.06.67 (2026-10-23) | Maintained, but still on the 2024.06 line; bundles Bitnami Redis/Postgres/MariaDB | **Remove** (mail is out of platform scope [inference]) | S | P3 |
| CoreDNS (commented) | `helmfile.yaml.gotmpl:66-67,250-258` (release named `registry` in `kube-system`) | – | chart 1.49.0 / CoreDNS 1.14.7 (2026-10-19); CoreDNS 1.15.0 upstream | Maintained | **Remove**: every distro (k3s included) ships its own CoreDNS, so a second release in `kube-system` would conflict [inference] | S | P3 |
| Wekan (commented) | `helmfile.yaml.gotmpl:68-69,261-269` | – | 12.65.0 / v12.65 (2026-10-25) | Maintained; images multi-arch; chart uses `ferretdb:latest` | **Remove** (not platform scope) | S | P3 |
| Loki / loki-stack / promtail | `helmfile.yaml.gotmpl:124-133` | unpinned | `loki-stack` 2.10.3 and promtail 6.17.1 both **deprecated** in the index | → see log-aggregation report | – | – | – |
| Linkerd | `helmfile.yaml.gotmpl:54-55,170-192`; `install_linkerd.sh`; `Dockerfile:39` | edge-26.10.1 CLI | → see service-mesh report | – | – | – | – |
| kaniko | task/pipeline defaults (see §2); `mirrored_docker_images.json.template:18` | `gcr.io/kaniko-project/executor:debug` (image dated 2025-05-23) | Google repo **archived** (June 2025). Forks: `osscontainertools/kaniko` v1.29.0 (2026-10-25, free images on ghcr) and `chainguard-dev/kaniko` v1.25.20 (source only; images for Chainguard customers) [34] | Unmaintained upstream | **Replace** with `ghcr.io/osscontainertools/kaniko:v1.29.0-debug` (multi-arch incl. arm64), or BuildKit rootless `moby/buildkit:v0.34.1-rootless` | S | P1 |
| Terraform task image | `tekton_ci/tasks/terraform-cli.yml:20`; `mirrored_docker_images.json.template:12` | `hashicorp/terraform:light` (= Terraform **1.4.2, 2023-03-16, amd64 only**) | Terraform 1.17.0 (BSL); OpenTofu 1.13.2 (2026-10-21, MPL-2.0) | BSL since 1.6 | **Replace** with `ghcr.io/opentofu/opentofu:1.13.2` (386/amd64/arm/arm64) | S | P2 |
| Maven task image | `tekton_ci/tasks/mvn-package.yml:13` | `maven:3.8.4-openjdk-17-slim` (built 2022-03-02) | `maven:3.9-eclipse-temurin-21` (multi-arch) | `openjdk` image variants are no longer updated [inference] | Update | S | P2 |
| Helm | `Dockerfile:74-82` (apt, unpinned) | apt latest (= 4.x) | v4.3.1 (2026-10-15) | Helm 3: last minor 2026-09-09, security fixes until **2027-02-10** [35] | Pin `helm=4.3.*`; Kubementat already handles `--verify=false` for helm-diff | S | P2 |
| helm-diff | `Dockerfile:38,86` | 3.15.15 | 3.15.15 (2026-09-26) | Current | Keep | – | P4 |
| helmfile | `Dockerfile:37` | 1.8.2 | 1.8.2 (2026-10-16) | Current (v1 line, `.gotmpl` already used) | Keep | – | P4 |
| kubectl | `Dockerfile:34` | 1.37 | v1.37.1 (2026-09-23) | Current. K8s 1.34 is EOL **2026-10-27** [36] | Keep; document a supported cluster range (1.35-1.37) | – | P4 |
| yq | `Dockerfile:36` | 4.54.1 | 4.54.1 (2026-09-29) | Current | Keep | – | P4 |
| uv | `Dockerfile:41` | 0.13.6 | 0.13.6 (2026-10-27) | Current | Keep | – | P4 |
| Node.js | `Dockerfile:40` | 24.22.1 | 26.12.1. Node 26 LTS from 2026-10-28; Node 24 active support ended 2026-10-20, EOL 2028-04-30 [36] | Supported (maintenance) | Bump to 26 LTS on the next image build | S | P3 |
| Base image | `Dockerfile:33`; `mirrored_docker_images.json.template:6` | `ubuntu:26.04` (CI), `ubuntu:24.04` (mirror list) | 26.04 LTS (EOL 2031-05-29) | Current | Keep; align the mirror list to 26.04 | S | P3 |
| git-crypt | `Dockerfile:80` (apt) | Ubuntu package | 0.8.0 (2025-09-24, the first release in years) [43] | Low-activity, single maintainer, GPL-3.0 | Keep for now; SOPS+age as medium-term option (§4.5) | – | P3 |
| skopeo | `Dockerfile:80` (apt) | Ubuntu package | v1.24.1 (2026-09-16) | Current | Keep | – | P4 |
| Python runtime | `pyproject.toml:6`, `.python-version` | **3.9** | 3.15.2 (3.15 released 2026-10-09); 3.14, 3.13, 3.12 and 3.11 supported | 3.9 EOL 2025-10-31, 3.10 EOL 2026-10-01 [36] | `requires-python >=3.12`, `.python-version` 3.14 [inference: Ubuntu 26.04's own python3 is 3.14 or newer, not verified] | S | P1 |
| kubernetes (Python) | `pyproject.toml`, `uv.lock:370-371` | 33.1.0 | **37.0.1** (2026-10-09), requires Python ≥3.10 [37] | Maintained | Bump with the Python bump | S | P1 |
| pytest | `pyproject.toml` dev group | 8.1.1 (pinned) | 9.2.0 (2026-10-27) | – | Bump | S | P3 |
| Example HPA | `examples/helm_charts/nginx-example/templates/hpa.yaml:2` | `autoscaling/v2beta1` | `autoscaling/v2` | Removed in K8s 1.25 [38] | Fix | S | P2 |
| Commented Tekton dashboard Ingress | `install_tekton.sh:80-103` | `extensions/v1beta1` | – | Removed in 1.22 [38] | Delete the block, or replace it with an HTTPRoute | S | P3 |

---

## 4. Per-component sections (replacements and non-trivial upgrades)

### 4.1 ingress-nginx → Gateway API (Traefik as the default controller)

**Facts.**
- Kubernetes SIG Network and the Security Response Committee announced the retirement in November 2025: best-effort maintenance until March 2026, then no releases, bug fixes or security updates [1].
- The final release in the chart index is chart 4.15.1 / controller 1.15.1 (2026-03-19) [7].
- The GitHub repo is archived; the last push was 2026-03-23 [2].
- Existing installs keep running but get no CVE fixes. ingress-nginx had a series of critical admission-controller CVEs in 2025 [unverified, recalled background].
- ingress2gateway reached 1.0 on 2026-03-20 and translates more than 30 common `nginx.ingress.kubernetes.io/*` annotations. It is explicitly a migration *assistant*: it warns on `configuration-snippet` and maps timeouts and body size imperfectly [3]. Latest is v1.2.0 (2026-07-07).
- Gateway API itself is at v1.6.3 (2026-10-06). TCPRoute and UDPRoute graduated to Standard in v1.6 [5].

**Candidates** (all Apache-2.0 or MIT, all multi-arch):

| Controller | Latest | Notes for Kubementat |
|---|---|---|
| **Traefik** | v3.7.16, chart `traefik/traefik` 42.1.3 (repo `https://traefik.github.io/charts`) | k3s's bundled default. amd64/arm/arm64/riscv64 image. Serves Ingress, Gateway API (HTTPRoute, TLSRoute, TCPRoute) **and** a `kubernetesIngressNGINX` provider (≥3.6.2) that understands most nginx annotations, so existing team Ingresses keep working during migration [4]. Known gaps: `configuration-snippet`, some regex and trailing-slash semantics, and request buffering defaults [4]. MIT licensed, single binary, small footprint (a "k3s + Traefik under 70 MB" claim is anecdotal [unverified]). |
| **Envoy Gateway** | v1.9.3 (2026-10-26) | The reference-grade Gateway API implementation (CNCF Envoy). amd64/arm64 only. Separate control plane plus an Envoy data plane per Gateway, so it is heavier than Traefik [inference]. Gateway API only, so every team Ingress must be converted first. |
| NGINX Gateway Fabric | v2.8.0 (2026-10-14) | F5/NGINX, Apache-2.0. Keeps NGINX semantics. Gateway API only. |
| kgateway | v2.4.7 | Envoy-based (ex-Gloo, CNCF sandbox). Has an ingress-nginx migration guide. |
| HAProxy Kubernetes Ingress | v3.2.18 | Mature, but Gateway API support is less complete [unverified]. |
| Cilium / Istio | – | Only sensible if Kubementat adopts them as CNI or mesh. Coordinate with the mesh report: if it recommends Istio, its Gateway API support can cover north-south routing as well. |

**Recommendation [inference].**
1. Install **Traefik v3.7** as `routing/traefik` via helmfile, with both `kubernetesGateway` and `kubernetesIngressNGINX` providers enabled, and Gateway API CRDs (standard channel v1.6.x) installed first.
2. Make **Gateway API the documented contract** for team apps: a shared `Gateway` per environment, with teams owning `HTTPRoute`s. Replace `scripts/utilities/kubernetes/create_ingress_yml.sh` with an HTTPRoute generator.
3. Keep ingress-nginx-style Ingresses working only as a transition path, and run `ingress2gateway --providers ingress-nginx` to convert team charts.
4. On k3s, either reuse the bundled Traefik (a HelmChartConfig override) or install with `--disable=traefik` and let Kubementat own Traefik. The second is more convention-over-configuration and identical across distros [inference].
5. Envoy Gateway is the fallback if strict conformance matters more than footprint and the gradual migration path.

**cert-manager changes that go with it** [6]:
```yaml
# routing/cert_manager values
crds:
  enabled: true
  keep: true
config:
  apiVersion: controller.config.cert-manager.io/v1alpha1
  kind: ControllerConfiguration
  gatewayAPI:
    enabled: true
```
The ClusterIssuers in `install_routing.sh:54,74` then use `solvers: [{ http01: { gatewayHTTPRoute: { parentRefs: [...] } } }]` instead of `ingress.class: nginx`. Install the Gateway API CRDs **before** cert-manager starts [6].

**Risks.**
- Team apps that rely on nginx annotations or snippets.
- The Grafana "nginx-ingress" dashboard (gnetId 9614, `grafana_values...template:100-101`) and the `prometheus.io/scrape` annotations on the controller service become obsolete. Replace them with Traefik's metrics and dashboards.
- SSH for the git forge needs a TCPRoute or LoadBalancer.

### 4.2 Grafana: repository move, chart 9 → 14, Grafana 12 → 13

**Facts.**
- `https://grafana.github.io/helm-charts` → `grafana/grafana` last released 10.5.15 (2026-01-30), and that entry carries `deprecated: true` [8].
- The Grafana community announced the move of community-maintained charts with a go-live of 2026-01-30 [10].
- The successor is **`grafana-community/grafana`**, at `https://grafana-community.github.io/helm-charts` or `oci://ghcr.io/grafana-community/helm-charts/grafana`. It is at **14.2.0 / Grafana 13.3.0** (2026-10-27) and requires K8s ≥1.25 [9].
- Grafana itself remains AGPL-3.0, which is fine for self-hosting.
- `loki-stack` and promtail in the old repo are also deprecated (see the log-aggregation report).

**Breaking changes from the chart README (9.0.0 → 14.x)** [9]:
- **10.0.0:** alerting files are templated, so `{{ $labels }}` must be escaped.
- **11.0.0:** minimum K8s 1.25, and deprecated APIs were removed.
- **12.0.0:** Grafana 13 by default, and an image renderer token is generated.
- **13.0.0:** `readOnlyRootFilesystem: true` and **distroless** tag by default. `/tmp` is now an emptyDir. `GF_*__FILE` and `GF_INSTALL_PLUGINS` env vars are unsupported; use the `plugins` value or `GF_PLUGINS_PREINSTALL_SYNC`.
- **14.0.0:** `values.schema.json` validation, so a wrongly typed value fails the install.

**Kubementat-specific fixes.**
- `grafana_values...template:77-80` installs `grafana-piechart-panel`. Pie chart has been a core panel since Grafana 8, and AngularJS plugins cannot load at all since Grafana 12 [11]. Remove `grafana-piechart-panel`. Check `digrich-bubblechart-panel` and `grafana-clock-panel` for React (non-Angular) versions [unverified for those two].
- The imported dashboards (gnetId 1860 rev 14, 7587, 12611, 9614, 9367, plus the inline JSON with gnetId 5321/6784) are old revisions. Some use Angular graph panels, which Grafana auto-migrates to timeseries [unverified per dashboard]. Bump revisions and test.
- `kubementat/config.py` and `GRAFANA_SERVICE_SELECTOR` (`app.kubernetes.io/name=grafana,...`) are unaffected; the labels are unchanged in the community chart [inference, verify after `helm template`].

**Target:**
```yaml
repositories:
- name: grafana-community
  url: https://grafana-community.github.io/helm-charts
releases:
- name: grafana
  chart: grafana-community/grafana
  version: "14.2.0"
```
Effort is M, because of the dashboard and plugin review.

### 4.3 Git forges: consolidate on Forgejo (remove GitBucket, Gitea, and GitLab from the default)

**Why Forgejo** (the user already runs it and prefers it):
- **Governance and license.** Forgejo is developed by its community; Codeberg e.V. (a non-profit) holds the domains. Since v9.0 it is GPLv3+ (earlier versions MIT) [24]. Gitea is steered by Gitea Ltd, a for-profit company [inference; that company control triggered the 2022 fork].
- **Release cadence** [21]. Quarterly majors. An **LTS every April** (v7, v11, v15, v19) with about 15 months of support; non-LTS majors live about 3 months.

  | Version | Released | EOL | LTS |
  |---|---|---|---|
  | 14.0 | 2026-01-15 | **2026-04-30** | no |
  | **15.0** | 2026-04-16 | **2027-07-15** | **yes** |
  | 16.0 | 2026-07-16 | 2026-10-29 | no |
  | 17.0 | 2026-10-15 | 2027-01-28 | no |
  | 19.0 | 2027-04-15 | 2028-07-13 | yes |

  Current patch releases (2026-10-26): **17.0.1**, 16.0.7, **15.0.11** [Codeberg releases API].
  > **Note for the user's other project:** the image `codeberg.org/forgejo/forgejo:14` is a **non-LTS major that reached EOL on 2026-04-30** and no longer gets security fixes. Move it to `:15` (LTS until 2027-07-15), or to `:17` if you want to follow every quarterly major. 14 → 15 is a normal forward upgrade; take a backup first [21].
- **ARM64.** `codeberg.org/forgejo/forgejo:15` publishes amd64, arm and arm64 (verified via manifest list). Raspberry Pi works.

**Official Helm chart** [22]:
- **Install:** `helm install forgejo oci://code.forgejo.org/forgejo-helm/forgejo`. It is OCI only; there is no `index.yaml` HTTP repo. Helmfile supports OCI charts directly. Latest release is **v17.3.2 (2026-10-26)**, whose `Chart.yaml` has `appVersion: 15.0.11`, so the chart tracks the **LTS** by default (the chart README says "This chart now uses Forgejo v15 by default").
- **Image:** `code.forgejo.org/forgejo/forgejo`, with `image.rootless: true` by default.
- **Database:**
  - **SQLite is the default.**
  - PostgreSQL, MySQL or MSSQL are supported via `gitea.config.database`, with credentials from a Secret via `gitea.additionalConfigSources`. Upstream recommends PostgreSQL or MySQL beyond about 5 active users.
  - The **bundled PostgreSQL and Valkey subcharts were removed in chart v14**. The only remaining dependency is Bitnami `common` (a library chart, mirrored at `oci://ghcr.io/visualon/bitnamicharts`), so there are **no Bitnami runtime images**.
- **Cache/session/queue:** memory/memory/level by default ("not recommended for production"). External Redis or Valkey is optional.
- **Persistence:** on by default, a 10Gi RWO PVC `gitea-shared-storage`, with `persistence.storageClass` and `claimName` available.
- **Routing:** `ingress.*`, plus **Gateway API `httpRoute.enabled` and `tcpRoute.enabled` (SSH)**. This lines up with §4.1.
- The values still use the `gitea:` top-level key, so Kubementat's Gitea values template ports almost 1:1.

**Kubementat-specific finding: the current Gitea values are already broken** (verified against chart 12.7.0):
- `gitea/values...template:94-108` sets `gitea.database.builtIn.*` and `gitea.cache.builtIn`. Those keys were removed from the Gitea chart several majors ago, so they are silently ignored.
- With the template as written, chart 12.7.0 therefore deploys its **defaults**:
  - `postgresql-ha.enabled: true` (PostgreSQL-repmgr plus pgpool)
  - `valkey-cluster.enabled: true` (3 nodes)
  - all from `bitnamilegacy/*` images
- Since the Bitnami catalog change of August/September 2025, `bitnamilegacy` images receive no updates [28]. The CHANGELOG already records that `docker.io/bitnami/mysql:8.0.30-...` is `NotFound`.
- On a Pi this is heavy (several database pods) and unpatched [inference].

**Migration from Gitea** [23]:
- Transparent in-place upgrades are only supported **from Gitea ≤1.22, via Forgejo v10.0.x**, then upward. Gitea 1.23+ (and the newly renumbered **Gitea 28.x** [26]) cannot be upgraded in place without manual database surgery.
- The supported path for newer Gitea is Forgejo's per-repository **migration feature** (New Migration → Gitea). It copies repos, issues, PRs, labels, milestones and releases via the API. Users, tokens and webhooks must be recreated [unverified for the full field list].
- For Kubementat this is cheap: Gitea is an `extra` component with fresh test data. Recommend "uninstall Gitea, install Forgejo" plus a one-paragraph migration note in the CHANGELOG.

**Forgejo Actions vs Tekton (short):**
- Forgejo Actions (GitHub-Actions-like YAML) needs a separate **forgejo-runner**: latest v13.2.0 (2026-09-18), with an official chart at `code.forgejo.org/forgejo-helm/forgejo-runner` [25].
- Runners usually need Docker/DinD or a Kubernetes-pod backend, which means privileged pods or extra setup [25].
- Kubementat's CI/CD is built around Tekton (cluster-native, tasks/pipelines/triggers already in tree). **Keep Tekton as the platform CI.** Offer Forgejo Actions as an optional, team-level convenience [inference].
- For Tekton Triggers, Forgejo webhooks are Gitea-style. Kubementat only ships GitHub and GitLab trigger templates (`tekton_ci/triggers/templates/{github,gitlab}`). Add a `forgejo` template using a CEL interceptor, and check whether Forgejo's signature header can be validated by the GitHub interceptor (`X-Hub-Signature-256`) [unverified, test before relying on it].

**Docker Compose (user's other project) vs in-cluster Helm.**
- The Compose deploy (SQLite by default, optional Postgres, optional Traefik labels) is simpler: one container, a bind mount, host-level backups.
- For Kubementat, an in-cluster Helm release fits better: everything else is helmfile, and it reuses cert-manager, the Gateway/Traefik, Prometheus (`gitea.metrics.serviceMonitor`) and PVC storage.
- The defaults map one to one: SQLite plus RWO PVC by default, an `extra`-group toggle for external PostgreSQL, and Traefik via Gateway API instead of Docker labels [inference].

**Consolidation recommendation.** **Yes: replace `gitlab`, `gitbucket` and `gitea` with a single `forgejo` release** (component `git`, group `extra`):
- GitBucket is uninstallable (see §3).
- Gitea is redundant with Forgejo and currently misconfigured.
- GitLab:
  - Its chart defaults to **EE** images (`global.edition: ee`, verified).
  - It bundles Bitnami Postgres and Redis.
  - It is far beyond Pi-class resources [inference].
  - It duplicates Tekton with GitLab CI.
  - If anyone needs it, document it as "bring your own GitLab" or keep it in an optional `helmfile.d/` snippet pinned to `10.5.1` with `global.edition: ce`.

Sketch:
```yaml
- name: forgejo
  chart: oci://code.forgejo.org/forgejo-helm/forgejo
  version: "17.3.2"          # appVersion 15.0.11 (LTS)
  namespace: forgejo
  labels: { component: git, component_name: forgejo, group: extra }
  values:
    - forgejo/values.encrypted.yaml   # gitea.admin.*, gitea.config.database (sqlite3 default), persistence, httpRoute
```

### 4.4 Prometheus stack: pin and upgrade to Prometheus 3; consider the operator-based stack later

**Facts.**
- `prometheus-community/prometheus` **29.39.0 / Prometheus v3.15.1** (2026-10-27). It bundles alertmanager 2.1.2, kube-state-metrics 8.7.1, node-exporter 4.60.1 and pushgateway 3.10.0 as OCI subcharts [12].
- `kube-prometheus-stack` is at 92.14.1 (operator v0.94.2).
- VictoriaMetrics `victoria-metrics-single` is 0.50.0 / v1.155.0 and `victoria-metrics-k8s-stack` is 0.97.0 (Apache-2.0, multi-arch incl. armv7) [44].

**Upgrade notes.**
- Kubementat's commented pin 18.1.0 (Prometheus 2.39) means any fresh install already gets Prometheus 3 because the release is unpinned. Existing clusters jump many chart majors on the next sync.
- Read the Prometheus 3 migration guide [13]. Changes include:
  - stricter scrape `Content-Type` handling
  - UTF-8 metric and label names
  - a new log format
  - TSDB format compatibility (Prometheus 2.55+ can read v3 blocks for rollback)
  - removed feature flags
- Kubementat's `prometheus_values...template` is small (an `extraScrapeConfigs` blackbox job, node-exporter disabled), so risk is low.
- **Pin `version: "29.39.0"`.** The Grafana datasource URL `prometheus-server.prometheus.svc.cluster.local` (`grafana_values...template`) does not change.

**Alternatives [inference].**
- **kube-prometheus-stack** brings the Prometheus Operator CRDs (`ServiceMonitor` and `PodMonitor`). Many charts expose them: Forgejo, cert-manager, Traefik, OpenBao, Tekton. It also bundles Grafana, which would replace the separate Grafana release. That is a bigger restructure (M-L).
- **VictoriaMetrics single** is materially lighter on RAM and disk for Pi-class nodes and understands ServiceMonitors via its operator.
- Suggested path: pin `prometheus` now (S). Re-evaluate `kube-prometheus-stack` vs VictoriaMetrics when the log-aggregation and mesh decisions settle, because both reports touch Grafana and scrape configuration.

**Exporters.**
- `prometheus-blackbox-exporter` 11.20.2: the module config layout changed across the 7 → 11 majors. Diff values with `helm template` [unverified for specific keys].
- `sstarcher/helm-exporter` 1.4.0: maintained, with a release on 2026-10-14 and repo activity on 2026-10-19. The Docker Hub image is multi-arch. Keep it and pin it [14].

### 4.5 Secrets: Vault → OpenBao, and the git-crypt question

**Vault facts.**
- HashiCorp relicensed Vault from MPL-2.0 to **BSL 1.1** in August 2023. The repo `LICENSE` still reads "Business Source License" (verified 2026-10-27) [16].
- IBM closed its HashiCorp acquisition in February 2025 [17].
- The chart is `hashicorp/vault` 0.34.1 / Vault 2.0.4 (2026-08-13), while Vault core is at v2.2.1.
- BSL is source-available, not open source. Kubementat's mission is "free & open source, no vendor lock-in", so Vault does not fit as a default [inference].

**OpenBao facts.**
- Linux Foundation project, **MPL-2.0**.
- `openbao/openbao` v2.7.2 (2026-10-26). Chart **`openbao/openbao` 0.30.6** (repo `https://openbao.github.io/openbao-helm`, 2026-10-27) requires K8s ≥1.30 [18].
- The image covers amd64/arm/arm64/ppc64le/riscv64/s390x (verified).
- The chart is derived from Vault's chart (dev, standalone, HA and external modes, plus an injector) [19].
- GitLab's own chart now depends on `openbao` 0.14.0 (seen in its chart dependencies), which suggests mainstream adoption [inference].

**Migration.**
- Kubementat's `vault_values...template` only enables the injector, server and UI. Swapping is: change the repo, the chart name, and `VAULT_SERVICE_SELECTOR`/namespace keys (`static.json.template:37-38`, `kmt tunnel-vault-ui`).
- In-place data migration from Vault is only documented as tested against Vault 1.14.x [19][unverified for newer]. For Kubementat installs (dev data), use a fresh OpenBao install plus re-seeding.
- Effort: S-M, mostly renaming the `vault` component to `openbao`, or keeping the component name and switching the chart.

**External Secrets Operator (optional) [20].**
- `external-secrets` 2.14.0 (2026-10-23, Apache-2.0, multi-arch).
- ESO syncs OpenBao (via its Vault/OpenBao provider) into native Secrets, which is the common pattern instead of the agent injector.
- Caveat: ESO paused releases in August 2025 for lack of maintainers and has since resumed [20][unverified resume details]. Release cadence looks healthy again (2.x in 2026).
- Recommend ESO only if teams need secrets from OpenBao in their apps; it is not required for the platform itself.

**git-crypt vs SOPS [inference, partly from secondary sources].**
- git-crypt 0.8.0 (Sept 2025) works, is offline, and has no lock-in. But it encrypts whole files (no meaningful diffs), relies on GPG key management, and has a very low release cadence [43].
- **SOPS** (v3.13.3, CNCF, MPL-2.0) with **age** keys:
  - encrypts values field by field, so YAML/JSON stays diffable
  - supports multiple recipients without GPG
  - integrates with helmfile via `secrets:` (helm-secrets plugin) or `ref+sops://` (vals) [unverified exact helmfile syntax]
  - is supported natively by Flux and Argo CD plugins
- For Kubementat (`platform_config/**.encrypted.json|yaml`), a SOPS migration would touch `crypto_utils.py`, `README_GIT_CRYPT.md`, the CI image and every `jq` read of `static.encrypted.json`, so it is M-L. Recommend it as a **planned P3**, not urgent: git-crypt is not deprecated.

### 4.6 Tekton: pin the LTS line; keep release-YAML installs

**Facts** (from `releases.md` in each repo) [31]:
- **Pipelines:**
  - v1.17 initial 2026-10-01, **EOL 2026-10-31**
  - **v1.15 LTS** initial 2026-07-31, EOL **2027-07-31**, latest patch **v1.15.5 (2026-10-23)**
  - v1.12 LTS EOL 2027-05-04
  - Policy: monthly releases; four LTS per year; non-LTS supported about one month.
- **Triggers:** **v0.37 LTS**, EOL 2027-08-06, latest v0.37.2. Still `triggers.tekton.dev/v1beta1`, which matches `config.py:27`.
- **Dashboard:** v0.73 (non-LTS, EOL 2026-11-04) supports "Pipelines v1.15.x LTS, v1.17.x, and Triggers v0.37.x LTS". v0.71 LTS (EOL 2027-08-04) states support for Pipelines 1.15 and Triggers **0.36**.
- **Governance:** Tekton moved from the CD Foundation to the **CNCF as an incubating project** (TOC vote 2026-03-13, announced 2026-03-24) [32]. This only changes governance, not install methods.

**Recommendation.**
```json
"TEKTON_VERSION_PIPELINE": "1.15.5",
"TEKTON_VERSION_TRIGGERS": "0.37.2",
"TEKTON_VERSION_DASHBOARD": "0.73.0"
```
- This is the only combination upstream explicitly lists as supported with both LTS backends.
- Moving Pipelines from 1.17 to 1.15 is a **downgrade** on existing clusters. Downgrading CRD storage versions is risky; fresh installs are fine. Existing clusters should stay on 1.17.x until the next LTS (expected January 2027 per the policy [inference]), and Renovate can track it.
- Keep `kubectl apply` of release YAML from `infra.tekton.dev`. It is what upstream documents, and the repo already handles the bucket move.
- The **Tekton Operator** (0.81.2) is a valid future option. It installs Pipelines, Triggers and Dashboard as one `TektonConfig` and has a built-in **pruner**, which would replace `setup_tekton_pipelinerun_cleanup_job.sh` [33]. It adds a moving part, though, and the e2e harness asserts on CRD groups. Treat it as P3.

### 4.7 CI build images (kaniko, Terraform, Maven) and the CI image itself

- **kaniko.**
  - `GoogleContainerTools/kaniko` is archived; the last release was v1.24.0 (2025-05-23) [34].
  - Chainguard maintains a source fork but publishes images only to customers.
  - `osscontainertools/kaniko` (v1.29.0, 2026-10-25) publishes free `ghcr.io/osscontainertools/kaniko:<ver>[-debug]` multi-arch images (verified for amd64/arm/arm64).
  - Lowest-effort fix: change the four `builder-image` defaults and `mirrored_docker_images.json.template:18` to `ghcr.io/osscontainertools/kaniko:v1.29.0-debug`. The `-debug` variant includes the busybox shell the task uses [inference from the existing `:debug` usage].
  - Longer term, **BuildKit rootless** (`moby/buildkit:v0.34.1-rootless`) or **Buildah** are better-supported daemonless builders. Rootless BuildKit needs relaxed seccomp/AppArmor on some nodes [unverified on k3s/Pi].
- **Terraform → OpenTofu.**
  - `hashicorp/terraform:light` is a stale tag: Terraform 1.4.2, 2023-03-16, **amd64-only**, so it cannot run on a Pi (verified).
  - Use `ghcr.io/opentofu/opentofu:1.13.2` (MPL-2.0, multi-arch). The CLI is `tofu`, so the task script must change `terraform` → `tofu`.
  - Note: OpenTofu's image docs have at times described it as a base image for copying the binary [unverified for 1.13].
- **Maven:** `maven:3.8.4-openjdk-17-slim` was built 2022-03-02. Use `maven:3.9-eclipse-temurin-21`.
- **CI image.**
  - Pin Helm (`apt-get install helm=4.3.*`), because an unpinned apt install will move with Helm 4 minors [35].
  - Bump Node 24 → 26 LTS on the next build.
  - Everything else is current (§3).
  - Another agent is currently editing `docker_files/**`, so coordinate with that work rather than duplicating it (see `TODOs.md`).

### 4.8 Commented-out components: delete, don't update

- **Polaris:** maintained (chart 6.0.1, Polaris 10.2.4). The image at `us-docker.pkg.dev/fairwinds-ops/oss/polaris:v10.2.4` is multi-arch, so the `warpigg/polaris-arm` comment in `polaris/values...template:1-5` is obsolete. Either re-enable it, pinned, as an `analytics` component, or delete the repo, release and template.
- **Mailu:** the chart is maintained (2.8.0), but the app is still the 2024.06 series and bundles Bitnami Redis/Postgres/MariaDB. A mail server is not platform tooling. Delete it.
- **Wekan:** maintained (12.65.0), but it is a team application, not platform tooling. Delete it (teams can deploy it like any app).
- **CoreDNS:** the release is named `registry` in `kube-system`, a copy-paste error [inference]. Every distribution ships its own CoreDNS. Delete it.
- `goldilocks`/`vpa` namespaces exist only in `constants.py:34,46` and `static.json.template:16,39`, with no installer anywhere. Fairwinds charts exist (goldilocks 11.2.0, vpa 5.1.0). Either add them or drop the keys [inference].

---

## 5. Cross-cutting recommendations

1. **Pin every chart version in the helmfile.**
   - Only Grafana is pinned today, and to a chart in a deprecated repo. Unpinned releases mean `helmfile apply` on two days can produce two different platforms, and a fresh install already gets Prometheus 3 or ingress-nginx's final release without anyone deciding it.
   - Store pins next to the release (`version:`). Optionally centralize them in `static.json` like the Tekton pins, for one place to look [inference].
2. **Adopt Renovate (self-hosted).**
   - It parses `helmfile.yaml.gotmpl` natively (helm and docker datasources) [39].
   - With custom regex managers it can also bump `static.json.template` Tekton pins, Dockerfile `ARG`s and Tekton task image defaults.
   - It supports the **Forgejo** platform [39], so it runs against the recommended forge with no SaaS dependency. Renovate is AGPL-3.0; self-hosting is fine.
3. **Gateway API as the routing contract** (§4.1). This also simplifies the git forge (Forgejo `httpRoute`/`tcpRoute`), cert-manager (`gatewayHTTPRoute` solver) and future mesh choices.
4. **Remove dead and commented components:**
   - repos: `kube-state-metrics`, `int128.github.io`, `mailu`, `coredns`, `wekan`; also `polaris` unless re-enabled
   - releases: gitbucket, gitea, gitlab (in favour of forgejo), and the commented blocks
   - Commented YAML ages silently. These entries pointed at a 2020 chart and Kubernetes APIs removed years ago.
5. **Avoid Bitnami runtime images.**
   - Since late 2025 the free Bitnami catalog no longer ships versioned, patched images (`bitnamilegacy` is frozen) [28].
   - Prefer charts without Bitnami database subcharts (Forgejo, OpenBao, Traefik), and for databases use an operator such as CloudNativePG [inference].
   - This also covers the open `TODOs.md` item "Refactoring backing services".
6. **Prefer OCI chart references where upstream recommends them:** Grafana community, Forgejo, Bitnami common.
7. **Document a supported Kubernetes range.**
   - K8s 1.34 reaches EOL today (2026-10-27); 1.35-1.37 are supported [36].
   - OpenBao's chart already requires ≥1.30 and Grafana's ≥1.25.
   - Recommend stating "1.35+".
8. **Clean up obsolete API usage:**
   - `autoscaling/v2beta1` in the example HPA
   - the commented `extensions/v1beta1` Ingress in `install_tekton.sh`
   - `kubernetes.io/ingress.class` annotations (`create_ingress_yml.sh:36`, commented examples in the gitea/gitbucket templates). Use `ingressClassName`, or HTTPRoutes after §4.1.
   - the `"extensions"` apiGroup in `helm-deployer-cluster-role` (`install_tekton.sh:60`) is harmless but obsolete.
9. **Python:**
   - `requires-python >= "3.12"` and `.python-version` → 3.14
   - re-lock with `kubernetes` 37.x, click 8.5 and pytest 9
   - run the 286-test unit suite
   - The CLI container already brings `uv`, so the interpreter can be fetched with `uv python install` [inference].

---

## 6. Suggested sequencing

1. **Week 1 (S items, low risk):**
   - Pin all charts at today's versions.
   - Pipelines → 1.15.5 for fresh installs.
   - kaniko image → osscontainertools.
   - Terraform → OpenTofu; Maven image.
   - Remove dead repos and commented releases.
   - Python and kubernetes-client bump.
   - Example HPA fix.
2. **Week 2:** Grafana → grafana-community 14.x (plugins and dashboards); cert-manager v1.21.3 with `crds.*`.
3. **Weeks 2-3:**
   - Forgejo release replacing gitea/gitbucket/gitlab.
   - OpenBao replacing Vault.
   - Forgejo trigger template for Tekton.
4. **Weeks 3-5:** Gateway API + Traefik; cert-manager Gateway solver; migrate team examples; delete ingress-nginx.
5. **Afterwards:** Renovate; decide on `kube-prometheus-stack` vs VictoriaMetrics together with the log-aggregation and mesh outcomes; SOPS evaluation.

---

## 7. Gaps and uncertainties

- Resource footprints of Traefik vs Envoy Gateway on Raspberry Pi were not measured. The claims are inference or anecdote. Measure with `kubectl top` on the e2e VM.
- Exact helm values breaking changes for `prometheus-blackbox-exporter` 7 → 11 and `prometheus` 18 → 29 were not diffed key by key. Run `helmfile diff` against a test cluster.
- Whether Forgejo webhooks validate through Tekton's GitHub interceptor is untested.
- The Gitea 28 renumbering date and details rest on secondary news coverage [26]; the GitHub release list confirms v28.0.0 on 2026-09-29.
- The ESO resumption details come from a competitor's blog [20].
- Ubuntu 26.04's default `python3` minor version was not verified.

---

## 8. Sources

All accessed 2026-10-27.

1. Kubernetes Blog, "Ingress NGINX Retirement: What You Need to Know" (2025-11-11). https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/
2. GitHub `kubernetes/ingress-nginx`: archived, last release controller-v1.15.1 (2026-03-19). https://github.com/kubernetes/ingress-nginx
3. Kubernetes Blog, "Announcing Ingress2Gateway 1.0: Your Path to Gateway API" (2026-03-20). https://kubernetes.io/blog/2026/03/20/ingress2gateway-1-0-release ; releases: https://github.com/kubernetes-sigs/ingress2gateway/releases
4. Traefik docs, Kubernetes Ingress NGINX provider. https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/ingress-nginx/ ; migration guide: https://doc.traefik.io/traefik/migrate/nginx-to-traefik/ ; chart index: https://traefik.github.io/charts/index.yaml
5. Gateway API implementations list. https://gateway-api.sigs.k8s.io/implementations/ ; releases: https://github.com/kubernetes-sigs/gateway-api/releases ; Envoy Gateway: https://github.com/envoyproxy/gateway/releases ; NGINX Gateway Fabric: https://github.com/nginx/nginx-gateway-fabric/releases ; kgateway: https://github.com/kgateway-dev/kgateway/releases
6. cert-manager docs, "Annotated Gateway resource / Gateway API". https://cert-manager.io/docs/usage/gateway/
7. ingress-nginx Helm index. https://kubernetes.github.io/ingress-nginx/index.yaml
8. Grafana legacy Helm index (deprecated entries). https://grafana.github.io/helm-charts/index.yaml
9. grafana-community Helm charts: Grafana chart README and upgrade notes. https://github.com/grafana-community/helm-charts/tree/main/charts/grafana ; index: https://grafana-community.github.io/helm-charts/index.yaml
10. Grafana Community forum, "Helm Repository Migration: Grafana Community Charts" (2026-01-13). https://community.grafana.com/t/helm-repository-migration-grafana-community-charts/160983
11. Grafana Labs blog, "AngularJS support will be removed in Grafana 12" (2025-04-03). https://grafana.com/blog/2025/04/03/angularjs-support-will-be-removed-in-grafana-12-what-you-need-to-know ; Pie chart plugin page: https://grafana.com/plugins/grafana-piechart-panel
12. prometheus-community Helm index. https://prometheus-community.github.io/helm-charts/index.yaml
13. Prometheus 3 migration guide. https://prometheus.io/docs/prometheus/latest/migration/
14. GitHub `sstarcher/helm-exporter` (release 1.4.0, 2026-10-14). https://github.com/sstarcher/helm-exporter ; index: https://shanestarcher.com/helm-charts/index.yaml
15. Jetstack Helm index and cert-manager chart values (v1.21.3: `installCRDs` deprecated). https://charts.jetstack.io/index.yaml ; https://github.com/cert-manager/cert-manager/releases ; trust-manager: https://github.com/cert-manager/trust-manager/releases
16. HashiCorp Vault LICENSE (BSL 1.1). https://github.com/hashicorp/vault/blob/main/LICENSE ; chart index: https://helm.releases.hashicorp.com/index.yaml
17. heise, "Acquisition completed: IBM integrates HashiCorp" (Feb 2025). https://heise.de/-10303743 ; Linuxiac: https://linuxiac.com/ibm-completes-6-4-billion-acquisition-of-hashicorp/
18. OpenBao: GitHub `openbao/openbao` (v2.7.2) and Helm index (0.30.6). https://github.com/openbao/openbao ; https://openbao.github.io/openbao-helm/index.yaml
19. OpenBao migration guide. https://openbao.org/docs/guides/migration/ ; third-party experience: https://alexlebens.dev/blog/openbao-migration/
20. External Secrets Operator OpenBao provider. https://external-secrets.io/latest/provider/openbao/ ; Infisical, "External Secrets Operator paused" (competitor source). https://infisical.com/blog/external-secrets-operator-paused ; ESO Helm index: https://charts.external-secrets.io/index.yaml
21. Forgejo release schedule. https://forgejo.org/docs/next/admin/release-schedule/ ; releases API: https://codeberg.org/api/v1/repos/forgejo/forgejo/releases
22. Forgejo Helm chart (README, Chart.yaml, values.yaml; release v17.3.2). https://code.forgejo.org/forgejo-helm/forgejo-helm
23. Forgejo, "Gitea 1.22 is the last version to allow a transparent upgrade to Forgejo" (Dec 2024). https://forgejo.org/2024-12-gitea-compatibility
24. Forgejo, "Forgejo is now copyleft, just like Git" (Aug 2024). https://forgejo.org/2024-08-gpl/ ; FAQ: https://forgejo.org/faq/
25. Forgejo Actions admin guide. https://forgejo.org/docs/latest/admin/actions/ ; runner releases: https://code.forgejo.org/forgejo/runner/releases ; runner chart: https://code.forgejo.org/forgejo-helm/forgejo-runner
26. AlternativeTo News, "Gitea drops legacy versioning with major 28.0 release" (Sept 2026). https://alternativeto.net/news/2026/9/gitea-drops-legacy-versioning-with-major-28-0-release-and-adds-audit-logging-bot-accounts/ ; releases: https://github.com/go-gitea/gitea/releases
27. Gitea Helm index and chart 12.7.0 values. https://dl.gitea.com/charts/index.yaml (identical content at `https://dl.gitea.io/charts/index.yaml`)
28. Bitnami catalog change. bitnami/charts issue #35164: https://github.com/bitnami/charts/issues/35164 (referenced, not fetched) ; Northflank summary: https://northflank.com/blog/bitnami-deprecates-free-images-migration-steps-and-alternatives
29. int128 Helm charts (GitBucket chart 4.34.0, 2020). https://int128.github.io/helm-charts/index.yaml ; https://github.com/int128/helm-charts ; GitBucket app: https://github.com/gitbucket/gitbucket/releases
30. GitLab Helm index and chart values (`global.edition: ee`). https://charts.gitlab.io/index.yaml
31. Tekton release policies. Pipelines: https://github.com/tektoncd/pipeline/blob/main/releases.md ; Triggers: https://github.com/tektoncd/triggers/blob/main/releases.md ; Dashboard: https://github.com/tektoncd/dashboard/blob/main/releases.md and release notes https://github.com/tektoncd/dashboard/releases
32. CNCF, "Tekton becomes a CNCF incubating project" (2026-03-24). https://www.cncf.io/blog/2026/03/24/tekton-becomes-a-cncf-incubating-project/ ; Tekton blog: https://tekton.dev/blog/2026/03/25/tekton-joins-the-cncf-as-an-incubating-project/
33. Tekton Operator docs. https://tekton.dev/docs/operator/ ; releases: https://github.com/tektoncd/operator/releases
34. kaniko: GitHub `GoogleContainerTools/kaniko` (archived). https://github.com/GoogleContainerTools/kaniko ; Chainguard, "Fork Yeah: We're Bringing Kaniko Back": https://www.chainguard.dev/unchained/fork-yeah-were-bringing-kaniko-back ; community fork: https://github.com/osscontainertools/kaniko
35. Helm blog, "Helm 3 End of Life". https://helm.sh/blog/helm-v3-end-of-life ; "Helm 4 Released": https://helm.sh/blog/helm-4-released ; releases: https://github.com/helm/helm/releases
36. endoflife.date APIs: Python https://endoflife.date/python , Node.js https://endoflife.date/nodejs , Kubernetes https://endoflife.date/kubernetes , Ubuntu https://endoflife.date/ubuntu ; Python devguide: https://devguide.python.org/versions/
37. PyPI `kubernetes` 37.0.1 (requires Python ≥3.10). https://pypi.org/project/kubernetes/
38. Kubernetes Deprecated API Migration Guide. https://kubernetes.io/docs/reference/using-api/deprecation-guide/
39. Renovate docs, helmfile manager. https://docs.renovatebot.com/modules/manager/helmfile/ ; Forgejo platform: https://docs.renovatebot.com/modules/platform/forgejo/
40. Fairwinds Helm index (polaris 6.0.1, goldilocks 11.2.0, vpa 5.1.0). https://charts.fairwinds.com/stable/index.yaml ; image registry `us-docker.pkg.dev/fairwinds-ops/oss/polaris`
41. Mailu, Wekan and CoreDNS Helm indexes. https://mailu.github.io/helm-charts/index.yaml ; https://wekan.github.io/charts/index.yaml ; https://coredns.github.io/helm/index.yaml
42. OpenTofu releases. https://github.com/opentofu/opentofu/releases ; Terraform releases: https://github.com/hashicorp/terraform/releases
43. git-crypt. https://github.com/AGWA/git-crypt ; SOPS: https://github.com/getsops/sops ; helmfile docs: https://helmfile.readthedocs.io/en/latest/
44. VictoriaMetrics Helm index. https://victoriametrics.github.io/helm-charts/index.yaml
45. Deprecated kube-state-metrics Helm index. https://kubernetes.github.io/kube-state-metrics/index.yaml

Verification performed for this report: Helm `index.yaml` downloads (all repos above), GitHub/Codeberg/Forgejo API release and archive queries, chart tarball inspection (gitea 12.7.0, gitlab 9.11.14, cert-manager v1.21.3, polaris 6.0.1, gitbucket 4.34.0), and `skopeo inspect --raw` manifest checks for ARM64 on the images named in §3 and §4.
