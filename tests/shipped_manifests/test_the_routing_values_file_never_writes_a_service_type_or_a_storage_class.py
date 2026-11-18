"""What the routing values file may never say, and why the shape of the file is part of that."""

import re

import yaml
from asset_paths import template_path, values_path

from kubementat.config.init import init_configuration

# The shipped values root, which is also the directory helmfile runs in and therefore the directory
# every `values:` reference in the manifest resolves against.
VALUES_ROOT = values_path()
VALUES_FILE = VALUES_ROOT / "traefik" / "values.yaml"
HELMFILE_MANIFEST = VALUES_ROOT / "helmfile.yaml.gotmpl"

# The provisioner names kubementat's clusters actually use: k3s's default, and the value
# `TEKTON_KUBERNETES_STORAGE_CLASS` answers as in `constants.DEFAULT_STATIC_ANSWERS`. Written as concatenation
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
# tests/uninstall/test_uninstall_sweeps_everything_kubementat_owns.py.
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
    """`(path, value)` for every mapping key in the document, leaves included."""
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
    """`service.spec.type`, with the old key gone rather than kept next to it."""
    service = values()["service"]

    assert service.get("spec", {}).get("type"), \
        "the Service type has to sit under `spec`, where chart v40+ reads it"
    assert "type" not in service, \
        "`service.type` is silently ignored; leaving it beside the real key is the trap, not a fix"


def test_the_values_file_never_names_a_storage_class():
    """No volume and therefore no StorageClass -- not even as a word that names one."""
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
    """The shipped half of the rule, resolved rather than asserted."""
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
    """A `REPLACEME_*` in a values file ships to the cluster as that exact string."""
    assert NOT_A_VALUES_VALUE.search(VALUES_FILE.read_text(encoding="utf-8")) is None, \
        "this file is plain YAML: nothing rewrites it, so a token is a value"


# --- what must not be there, stated while the file is new -------------------------------


def test_no_node_port_is_claimed_and_nothing_internal_is_reachable_from_outside():
    """`hostPort` is the knob that collides with whatever already holds 80/443."""
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
    """`additionalProperties: false` at the chart's root: a key it does not know is not ignored."""
    written = set(values())

    assert written <= CHART_42_ACCEPTED_ROOT_KEYS, (
        f"{sorted(written - CHART_42_ACCEPTED_ROOT_KEYS)} are not keys of "
        "traefik/traefik 42.1.3, whose schema forbids extra properties at the root; "
        "the apply fails instead of installing"
    )


def test_an_object_the_schema_makes_a_key_mandatory_is_never_written_half_set():
    """An object that is opened is a promise that its required keys are there."""
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
    """The class is config, and this would be the second place it is decided."""
    assert "name" not in values().get("ingressClass", {}), \
        "the class name is helmfile's to pass, read from the environment's static.json"
