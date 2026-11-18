"""The values kubementat uses when the configuration root does not declare them.

`<root>/{environment}/static.json` is the source of truth and code is the fallback. That only
holds if the fallback says the same thing the tree says, so the defaults live in exactly one module
instead of being duplicated by every caller. A default that six callers each write down is six
answers, and the sixth one is the one a stale tree reads.

Throughout this file `<root>` is the configuration root: the directory `--config-dir` names, or
the one `$KUBEMENTAT_PLATFORM_CONFIG_DIR` already points at. There is no directory called
`platform_config` to find, and kubementat looks beside no checkout for one.

A component is named by its helmfile release/component name, lower-case with hyphens. The
config key is derived from it, never spelled out per call site:

    component            key
    -------------------  -------------------------------------
    grafana              GRAFANA_DEPLOYMENT_NAMESPACE
    cert-manager         CERT_MANAGER_DEPLOYMENT_NAMESPACE
    tekton               TEKTON_DEPLOYMENT_NAMESPACE

`tekton` is deliberately not `tekton-pipelines`: the component is tekton, the namespace it
lands in happens to be `tekton-pipelines`, and conflating the two is how a rename ends up
being a code change.

Linkerd is not a component here: kubementat does not use the mesh, so linkerd has no entry, no key
and no flag, and adding one would put back what `kmt uninstall` sweeps out. What a cluster that ever
had the mesh keeps is found by `automations/uninstall/linkerd.py`, which needs no component entry.

An ingress controller *is* a component here: Traefik takes the routing role, and the reason is the
product's rather than the chart's. A dropped controller that is not named by a replacement leaves
every ingress path in kubementat classless — the Ingress-manifest script, the tekton EventListener
ingresses and every `Ingress` in the tree name a class the cluster has no controller for, a failure
that stays silent until a certificate or a listener stops answering. So the ingress controller gets an
entry, a key and a chart exactly as `cert-manager` does, named by `TRAEFIK_CHART` below; what it does
not get is an alias or a compatibility shim for the `NGINX_INGRESS_*` names, because an alias for a
replaced name is a second key nobody can fix. The old controller is swept by
`automations/uninstall/nginx_ingress.py`, which needs neither the entry nor the key.

The nginx sweep keeps its own controller string (`NGINX_INGRESS_CONTROLLER_PREFIX`, declared in that
same file) for the same reason the two must never be conflated: pointing it at Traefik's
controller would delete the class kubementat just installed, and the sweep's job is the residue
of the controller kubementat *does not* install. The namespaces in this dictionary are therefore
the set `kmt uninstall` deletes, and a cluster that has ever had nginx-ingress carries one more
than this says — which is why that step reads the cluster instead of this table.

The entries cover more components than Python opens a tunnel to, and that is the deliberate half of
the table: `namespaces_kubementat_owns` in `automations/uninstall/sweep.py` iterates
`DEFAULT_NAMESPACES` to decide what `kmt uninstall` deletes, so a component nobody tunnels to is
still swept. The list is read through `Config`, falling back to this table.

**This table answers "in which namespace", not "does it exist."** The existence question is answered
once, in `core/components.py`, which pairs these keys with the releases the shipped manifest labels
and gives each name a kind. A key here with no release is a namespace kubementat creates (`tekton`,
`tekton-dashboard`, `tekton-pipelines-resolvers`, `cluster-cronjobs`, `goldilocks`, `vpa`); a label the
manifest carries that is not a key here is a release that belongs to no component (`alloy`, the two
prometheus exporters). Both halves of that sentence are derived rather than counted, and that is the
point: the same function decides what `kmt component install` accepts, what the namespace export
writes, which releases the rename guard may ask about, and which CRD groups the uninstall sweep
deletes, so a name cannot be accepted by one verb and refused by another while the manifest disagrees
with both.
"""

# Namespace each component is deployed into. Equal to what the environment layer's witness spells
# (`tests/fixtures/configuration_shape/environment/static.json.template`) and to what the code said
# before this was configurable — the point of making it configurable was to be able to change it,
# not to change it here.
DEFAULT_NAMESPACES = {
    "cert-manager": "cert-manager",
    "cluster-cronjobs": "cluster-cronjobs",
    # The CI server arrived 2026-11-17 as the second member of the `extra` group. Opt-in is the
    # release's business (the helmfile block defaults `CONCOURSE_ENABLED` false); the entry's
    # business is addressability, and it is load-bearing twice over exactly as openbao's is:
    # `HelmUtils._export_component_namespaces` iterates this set to export
    # `CONCOURSE_DEPLOYMENT_NAMESPACE` before helmfile runs, and `kmt uninstall` deletes the
    # namespaces in it.
    "concourse": "concourse",
    "goldilocks": "goldilocks",
    "grafana": "grafana",
    "loki": "loki",
    # The secret manager kubementat installs, for the licence reason written in the helmfile release
    # block. The entry is load-bearing twice over, exactly as Traefik's is:
    # `HelmUtils._export_component_namespaces` iterates this set to export
    # `OPENBAO_DEPLOYMENT_NAMESPACE` before helmfile runs, and `kmt uninstall` deletes the namespaces
    # in it. A `vault` key beside it is an alias: one more config key pointing at a namespace nothing
    # creates.
    "openbao": "openbao",
    "polaris": "polaris",
    "prometheus": "prometheus",
    "tekton": "tekton-pipelines",
    "tekton-dashboard": "tekton-dashboard",
    "tekton-pipelines-resolvers": "tekton-pipelines-resolvers",
    # The controller kubementat installs, decided on 2026-10-28. The entry is load-bearing twice
    # over: `HelmUtils._export_component_namespaces` iterates this set to export
    # `TRAEFIK_DEPLOYMENT_NAMESPACE` before helmfile runs, and `kmt uninstall` deletes the
    # namespaces in it. Omit it and both halves keep working against a cluster that has nothing
    # installed — a namespace nobody exports is a release that always lands on the template's
    # default, and a namespace nobody sweeps is a component that outlives its own uninstall.
    "traefik": "traefik",
    "vpa": "vpa",
}

# Pod selectors the `kmt tunnel-*` commands need to find the right pod. Same shape as the
# namespaces: one key per component, one place to say it.
DEFAULT_SERVICE_SELECTORS = {
    "grafana": "app.kubernetes.io/name=grafana,app.kubernetes.io/instance=grafana",
    "polaris": "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris",
    # `kmt tunnel tekton` reaches the dashboard, and the dashboard release lands in the
    # tekton namespace, so the namespace and the selector belong to the same component.
    "tekton": "app=tekton-dashboard",
    # The Concourse chart labels by component rather than by the kubernetes.io pair the Traefik
    # and OpenBao charts write: `app=concourse-web` on the web pods, `app=concourse-worker` on the
    # workers, `app=concourse-postgresql` on the database, so this selector is the web tier and
    # nothing else. Read off a live release rather than a chart helper, and a config key for the
    # same reason every entry here is one: the selector only matches while the release answers to
    # `concourse`, and the key is where a renamed release is answered.
    "concourse": "app=concourse-web",
    # Verified against the chart's own label helpers: `app.kubernetes.io/name` is the chart name
    # and `app.kubernetes.io/instance` is the release name, so this pair only matches while the
    # release is named `traefik` — which is exactly why the selector is a config key and not a
    # string derived from the component name. A renamed release breaks the tunnel, not the install.
    "traefik": "app.kubernetes.io/name=traefik,app.kubernetes.io/instance=traefik",
    # Read out of the OpenBao chart's own `_helpers.tpl` and its rendered `server-statefulset.yaml`
    # at chart 0.30.6: `app.kubernetes.io/name` is the chart name (`openbao`),
    # `app.kubernetes.io/instance` is the release name, and the server pods carry the pair plus
    # `component: server`. The injector's pods are named `openbao-agent-injector`, so this pair
    # selects the server and not the webhook. Same shape as Traefik's entry above, and the same
    # reason it is a config key: it only matches while the release is named `openbao`.
    "openbao": "app.kubernetes.io/name=openbao,app.kubernetes.io/instance=openbao",
}

# The one chart that installs Traefik, and the only place this product answers "why not
# `traefik-crds`?" — the reader that matches a chart name, the CRD sweep and the values manifest all
# point here for it rather than restating it. The answer, once: the companion chart was deprecated at
# chart v38.0.2, ships no proxy, and the `traefik` chart carries its own `crds/`, so naming it instead
# of (or beside) the proxy chart leaves a cluster with CRDs from one chart and a controller from
# another, which helm does not reconcile for anyone. Two consequences follow from it and are owned by
# the code that needs them: a `helm list` row whose chart is `traefik-crds` is *not* a running
# controller, and a cluster that carries only that chart has no controller left for the
# `TRAEFIK_CRD_GROUPS` definitions to serve.
TRAEFIK_CHART = "traefik/traefik"

# The controller string on the IngressClass this chart creates, read from the chart's own
# `_helpers.tpl` rather than guessed. It is what the routing guard matches IngressClasses on, and
# it is not `NGINX_INGRESS_CONTROLLER_PREFIX`, declared in `automations/uninstall/nginx_ingress.py`,
# which points the other way on
# purpose: that one finds the controller kubementat is not, and matching Traefik's class with it
# would delete the class kubementat just installed.
TRAEFIK_CONTROLLER_NAME = "traefik.io/ingress-controller"


# ---------------------------------------------------------------------------------------------
# The binaries kubementat is pinned to. One declaration of each, read by the dependency check now
# and by a CI image build later (row 16's other half, which this change deliberately does not add).
#
# What a version that is *not* here costs: it warns, and the run continues. That is a ruling and not
# a hedge, and the argument is this tree's own history. `e57fed3` fixed `helm list --all` -- accepted
# by helm 3, rejected by helm 4 -- and the fix was to spell the six statuses so the product works on
# **both** majors. Portability was bought on purpose. A hard pin would take a working helm 3, or a
# helm 4 newer than anything measured here, and block `kmt install` and every verb that reads a
# cluster with it: safety the code already provides, paid for in operator downtime. So this answers
# the question that
# bug left unanswered -- *which side is this run on?* -- and stops there. Presence of a binary still
# fails the run; only a version outside the served range becomes one warning line.
#
# The range is *derived from the pin* rather than declared next to it, because two constants that
# agree by luck are how this file would acquire its next lie. What cannot be derived is how far
# *below* the pinned major a run may be, which is a portability decision rather than a measurement,
# and is written as a count for exactly that reason.
#
# Chart versions are not here and never will be: `helmfile.yaml.gotmpl` carries those and they are a
# different ticket (row 3a). A pinned chart under an unpinned helm has this exact bug still.
PINNED_BINARY_VERSIONS = {
    "helm": "4.3.1",
    "helmfile": "1.7.0",
}

# Majors under the pinned one that are served without warning. helm: one, because `e57fed3` made the
# release listing portable across helm 3 and helm 4 deliberately. helmfile: none, because no such
# portability was ever bought -- its 0.x/1.x split is not something kubementat has reconciled, so a
# major nobody measured is a guess and gets the warning.
PORTABLE_MAJORS_BELOW_THE_PIN = {
    "helm": 1,
    "helmfile": 0,
}

# How each binary is asked for its version is decided next to the check that asks it, in
# `automation.BINARY_VERSION_ARGV`, and not here: this table is the fact, and the argv is the
# decision about how to read the fact out of a machine. It lives in one place for the same reason.

def pinned_binary_version(binary: str) -> str:
    """The single declared version of *binary*. The only place that number is written."""
    return PINNED_BINARY_VERSIONS[binary]


def binary_majors_served(binary: str) -> frozenset:
    """Major versions kubementat runs without warning, derived from the pin.

    A run on any of them is a run kubementat has decided how to behave on. One below that is not an
    error, it is a fact the operator should be told, so the caller warns and carries on.
    """
    pinned = int(PINNED_BINARY_VERSIONS[binary].split(".")[0])
    return frozenset(range(pinned - PORTABLE_MAJORS_BELOW_THE_PIN.get(binary, 0), pinned + 1))


def binary_majors_served_label(binary: str) -> str:
    """The served majors as one short span, so the warning line stays a line."""
    served = sorted(binary_majors_served(binary))
    return str(served[0]) if len(served) == 1 else f"{served[0]}-{served[-1]}"

# ---------------------------------------------------------------------------------------------
# The answers `initialize`'s templates write into a configuration root, held as L0.
#
# These keys are answered only by the template copies the `initialize` verb writes, so a tree built by
# `config init` answers none of them: a reader that asks `static.json` gets `None` and carries on. The
# value lives here and the reader consults it, which is what "defaults resolved at read time" means
# here — not a template, not an environment variable, and not an inline default at the call site.
#
# The shape is `DEFAULT_NAMESPACES`' and `DEFAULT_SERVICE_SELECTORS`' — a table keyed by the key's
# own name, each entry carrying the sentence that says what it is the default *of* — with one
# difference worth naming: those two are keyed by *component* and have their key name built at the
# read (`component_config_key(component, suffix)`), while these keys name no component, so the
# key is spelled in the table and is the spelling a `static.json` uses. `core/config/config.py` consults
# the table on the same walk the layers get, after every layer has failed to answer, so it is L0 and not
# a second layer: a value an operator wrote still outranks it, and `Config.declared()` never sees it
# (the rule about what may be *deleted* is a file's, not a default's).
#
# Two exclusions are deliberate:
#
# * `PIPELINE_NAMESPACE` and `APP_DEPLOYMENT_NAMESPACE` are **not** in this table, because those
#   strings are consequences of the team's name (`core/config/derived.py`). A `constants.py` default for
#   them would install a second owner for a computed value, and a table entry is the kind of second
#   owner that reads as documentation and answers as a lie.
# * `MYSQL_HOST` / `REDIS_MASTER_HOST` are derived for the same reason, and `MYSQL_ROOT_PASSWORD` and
#   the rest of the encrypted set are *generated* by `config set --generate` — generation is not a
#   default, so nothing here may invent one.
#
# `TRAEFIK_INGRESS_CLASS` is in this table and is the only answer for it. The manifest once spelled
# `env "TRAEFIK_INGRESS_CLASS" | default "traefik"` beside it, and two owners that happen to agree is
# precisely where drift starts, so the manifest half is gone: the release's `ingressClass.name` line
# reads the variable and nothing else, and `export_ingress_class` in `infrastructure/helm.py` puts this
# table's answer — or the layer that outranks it — into that variable on every apply. The export does
# not skip keys no layer declared for the same reason: L0 answers the key, so a tree that wrote nothing
# still has an answer to be handed over.
# ---------------------------------------------------------------------------------------------
DEFAULT_STATIC_ANSWERS = {
    # Which Tekton Pipelines release `kmt install` applies, and therefore which API versions the
    # cluster is expected to serve. Every PipelineRun kubementat generates is written against it.
    "TEKTON_VERSION_PIPELINE": "1.17.1",
    # The dashboard release that reads the Pipelines API above, applied second because it reads it.
    "TEKTON_VERSION_DASHBOARD": "0.73.0",
    # The trigger controller's release, whose `interceptors.yaml` also answers the
    # `config-triggers-core-interceptors` ConfigMap the controller fails to start without.
    "TEKTON_VERSION_TRIGGERS": "0.37.2",
    # The image name part of the CI container's reference — the registry base URL and the tag are the
    # other two pieces, and all three are needed to name one image and no half of one.
    "TEKTON_CI_IMAGE_NAME": "ubuntu-ci-minimal",
    # The tag of that image. `latest` is what the tree shipped, so a tree that typed nothing runs the
    # image it always ran rather than failing with a reference ending in `:None`.
    "TEKTON_CI_IMAGE_TAG": "latest",
    # The `storageClassName` of the workspace volume claim a generated PipelineRun asks for, and the
    # class a pipeline's own workspaces mount. A class name is a fact about the cluster, so this is a
    # default and not an answer: an operator whose cluster has no `local-path` writes the key.
    "TEKTON_KUBERNETES_STORAGE_CLASS": "local-path",
    # The ServiceAccount the Helm deployer's own work runs as, which is the identity the deploy
    # pipeline's steps are bound to and the one whose RBAC kubementat grants.
    "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": "helm-deployer-sa",
    # The repository name the automation clones and pushes from — the project half of the git
    # coordinate, next to the revision below and the server keys, which are *not* defaulted here.
    "AUTOMATION_GIT_PROJECT_NAME": "kubementat",
    # The branch the automation reads and writes. It is the half of the coordinate that changes
    # without any file being edited, which is the reason it is a default and not a constant.
    "AUTOMATION_GIT_REVISION": "main",
    # The `ingressClassName` the generated Ingress, the cert-manager solver and the EventListener
    # ingresses all name. It is Traefik's class because Traefik is the controller this product
    # installs (see `TRAEFIK_CHART` above), and a class nothing installs is a silent failure.
    "TRAEFIK_INGRESS_CLASS": "traefik",
}

# The component the routing role belongs to, spelled once. The string is this file's own convention —
# lower-case, hyphenated, equal to the helmfile release name — so it belongs beside the tables that key
# on it and nowhere else, and `constants.py` imports nothing for it. Every routing module imports this
# name instead of spelling it again: three spellings is three owners of one component name and no
# single place a rename has to visit.
COMPONENT = "traefik"

# The fallback for the ingress-class read, as `DEFAULT_NAMESPACES` is the fallback for the namespace
# read: one key per component, one place that says what the default is. The value is not written again
# here — it is read out of `DEFAULT_STATIC_ANSWERS` under the key this read takes, so the one place the
# word `traefik` stands as this key's default is that table. A spelling of its own in a routing module
# would be a private L0 the read path never consults.
#
# The key is spelled as a literal rather than built. The rule that gives the key its *format* —
# component name upper-cased and its hyphens turned into underscores — lives in
# `config.component_config_key`, and this file may not import it: `core/config/config.py` imports
# this file, and
# importing it back is the cycle. Spelling the key is `constants.py`'s own idiom rather than a
# workaround, and every key in `DEFAULT_STATIC_ANSWERS` above is spelled exactly this way; this table
# indexes one key and states which one. The invariant it protects — that one place states the value —
# holds just as well when the index is written here.
#
# Indexing the table rather than calling `static_answer` is deliberate too: a missing entry is a
# `KeyError` on import and not a `None` that reaches `spec.ingressClassName` months later.
DEFAULT_INGRESS_CLASSES = {COMPONENT: DEFAULT_STATIC_ANSWERS["TRAEFIK_INGRESS_CLASS"]}

# What `config init` seeds into `<root>/<environment>/static.encrypted.json`, and the only place these
# numbers are written. Exactly one key is here, and why only one is the whole content of this comment.
#
# `GRAFANA_ADMIN_USER` is a name and not a secret: grafana's `adminUser` is rendered with helmfile's
# `requiredEnv`, so a tree that does not name one aborts the whole file (`_grafana_admin_credentials`
# in `infrastructure/helm.py` says so before anything is applied). A tree that got no answer for it
# would hit that refusal on its first `install`, so the default is seeded here rather than left to a
# template copy.
#
# The other five keys of the old template deliberately do NOT come here. Four carry a `REPLACEME_`
# sentinel, and seeding one would write a value that looks chosen into a file that is read as a
# credential — a placeholder is a lie in a place where an absence is honest. `GRAFANA_USERS` is a list
# of real people. Both belong to a human who names them. `GRAFANA_ADMIN_PASSWORD` is absent for the same
# reason and warns instead of refusing: needed to be useful, not needed to exist.
DEFAULT_ENCRYPTED_ANSWERS = {
    "GRAFANA_ADMIN_USER": "grafadmin",
}


# Answers `config init` fills in with something it makes up, and the only place this list is written.
# A generated answer is different in kind from a seeded one and the two tables say so: seeding writes a
# value that is always the same, generation writes a secret that is different per tree.
#
# `GRAFANA_ADMIN_PASSWORD` is here because helmfile renders it with `requiredEnv`, so a tree that does
# not name one does not skip grafana — it aborts the whole file, and did so with a message that named
# neither the key nor the file. It cannot be seeded: a constant admin password in shipped code is a
# credential every installation in the world shares, which is strictly worse than the absence it
# replaced. Generating is what the product already does for a key nobody answered (`config set
# --generate`), so init is not inventing a mechanism, it is using its own.
GENERATED_ENCRYPTED_ANSWERS = ("GRAFANA_ADMIN_PASSWORD",)


def encrypted_answer(key: str):
    """The seed `config init` writes for *key*, or `None` when this table says nothing about it."""
    return DEFAULT_ENCRYPTED_ANSWERS.get(key)


def static_answer(key: str):
    """L0 for *key*, or `None` when this table answers nothing for it.

    Reached by `core/config/config.py`'s resolution walk, and by any caller that has to name the
    default of a key it did not build from a component. It is a lookup and not a decision: the table
    says what the answer is, the walk says whether a layer outranks it.
    """
    return DEFAULT_STATIC_ANSWERS.get(key)


# What a freshly initialized tree declares about the product it is configured for. Both keys land in
# `<root>/kubementat.yaml`, which `config_init.init_configuration` writes and which
# `check_configuration_stamp` in `core/config/stamp.py` reads before any CLI verb resolves a
# cluster. A constraint that lived in a template literal would be a second source of truth the day the
# stamp reader started asking, and this file is where `pyproject.toml`'s range is answered from.
#
# `CONFIG_SCHEMA_VERSION` is stamped and never bumped by a library function — reading a tree from an
# older schema is a migration decision, not a side effect of writing one file.
#
# What the constraint *means* is stated once, and this is that one place: it names the release line
# this build belongs to, and a major release narrows it to that line rather than widening the ceiling
# to keep older trees quiet. The gap is the point — moving the floor is how a stale stamp ends up read
# as current, which is the mistake the check exists to catch. What to *do* about a gap is stated once,
# elsewhere: `ConfigurationStamp.version_gap_remediation` is the single remediation answer every verb
# quotes, so this comment says no second sentence about bump PRs and no second `schema_version`.
CONFIG_KUBEMENTAT_CONSTRAINT = ">=2,<3"
CONFIG_SCHEMA_VERSION = 1

# The name of the file that carries those two values. It lives here and not beside either writer
# because two files touch it: `config_init` writes it and `config` has to read it before it decides
# whether anything else about the tree may be trusted. Naming it twice would be the ordinary way for
# the reader to keep answering about a path the writer stopped writing.
CONFIG_MANIFEST_FILENAME = "kubementat.yaml"


# ---------------------------------------------------------------------------------------------
# The vocabulary a cluster claim is answered with: the two keys a tree declares a cluster in, and the
# labels that say which layer answered.
#
# They live here rather than in `core/identity/` because three of that split's five modules name
# them. `CLUSTER_KUBE_CONTEXT_KEY` alone is read by a target's constructor to write its refusal, by
# the registry's declaration reads to state what was declared, and by `Context`'s four resolution
# layers to say which of them answered — and a key that three files name cannot live in one of the
# three, because the two that are not it would have to read upward to ask. Duplicating a shared value
# downward is the layering rule's own answer, and this file is what "downward" means: it is the one
# module in the tree that imports nothing.
#
# The two keys arrived from `cluster.py`, where the comment beside them argued that a
# constant nobody reads from the resolution order is a constant that goes stale. That argument was
# about staleness, not about placement: what goes stale is a key nobody reads, not a key spelled in a
# module that does not resolve clusters, and every reader is told to import it.
# ---------------------------------------------------------------------------------------------
CLUSTER_KUBE_CONTEXT_KEY = "CLUSTER_KUBE_CONTEXT"
CLUSTER_KUBECONFIG_PATH_KEY = "CLUSTER_KUBECONFIG_PATH"

# The `source` values a target can carry. `static.json:<path>` is a prefix plus the path rather
# than a
# bare label, because "which file said this" is the answer a debugging session is looking for.
SOURCE_FLAG = "flag"
SOURCE_ENV = "env"
SOURCE_CONNECTION = "connection"
SOURCE_SERVICE_ACCOUNT = "service-account"
STATIC_SOURCE_PREFIX = "static.json:"
