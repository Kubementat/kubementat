"""The values kubementat uses when `platform_config` does not declare them.

`platform_config/{environment}/static.json` is the source of truth and code is the fallback. That only
holds if the fallback says the same thing the template says, so the
defaults live in exactly one module instead of being duplicated by every caller (the four
`# TODO: make this a config` comments in `automation.py` and the namespace list in
`uninstall_kubementat.sh` were six copies of the same fact).

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

Linkerd is not a component here. The owner decided on 2026-10-28 that kubementat does not use
the mesh, so it has no entry, no key and no flag, and adding one would put back what
`kmt uninstall` sweeps out. What a cluster that used to have the mesh keeps is handled by the
removal step in `Automation`, which needs no component to find it.

An ingress controller *is* a component here. The owner decided on 2026-10-28, the same day
nginx-ingress came out, that Traefik takes the routing role, and the reason is the product's
rather than the chart's: dropping nginx-ingress without naming a replacement left every ingress
path in kubementat classless — the Ingress-manifest script, the tekton EventListener ingresses and
every `Ingress` in `platform_config` all named a class nothing installed any more, which is a
failure that stays silent until a certificate or a listener stops answering. (The first of those is
`ingress_generator.py`, which reads its class from configuration exactly as this component's key says it
should be read.) So the ingress
controller gets an entry, a key and a chart exactly as `cert-manager` does, named by
`TRAEFIK_CHART` below. What it does not get is an alias or a compatibility shim for the
`NGINX_INGRESS_*` names: the rule against aliases for a replaced name is stated where the rename
landed (`cli/commands/component.py`) and in this file's own history, and the old controller is still
swept by `Automation.uninstall_nginx_ingress`, which needs neither the entry nor the key.
The nginx sweep keeps its own controller string (`NGINX_INGRESS_CONTROLLER_PREFIX` in
`automation.py`) for the same reason the two must never be conflated: pointing it at Traefik's
controller would delete the class kubementat just installed, and the sweep's job is the residue
of the controller kubementat *does not* install. The namespaces in this dictionary are therefore
the set `kmt uninstall` deletes, and a cluster that has ever had nginx-ingress carries one more
than this says — which is why that step reads the cluster instead of this table.

The entries cover more components than Python reads today: `uninstall_kubementat.sh` deletes
namespaces for components that no Python function opens a tunnel to, and bash has no
constants to fall back on, so it reads every one of them from the config file instead.
"""

# Namespace each component is deployed into. Equal to what `static.json.template` ships and
# to what the code said before this was configurable — the point of making it configurable
# was to be able to change it, not to change it here.
DEFAULT_NAMESPACES = {
    "cert-manager": "cert-manager",
    "cluster-cronjobs": "cluster-cronjobs",
    "goldilocks": "goldilocks",
    "grafana": "grafana",
    "loki": "loki",
    # The secret manager that replaced Vault on 2026-10-31, for the licence reason written in the
    # helmfile release block. The entry is load-bearing twice over, exactly as Traefik's is:
    # `HelmUtils._export_component_namespaces` iterates this set to export
    # `OPENBAO_DEPLOYMENT_NAMESPACE` before helmfile runs, and `kmt uninstall` deletes the namespaces
    # in it. Keeping the `vault` key alongside it is the alias the owner refused to keep: one more
    # config key pointing at a namespace nothing creates any more.
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

# The one chart that installs Traefik. `traefik-crds` is deliberately not named here: it has been
# deprecated since chart v38.0.2 and ships no proxy, and the `traefik` chart carries its own
# `crds/`. Naming the companion instead (or alongside) is how an install ends up with CRDs from one
# chart and a proxy from another, which helm will not reconcile for you.
TRAEFIK_CHART = "traefik/traefik"

# The controller string on the IngressClass this chart creates, read from the chart's own
# `_helpers.tpl` rather than guessed. It is what the routing guard matches IngressClasses on, and
# it is not `NGINX_INGRESS_CONTROLLER_PREFIX` in `automation.py`, which points the other way on
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
# The ten answers `initialize`'s templates used to leave behind, as L0.
#
# Census L measured that this set was answered only by the template copies `initialize` wrote into
# `platform_config/`, and that nothing in `constants.py` carried any of it. That is not a gap that
# opens when `initialize` is deleted — it is a gap that is *already open*: a `config init` tree
# answers none of it today, and a reader that asks `static.json` gets `None` and carries on. So the
# value lives here and a reader consults it, which is what "defaults resolved at read time" means here.
# Not a template, not an environment variable, and not an inline default at the call site.
#
# The shape is `DEFAULT_NAMESPACES`' and `DEFAULT_SERVICE_SELECTORS`' — a table keyed by the key's
# own name, each entry carrying the sentence that says what it is the default *of* — with one
# difference worth naming: those two are keyed by *component* and have their key name built at the
# read (`component_config_key(component, suffix)`), while these ten keys name no component, so the
# key is spelled in the table and is the spelling a `static.json` uses. `config.py` consults the
# table on the same walk the layers get, after every layer has failed to answer, so it is L0 and not
# a second layer: a value an operator wrote still outranks it, and `Config.declared()` never sees it
# (the rule about what may be *deleted* is a file's, not a default's).
#
# Two exclusions are deliberate, and saying them here is cheaper than letting the next reader find
# them out of a diff:
#
# * `PIPELINE_NAMESPACE` and `APP_DEPLOYMENT_NAMESPACE` are **not** in this table. The templates do
#   carry `dev1-pipelines` and `dev1`, but those strings were placeholders for the team substitution,
#   and they are consequences of the team's name (`config_derived.py`). A `constants.py`
#   default for them would install a second owner for a value that is computed, and a table entry is
#   the kind of second owner that reads as documentation and answers as a lie.
# * `MYSQL_HOST` / `REDIS_MASTER_HOST` are derived for the same reason, and `MYSQL_ROOT_PASSWORD` and
#   the rest of the encrypted set are *generated* by `config set --generate` — two correct
#   answers that differ is generation, not a default, so nothing here may invent one.
#
# `TRAEFIK_INGRESS_CLASS` is in this table, and it is the only answer that table has. It used to have a
# second owner in the manifest -- `helmfile.yaml.gotmpl` spelled
# `env "TRAEFIK_INGRESS_CLASS" | default "traefik"` next to the variable it reads -- and both sides of
# that pair saying `traefik` is precisely when drift starts, so the manifest half is gone: the
# release's `ingressClass.name` line reads the variable and nothing else, and
# `helm_utils.export_ingress_class` puts this table's answer -- or the layer that outranks it -- into
# that variable on every apply. Leaving two owners standing on purpose is what that pair was until
# 2026-11-03; what is true now is one owner and one read path, and a missing answer fails as helmfile's
# own rather than as a default that happens to be right. The reason the export does not skip keys that
# no layer declared is this table: L0 answers the key, so a tree that wrote nothing has an answer to be
# handed over. See
# `tests/test_the_ingress_class_the_manifest_names_is_the_one_the_configuration_answers.py`.
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
    "AUTOMATION_GIT_REVISION": "master",
    # The `ingressClassName` the generated Ingress, the cert-manager solver and the EventListener
    # ingresses all name. It is Traefik's class because Traefik is the controller this product
    # installs (see `TRAEFIK_CHART` above), and a class nothing installs is a silent failure.
    "TRAEFIK_INGRESS_CLASS": "traefik",
}

# What `config init` seeds into `<root>/<environment>/static.encrypted.json`, and the only place these
# numbers are written. Exactly one key is here, and why only one is the whole content of this comment.
#
# `GRAFANA_ADMIN_USER` is a name and not a secret: grafana's `adminUser` is rendered with helmfile's
# `requiredEnv`, so a tree that does not name one aborts the whole file (`helm_utils
# ._grafana_admin_credentials` says so before anything is applied). Before `kmt initialize` was deleted
# the value arrived from `static.encrypted.json.template`, which was the only copy that verb made; a
# tree built by `config init` got `{}`, so every fresh tree hit that refusal on its first `install`.
# This table is that value with the copying verb gone.
#
# The template's other five keys deliberately do NOT come here. Four carry a `REPLACEME_` sentinel, and
# seeding one would write a value that looks chosen into a file that is read as a credential — a
# placeholder is a lie in a place where an absence is honest. `GRAFANA_USERS` is a list of real people.
# Both belong to a human who names them, which is the sense in which init writes keys and not product
# shape. `GRAFANA_ADMIN_PASSWORD` is absent for the same reason and warns instead of refusing:
# needed to be useful, not needed to exist.
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

    Reached by `config.py`'s resolution walk, and by any caller that has to name the default of a
    key it did not build from a component. It is a lookup and not a decision: the table says what the
    answer is, the walk says whether a layer outranks it.
    """
    return DEFAULT_STATIC_ANSWERS.get(key)


# What a freshly initialized tree declares about the product it is configured for. Both keys land in
# `<root>/kubementat.yaml`, which `config_init.init_configuration` writes and which `config.py`'s
# `check_configuration_stamp` now reads before any CLI verb resolves a cluster — it had no reader
# since it was first written, which is the sentence this comment used to carry. A constraint that
# lived in a template literal would be a second source of truth the day the stamp reader started
# asking, and this file is where `pyproject.toml`'s range is answered from.
#
# `CONFIG_SCHEMA_VERSION` is stamped and never bumped by a library function — reading a tree from an
# older schema is a migration decision, not a side effect of writing one file.
CONFIG_KUBEMENTAT_CONSTRAINT = ">=0.4,<2"
CONFIG_SCHEMA_VERSION = 1

# The name of the file that carries those two values. It lives here and not beside either writer
# because two files touch it: `config_init` writes it and `config` has to read it before it decides
# whether anything else about the tree may be trusted. Naming it twice would be the ordinary way for
# the reader to keep answering about a path the writer stopped writing.
CONFIG_MANIFEST_FILENAME = "kubementat.yaml"
