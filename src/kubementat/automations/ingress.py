"""`kmt kubernetes create-ingress`: the Ingress manifest, built as data instead of printed as text.

`create_ingress_yml.sh` was the last script that answered the
ingress-class question by `jq`-ing a file out from under the directory it happened to be run from.
This replaces it. Three things it does on purpose:

*   **The manifest is a document, not a template.** `yaml.safe_dump` writes it, so a value that
    needs quoting is quoted by the same library that would have to parse it, and the fields are
    a `dict` that a caller can read as well as print. Nothing here is a `cat << EOF`.
*   **The class is answered, not recited.** `Config._component_value("traefik", "INGRESS_CLASS",
    DEFAULT_INGRESS_CLASSES)` is the read, which resolves to the `static.json` key
    `TRAEFIK_INGRESS_CLASS` through `component_config_key` -- the same key and the same fallback the
    routing command uses when it points the cert-manager solver at a
    class, so a generated Ingress and the solver that has to reach it cannot disagree. Both halves of
    that agreement now come from `constants.py`, and nothing here reaches into the routing package to
    get them. The
    fallback is `traefik` because `constants.DEFAULT_STATIC_ANSWERS` says so, and that
    table is also what the cluster's release is configured with: the traefik block in
    `helmfile.yaml.gotmpl` reads the variable `export_ingress_class` in
    `infrastructure/helm.py` puts into the
    environment and carries no `default` of its own, so a tree that configured nothing gets the same
    `traefik` in the manifest and on the cluster, and a tree that configured something gets what it
    wrote. A literal in this file would be a second answer to that question.
*   **Reading no configuration is said out loud, in two sentences.** bash warned on stderr because
    its stdout was the manifest, and a warning that lands in the YAML is a warning nobody reads.
    The same separation is kept here: `GeneratedIngress.warnings` is what the operator's terminal
    sees, and the text it returns never contains it. The two reasons the answer did not come from
    configuration -- no environment was given, or no `static.json` is at the path that environment
    names -- are two different sentences, because they have two different fixes: pass the fourth
    argument or `$ENVIRONMENT`, and run `kmt config init` or fix the path.

What is load-bearing and stays as it was, verbatim: the `cert-manager.io/cluster-issuer:
letsencrypt` annotation, `kubernetes.io/tls-acme: "true"`, and the whole `spec.tls` block with its
`<service>-cert` secret name. Both issuer names are referenced from
the two `template-event-listener-ingress.yml` files this kmt ships (under
`Config.tekton_trigger_template_dir/{github,gitlab}/`), so renaming them breaks
the webhook ingresses beside the one being generated. `tests/test_generated_ingress_manifests_name_
the_service_the_class_and_the_issuer.py` locks all of it, and it locks the *absence* too:
there is no nginx annotation in this manifest and none may appear. kubementat ships no nginx
compatibility, and the owner's decision was to delete those annotations rather
than translate them onto Traefik middlewares. The `ssl-redirect` half of that is already answered
cluster-wide by the entrypoint redirect in the Traefik values, so a per-Ingress copy of it would be
a second opinion nobody would find.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kubementat.core.constants import COMPONENT, DEFAULT_INGRESS_CLASSES
from kubementat import errors

# The Ingress this generates is a `networking.k8s.io/v1` object, which is what the script declared
# and what every cluster kubementat installs on runs.
INGRESS_API_VERSION = "networking.k8s.io/v1"
INGRESS_KIND = "Ingress"

# The issuer the annotation names, and the secret the TLS block names. Both spelled here once and
# pinned by tests against the two tekton EventListener templates, which reference the same issuer
# by string.
CLUSTER_ISSUER_NAME = "letsencrypt"
TLS_ACME_ENABLED = "true"
INGRESS_NAME_SUFFIX = "-ingress"
CERT_SECRET_SUFFIX = "-cert"

# Named so this file says `DEFAULT_INGRESS_CLASS` where it means the answer, without a literal.
# The value is `constants.DEFAULT_INGRESS_CLASSES`, read for the component that owns the key.
DEFAULT_INGRESS_CLASS = DEFAULT_INGRESS_CLASSES[COMPONENT]


@dataclass(frozen=True)
class GeneratedIngress:
    """What this run produced, and what it had to say alongside it.

    `text` is the manifest; `warnings` is what belongs in the terminal and not in the file. The
    two halves stay separate types for the reason the script gave: the one is the artifact, the
    other is a note about it, and a caller that pipes the first should not receive the second.
    """

    manifest: dict
    ingress_class: str
    answered_by: str
    warnings: tuple = field(default_factory=tuple)

    def text(self) -> str:
        """The manifest as the file it would be. Sorted keys are off: a person reads this, and
        the order the script wrote it in is the order that reads as an Ingress."""
        return yaml.safe_dump(self.manifest, sort_keys=False, default_flow_style=False)


class IngressGenerator:
    """Builds an `Ingress` manifest from configuration and its arguments.

    No cluster is read on this path. Which namespace a generated Ingress eventually lands in is
    the operator's choice at `kubectl apply` time, the same way it was when they typed
    `create_ingress_yml.sh ... | kubectl apply -f -`, and inventing a cluster read here would make
    a file generator depend on something it has no business asking. The `Context` is still the
    argument — kubementat takes one type for every class that reads configuration, and a generator
    that ignored its `target` while keeping the `(environment, team)` shape would be the second
    signature that design refused — and the half it consults is `config`, which is what answers the
    ingress-class question. `ClusterTarget` is a value it never asks anything of.
    """

    def __init__(self, ctx):
        # `environment` is what selects the `static.json` that answers the class, and it is the only
        # half of the `Context` this generator asks anything of. The team-level files `Config` says
        # are missing when no team is named are not the half that answers an ingress-class question,
        # and the guard that used to sit here said otherwise: it claimed a `Config` built without a
        # team interpolates `None` into the paths it derives, which stopped being true when
        # `Config._team_scoped_path` moved that refusal to the read. Nothing on this path performs a
        # team-scoped read, so the refusal arrived one step before a failure that could never have
        # happened, and `kmt kubernetes create-ingress` refused a team it has no key for. An empty
        # `environment` is not a default that got dropped, it is "no environment was given", and it
        # is answered in `_read_ingress_class`.
        self.environment = ctx.environment
        self.config = ctx.config

    def generate(self, domain: str, service_name: str, service_port: str) -> GeneratedIngress:
        """The manifest, the class it names, and the warnings that go with it.

        Raises:
            kubementat.errors.IngressArgumentNotSuppliedError: If `domain`, `service_name` or `service_port` is
                empty. The three are required for the same reason they were in bash's `$1..$3`.
            kubementat.errors.IngressPortNotUsableError: If `service_port` is not a port number.

        Returns:
            GeneratedIngress: `manifest` is the document, `answered_by` says whether configuration
            or kubementat's own default produced the class, and `warnings` is for stderr.
        """
        domain = _supplied(domain, "DOMAIN")
        service_name = _supplied(service_name, "SERVICE_NAME")
        service_port = _port(service_port)
        ingress_class, source, warnings = self._read_ingress_class()

        manifest = {
            "apiVersion": INGRESS_API_VERSION,
            "kind": INGRESS_KIND,
            "metadata": {
                "annotations": {
                    "cert-manager.io/cluster-issuer": CLUSTER_ISSUER_NAME,
                    "kubernetes.io/tls-acme": TLS_ACME_ENABLED,
                },
                "name": f"{service_name}{INGRESS_NAME_SUFFIX}",
            },
            "spec": {
                "ingressClassName": ingress_class,
                "rules": [{
                    "host": domain,
                    "http": {"paths": [{
                        "pathType": "Prefix",
                        "path": "/",
                        "backend": {"service": {
                            "name": service_name,
                            "port": {"number": service_port},
                        }},
                    }]},
                }],
                "tls": [{
                    "hosts": [domain],
                    "secretName": f"{service_name}{CERT_SECRET_SUFFIX}",
                }],
            },
        }
        return GeneratedIngress(manifest=manifest, ingress_class=ingress_class,
                                answered_by=source, warnings=tuple(warnings))

    def write(self, generated: GeneratedIngress, target: Path) -> Path:
        """Write `generated` to `target`, or say why it will not be.

        The directory is created rather than demanded -- bash's `>` needed it to exist already,
        which is the same trap the pipeline-run generator hit -- but a file that
        is already there is refused. A generated manifest is a starting point someone edits before
        they apply it, so a rerun that overwrote it would delete their work without a word.
        """
        target = Path(target)
        if target.exists():
            raise errors.IngressTargetExistsError(
                f"{target} already exists, and kubementat will not overwrite it. Review or remove "
                f"the file this would replace, or point the command somewhere else.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated.text())
        return target

    def _read_ingress_class(self) -> tuple:
        """The class, how it was answered, and what the operator has to be told.

        Three branches, and the difference between the last two is the whole reason the bash script
        printed a warning instead of staying quiet: `Config` reads a file, so when there is no file
        there is nothing for it to have defaulted from, and the cluster may be serving a class this
        command has no way to know about.
        """
        default = DEFAULT_INGRESS_CLASS
        if not self.environment:
            return default, "default", [
                "did not read TRAEFIK_INGRESS_CLASS - no environment was given, as the first "
                "argument or in $ENVIRONMENT",
                _default_warning(default)]
        config = self.config
        if not config.env_static_config_path.is_file():
            return default, "default", [
                f"did not read TRAEFIK_INGRESS_CLASS - there is no static.json at "
                f"{config.env_static_config_path} - has '{self.environment}' been prepared, or is "
                f"the path wrong?",
                _default_warning(default)]
        # Configuration answers when it has the key; `config.py` warns once when it does not, and
        # that is the only warning this branch produces, so a run that found its configuration says
        # nothing.
        return config._component_value(COMPONENT, "INGRESS_CLASS",
                                       DEFAULT_INGRESS_CLASSES), "configuration", []


def _default_warning(default: str) -> str:
    return (f"writing spec.ingressClassName: {default}, kubementat's default. If this cluster "
            f"serves a different class, the Ingress this writes will exist and never be routed to.")


def _supplied(value: str, name: str) -> str:
    if value is None or not str(value).strip():
        raise errors.IngressArgumentNotSuppliedError(
            f"{name} is empty. The command is "
            f"kmt kubernetes create-ingress <ENVIRONMENT> <DOMAIN> <SERVICE_NAME> <SERVICE_PORT>.")
    return str(value).strip()


def _port(value) -> int:
    text = _supplied(value, "SERVICE_PORT")
    try:
        number = int(text)
    except ValueError:
        raise errors.IngressPortNotUsableError(
            f"SERVICE_PORT is '{text}', which is not a port number. Give the port the service "
            f"listens on, e.g. 80.")
    if not 1 <= number <= 65535:
        raise errors.IngressPortNotUsableError(
            f"SERVICE_PORT is {number}, which is not a port an Ingress can route to. Use 1-65535.")
    return number
