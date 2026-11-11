# Research: Service Mesh Solution for Kubementat

**Date:** 2026-10-27
**Scope:** Replace Kubementat's outdated, CLI-piped Linkerd setup with a current open-source service mesh approach. The approach has to work on clusters from a Raspberry Pi (ARM64) up to cloud, install through helmfile, and avoid vendor lock-in.
**Method:** I checked official docs, GitHub release pages, Helm repository `index.yaml` files and chart `values.yaml` files (downloaded and inspected on 2026-10-27), and container registry manifests (multi-arch checks). Secondary sources are used only where noted. Items marked **[Inference]** are my own reasoning. Items marked **[Unverified]** could not be confirmed from a primary source.

---

## 1. Executive Summary and Recommendation

**Recommendation: replace the Linkerd CLI install with Istio in ambient mode, installed as four pinned Helm releases from helmfile (`istio/base`, `istio/istiod`, `istio/cni`, `istio/ztunnel`, version 1.31.3).** It should stay in the optional `additional` group and be enabled per namespace with the label `istio.io/dataplane-mode=ambient`. **Fallback: Linkerd edge (chart `2026.10.1` / `edge-26.10.1`), installed through Helm with cert-manager and trust-manager managing identity.** The fallback suits users who want the smallest footprint and do not mind the edge-only release model.

Reasons for Istio ambient as the primary choice:

1. **No vendor lock-in, and the upstream project publishes stable releases.** Istio is CNCF graduated and ships versioned releases from the project itself: roughly one minor release per quarter, each supported until 6 weeks after N+2 [S10]. Since February 2024, the open-source Linkerd project publishes **only edge releases**. Stable artifacts come only from vendors (Buoyant Enterprise for Linkerd, BEL). BEL is free for individuals and companies with fewer than 50 employees; larger companies need a paid subscription to run stable artifacts in production [S1][S4][S5]. Linkerd's code is still Apache-2.0, and edge releases may legally run in production [S5]. Still, "pick a weekly edge build, or pay one vendor for stable" conflicts with Kubementat's NO VENDOR LOCK-IN value more than Istio's multi-vendor model does.
2. **No sidecars, so no Job/Tekton completion problems and no per-pod proxy cost.** Ambient mode has been GA since Istio 1.24 (November 2024) [S12]. A per-node Rust proxy (ztunnel) provides L4 mTLS, and Envoy "waypoint" proxies are added only where L7 features are needed [S13]. Workloads do not need restarts or annotations to join or leave the mesh.
3. **Works with the existing Prometheus with no extra config.** istiod, ztunnel and istio-cni pods carry `prometheus.io/scrape` annotations in the charts (verified in chart 1.31.3), so the stock `prometheus-community/prometheus` annotation-based pod scraping picks them up. Linkerd needs a dedicated scrape-config variant (Kubementat already keeps one) [S8].
4. **Fits the ingress change Kubementat has to make anyway.** ingress-nginx was retired in March 2026 [S20][S21], so Kubementat needs a Gateway API ingress. Istio is both a conformant Gateway API controller and a conformant GAMMA mesh implementation for Gateway API v1.6 [S22]. That gives one option for ingress and mesh together, without making one depend on the other.

Main costs of the recommendation, explained in sections 5 and 8:
- **Footprint.** The default resource requests are sized for large clusters: istiod requests 500m / 2048Mi and ztunnel requests 200m / 512Mi per node (chart 1.31.3 defaults). Kubementat must ship lower requests for small and Pi clusters.
- **Upgrade cadence.** A minor upgrade is needed about every 6 months, which is more upgrade work than Linkerd's edge model.
- **CNI dependence.** istio-cni must be configured for the platform (for example `global.platform=k3s`) [S14].

**Linkerd stays a valid fallback, but the current automation must go either way.** The `helm.linkerd.io/stable` repo in Kubementat's helmfile is **frozen at `linkerd-control-plane` 1.16.11 / `stable-2.14.10`, published 2024-02-20** (verified from the repo index). The commented-out `1.4.0` pins are even older. The CLI flow, `run.linkerd.io/install` (stable channel), and `linkerd upgrade | kubectl apply --prune` all describe a release model that no longer exists.

---

## 2. Current State in Kubementat and Why It Is Outdated

| Area | Current state (repo, 2026-10-27) | Problem |
|---|---|---|
| Install | `kmt install --enable-linkerd` runs `scripts/automation/components/install_linkerd.sh`: `linkerd check --pre`, `linkerd install --crds \| kubectl apply`, `linkerd install [--ha] \| kubectl apply`, `linkerd viz install \| kubectl apply` | Imperative CLI piping with no Helm release state and no pinned version. It is not reproducible, does not fit helmfile/GitOps, and diverges from every other Kubementat component. |
| Upgrade | `curl https://run.linkerd.io/install \| sh`, `linkerd upgrade \| kubectl apply --prune -l ...`, rollout restarts | Downloads an unpinned binary at runtime. The stable install channel is meaningless for OSS since 2.15 [S1]. `--prune` with label selectors is fragile. |
| Docs | Links to Linkerd 2.12 docs | Linkerd is at 2.20 (June 2026) and edge-26.10.1 (October 2026) [S1][S2]. |
| Identity certs | `generate_linkerd_certs.sh` creates a trust anchor and issuer with openssl (ECDSA P-256, 730 days) and stores them git-crypt encrypted in `platform_config` | **Not used by the install**, because the CLI generates its own short-lived certs. There is no rotation. The issuer CSR extension is ignored (the script's own BUG comment says so). Hand-rolled PKI is what cert-manager/trust-manager are meant to replace [S7]. |
| Helm (commented) | `linkerd/linkerd-crds` and `linkerd/linkerd-control-plane` `1.4.0` from `https://helm.linkerd.io/stable` | The stable repo stopped at 2.14.10 (Feb 2024). Current charts are in `https://helm.linkerd.io/edge` with calendar versions (`2026.10.1`). |
| Observability | `linkerd viz` brings its own Prometheus. A TODO says to reuse the platform Prometheus. A `prometheus_values_with_linkerd_config` variant exists. | Two Prometheus instances. viz supports `prometheus.enabled=false` plus `prometheusUrl` [S8]. |
| CI image | Pins Linkerd CLI `edge-26.10.1` from `run.linkerd.io/install-edge` | The CLI is only needed because of the CLI install path. |
| Uninstall | `_uninstall_linkerd` runs `linkerd viz uninstall` / `linkerd uninstall` when the CLI is present, then sweeps namespaces and CRDs ending in `.linkerd.io` | Needs the CLI. With Helm, `helm uninstall` (helmfile destroy) is enough. |
| App opt-in | Example chart uses the `linkerd.io/inject: enabled` annotation | Mesh-specific. Ambient uses a namespace label instead. |
| Ingress | `ingress-nginx/ingress-nginx` | **Retired upstream in March 2026**: no further releases or security patches [S20][S21]. This is out of scope here but closely related (section 6.6). |

---

## 3. Comparison Table (as of 2026-10-27)

| | **Istio (ambient)** | **Linkerd (edge)** | **Cilium Service Mesh** | **Kuma** | **Consul** | **OSM** | **Traefik Mesh** |
|---|---|---|---|---|---|---|---|
| License | Apache-2.0 | Apache-2.0 (code). Stable binaries only via vendors (BEL; free for under 50 employees) [S4][S5] | Apache-2.0 (eBPF parts GPL/BSD) | Apache-2.0 | **BSL 1.1** since Aug 2023, so not OSI open source [S27] | Apache-2.0, **archived** [S26] | Apache-2.0 |
| Governance | CNCF graduated, multi-vendor | CNCF graduated, effectively single-vendor (Buoyant) [S5][S6] | CNCF graduated (Cilium), Isovalent/Cisco-led | CNCF **sandbox** since 2020, Kong-led [S25] | IBM/HashiCorp (acquired Feb 2025) [S27] | Archived by CNCF (2023) | Traefik Labs. Last release v1.4.8 (Aug 2022) **[Unverified for 2026]** [S28] |
| Latest version | **1.31.3** (2026-10-21). 1.32.0-beta.0 in repo [S9][chart index] | **edge-26.10.1** (2026-10-05). Linkerd "2.20" = edge-26.6.3 (2026-06-23) [S1][S2][S3] | **1.20.4** (2026-10-23) [S23] | **3.0.0** (2026-10-15, breaking) [S24] | n/a | n/a | v1.4.8 |
| Architecture | Per-node Rust ztunnel (L4/mTLS) + optional Envoy waypoints (L7). Sidecar mode also available | Per-pod Rust micro-proxy sidecar (native sidecar default since 2.20) | eBPF + per-node Envoy. mTLS via WireGuard/IPsec or (beta) ztunnel | Envoy sidecars | Envoy sidecars | Envoy sidecars | Per-node Traefik |
| Requires CNI change | No (istio-cni chains with the existing CNI) | No (optional linkerd-cni) | **Yes, Cilium must be the CNI** | No | No | – | No |
| Footprint (defaults) | istiod req 500m/2Gi; ztunnel 200m/512Mi per node; cni 100m/100Mi per node. All tunable | Non-HA: no requests set. HA values: proxy 100m/20Mi, controllers 100m/50Mi | Already present if Cilium is the CNI | Envoy per pod (heavier) | – | – | – |
| ARM64 | Yes: `istio/pilot`, `ztunnel`, `install-cni` 1.31.3 are amd64+arm64 (verified) | Yes: `ghcr.io/linkerd/proxy`, `controller` edge-26.10.1 are amd64+arm64 (verified). ARMv7 no longer in the manifest | Yes | Yes | – | – | – |
| Gateway API | Conformant gateway **and** GAMMA mesh, v1.6.0 [S22] | Uses HTTPRoute/GRPCRoute for mesh routing. Supports Gateway API 1.2.1–1.5.1 (2.20) [S11]. Not listed as conformant on the v1.6 page [S22] | Conformant gateway + mesh, v1.6.1 [S22] | Partial | – | – | – |
| Install method | Helm (4 charts, official), istioctl optional [S14] | Helm (crds + control-plane, official) or CLI [S6] | Helm (cilium chart) | Helm / kumactl | Helm | – | Helm |
| Maturity for Kubementat | High. Ambient GA since 1.24 | High (mature, simple), release model is the issue | High as CNI; mesh mTLS still evolving (ztunnel beta) [S23b] | Medium. 3.0 just removed legacy APIs | Excluded (license) | Excluded (dead) | Excluded (stagnant) |

---

## 4. Per-Option Findings

### 4.1 Linkerd

**Release model (verified).** The Linkerd releases page says stable releases follow semver, but "the open source project no longer provides them". Instead, "the vendor community around Linkerd is responsible for creating stable release artifacts". Edge releases ship weekly or near-weekly and are "generally considered production ready" [S1]. The change was announced in February 2024 and took effect with 2.15 [S4][S5]. Buoyant's June 2026 licensing article confirms the following [S5]:
- The code is Apache-2.0.
- Edge can run in production for free.
- BEL is free for individuals and companies with fewer than 50 employees; above that a subscription is needed for BEL stable artifacts.

Coverage at the time put pricing at $2,000/cluster/month for larger companies [S4b] **[Pricing may have changed; not re-verified]**.

**Versions.**
- **Linkerd 2.20** was announced 2026-06-23 and corresponds to edge-26.6.3 [S1][S2]. 2.19 was announced 2025-10-31 (edge-25.10.7).
- Latest edge is **edge-26.10.1** (2026-10-05) [S3].
- Helm charts in `https://helm.linkerd.io/edge` use calendar versions: `linkerd-crds`, `linkerd-control-plane`, `linkerd-viz`, `linkerd2-cni` and `linkerd-multicluster` are all at **2026.10.1** (created 2026-10-05; verified from the repo index).
- Some edge release notes carry an "Overall status: RECOMMENDED" label (seen on 26.7.x and 26.8.x) [S3]. Kubementat could pin only to edges marked that way **[Inference]**.

**2.20 highlights** [S2]:
- Rate-limit-aware load balancing.
- Destination controller memory down by up to about 85%.
- Inbound metrics close to parity with outbound.
- **Native sidecars are stable and the default** (`proxy.nativeSidecar: true` in chart 2026.10.1; verified). This solves the long-standing problem of meshed Jobs/CronJobs never completing [S2][S16].
- Maximum Kubernetes 1.35, maximum Gateway API 1.5.1.

**Helm is the documented production path** [S6]:
1. `helm repo add linkerd-edge https://helm.linkerd.io/edge`
2. Install `linkerd-crds`, then `linkerd-control-plane` with `identityTrustAnchorsPEM`, `identity.issuer.tls.crtPEM`/`keyPEM`.
3. For HA, add `-f values-ha.yaml` from the chart.
4. Upgrade with `helm upgrade ... --reset-values -f values.yaml --atomic`.

Helm does not generate certificates, so they must come from outside the chart.

**Cert management** [S7]:
- cert-manager issues the trust anchor (self-signed CA, ECDSA, `rotationPolicy: Always`).
- A `ClusterIssuer` (CA type) signs the identity issuer `Certificate` in the `linkerd` namespace (48h duration, 25h renewBefore).
- **trust-manager** publishes a `Bundle` named `linkerd-identity-trust-roots`, combining the current and previous anchors, into namespaces labelled `linkerd.io/is-control-plane=true`.
- The chart is installed with `identity.externalCA=true` and `identity.issuer.scheme=kubernetes.io/tls`.
- Issuer rotation is automatic. **Trust anchor rotation still needs manual, ordered restarts.**
- Warning from the docs: cert-manager ≤ 1.17 defaulted `rotationPolicy: Never`, so set `Always` explicitly.
- Note: trust-manager is migrating from `Bundle` (trust.cert-manager.io/v1alpha1) to `ClusterBundle`. As of v0.25 the v1alpha1 `Bundle` is still served with conversion [S18][S19]. Plan for that migration.

**Gateway API.** Since 2.19, Linkerd no longer installs or manages the Gateway API CRDs by default [S1b]. `linkerd-crds` has `installGatewayAPI: false` (verified in chart 2026.10.1), and the Gateway API CRDs it can install are annotated `helm.sh/resource-policy: keep`. Supported Gateway API range for 2.20 is 1.2.1–1.5.1 [S11]. The current Gateway API release is **v1.6.3** (2026-10-06) [S17]. **A Linkerd setup must pin Gateway API ≤ 1.5.1 until a Linkerd edge declares 1.6 support. This conflicts with Gateway API ingress controllers that want 1.6.**

**Observability.**
- `linkerd-viz` supports `prometheus.enabled: false` + `prometheusUrl` [S8]. The external Prometheus needs three scrape jobs: `linkerd-controller`, `linkerd-service-mirror`, `linkerd-proxy` (10s interval recommended).
- The control-plane chart also offers Prometheus Operator `podMonitor`s. These don't apply here, because Kubementat uses the non-operator `prometheus-community/prometheus` chart.
- Linkerd charts do **not** set `prometheus.io/scrape` annotations (verified), so explicit scrape config is required.

**ARM64.** Yes, see the table (verified multi-arch manifests). Historically ARMv7 was supported too (since 2.9) [S29]. The current edge manifest lists only amd64 and arm64.

**Footprint.** The lightest per-pod proxy among the options [S30]. Non-HA chart defaults set no requests or limits.

### 4.2 Istio

**Versions and support** [S9][S10]:

| Minor | Released | End of life | Supported Kubernetes |
|---|---|---|---|
| 1.31 (latest patch **1.31.3**, 2026-10-21) | 2026-08-27 on the support page, 2026-08-31 on the release index (the two pages disagree) | ~Feb 2027 | 1.32–1.37 |
| 1.30 | 2026-05-14 | ~Dec 2026 | 1.32–1.36 |
| 1.29 | – | 2026-10-15 (now EOL) | – |

The Helm repo already contains `1.32.0-beta.0` (2026-10-22). Support policy: N+2 minor plus 6 weeks. The control plane may be one minor ahead of the data plane, never behind [S10].

**Ambient mode** became GA in 1.24 (2024-11-07) [S12]:
- **ztunnel** is a per-node Rust proxy for mTLS, L4 authz and L4 telemetry, using HBONE tunnelling.
- **Waypoints** are optional per-namespace Envoy deployments for L7 routing, authz and telemetry.
- Sidecar and ambient workloads can coexist [S13].

1.31 added weighted waypoint canaries, multi-cluster fixes, and an agentgateway waypoint class [S9b]. Wasm and Lua extension of waypoints are still alpha [S13].

**Helm install (official)** [S14]. The repo is **`https://blob.istio.io/istio-release/charts`**; OCI is `ghcr.io/istio/release/charts`. The old `istio-release.storage.googleapis.com` and `gcr.io/istio-release` locations are being retired, with scheduled outages through December 2026 [S9b]. Order:
1. `istio/base` (CRDs)
2. Gateway API CRDs
3. `istio/istiod --set profile=ambient`
4. `istio/cni --set profile=ambient`
5. `istio/ztunnel`
6. (optional) `istio/gateway`

Helm 3.6+ is required and Helm 4 is supported. istioctl is optional.

**Platform specifics** [S15]:
- **k3s:** `global.platform=k3s`, which sets CNI dirs `/var/lib/rancher/k3s/agent/etc/cni/net.d` and `/var/lib/rancher/k3s/data/cni` (verified in chart). Don't set it if k3s uses a custom CNI.
- **k3d:** `k3d`. **MicroK8s:** `microk8s`. **minikube** (Docker driver): `minikube`.
- **Cilium as CNI:** `cni.exclusive=false`, no `bpf.masquerade`.
- **kind:** no special profile is listed. The official ambient getting-started guide uses kind **[Inference: works with defaults]**.

**CRDs.** `istio/base` templates 15 CRDs with `helm.sh/resource-policy: keep` (verified). Helm upgrades therefore update the CRDs, but `helm uninstall` leaves them behind, so Kubementat's CRD sweep must handle `*.istio.io`.

**Gateway API.**
- Conformant as gateway and as GAMMA mesh at v1.6.0 (report for 1.31.0) [S22].
- Waypoints are Gateway API `Gateway` resources (GatewayClass `istio-waypoint`), so the CRDs are needed for L7.
- The Istio docs apply `experimental-install.yaml` v1.6.0 [S14]. The standard channel suffices for HTTPRoute/Gateway **[Inference; experimental needed only for TCPRoute/TLSRoute-era features or BackendTLSPolicy variants — verify per feature]**.

**Prometheus.**
- istiod (port 15014), ztunnel (15020) and istio-cni (15014) carry `prometheus.io/scrape` annotations in chart 1.31.3 (verified).
- Istio's integration docs describe annotation-based scraping and the "merged metrics" behaviour [S31].
- Grafana dashboards are published on grafana.com by the Istio project **[Unverified for exact IDs; historically 7639 Mesh, 7636 Service, 7630 Workload, 7645 Control Plane]**.

**ARM64.** Verified for 1.31.3 images.

**Footprint.**
- Defaults (chart 1.31.3): istiod requests 500m CPU / 2048Mi (HPA 1–5); ztunnel 200m / 512Mi (the comment says this is sized for ~200k pods); cni 100m / 100Mi.
- Istio cites over 90% lower memory than sidecars for L4-only ambient [S32] (secondary figure). Istio's own benchmark reports ztunnel throughput leading Linkerd and Cilium [S33].
- Linkerd's own benchmark claims lower p99 latency than ambient with L7 waypoints [S30]. Benchmarks are vendor-produced; treat them as directional.

### 4.3 Cilium Service Mesh

- The latest release is 1.20.4 (2026-10-23) [S23].
- Mesh features (Envoy-based L7, Gateway API/GAMMA v1.6.1 conformant [S22], WireGuard/IPsec encryption) require **Cilium to be the cluster CNI**.
- In 1.19 the old out-of-band mutual authentication was disabled by default. A **ztunnel-based mTLS integration arrived as beta**, with limitations: no Cluster Mesh, and L4 policy only on the HBONE port [S23b][S23c].
- For Kubementat this is a **CNI decision, not an add-on**. On k3s, replacing flannel means installing k3s with `--flannel-backend=none --disable-network-policy` at cluster creation **[Inference from k3s/Cilium docs; not re-verified]**. That can't be toggled by `kmt install --enable-...` on an existing cluster.
- Recommendation: support it in docs ("if your cluster already runs Cilium, you may use its mesh instead") but do not ship it.

### 4.4 Kuma / Kong Mesh

- CNCF sandbox since 2020 [S25], Apache-2.0, Envoy sidecars.
- **Kuma 3.0.0 (2026-10-15) removed legacy policies, `Mesh.mtls` and the built-in gateway**, and requires upgrade work for most deployments [S24].
- Its strength is multi-zone and VM support. It has a heavier per-pod footprint (Envoy) and a smaller community.
- Kong Mesh is the commercial edition. Not recommended.

### 4.5 Consul, Open Service Mesh, Traefik Mesh

- **Consul:** HashiCorp moved Consul to **BSL 1.1** on 2023-08-10. IBM completed the HashiCorp acquisition on 2025-02-27 [S27]. Not open source by OSI definition, so excluded.
- **Open Service Mesh:** **archived** by CNCF in 2023; maintainers moved to Istio [S26]. Excluded.
- **Traefik Mesh:** no release found since v1.4.8 (Aug 2022) [S28]. Effectively stagnant. Excluded.
- **Newer entrants:** agentgateway appears as an Istio waypoint option and a Gateway API implementation [S9b][S22]. It is not a standalone mesh. I found no new general-purpose OSS mesh of note in 2025–2026 **[Gap: no dedicated search for niche projects]**.

---

## 5. Best Practices (Late 2026)

### 5.1 Do you need a mesh at all?

A mesh is justified when you need one or more of the following:
- (a) Zero-config mTLS and workload identity between pods.
- (b) Golden metrics (success rate, latency, RPS) per service without instrumenting apps.
- (c) L7 traffic management (retries, timeouts, traffic splitting/canary, circuit breaking) via Gateway API GAMMA (HTTPRoute with `parentRefs` to a Service).
- (d) L4/L7 authorization policies stronger than NetworkPolicy.

Without these, NetworkPolicy plus app-level TLS plus ingress-level metrics are enough. Keeping the mesh **optional (default off)** is consistent with industry practice **[Inference]**. Ambient narrows the cost side: L4 mTLS without sidecars is cheap enough to enable broadly.

### 5.2 Sidecar vs sidecarless

| | Sidecar (Linkerd, Istio sidecar) | Sidecarless (Istio ambient, Cilium) |
|---|---|---|
| Cost scaling | Per pod | Per node, plus per-namespace waypoints for L7 |
| Onboarding | Inject and restart pods | Label namespace, no restart |
| Jobs/CronJobs/Tekton | Needs native sidecars (K8s ≥ 1.29 beta, GA 1.33) or workarounds | No issue |
| Blast radius | Per pod | Node-shared ztunnel: a ztunnel failure affects all ambient pods on the node |
| L7 | Always available | Only with a waypoint (extra hop) |
| Security isolation | Strong (per-pod) | Shared node proxy, which handles keys for every pod on the node **[Inference; Istio documents mitigations]** |

### 5.3 Install via Helm/helmfile, not CLI piping

Both Linkerd and Istio document Helm as a first-class path [S6][S14]. Helm gives release history, `helm diff` (already in Kubementat's CI image), atomic rollback and declarative values, and it fits the existing helmfile labels/groups model. Pin chart versions exactly. Keep CLIs (`linkerd`, `istioctl`) only as **optional diagnostics** (`linkerd check`, `istioctl analyze`, `istioctl proxy-status`), not as installers.

### 5.4 CRD lifecycle

- Install CRD charts as separate releases, ordered with helmfile `needs:`.
- Istio templates its CRDs, so Helm upgrades update them, but they carry `resource-policy: keep`.
- Linkerd's own CRDs are plain templates in `linkerd-crds`.
- **Gateway API CRDs are shared cluster infrastructure.** Install them once at a pinned version from one owner (kmt), with server-side apply. Do not let multiple charts own them. Do not delete them on mesh uninstall if an ingress controller uses them.

### 5.5 Certificate rotation

- **Istio:** istiod's built-in CA self-signs a root and rotates workload certs (24h by default) automatically **[Unverified exact defaults for 1.31]**. For a custom or long-lived root, use plugged-in CA certs or cert-manager `istio-csr`. For Kubementat's "convention over configuration" approach, the built-in CA is the right default. Document `istio-csr` as an advanced option.
- **Linkerd:** cert-manager for the trust anchor and issuer, trust-manager for the bundle, `identity.externalCA=true` [S7]. Do not commit long-lived CA private keys to git.

### 5.6 Upgrade strategy

- **Istio:** upgrade one minor at a time: CRDs (base), then istiod, then cni, then ztunnel (a ztunnel restart briefly disrupts the node's ambient traffic). Waypoints follow istiod. Revisions/canary upgrades are possible but optional. The control plane must never be older than the data plane [S10][S14]. Budget two minor upgrades per year.
- **Linkerd edge:** upgrade CRDs, then the control plane with `--reset-values -f values.yaml --atomic`, then `rollout restart` meshed workloads to pick up the new proxy [S6]. Read each edge's notes, since the cadence is weekly.

### 5.7 Prometheus/Grafana integration

Reuse the single platform Prometheus.
- **Istio:** annotation-based scraping, plus Grafana dashboards provisioned through the existing Grafana chart's dashboard provider.
- **Linkerd:** the three scrape jobs from [S8]; viz with `prometheus.enabled=false`, `prometheusUrl`; Grafana dashboards from the Linkerd grafana.com org; viz `grafana.url`/`externalUrl` to link out.
- Use a 10s scrape interval for mesh targets [S8].

### 5.8 Tekton pipeline pods

- Tekton TaskRun pods are Pods with sidecars. Injected mesh sidecars historically prevented completion and confused readiness.
- Tekton has `running-in-environment-with-injected-sidecars` (keep the default when meshed). It also has `enable-kubernetes-sidecar`, which requires K8s ≥ 1.29 [S34].
- Linkerd 2.20 injects as a native sidecar by default [S16]. Istio ambient injects nothing.
- **Recommendation: do not mesh the Tekton namespace(s) by default.** If teams want CI egress mTLS, ambient is the safe option.

### 5.9 Ingress controllers

- ingress-nginx is retired [S20]. The migration tool `ingress2gateway` reached 1.0 on 2026-03-20 [S21].
- The ingress controller must be in the mesh for end-to-end mTLS:
  - **Istio ambient:** label the ingress namespace.
  - **Linkerd:** inject the ingress pods, and with nginx use `service-upstream` **[Inference from Linkerd ingress docs; not re-fetched]**.
- If Kubementat adopts Istio, the `istio/gateway` chart or Istio's Gateway API controller could later replace ingress-nginx. Keep that a separate decision so the mesh remains optional (section 9).

---

## 6. Detailed Recommendation for Kubementat

### 6.1 What to ship

| Item | Decision |
|---|---|
| Mesh | **Istio ambient 1.31.3** (helmfile, group `additional`, label `component: service_mesh`) |
| Fallback | **Linkerd edge 2026.10.1** via Helm + cert-manager/trust-manager (documented, optionally a second helmfile label `component_name: linkerd-*`) |
| Gateway API CRDs | Applied by `kmt` (Python) from a pinned URL, server-side apply. **v1.6.3** for Istio; **v1.5.1** if Linkerd is selected [S11][S17] |
| CLI flag | `kmt install --enable-service-mesh` (default off). Keep `--enable-linkerd` as a deprecated alias that prints a migration hint **[Inference]** |
| Namespace opt-in | `istio.io/dataplane-mode=ambient` label (replace `linkerd.io/inject` in the example chart) |
| L7 | Opt-in per namespace: `istioctl waypoint apply -n <ns>` or a `Gateway` with `gatewayClassName: istio-waypoint` |

### 6.2 Example helmfile releases (Istio ambient, primary)

```yaml
repositories:
- name: istio
  url: https://blob.istio.io/istio-release/charts   # NOT istio-release.storage.googleapis.com (being retired) [S9b]

releases:
- name: istio-base
  chart: istio/base
  version: "1.31.3"
  namespace: istio-system
  labels: { component: service_mesh, component_name: istio-base, group: additional }

- name: istiod
  chart: istio/istiod
  version: "1.31.3"
  namespace: istio-system
  needs: [ istio-system/istio-base ]
  labels: { component: service_mesh, component_name: istiod, group: additional }
  values:
  - service_mesh/istiod_values.yaml

- name: istio-cni
  chart: istio/cni
  version: "1.31.3"
  namespace: istio-system
  needs: [ istio-system/istiod ]
  labels: { component: service_mesh, component_name: istio-cni, group: additional }
  values:
  - service_mesh/istio_cni_values.yaml

- name: ztunnel
  chart: istio/ztunnel
  version: "1.31.3"
  namespace: istio-system
  needs: [ istio-system/istio-cni ]
  labels: { component: service_mesh, component_name: ztunnel, group: additional }
  values:
  - service_mesh/ztunnel_values.yaml
```

Minimal values, small-cluster friendly. The resource numbers are **[Inference]** starting points to validate on a Pi, not upstream recommendations:

```yaml
# service_mesh/istiod_values.yaml
profile: ambient
global:
  platform: ""        # "k3s" | "k3d" | "microk8s" | "minikube" | "" — from platform_config static.json
pilot:
  autoscaleEnabled: false
  replicaCount: 1     # 2+ with PDB when an HA flag (successor of LINKERD_HA_ENABLED) is true
  resources:
    requests: { cpu: 100m, memory: 256Mi }

# service_mesh/istio_cni_values.yaml
profile: ambient
global:
  platform: ""

# service_mesh/ztunnel_values.yaml
global:
  platform: ""
resources:
  requests: { cpu: 50m, memory: 64Mi }
```

Gateway API CRDs (kmt, before `helmfile sync`):

```bash
kubectl apply --server-side -f \
  https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.3/standard-install.yaml
```

Platform config (`static.json`): replace `LINKERD_*` keys with `SERVICE_MESH_PROVIDER` (`istio` | `linkerd`), `SERVICE_MESH_HA_ENABLED`, `SERVICE_MESH_PLATFORM` (`k3s`, ...), `ISTIO_DEPLOYMENT_NAMESPACE: istio-system` **[Inference / design proposal]**.

### 6.3 Example helmfile releases (Linkerd edge, fallback)

```yaml
repositories:
- name: linkerd-edge
  url: https://helm.linkerd.io/edge
# jetstack already present

releases:
- name: trust-manager
  chart: jetstack/trust-manager
  version: "v0.25.0"          # v0.26.0 released today (2026-10-27); wait before adopting
  namespace: cert-manager
  needs: [ cert-manager/cert-manager ]
  labels: { component: service_mesh, component_name: trust-manager, group: additional }

- name: linkerd-identity           # local chart: Issuer/Certificate/ClusterIssuer/Bundle from [S7]
  chart: ./service_mesh/linkerd-identity
  namespace: linkerd
  needs: [ cert-manager/trust-manager ]
  labels: { component: service_mesh, component_name: linkerd-identity, group: additional }

- name: linkerd-crds
  chart: linkerd-edge/linkerd-crds
  version: "2026.10.1"
  namespace: linkerd
  values: [ { installGatewayAPI: false } ]   # kmt owns Gateway API CRDs (pinned ≤ v1.5.1)
  labels: { component: service_mesh, component_name: linkerd-crds, group: additional }

- name: linkerd-control-plane
  chart: linkerd-edge/linkerd-control-plane
  version: "2026.10.1"
  namespace: linkerd
  needs: [ linkerd/linkerd-crds, linkerd/linkerd-identity ]
  values:
  - identity:
      externalCA: true
      issuer:
        scheme: kubernetes.io/tls
  # HA: add values-ha.yaml contents when SERVICE_MESH_HA_ENABLED=true
  labels: { component: service_mesh, component_name: linkerd-control-plane, group: additional }

- name: linkerd-viz
  chart: linkerd-edge/linkerd-viz
  version: "2026.10.1"
  namespace: linkerd-viz
  needs: [ linkerd/linkerd-control-plane ]
  values:
  - prometheus: { enabled: false }
    prometheusUrl: http://prometheus-server.<monitoring-namespace>.svc.cluster.local
  labels: { component: service_mesh, component_name: linkerd-viz, group: additional }
```

The `linkerd-identity` local chart contains the objects from [S7]:
- self-signed `Issuer`
- `Certificate` `linkerd-trust-anchor` (in the cert-manager namespace, `isCA`, ECDSA, `rotationPolicy: Always`)
- `ClusterIssuer` `linkerd-identity-issuer` (CA)
- `Certificate` `linkerd-identity-issuer` (in `linkerd`, 48h / 25h renewBefore)
- trust-manager `Bundle` `linkerd-identity-trust-roots`

The `linkerd` namespace needs the `linkerd.io/is-control-plane=true` label. Ordering caveat: the namespace must exist with that label before trust-manager writes the ConfigMap, and the ConfigMap must exist before the control plane starts. helmfile `needs:` plus a namespace manifest handle this **[Inference; test required]**.

### 6.4 Prometheus/Grafana

- **Istio:** no Prometheus values variant needed, if Kubementat's Prometheus values keep the chart's annotation-based `kubernetes-pods` job (verify in `prometheus_values*.yaml`). Add the Istio Grafana dashboards via the Grafana chart's `dashboards:` provider (grafana.com IDs, **verify**). Optionally add Kiali later; not by default.
- **Linkerd fallback:** update `prometheus_values_with_linkerd_config` to the three jobs from [S8] and disable viz's Prometheus.

### 6.5 What to do with existing artifacts

| Artifact | Action |
|---|---|
| `install_linkerd.sh` | **Delete.** Replace with helmfile releases plus a small Python step in `automation.py` (Gateway API CRDs, namespace labels, wait for rollout). Consistent with the ongoing bash-to-Python migration. |
| `generate_linkerd_certs.sh` + encrypted PEMs in platform_config | **Delete.** Istio uses the istiod CA. The Linkerd fallback uses cert-manager/trust-manager. Remove the git-crypt'ed key material from templates; existing environments can delete theirs after migration. |
| Linkerd CLI in CI image | **Remove** from `ubuntu_ci_minimal`, since Helm/helmfile are already there. Optionally add `istioctl` (pinned 1.31.3) for `istioctl analyze` diagnostics only; not required. |
| `_uninstall_linkerd` (CLI-based) | Replace with Helm-based uninstall: helmfile releases are already uninstalled per namespace by the existing helm-release sweep. Extend `CRD_GROUPS_KUBEMENTAT_OWNS` with `istio.io` (the suffix match covers `networking.istio.io`, `security.istio.io`, `telemetry.istio.io`, `extensions.istio.io`), because Istio CRDs carry `resource-policy: keep`. **Do not** sweep `gateway.networking.k8s.io` unless kmt installed it and no ingress uses it. Keep a one-time "legacy Linkerd CLI install detected" branch for old clusters. |
| `LINKERD_COMPONENTS`, constants `linkerd`/`linkerd-viz` | Replace with mesh-provider-aware namespaces (`istio-system`; `linkerd`, `linkerd-viz` for the fallback). |
| `open_linkerd_dashboard_tunnel.sh` | Drop for Istio, or replace with a Grafana or Kiali port-forward. |
| `linkerd.io/inject: enabled` in example chart | Replace with a namespace label in the environment/team namespace templates (`istio.io/dataplane-mode: ambient`), behind the mesh flag. |

---

## 7. Migration Plan

**Steps**
1. **Gateway API CRD step in kmt** (Python): pinned version, server-side apply, idempotent, with a version check like the one in [S11]/[S14].
2. **Add helmfile releases** (section 6.2) under `group: additional`. Add a `--enable-service-mesh` flag that selects the `component=service_mesh` releases.
3. **Platform config:** add the `SERVICE_MESH_*` keys to `static.json.template`. Map `global.platform` from the cluster type (k3s, k3d, minikube) and detect it where possible **[Inference]**.
4. **Existing CLI-installed Linkerd clusters** (opt-in feature, so probably few):
   - a. Inventory meshed namespaces (`linkerd.io/inject` annotations).
   - b. Install Istio ambient alongside it. Do **not** set STRICT `PeerAuthentication` yet.
   - c. For each namespace: remove the Linkerd injection annotation, label `istio.io/dataplane-mode=ambient`, and `rollout restart`, so pods restart without the Linkerd sidecar and ztunnel captures them.
   - d. When no Linkerd proxies remain (`kubectl get pods -A -o json` shows no `linkerd-proxy` containers), run the legacy uninstall (`linkerd viz uninstall | kubectl delete -f -`, `linkerd uninstall | kubectl delete -f -`), or the Helm-free sweep already in `_uninstall_linkerd`.
   - e. Optionally enable a mesh-wide STRICT mTLS policy.
5. **Delete** the bash scripts and cert script, remove the CLI from the CI image, and update the e2e tests (`tests/test_uninstall_sweeps_everything_kubementat_owns.py`, `tests/cluster_parity.py` reference Linkerd).
6. **Docs:** a new `docs/README_SERVICE_MESH.md` covering enabling, namespace opt-in, waypoints, dashboards, upgrades and Pi tuning.

**Risks and mitigations**

| Risk | Mitigation |
|---|---|
| Plaintext gap between Linkerd-meshed and ambient pods during step 4 (Linkerd sends plaintext to unmeshed peers; ambient accepts plaintext only in PERMISSIVE) **[Inference]** | Keep PERMISSIVE during migration. Migrate namespaces that talk to each other together. Dev clusters can accept a short window. |
| istio-cni misconfigured on k3s/k3d/minikube, so pods fail to start | Platform value from config, plus an e2e test on k3s (Pi) and kind |
| ztunnel/istiod OOM with lowered requests | Set requests low and leave limits unset (or generous). Measure on a Pi 4/5 with 4–8 GB. |
| Raspberry Pi OS kernel lacking iptables/nftables modules needed by ztunnel redirection **[Unverified]** | Pre-flight check in kmt. Istio 1.28+ supports native nftables [S9c]. |
| Istio's 6-month support window | Add a `kmt` upgrade path for the mesh (helmfile apply with a bumped version, ordered) and document "one minor at a time" |
| Gateway API version skew (Istio wants 1.6, Linkerd ≤ 1.5.1, ingress controller X) | kmt owns the CRD version per environment and validates it against the selected mesh |
| Istio artifact URL changes (GCS retirement) | Use `blob.istio.io` or the OCI `ghcr.io/istio/release/charts` from day one |
| Linkerd fallback: edge cadence and possible breaking changes | Pin "RECOMMENDED" edges only. Read release notes. CI e2e on each bump. |

---

## 8. Footprint Notes for Raspberry Pi / Small Clusters

- **Istio ambient, L4 only:** about 1 istiod + 1 ztunnel and 1 istio-cni per node, plus no per-pod cost. With the requests from section 6.2, roughly 150m CPU / ~420Mi requested on a single-node cluster **[Inference from chart values; actual RSS not measured]**.
- **Linkerd non-HA:** destination, identity and proxy-injector pods (each with its own proxy), plus a ~10–20 MiB-class proxy per meshed pod (the HA values request 20Mi per proxy). For very small clusters with few meshed pods, Linkerd is likely lighter. As meshed pods grow, ambient wins on aggregate memory **[Inference]**.
- **Both** publish arm64 images (verified). Neither publishes armv7 images (verified for current tags), so 32-bit Pi OS is not supported.

---

## 9. Open Questions

1. **Ingress replacement:** ingress-nginx is retired. Should Kubementat adopt Istio's Gateway API controller (shared with the mesh) or a mesh-independent controller (Envoy Gateway, Traefik, NGINX Gateway Fabric; all v1.6-conformant [S22])? This affects whether Istio becomes partly "core" instead of optional. Recommend separate research.
2. **Kubementat's minimum Kubernetes version:** Istio 1.31 needs ≥ 1.32 [S10]; Linkerd 2.20 supports up to 1.35 [S2]. What do target k3s versions ship?
3. **Real Pi measurements** of ztunnel and istiod with lowered requests; not available from primary sources.
4. **Should Kubementat support both meshes** (provider switch) or only one? Convention over configuration argues for one shipped default plus a documented fallback.
5. **Trust-manager `ClusterBundle` migration timing** (Linkerd fallback only) [S18][S19].
6. **Exact Grafana dashboard IDs** for Istio ambient/ztunnel; not verified.
7. **Linkerd and Gateway API 1.6:** not yet declared supported as of 2.20. Check newer edge notes.
8. Whether BEL's "<50 employees free" terms or pricing changed after June 2026 [S5].

---

## 10. Sources

Primary sources were accessed 2026-10-27 unless dated otherwise. The verified items in sections 3–4 also come from direct inspection of the Helm repo indexes (`helm.linkerd.io/edge/index.yaml`, `helm.linkerd.io/stable/index.yaml`, `blob.istio.io/istio-release/charts/index.yaml`, `charts.jetstack.io/index.yaml`, `helm.cilium.io/index.yaml`, `kumahq.github.io/charts/index.yaml`), the chart `values.yaml` files (linkerd-crds/control-plane/viz 2026.10.1; istio base/istiod/cni/ztunnel 1.31.3), and the OCI image manifests on ghcr.io (`linkerd/proxy`, `linkerd/controller` at edge-26.10.1) and Docker Hub (`istio/pilot`, `istio/ztunnel`, `istio/install-cni` at 1.31.3).

**Linkerd**
- [S1] Linkerd, "Releases and Versions": https://linkerd.io/releases/
- [S1b] Buoyant, Enterprise 2.19.0 release notes (Gateway API CRDs no longer managed; 2025-10-31): https://docs.buoyant.io/release-notes/buoyant-enterprise-linkerd/enterprise-2.19.0/
- [S2] Linkerd blog, "Announcing Linkerd 2.20" (2026-06-23): https://linkerd.io/2026/06/23/announcing-linkerd-2.20/
- [S3] GitHub, linkerd/linkerd2 releases (edge-26.10.1, 2026-10-05): https://github.com/linkerd/linkerd2/releases and https://github.com/linkerd/linkerd2/releases/tag/edge-26.10.1
- [S4] Buoyant, "Clarifications on Linkerd 2.15 stable announcement" (2024): https://www.buoyant.io/blog/clarifications-on-linkerd-2-15-stable-announcement
- [S4b] TechTarget, "Some Linkerd service mesh users will soon have to pay" (Feb 2024): https://techtarget.com/searchitoperations/news/366570820/Linkerd-service-mesh-production-users-will-soon-have-to-pay
- [S4c] Linkerd blog, "Towards a Sustainable Service Mesh" (2024-10-23): https://linkerd.io/2024/10/23/making-linkerd-sustainable/
- [S5] Buoyant, "How Linkerd Licensing Actually Works: Apache 2.0, Edge Releases, and What BEL Pays For" (June 2026): https://www.buoyant.io/articles/how-linkerd-licensing-actually-works-apache-2-0-edge-releases-and-what-bel-pays-for
- [S6] Linkerd docs, "Installing Linkerd with Helm" (edge): https://linkerd.io/2-edge/tasks/install-helm/
- [S7] Linkerd docs, "Automatically Rotating Control Plane TLS Credentials" (cert-manager + trust-manager): https://linkerd.io/2-edge/tasks/automatically-rotating-control-plane-tls-credentials/
- [S8] Linkerd docs, "Bringing your own Prometheus": https://linkerd.io/2-edge/tasks/external-prometheus/
- [S11] Linkerd docs, "Gateway API support" (version matrix): https://linkerd.io/2-edge/features/gateway-api/
- [S16] Linkerd docs, "Native sidecars": https://linkerd.io/2-edge/features/native-sidecars/
- [S29] Linkerd blog, "Announcing Linkerd 2.9" (ARM support, 2020-11-09): https://linkerd.io/2020/11/09/announcing-linkerd-2.9/
- [S30] Linkerd blog, "Linkerd vs Ambient Mesh: 2025 Benchmarks" (2025-04-24): https://linkerd.io/2025/04/24/linkerd-vs-ambient-mesh-2025-benchmarks/
- Artifact Hub, linkerd-control-plane (edge): https://artifacthub.io/packages/helm/linkerd2-edge/linkerd-control-plane

**Istio**
- [S9] Istio release index 1.31.x (1.31.0 2026-08-31 … 1.31.3 2026-10-21): https://istio.io/latest/news/releases/1.31.x/
- [S9b] "Announcing Istio 1.31.0": https://istio.io/latest/news/releases/1.31.x/announcing-1.31/
- [S9c] "Announcing Istio 1.28.0" (nftables in ambient, 2025-11-05): https://istio.io/latest/news/releases/1.28.x/announcing-1.28/
- [S10] Istio, "Supported Releases": https://istio.io/latest/docs/releases/supported-releases/
- [S12] Istio blog, "Ambient Mode Reaches General Availability in v1.24" (2024-11-07): https://istio.io/blog/2024/ambient-reaches-ga/ (CNCF copy: https://cncf.io/blog/2024/11/07/fast-secure-and-simple-istios-ambient-mode-reaches-general-availability-in-v1-24)
- [S13] Istio docs, "Ambient mode overview": https://istio.io/latest/docs/ambient/overview/
- [S14] Istio docs, "Install ambient with Helm": https://istio.io/latest/docs/ambient/install/helm/
- [S15] Istio docs, "Platform-Specific Prerequisites" (ambient): https://istio.io/latest/docs/ambient/install/platform-prerequisites/
- [S31] Istio docs, "Prometheus integration": https://istio.io/latest/docs/ops/integrations/prometheus/ (not re-fetched in this session)
- [S32] Secondary memory-reduction figure, cited via CNCF/Istio performance blog: https://www.cncf.io/blog/2025/03/27/istio-the-highest-performance-solution-for-network-security/
- [S33] Istio blog, "Istio: The Highest-Performance Solution for Network Security" (2025): https://istio.io/latest/blog/2025/ambient-performance

**Gateway API / ingress**
- [S17] GitHub, kubernetes-sigs/gateway-api releases (v1.6.3 2026-10-06; v1.7.0-rc.3): https://github.com/kubernetes-sigs/gateway-api/releases
- [S20] Kubernetes blog, "Ingress NGINX Retirement: What You Need to Know" (2025-11-11): https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/
- [S21] InfoQ, "Kubernetes ingress-nginx retirement" (Nov 2025): https://www.infoq.com/news/2025/11/kubernetes-ingress-nginx ; ingress2gateway 1.0 via https://ecorpit.com/ingress-nginx-eol-gateway-api-migration-plan-2026/ (secondary)
- [S22] Gateway API, "Implementations" (conformance v1.5/v1.6): https://gateway-api.sigs.k8s.io/implementations/

**cert-manager / trust-manager**
- [S18] cert-manager announcement, "trust-manager ClusterBundle future" (2025-09-05): https://cert-manager.io/announcements/2025/09/05/trust-manager-clusterbundle-future
- [S19] GitHub, cert-manager/trust-manager releases (v0.25.0 2026-09-11, v0.26.0 2026-10-27): https://github.com/cert-manager/trust-manager/releases

**Other meshes**
- [S23] GitHub, cilium/cilium releases (1.20.4, 2026-10-23): https://github.com/cilium/cilium/releases
- [S23b] InfoQ, "Cilium 1.19" (Feb 2026): https://www.infoq.com/news/2026/02/cilium-119
- [S23c] Cilium docs, "Ztunnel Transparent Encryption (Beta)": https://docs.cilium.io/en/stable/security/network/encryption-ztunnel/
- [S24] GitHub, kumahq/kuma releases (3.0.0, 2026-10-15): https://github.com/kumahq/kuma/releases
- [S25] CNCF project page, Kuma (Sandbox since 2020-06-25): https://www.cncf.io/projects/kuma
- [S26] GitHub, openservicemesh/osm (archived notice): https://github.com/openservicemesh/osm/ ; SDxCentral (May 2023): https://www.sdxcentral.com/articles/interview/linkerd-welcomes-osm-orphans-shuttled-to-istio/2023/05/
- [S27] HashiCorp BSL change (2023-08-10) and IBM close (2025-02-27): TechRepublic https://www.techrepublic.com/article/ibm-hashicorp-acquisition-completed/ ; heise https://heise.de/-10303743 ; HashiCorp announcement https://www.hashicorp.com/blog/hashicorp-adopts-business-source-license (not re-fetched)
- [S28] Traefik community, "Project abandoned?": https://community.traefik.io/t/project-abandoned/10311 ; docs https://doc.traefik.io/traefik-mesh/

**Tekton / Kubernetes**
- [S34] Tekton docs, "Additional configs" (feature flags incl. `enable-kubernetes-sidecar`, `running-in-environment-with-injected-sidecars`): https://tekton.dev/docs/pipelines/additional-configs/
- Kubernetes docs, "Adopting Sidecar Containers": https://kubernetes.io/docs/tutorials/configuration/pod-sidecar-containers
