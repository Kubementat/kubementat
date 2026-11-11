# Research: Replacing the Kubementat log aggregation component

- Date: 2026-10-27
- Scope: replacement for the `grafana/loki-stack` release (Loki plus Promtail) in `templates/environment/kubementat_components/helmfile.yaml.gotmpl`
- Method: web research using official docs, GitHub releases, ArtifactHub API and chart sources. All chart and app versions below were read on 2026-10-27. I also checked the proposed configs locally (section 8.5).
- Markers: **[verified]** means checked against a primary source or by running the software. **[inference]** means my own judgement. **[unverified]** means I could not confirm it.

---

## 1. Executive summary and recommendation

**The current component is older than its name suggests.** The last `grafana/loki-stack` release (2.10.3, 2025-10-31) is flagged `deprecated: true`. Its Chart.yaml advertises `appVersion: v2.9.3`, but the bundled `loki` subchart 2.16.0 actually ships the image **`grafana/loki:2.6.1`** (2022). Its default schema is `boltdb-shipper` / `v11`. Retention runs through the deprecated `table_manager`. The bundled Promtail 3.5.1 has been end-of-life since 2026-03-02. [verified, see 2.2]

**Recommended stack (primary): "modern Loki, monolithic, filesystem" plus Grafana Alloy as the DaemonSet collector**

| Piece | Chart | Repo | Pin (as of 2026-10-27) | App version |
|---|---|---|---|---|
| Log store | `loki` | `grafana-community` → `https://grafana-community.github.io/helm-charts` (or `oci://ghcr.io/grafana-community/helm-charts`) | `20.0.2` | Loki 3.7.9 (2026-10-20) |
| Collector | `alloy` | `grafana` → `https://grafana.github.io/helm-charts` (still Grafana Labs maintained) | `1.14.3` | Alloy v1.21.2 (2026-10-22) |
| UI | unchanged Grafana `loki` datasource, same URL `http://loki.loki.svc.cluster.local:3100` | | | |

Why this stack:
- It is the smallest change for users. LogQL, the Grafana datasource type, the datasource URL, the existing dashboard 12611 and the documented `{namespace="dev1-pipelines"}` queries all keep working. I verified the service name and port by rendering the chart, and the query by running Loki 3.7.9 (section 8.5).
- Everything is OSS. Loki is AGPL-3.0 and Alloy is Apache-2.0.
- Both run multi-arch on arm64. Loki also ships `arm/v7`. Alloy does **not**, so 32-bit Raspberry Pi OS is out (section 6).
- Monolithic mode with filesystem storage is still supported by the community chart. The chart validates it explicitly: one replica, no object store.

**Fallback stack: VictoriaLogs**

`victoria-logs-single` (chart 0.13.10, app v1.53.0, Apache-2.0) as the store. For the collector, either keep the same Alloy DaemonSet and point it at VictoriaLogs' Loki-compatible push endpoint `/insert/loki/api/v1/push`, or use `victoria-logs-collector` (vlagent, chart 0.3.8). Grafana then needs the signed `victoriametrics-logs-datasource` plugin (v0.33.1). Choose this path if the RAM and disk footprint on a Raspberry Pi matters more than staying in the Loki/LogQL ecosystem. The costs are a new query language (LogsQL), dashboards that must be rewritten, and an extra Grafana plugin.

**Collector fallback:** Fluent Bit (`fluent/fluent-bit` chart 0.58.3, app 5.1.x, Apache-2.0, CNCF graduated). It has the smallest footprint and the widest arch support (including `arm/v7`) and has a native `loki` output.

**Migration approach (dev platform):** Do a "big bang" replacement instead of dual-running. Accept losing up to 14 days of dev logs, or keep the old release up temporarily in a different namespace as a second datasource "Loki (legacy)". The TODO's "join the new Loki into the existing ring" plan is not viable (section 7.2).

**Adjacent finding (action needed outside logging):** the `grafana/grafana` chart in the `grafana` repo is **also deprecated**. Its last version is 10.5.15 with Grafana 12.3.1, published 2026-01-30. It moved to `grafana-community/grafana` (14.2.0, Grafana 13.3.0). Kubementat's Grafana release currently uses `chart: grafana/grafana` unpinned and is frozen on that last version. [verified]

---

## 2. Current state and why it is obsolete

### 2.1 What Kubementat deploys today (from the repo)

- `helmfile.yaml.gotmpl`: release `loki`, `chart: grafana/loki-stack`, version unpinned (a commented-out `# version: "2.8.7"`), namespace `loki`, labels `component=logging`, `component_name=loki`, `group=standard`.
- Values `logging/loki_stack_values.encrypted.yaml`: single replica, 10Gi PVC, `table_manager` retention 336h, `chunk_store_config.max_look_back_period: 336h`, Promtail enabled with chart defaults (no custom scrape config).
- Grafana datasource `Loki` → `http://loki.loki.svc.cluster.local:3100`. Dashboard `gnetId 12611` ("Logging Dashboard via Loki") uses the labels `container`, `pod` and `stream`.
- `templates/environment/kubementat_components/logging/` already has an unused `loki_values.encrypted.yaml.template`. Its content is the same old loki-stack style (`table_manager`) and it references `artifacthub.io/packages/helm/grafana/loki`. It cannot be used with the new chart as written.

### 2.2 What that resolves to upstream [verified]

| Item | Finding | Source |
|---|---|---|
| `grafana/loki-stack` | Latest 2.10.3, published 2025-10-31, `deprecated: true`. README: *"This chart is deprecated and will no longer receive updates or support. Please consider using the Community maintained Loki chart"* | [1], [2] |
| Loki actually deployed | The bundled subchart `loki` 2.16.0 has `appVersion v2.6.1` and image tag `2.6.1`. The `v2.9.3` in loki-stack's Chart.yaml is **not** what runs. Checked by unpacking `loki-stack-2.10.3.tgz` | [3] |
| Storage schema | The legacy loki chart default is `store: boltdb-shipper`, `object_store: filesystem`, `schema: v11` | [3] |
| Promtail | The bundled `promtail` subchart is 6.17.0 (app 3.5.1). The standalone `grafana/promtail` chart is `deprecated: true` (6.17.1, 2025-10-31). Promtail is *"end of life (EOL) as of March 2, 2026. Commercial support has ended."* Promtail code was removed from the Loki repo in 3.7.3 | [4], [5], [6] |
| `table_manager` retention | Deprecated, and it will be **removed in Loki 4.0**: *"Starting with Loki 4.0, the single store TSDB is the only supported Loki index"*. Retention is now done by the compactor | [7], [8] |
| BoltDB index | Deprecated since 3.0 and **removed in Loki 4.0** (with Cassandra, DynamoDB, BigTable, gRPC store) | [9], [10] |
| `max_look_back_period` | Removed in Loki 3.x. Use `querier.max-query-lookback` / `limits_config.max_query_lookback` | [11] |

In short, the current setup is four years behind upstream. It sits on two deprecated storage mechanisms and an EOL shipper. None of its values can move forward unchanged.

---

## 3. Verification of the claims in the TODO notes

| TODO claim | Verdict | Details |
|---|---|---|
| "loki-stack chart is deprecated" | **Correct** | `deprecated: true` since 2.10.3 (2025-10-31) [1][2] |
| "promtail EOL 2026-03-02" | **Correct** | Exact wording in the Loki docs [5]. It was later removed from the Loki codebase in 3.7.3 [6] |
| "Loki helm chart may have moved to a grafana-community chart" | **Correct, and more definite than "may"** | Forked effective 2026-03-16. The last chart from the Loki repo was 6.55.0 (appVersion 3.6.7). The community chart is now `grafana-community/loki` 20.0.2 (Loki 3.7.9). The Grafana Labs-maintained `grafana/loki` chart (7.5.0, Loki 3.6.16) is now **only for Grafana Enterprise Logs (GEL)** [12][13][14] |
| "options: monolithic / simple-scalable / microservices" | **Partly outdated** | Chart 12.0 renamed `SingleBinary` to **`Monolithic`** and made it the default. Simple Scalable (SSD) is *"being deprecated and removed in Loki 4.0"*, and the replacement includes a new "HA Monolithic" mode. "Microservices" is called `Distributed` in the chart [12][15][16] |
| "retention + storage config per the upgrade-to-community page" | **Misleading** | That page is a chart upgrade guide (6.x → 18.x+ breaking changes). It does not cover retention or filesystem storage. The relevant docs are the retention page [7] and the filesystem storage page [17] |
| "Grafana Alloy ... can be configured with promtail configs via its convert command" | **Correct, with caveats** | `alloy convert --source-format=promtail` exists, and so does `alloy run --config.format=promtail` for a bridge period. Promtail's own metrics change, and the positions file location differs [18]. For Kubementat it adds little, because only chart-default Promtail config is used. Writing the Alloy config fresh is simpler (section 8.3) |
| "plan for dual-run: ... join it to the existing ring" | **Not viable** | See section 7.2 |
| "both charts have incompatible values schemas" | **Correct, and stricter than before** | Chart 20.0.0 also **fails** on unknown top-level keys (*"additional properties '<key>' not allowed"*), so stale keys break `helmfile apply` instead of being silently ignored [14] |
| "Vector or Fluent Bit -> Loki to drop the promtail/alloy dependency" | **Valid option** | Both have native Loki sinks. See section 5 for the trade-offs |

Things the TODO notes did not mention:
- (a) The Loki that actually runs is **2.6.1**, not 2.9.3.
- (b) The **Grafana chart moved too**.
- (c) The new chart's defaults are **far too large for a Pi**. `chunksCache.allocatedMemory: 8192` (MB) plus `resultsCache` 1024 MB, `replication_factor: 3`, `auth_enabled: true`, gateway, canary and helm test are all on, and `singleBinary.replicas: 0`. Every one of these has to be overridden [14].
- (d) The MinIO subchart is gone in chart 20.0.0. It was deprecated with removal announced for 2026-10-31 [14][19].

---

## 4. Backend comparison

### 4.1 Overview table

| Criterion | **Loki 3.7 (monolithic)** | **VictoriaLogs 1.53 (single)** | OpenObserve 1.1 | Quickwit 0.9 | SigNoz 0.147 (ClickHouse) | OpenSearch 3.9 / Elasticsearch |
|---|---|---|---|---|---|---|
| License | AGPL-3.0 [20] | Apache-2.0 [21] | AGPL-3.0 OSS edition; Enterprise edition commercial [22] | Apache-2.0 (relicensed after the Datadog acquisition, Jan 2025) [23][24] | MIT outside `ee/`; `ee/` under an enterprise license [25] | OpenSearch Apache-2.0; Elasticsearch AGPLv3 / ELv2 / SSPL (AGPL option since Aug 2024) [26] |
| Latest release | 3.7.9, 2026-10-20 [6] | v1.53.0, 2026-10-01 [27] | v1.1.2, 2026-10-20 | v0.9.3, 2026-10-22 | v0.147.0, 2026-10-22 | OpenSearch 3.9.0, 2026-09-29 |
| Helm chart | `grafana-community/loki` 20.0.2 | `vm/victoria-logs-single` 0.13.10 | `openobserve/openobserve-standalone` 1.1.2 | `quickwit/quickwit` 0.8.17 (app v0.8.2, **lags** 0.9.x) | `signoz/signoz` 0.147.0 | `opensearch-project-helm-charts/opensearch` 3.9.0 |
| Single binary / small node | Yes (monolithic, 1 replica) | Yes ("runs smoothly on Raspberry PI") [21] | Yes (single binary) | Needs an object store or a separately run metastore, so it is less suited to "PVC only" setups [inference] | No. ClickHouse plus Keeper plus collectors, multi-GB RAM [inference] | No. JVM heap, GB-range RAM [inference] |
| Filesystem (no S3) | Yes, *"not supported for production"* by Grafana Labs; single replica only [17] | Yes, native (PVC) | Yes (local disk), S3-native design | Possible, but the design targets object storage | Yes (ClickHouse disks) | Yes |
| Query language | LogQL | LogsQL (Loki → LogsQL conversion guide exists) | SQL / PromQL | Elasticsearch-compatible DSL | ClickHouse-SQL / query builder | DSL / PPL / SQL |
| Grafana integration | Native core datasource; Logs Drilldown | Signed plugin `victoriametrics-logs-datasource` v0.33.1 [28] | No first-party plugin mentioned [22] | Signed plugin `quickwit-quickwit-datasource` v0.6.3 | Own UI; Grafana via ClickHouse plugin [inference] | Plugin (OpenSearch) / core (Elasticsearch) |
| Ingest from Alloy / Fluent Bit / Vector / OTel | All, natively (Loki push and `/otlp`) | Loki push API, OTLP, ES bulk, own native API [29] | OTLP, ES bulk, own API | ES bulk / own API, OTLP | OTLP | ES bulk |
| Fit to Kubementat values | **High**: no UI change, same datasource | **High** for footprint, medium for "convention" (new QL, plugin) | Medium: AGPL plus an open-core split; Grafana is not its primary UI | Low/medium: chart lags the app; object-storage oriented; Datadog stewardship | Low: footprint; brings its own UI next to Grafana | Low: footprint and operational weight |

### 4.2 Footprint data

Vendor claims:
- VictoriaLogs: *"up to 30x less RAM and up to 15x less disk space than ... Elasticsearch and Grafana Loki"* [21].
- OpenObserve: "140x lower storage cost" than Elasticsearch [22].

Treat both as marketing figures. I did not reproduce them.

Own measurement, as a rough reference only. These are idle containers on amd64 with no log load, in Docker and not Kubernetes, measured on 2026-10-27:

| Process | Idle RSS (docker stats) |
|---|---|
| Loki 3.7.9 (`-target=all`, recommended config, caches off) | ~54 MiB |
| VictoriaLogs v1.53.0 | ~7 MiB |
| Alloy v1.21.2 (one file source + loki.write) | ~43 MiB |
| Fluent Bit 5.1.5 (default config) | ~4 MiB |

Under real load, Loki's ingester memory scales with active streams and chunk size. A limit of about 1 GiB for a small dev cluster is a common starting point [inference]. The Loki chart's **default** caches alone would request about 10 GiB (`chunksCache.allocatedMemory: 8192` MB ×1.2) [14], which is why the recommended values disable them.

---

## 5. Collector comparison (DaemonSet, all container logs)

| Criterion | **Grafana Alloy** | Fluent Bit | Vector | OpenTelemetry Collector | vlagent (VictoriaLogs) |
|---|---|---|---|---|---|
| License | Apache-2.0 | Apache-2.0 (CNCF graduated) | MPL-2.0, owned by Datadog | Apache-2.0 (CNCF) | Apache-2.0 |
| Latest | v1.21.2, 2026-10-22 [30] | v5.1.5, 2026-10-21 [31] | v0.60.0, 2026-10-19 [32] | v0.164.0, 2026-10-26 | v1.53.0 |
| Helm chart | `grafana/alloy` 1.14.3, or `grafana/k8s-monitoring` 4.5.5 (uses the Alloy Operator) [33][34] | `fluent/fluent-bit` 0.58.3 (app 5.1.3); `fluent/fluent-operator` 4.3.0 | `vector/vector` 0.60.0 | `opentelemetry-helm/opentelemetry-collector` 0.178.0; `opentelemetry-operator` 0.127.0 | `vm/victoria-logs-collector` 0.3.8 |
| Images (multi-arch, checked with `docker manifest inspect`) | amd64, **arm64**, ppc64le, s390x. **No arm/v7** | amd64, arm64, arm/v7 | amd64, arm64, arm/v7 | amd64, arm64, ppc64le, riscv64, s390x | amd64, arm64, arm/v7, ppc64le |
| Idle footprint | ~40 MiB (measured) | ~4 MiB (measured) | tens of MiB [inference] | ~50–150 MiB [inference] | small (100m / 128Mi example limits in its docs) [35] |
| Loki compatibility | Native `loki.write`; Promtail-equivalent pipeline stages; `alloy convert` from Promtail | `loki` output plugin (labels, `auto_kubernetes_labels`) | `loki` sink | `otlphttp` exporter → Loki `/otlp` (Loki maps k8s resource attributes to labels such as `k8s_namespace_name`, `service_name`) [36] | Not a target (VictoriaLogs only) |
| Label conventions out of the box | You define them. The config in 8.3 reproduces Promtail's `namespace`, `pod`, `container`, `app`, `job`, `instance`, `component`, `node_name`, `stream` | `kubernetes` filter fields, mapped to labels in the output | `kubernetes_logs` source fields, mapped in the sink | OTel semantic names (`k8s.namespace.name`, ...). This **breaks** the existing `{namespace=...}` queries and dashboard 12611 unless remapped | `kubernetes.pod_namespace`, `kubernetes.pod_name`, `kubernetes.container_name` stream fields [35] |
| K8s events | `loki.source.kubernetes_events`, cluster-wide de-duplication via `clustering` [37] | `kubernetes_events` input | `kubernetes_events` is not built in [unverified] | `k8sobjects` receiver / `kubernetesEvents` preset [38] | No |
| Ecosystem fit | Same vendor as Loki and Grafana; can later also replace Prometheus scraping | Very mature, vendor neutral | Strong VRL transforms; Datadog stewardship is a lock-in concern for some | Vendor neutral; best if traces/OTLP are planned | Only with VictoriaLogs |

Notes on the Alloy chart choice:
- **Plain `grafana/alloy` chart (recommended).** It is a DaemonSet with `alloy.mounts.varlog: true` and the config inline under `alloy.configMap.content`. It is transparent and small, and it fits "convention over configuration" because Kubementat ships the config.
- **`grafana/k8s-monitoring` 4.x.** Opinionated, with feature toggles such as `podLogsViaLoki` and `clusterEvents`. It installs the **Alloy Operator** (`alloy-operator` 0.9.3) plus CRD-managed Alloy instances [34]. It is aimed at Grafana Cloud-style "collect everything". For Kubementat it adds an operator, more pods and an abstraction to track across major versions (it went from 3.x to 4.x in 2026). Not recommended as the default [inference].
- `loki.source.kubernetes` (tailing through the API server) needs no hostPath mount. It does put load on the kube-apiserver, so the file-based `local.file_match` + `loki.source.file` approach (Promtail's model) is preferred on small control planes [inference; Alloy docs note both options [39]].

---

## 6. Detailed notes per option

### 6.1 Loki 3.x (recommended backend)

- **Versions** [6][40]: 3.7.0 released 2026-03-26; current 3.7.9 (2026-10-20); the 3.6 line is still patched (3.6.19, 2026-10-20). No 4.0 release yet.
- **Notable 3.x changes**:
  - 3.0 (2024-04-08) made schema v13/TSDB the default, enabled structured metadata by default (it requires tsdb+v13 or Loki *"won't start"*), added native OTLP ingestion at `/otlp`, experimental bloom filters, and deprecated BoltDB [41][11].
  - 3.7 shared the scheduler worker, stopped parsed labels from overriding structured metadata, added `loki health`, and allowed compactor delete markers in object storage [40].
  - The pattern ingester and `volume_enabled` power Grafana **Logs Drilldown**. That needs Grafana ≥ 13.2.0 and Loki ≥ 3.2.0 [42]. Kubementat's frozen Grafana 12.3 does not meet this, which is another reason to move Grafana to `grafana-community/grafana`.
- **Loki 4.0 outlook** (Grafana "What's next" notes dated 2026-08-24, no release date) [9][15][43]:
  - SSD mode is removed and a new HA Monolithic mode arrives.
  - The Table Manager and multi-store backends (BoltDB etc.) are removed, and TSDB becomes the only index.
  - The thanos objstore client becomes the default.
  - A Red Hat ticket describes a columnar "dataobj" storage and a new query engine [44] [secondary source].
  - **Implication:** a fresh install on Monolithic + TSDB v13 + compactor retention is already the 4.0-compatible path.
- **Community chart facts** (`grafana-community/loki` 20.0.2, `kubeVersion >=1.25`) [14][12]:
  - Default `deploymentMode: Monolithic`.
  - `loki.storage.type: filesystem` is supported. `validate.yaml` refuses more than one monolithic replica, and any scalable or distributed replicas, without object storage.
  - A `schemaConfig` is mandatory. The template fails with *"You must provide a schema_config for Loki"*.
  - 18.3.0 retains PVCs on uninstall by default.
  - 19.3.0 stopped rolling pods on chart-version label changes.
  - 20.0.0 removed MinIO and enforces strict top-level keys.
  - For monolithic mode the main Service is named after the release fullname. With release name `loki` that is **`loki`**, port 3100, so the URL `loki.loki.svc.cluster.local:3100` is unchanged with the gateway disabled. With the gateway enabled, the gateway Service listens on port 80.
- **Retention** [7]:
  - Set `compactor.retention_enabled: true`, `compactor.delete_request_store` (required), and `limits_config.retention_period: 336h`.
  - It requires a 24h index period. Deletion happens after `retention_delete_delay` (default 2h).
  - Loki does **not** delete by free disk space. With filesystem storage, size the PVC for 14 days of volume or add a size alarm [17].
- **Multi-tenancy**: `auth_enabled: false` puts everything in the single tenant `fake` and needs no `X-Scope-OrgID`. The chart default is `true`, so it must be overridden. Otherwise every push and query needs a tenant header [14] [verified by running: push and query worked without a header].
- **License**: AGPL-3.0. No impact for self-hosting unmodified binaries [inference].

### 6.2 VictoriaLogs (fallback backend)

- Apache-2.0, single-node or cluster, LTS lines exist (v1.52.x LTS) [21][27].
- Ingests the Loki push protocol at `/insert/loki/api/v1/push`. Promtail, Grafana Agent and Alloy are documented clients. Labels become stream fields, overridable with `_stream_fields` [29].
- Chart `victoria-logs-single`: `server.retentionPeriod` (a number without a unit means months; minimum 24h), `server.persistentVolume.{enabled,size}` (default 10Gi), Service `<release>-victoria-logs-single-server:9428`. No collector is bundled. Use `victoria-logs-collector` (vlagent DaemonSet) or keep Alloy [35][45].
- Grafana: the `victoriametrics-logs-datasource` plugin (Apache-2.0, Grafana-signed, v0.33.1, 2026-10-16) has to be installed, for example via the Grafana chart's `plugins:` list [28].
- Verified locally: a Loki-format JSON push to v1.53.0 returned 204 and was queryable with LogsQL `namespace:="dev1-pipelines"`.
- Trade-offs: dashboards and LogQL docs must be rewritten in LogsQL. No Logs Drilldown. It is a smaller ecosystem, but vendor-neutral ingestion keeps the collector swappable.

### 6.3 Other backends (not recommended for Kubementat)

- **OpenObserve**: single binary with an attractive footprint. AGPL OSS edition plus a commercial Enterprise edition (SSO, advanced RBAC etc. are enterprise-only), so open-core lock-in pressure is higher. Its own UI rather than Grafana [22].
- **Quickwit**: acquired by Datadog in January 2025 and relicensed to Apache-2.0. Still actively released: 0.9.0 on 2026-07-25 with Ingest V2 as default, 0.9.3 on 2026-10-22, last commit 2026-10-27. The Helm chart still ships app v0.8.2, which is a maintenance-signal concern. Its design is object-storage first. A Grafana plugin exists [23][24][46].
- **SigNoz**: OTel-native and ClickHouse-based, with its own UI. The `ee/` directory is under an enterprise license. Too heavy for the Pi target and duplicates Grafana [25].
- **OpenSearch / Elasticsearch (EFK)**: mature full-text search, but JVM memory needs rule out Pi-class nodes. ECK (the operator) is Elastic-License-2.0. Elasticsearch regained an AGPL option in Aug 2024 [26].

### 6.4 Collectors in more detail

- **Alloy (recommended)**: GA stability by default in the chart (`stabilityLevel: generally-available`). Recent breaking change (v1.21.0): the `loki.write` WAL now needs `--stability.level=experimental`, which the recommended config does not use [30]. The DaemonSet must filter discovery to its own node (`spec.nodeName=$HOSTNAME`) [39]. `loki.source.kubernetes_events` with `clustering { enabled = true }` and chart `alloy.clustering.enabled: true` means *"only a single node in the cluster will collect events"* [37]. That gives events without a second Deployment.
- **Fluent Bit**: best footprint and `arm/v7` support. With the plain chart you write the `[INPUT] tail` / `[FILTER] kubernetes` / `[OUTPUT] loki` config yourself. Label parity with Promtail (`app`, `job`) needs Lua or record-modifier work [inference]. `fluent-operator` adds CRDs, which is not worth it at Kubementat's scale [inference].
- **Vector**: excellent transforms (VRL), arm64 and arm/v7 images. It is MPL-2.0 and Datadog-controlled. Releases are frequent and often carry breaking changes (v0.59.0 had 6 breaking changes) [32].
- **OpenTelemetry Collector**: use the `opentelemetry-collector-k8s` image with the chart presets `logsCollection` (filelog receiver, needs `mode: daemonset`), `kubernetesAttributes` and `kubernetesEvents` [38]. Shipping to Loki over OTLP changes label names to the OTel form (`k8s_namespace_name`, `k8s_pod_name`, `service_name`) [36]. Worth it only if Kubementat adopts OTLP for traces and metrics as well [inference].

---

## 7. Migration plan

### 7.1 Steps

1. **Pin and add repos.** Add `grafana-community` (`https://grafana-community.github.io/helm-charts`) to `repositories:`. Pin `loki` to `20.0.2` and `alloy` to `1.14.3`. In the same PR or a follow-up, move Grafana to `grafana-community/grafana` and pin it (14.2.0 / Grafana 13.3.0 today; that is a major version jump, so test it separately). OCI note: at the time of writing, `oci://ghcr.io/grafana-community/helm-charts/loki` had 20.0.1 but not yet 20.0.2, while the HTTP repo had both. Keep the HTTP repo, or pin 20.0.1 if you switch to OCI [verified].
2. **Rewrite the values.** Replace `logging/loki_stack_values.encrypted.yaml` and the unused `loki_values.encrypted.yaml.template` with the values in 8.2, and add `logging/alloy_values.encrypted.yaml` from 8.3. Remove `table_manager` and `chunk_store_config.max_look_back_period` completely. Chart 20 rejects unknown top-level keys.
3. **Schema for a new install.** Use a single `tsdb` / `v13` / `filesystem` period with a `from` date **in the past** (for example `2024-04-01`). A future `from` date leaves the present without a schema. [inference, standard Loki behaviour]
4. **Retention parity.** Set `compactor.retention_enabled: true`, `delete_request_store: filesystem` and `limits_config.retention_period: 336h`. Optionally set `max_query_lookback: 336h` to mirror the old `max_look_back_period`.
5. **Cut over (big bang, recommended for dev).**
   1. `helmfile -l component_name=loki destroy`, or `kmt` uninstall of the logging component.
   2. Delete the old PVC. The old StatefulSet's PVC is not removed by `helm uninstall`. It holds boltdb-shipper v11 data that the new single v13 period config cannot read anyway.
   3. `helmfile -l component=logging apply`, which installs `loki` (new chart) and `alloy`.
   4. Keep release name `loki` and namespace `loki`. That keeps `src/kubementat/constants.py` (`"loki": "loki"`), the uninstall logic and the Grafana datasource URL unchanged.
6. **Grafana.** Datasource: no change needed (8.4). Dashboard 12611 keeps working, because the Alloy config reproduces `container`, `pod` and `stream`. Re-check any custom dashboards that use `app`, `job` or `instance`.
7. **Smoke test.**
   - `kubectl -n loki get pods` shows `loki-0` and one `alloy-*` per node.
   - In Grafana Explore, `{namespace="loki"}` returns lines within about 1 minute.
   - `{job="integrations/kubernetes/eventhandler"}` returns events.
   - `curl loki:3100/ready` returns ready.
   - `curl loki:3100/config` contains `retention_enabled: true`.
8. **Docs.** Update `docs/README_MONITORING_AND_LOGGING.md` (Alloy instead of Promtail, events query, Drilldown once Grafana ≥ 13.2), README component list, and `TODOs.md`.

### 7.2 Historical logs, dual-run and why "join the ring" does not work

- The old instance is Loki **2.6.1** with an in-process ring, boltdb-shipper v11 and table-manager retention. The new one is 3.7.9 monolithic with TSDB v13 and its own memberlist ring. Joining one ring across a 2.6 → 3.7 jump, across two Helm releases, with different storage paths is not a supported migration path. Each monolithic instance owns its local filesystem store, so a "joined" ring could not query the other's chunks anyway [inference, based on [17] *"Running Loki as a cluster is not possible with the filesystem store"*].
- The supported way to keep old data would be an in-place upgrade with a new schema period: 2.6 → 2.9 → add a `tsdb`/`v13` period → 3.x [11]. That is not possible with a chart swap, because the PVC layout and names differ between charts [inference]. It is not worth the effort for a dev platform with 14-day retention.
- If old logs must stay visible, a **parallel read-only option** exists:
  1. Install the new stack in a temporary namespace (for example `logging`) or with release name `loki3`. Datasource URL `http://loki3.<ns>.svc.cluster.local:3100`.
  2. Point Alloy at it, and disable Promtail in the old release (`promtail.enabled: false`).
  3. Add a second datasource "Loki (legacy)".
  4. After 14 days, uninstall loki-stack and optionally rename.

  This breaks the "same namespace and URL" convention for two weeks, so I recommend it only for production-like environments.

### 7.3 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Chart defaults request about 10 GiB for memcached caches, plus canary, test pod, gateway and 3× replication | Use the values in 8.2: caches, canary, test and gateway off, `replication_factor: 1`, `singleBinary.replicas: 1`, `read/write/backend.replicas: 0` |
| `singleBinary.replicas` defaults to `0`, so nothing gets deployed | Set it to `1` explicitly |
| Disk fills up (filesystem store does not delete by disk usage) | Size the PVC for 14 days, alert on PVC usage via the existing Prometheus |
| Alloy has no `arm/v7` image | Kubementat's Pi target must be a 64-bit OS (arm64). Otherwise use Fluent Bit as the collector |
| inotify / file-handle limits on small nodes cause tailer errors ("too many open files") | Raise `fs.inotify.max_user_instances` / `max_user_watches` in the Pi/k3s node prep [inference, common DaemonSet tailer issue] |
| Docker-shim runtimes symlink `/var/log/pods` to `/var/lib/docker/containers` | k3s and modern kubeadm use containerd, so `/var/log/pods` holds real files. On Docker runtimes set `alloy.mounts.dockercontainers: true` |
| Chart major versions move fast (12 majors in 7 months, 20.0.0 on 2026-10-25) | Pin exact versions in helmfile and upgrade deliberately using the chart's README upgrade notes. Renovate/Dependabot are useful here |
| Loki 4.0 removes SSD, Table Manager and BoltDB | The recommended config uses none of them |
| AGPL concerns | Not relevant for self-hosted, unmodified use [inference]. VictoriaLogs (Apache-2.0) is the alternative if AGPL is unacceptable |
| Promtail-specific metrics or alerts | Kubementat has none [verified in repo grep] |

---

## 8. Example configuration (recommended stack)

### 8.1 helmfile release

```yaml
repositories:
- name: grafana              # still used for alloy (Grafana Labs maintained)
  url: https://grafana.github.io/helm-charts
- name: grafana-community    # loki (and grafana, see 7.1 step 1)
  url: https://grafana-community.github.io/helm-charts

releases:
# Log aggregation: storage + query (Loki monolithic, filesystem, 14d retention)
- name: loki
  chart: grafana-community/loki
  version: "20.0.2"
  namespace: loki
  labels:
    component: logging
    component_name: loki
    group: standard
  values:
    - logging/loki_values.encrypted.yaml

# Log collection: Alloy DaemonSet shipping all container logs + k8s events to Loki
- name: alloy
  chart: grafana/alloy
  version: "1.14.3"
  namespace: loki
  labels:
    component: logging
    component_name: alloy
    group: standard
  needs:
    - loki/loki
  values:
    - logging/alloy_values.encrypted.yaml
```

### 8.2 `logging/loki_values.encrypted.yaml` (chart 20.0.2)

```yaml
# Loki 3.7, monolithic, single replica, filesystem storage, 14 day retention.
# Reference: https://github.com/grafana-community/helm-charts/tree/main/charts/loki
deploymentMode: Monolithic

loki:
  auth_enabled: false            # single tenant, no X-Scope-OrgID header needed
  commonConfig:
    replication_factor: 1
  storage:
    type: filesystem             # chunks/rules under /var/loki on the PVC
  schemaConfig:
    configs:
      - from: "2024-04-01"       # must be in the past for a fresh install
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: index_
          period: 24h            # retention requires a 24h index period
  limits_config:
    retention_period: 336h       # 14 days, same as before
    allow_structured_metadata: true
    volume_enabled: true         # Grafana Logs Drilldown
    reject_old_samples: true
    reject_old_samples_max_age: 168h
  compactor:
    retention_enabled: true
    delete_request_store: filesystem
    working_directory: /var/loki/compactor
  pattern_ingester:
    enabled: true                # Grafana Logs Drilldown patterns; set false to save RAM

singleBinary:
  replicas: 1                    # chart default is 0
  sidecar: false                 # no rules sidecar (no Loki alert rules shipped)
  persistence:
    enabled: true
    size: 10Gi
  resources:
    requests: {cpu: 100m, memory: 256Mi}
    limits: {memory: 1Gi}

# no scalable/distributed targets without object storage
read: {replicas: 0}
write: {replicas: 0}
backend: {replicas: 0}

gateway:
  enabled: false                 # keeps http://loki.loki.svc.cluster.local:3100
chunksCache:
  enabled: false                 # default would request ~9.8Gi memcached
resultsCache:
  enabled: false
lokiCanary:
  enabled: false
test:
  enabled: false                 # helm test requires the canary
```

### 8.3 `logging/alloy_values.encrypted.yaml` (chart 1.14.3)

The labels match what the loki-stack Promtail chart produced: `namespace`, `pod`, `container`, `app`, `job`, `instance`, `component`, `node_name`, `stream`. They also cover Kubernetes events.

```yaml
controller:
  type: daemonset
alloy:
  clustering:
    enabled: true          # lets exactly one pod collect cluster events
  mounts:
    varlog: true           # hostPath /var/log (read pod log files)
  resources:
    requests: {cpu: 50m, memory: 128Mi}
    limits: {memory: 512Mi}
  configMap:
    content: |
      // Pods on this node only (Alloy runs as a DaemonSet)
      discovery.kubernetes "pods" {
        role = "pod"
        selectors {
          role  = "pod"
          field = "spec.nodeName=" + coalesce(sys.env("HOSTNAME"), constants.hostname)
        }
      }

      // Same label set the loki-stack promtail chart produced
      discovery.relabel "pod_logs" {
        targets = discovery.kubernetes.pods.targets

        rule {
          source_labels = ["__meta_kubernetes_pod_controller_name"]
          regex         = "([0-9a-z-.]+?)(-[0-9a-f]{8,10})?"
          target_label  = "__tmp_controller_name"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_name", "__meta_kubernetes_pod_label_app", "__tmp_controller_name", "__meta_kubernetes_pod_name"]
          regex         = "^;*([^;]+)(;.*)?$"
          target_label  = "app"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_instance", "__meta_kubernetes_pod_label_instance"]
          regex         = "^;*([^;]+)(;.*)?$"
          target_label  = "instance"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_label_app_kubernetes_io_component", "__meta_kubernetes_pod_label_component"]
          regex         = "^;*([^;]+)(;.*)?$"
          target_label  = "component"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_node_name"]
          target_label  = "node_name"
        }
        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          target_label  = "namespace"
        }
        rule {
          source_labels = ["namespace", "app"]
          separator     = "/"
          target_label  = "job"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_name"]
          target_label  = "pod"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_container_name"]
          target_label  = "container"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_uid", "__meta_kubernetes_pod_container_name"]
          separator     = "/"
          replacement   = "/var/log/pods/*$1/*.log"
          target_label  = "__path__"
        }
        // static pods (k3s/kubeadm control plane) log under their config hash
        rule {
          source_labels = ["__meta_kubernetes_pod_annotationpresent_kubernetes_io_config_hash", "__meta_kubernetes_pod_annotation_kubernetes_io_config_hash", "__meta_kubernetes_pod_container_name"]
          separator     = "/"
          regex         = "true/(.*)"
          replacement   = "/var/log/pods/*$1/*.log"
          target_label  = "__path__"
        }
      }

      local.file_match "pod_logs" {
        path_targets = discovery.relabel.pod_logs.output
      }

      loki.source.file "pod_logs" {
        targets    = local.file_match.pod_logs.targets
        forward_to = [loki.process.pod_logs.receiver]
      }

      loki.process "pod_logs" {
        stage.cri {}
        // stdout/stderr as a label; Grafana dashboard 12611 filters on it
        stage.labels {
          values = { stream = "" }
        }
        stage.label_drop {
          values = ["filename"]
        }
        forward_to = [loki.write.default.receiver]
      }

      // Kubernetes events; with clustering only one DaemonSet pod collects them
      loki.source.kubernetes_events "cluster_events" {
        job_name   = "integrations/kubernetes/eventhandler"
        log_format = "logfmt"
        clustering {
          enabled = true
        }
        forward_to = [loki.write.default.receiver]
      }

      loki.write "default" {
        endpoint {
          url = "http://loki.loki.svc.cluster.local:3100/loki/api/v1/push"
        }
      }
```

### 8.4 Grafana datasource (unchanged, optional additions)

```yaml
datasources:
  datasources.yaml:
    apiVersion: 1
    datasources:
    - name: Loki
      type: loki
      access: proxy
      url: http://loki.loki.svc.cluster.local:3100   # same Service name/port as before
      jsonData:
        maxLines: 1000
```

If the gateway is enabled later, the URL becomes `http://loki-gateway.loki.svc.cluster.local` (port 80) [inference from the chart's naming helper; I rendered only the gateway-disabled variant].

Fallback variant (VictoriaLogs). Add `victoriametrics-logs-datasource` to the Grafana chart's `plugins:` list, then:

```yaml
    - name: VictoriaLogs
      type: victoriametrics-logs-datasource
      access: proxy
      url: http://vls-victoria-logs-single-server.loki.svc.cluster.local:9428
```

with `vm/victoria-logs-single` values `server: {retentionPeriod: 14d, persistentVolume: {enabled: true, size: 10Gi}}`. In Alloy, change only the `loki.write` URL to `http://vls-victoria-logs-single-server.loki.svc.cluster.local:9428/insert/loki/api/v1/push` [29][45].

### 8.5 What I verified locally (2026-10-27)

- `helm template` of `grafana-community/loki` **20.0.2** with the values in 8.2 renders without error: one StatefulSet, Services `loki` (3100/9095/9096), `loki-headless`, `loki-memberlist`, and image `docker.io/grafana/loki:3.7.9`. The rendered `config.yaml` contains the compactor retention, the v13/tsdb filesystem schema and `auth_enabled: false`.
- `grafana/loki:3.7.9 -verify-config` on the rendered config returned `msg="config is valid"`.
- I ran Loki 3.7.9 with the rendered config, adapted for Docker by switching the ring to `inmemory` and removing memberlist and runtime-config. A JSON push returned 204, and `{namespace="dev1-pipelines"}` returned the line. Loki added `service_name` and `detected_level` automatically.
- `alloy validate` and `alloy fmt` (v1.21.2) pass on the config in 8.3. An earlier draft used the non-existent `stage.labeldrop`, which `validate` caught; the correct name is `stage.label_drop`. `helm template` of `grafana/alloy` 1.14.3 renders a DaemonSet with the `/var/log` hostPath and a ClusterRole including `events` get/list/watch.
- **Not verified**: an end-to-end run on a real Kubernetes or k3s cluster or on a Raspberry Pi (Alloy discovery against a live API server, real log volume, memory under load).

---

## 9. Open questions

1. **Raspberry Pi OS bitness.** Is the Pi target always arm64? If 32-bit (`arm/v7`) must be supported, Alloy is excluded and Fluent Bit becomes the collector.
2. **Grafana chart migration.** Move to `grafana-community/grafana` 14.x (Grafana 13) in the same change, or separately? It is needed for Logs Drilldown (Grafana ≥ 13.2) and for any future fixes.
3. **Historical logs.** Is losing up to 14 days of logs acceptable for every environment Kubementat users run, or should `kmt` offer the parallel "legacy datasource" path from 7.2?
4. **Object storage.** If a "PRO" MinIO/S3 option lands (see TODOs.md), should the logging component switch to `loki.storage.type: s3` automatically? That would enable multi-replica, and the HA Monolithic mode in Loki 4.0.
5. **Events.** Should Kubernetes events go into Loki by default (included above), or be optional?
6. **Pattern ingester.** Keep it on for Drilldown, or turn it off on the smallest profile to save memory? Its actual RAM cost under load is not measured.
7. **Upgrade cadence.** The community Loki chart produced majors 19 and 20 within days (2026-10-24/25). Kubementat should decide on a pin-and-bump policy, for example Renovate PRs with a CI `helm template` check.

---

## 10. Sources

All accessed 2026-10-27.

1. ArtifactHub API, `grafana/loki-stack` (2.10.3, deprecated, 2025-10-31) – https://artifacthub.io/api/v1/packages/helm/grafana/loki-stack
2. loki-stack README deprecation notice – https://github.com/grafana/helm-charts/blob/main/charts/loki-stack/README.md
3. loki-stack 2.10.3 chart package (bundled loki 2.16.0 / image 2.6.1, promtail 6.17.0 / 3.5.1) – https://github.com/grafana/helm-charts/releases/download/loki-stack-2.10.3/loki-stack-2.10.3.tgz
4. ArtifactHub API, `grafana/promtail` (6.17.1, deprecated) – https://artifacthub.io/api/v1/packages/helm/grafana/promtail
5. Grafana Loki docs, Promtail (EOL 2026-03-02) – https://grafana.com/docs/loki/latest/send-data/promtail/
6. grafana/loki GitHub releases (v3.7.9 2026-10-20, v3.6.19) – https://github.com/grafana/loki/releases/
7. Loki docs, Retention (compactor, delete_request_store, table manager deprecated) – https://grafana.com/docs/loki/latest/operations/storage/retention/
8. Grafana What's next: Loki table manager removed (4.0) – https://grafana.com/whats-next/2026-08-24-loki-table-manager-removed/
9. Grafana What's next: Loki multi-store backends removed (4.0) – https://grafana.com/whats-next/2026-08-24-loki-multi-store-backends-removed/
10. Loki 3.7 release notes (BoltDB deprecated, Promtail removed in 3.7.3) – https://grafana.com/docs/loki/latest/release-notes/v3-7/
11. Loki upgrade guide (2.x → 3.x, tsdb/v13 requirement, max-look-back removal) – https://grafana.com/docs/loki/latest/setup/upgrade/
12. Loki docs, Upgrade to the community Helm chart (6.55.0 → 18.x, breaking changes) – https://grafana.com/docs/loki/latest/setup/upgrade/upgrade-to-community/
13. ArtifactHub API, `grafana/loki` (7.5.0, Loki 3.6.16, GEL) and `grafana-community/loki` (20.0.2, 3.7.9) – https://artifacthub.io/api/v1/packages/helm/grafana/loki , https://artifacthub.io/api/v1/packages/helm/grafana-community/loki
14. grafana-community/helm-charts, Loki chart (Chart.yaml, values.yaml, README upgrade notes 19.x/20.x, templates/validate.yaml) – https://github.com/grafana-community/helm-charts/tree/main/charts/loki
15. Grafana What's next: Loki SSD deployment no longer available (4.0, HA Monolithic) – https://grafana.com/whats-next/2026-08-24-loki-ssd-deployment-no-longer-available/
16. grafana-community/helm-charts releases (loki-19.x / 20.x, 2026-10-24..27) – https://github.com/grafana-community/helm-charts/releases?q=loki
17. Loki docs, Filesystem object store – https://grafana.com/docs/loki/latest/operations/storage/filesystem/
18. Alloy docs, Migrate from Promtail – https://grafana.com/docs/alloy/latest/set-up/migrate/from-promtail/
19. Grafana What's next: MinIO deprecated in Loki Community Helm Chart (2026-10-31) – https://grafana.com/whats-next/2026-10-31-minio-deprecated-in-loki-community-helm-chart/
20. grafana/loki license (AGPL-3.0, GitHub API) – https://github.com/grafana/loki/blob/main/LICENSE
21. VictoriaLogs docs (license, Raspberry Pi, resource claims, retention flags) – https://docs.victoriametrics.com/victorialogs/
22. OpenObserve GitHub README (AGPL-3.0 / Enterprise) – https://github.com/openobserve/openobserve
23. Quickwit joins Datadog – https://quickwit.io/blog/quickwit-joins-datadog
24. Datadog acquires Quickwit – https://www.datadoghq.com/blog/datadog-acquires-quickwit/
25. SigNoz LICENSE (MIT outside ee/) – https://github.com/SigNoz/signoz/blob/main/LICENSE
26. InfoQ: Elastic adds AGPL option (Sep 2024) – https://www.infoq.com/news/2024/09/elastic-open-source-agpl
27. VictoriaLogs GitHub releases (v1.53.0 2026-10-01, v1.52.x LTS) – https://github.com/VictoriaMetrics/VictoriaLogs/releases
28. VictoriaLogs Grafana integration; Grafana plugin catalog API – https://docs.victoriametrics.com/victorialogs/integrations/grafana/ , https://grafana.com/api/plugins/victoriametrics-logs-datasource
29. VictoriaLogs data ingestion via Promtail/Alloy (Loki push endpoint) – https://docs.victoriametrics.com/victorialogs/data-ingestion/promtail/
30. grafana/alloy GitHub releases (v1.21.2 2026-10-22) – https://github.com/grafana/alloy/releases
31. fluent/fluent-bit GitHub releases (v5.1.5 2026-10-21) – https://github.com/fluent/fluent-bit/releases
32. vectordotdev/vector GitHub releases (v0.60.0 2026-10-19) – https://github.com/vectordotdev/vector/releases
33. Alloy Helm chart (values.yaml, Chart.yaml 1.14.3) – https://github.com/grafana/alloy/tree/main/operations/helm/charts/alloy
34. k8s-monitoring Helm chart README / Chart.yaml (4.5.5, alloy-operator 0.9.3) – https://github.com/grafana/k8s-monitoring-helm/tree/main/charts/k8s-monitoring
35. victoria-logs-collector chart docs – https://docs.victoriametrics.com/helm/victoria-logs-collector/
36. Loki docs, Ingesting logs with OpenTelemetry (OTLP label mapping) – https://grafana.com/docs/loki/latest/send-data/otel/
37. Alloy reference, `loki.source.kubernetes_events` (clustering) – https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.kubernetes_events/
38. OpenTelemetry Collector Helm chart README (presets) – https://github.com/open-telemetry/opentelemetry-helm-charts/tree/main/charts/opentelemetry-collector
39. Alloy docs, Collect Kubernetes logs – https://grafana.com/docs/alloy/latest/collect/logs-in-kubernetes/
40. Loki 3.7 release notes (3.7.0 2026-03-26 and patch dates) – https://grafana.com/docs/loki/latest/release-notes/v3-7/
41. Loki 3.0 release notes (2024-04-08) – https://grafana.com/docs/loki/latest/release-notes/v3-0/
42. Grafana Logs Drilldown requirements – https://grafana.com/docs/grafana/latest/explore/simplified-exploration/logs/access/
43. Grafana What's next: Thanos object storage default for Loki (4.0) – https://grafana.com/whats-next/2026-08-24-thanos-object-storage-now-default-for-loki/
44. Red Hat Jira LOG-7350 (Loki 4.0 dataobj architecture, secondary) – https://issues.redhat.com/browse/LOG-7350
45. victoria-logs-single chart docs – https://docs.victoriametrics.com/helm/victoria-logs-single/
46. quickwit-oss/quickwit releases (v0.9.0 2026-07-25 … v0.9.3 2026-10-22) – https://github.com/quickwit-oss/quickwit/releases
47. Grafana chart migration notice (grafana/grafana → grafana-community, 2026-01-30) – https://github.com/grafana/helm-charts/blob/main/charts/grafana/README.md ; forum announcement https://community.grafana.com/t/helm-repository-migration-grafana-community-charts/160983
48. Governance proposal that led to grafana-community (grafana/helm-charts#3955) – https://github.com/grafana/helm-charts/issues/3955
49. ArtifactHub API for other chart versions cited (grafana-community/grafana 14.2.0; fluent/fluent-bit 0.58.3; fluent/fluent-operator 4.3.0; vector/vector 0.60.0; opentelemetry-collector 0.178.0; opentelemetry-operator 0.127.0; openobserve 1.1.2; quickwit 0.8.17; signoz 0.147.0; opensearch 3.9.0; eck-operator 3.5.0 Elastic-2.0; victoria-logs-* 0.13.10/0.3.8/0.2.9) – https://artifacthub.io/api/v1/packages/helm/{repo}/{chart}
50. Grafana dashboard 12611 "Logging Dashboard via Loki" (labels container/pod/stream) – https://grafana.com/grafana/dashboards/12611
