# Platform configuration

This directory contains configuration for the according environments. **An environment is exactly one
cluster, and kubementat enforces that instead of assuming it.** A sub-directory is an `ENVIRONMENT` (dev,
prod, smoke) and the two `CLUSTER_*` keys are what make the 1:1 true: `CLUSTER_KUBE_CONTEXT` names the
kubeconfig context that reaches this environment's cluster, and `CLUSTER_KUBECONFIG_PATH` names the file that
context lives in. Both sit in `platform_config/<environment>/static.json`, beside `CLUSTER_MANAGER_EMAIL`.
A missing or `null` `CLUSTER_KUBE_CONTEXT` is an error that names the environment and the file, because the
`current-context` fallback is gone: kubementat installs into what the environment declares, not into whatever
`kubectl` was last pointed at.

Because every environment declares its cluster, the rule is checkable. kubementat compares the **server
address** each environment in the root resolves to, and two environments that reach the same address are a
**collision — a hard block, with no override flag**: the command refuses, writes nothing, names both
environments and both addresses, and points at `kmt environments list`, which answers the same question
without contacting a cluster. An environment that cannot be resolved (no key, no file, no such context) does
not block anyone; it is reported by name with the reason, so the check never claims coverage it does not
have. The one exclusion is the honest case, not a loophole: an environment whose address equals the cluster
the command is standing on — kubementat resolving in-cluster, spec §14 — is excluded from the comparison, and
the report says *not verified: <environment> is the cluster this command is running on* rather than saying
*verified* about a comparison that held itself back.

The surface follows the rule. `environment` is the first positional of every command that reads or writes a
cluster, and nothing defaults: no default environment, no `DEFAULT_TEAM`, no ambient kubecontext. A command
that was not told what it means says so before it touches anything.

The same rule covers the root. Every verb that reads or writes a tree — the seven under `kmt config` and
every command that reaches a cluster — names it with `--config-dir`, or with
`$KUBEMENTAT_PLATFORM_CONFIG_DIR`, and the flag sits above the variable. With neither, the command
says so and stops: kubementat reads no configuration out of the directory it was installed from, and
it has no default root to fall back to.

## How a configuration tree is created now

`kmt config init --environment E --config-dir <configuration-root>` lays one down. It writes the
root's `kubementat.yaml` and the environment's
two static files (`<configuration-root>/<environment>/static.json`,
`<configuration-root>/<environment>/static.encrypted.json`), plus a
team's two empty files when `--team` says so — and it writes **answers, not product shape**: no namespace, no
domain, no registry, nothing that a default elsewhere already answers. `--set` / `--encrypted-set` answer a
key into those two files, with the value taken as JSON when it parses, so `--set GRAFANA_ENABLED=false` stores
a boolean and `--set BASE_DOMAIN=example.test` stores a string. Running it twice is a no-op: a path that is
byte-identical prints `skipped`, a path that already differs from what init would write is refused and named,
and `--force` accepts a tree holding material kubementat did not write while touching none of it. The
root is `--config-dir`, or the `$KUBEMENTAT_PLATFORM_CONFIG_DIR` a container already sets, and with
neither the verb refuses before `git init` decides anything. It used to be the other way round for
these seven verbs: the directory was a positional and neither spelling of a root was read, so a machine
that had configured its root once could install against that tree and could not configure it. What a
fresh tree
does not do is address a cluster: `CLUSTER_KUBE_CONTEXT` is written `null` and stays null until someone names
the context in that file, which is the sentence `kmt environments list` repeats. `kmt config init` is the only
verb that prepares a directory, and it writes only the root it was named; `kmt initialize`, which wrote the
checkout it was run in, is deleted.

These directories contain another layer of grouping which we call TEAMS for separating team namespaces. Under
the team directories you will also find a sub-directory for each application that the team is deploying. Those
are used for storing application specific settings. A team is a directory inside an environment, and a team
is not a cluster: `kmt routing install <environment>` and the component and grafana commands read their
namespace and their class from the environment's `static.json` and take no team at all, because there is
nothing in a team's configuration for them to ask.

Here is an example structure for the configuration of the nginx-example in ENVIRONMENT=dev and TEAM=dev1:
- dev/dev1/nginx-example/values.encrpyted.yaml -> the helm deployment configuration

All relevant env and team specific configuration is contained in json or yaml files, read by the
library through `Config` (`src/kubementat/core/config/config.py`) and by the pipeline steps through
`kmt` and the `--config-dir` flag. Nothing reads them with `jq` any more: the `jq -r` that used to
answer these keys lived in the shell scripts, those scripts are deleted, and the reads they answered
now go through the layers — which is why a missing key warns and says which file to edit, instead of
becoming the literal string `null` the way `jq -r` handed it on.

Every `*.encrypted.*` file kubementat touches lives **in the configuration directory**, never beside the
product, and git-crypt is initialized there by `config init` (see `docs/README_GIT_CRYPT.md`). The product
repository itself ships nothing encrypted.

## How a tree stays current

`<dir>/kubementat.yaml` is the tree's own statement of what it was prepared for: `schema_version` for what
the keys *mean*, and `kubementat: ">=0.4,<2"` for the release line it was last validated against. Both are
written by `kmt config init` and read by every verb before it touches a cluster.

Before upgrading, ask whether the tree survives the build you are about to run:

```
kmt config validate <environment> --config-dir <configuration-root>
```

That reads and writes nothing and addresses no cluster. It says whether the tree sits inside the version
window this build reads, and it is the only command that refuses about the window: a `schema_version` two
or more steps behind is an error that names both numbers, and a `kubementat:` constraint this build falls
outside is refused there too. A cluster command is gentler than that on purpose -- a running cluster must
not be bricked by a version string -- so the same fact is only a warning when you install.

There is no migration to run, and that is a decision rather than a gap. kubementat records no rename table
and rewrites no operator tree, so nothing tells you what a bump would cost and nothing moves a file for
you. Where a path moved, the product refuses the old spelling by name at the point it reads it, and moving
what you keep is your own edit. What says the same thing twice is the CHANGELOG and the release note, which
are now the only channel that says what changed between versions.

So the pre-upgrade ritual is three commands: `kmt config validate <environment> --config-dir <configuration-root>`
against the tree you
have, then the one-line bump in `<dir>/kubementat.yaml` if the verdict asks for one, then validate again. A
tree that passes and still holds an old directory will be refused by that directory's name, and the refusal
tells you which two paths it is talking about.

# List of configuration options

## static.json

The keys are read through `Config._component_value` (`src/kubementat/core/config/config.py:791`):
configuration answers first, `src/kubementat/core/constants.py` is the fallback, and a key that is
missing warns once and
uses the built-in default instead of failing. A warning that names the key is therefore not an error —
it is kubementat telling you the cluster got its default and you may want something else. The rule
applies to every component; the routing keys are the ones documented here because that is the surface
that changed. **It does not apply to the two cluster keys**: `CLUSTER_KUBE_CONTEXT` has no default to fall
back to and a missing one is a refusal, not a warning (see *Cluster addressing* above). A key that answers
"which cluster" cannot be defaulted by a constant in the product tree without the command deciding to talk
to something it was never told to talk to.

Two keys break that rule, and they break it toward the manifest. `<COMPONENT>_ENABLED` and
`<COMPONENT>_VERSION` are answered by `Config.component_release_enabled` and
`Config.component_release_version` (`src/kubementat/core/config/config.py:579` and `:621`), which read the layers only — the team's
pair, then the environment's — and fall back to no constant at all. So `false` is not a warning and not a
default: it is `installed: false` in the spec helmfile builds, which removes a release that is live and is
not an error on a cluster that never had one. A version nobody names answers from the shipped manifest,
whose `default` in each release block carries the chart version kubementat ships — the one place in the read
path where the fallback is not `src/kubementat/core/constants.py`, because R-4.4 made that manifest the entry
point and copying the pin here would give it two owners. A number, list or dict under either key is refused
where it was typed, naming the file to edit, because neither knob has a valid answer that is not a boolean
or a string.

### Cluster addressing

Two keys, both environment-level, both read directly rather than through `component_config_key`:
`CLUSTER_KUBE_CONTEXT` and `CLUSTER_KUBECONFIG_PATH`. Neither answers from a shipped default, so no verb
requires them and none fills them — `config init` writes `null` and prints what that means, because
recording whatever `kubectl` is pointed at would be kubementat inventing a cluster claim it did not earn.

One resolution order, in one place (`Context.build`, `src/kubementat/core/identity/context.py:220`),
for every command — `core/identity/` is where that resolution lives now, and the module it was
extracted out of, `cluster.py`, is deleted. It is
five layers, and the fifth is the one that arrived after the spec was written:

```
1. --context / --kubeconfig                     the command's own flags
2. CLUSTER_KUBE_CONTEXT / CLUSTER_KUBECONFIG_PATH   process environment variables
3. env_static["CLUSTER_KUBE_CONTEXT"] / [..._PATH]  platform_config/<environment>/static.json
4. the material the kubelet mounts in this pod   ClusterTarget.from_service_account
5. otherwise: a named error naming the environment, the file, the key and the value found
```

Layer 4 is last among the layers that can *answer*, which is what keeps in-cluster from being a silent
winner: an environment that declares `CLUSTER_KUBE_CONTEXT` uses it, in a pod, every time. It is a
construction and not a declaration, which is why `Context.build` asks for it itself rather than folding it
into the declared three. A target built that way carries neither context nor file, and that has one
consequence a reader hits eventually: `kubectl logs`/`exec`/`port-forward` have no address to carry and
refuse, while every client path works. Sessions need a kubeconfig; a service account is not one.

Every `ClusterTarget` says where it came from, and there are five answers, not four: `"flag"`, `"env"`,
`"static.json:<path>"`, `"connection"`, and `"service-account"`. The last is the one that appeared with
in-cluster resolution, and it is what makes the answer verifiable — "resolved from the mounted token" and
"resolved from `platform_config/prod/static.json`" are two different debugging sessions.

### Routing

Four keys decide routing, all four answered by the environment's `static.json` (whose shipped
defaults are `kubementat/core/assets/templates/environment/static.json.template`, inside the installed
`kmt`). Three of them
(`TRAEFIK_DEPLOYMENT_NAMESPACE`, `TRAEFIK_INGRESS_CLASS`, `TRAEFIK_SERVICE_SELECTOR`) belong to the
component and are named by deriving the component's name into the key (`component_config_key`,
`src/kubementat/core/config/keys.py:195`); the fourth, `CLUSTER_MANAGER_EMAIL`, is read directly and has no default
an operator can lean on.

| key | default | who reads it | what it decides |
|---|---|---|---|
| `TRAEFIK_DEPLOYMENT_NAMESPACE` | `traefik` (`constants.DEFAULT_NAMESPACES`, `core/constants.py:58`) | `kubementat/core/assets/values/helmfile.yaml.gotmpl:432` reads it as `env "TRAEFIK_DEPLOYMENT_NAMESPACE" \| default "traefik"`, exported there by `HelmUtils._export_component_namespaces` (`src/kubementat/infrastructure/helm.py:802`); `Config.component_namespace("traefik")` (`core/config/config.py:548`) reads the same key on the Python side, and it is one of the namespaces `kmt uninstall` deletes. | which namespace the controller release lands in — and, because "ours" is decided by reading the cluster, **whether kubementat recognises a Traefik as its own** (`traefik_presence.py`'s classification is a Traefik chart release *in this namespace*, nothing else). Rename it and a release the cluster still carries is reported as `foreign`. |
| `TRAEFIK_INGRESS_CLASS` | `traefik` (`constants.DEFAULT_STATIC_ANSWERS`, which `routing.py`'s fallback table reads from) | `Config.component_ingress_class` (`core/config/config.py:755`) is the one read path, and the same answer reaches helmfile from `export_ingress_class` (`infrastructure/helm.py:986`) — the traefik release's inline `ingressClass.name` entry (`helmfile.yaml.gotmpl`, the block labelled `component_name: traefik`) reads `env "TRAEFIK_INGRESS_CLASS"` and names no class of its own. Also: the `ClusterIssuer` HTTP-01 solvers (`_apply_cluster_issuers` at `automations/routing/routing.py:491`, which writes the class into the solver at `routing.py:534`), the "nothing serves that class" warning (`_warn_when_nothing_serves_the_class`, `automations/routing/routing.py:568-611`), and `src/kubementat/automations/ingress.py`, which reads it through `Config` when it writes an `Ingress` for `kmt kubernetes create-ingress` — where the script that used to `jq` this file out of a path built from its own location used to answer it | the `IngressClass` the controller creates and the class every solver and every generated `Ingress` names. It is a config key and not a literal because a cluster that already runs a controller has to be able to rename this one without editing a template. Note what it does **not** do: it is not the cluster default. `ingressClass.isDefaultClass: true` is what makes an Ingress that names nothing routable, and that lives in the values file below. |
| `TRAEFIK_SERVICE_SELECTOR` | `app.kubernetes.io/name=traefik,app.kubernetes.io/instance=traefik` (`constants.DEFAULT_SERVICE_SELECTORS`, `core/constants.py:88`) | `Config.component_service_selector` (`core/config/config.py:746`) — but no `src/` call site names `traefik` today: the selector-based pod lookups resolve `grafana`, `tekton` and `openbao` only, in `automations/tunnels/tunnels.py:39,70,103`. (`polaris` is retired and no tunnel command reads a selector for it.) | which pod a selector-based lookup would find. It is declared so that a rename is answered in `static.json` and not in code, exactly like the other components; **today it has no reader.** The pair only matches while the release is named `traefik`, which is why it is a key and not something built from the component name. |
| `CLUSTER_MANAGER_EMAIL` | none, and nothing writes one. No shipped default answers it and no verb invents a value for it, so a fresh tree has the key absent | `automations/routing/routing.py:540-558` (`_cluster_manager_email`), called from `_apply_cluster_issuers`, when both `ClusterIssuer`s are applied | the ACME account contact. `kmt routing install` raises with this file's path when the value is missing or still a `REPLACEME_*` placeholder, because cert-manager registers the account with it and Let's Encrypt rejects a malformed contact — an issuance failure that stays invisible until a certificate expires. Set it before the first `routing install` with
`kmt config set <env> CLUSTER_MANAGER_EMAIL=... --config-dir <configuration-root>`. |

The nginx-era keys do not come back. `NGINX_INGRESS_DEPLOYMENT_NAMESPACE` is out of this file and out of
`constants.DEFAULT_NAMESPACES`, there is no `--enable-ingress`, no `--routing`, no alias and no key that
still accepts the old spelling — the same rule every renamed surface follows (`helm.helm_component_uninstall`).

## static.encrypted.json

TODO:

# Routing: what gets applied, and by what

`kmt routing install <environment> [--yes] [--update]` applies helmfile's `routing` group — the Traefik
release and the `cert-manager` release (`helmfile.yaml.gotmpl:388-441`, the two releases labelled
`group: routing`) — and then applies the two
`letsencrypt` / `letsencrypt-staging` `ClusterIssuer`s with their solver pointed at
`TRAEFIK_INGRESS_CLASS`. Which of those releases is touched is decided by a label filter that the command
can narrow (`group=routing`, or `group=routing,component_name!=traefik` when it finds a controller it does
not own), so the answer to "does kubementat install a controller here?" comes from the cluster and not from
a group name. `kmt routing status <environment>` is the read-only half of the same surface: three cluster
reads and a report that says which Traefik the cluster is running, whose it is, and what `install` would do.
`kmt install` applies `group=standard` and installs no controller; the routing group is applied by `kmt
routing install` or not at all. See the routing entry under `# Unreleased` in `CHANGELOG.md` for the
decision rules, and `docs/plans/traefik-integration.md` for the design they come from.

## `kubementat/core/assets/values/traefik/values.yaml`

`config init` writes nothing for it: since T-18 it is shipped data, applied from inside the package, and **the configuration holds the operator's overrides** rather than a copy of the product's defaults.
Templates are laid out by subdirectory for a shape the old copy step depended on; that step is deleted, and
the layout stays because it is what the directory of shipped values looks like, not because a verb reads it
one subdirectory at a time. The subdirectory's name is what the release block's values path is relative to — the routing release block
(`helmfile.yaml.gotmpl`, the block whose `component_name: traefik`) reads `traefik/values.yaml` from
`src/kubementat/core/assets/values/`, which is helmfile's working directory. It is plain YAML shipped by the
product: there is no git-crypt filter in this repository any more, so nothing here is encrypted and the
operator's overrides live encrypted in the configuration directory instead.

Nothing rewrites this file at install time — no `envsubst`, no placeholder table, no `REPLACEME_*` token.
It is plain YAML handed to Helm, which means a key the chart does not know is not silently ignored:
`traefik/traefik` 42.1.3 sets `additionalProperties: false` at the root, so one renamed key turns
`helmfile apply -l group=routing` into a release that installs nothing. `tests/test_the_routing_values_file_never_writes_a_service_type_or_a_storage_class.py`
validates the template against the chart's own `values.schema.json` for that reason.

**What deliberately is not in it, and why that is the point.** The namespace and the class name are not
here. `helmfile.yaml.gotmpl` supplies both from the environment kubementat hands it:
`env "TRAEFIK_DEPLOYMENT_NAMESPACE" | default "traefik"` for the namespace, and for the class an
`ingressClass.name` entry fed by `env "TRAEFIK_INGRESS_CLASS"` and carrying **no `default`** since
T-31 — the answer is exported into that environment by `export_ingress_class`
(`infrastructure/helm.py:986`) from
`Config.component_ingress_class`, which is why the template needs no fallback of its own. (It used to
say `default "traefik"` there, and while it did, nothing in `src/` exported the key: the release read
the template's own word and the Python side read the layer, which is how a tree that declared a class
could get `traefik` on the cluster and its own name in every manifest. `CHANGELOG.md`'s first row says
what that meant for a cluster that declared one.) Either way, writing one of the two into this file
would give one question two answers that can disagree — a release deployed to namespace A by helmfile
and found in namespace B by `Config`, or a solver pointing at a class no controller created. This file
holds only what no config key exists for, plus the things that must be true regardless of the
environment:

- `service.spec.type: LoadBalancer` — `spec.type` and never `service.type`, the spelling chart v40 moved.
  The template's own comment is the source for this: the old key is silently ignored, which leaves a
  LoadBalancer Service that never schedules and an install that logged nothing wrong. The second reason is
  upstream's own — a second LoadBalancer Service on a node whose ports 80/443 are held by another
  controller's `svclb` pods stays Pending.
- no `hostPort`, no StorageClass literal, no exposed dashboard: the API/dashboard and metrics
  entrypoints ship `expose.default: false`, `accessLog` is off because kubementat ships no log sink for
  it, `persistence.enabled: false` because cert-manager holds the ACME account and there is no
  `acme.json` to put a volume under, and `hub.enabled: false` because a licence token is not
  kubementat's to ship (`hub.namespaces: []` is written because the chart's schema marks that field
  required even when the hub is off).
- the `web` entrypoint redirects to `websecure` cluster-wide, permanently. That is the single answer to
  nginx's `force-ssl-redirect` / `ssl-redirect` annotations, which were deleted rather than translated:
  one redirect is one setting, not one annotation per Ingress.
- `providers.kubernetesIngressNGINX: false`, so a second provider does not discover the same Ingresses
  and build duplicate routers.
- `ingressClass.isDefaultClass: true`, the owner's decision of 2026-10-29. The honest cost, stated where
  it is set and not only in the CHANGELOG: a cluster that already has a different default class gains a
  second claimant. That is why the guard reads the cluster before it writes, why `kmt install` installs
  no controller, and why the lane that proves the install branch runs turns k3s's bundled controller off
  first.
