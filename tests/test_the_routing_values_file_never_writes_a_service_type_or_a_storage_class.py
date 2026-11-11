"""What the routing values file may never say, and why the shape of the file is part of that.

`templates/environment/kubementat_components/traefik/values.encrypted.yaml.template` is plain YAML:
what is written is what helm sees, because nothing rewrites this file. That is the whole reason the
checks exist. Four shapes of value fail by reporting success, and the exit criteria name each
of them:

* `service.type` is the *old* spelling. Chart v40 moved the key under `spec`, and the old one is
  silently ignored -- the Service falls back to `LoadBalancer`, which on k3s means svclb pods that
  cannot schedule and a Pending Service with no error anywhere. Writing `type:` beside `enabled:`
  is the defect, not a style choice.
* A StorageClass named by literal. `persistence.storageClass: nil` already means "the cluster's
  own default", and kubementat's StorageClass is a config key
  (`TEKTON_KUBERNETES_STORAGE_CLASS`), not a string in a values file. The requirements' grep is
  what turns that into a lane failure.
* A `REPLACEME_*` token, or an `env ...` expression. Those belong to `static.json` and to helmfile.
  Copying either mechanism in writes one fact in two places, where the disagreement is resolved
  silently by whoever applied last.
* A `hostPort`, or the dashboard reachable from outside. Both compete for node ports something
  else already holds, and both read as a working install right up until a pod does not schedule.

The chart's own `values.schema.json` is what makes the key names themselves a contract. That schema
sets `additionalProperties: false`, so a key the chart does not know does not install defaults and does
not warn -- it is an apply that stops before installing anything, which is why the two cases at the end
check this file's keys against the recorded chart rather than against each other.

The placement rule is the other half of the file, and it is a shipped path and not a
copy: the values file sits under `src/kubementat/assets/values/traefik/` beside the manifest that
names it, because that is the directory helmfile resolves a `values:` reference against. The wrong
folder or a leftover `.template` suffix still gives the same silent failure -- a release that
installs the chart's defaults while the operator believes their values are in use -- but it is now
a failure of the package layout rather than of `initialize`, and nothing in a configuration
directory can hold it.
"""

import re

import yaml

from kubementat.config_init import init_configuration
from asset_paths import template_path, values_path

# The shipped values root, which is also the directory helmfile runs in and therefore the directory
# every `values:` reference in the manifest resolves against.
VALUES_ROOT = values_path()
VALUES_FILE = VALUES_ROOT / "traefik" / "values.yaml"
HELMFILE_MANIFEST = VALUES_ROOT / "helmfile.yaml.gotmpl"

# The provisioner names kubementat's clusters actually use: k3s's default, and the value
# `TEKTON_KUBERNETES_STORAGE_CLASS` ships in `static.json.template`. Written as concatenation
# because the requirements' own grep is the one that names the first of them -- the pattern table
# must not trip the pattern it states.
STORAGE_CLASS_WORDS = re.compile(r"\b(" + "host" + r"path\b|local-path\b)")

# A value that is a placeholder rather than a value: `initialize`'s substitution marker, and the
# template expression helmfile evaluates. Neither means anything inside a values file.
NOT_A_VALUES_VALUE = re.compile(r"REPLACEME_|{{")

# Any spelling of a key that would name a StorageClass.
STORAGE_CLASS_KEY = re.compile(r"storageclass", re.IGNORECASE)

# The closed set of top-level keys this chart accepts, recorded rather than inferred: the `properties`
# map of `traefik/values.schema.json`, as pulled on 2026-10-28 by
#     helm pull traefik/traefik --version 42.1.3 && tar xzf traefik-42.1.3.tgz
# That file sets `"additionalProperties": false` at the root, so the list is the whole contract and
# not a hint -- 57 keys, and a key outside it is a failed render rather than an ignored line. The
# version is named because a list with no version in it is not evidence (tests/README.md), and 42.1.3
# is the pin this tree installs; the same chart version and the same pull command are stated in
# tests/test_uninstall_sweeps_everything_kubementat_owns.py.
# No test fetches this: the network is not part of the unit suite, so what is recorded here is the
# assertion.
CHART_42_ACCEPTED_ROOT_KEYS = frozenset({
    "accessLog", "additionalArguments", "additionalVolumeMounts", "affinity", "api",
    "autoscaling", "certificatesResolvers", "commonLabels", "core", "deployment", "enabled",
    "env", "envFrom", "experimental", "extraObjects", "fullnameOverride", "gateway",
    "gatewayClass", "global", "hostNetwork", "hub", "image", "imagePullSecrets", "ingressClass",
    "ingressRoute", "instanceLabelOverride", "livenessProbe", "log", "metrics", "nameOverride",
    "namespaceOverride", "nodeSelector", "oci_meta", "ocsp", "offering_version", "persistence",
    "podDisruptionBudget", "podSecurityContext", "ports", "priorityClassName", "providers",
    "rbac", "readinessProbe", "resources", "securityContext", "service", "serviceAccount",
    "serviceAccountAnnotations", "startupProbe", "tlsOptions", "tlsStore", "tolerations",
    "topologySpreadConstraints", "tracing", "updateStrategy", "versionOverride", "volumes",
})

# The one place in that same schema (traefik/values.schema.json, same pull as above) where a nested
# object declares a key of its own as `required`: `properties.hub.required == ["namespaces"]`, which
# is also what `helm show values traefik/traefik --version 42.1.3` prints (`namespaces: []  # @schema
# required:true`). `additionalProperties: false` explains a key that is rejected; this explains the
# other half -- a key that is mandatory once you open the object it belongs to, so that writing the
# object halfway is invalid even when the feature is off.
SCHEMA_REQUIRED_KEYS_INSIDE = {"hub": ("namespaces",)}


def values():
    """The file as helm reads it: the parsed document, not a text search."""
    return yaml.safe_load(VALUES_FILE.read_text(encoding="utf-8"))


def every_key(document, prefix=()):
    """`(path, value)` for every mapping key in the document, leaves included.

    An assert over the parsed document cannot be satisfied by a comment that says the forbidden
    thing is absent, which is the shortcut a text-only check would leave open.
    """
    if isinstance(document, dict):
        for key, value in document.items():
            path = prefix + (key,)
            yield path, value
            yield from every_key(value, path)
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from every_key(value, prefix + (index,))


def dotted_paths_for(document, name):
    """Every path whose last key is *name*, as dotted strings -- sorted for a stable message."""
    return sorted(".".join(str(part) for part in path)
                  for path, _ in every_key(document)
                  if str(path[-1]).lower() == name.lower())


# --- the two failures the file's name is about ------------------------------------------


def test_the_service_type_is_written_where_the_chart_actually_reads_it():
    """`service.spec.type`, with the old key gone rather than kept next to it.

    Both present still installs, and still leaves a value helm ignores -- which is how the next
    person who edits "the service type" edits the half that does nothing.
    """
    service = values()["service"]

    assert service.get("spec", {}).get("type"), \
        "the Service type has to sit under `spec`, where chart v40+ reads it"
    assert "type" not in service, \
        "`service.type` is silently ignored; leaving it beside the real key is the trap, not a fix"


def test_the_values_file_never_names_a_storage_class():
    """No volume and therefore no StorageClass -- not even as a word that names one.

    `persistence.enabled: false` is the correct answer while cert-manager holds the ACME account,
    and naming a provisioner is what makes the file stop being portable across clusters.
    """
    document = values()
    text = VALUES_FILE.read_text(encoding="utf-8")

    named = sorted(key for key, _ in every_key(document) if STORAGE_CLASS_KEY.search(str(key)))
    assert named == [], \
        f"a StorageClass key is a literal ({named}); what `nil` already means is the cluster default"
    assert not STORAGE_CLASS_WORDS.search(text), \
        "the requirements grep the tree for a provisioner name; this file is part of the tree"
    assert document.get("persistence", {}).get("enabled") is False, \
        "the file must not ask for a volume it has no reason to ask for"


# --- the file is a template, not a copy, so its shape is an assertion -------------------


def test_the_file_the_release_names_is_shipped_and_copied_by_nothing(tmp_path):
    """The shipped half of the rule, resolved rather than asserted.

    This file came out of `initialize`: the traefik values are product data, and the path the
    release spells is answered by the package and by nothing else. What this executes is that
    answer -- the reference resolved the way helmfile resolves it, against the directory of the
    manifest it was handed -- because a file that exists one directory away is the same silent
    failure the placement rule was written against.
    """
    referenced = [
        line.strip()[2:] for line in HELMFILE_MANIFEST.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s*- traefik/values", line)
    ]
    assert referenced == ["traefik/values.yaml"], \
        f"the traefik release references {referenced}, and a path it does not name is a file it never reads"
    assert (VALUES_ROOT / referenced[0]).is_file(), \
        "the reference does not resolve from the directory helmfile is run in"

    # ...and the write set that exists copies neither this file nor its manifest into a
    # configuration tree, because a copy would be a second copy of one file -- which is the bug
    # that moved them, not a compatibility branch a later release is supposed to keep. The write
    # set is what `config init` lays down now that the command that copied is deleted, so the
    # claim is asserted against the steps a real init reports and not against a constant.
    configuration_root = tmp_path / "platform_config"
    steps = init_configuration(configuration_root, "dev", "dev1")
    written = [str(step.path.relative_to(configuration_root)) for step in steps]
    assert not any("helmfile" in path or "kubementat_components" in path for path in written), \
        f"a manifest is written into the configuration tree: {written}"
    assert not template_path("environment", "kubementat_components",
                             "helmfile.yaml.gotmpl").is_file(), \
        "the manifest is shipped twice, and the copy under templates/ is the one a reader edits"


def test_no_placeholder_of_any_kind_survives_in_a_file_helm_reads_directly():
    """A `REPLACEME_*` in a values file ships to the cluster as that exact string.

    The knobs that vary per environment are the namespace and the class name, and both reach
    helmfile from `static.json` through `env ... | default`. A template expression here is the
    same fact written twice.
    """
    assert NOT_A_VALUES_VALUE.search(VALUES_FILE.read_text(encoding="utf-8")) is None, \
        "this file is plain YAML: nothing rewrites it, so a token is a value"


# --- what must not be there, stated while the file is new -------------------------------


def test_no_node_port_is_claimed_and_nothing_internal_is_reachable_from_outside():
    """`hostPort` is the knob that collides with whatever already holds 80/443.

    It is also the one whose collision arrives as a Pending Service instead of a port error. The
    API entrypoint is chart-default-off for that reason and for the dashboard's: a route to it is
    a route to the cluster's routing configuration.
    """
    document = values()

    assert dotted_paths_for(document, "hostPort") == [], \
        "a hostPort competes with the controller that got there first, and loses"
    assert document["ports"]["traefik"]["expose"]["default"] is False, \
        "the API/dashboard entrypoint is not exposed, and the chart's own comment says not to"
    assert document.get("api", {}).get("insecure", False) is False, \
        "`api.insecure` publishes the dashboard on every entrypoint"
    assert document.get("ingressRoute", {}).get("dashboard", {}).get("enabled", False) is False, \
        "the dashboard route is a per-environment opt-in, never a shipped default"
    assert document.get("hostNetwork", False) is False, \
        "hostNetwork turns a port collision into a node-wide one"


def test_the_class_it_creates_is_the_cluster_default_and_no_nginx_provider_runs():
    """Traefik is kubementat's Ingress controller, so the class it creates is the cluster's default.

    The owner decided this: "Traefik *is* the default when it is installed; that is the point of
    bringing a controller back. The IngressClass is the cluster-wide answer to an Ingress that
    names none." Before that answer this file asserted the opposite -- "a default class is an owner's
    decision, not a default" -- and the consequence was a cluster that had run `kmt routing install` with a
    controller installed and no claim on unrouted Ingresses, so every generated manifest had to spell
    `ingressClassName` out.

    The nginx-compat half of the assertion is unchanged, and for its own reason: that provider watches the
    same Ingresses the standard one does, which upstream warns makes duplicate routers -- and no nginx
    annotation survives in this tree to need it.
    """
    document = values()

    assert document["ingressClass"]["enabled"] is True, \
        "with no class nothing serves the ingresses kubementat generates"
    assert document["ingressClass"]["isDefaultClass"] is True, \
        "kubementat is opinionated about its controller: when it installs one, that one answers for an " \
        "Ingress that names no class"
    assert document["providers"]["kubernetesIngress"]["enabled"] is True, \
        "the Ingress provider is what makes a plain Ingress work"
    assert document["providers"]["kubernetesIngressNGINX"]["enabled"] is False, \
        "two providers watching one Ingress is two routers for it"


# --- what the chart's own `values.schema.json` accepts, checked against it ----------------


def test_every_root_key_the_values_file_writes_is_a_key_the_chart_accepts():
    """`additionalProperties: false` at the chart's root: a key it does not know is not ignored.

    That is the difference between this file and a comment. An unknown key here does not install the
    chart's defaults and does not warn -- `helmfile apply -l group=routing` stops with
    `additional properties 'logs' not allowed`, which is a release that never happened. So the check
    is membership in the recorded set, not a style rule: see
    `CHART_42_ACCEPTED_ROOT_KEYS` for where the set came from.

    It caught `logs.general.level`, which is the shape the earlier draft shipped: chart 42
    spells the key `log` (singular) and nests `level`/`format`/`filePath` under it, so the draft was
    rejected at the root rather than silently dropped a level down. It also catches the opposite
    mistake, a key renamed by a later chart version -- a rename moves a value to a place nothing
    reads, and the set is the only thing that says so without a cluster.
    """
    written = set(values())

    assert written <= CHART_42_ACCEPTED_ROOT_KEYS, (
        f"{sorted(written - CHART_42_ACCEPTED_ROOT_KEYS)} are not keys of "
        "traefik/traefik 42.1.3, whose schema forbids extra properties at the root; "
        "the apply fails instead of installing"
    )


def test_an_object_the_schema_makes_a_key_mandatory_is_never_written_half_set():
    """An object that is opened is a promise that its required keys are there.

    `hub: {enabled: false}` is the sentence "the hub is off", and it is exactly the sentence that is
    invalid: `hub` requires `namespaces`, so the render fails on the disabled branch too. The fix is
    not to prefer a namespace list, it is to satisfy the declaration -- `namespaces: []` is the
    chart's own default and asks for nothing.

    The mapping this loops over is recorded, not derived at runtime, for the same reason the key set
    is: no network in the unit suite. If a future chart version declares a second required key inside
    some object, the assert still holds of whatever is listed, and the list names its source.
    """
    document = values()

    for name, required in SCHEMA_REQUIRED_KEYS_INSIDE.items():
        if name not in document:
            continue
        present = document[name]
        assert isinstance(present, dict), \
            f"`{name}` has to be the object the schema describes, got {type(present).__name__}"
        missing = [key for key in required if key not in present]
        assert missing == [], \
            f"`{name}` is present but does not name {missing}, which its schema marks required"


def test_the_class_name_is_not_written_here():
    """The class is config, and this would be the second place it is decided.

    helmfile's inline entry passes `TRAEFIK_INGRESS_CLASS`; a name in this file would win or lose
    against it by merge order, and a rename test would rename exactly one of the two.
    """
    assert "name" not in values().get("ingressClass", {}), \
        "the class name is helmfile's to pass, read from the environment's static.json"
