"""`kmt tunnel grafana`, `kmt tunnel tekton`, `kmt tunnel openbao`.

Each function is two configuration reads and one port-forward. The namespace and the service
selector come from `Config.component_namespace` and `Config.component_service_selector`, so a
renamed namespace moves the tunnel with it, and a component that is not installed is a
`kubectl get pods` that finds nothing rather than a guess about where it might have been.
"""


import logging

from kubementat.infrastructure.kubernetes import KubernetesUtils


def open_tunnel_grafana(ctx, local_port=3001, remote_port=3000, host='0.0.0.0'):
    '''
    Opens a tunnel to the Grafana dashboard.

    Parameters:

    - local_port: int = 3001 - Local port for tunnel
    - remote_port: int = 3000 - Remote port (Grafana default)
    - host: str = '0.0.0.0' - Host IP

    Returns:

    - None (performs side-effect operations)

    Raises:

    - Exception - If tunneling fails
    
    '''

    config = ctx.config
    kubernetes_utils = KubernetesUtils(ctx)

    namespace = config.component_namespace('grafana')
    selector = config.component_service_selector('grafana')
    pod_name = kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
    return kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)


def open_tunnel_tekton_dashboard(ctx, local_port=9097, remote_port=9097, host='0.0.0.0'):
    '''
    Opens a tunnel to the Tekton dashboard.

    Parameters:

    - local_port: int = 9097 - Local port for tunnel
    - remote_port: int = 9097 - Remote port (Tekton default)
    - host: str = '0.0.0.0' - Host IP

    Returns:

    - None (performs side-effect operations)

    Raises:

    - Exception - If tunneling fails

    '''

    config = ctx.config
    kubernetes_utils = KubernetesUtils(ctx)

    # 'tekton', not 'tekton-dashboard': the dashboard release lands in the tekton
    # namespace, so namespace and selector belong to the same component.
    namespace = config.component_namespace('tekton')
    selector = config.component_service_selector('tekton')
    pod_name = kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
    return kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)



def open_tunnel_openbao(ctx, local_port=8205, remote_port=8200, host='0.0.0.0'):
    '''
    Opens a tunnel to the OpenBao service.

    Parameters:

    - local_port: int = 8205 - Local port for tunnel
    - remote_port: int = 8200 - Remote port (OpenBao's API port, the same 8200 Vault answered on)
    - host: str = '0.0.0.0' - Host IP

    Returns:

    - None (performs side-effect operations)

    Raises:

    - Exception - If tunneling fails
    '''

    config = ctx.config
    kubernetes_utils = KubernetesUtils(ctx)

    # 'openbao', not 'vault': the component that answers on 8200 now is the fork, and both
    # halves of the lookup come from the same configuration file for the same reason every
    # other component's do. Keeping the old key would send the tunnel to a namespace nothing
    # creates any more.
    namespace = config.component_namespace('openbao')
    selector = config.component_service_selector('openbao')
    pod_name = kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
    return kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)
