"""Team accounts: the service accounts kubementat hands its teams to log in with.

`scripts/user_management/user_management_helpers.sh` was the only implementation of this
flow for its whole life, and `scripts/automation/tekton/create_team_account.sh` grew a
*second* `check_cluster_and_access` beside it, which is why the `--yes` escape hatch that
`390bd61` added to the helper never reached the second copy: a fix applied to one of two
implementations is a fix to neither. This module is that one implementation.

Two things are deliberately shaped differently from the bash:

* **The kubeconfig is built as a dict and dumped, not interpolated.** What the operator
  pastes into `~/.kube/config` is the product of this command. A shell heredoc interpolating
  `$(kubectl get secret ... | base64 -d)` over settings read out of a local kubeconfig makes
  the *shape* of that document a property of the quoting, so a token containing a newline
  breaks the document silently and nothing in the flow can notice. Here the document is
  data, and `yaml.safe_dump` decides how data is written.
* **The cluster's own address comes from the loaded kubeconfig, not from a second opinion.**
  bash asked `kubectl config view --flatten --minify` for the server and the CA, because the
  flow needs both and the client hides them. `--flatten` is what inlines a CA given as a
  *file* so the emitted kubeconfig is usable away from this machine; that stays.

Everything here raises; nothing here exits.
"""

import json
import logging
import sys
import time

import click
import yaml

from kubementat import shell
from kubementat.infrastructure.kubernetes import KubernetesUtils

# How long the token controller gets to fill in a Secret it was just told about. The Secret
# is created empty and the controller writes the token into it, so reading immediately can
# read nothing; bash read immediately and got nothing in return, which it wrote into a
# kubeconfig as `token: ` and never looked at again.
TOKEN_WAIT_SECONDS = 10
TOKEN_POLL_INTERVAL_SECONDS = 0.5

# The verbs the flow will need on the cluster. bash printed the answer to each of these and
# threw it away, which is the same as not checking: a "no" printed is a "no" ignored. Each
# one is answered here, and a denied verb stops the run before the first write.
#
# The resources are plural. `kubectl auth can-i update serviceaccount` sends the kind and lets
# kubectl map it through discovery; a `SelfSubjectAccessReview` is what that mapping produces, so
# the request itself has to carry the API resource name. A singular name here answers "no" on every
# cluster and fails this preflight for a permission the service account does have -- which is the
# only way this conversion could have gone quietly wrong, and the reason it is written down rather
# than discovered by the next install.
CLUSTER_ACCESS_CHECKS = (
    ("create", "namespaces"),
    ("create", "serviceaccounts"),
    ("update", "serviceaccounts"),
    ("patch", "serviceaccounts"),
    ("create", "roles"),
    ("update", "roles"),
    ("patch", "roles"),
    ("create", "rolebindings"),
    ("update", "rolebindings"),
    ("patch", "rolebindings"),
)

ADMIN_RULES = [{
    "apiGroups": ["", "rbac.authorization.k8s.io", "networking.k8s.io", "batch",
                  "extensions", "apps", "autoscaling", "tekton.dev"],
    "resources": ["*"],
    "verbs": ["*"],
}]

READ_ONLY_RULES = [
    {
        "apiGroups": ["", "rbac.authorization.k8s.io", "networking.k8s.io", "batch",
                      "extensions", "apps", "autoscaling", "tekton.dev"],
        "resources": ["*"],
        "verbs": ["get", "list"],
    },
    {
        "apiGroups": [""],
        "resources": ["pods/portforward"],
        "verbs": ["get", "list", "create"],
    },
]

# The tunneling roles exist so a team can port-forward a component's pod without owning the
# namespace it lives in. `pods` itself is only readable; `create` belongs on the
# `pods/portforward` subresource, which is the thing that actually opens the tunnel.
TUNNELING_RULES = [
    {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
    {"apiGroups": [""], "resources": ["pods/portforward"], "verbs": ["get", "list", "create"]},
]

NAMESPACE_ADMIN_ROLE = "namespace-admin"
NAMESPACE_READ_ONLY_ROLE = "namespace-readonly"
TEKTON_TUNNELING_ROLE = "tekton-tunneling"
GRAFANA_TUNNELING_ROLE = "grafana-tunneling"
POLARIS_TUNNELING_ROLE = "polaris-tunneling"

# Which Role tunnels which component, in the order the bash applied them. The namespace is
# looked up per component from `Config`; these three lines are why the bash needed a
# GRAFANA_NAMESPACE and a POLARIS_NAMESPACE variable of its own.
TUNNEL_COMPONENTS = (
    ("tekton", TEKTON_TUNNELING_ROLE),
    ("grafana", GRAFANA_TUNNELING_ROLE),
    ("polaris", POLARIS_TUNNELING_ROLE),
)


def check_cluster_and_access(kubernetes_utils: KubernetesUtils, assume_yes: bool,
                             action: str) -> None:
    """Say which cluster this is, ask, and prove the verbs the flow will need.

    `action` is what the run is about to do, phrased as a question the operator can answer.
    It is an argument rather than text hardcoded here so the three commands that call this
    can each say what they mean, which is what the duplicated bash copy could not do.

    Three exits, in this order, and all three fail closed:

    * `assume_yes` skips the prompt only. That is the scripted escape hatch the E2E lane
      needs, and it is what `--yes` means.
    * No `--yes` and no terminal means nobody is there to answer, so nothing gets edited.
      A prompt that reads EOF would be answered by the empty string and the old script's
      `set -e` would then abort it with no message at all, which is exactly how
      `cap:create-team-account` looked red without saying why.
    * A "no" cancels.

    Raises:
    - click.ClickException: On a refusal, or on a verb the cluster will not grant.
    """
    logging.info("Checking cluster access")
    # Which cluster this is has become a property of the target rather than a question asked of
    # the local kubeconfig. The old line read `config.list_kube_config_contexts()`, which answered
    # with the process's ambient current-context: on a machine whose kubeconfig says prod and whose
    # command says dev, it printed prod. What is printed now is the address and the context name of
    # the cluster this instance's client is bound to, which is the same claim the resolution report
    # makes, so the two can be compared and cannot disagree.
    target = kubernetes_utils.target
    logging.info("You are going to edit users on the following cluster: environment %s, context "
                 "%s, address %s (resolved from %s)",
                 target.environment, target.context, target.address, target.source)

    if assume_yes:
        logging.info("Skipping the cluster confirmation because --yes was given.")
    elif not sys.stdin.isatty():
        raise click.ClickException(
            f"Confirmation required, but stdin is not a terminal. Re-run with --yes for: {action}"
        )
    else:
        while True:
            answer = input(f"Do you really wish to {action}? ").strip().lower()
            if answer in ("y", "yes"):
                break
            if answer in ("n", "no"):
                raise click.ClickException("Cancelled.")
            logging.info("Please answer yes or no.")

    denied = []
    for verb, resource in CLUSTER_ACCESS_CHECKS:
        if kubernetes_utils.self_can(verb, resource):
            continue
        denied.append(f"{verb} {resource} (no)")
    if denied:
        raise click.ClickException(
            "This cluster will not let kubementat manage team accounts, so nothing was "
            "created. Missing:\n  " + "\n  ".join(denied))
    logging.info("Finished checking cluster access")


def role_body(name: str, rules: list) -> dict:
    """A namespaced Role, as data.

    The rules are arguments, not heredoc text, because `namespace-admin` and
    `namespace-readonly` differ by exactly one verb list and the bash had to type the same
    apiGroup list twice to say so.
    """
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"name": name},
        "rules": rules,
    }


def role_binding_body(name: str, role_name: str, service_account_name: str,
                      service_account_namespace: str) -> dict:
    """A RoleBinding, as data.

    The role is always a namespaced `Role`, so the `apiGroup`/`kind` pair is fixed here
    rather than threaded through every caller: the flow never binds a ClusterRole.
    """
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": name},
        "subjects": [{
            "kind": "ServiceAccount",
            "name": service_account_name,
            "namespace": service_account_namespace,
        }],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": role_name,
        },
    }


def role_binding_name(role_name: str, service_account_name: str) -> str:
    """The name this flow binds a role under. Delete has to derive it the same way."""
    return f"{role_name}-{service_account_name}-binding"


def service_account_secret_name(service_account_name: str) -> str:
    """The token Secret that carries a team account's credentials.

    Same convention the bash used, so an account created before this was Python still has a
    readable token after it.
    """
    return f"{service_account_name}-sa-secret"


def read_service_account_token(kubernetes_utils: KubernetesUtils, namespace: str,
                               service_account_name: str) -> str:
    """Ask the cluster for the token a team account authenticates with, waiting for it.

    A Secret of type `kubernetes.io/service-account-token` is created empty and filled in by
    the controller, so the first read can legitimately find nothing. The bash read once; if
    it had found nothing it would have emitted `token:` and handed the operator a broken
    kubeconfig with exit code 0.
    """
    deadline = time.monotonic() + TOKEN_WAIT_SECONDS
    while True:
        try:
            return kubernetes_utils.get_service_account_token(namespace, service_account_name)
        except Exception as error:
            if time.monotonic() >= deadline:
                raise
            logging.info(f"Token for '{service_account_name}' is not filled in yet, waiting: {error}")
            time.sleep(TOKEN_POLL_INTERVAL_SECONDS)


def local_cluster_settings(kubernetes_utils) -> tuple:
    """The server address and CA the cluster this command resolved answers to.

    Both are facts about a kubeconfig, which is why they come from `kubectl config view` rather
    than from the loaded client: the client resolves a CA given as a file into a *temporary* file,
    which is useless inside a document that is about to be pasted onto another machine.
    `--flatten` is what re-inlines those, and `--minify` what narrows the answer to one context.

    The context it narrows to is now the `Context`'s and not the process's. Without `--context`,
    `--minify` follows the kubeconfig's `current-context`, so a run that resolved `dev` while the
    local file pointed at `prod` printed a kubeconfig for `prod` and said nothing about it -- the
    same failure removed from the two `current-context` call sites, met from the other side.
    """
    result = shell.run(["kubectl", *kubernetes_utils.kubectl_args,
                        "config", "view", "--flatten", "--minify", "-o", "json"],
                       capture=True)
    try:
        clusters = json.loads(result.stdout).get("clusters") or []
    except ValueError as error:
        raise click.ClickException(f"Could not read the kubeconfig this command resolved: {error}")
    if not clusters:
        raise click.ClickException(
            "The kubeconfig this command resolved has no cluster to copy, so there is nothing to "
            "hand to the account. Run kubectl config view --minify to see what that context is "
            "configured for.")

    cluster = (clusters[0].get("cluster") or {})
    server = cluster.get("server")
    authority = cluster.get("certificate-authority-data")
    if not server or not authority:
        raise click.ClickException(
            "The kubeconfig this command resolved does not give its cluster both a server address "
            f"and a certificate-authority-data entry (server={server!r}, "
            "certificate-authority-data is "
            f"{'present' if authority else 'absent'}). Without both, the kubeconfig this "
            "prints is not something a person can paste into ~/.kube/config.")
    return server, authority


def kubeconfig_for_service_account(ctx, service_account_name: str, namespace: str, token: str,
                                   server: str, certificate_authority_data: str) -> str:
    """The kubeconfig text a team account is used with.

    Assembled as a dict and dumped, so the document's shape is a property of this function
    and not of how a shell interpolated whatever the token happened to contain.

    It takes the `Context` because the two names it derives -- the cluster and the context -- are
    named after the environment and the team, and this used to be the last place in `src/` that
    spelled those two strings into a pair and read a `Config` from them. The `require_team` here is
    the *name* reason and not the configuration reason: the team reaches exactly one line, the
    context name interpolated below, and this function opens no configuration file at all -- its
    caller has already resolved the team's configuration before it asks for a token. So the sentence
    says what is missing rather than pointing at a path: a context name with `None` in it is a
    document that names no team, and guessing one is a refusal that belongs upstream, not this
    function's to make.

    A blank token is refused rather than written. `read_service_account_token` already
    refuses to hand one back, so this is the second gate and not the only one: the text
    printed here is what a person pastes into `~/.kube/config`, and a document whose user
    authenticates with nothing is indistinguishable from a working one until the cluster
    rejects it. A `ValueError` rather than a `ClickException` because a blank token at this
    point is not a state the operator can act on -- it means the read path is broken.

    Raises:
    - ValueError: If the token is empty or all whitespace.
    """
    if not token or not token.strip():
        raise ValueError(
            f"Refusing to write a kubeconfig for '{service_account_name}' with no token. "
            "The account cannot authenticate with nothing, and an empty `token:` field reads "
            "like a working credential until something tries to use it."
        )
    environment = ctx.environment
    team = ctx.require_team("the name the account kubeconfig's context is built from")
    cluster_name = f"{environment}-cluster"
    context_name = f"{environment}-cluster-{team}-{service_account_name}"
    document = {
        "apiVersion": "v1",
        "clusters": [{
            "name": cluster_name,
            "cluster": {
                "certificate-authority-data": certificate_authority_data,
                "server": server,
            },
        }],
        "contexts": [{
            "name": context_name,
            "context": {
                "cluster": cluster_name,
                "namespace": namespace,
                "user": service_account_name,
            },
        }],
        "current-context": context_name,
        "kind": "Config",
        "preferences": {},
        "users": [{"name": service_account_name, "user": {"token": token}}],
    }
    return yaml.safe_dump(document, default_flow_style=False, sort_keys=True)
