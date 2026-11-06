"""The kubectl port-forward, held open until the operator leaves it.

One call, one rule: a tunnel that fails says so with an exit code and not a traceback, because
the person it failed for is standing at a terminal and not reading a stack.
"""

import logging

from kubementat import errors, shell
from kubementat.infrastructure.kubernetes.clients import KubernetesClients




class KubernetesTunnel(KubernetesClients):
    """The port-forward: one pod's port on a local address, until the operator leaves.

    Composed into `KubernetesUtils`. It is its own area because it is the only thing in the
    package meant to block, and a tunnel that reports its failure as a traceback is a tunnel
    nobody can use.
    """
    # ATTENTION: This will stall the current process until the tunnel is closed
    def open_tunnel(self, namespace, pod_name, local_port, remote_port, host='0.0.0.0'):
        argv = [
            "kubectl",
            *self.kubectl_args,
            "--namespace",
            namespace,
            "port-forward",
            "--address",
            host,
            f"pod/{pod_name}",
            f"{local_port}:{remote_port}",
        ]
        try:
            logging.info(f"Opening tunnel to pod: {pod_name} in namespace: {namespace} on local port: {local_port} pointing to remote pod port: {remote_port}")
            logging.info("###########################")
            logging.info(f"Local address: http://localhost:{local_port}")
            logging.info("###########################")
            logging.info("The tunnel will be closed when you press Ctrl+C")
            # capture=False: port-forward is interactive. Capturing its output is what used to
            # make the tunnel look frozen — nothing reaches the terminal until it exits.
            shell.run(argv, capture=False, check=True)
            return 0
        except errors.CommandExecutionError as error:
            logging.error(f"An error occured while opening a tunnel to pod: {pod_name} in namespace: {namespace}.")
            logging.error(error)
            return 1
