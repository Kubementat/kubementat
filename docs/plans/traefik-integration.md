# Plan: Traefik in kubementat, with a presence guard and an `--update` escape hatch

The implementation shape for `docs/research/traefik-installation-k9s-research.md`. The research answered
*what* to install; this document is the **specification**: what each deliverable must do, what it must
never do, and how it is proved. It is written so a coding agent can pick up a ticket and implement
without re-deriving the context — that context is §8.

```
  ┌───────────────────────────────────────────────────────────────────────────┐
  │  ONE RULE, AND ONE ESCAPE HATCH                                           │
  │                                                                           │
  │  `kmt routing install` installs Traefik exactly once: when the cluster     │
  │  has none. If one is already there, kubementat keeps its hands off and     │
  │  warns. `--update` is the operator saying "the one you found is the one I   │
  │  want overwritten".                                                       │
  │                                                                           │
  │  `kmt install` is untouched: no routing, no --update, no pass-through.     │
  └───────────────────────────────────────────────────────────────────────────┘
```

**Decisions this spec is written against.**

1. Routing is not part of `kmt install`. It is installed by its own command.
2. The surface is `kmt routing install [--update]` and `kmt routing status`.
3. A foreign Traefik plus `--update` applies anyway, with the second-controller warning.
4. The everyday lane keeps **k3s's bundled Traefik exactly as the cluster has it today**; it applies
   routing through `kmt routing install` and proves the command did not overwrite that controller. The
   install branch is proved by one separately startable lane.
5. The code lives in `src/kubementat/automations/routing/`.

---

## 1. The three things that decide the shape

### 1.1 A "skip" is a helmfile selector, not new machinery

helmfile's selector grammar (verified against `helmfile --help`, v1.7.0): *"Labels can take the form of
foo=bar or foo!=bar. A release must match all labels in a group."* So the decision is expressed as the
one string `HelmUtils.helmfile_apply` already receives:

| decision | label filter |
|---|---|
| install Traefik | `group=routing` |
| skip Traefik | `group=routing,component_name!=traefik` |

Everything else in the routing group — cert-manager — is applied in both cases. An unselected release is
not destroyed: helmfile syncs what it selects. No `installed:` templating, no rewriting the rendered
helmfile, no second release file, no new parameter on `helmfile_apply`.

### 1.2 The check reads the cluster, not a name list

k3s's bundled controller is a real helm release pair (`traefik` and `traefik-crd`) in `kube-system`, and
a controller that came from plain YAML has no release at all. Discovery is therefore three independent
cluster reads, unioned, each tolerant of the cluster not answering:

| probe | read | what it contributes |
|---|---|---|
| 1 | `helm list --all-namespaces --all --output json` | rows whose chart is `traefik-*`: name, namespace, chart+version, status |
| 2 | `kubectl get ingressclass --output json` | classes whose `spec.controller` is `traefik.io/ingress-controller` |
| 3 | pods across all namespaces, namespace/name plus container images | a traefik image, so a controller that created no IngressClass still counts |

The controller string is Traefik's real one (`_helpers.tpl`, research §6.4). It must never be conflated
with the uninstall sweep's `NGINX_INGRESS_CONTROLLER_PREFIX = "k8s.io/ingress-nginx"`, which points the
other way.

### 1.3 `--update` is not "re-apply"

Helm applies `crds/` at install and **never** upgrades them (research §7.7.4), so `--update` is a
different sequence of operations, not the same one with a boolean:

```
   DEFAULT, Traefik present            --update
   ────────────────────────            ────────
   skip the release                    update the traefik chart repository index
   WARN, naming what was found         apply the chart's CRDs server-side, resolving conflicts
   apply everything else               apply the releases, Traefik included
```

The CRD step runs before the release step, and only under `--update`: on a first install the chart's own
`crds/` land with `helm install`, and on a default skip nothing is being changed, which is the point.

---

## 2. The decision table

The *action* depends on what discovery found and on the flag. The *message* depends additionally on whose
Traefik it is — one rule, but it would be dishonest to warn about "an existing installation" without
saying whether kubementat put it there.

| what discovery found | default | `--update` |
|---|---|---|
| nothing | install | install, after the repository update and CRD apply |
| kubementat's own release, in `TRAEFIK_DEPLOYMENT_NAMESPACE` | **skip + WARNING** naming chart, revision and `--update` | upgrade in place, CRDs applied first |
| a Traefik release in another namespace — **the everyday k3s case** | **skip + WARNING** naming namespace, release and class | apply kubementat's release into its own namespace, after saying plainly that this means a **second controller, a second class and a second status writer** |
| a Traefik class or pods but no release (manifest install) | **skip + WARNING** | install, and say that CRDs are applied while the existing controller stays |

Three consequences stated rather than buried in a log level:

* **Config drift is not silently corrected.** Editing `traefik/values.encrypted.yaml` and running the
  command again changes nothing; the warning names `--update`.
* **A namespace rename makes our own release look foreign.** If `TRAEFIK_DEPLOYMENT_NAMESPACE` moves, the
  release is found under the old namespace and classified as not ours, so the routing step has to be run
  once with `--update`. That belongs in the docs, not in a log.
* **Skip is per release, never per group.** cert-manager is always applied. If Traefik's presence ever
  suppressed the whole routing group, the platform loses its issuer silently.

---

## 3. Where the code lands

```
        operator                                   ┌──────────────────────────────┐
        ────────                                   │  kmt install   UNCHANGED     │
          kmt routing install dev                  │  no routing, no --update     │
          kmt routing install dev --update         └──────────────────────────────┘
          kmt routing status dev
                        │
                        ▼
        ┌───────────────────────────────────────────────┐
        │ src/cli/commands/routing.py            NEW     │  the `routing` Click group
        │   kmt routing install <env> [--yes] [--update] │  registered in src/cli/kmt.py
        │   kmt routing status  <env>          read-only │
        └──────────────────┬────────────────────────────┘
                           ▼
        ┌───────────────────────────────────────────────┐
        │ src/kubementat/automations/routing/            │  __init__.py at both
        │   routing.py          install_routing(...)     │  levels, or the package is
        │   traefik_presence.py discover() → Found       │  not installed at all
        └──────────┬──────────────────────┬─────────────┘
                   ▼                      ▼
     HelmUtils.helmfile_apply(...)   constants.py: "traefik", selector,
     unchanged; only the label       TRAEFIK_CHART, TRAEFIK_CONTROLLER_NAME
     filter string differs           static.json.template: three keys
```

`src/kubementat/automations/` exists in the tree and is empty. It needs `__init__.py` and
`automations/routing/__init__.py`: with
`[tool.setuptools.packages.find] include = ["kubementat*"]` over `where = ["src"]`, a subdirectory
without a package marker is not an installed package, and that failure surfaces as an `ImportError` on
someone else's machine rather than here.

| file | change |
|---|---|
| `src/kubementat/automations/__init__.py`, `automations/routing/__init__.py` | new package markers |
| `src/kubementat/automations/routing/traefik_presence.py` | discovery only. Never installs, never writes. |
| `src/kubementat/automations/routing/routing.py` | `install_routing(...)`: decision, apply, CRDs, ClusterIssuers |
| `src/cli/commands/routing.py` | the `kmt routing` group: `install`, `status` |
| `src/cli/kmt.py` | import the module, `cli.add_command(...)`, and update nothing else |
| `src/kubementat/constants.py` | the traefik namespace entry + service selector, `TRAEFIK_CHART`, `TRAEFIK_CONTROLLER_NAME`, and a rewrite of the paragraph that says an ingress controller is not a component |
| `src/kubementat/automation.py` | **uninstall side only**: traefik in the namespace sweep, plus the `*.traefik.io` CRD step. `install_kubementat` is not touched. |
| `templates/environment/kubementat_components/helmfile.yaml.gotmpl` | repository entry for `traefik` + one release block, specified in §6.4 |
| `templates/environment/kubementat_components/traefik/values.encrypted.yaml.template` | new, research §7.3's values |
| `templates/environment/static.json.template` | `TRAEFIK_DEPLOYMENT_NAMESPACE`, `TRAEFIK_INGRESS_CLASS`, `TRAEFIK_SERVICE_SELECTOR` |
| `scripts/automation/components/install_routing.sh` | deleted, not shimmed: it has no caller today (§5.1) |
| `scripts/utilities/kubernetes/create_ingress_yml.sh`, `tekton_ci/triggers/templates/{github,gitlab}/template-event-listener-ingress.yml` | research §7.5's annotation set |

Deliberately **not** touched: `Automation.install_kubementat`, `src/cli/commands/kubementat.py::install`,
and `tests/e2e/stages/01-provision.sh`'s k3s block. Decisions 1 and 4 both say "leave those alone".

---

## 4. The flow

```
   kmt routing install dev              kmt routing install dev --update
              │                                  │
              ▼                                  ▼
     discover(): 3 cluster reads          discover(): the same reads
              │                                  │
        found? ─── none ─────────────┐             ▼
       │   │                         │      decision: found
       │   └── ours ──────────┐      │             │
       │                      │      │      foreign? ── WARNING "second controller,
       ▼                      ▼      │             │       second class, second status
   helmfile apply        skip + WARN │             │       writer" — then continues
   -l group=routing        -l group=routing,       ▼
                          component_name!=traefik  update repo index
                    │                              apply chart CRDs server-side
                    │                                        │
                    └──────────────────┬─────────────────────┘
                                       ▼
                        helmfile apply -l group=routing
                                       ▼
              ClusterIssuers: solvers[0].http01.ingress.ingressClassName: <class>
                                       ▼
              if no Traefik on this cluster serves <class>:
                  WARNING: the solver points at a class nothing serves
                  — that is how certificate issuance fails forever, silently
```

---

## 5. Two findings from reading the code

### 5.1 `kmt install` does not apply the routing group today, and `install_routing.sh` has no caller

`Automation.install_kubementat` applies exactly one group (`src/kubementat/automation.py:542`, the
default is `standard`), and nothing in `src/` or `scripts/` invokes `install_routing.sh` — the only grep
hits are old session logs. So research §7.6's premise ("`kmt install` already runs `group=routing`") is
**false** here, and no product code path applies the ClusterIssuers at all.

Decision 1 keeps that shape on purpose. The consequence: `kmt routing install` is the only product path
that applies `group=routing`, and the only thing that applies the issuers. That is why §7 changes the
lane — a lane that applies routing with a bare apply would bypass the guard, install a second controller
next to k3s's, and then assert something the cluster cannot deliver.

### 5.2 Keeping k3s's controller makes the guard the everyday case, which is what we want

Decision 4 means the everyday lane runs against a cluster that already has Traefik (`kube-system/traefik`
and `kube-system/traefik-crd`, class `traefik`, chart 41.6.x). So in the everyday lane `kmt routing
install` is *supposed* to find a foreign controller and skip: the guard stops being a corner case and
becomes asserted behaviour of every lane run, while the install branch moves to its own lane.

The one thing that must stay correct in that state is the solver. `TRAEFIK_INGRESS_CLASS` defaults to
`traefik`, which is what k3s's release names its class, so the issuers solve through a class that really
exists. §6.2's "nothing serves that class" warning is what catches the day someone renames it.

---

## 6. Specification of each deliverable

### 6.1 `traefik_presence.py`

*Exposes*: one discovery entry point returning a result object, plus a text report.
*Depends on*: `kubementat.shell` for the three reads in §1.2. Nothing else.

Requirements:

- Every read uses `capture=True, check=False`; a cluster that refuses to answer is a **finding**, not an
  exception. A cluster old enough to lack `IngressClass` answers with an error and nothing to report.
- A release counts as Traefik when its chart name is `traefik` or `traefik-<version>`; the release's
  namespace is carried along, because the ours/foreign split is the entire point.
- Classifies as exactly one of: `none`, `ours` (a release in the namespace the configuration names for
  the component), `foreign` (anything else the probes found). Mixed findings are reported together and
  resolved to the most consequential one — `foreign` beats `ours` beats `none`, so a cluster with
  kubementat's release and k3s's warns about the second controller rather than skipping quietly.
- The report names, per finding: namespace, release, chart+version, status, the class(es), and which
  probe found it. It states the classification and the action `install` would take, because that is what
  makes `kmt routing status` worth running before `--update`.
- No side effects. No import of `routing.py` (the dependency runs the other way).

### 6.2 `routing.py::install_routing`

*Signature shape*: environment and team, plus `update=False` and `assume_yes=False`. Returns a result
describing the decision, so the CLI and any later caller both read the same answer.

Order of operations, and why each is where it is:

1. Read the class name and namespace from the configuration (`Config.component_namespace` for the
   namespace, the config key for the class) — never from a literal. Missing key: warn that the default is
   being used, then use it, which is the rule in `config.py`.
2. `discover()`.
3. Decide, per §2's table. Each branch logs at WARNING (except the `none` branch, which logs what it is
   about to install) using wording that survives copy-paste into a bug report: name the namespace, the
   release, the class, and the command that would change the outcome.
4. If a foreign controller is present and `update` is set, say the second-controller sentence. Without
   `--yes`, ask; with `--yes`, log it as accepted rather than silently proceeding.
5. Under `update`: refresh the repository index for the traefik repo, then apply the chart's CRDs with
   `kubectl apply --server-side --force-conflicts`. Fail the command if that read fails while `--update`
   was asked for — proceeding would mean "upgraded" over CRDs that cannot serve the new controller.
6. Apply through `HelmUtils.helmfile_apply` with the label filter from §1.1. Do not shell out to helmfile
   here; that function is what exports the namespace variables, and bypassing it is how a release lands
   in the wrong namespace.
7. Apply both ClusterIssuers with `http01.ingress.ingressClassName` set to the configured class. Both
   issuer names (`letsencrypt`, `letsencrypt-staging`) keep their names: `tekton_ci/triggers/templates/
   {github,gitlab}/template-event-listener-ingress.yml` and `create_ingress_yml.sh` reference them.
8. Verify: list IngressClasses, and warn if nothing serves the class the solver was just pointed at.
   This costs one read and catches the failure mode research §4 describes.

Failure behaviour: an `helmfile apply` that exits non-zero raises with helmfile's output attached, like
every other apply in the codebase. Nothing in this function retries.

### 6.3 The CLI surface

- `kmt routing install <environment>` — flags `--yes/-y` (scripted path; without it and without a
  terminal the command fails closed and names the flag, the contract `kmt uninstall` and `kmt users`
  established) and `--update`. The environment argument takes `ENVIRONMENT` from the environment the way
  every other command does.
- `kmt routing status <environment>` — read-only, prints §6.1's report. No flags beyond environment.
- Registration is one import and one `cli.add_command` in `src/cli/kmt.py`; the group is what the harness
  capability table will call.
- Both commands must be visible in `--help`, self-documenting (a `test_cli_surface.py` check asserts
  that), and absent from `kmt install`'s help.

### 6.4 Config and templates

- The release block declares: the `traefik/traefik` chart from the `traefik` repository, namespace
  `{{ env "TRAEFIK_DEPLOYMENT_NAMESPACE" | default "traefik" }}`, labels `component: routing`,
  `component_name: traefik`, `group: routing`, and values from `traefik/values.encrypted.yaml` plus an
  inline entry that pins the class name from `TRAEFIK_INGRESS_CLASS`. The namespace must be read from the
  environment exactly the way the other releases do, because `tests/test_component_namespaces_come_from_configuration.py`
  asserts that shape with a regex and fails if it deviates.
- `constants.DEFAULT_NAMESPACES` gains `"traefik": "traefik"`. This is load-bearing twice: it is what
  `HelmUtils._export_component_namespaces` iterates to export `TRAEFIK_DEPLOYMENT_NAMESPACE` before
  helmfile runs, and it is the set `kmt uninstall` deletes namespaces from.
- The values template follows research §7.3. Hard rules: `service.spec.type`, never `service.type` (the
  old key is silently ignored and produces a never-scheduling Service); no `hostPort`; no StorageClass
  literal (the lane greps the tree for the word in `FORBIDDEN_PATTERNS`); no dashboard exposure;
  `isDefaultClass` false; no `REPLACEME_*` tokens (a values template is plain YAML, nothing rewrites it).
- The template lives in a **subdirectory** of `templates/environment/kubementat_components/` and ends in
  `.template`: `_write_config_from_templates_for_directory` only copies templates it finds inside a
  subdirectory, so `traefik/values.encrypted.yaml.template` is what `initialize` materialises as
  `platform_config/<env>/kubementat_components/traefik/values.encrypted.yaml`. The `*.template` exclusion
  in `.gitattributes` means the template itself is not encrypted in git; the generated file is.
- `static.json.template` gains the three keys, alphabetically, and each key must have a reader: the
  namespace (helmfile + `Config.component_namespace`), the class (helmfile, the issuer solvers, the
  warning in §6.2.8), and the selector (the tunnel commands' convention).

### 6.5 Uninstall side

- The namespace sweep picks traefik up for free through `DEFAULT_NAMESPACES`.
- The release is uninstalled by the name the cluster reports (`_uninstall_helm_releases`), not a list.
- The IngressClass goes with the release — kubementat's release creates it, so `helm delete` removes it.
  This is the opposite of nginx-ingress, where the class had to be found by controller.
- A new step sweeps what Helm leaves behind: the chart's CRDs, by group, shaped like
  `_delete_ingress_classes_bound_to` (read the cluster, delete what is ours, never fail a cluster that is
  too old to answer). The group name is still unverified in the research (§9) — resolving it is part of
  the ticket, and a wrong guess silently deletes nothing while claiming it deleted something.
- What must not be touched: k3s's `kube-system` releases and namespace, and any class whose controller is
  not Traefik's. The nginx sweep's controller prefix stays as it is.

### 6.6 The harness changes decision 4 forces

Specified in §7.2 below, with the acceptance wording. Three of them are not optional extras: applying the
guard in stage 02 but not in stage 04's rerun, leaving the release-set assert as it is, and comparing
releases by name alone each make the lane prove something false.

---

## 7. Done means

### 7.1 Unit suite (`uv run pytest -q`, no cluster)

Named for behaviour — `tests/test_test_name_conventions.py` fails on ticket ids in names.

| file (new unless noted) | locks down |
|---|---|
| `test_routing_install_skips_traefik_when_the_cluster_already_has_one.py` | a release in `kube-system` → skip, a WARNING naming namespace and chart, selector excludes `component_name=traefik`, cert-manager still in the apply |
| `test_routing_install_applies_traefik_when_the_cluster_has_none.py` | plain `group=routing`, no exclusion, no CRD step |
| `test_routing_install_does_not_touch_a_traefik_it_installed_before.py` | our own release present: excluded, warning names chart, revision, `--update` |
| `test_routing_update_applies_the_crds_before_the_release.py` | with `update=True`, the CRD apply is ordered before the release apply, and a failed CRD apply fails the command |
| `test_routing_update_installs_a_second_controller_only_after_saying_so.py` | foreign + `update=True`: the second-controller sentence is logged, the apply still runs; with `assume_yes=True` it is accepted, not asked |
| `test_routing_presence_reads_the_cluster_rather_than_a_release_name_list.py` | a manifest-installed Traefik (no helm row) is still detected; a cluster that answers nothing is still answered |
| `test_routing_status_reports_what_is_on_the_cluster_and_whose_it_is.py` | namespace, chart, class, ours/foreign, and the action `install` would take |
| `test_routing_install_points_the_solver_at_a_class_something_serves.py` | the warning fires when the configured class is not served, and does not when it is |
| `test_the_routing_values_file_never_writes_a_service_type_or_a_storage_class.py` | the template contains `spec.type` and no StorageClass literal |
| `constants` / namespaces / helmfile-shape tests (extended) | `test_component_namespaces_come_from_configuration.py`, `test_uninstall_sweeps_everything_kubementat_owns.py`, `test_a_helm_component_is_uninstalled_from_the_namespace_the_config_names.py` |
| `test_no_dead_code_ships_in_the_tree.py`, `test_cli_surface.py` (extended) | both new modules are imported by real code, and the group plus its two commands are the expected surface |

### 7.2 The everyday lane (`full`)

k3s keeps its bundled Traefik; `01-provision.sh` is not touched. The routing group stops being applied by
`helm.helmfile-apply` and becomes `kmt routing install`, so the guard sits on the path the lane actually
walks.

**a) Two capability rows and one mapping, used by two stages.** Add `routing.install`
(`kmt routing install {env} --yes`, mutating) and `routing.status` (`kmt routing status {env}`, not
mutating) to `CAPABILITIES` in `tests/e2e/harness/config.py`, and a mapping from helmfile group name to
the capability that applies it, so "how is this group applied" has one answer. A group with no entry
keeps today's behaviour.

- `stage_install` (`stages.py:574-583`) resolves `groups[1:]` through the mapping instead of calling the
  apply capability directly, keeping the `group=<name>` detail prefix in `record_result`.
- `_rerun_capabilities` (`stages.py:808`) must resolve through the same mapping. Today it rebuilds
  `later_groups` as bare applies for every later group — precisely the path that bypasses the guard. Left
  alone, stage 02 skips correctly and stage 04 installs a second controller while the idempotency line
  is comparing a change the lane said would not happen.

Acceptance: nowhere in `tests/e2e/` does anything apply `group=routing` through `helm.helmfile-apply`.

**b) The release-set assert learns about the skip.** `_expect_release_set` (`stages.py:637-651`, expected
names read from the guest copy by `_releases_in_groups`) demands every declared release of the applied
groups be present and `deployed`. Once `helmfile.yaml.gotmpl` declares traefik in `group=routing`, that
assert fails here by construction.

- Rule: *declared **and** applicable*. Subtract a declared release when the cluster already runs that
  chart in a namespace the configuration does not name.
- Decide it from the same read the product decides on (`helm list -A -o json`), never from a constant, so
  a lane against a cluster without Traefik still expects it to appear. A constant would quietly stop
  testing anything the day the controller is installed.
- Say what was subtracted in the detail. `helm-releases-present` is what a reader of `result.json`
  trusts; it must not be able to mean "kubementat installed nothing, fine".

**c) Release identity gets namespaced.** `no-kubementat-releases` (`stages.py:1497-1502`) compares release
*names* across the post-install snapshot, the pre-install baseline and what survives teardown. kubementat's
Traefik is `traefik` in namespace `traefik`; k3s's is `traefik` in `kube-system`. Two objects, one name.

- Compare by `namespace/name`. `Cluster.helm_list` (`tests/e2e/harness/asserts.py:190`) returns
  namespace, release and status but **drops `chart` and `revision`**, which assertion (d) needs. Extend
  it with those two keys and keep the existing ones, so the current callers keep working.
- Do not redefine `helm_baseline_releases`'s type (today: a sorted list of names) and leave a caller
  comparing a `set` of tuples against a `set` of strings. If the baseline needs the richer form, add a
  parallel field.

**d) The guard's own assertions.** Cluster facts, not stdout greps, in stage 02 where routing is applied:

| assert | how it is decided | why it exists |
|---|---|---|
| `routing-status-foreign` | `routing.status` exits 0; its report names a release kubementat does not own, in `kube-system`, class `traefik` | the operator's question, answered by the read-only command |
| `routing-install-skipped-existing-traefik` | exit 0, and `cert-manager` reaches `deployed` | the command may not fail on a cluster it cannot improve |
| `routing-left-k3s-traefik-alone` | exactly one release on the cluster whose chart is `traefik-*` and it is k3s's in `kube-system`; no namespace `traefik`; the only IngressClass bound to Traefik's controller is k3s's | "we did not install another controller", as a fact about the cluster |
| `routing-did-not-upgrade-k3s-traefik` | k3s's release still reports the chart version **and revision** recorded before kubementat ran | the assert that proves nothing was overwritten: `helm upgrade` of a release nobody asked for would otherwise look green — same name, same namespace, status `deployed` |
| `idempotent:routing.install` | stage 04's existing before/after `resource_sets` + `content_digests` comparison | the rerun path is the one that drifts silently |
| feature `routing_guard` | run `routing.install` a second time: skips again, cluster set unchanged | "present" is a stable answer, not a race between two controllers |

`install_groups` stays `("standard", "routing")`, the `standard` asserts hold, and stage 02 replaces a
command it already ran rather than gaining a phase: give `02-install` more of its existing 1200 s, not a
new stage.

### 7.3 The separately startable case (new lane `routing`)

A lane is already the unit this suite starts separately, so add `Lane(name="routing", ...)` with the shape
of `full`, `install_groups=("standard",)`, `features=(..., "routing_ingress", "routing_guard")`, its own
`timeouts`, and register it in `LANES`. It is run with
`uv run python tests/e2e/suite.py --lane routing --scope full` — that is the "one test case we can start
separately", without inventing new stage machinery or changing `smoke` and `full`.

Provisioning stays parameter-free (FR-13: `01-provision.sh` gets no arguments and no environment), so
turning k3s's controller off is a stage step before the first routing command, specified as:

1. Add the disable entry for the bundled Traefik to `/etc/rancher/k3s/config.yaml`, preserving the
   `write-kubeconfig-mode` line `01-provision.sh:99-101` wrote. **Not** a `.skip` file: k3s documents that
   a skip file does not remove what is already installed (research §3), so the cluster would keep both
   controllers and the lane would assert nothing.
2. Restart the service, wait for the node Ready.
3. Wait for k3s's `traefik` release and its `svclb` pods to be gone — via the harness `wait()` helper, not
   a fixed `sleep`. Until k3s finishes uninstalling a disabled addon, ports 80/443 on the node are still
   held and the next step is a race.

Then, in order:

| step | assert |
|---|---|
| `kmt routing install` | release `deployed` in the configured namespace; the IngressClass kubementat created exists and is the only one bound to Traefik's controller; the Service has endpoints; an HTTP request on the node address answers a redirect; TLS terminates from a cert-manager-issued certificate. **The only place the install branch runs.** |
| `kmt routing status` | reports the release as kubementat's own, with the chart version applied |
| `kmt routing install` again | skips with the warning; cluster set unchanged |
| `kmt routing install --update` | the CRD apply is reported by the command; the release is upgraded; HTTP still answers. The CRD line has to be visible, because it is the one thing Helm will not do on its own |
| `kmt uninstall` | namespace gone, no `*Traefik*`-shaped CRDs left. The group name is still unverified (research §9): this lane is where it gets answered, and T-7 changes if it comes back different |

Budget: about a full `02-install` plus the ingress asserts plus the disable-and-wait step, so it needs its
own `timeouts` entries (NFR 6.1).

---

## 8. Context the implementer needs

Read this before writing code for a ticket. All of it was true at the time of writing and most of it is
verifiable by reading one file.

**Repo tooling.**
- Search the codebase with `mcp__codegraph__codegraph_explore` (the project's instruction in `AGENTS.md`).
- `uv sync --all-extras` to install; `uv run pytest -q` is the unit suite and runs without a cluster;
  `uv run python tests/e2e/suite.py --lane full` is the only e2e path and takes ~29 min. Attach its
  `result.json` (it names the git SHA) when reporting e2e work.
- `git-crypt unlock` before touching `platform_config/`. `AGENTS.md` names the same command.

**How kubementat runs things.**
- `src/kubementat/shell.py::run(argv, cwd=, capture=, check=True, retries=0, input=)` is the only way to
  start a process. `argv` must be a list (a string is rejected by design), `check=False` hands the verdict
  back instead of raising, `capture=True` is for output you parse and `False` for output the operator
  should see — helmfile's diff belongs in the second category.
- Cluster reads are `kubectl`/`helm` with `--output json`, `capture=True, check=False`, parsed with
  `json.loads`. The `automation.py` helpers show the house style (`_delete_ingress_classes_bound_to` is
  the closest model: read the cluster, delete what is ours, tolerate a cluster that cannot answer).
- `HelmUtils.helmfile_apply(environment_name, label_filter, interactive)` runs helmfile in
  `platform_config/<env>/kubementat_components` and, before that, exports
  `<COMPONENT>_DEPLOYMENT_NAMESPACE` for **every key in `constants.DEFAULT_NAMESPACES`** and the grafana
  admin credentials (raising if the admin user is missing). Calling it is therefore how a release gets the
  right namespace; shelling out to helmfile instead is how a release lands in the wrong one.
- Configuration: `Config(environment, team)` reads `platform_config/<env>/static.json` plus the encrypted
  file; `component_namespace(component)` resolves `<KEY>_DEPLOYMENT_NAMESPACE` with `constants` as the
  fallback, and `component_config_key` derives the key from the component name. The rule in `config.py`
  and the module docstring in `constants.py` is "config answers, code falls back" — a missing key warns
  and uses the default, it does not crash and does not silently invent a name.

**How the CLI is shaped.**
- Commands live in `src/cli/commands/*.py`, registered one by one in `src/cli/kmt.py::main`. The `users`
  group in `cli/commands/users.py` is the model for a group with subcommands and for the option helpers
  (`_environment_option`, `_yes_option`, `team_option`).
- The confirmation contract is "fail closed with no terminal, name `--yes`". See
  `users._require_confirmation` and `kubementat.uninstall`. Mutating commands get `--yes/-y`; read-only
  ones do not need it.
- `tests/test_cli_surface.py` asserts the exact command set (a set per group) and that every command
  documents itself; a new group needs its expected-command set there or CI fails, not at runtime.
- No aliases and no shims for renamed or replaced commands. `constants.py`'s docstring and the
  `helm.helm_component_uninstall` comment both state this explicitly, and `install_routing.sh` being
  deleted rather than shimmed follows it.

**How a component's configuration reaches the cluster.**
- `initialize` copies templates from `templates/...` into `platform_config/<env>/...`. Templates must be
  inside a subdirectory and end in `.template` to be copied (see §6.4). It refuses to run if the
  environment's static files already exist (`automation.py::_check_is_already_initialized`), which is why
  the lane deletes the marker files before calling it (INV-1).
- A values file's name in the helmfile release must match what `initialize` produced. `cert_manager` is
  the worked example: template `routing/cert_manager.encrypted.yaml.template`, referenced as
  `routing/cert_manager.encrypted.yaml`.
- helmfile evaluates with the process environment, which is why namespaces are `env ... | default` in the
  template and exported by `helmfile_apply` in Python. Both halves of the rule matter: rename the key, the
  release moves; omit the key, the default applies and the apply still works.

**How the e2e suite is shaped.**
- Everything the lane runs is a capability row in `tests/e2e/harness/config.py::CAPABILITIES`, resolved
  through `config.argv_for` and prefixed with `uv run` by `stages.py::kmt_argv`. Stage code never spells
  out a command. Placeholders come from `LANE_PLACEHOLDERS` (`env`, `team`, `workdir`, `account`, …) plus
  per-call kwargs from `STAGE_PLACEHOLDERS` (`group`, `port`, `ns`, `name`, `pod`).
- Assertions: `self.expect(name, passed, detail, cls)` records `assert:<name>`; `record_result` records
  the outcome of a capability run with its stdout/stderr as artifacts; `FeatureSkip` records a skip
  instead of a failure; `self.wait(...)` is the polling helper. Everything lands in `result.json`, which
  is the deliverable.
- Stage 02 applies `install_groups[0]` through `kmt install` and later groups through applies
  (`stages.py:574-583`); stage 04 re-runs capabilities through `_rerun_capabilities`, which currently
  reconstructs later groups as bare applies; stage 05 dispatches `features` to `feature_<name>` methods.
- `Cluster` (`tests/e2e/harness/asserts.py:59`) is how a stage reads the cluster: `helm_list()` (drops
  chart/revision today), `namespaces()`, `cluster_scoped_names(kind)`, `pvc_status()`,
  `node_count_ready()`, `apply()`, `kubectl(...)`, `resource_sets`/`content_digests`/`diff_sets` for the
  idempotency detector.
- The idempotency detector compares name-sets and content digests over `IDEMPOTENCY_KINDS` excluding
  `kube-system`, `kube-public`, `kube-node-lease`. That exclusion is *why* §7.2(d) needs the explicit
  revision assert: a change inside `kube-system` is invisible to that detector, so "nothing was
  overwritten" cannot be proved by it.
- k3s's own releases land after the node is Ready, and `_helm_baseline` (`stages.py:500`) waits for
  `traefik` **and** `traefik-crd` to be `deployed` before taking it. Any assumption that the cluster is
  quiet when stage 02 starts is wrong by design.
- Test file and function names must describe behaviour (`tests/test_test_name_conventions.py`); ticket ids
  belong in a docstring body.

**Facts about Traefik this spec depends on** (all cited in the research document; re-verifying them is
cheap and a wrong one is expensive):
- One chart: `traefik/traefik`. The companion `traefik-crds` chart is deprecated and ships no proxy.
- The chart's IngressClass controller string is `traefik.io/ingress-controller`.
- `service.type` is silently ignored in current chart versions; `service.spec.type` is what works.
- Helm applies `crds/` on install and never upgrades them; `helm uninstall` leaves the CRDs and their
  objects behind.
- k3s ships chart 41.6.x / Traefik 3.7.15 as HelmChart CRs in `kube-system`, and ServiceLB holds node
  ports 80/443 for its `LoadBalancer` Service — a second such Service will not schedule.
- cert-manager's solver field is `http01.ingress.ingressClassName`; the older `class` field sets the
  `kubernetes.io/ingress.class` annotation, which Traefik's default provider does not serve.

---

## 9. Decisions, and what is still open

**Settled:** routing is not in `kmt install`; the surface is `kmt routing install` / `kmt routing
status`; `--update` lives on the first only; foreign + `--update` applies with the warning; the code lives
in `src/kubementat/automations/routing/`; the everyday lane applies routing through `kmt routing install`
and asserts that it left k3s's Traefik untouched, which is also why the install branch needs its own
lane.

**Decided by the owner, 2026-10-29** — these were open when the work landed; the answer is now on the record
and is what T-3 and the follow-up row *Traefik defaults* of
`docs/plans/finish-python-migration-last-lap.md` implement.

1. **`isDefaultClass` — ship `true`.** Traefik is the default controller once it is installed; kubementat is
   opinionated about that. The template's `isDefaultClass: false` and its "an owner who wants it says so per
   environment" comment both go, and the CHANGELOG has to say that a cluster that names no class now resolves
   to kubementat's Traefik.
2. **Pin the chart**, `helmfile.yaml.gotmpl` carrying a real `version:` on the `traefik` release. A commented
   `# version: "42.1.3"` resolves "newest" against the `kubeVersion >=1.30.0-0` floor (research §6.2), which
   is not a pin. The pin is what makes mirroring (3) an actual guarantee rather than a preference.
3. **Mirror `traefik/traefik` and its image** into the platform registry, like every other kubementat image.
4. **The CRD group to sweep on uninstall** — answered from the chart, not from memory: `helm show crds` on
   `traefik/traefik` 42.1.3 names both `traefik.io` and `hub.traefik.io`, and the landed uninstall sweep
   covers both. If the pin in 2 changes the chart version, that answer is re-derived and not remembered.
5. **`examples/helm_charts/nginx-example` — retarget it to Traefik.** Not renamed away, not left as a
   worked example of the old world: an example that still writes nginx annotations teaches the wrong thing.
6. **`providers.kubernetesIngressNGINX`** — the migration deletes the nginx annotations (this plan's
   assumption, and the one already implemented in T-6). Enabling an NGINX-compatibility provider is a
   Traefik Hub question and is out of scope.

## 10. Tickets, ordered

| # | does | depends |
|---|---|---|
| T-1 | `constants.py` + `static.json.template`: keys, namespace, selector, `TRAEFIK_CHART`, `TRAEFIK_CONTROLLER_NAME`, docstring rewrite | owner §9.2 |
| T-2 | `automations/routing/traefik_presence.py` + `kmt routing status`, with the discovery and report tests | T-1 |
| T-3 | `helmfile.yaml.gotmpl` repo + release block, `traefik/values.encrypted.yaml.template` | T-1, owner §9.1–3 |
| T-4 | `automations/routing/routing.py::install_routing` + the `kmt routing` CLI group, delete `install_routing.sh`, the guard tests | T-2, T-3 |
| T-5 | ClusterIssuer solvers moved to `ingressClassName` from config, inside `install_routing`, plus the "nothing serves that class" warning | T-3, T-4 |
| T-6 | annotation sweep: `create_ingress_yml.sh`, both tekton templates | T-3, T-5, owner §9.6 |
| T-7 | uninstall: traefik in the namespace sweep + the CRD sweep (group name to be verified) | T-1, owner §9.4 |
| T-8 | everyday lane: capability rows + group mapping, stage 02 **and** stage 04 routed through it, namespaced release identity, the declared-and-applicable release set, the four guard asserts of §7.2 | T-1..T-5 |
| T-9 | new `routing` lane: k3s disable as a stage step, the install/status/idempotency/`--update`/uninstall asserts | T-8 |
| T-10 | `docs/README_PLATFORM_CONFIG.md`, `CHANGELOG.md`, `TODOs.md`, and the `docs/plans/**` row tracking FIN-17: what landed, what each lane proves, what is still owed | T-9 |
