"""`kmt users create-team-account`, `...-read-only`, `delete-team-account`.

One implementation and two Roles. The bash had two near-identical 96-line scripts that differed by
the two words `namespace-admin` and `namespace-readonly`, which is how one of them came to be fixed
and the other not, so the only difference the Python keeps is which role name and which rules get
passed down.

The namespaces an account is bound in come from the team's own configuration, through the same
strict reader the team-account commands have always needed: a Role bound in a namespace nobody
named is an account that silently does nothing, and a namespace a file never set is not kubementat's
to bind anything in.
"""


import logging

from kubementat.infrastructure import user_management
from kubementat.infrastructure.kubernetes import KubernetesUtils

from kubementat.automations.teams import team_namespaces


def create_team_account(ctx, service_account_name: str, assume_yes: bool = False) -> None:
    """Create a team account that can do anything inside the team's namespaces.

    Parameters:

    - service_account_name: str - Name the account is created under.
    - assume_yes: bool = False - Skip the confirmation (`kmt users create-team-account --yes`).
    """
    _ensure_team_account(ctx, service_account_name,
        role_name=user_management.NAMESPACE_ADMIN_ROLE,
        rules=user_management.ADMIN_RULES,
        assume_yes=assume_yes,
        action=f"create the team account '{service_account_name}' on this cluster",)


def create_team_account_read_only(ctx, service_account_name: str, assume_yes: bool = False) -> None:
    """Create a team account that can read everything and change nothing.

    The only difference from `create_team_account` is which Role gets bound, so there is
    one implementation. The bash had two 96-line scripts that differed by the two words
    `namespace-admin` and `namespace-readonly`, which is how one of them came to be fixed
    and the other not.

    Parameters:

    - service_account_name: str - Name the account is created under.
    - assume_yes: bool = False - Skip the confirmation (`kmt users create-team-account-read-only --yes`).
    """
    _ensure_team_account(ctx, service_account_name,
        role_name=user_management.NAMESPACE_READ_ONLY_ROLE,
        rules=user_management.READ_ONLY_RULES,
        assume_yes=assume_yes,
        action=f"create the read-only team account '{service_account_name}' on this cluster",)


def _ensure_team_account(ctx, service_account_name, role_name, rules, assume_yes, action) -> None:
    """Write a team account and the Roles that say what it may do.

    The order is the bash's, because the order is what makes the result readable: name
    the cluster and check the verbs, name the namespaces, create the account and its
    token, create the Roles, bind them, show what is now there, hand over the kubeconfig.

    Raises:
    - click.ClickException: Refused at the confirmation, or a verb the cluster will not
      grant, or a namespace the team's configuration did not name.
    """

    environment = ctx.environment
    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)

    user_management.check_cluster_and_access(kubernetes_utils, assume_yes, action)
    pipeline_ns, app_ns = team_namespaces(ctx, team)
    tunnels = _team_account_tunnel_roles(ctx)
    present = set(kubernetes_utils.list_namespaces())

    logging.info("#########################")
    logging.info(f"Environment: {environment} | Team: {team}")
    logging.info(f"namespace: {app_ns}")
    logging.info(f"pipeline namespace: {pipeline_ns}")
    logging.info("#########################")

    for namespace in (pipeline_ns, app_ns):
        kubernetes_utils.create_namespace(namespace)

    logging.info(f"Configuring serviceaccount: {service_account_name} in namespace: {app_ns}")
    kubernetes_utils.create_or_replace_service_account(app_ns, service_account_name)
    secret_name = user_management.service_account_secret_name(service_account_name)
    kubernetes_utils.create_or_replace_service_account_token_secret(
        app_ns, secret_name, service_account_name)

    logging.info(f"Configuring role: {role_name} in namespaces: {app_ns}, {pipeline_ns}")
    bindings = []
    for namespace in (app_ns, pipeline_ns):
        kubernetes_utils.create_or_replace_role(
            namespace, role_name, user_management.role_body(role_name, rules))
        bindings.append((role_name, namespace))

    # A tunneling Role is only worth writing where the component it tunnels is installed.
    # The bash applied it unconditionally, and a Role cannot be created in a namespace
    # that is not there, so a cluster where that namespace was never made -- `extra`, or a
    # cluster that skipped `kmt install` -- could not have a team account at all.
    # That, and not the confirmation, is why `cap:create-team-account` has been red.
    # `polaris` is installed by `group=standard` since 2026-11-01, so on a normal lane run this
    # branch is the exception and not the rule; the guard stays, because the namespace is still
    # configuration and a renamed one may well not exist.
    for component, role in user_management.TUNNEL_COMPONENTS:
        namespace = tunnels[component]
        if namespace not in present:
            logging.info(f"{component} is not installed here (namespace '{namespace}' does not "
                         f"exist); not creating its {role} role. A team account on this cluster "
                         f"cannot tunnel into {component}.")
            continue
        kubernetes_utils.create_or_replace_role(
            namespace, role, user_management.role_body(role, user_management.TUNNELING_RULES))
        bindings.append((role, namespace))

    logging.info(f"Binding roles to serviceaccount: {service_account_name}")
    for role, namespace in bindings:
        binding_name = user_management.role_binding_name(role, service_account_name)
        kubernetes_utils.create_or_replace_role_binding(
            namespace, binding_name,
            user_management.role_binding_body(binding_name, role, service_account_name, app_ns))
        # The read after the write: creating a RoleBinding with the client reports nothing
        # about whether it took, and an account with no binding is an account that is
        # silently allowed to do nothing.
        kubernetes_utils.get_role_binding(namespace, binding_name)

    _print_team_account_state(ctx, [app_ns, pipeline_ns] + [n for n in tunnels.values() if n in present])
    _print_team_account_kubeconfig(ctx, service_account_name, app_ns)


def delete_team_account(ctx, service_account_name: str, assume_yes: bool = False) -> None:
    """Remove a team account and every RoleBinding that let it act.

    Both Roles are unbound whether or not this account was created by the admin or the
    read-only command: the flow does not record which one it used, and guessing would
    leave a live binding behind for a person who is no longer a person.

    Parameters:

    - service_account_name: str - Account to delete.
    - assume_yes: bool = False - Skip the confirmation (`kmt users delete-team-account --yes`).

    Raises:
    - click.ClickException: Refused at the confirmation, or a verb the cluster will not grant.
    """

    team = ctx.team
    kubernetes_utils = KubernetesUtils(ctx)

    user_management.check_cluster_and_access(
        kubernetes_utils, assume_yes,
        f"delete the team account '{service_account_name}' and everything it can do on this cluster")
    pipeline_ns, app_ns = team_namespaces(ctx, team)
    tunnels = _team_account_tunnel_roles(ctx)
    present = set(kubernetes_utils.list_namespaces())

    logging.info(f"Deleting role bindings for serviceaccount: {service_account_name}")
    for role in (user_management.NAMESPACE_ADMIN_ROLE, user_management.NAMESPACE_READ_ONLY_ROLE):
        for namespace in (app_ns, pipeline_ns):
            kubernetes_utils.delete_role_binding(
                namespace, user_management.role_binding_name(role, service_account_name))
    # The same table the create walks, so the name deleted is the name written. `tunnels`
    # is keyed by component, and a binding's name is built from the Role that tunnels it:
    # iterating the dictionary hands over `tekton` where the create wrote
    # `tekton-tunneling`, the delete then asks for a binding that never existed, and the
    # 404 that follows is one this primitive is deliberately quiet about. The account goes
    # and the tunnel stays open.
    for component, role in user_management.TUNNEL_COMPONENTS:
        namespace = tunnels[component]
        if namespace not in present:
            continue
        kubernetes_utils.delete_role_binding(
            namespace, user_management.role_binding_name(role, service_account_name))

    logging.info(f"Deleting serviceaccount: {service_account_name} from namespace: {app_ns}")
    kubernetes_utils.delete_service_account(app_ns, service_account_name)
    # The token outlives the account otherwise: the Secret holding it is what anyone with
    # the kubeconfig authenticates with, and deleting the service account does not delete
    # a Secret this flow created by hand. The bash deleted the account and left the
    # credential, so `kmt users delete-team-account` reported success while the kubeconfig
    # kept working.
    kubernetes_utils.delete_secret(
        app_ns, user_management.service_account_secret_name(service_account_name))

    _print_team_account_state(ctx, [app_ns, pipeline_ns] + [n for n in tunnels.values() if n in present])


def _team_account_tunnel_roles(ctx) -> dict:
    """Which Role tunnels which component, and in which namespace.

    The namespaces come from `Config`, not from the three `*_NAMESPACE="..."` lines the
    bash carried, so renaming a component's namespace moves the tunneling role with it.
    """

    config = ctx.config

    return {
        "tekton": config.component_namespace("tekton"),
        "grafana": config.component_namespace("grafana"),
        "polaris": config.component_namespace("polaris"),
    }


def _print_team_account_state(ctx, namespaces: list) -> None:
    """Show what is in the namespaces the account was made to use.

    The bash read them with `kubectl get`, three calls per namespace, and printed the
    tables. Here the reads go through the client and the point of them is unchanged: a
    person at the terminal sees what the command just made true, rather than what it
    claims it made true. Namespaces that are not installed are not read and are named.
    """

    kubernetes_utils = KubernetesUtils(ctx)

    logging.info("Current state of the namespaces this account touches:")
    for namespace in namespaces:
        accounts = kubernetes_utils.list_service_accounts(namespace)
        logging.info(f"namespace: {namespace}")
        for account in accounts:
            logging.info(f"  serviceaccount: {account.metadata.name}")
        for role in kubernetes_utils.list_roles(namespace):
            logging.info(f"  role: {role.metadata.name}")
        for binding in kubernetes_utils.list_role_bindings(namespace):
            logging.info(f"  rolebinding: {binding.metadata.name}")
    logging.info("")


def _print_team_account_kubeconfig(ctx, service_account_name: str, namespace: str) -> None:
    """Print the kubeconfig a human pastes into `~/.kube/config`.

    This text is the product of the command, so it is built as data and dumped, not
    interpolated. The token is read back from the Secret the controller fills in, and the
    cluster address and CA come from the kubeconfig this command is running under, because
    that is the only place they are stated correctly.
    """

    kubernetes_utils = KubernetesUtils(ctx)

    token = user_management.read_service_account_token(
        kubernetes_utils, namespace, service_account_name)
    server, authority = user_management.local_cluster_settings(kubernetes_utils)
    text = user_management.kubeconfig_for_service_account(
        ctx, service_account_name, namespace, token, server, authority)
    logging.info("###########################")
    logging.info("Please put the settings below in your ~/.kube/config file:")
    logging.info("###########################")
    logging.info(text.rstrip("\n"))
    logging.info("##########")


###########################

## uninstall pipelines
