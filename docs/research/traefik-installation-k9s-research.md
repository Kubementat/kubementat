# Research: Installing Traefik on Kubernetes, and the plan for kubementat

**Date of retrieval:** 2026-10-28 (all URLs below were fetched on this date; versions are the ones the upstream indexes returned that day).
**Author:** research/plan agent, branch `python_refactor_2` @ `44b27fc`.
**Scope:** how Traefik should be installed on Kubernetes, and what that means for kubementat's file layout. Product code is not touched by this document.
**Method:** the chart's own published artifacts (the helm repository `index.yaml`, the chart's `Chart.yaml`, `VALUES.md`, `Changelog.md`, `values.yaml` and repo `README.md` on the `master` branch), the Traefik docs, and the k3s docs. Blog posts were avoided. Every claim carries a source; things I could not confirm from a primary source are under **Unverified** and are never stated as fact.
**Notation:** `[S<n>]` points at the Sources list at the end. **[Inference]** marks my own reasoning built on a cited fact. **[Unverified]** marks a gap. Where I point at a file under `tests/e2e/**` I name the symbol, not the line number: that tree is being edited by another agent while this document is written, and numbers there drift (this document's own commit contains no change to it).

---

## 0. What I would do, in five lines

1. Install **one chart: `traefik/traefik`** (newest published: **42.1.3**, Traefik **v3.7.16**, `kubeVersion >=1.30.0-0`) [S1][S2][S4]. The `traefik-crds` companion is not needed — it is deprecated since chart v38.0.2 and the main chart ships its `crds/` [S6][S7].
2. Land it in **`group: routing`** next to cert-manager, with the release's namespace from `env "TRAEFIK_DEPLOYMENT_NAMESPACE" | default "traefik"`, and **always installed** by `kmt install` rather than gated — dropping nginx without naming a replacement left every ingress path in the product classless.
3. On the lane, **disable k3s's bundled Traefik** (`disable: [traefik]` in `/etc/rancher/k3s/config.yaml`) rather than running two controllers: k3s's `svclb` pods already hold node ports 80/443, so a second `LoadBalancer` controller is a Service that never schedules, and two controllers both writing `Ingress.status` is a race upstream documents [S11][S12][S13][S19].
4. **Keep cert-manager.** Traefik's own ACME resolver works, but it costs a volume for `acme.json`, two resolvers where there are two issuers, and an annotation change in every file that today says `cert-manager.io/cluster-issuer` [S15][S16]. What must change in `install_routing.sh` is one key: the solver's `class: nginx` becomes `ingressClassName: traefik`, because a solver Ingress annotated `kubernetes.io/ingress.class: nginx` is one Traefik's default provider will not serve [S16][S18].
5. For kubementat's own Ingress files, **do not migrate annotations to Traefik middlewares — delete them**: `ssl-redirect`/`force-ssl-redirect` become a cluster-wide entrypoint redirect, `proxy-body-size: "0"` becomes nothing, `ingressClassName: nginx` becomes `traefik`. Nothing in this repository's tree needs the CRD path; path rewrites and snippets would [S23][S24].

The five decisions that are **not** mine are listed in §8; §9 lists every claim I could not verify from a primary source.

---

## 1. Which chart, which version, and what changed between v2 and v3

**There are two charts, and only one of them is what kubementat should install.**

| | `traefik` | `traefik-crds` |
|---|---|---|
| Latest chart version published in the repo index | **42.1.3**, released 2026-10-22 [S1][S2] | 1.18.0, released 2026-05-06 (index also lists 1.14.1, a backport, 2026-05-04) [S1][S3] |
| Traefik (appVersion) it installs | **v3.7.16** [S2][S4] | none — it ships no proxy, `appVersion` is empty for every entry [S1][S3] |
| `kubeVersion` constraint | `>=1.30.0-0` (raised by chart 42.0.0) [S4][S5] | `>=1.25.0-0` for 1.18.0 (1.14.1 keeps `>=1.22.0-0`) [S1][S3] |
| What it contains | the whole install: Deployment/DaemonSet, RBAC, Service, entrypoints, and the Traefik CRDs in `crds/` [S6] | CRDs only, for people who must apply CRDs out of band (GitOps/OCI flows) [S7] |

**`traefik` is the one to use, and it is self-contained for CRDs.** The chart ships a `traefik/crds` directory [S6], so `helm install`/`helmfile apply` creates the `*Traefik*` CRDs on first install. The separate `traefik-crds` chart exists to work around Helm's CRD caveats and is **deprecated since chart v38.0.2** according to the chart repo's own README: "Starting with v34.x, to work around Helm caveats, you can use an additional chart dedicated to CRDs: **traefik-crds**. ⚠️ This has been deprecated since v38.0.2." [S7]. A kubementat helmfile block that installs only `traefik/traefik` is therefore correct and is the smallest thing that works.

**What kubementat does have to care about, though, is that Helm does not update CRDs.** The README's upgrade instructions say you `MUST` upgrade CRDs before `helm upgrade` because Helm does not do it automatically, and give `helm show crds traefik/traefik | kubectl apply --server-side --force-conflicts -f -` [S7]. This is exactly the shape of the `uninstall_nginx_ingress()` problem in reverse: the CRDs outlive the release, and nothing in a `helm uninstall` takes them away. It argues for either (a) an explicit CRD-apply step before/with the traefik release, or (b) accepting that CRDs stay and stating so in the uninstall sweep. See §7.

**Gateway API CRDs are a separate matter and are not needed for what is being decided here.** The chart README: "⚠️ Gateway API CRDs are no longer shipped with this chart. If you use the Kubernetes Gateway API provider, you will need to install them yourself before deploying Traefik v3.7.12" [S7]. Chart 42.1.0's release notes repeat it for the Gateway API v1.6.3 bump [S5]. Since kubementat's plan is the Ingress + IngressRoute path, not Gateway, nothing should pull in Gateway API. **[Inference]** keeping `experimental.kubernetesGateway.enabled` at its default `false` (VALUES.md) [S8] is what keeps that requirement off the table.

**Traefik v2 → v3, and what it means for kubementat.** Chart v28 is the boundary: "Starting with v28.x, this chart bootstraps Traefik Proxy version 3 as a Kubernetes ingress controller, using the `IngressRoute` Custom Resource" [S7], with a migration guide listed at that point. Consequences that reach into kubementat's files:

1. **CRD group moved** from `traefik.containo.us` (v2) to `traefik.io` (v3). **[Unverified — needs confirmation]**: the migration page did not get fetched in this pass; I would confirm the group rename and the full list of renamed kinds against `https://doc.traefik.io/traefik/migration/v2-to-v3/` before writing a CRD name anywhere. It matters because a CRD-name sweep (like `uninstall_linkerd`'s `.linkerd.io` sweep) that gets the group wrong silently deletes nothing.
2. **`ingressClass` handling is per-provider.** `providers.kubernetesIngress.ingressClass` governs `networking.k8s.io/Ingress` objects ("only Ingresses containing an annotation with the same value are processed. Otherwise, Ingresses missing the annotation, having an empty value, or the value `traefik` are processed"), and `providers.kubernetesCRD.ingressClass` is the same idea for `IngressRoute` [S8]. Both providers are enabled by default in the chart (`providers.kubernetesIngress.enabled: true`, `providers.kubernetesCRD.enabled: true`) [S8]. **This is the single most important knob for kubementat**: with the defaults, an existing `Ingress` that says nothing about class is served by Traefik.
3. **The chart creates the IngressClass itself.** `ingressClass.enabled: true` and `ingressClass.isDefaultClass: true` by default [S8]. **[Inference]** On a cluster that already has another default class, `isDefaultClass: true` is a cluster-wide behaviour change and must be an explicit values decision, not a default kubementat silently copies.
4. **Ports/entrypoints**: `ports.web` (container 8000, service 80) and `ports.websecure` (container 8443, service 443) plus `ports.traefik` (8080, the API/dashboard entrypoint) and `ports.metrics` (9100) [S8]. `ports.<ep>.hostPort` and `ports.<ep>.nodePort` exist per entrypoint, and `ports.<ep>.exposedPort` sets the Service-side port [S8]. This is what makes the k3s `svclb` collision addressable without hostNetwork (§3).
5. **The Service defaults to `LoadBalancer`, single service** (`service.spec.type: LoadBalancer`, `service.single: true`) [S8][S5]. On k3s with no CCM that means pending — see §3.

---

## 2. Install best practice, as the chart itself documents it

Everything in this section is a chart default or an explicitly documented value, not folklore. Where I have drawn a conclusion from a default I mark it.

- **Namespace and RBAC.** `rbac.enabled: true` by default, which creates the ClusterRole/ClusterRoleBinding the proxy needs [S8]. `rbac.namespaced: true` switches to Role/RoleBinding and sets `disableClusterScopeResources`, and the chart warns that "IngressClass, NodePortLB and Gateway provider cannot be used with namespaced RBAC" [S8]. `serviceAccount.name` lets you bring your own SA [S8]. **[Inference]** kubementat's per-component namespace pattern plus cluster-wide watch of all namespaces is compatible with the default (`namespaced: false`) mode; the ingress-controller's ClusterRole is the price of watching more than one namespace, and it is not avoided by putting the release in its own namespace.
- **Deployment vs DaemonSet.** `deployment.kind: "Deployment"`, `deployment.replicas: 1` by default, `DaemonSet` available as a value [S8]. **[Inference]** on a single-node cluster the two are equivalent in reach, and Deployment is the better default because it is what upstream ships, is cheaper, and leaves room for an HPA later (`deployment.replicas: null` is documented for when an external controller owns scaling) [S8].
- **Resource requests.** `resources: {}` — the chart ships none [S8]. **[Inference]** kubementat's other components do get requests, and a component that fronts everything should not be OOM-killable at the default 0/100Mi, so setting requests/limits is a kubementat decision, not a chart default (§7).
- **The dashboard must be off by default, and in the shipped chart it effectively is.** `ingressRoute.dashboard.enabled: false` and `ingressRoute.healthcheck.enabled: false` by default, with the entrypoint list defaulting to `traefik`, "which is not exposed" [S8]. And `ports.traefik.expose.default: false`, with the chart's own comment: "You SHOULD NOT expose the traefik port on production deployments. If you want to access it from outside your cluster, use `kubectl port-forward` or create a secure ingress" [S8]. Note the subtlety: `api.dashboard: true` is set in `values.yaml` (it only registers the API on the internal entrypoint) [S9], while `api.insecure` is unset [S9] — so "dashboard off" means "no route to it", not "the API is disabled". **[Inference]** a kubementat values template that leaves `ingressRoute.dashboard` alone, plus `ports.traefik.expose.default: false`, is a compliant default; turning it on at all should be a per-environment opt-in, and Traefik's own dashboard docs describe the `dashboard@internal` router + `api.dashboard=true` requirement plus authentication (BasicAuth/DigestAuth middleware) if it is ever exposed. **[Unverified]** the exact authentication flags recommended for an exposed dashboard — fetch `https://doc.traefik.io/traefik/observability/dashboard/` and its rest-authorization page.
- **Metrics.** `metrics.prometheus.entryPoint: "metrics"` and the entrypoint is not exposed (`ports.metrics.expose.default: false`, port 9100) [S8]. `metrics.prometheus.serviceMonitor.enabled: false` and `prometheusRule.enabled: false` by default, because they need the Prometheus Operator CRDs [S8]. **[Inference]** with kubementat's own annotation-based Prometheus scraping, the cheapest correct move is to leave `metrics.*` off and let the metrics endpoint be scraped in-cluster, or enable `metrics.prometheus` + ServiceMonitor only if the Prometheus Operator CRDs are known present.
- **Security posture.** The chart exposes `tlsOptions: {}` (creates `TLSOption` CRDs) and `tlsStore: {}` (creates `TLSStore` CRDs), with `tlsOptions` explicitly intended for the cluster-wide `default` TLSOption [S8]. TLS minimum version and cipher suites are a `TLSOption` concern, not a values-file flag. **[Unverified]** the exact `spec.minVersion` / `cipherSuites` field spelling and the recommended floor (`VersionTLS12` vs `VersionTLS13`) — check `https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/crd/tls/tlsoption/`. Chart 42.x additionally restricts the namespace that may define a cluster-wide `default` TLSOption/TLSStore via `providers.kubernetesCRD.defaultTLSResourcesNamespace`, which needs Traefik v3.7.11+ [S8].
- **Provider flags.** Both `providers.kubernetesIngress` and `providers.kubernetesCRD` are on [S8]; `publishedService.enabled: true` so Ingress status is written from the chart's own Service [S8]. `providers.kubernetesIngress.ingressEndpoint.hostname` / `.ip` let you set the status address explicitly if publishedService is not wanted [S8].
- **How to expose 80/443 without fighting an existing `svclb`.** Documented mechanisms: `service.type` (default LoadBalancer), `ports.<ep>.nodePort` per entrypoint [S8], `ports.<ep>.hostPort` per entrypoint [S8], and `hostNetwork: false` with the chart noting that if `hostNetwork: true` and replicas>1 a pod anti-affinity is recommended and set for you [S8]. **[Inference]** `hostPort` is the knob that collides with k3s's `svclb-traefik`, so it is the one kubementat must not set by default (§3, §4).
- **A documented nginx-compat provider exists.** The chart carries `providers.kubernetesIngressNGINX` with `ingressClass: "nginx"`, `controllerClass: "k8s.io/ingress-nginx"`, `allowSnippetAnnotations: false`, `proxyBodySize`, `clientBodyBufferSize`, `ipAllowListStrategy` and more, defaulting to `enabled: false` [S8]. **[Unverified]** whether it is a Traefik Hub (licensed) feature or OSS — the `hub.*` and `modsec` entries that reference `Requires a valid license token` suggest the former, but I did not confirm the provider's own documentation page. If it is OSS, it is a genuine migration lever for §6; if it is Hub, kubementat must not count on it. This is one of the two questions that most change the migration ticket.

---

## 3. k3s coexistence: the bundled Traefik and the `svclb` hostPort collision

This is the part of the task the plan has to answer, so it gets the most citations.

**What k3s ships, in its own words.** "Traefik is deployed by default when starting the server" and "The Traefik ingress controller deploys a LoadBalancer Service that uses ports 80 and 443, advertises the LoadBalancer Service's External IPs in the Status of Ingress resources it manages" [S10]. "By default, ServiceLB will use all nodes in the cluster to host the Traefik LoadBalancer Service, meaning ports 80 and 443 will not be usable for other HostPort or NodePort pods, and Ingress resources' Status will show all cluster members' node IPs" [S10]. So on a stock k3s node — which is exactly what `tests/e2e/stages/01-provision.sh` builds (`curl -sfL https://get.k3s.io | sudo sh -`, no flags, line 90) — ports 80 and 443 on the node are already claimed by the pods ServiceLB created for k3s's own Traefik.

**How the claiming works, which is why it fails the way it does.** ServiceLB (formerly Klipper) "watches Kubernetes Services with the spec.type field set to LoadBalancer", and "For each LoadBalancer Service, a DaemonSet is created in the kube-system namespace... These pods leverage hostPort using the service port, hence they will only be deployed on nodes that have that port available. If there aren't any nodes with that port available, the LB will remain Pending" [S11]. So the collision is not a subtle routing problem — the second holder of the port is a DaemonSet pod that simply cannot schedule, and the Service it backs stays `Pending`.

**What k3s says to do about it.**
- To remove the bundled controller: "To remove Traefik from your cluster, start all servers with the `--disable=traefik` flag" [S10], and the same flag is documented for disabling a packaged component on an existing cluster — "Disabled AddOns are actively uninstalled from the cluster, and the source files deleted from the manifests directory" [S12]. The alternative `.skip` file mechanism is explicitly *not* equivalent: "creating a `.skip` file after an AddOn has already been created will not remove or otherwise modify it or the resources it created", and "If Traefik had already been deployed prior to creating the traefik.skip file, Traefik would stay as-is, and would not be affected by future updates when K3s is upgraded" [S12]. **A plan that relies on a `.skip` file gets a cluster with both controllers and no way to tell that from the outside.**
- ServiceLB is separately disableable: "To disable ServiceLB, configure all servers in the cluster with the `--disable=servicelb` flag. This is necessary if you wish to run a different LB, such as MetalLB" [S11]. And the state you land in is documented: "When ServiceLB is disabled, LoadBalancer Services, including the packaged Traefik Service, remain pending until another load balancer is installed, but their NodePorts are still allocated on every node. If you expose Traefik through other means, set its Service type to ClusterIP" [S11].
- k3s's own knob for a different exposure model is the `HelmChartConfig` in `/var/lib/rancher/k3s/server/manifests`, e.g. `spec.valuesContent` setting `service.spec.type: ClusterIP` [S13]. Note the trap it warns about: "Starting with the June 2026 releases (v1.33.13+k3s1, v1.34.9+k3s1, v1.35.6+k3s1 and v1.36.2+k3s1), K3s ships Traefik chart v40, which moved the Service type from `service.type` to `service.spec.type`. The old `service.type` key is silently ignored, and the Traefik Service falls back to LoadBalancer" [S13]. **This is directly relevant to kubementat**: the chart's own `service.spec.type` spelling [S8] is the new one, so a kubementat values template written against an older chart (`service.type`) would silently ship a `LoadBalancer` service. **[Unverified]** which k3s version the harness's `curl -sfL https://get.k3s.io` currently resolves to, and therefore whether the node is running chart v40 (Traefik 3.3-ish) or newer — the lane's own `k3s --version` dump (`tests/e2e/stages/01-provision.sh:143`) is the place to read it. It matters for §1's `kubeVersion >=1.30.0-0` floor on chart 42.x [S5].

**The lane's existing position, restated.** The `IDEMPOTENCY_EXCLUDED_NAMESPACES` comment in `tests/e2e/harness/config.py` excludes `kube-system` from the idempotency snapshot because "k3s's own helm-controller installs and updates its bundled addons (traefik: `chart-values-*`, `sh.helm.release.v1.traefik*.v1` and the `helm-traefik` ServiceAccounts) whenever it feels like"; the `helm_baseline_releases` read in `stage_provision` (`tests/e2e/harness/stages.py`) takes a helm baseline after those charts land; and plan §1.4c records a real collision between k3s's traefik `svclb` and the routing group's ingress `svclb`. So the lane already treats k3s's Traefik as baseline noise, and already observed the collision.

**The three options, and the one I would take.**

| Option | What it costs | What you get |
|---|---|---|
| **A. Disable k3s's bundled Traefik in the lane** (`disable: [traefik]` in `/etc/rancher/k3s/config.yaml`, restart) and let kubementat's release be the only controller | A harness/provisioning change, plus re-baselining everything that assumes `kube-system` has a traefik release; ServiceLB still owns 80/443 for kubementat's own `LoadBalancer` Service | One controller, one class, one status writer, no hostPort fight. Closest to a cluster without an ingoing bundled controller. |
| **B. Leave k3s's Traefik and give kubementat's Traefik a Service type that allocates no host ports** (`service.spec.type: ClusterIP`, optionally `nodePort` per entrypoint) | Two controllers on the node, two IngressClasses, two writers of `Ingress.status`, and a split in which class a workload's Ingress must name | No hostPort collision at all, no harness change. But the collision is dodged, not resolved, and the lane cannot assert "exactly one controller". |
| **C. Leave both, keep `LoadBalancer`** | k3s's `svclb-traefik` keeps 80/443; kubementat's own `svc-*` DaemonSet pods are `Pending` on every node (per [S11]) | A Service that never becomes Ready — this is the current lane collision, and it is why the harness currently treats the ingress `svclb` as noise rather than as a passing thing. |

**Recommendation: A, with B as the documented fallback for clusters kubementat does not provision.** Concretely: kubementat does not own the k3s server flags, so the *product* cannot disable what k3s bundles — that is a lane/provisioning decision, and it belongs in `tests/e2e/stages/01-provision.sh` (write `disable: [traefik]` next to the `write-kubeconfig-mode` line it already writes, then restart before the baseline is taken). What the product *can* do is make its exposure model an explicit configuration key rather than the chart's `LoadBalancer` default, so a cluster that already has a node-port-holding `svclb` can be told "ClusterIP + nodePort" without editing the values template (§7). Choosing A over B is the owner's call, because it changes what the e2e lane proves about a real cluster. **[Inference]** The reason to prefer A is not cleanliness for its own sake: two controllers both able to be `isDefaultClass` is a class-conflict, and both writing Ingress status makes any "the ingress works" assert non-deterministic — the lane would then be asserting something the cluster cannot guarantee.

---

## 4. TLS: Traefik's own ACME resolver, or cert-manager, or both

**What Traefik's ACME actually needs.** A certificate resolver is static configuration: `certificatesResolvers.<name>.acme.email`, `.storage` (a file path, default `acme.json`, `Required`), and one of `httpChallenge.entryPoint` / `dnsChallenge` / `tlsChallenge`. The CA default is the Let's Encrypt **production** directory (`acme.caServer` defaults to `https://acme-v02.api.letsencrypt.org/directory`), `acme.keyType` defaults to `RSA4096`, renewal is automatic (90-day certificates, renewed 30 days before expiry), and the HTTP-01 challenge's entrypoint "must be reachable by Let's Encrypt through port 80" [S15]. The chart's own ACME example puts the storage on `/data/acme.json`, i.e. behind `persistence` [S17] (VALUES.md: `persistence.enabled: false`, `path: /data`, `accessMode: ReadWriteOnce`, `size: 128Mi`) [S8].

**What cert-manager needs from Traefik.** cert-manager's HTTP01 solver creates throwaway Ingresses. `solvers[].http01.ingress.ingressClassName` is "the recommended way of configuring the Ingress controller" (field added in cert-manager 1.12); the older `class` field instead sets the annotation `kubernetes.io/ingress.class`; and if neither is set, cert-manager "will not set the ingress class on these resources, meaning **all ingress controllers installed in your cluster will serve traffic for the challenge solver**, potentially incurring additional cost" [S16].

**The bug that this decision fixes.** `scripts/automation/components/install_routing.sh` currently applies both ClusterIssuers with `solvers: [{http01: {ingress: {class: nginx}}}]`. Traefik's default `providers.kubernetesIngress.ingressClass: ""` means it processes resources "missing the annotation, having an empty value, or the value `traefik`" [S18] — so a solver Ingress annotated `kubernetes.io/ingress.class: nginx` is **not** picked up by Traefik, the challenge gets no router, and issuance fails. That is a concrete, citable reason why the issuer's solver must change in the same change that installs Traefik; it is not a cosmetic edit. (The staging issuer has the same problem; if the staging CA is what the lane validates against, it is the lane's first TLS failure.)

**Option A — keep cert-manager, retarget the solver (recommended).** cert-manager stays in `group: routing`, the two ClusterIssuers stay applied by `install_routing.sh`, and their solver becomes `http01.ingress.ingressClassName: <traefik class>`. Reasons:
- cert-manager is load-bearing outside routing already: `cert-manager.io/cluster-issuer: letsencrypt` is referenced by `tekton_ci/triggers/templates/{github,gitlab}/template-event-listener-ingress.yml` (line 10) and by `scripts/utilities/kubernetes/create_ingress_yml.sh` (line 34, `CLUSTER_ISSUER_NAME`). Removing cert-manager breaks those two paths, not just TLS.
- It keeps one ACME account and one certificate per SAN set, which is what avoids renewal races.
- It costs nothing extra on a single-node cluster: no PVC, no `acme.json` file ownership, no initContainer.
- The diff is small and reversible: two YAML keys.

**Option B — Traefik's `certResolver`, cert-manager leaves routing.** kubementat deletes the ClusterIssuers and cert-manager, and sets `certificatesResolvers.<name>.acme.httpChallenge.entryPoint: web` in the values template, with routes pointing at the resolver (`tls.certResolver` on an `IngressRoute`, which is the CRD path). **[Unverified]** the equivalent annotation on a plain `networking.k8s.io/Ingress` — `traefik.ingress.kubernetes.io/router.tls.certresolver` is what I would expect and it was not present in any page I fetched; confirm before writing it into a template. Costs: `acme.json` needs a volume on the node (see §6 for the hostPath/local-path angle), the staging/production split becomes two resolvers instead of two issuers, every component that today annotates an Ingress with `cert-manager.io/cluster-issuer` needs its annotation changed, and any non-HTTP01 use of cert-manager (Vault/OIDC-style issuers, `Certificate` resources owned by other charts) has nowhere to live.

**Recommendation: A.** cert-manager keeps its place and its group; Traefik keeps its place as the class those issuers solve through. Option B is the smaller install and the smaller RBAC surface, and it is the right answer if the owner wants `group: routing` to be one release — but it spends a much wider blast radius to get there, and it is the owner's decision, not this document's. **One thing A does still owe:** the issuer's solver class must be *configurable*, not hardcoded, or a cluster that comes up with a class named something else silently loses certificate issuance. `CLUSTER_MANAGER_EMAIL` is already read from `static.json`; the class should follow the same pattern (§7, ticket T-3).

---

## 5. Migration table: what each nginx-ingress assumption becomes

Two mechanisms, and they are not interchangeable:
1. **The standard `kubernetesIngress` provider plus Traefik `Middleware` CRDs.** What an `Ingress` can express without any nginx-compat layer: `spec.ingressClassName`, `spec.tls.secretName`, entrypoint selection and middleware references via `traefik.ingress.kubernetes.io/router.entrypoints` / `.../router.middlewares` / `.../router.tls.options` [S18][S19], and TLS through cert-manager annotations.
2. **Traefik's `providers.kubernetesIngressNGINX`**, a Kubernetes Ingress controller that "also supports many of the ingress-nginx annotations on Ingresses, enabling teams to migrate from NGINX Ingress Controller to Traefik with minimal configuration changes", requiring Traefik v3.6.2+ [S19][S20]. It defaults to `enabled: false` and `allowSnippetAnnotations: false` [S21], and it warns: "This provider discovers all Ingresses in the cluster by default, which may lead to duplicated routers if you are also using the standard Kubernetes Ingress provider" [S22].

**[Unverified]** whether that provider is an OSS feature or licensed. The docs page sits in the Traefik docs tree with no license note, while the chart's `providers.kubernetesIngressNGINX.modsec.*` says "Requires Traefik Hub >= v3.20.0-ea.8" [S21]. Confirm before relying on it — the answer decides whether kubementat's migration is "delete the annotations" or "keep them and turn on a provider".

| Today (kubementat's tree) | On Traefik | Needs the CRD path? |
|---|---|---|
| `ingressClassName: nginx` (tekton templates) | `ingressClassName: traefik` (the class the chart creates: `ingressClass.enabled: true`) [S8] | No |
| `kubernetes.io/ingress.class: nginx` (annotation form) | Prefer `spec.ingressClassName`. Traefik reads the annotation too — `providers.kubernetesIngress.ingressClass` is "the IngressClass resource name **or** the `kubernetes.io/ingress.class` annotation value" [S18] | No |
| `nginx.ingress.kubernetes.io/ssl-redirect: "true"` (`create_ingress_yml.sh:37`, `force-ssl-redirect: 'true'` in the tekton templates) | Cluster-wide entrypoint redirection (`ports.web.http.redirections.entryPoint.to: websecure`, `scheme: https`, `permanent: true`) [S8]; the nginx-compat provider additionally honours both annotations per-Ingress [S23] | No (global) / No (annotation, if provider on) |
| `nginx.ingress.kubernetes.io/proxy-body-size: "0"` (tekton templates, examples) | `providers.kubernetesIngressNGINX.proxyBodySize` (bytes, `1m` → `1048576`) [S23]; otherwise a `buffering`/`bufferRq` Middleware attached by annotation [S18] | No if the nginx provider is on; **Yes** otherwise (Middleware CRD) |
| `nginx.ingress.kubernetes.io/rewrite-target` | `stripPrefix` / `replacePath` / `replacePathRegex` middlewares [S24], or the nginx provider's `rewrite-target` handling [S24] | **Yes** — no plain-Ingress annotation expresses a path rewrite |
| `configuration-snippet` / `server-snippet` | Only through the nginx-compat provider, with `allowSnippetAnnotations: true`, and only the directives it lists (`add_header`, `proxy_set_header`, `rewrite`, `if`, `return`, …) [S24] | **Yes** — the standard provider has no snippet mechanism at all |
| `whitelist-source-range` / `denylist-source-range` | `ipAllowList` middleware [S24]; the nginx provider maps `whitelist-source-range`/`allowlist-source-range` directly [S24]. Note `denylist-source-range` is on the unsupported list [S25] | No via nginx provider; **Yes** (Middleware CRD) with the standard provider |
| TLS secret handling (`spec.tls.secretName` + `cert-manager.io/cluster-issuer`) | Unchanged: `spec.tls` with a secret is still how an Ingress declares TLS, and cert-manager still writes it (§4 option A). Traefik's `tlsOptions` / `tlsStore` CRDs govern *options*, not secrets [S8] | No (secrets) / Yes (TLSOption, TLSStore) |
| cert-manager solver `http01.ingress.class: nginx` | `http01.ingress.ingressClassName: traefik` [S16] | No |

**What must go through a CRD, stated plainly:** path rewriting, request/response buffering limits if the nginx provider is off, IP allow/deny lists if the nginx provider is off, any TLS option or TLS store, and any `IngressRoute`/`Middleware`/`TraefikService` behaviour the standard Ingress annotations cannot express. Everything in kubementat's actual tree today — one class name, `ssl-redirect`/`force-ssl-redirect`, `proxy-body-size: "0"`, TLS secret + issuer annotation — is expressible **without** touching a single CRD, which is why the plan's first tickets can be annotation edits.

**The dual-controller caveat, from upstream.** Running Traefik alongside the old controller on the same class makes both write `status.loadBalancer.ingress[]` "in a tight reconciliation loop, with no error reported in the logs", which breaks ExternalDNS, monitoring, GitOps drift detection and any operator that reconciles on Ingress status [S23]. Upstream's own mitigation is to disable status publishing on Traefik during the coexistence window (`providers.kubernetesIngress.publishedService.enabled: false`) [S23][S8] — which is also exactly what a cluster that has *two* controllers needs, and is a reason to prefer §3 option A over B.


---

## 6. Gotchas that matter to a single-node cluster with hostPath storage

1. **Helm does not upgrade CRDs, and `helm uninstall` leaves them.** `crds/` is applied on install only; upstream's own upgrade recipe is `helm show crds traefik/traefik | kubectl apply --server-side --force-conflicts -f -` before `helm upgrade` [S7]. A `kmt uninstall` that deletes the release and the namespace leaves the `*Traefik*` CRDs, and every object stored under them, behind. See ticket T-6 for how the sweep says so.
2. **`kubeVersion: >=1.30.0-0` is a gate, not a comment.** Chart 42.0.0 raised it [S5]. On a k3s older than 1.30, `helmfile apply` fails with a version-constraint error rather than installing the wrong thing — the good outcome — but a fleet that includes Pi nodes on an old k3s needs chart 41.x pinned, and kubementat pins nothing today except `grafana: "9.0.0"` [S26]. **[Unverified]** what `curl -sfL https://get.k3s.io` resolves to in the lane today: the lane's own `k3s --version` log line in `tests/e2e/stages/01-provision.sh` answers it, and the k3s releases named in the June-2026 note (the v1.33/v1.34/v1.35/v1.36 lines) [S13] are all well above 1.30.
3. **The k3s bundled release is upstream chart 41.6.1 / Traefik 3.7.15, installed as two `HelmChart` CRs.** `k3s-io/k3s@master` ships `traefik-crd-41.6.101+up41.6.1.tgz` **and** `traefik-41.6.101+up41.6.1.tgz`, with `image.tag: "3.7.15"`, `providers.kubernetesIngress.publishedService.enabled: true`, `priorityClassName: system-cluster-critical`, control-plane tolerations, and `service.spec.ipFamilyPolicy: PreferDualStack`; it does **not** set `service.spec.type`, so it gets the chart's `LoadBalancer` default [S27]. Two consequences: (a) kubementat's Traefik is a minor bump over what k3s already ships rather than a foreign object — the lane's `helm_baseline_releases` will hold a release named `traefik` in `kube-system`, and kubementat's own is a second `traefik` release in another namespace, exactly the duplicate the idempotency snapshot already excludes (`IDEMPOTENCY_EXCLUDED_NAMESPACES`) [S28]; (b) the class name is release-derived (`ingressClass.name | default traefik.fullname`, fullname being the release name when it contains the chart name) [S29], so a release named `traefik` creates an `IngressClass` named `traefik` — the same name k3s's own controller gives. **Pin `ingressClass.name` explicitly in the values template** instead of letting the release name decide.
4. **The controller name is `traefik.io/ingress-controller`.** The chart's IngressClass is `spec.controller: traefik.io/ingress-controller` [S29]. Any sweep keyed on the controller — and `_delete_ingress_classes_bound_to` is keyed on exactly that field, with `"k8s.io/ingress-nginx"` for the old chart (`automation.py:1396`) — must use Traefik's real controller string, not `traefik.io/traefik`.
5. **The `service.type` rename is a silent-failure trap.** k3s warns that with chart v40+ the old `service.type` key "is silently ignored, and the Traefik Service falls back to LoadBalancer" [S13]; chart 42 keeps `service.spec.type` [S8]. A template written the old way produces a `LoadBalancer` Service that pends forever with no error anywhere. **Never write `service.type` in kubementat's values template.**
6. **`LoadBalancer` is not a no-op on k3s; it is a `hostPort` request.** Per §3, ServiceLB allocates the Service port on the node through `hostPort` [S11], so kubementat's ingress Service either wins 80/443 first (option A) or never schedules (options B/C). Never set `ports.*.hostPort` in the template: it competes with k3s's `svclb` directly and, on a k3s that kept its own Traefik, cannot win [S8][S11].
7. **No storage-class literal in the values template.** The lane greps the tree for the word `hostpath` under `no-hardcoded-storage-class` in `FORBIDDEN_PATTERNS` (`tests/e2e/harness/config.py`) [S28], so naming a StorageClass by literal is a lane failure, not just a smell. kubementat already has the pattern (`TEKTON_KUBERNETES_STORAGE_CLASS: local-path`). Since `persistence.storageClass: nil` means "use the cluster default" [S8], the cleanest template asks for **no** volume at all — which is what §4 option A buys.
8. **`persistence` + ACME is a second moving part on hostPath storage.** Traefik's ACME storage is a file whose path is `Required` static config [S15]; the chart's example mounts it under `/data` via `persistence` [S17][S8], and `deployment.initContainers` is documented with "e.g. for setting file permission as shown below" [S8] because the file's mode matters. **[Unverified]** the exact mode Traefik requires (commonly `0600`) and whether `local-path` delivers a writable, restart-stable volume with that ownership — check the ACME resolver reference and k3s's storage docs before writing that template.
9. **A `REPLACEME` token is the wrong shape for anything Traefik needs at apply time.** `templates/environment/static.json.template` is filled from environment variables (`_write_environment_static_json`, `automation.py:271-290`) and unreplaced `REPLACEME_*` literals ship to the cluster as strings; a component values template, by contrast, is plain YAML where what you write is what helm sees (`routing/cert_manager.encrypted.yaml.template` is exactly that shape: a comment plus `installCRDs: true`) [S30]. So the scalar knobs (class name, service type, namespace) belong in `static.json` and reach helmfile through the `env "..." | default` pattern, while domains and the ACME email should ride on `BASE_DOMAIN` / `CLUSTER_MANAGER_EMAIL`, which already exist.
10. **Dashboard and metrics want to stay unexposed.** `ports.traefik.expose.default: false` and `ports.metrics.expose.default: false` are chart defaults carrying an explicit "SHOULD NOT expose" comment [S8]. Keeping them unexposed is also what avoids node-port collisions with `svclb`. And if k3s's own Traefik stays, its pods already advertise `prometheus.io/port: "8082", prometheus.io/scrape: "true"` [S27], so kubementat's Prometheus will scrape both controllers unless the template says otherwise.

---

## 7. The plan, for this repository, file by file

Everything below assumes §3 option A (k3s's bundled Traefik disabled in the lane) and §4 option A (cert-manager stays). If the owner picks otherwise, the files that change are named at the end of each step.

### 7.1 The helmfile release block

`templates/environment/kubementat_components/helmfile.yaml.gotmpl` — one release, in the `routing` group, shaped exactly like the cert-manager block above it:

```yaml
# Routing: the ingress controller. nginx-ingress left on 2026-10-28; Traefik is the
# member that took the routing role. The class it installs is named in static.json,
# not here, and the Service type is what the cluster's own load balancer can honour.
- name: traefik
  chart: traefik/traefik
  # version: "42.1.3"
  namespace: {{ env "TRAEFIK_DEPLOYMENT_NAMESPACE" | default "traefik" }}
  labels:
    component: routing
    component_name: traefik
    group: routing
  values:
    - traefik/values.encrypted.yaml
    - ingressClass:
        name: {{ env "TRAEFIK_INGRESS_CLASS" | default "traefik" | quote }}
```

And the repository entry, replacing the comment that currently says kubementat ships no ingress controller:

```yaml
- name: traefik
  url: https://traefik.github.io/charts
```

Notes that follow from the house rules and from §1/§6:

- **The namespace comes from `env`, with the default in `constants.py`** — the same sentence the file's own header comment makes true [S30]. `default` is what keeps `helmfile apply` working when the key is absent.
- **Labels.** `component: routing` + `group: routing` is what makes `kmt helmfile-apply <env> --helmfile-label-filter group=routing` apply it, which is what `install_routing.sh` already runs and what `kmt install` runs through the routing group.
- **The version stays commented.** Every other release in the file comments its `# version:` out except grafana. A pin is what §6.2's `kubeVersion` gate interacts with, so pinning is a decision (7.8); without a pin helmfile takes chart 42.1.3 today and whatever is newest tomorrow [S1].
- **The `ingressClass.name` entry is inline because it is config**, following the grafana `adminUser` precedent in the same file [S30]. If the owner does not want a config key for the class, delete the inline entry and set `ingressClass.name` in the values template instead — but then it is not renameable per environment.
- **The CRD caveat is not solved by this block.** Helm applies `crds/` once and never again [S7]; ticket T-6 decides what kubementat says about that.

### 7.2 Config keys

`templates/environment/static.json.template` (kept alphabetical, as the file is today):

```json
  "TRAEFIK_DEPLOYMENT_NAMESPACE": "traefik",
  "TRAEFIK_INGRESS_CLASS": "traefik",
  "TRAEFIK_SERVICE_SELECTOR": "app.kubernetes.io/name=traefik,app.kubernetes.io/instance=traefik",
```

`src/kubementat/constants.py`, in `DEFAULT_NAMESPACES` and `DEFAULT_SERVICE_SELECTORS`:

```python
    "traefik": "traefik",
    ...
    "traefik": "app.kubernetes.io/name=traefik,app.kubernetes.io/instance=traefik",
```

- The selector shape is verified against the chart's own label helpers: `app.kubernetes.io/name: <chart name>` plus `app.kubernetes.io/instance: <release>` [S32]. It only matches when the release is named `traefik`; a renamed release is why the key exists.
- `TRAEFIK_INGRESS_CLASS` is not decoration: it is read by `install_routing.sh`'s solver (§7.4) and by `create_ingress_yml.sh` (§7.5). A key that nothing reads is exactly what `config.py:416` is written to prevent, so it goes in with a reader in the same ticket.
- The `constants.py` module docstring currently states that "An ingress controller is not a component here" [S33]. That sentence becomes false on this commit and has to be rewritten to say the opposite, with the reason and the date. **Not** adding a compatibility alias for the old `NGINX_INGRESS_*` names: plan §1.7a forbids aliases for renamed commands, and the same logic applies to renamed keys.

### 7.3 The values template

New file `templates/environment/kubementat_components/traefik/values.encrypted.yaml.template`, copied by the installer to `platform_config/{env}/kubementat_components/traefik/values.encrypted.yaml` (the same mapping `routing/cert_manager.encrypted.yaml.template` follows). What belongs in it, and what deliberately does not:

```yaml
# see: https://github.com/traefik/traefik-helm-chart/blob/master/traefik/values.yaml
# What is *not* here is decided elsewhere: the namespace is helmfile's `env ... | default`,
# the class name is helmfile's inline entry, and neither is duplicated in this file.

deployment:
  kind: Deployment            # upstream default; equivalent to DaemonSet on one node (§2)
  replicas: 1

resources:                    # the chart ships none (§2); kubementat sets a floor
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    memory: 256Mi

ports:
  traefik:                    # the API/dashboard entrypoint: never exposed (§2)
    expose:
      default: false
  metrics:
    expose:
      default: false
  web:
    port: 8000
    exposedPort: 80
    http:
      redirections:
        entryPoint:           # this replaces nginx's ssl-redirect/force-ssl-redirect (§5)
          to: websecure
          scheme: https
          permanent: true
  websecure:
    port: 8443
    exposedPort: 443

service:
  enabled: true
  spec:
    type: LoadBalancer        # never `service.type`: the old key is silently ignored (§6.5)

ingressClass:
  enabled: true
  isDefaultClass: false       # a default class is a cluster-wide claim (§1); owner decision

providers:
  kubernetesIngress:
    enabled: true
    publishedService:
      enabled: true           # writes Ingress status from kubementat's own Service
  kubernetesCRD:
    enabled: true
  kubernetesIngressNGINX:
    enabled: false            # no nginx annotations survive here (§5)

logs:
  general:
    level: INFO

accessLog:
  enabled: false              # kubementat ships no log sink for it

persistence:
  enabled: false              # no ACME in Traefik under §4 option A (§6.8)

hub:
  enabled: false              # a licence token is not kubementat's to ship
```

What deliberately stays out:
- **Any StorageClass literal** (§6.7), **any `hostPort`** (§6.6), **any `dashboard`/`api.insecure`** (§2), **any ACME resolver** (§4), **the image reference** (the chart pins `appVersion` and kubementat mirrors nothing for Traefik — mirroring it is a separate decision, and `image.registry` is where a mirror would go [S8]).
- **No `REPLACEME_*` tokens in this file.** A values template is not a `static.json` template: nothing rewrites it (§6.9).

### 7.4 `scripts/automation/components/install_routing.sh`

Two edits, plus the header comment that currently says "it does not name a replacement":

1. Both ClusterIssuers: `solvers: [{http01: {ingress: {class: nginx}}}]` becomes `solvers: [{http01: {ingress: {ingressClassName: <class>}}}]`, where `<class>` is read from `static.json` the same way `CLUSTER_MANAGER_EMAIL` is — a new `jq -r '.TRAEFIK_INGRESS_CLASS // "traefik"'` line. cert-manager keeps `cert-manager.io/v1` and both issuer names, because `tekton_ci/triggers/templates/*/template-event-listener-ingress.yml:10` and `create_ingress_yml.sh:34` already reference `letsencrypt` by name.
2. `./helmfile_apply.sh "${ENVIRONMENT}" 'group=routing' "true"` is unchanged — the group still resolves, now to cert-manager **and** traefik. Ordering is not a problem: `helmDefaults.wait: true` means Traefik is Ready before the issuers are applied, so the first HTTP-01 solve has something listening [S30].

If the owner picks §4 option B, this file loses both heredocs and gains nothing; cert-manager's helmfile release is deleted; and `CERT_MANAGER_DEPLOYMENT_NAMESPACE` becomes a key `kmt uninstall` still sweeps but nothing installs.

### 7.5 The annotation edits

- `scripts/utilities/kubernetes/create_ingress_yml.sh`: `INGRESS_CLASS="nginx"` → read `TRAEFIK_INGRESS_CLASS` from the environment's `static.json` with `traefik` as the fallback; emit `spec.ingressClassName: <class>` instead of the `kubernetes.io/ingress.class` annotation (both forms work on Traefik, but the annotation is the one being retired [S18]); drop `nginx.ingress.kubernetes.io/ssl-redirect` because the entrypoint redirect in §7.3 is the cluster-wide answer (§5); keep `cert-manager.io/cluster-issuer` and the `spec.tls` block untouched.
- `tekton_ci/triggers/templates/github/template-event-listener-ingress.yml` and `tekton_ci/triggers/templates/gitlab/template-event-listener-ingress.yml`: `ingressClassName: nginx` → `traefik`; delete `nginx.ingress.kubernetes.io/force-ssl-redirect: 'true'`; delete `nginx.ingress.kubernetes.io/proxy-body-size: "0"` (in nginx `"0"` means unlimited, and Traefik's standard provider has no body-size knob — `proxy-body-size` only means something to the nginx-compat provider [S23]). **[Unverified]** whether the standard provider imposes any body limit of its own: confirm against the `buffering` middleware reference before concluding "no limit".
- Both files keep `cert-manager.io/cluster-issuer: letsencrypt`.

### 7.6 `kmt install`: always on, or opt-in

**Recommendation: always installed with the routing group, no flag, no `when:` gate.** Reasons:
- `kmt install` already runs `group=routing` as part of install, so "always" is the cheapest change and needs no new surface in `kmt` at all.
- The alternative is a cluster with no ingress controller at all: every existing path (`create_ingress_yml.sh`, the tekton EventListener ingresses, every `Ingress` in `platform_config`) needs a class. That is the state `81ad160` left the product in, and the honest description of this change is that it closes that hole.
- An opt-in needs a gate, and kubementat has no precedent for gating a helmfile release. `helmfile` supports `installed: false` conditions, but wiring one through `kmt install` means a new config key, a new code path, and a mode that ships untested — the opposite of "new behaviour must be reachable through `kmt`, not only a library call".
- A flag named after a component kubementat installs anyway is what the Linkerd decision in `constants.py` ruled out [S33]; the same reasoning applies here.
- What opt-in *would* buy: clusters that already run a controller nobody wants to displace. That is a §3 option B problem, and it is answered by `service.spec.type` + `isDefaultClass: false`, not by not installing.

**Owner owes a yes/no here**, because it changes `kmt install` behaviour for everyone and because "always installed" contradicts nothing in the repo *except* the sentence in `constants.py` that says no ingress controller is a component.

### 7.7 The uninstall sweep

The shape is `uninstall_nginx_ingress()` reversed, and the pieces already exist:

1. **The normal component sweep does the release and the namespace.** Adding `"traefik": "traefik"` to `DEFAULT_NAMESPACES` puts it in the set `kmt uninstall` deletes (`constants.py`'s own comment says this dictionary *is* that set) [S33]. No new function is needed for that, and the release is uninstalled by the read-the-cluster path (`_uninstall_helm_releases`), not by a name list.
2. **The IngressClass goes with the release, not with a sweep.** kubementat's release creates the class, so `helm delete` removes it [S29]. Contrast with nginx, where the class had to be found by controller because nothing named it any more.
3. **The nginx sweep stays exactly as it is.** `NGINX_INGRESS_CONTROLLER_PREFIX = "k8s.io/ingress-nginx"` keeps sweeping the old controller's classes; it must not be widened to match Traefik's `traefik.io/ingress-controller`, which would delete the class kubementat just installed (§6.4).
4. **A new step is owed for what helm leaves: the CRDs.** Helm's own upgrade rule (`crds/` never updated) and helm's uninstall behaviour together mean a `kmt uninstall` leaves `*.traefik.io` objects in the cluster that nothing in kubementat can name. The CRD sweep must be shaped like `_delete_ingress_classes_bound_to` — read the cluster, delete what is ours, never fail on a cluster too old to answer — but keyed on the CRD group, **and the group name is [Unverified]** until checked against `kubectl api-resources` on a Traefik-3 cluster / the v3 migration notes (§1.2). A wrong guess deletes nothing and says it deleted something.
5. **What the sweep must *not* delete:** k3s's own `traefik` release and its `kube-system` namespace (not kubementat's), and any IngressClass whose controller is not `traefik.io/ingress-controller` — the code already refuses classes bound to somebody else's controller [S34].

### 7.8 The lane

| Where | Change |
|---|---|
| `tests/e2e/stages/01-provision.sh` (the k3s block that already writes `write-kubeconfig-mode`) | Write `disable: [traefik]` into `/etc/rancher/k3s/config.yaml` next to `write-kubeconfig-mode`, before the restart that already exists. This is §3 option A and it is the whole point of the lane change: without it, the node ports stay taken by k3s's `svclb` [S11][S12]. Do **not** use a `.skip` file — it does not remove what is already installed [S12]. |
| `tests/e2e/harness/config.py` (`IDEMPOTENCY_EXCLUDED_NAMESPACES`) | No change: `kube-system` exclusion is what already hides k3s's own helm churn. |
| `tests/e2e/harness/config.py` (lanes) | `FULL` already installs `group=("standard","routing")`, so Traefik is exercised without a new lane; `SMOKE` still does not touch routing. Adding routing to `SMOKE` is a lane-budget decision (`02-install: 600`), not a product one. |
| `tests/e2e/harness/config.py` (new feature) | A `routing_ingress` feature id in `FULL.features`, asserting something concrete: that the `IngressClass` kubementat created is the only one that claims Traefik's controller, that the `traefik` Service has endpoints, and that an HTTP request on the class's address reaches a test backend over both 80 and 443 with the 80→443 redirect honoured. |
| `tests/e2e/harness/asserts.py` | No new assert *helper* is needed for the above; `resource_sets`/`diff_sets` and the namespace helpers cover the "only one controller appeared" claim. |
| Plan §1.4c | The recorded lane collision becomes resolved rather than tolerated; the row should be re-worded to say which option resolved it. |
| `tests/e2e/harness/stages.py` (the `helm_baseline_releases` read in `stage_provision`) | Keep taking `helm_baseline_releases` after k3s's charts land, but the comment that explains why must now mention that under option A k3s's `traefik` release is deliberately absent. |

### 7.9 Ordered tickets

| # | File touched | What | Depends on |
|---|---|---|---|
| T-1 | `templates/environment/kubementat_components/helmfile.yaml.gotmpl` | `traefik` repo entry + the release block of §7.1; delete the "no ingress-controller repository" comment | — |
| T-2 | `templates/environment/kubementat_components/traefik/values.encrypted.yaml.template` (new) | The values of §7.3, and only that | — |
| T-3 | `templates/environment/static.json.template`, `src/kubementat/constants.py` | The three keys, the two defaults, and the rewrite of the "an ingress controller is not a component" paragraph | — |
| T-4 | `tests/test_component_namespaces_come_from_configuration.py`, `tests/test_uninstall_sweeps_everything_kubementat_owns.py`, `tests/test_a_helm_component_is_uninstalled_from_the_namespace_the_config_names.py` | The namespace/key lists that these already assert against | T-3 |
| T-5 | `scripts/automation/components/install_routing.sh` | Both ClusterIssuer solvers → `ingressClassName`, read from config | T-1 (the class must exist before the issuers solve) |
| T-6 | `src/kubementat/automation.py`, `src/kubementat/constants.py` | The CRD-side residue step described in §7.7.4 — and nothing else | the [Unverified] CRD group |
| T-7 | `scripts/utilities/kubernetes/create_ingress_yml.sh`, `tekton_ci/triggers/templates/github/template-event-listener-ingress.yml`, `tekton_ci/triggers/templates/gitlab/template-event-listener-ingress.yml` | The annotation set of §7.5 | T-1, T-5 |
| T-8 | `tests/e2e/stages/01-provision.sh`, `tests/e2e/harness/config.py`, `tests/e2e/harness/stages.py` | Disable k3s's Traefik, add the `routing_ingress` feature, fix the baseline comment | T-1..T-7 |
| T-9 | `docs/README_PLATFORM_CONFIG.md`, `CHANGELOG.md`, `TODOs.md` (and the `docs/plans/**` row that tracks FIN-17) | Say what landed, what the lane proves, and what is still owed | T-8 |

Two things deliberately not in the ticket list: **examples** (`examples/helm_charts/nginx-example/**` is the demonstration of "how to ship your own chart", and its ingress is nginx-shaped — retarget it, rename it, or leave it as a worked example of the old world; the owner should say which) and **pinned versions** (§6.2's `kubeVersion` gate — pin `42.1.3` in the helmfile block, or leave it commented and accept tracking newest).

---

## 8. Decisions, sorted by whose they are

**Mine, and I have taken them** (the plan above assumes them): cert-manager stays in `group: routing` (§4 A); the ingress controller lands in `group: routing` with `component: routing`; `Deployment` not `DaemonSet`; `LoadBalancer` with the Service type spelled `spec.type`; the dashboard/API/metrics entrypoints not exposed; no ACME in Traefik; no `hostPort`; no `hub`; `isDefaultClass: false`; always-on rather than opt-in; the lane disables k3s's Traefik.

**The owner owes these, and they block tickets**:
1. **§3 A or B** — disable k3s's bundled Traefik in the lane (A) or let both run and give kubementat's Traefik a portless Service (B). A changes what the lane proves. Blocks T-1's `service.spec.type` default and T-8.
2. **§4 A or B** — cert-manager stays, or Traefik's own resolver replaces the ClusterIssuers. Blocks T-5, and decides whether `CERT_MANAGER_DEPLOYMENT_NAMESPACE` keeps a reader.
3. **Always-on or opt-in** (§7.6). Blocks whether `kmt install` changes shape.
4. **`isDefaultClass`** true or false (§7.3). `true` makes kubementat the default class of the cluster; `false` makes kubementat's Traefik one class among possibly several and requires every Ingress to name it.
5. **Pin the chart, or not** (§6.2), and whether to mirror `traefik/traefik` into the platform registry like other images.
6. **What happens to `examples/helm_charts/nginx-example`** (§7.9).
7. Whether `providers.kubernetesIngressNGINX` is in scope at all — as OSS (an annotation-compatible migration path) or out of scope (kubementat deletes nginx annotations outright). **[Unverified]** licensing.

---

## 9. Unverified, with what would close each gap

| Claim | How to check |
|---|---|
| The v2→v3 CRD group rename (`traefik.containo.us` → `traefik.io`) and the full list of renamed kinds | `https://doc.traefik.io/traefik/migration/v2-to-v3/` (the page returned a redirect I did not follow) plus `kubectl api-resources` on a live v3 install |
| The exact authentication flags/CRDs for a dashboard that is ever exposed | Traefik dashboard + rest-authorization docs |
| `TLSOption` field spelling and a defensible `minVersion`/cipher floor | `https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/crd/tls/tlsoption/` |
| Whether `providers.kubernetesIngressNGINX` is OSS or Hub-licensed | The provider's routing-configuration page's own version badge / the chart's `values.schema.json`, or Traefik Labs directly |
| `traefik.ingress.kubernetes.io/router.tls.certresolver` existing as an Ingress annotation | The standard-provider annotations reference |
| Whether the standard provider imposes a request-body limit by default | The `buffering` middleware reference page |
| Whether k3s marks `local-path` as the cluster's default StorageClass (matters only under §4 option B) | k3s storage docs |
| The k3s version and therefore the bundled Traefik/chart the lane gets today | `k3s --version` from a lane run — the dump line in `tests/e2e/stages/01-provision.sh` |
| The Traefik CRD group(s) to sweep in T-6 | `kubectl get crd` filtered by group, on a cluster that has had Traefik installed |

## Sources

All fetched 2026-10-28.

1. `https://traefik.github.io/charts/index.yaml` — the published helm repository index (`traefik`: 357 entries, `traefik-crds`: 24; newest by `created` as listed above).
2. `https://traefik.github.io/charts/traefik/index.yaml` — chart 42.1.3, appVersion v3.7.16, created 2026-10-22.
3. `https://traefik.github.io/charts/traefik-crds/index.yaml` — same repository index, `traefik-crds` entries.
4. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/traefik/Chart.yaml` — `version: 42.1.3`, `appVersion: v3.7.16`, `kubeVersion: '>=1.30.0-0'`.
5. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/traefik/Changelog.md` — 42.0.0 breaking changes (Kubernetes v1.30+), 42.1.0 Gateway API CRD note.
6. `https://github.com/traefik/traefik-helm-chart/tree/master/traefik` — chart contents, including `traefik/crds/`.
7. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/README.md` — v28 boundary, `traefik-crds` "deprecated since v38.0.2", Gateway API CRDs no longer shipped, upgrade recipe with `helm show crds`.
8. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/traefik/VALUES.md` — generated values reference (`ports.*`, `service.spec`, `rbac.*`, `providers.*`, `ingressClass.*`, `ingressRoute.dashboard.enabled: false`, `persistence.*`, `tlsOptions`, `resources: {}`).
9. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/traefik/values.yaml` — `api.dashboard: true`, `api.insecure` unset, `certificatesResolvers: {}`, `tlsOptions: {}`, `hostNetwork: false`.
10. `https://docs.k3s.io/networking/networking-services` — "Traefik Ingress Controller" and "Service Load Balancer" sections.
11. Same page as S10 — "How ServiceLB Works", "Disabling ServiceLB", "Traefik Service Type".
12. `https://docs.k3s.io/installation/packaged-components` — `--disable`, `.skip` files, manifests directory.
13. `https://docs.k3s.io/networking/networking-services` — the `HelmChartConfig` examples and the chart-v40 `service.type` → `service.spec.type` warning.
14. `https://docs.k3s.io/installation/configuration` — configuration file / `config.yaml` mechanics.
15. `https://doc.traefik.io/traefik/reference/install-configuration/tls/certificate-resolvers/acme/` — resolver options table, defaults (`caServer`, `keyType: RSA4096`, `storage: acme.json`), HTTP-01 reachability, renewal policy.
16. `https://cert-manager.io/docs/configuration/acme/http01/` — `ingressClassName` (1.12+) vs `class` (sets `kubernetes.io/ingress.class`), and the "all ingress controllers serve the solver" default.
17. Same Traefik ACME page — the Helm-values example that stores `acme.json` under `/data`.
18. `https://doc.traefik.io/traefik/reference/install-configuration/providers/kubernetes/kubernetes-ingress/` — `providers.kubernetesIngress.ingressClass`, `disableClusterScopeResources`, `ingressEndpoint`, `crossProviderNamespaces`, `strictPrefixMatching`.
19. `https://doc.traefik.io/traefik/migrate/nginx-to-traefik/` — the migration guide: zero-downtime coexistence, ConfigMap→provider mapping, the Ingress-status race, Traefik v3.6.2 requirement, nginx-ingress retirement in March 2026.
20. `https://doc.traefik.io/traefik/reference/install-configuration/providers/kubernetes/kubernetes-ingress-nginx/` — the provider, its options, "duplicated routers if you are also using the standard Kubernetes Ingress provider".
21. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/traefik/VALUES.md` — `providers.kubernetesIngressNGINX.*` (`enabled: false`, `allowSnippetAnnotations`, `proxyBodySize`, `modsec` requiring Hub).
22. Same page as S20.
23. `https://doc.traefik.io/traefik/migrate/nginx-to-traefik/` — ssl-redirect, TLS options, HSTS/headers, and the coexistence status race with its publishedService mitigation.
24. `https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/ingress-nginx/` — the per-annotation tables (routing, buffering, IP allowlist, snippets) and their limitations.
25. Same page — the "Unsupported Annotations" list.
26. `templates/environment/kubementat_components/helmfile.yaml.gotmpl` in this repository, at `44b27fc`.
27. `https://raw.githubusercontent.com/k3s-io/k3s/master/manifests/traefik.yaml` — the two `HelmChart` CRs, chart `traefik-41.6.101+up41.6.1`, `image.tag: 3.7.15`, `publishedService.enabled: true`, no Service type.
28. `tests/e2e/harness/config.py` in this repository, at `44b27fc` (lanes, `IDEMPOTENCY_EXCLUDED_NAMESPACES`, `FORBIDDEN_PATTERNS`).
29. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/traefik/templates/ingressclass.yaml` and `templates/_helpers.tpl` — `spec.controller: traefik.io/ingress-controller`, `ingressClass.name | default traefik.fullname`, `traefik.fullname`, `traefik.labelselector`.
30. `templates/environment/static.json.template`, `templates/environment/kubementat_components/routing/cert_manager.encrypted.yaml.template`, `src/kubementat/automation.py` (`_write_environment_static_json`, `_delete_ingress_classes_bound_to`, `NGINX_INGRESS_CONTROLLER_PREFIX`), `src/kubementat/constants.py`, `src/kubementat/config.py` — this repository, at `44b27fc`.
31. `https://doc.traefik.io/traefik/middlewares/http/buffering/` — **not fetched successfully**; listed here because §7.5 points at it.
32. `https://raw.githubusercontent.com/traefik/traefik-helm-chart/master/traefik/templates/_helpers.tpl` — `traefik.labelselector` = `app.kubernetes.io/name` + `app.kubernetes.io/instance`.
33. `src/kubementat/constants.py` module docstring, this repository, at `44b27fc`.
34. `src/kubementat/automation.py` `_delete_ingress_classes_bound_to`, this repository, at `44b27fc`.
