"""Sessions against a pod: the part of the package that stays with `kubectl`.

`kubectl exec --stdin --tty` and `kubectl logs -f` *are* the terminal: they own a tty and they
end when the operator interrupts them, so capturing either shows nothing until it is over. That
is why these calls survived the conversion to the client, and it is why the sessions are kept
apart from the reads -- a session that was asked to be captured is a hang.

The pod a session runs in is found through the workload reads, which is what the base class says
rather than what an import inside a method would say: the selector that finds the pod exists once
and both areas read it.
"""

import logging

from kubernetes import client

from kubementat import errors
from kubementat import shell
from kubementat.errors import NamespaceNotFoundError
from kubementat.infrastructure.kubernetes.constants import DEPLOYMENT_LABEL
from kubementat.infrastructure.kubernetes.workloads import KubernetesWorkloads


# What `kmt deploy-ssh` hands the pod when the operator does not name a command: bash, under
# the path every kubementat image has shipped. It is a path and not `sh -c ...` for the same
# reason the script it replaced was written that way -- a one-token argv entry cannot be
# re-parsed by a shell that is not there.
DEPLOYMENT_SHELL_COMMAND = ("/bin/bash",)




class KubernetesExec(KubernetesWorkloads):
    """Sessions against a running pod: a command inside it, a shell, and a deployment's logs.

    Composed into `KubernetesUtils`, and it takes the workload reads as a base rather than
    reaching across for a copy of the pod lookup. The calls here stay with `kubectl` because they
    own a terminal -- capturing them would show an operator nothing until they ended -- and
    everything that can be answered by a read has already gone to the area that owns it.
    """
    def stream_logs(self, namespace: str, selector: str, container: str = None,
                    tail_lines: int = None) -> "shell.CommandResult":
        """
        Follow the logs of everything matching a selector, straight to the terminal.

        Blocking, like :meth:`open_tunnel`: `kubectl logs -f` ends when the operator
        interrupts it, so capturing it would show them nothing until it exits. A deployment
        is addressed by its `app.kubernetes.io/instance` label, which is what `selector`
        is for.

        Args:
            namespace (str): Namespace to read logs from.
            selector (str): Label selector selecting the deployment's pods.
            container (str, optional): Container within the selected pods.
            tail_lines (int, optional): Lines from the end to start with.

        Returns:
            shell.CommandResult: Whatever `kubectl logs` reported once it stopped.
        """
        argv = [
            "kubectl",
            *self.kubectl_args,
            "--namespace", namespace,
            "logs",
            "--selector", selector,
            "--timestamps=true",
            "--follow",
        ]
        if container:
            argv += ["--container", container]
        if tail_lines:
            argv += ["--tail", str(tail_lines)]
        logging.info(f"Streaming logs for '{selector}' in namespace '{namespace}' (Ctrl+C to stop)")
        return shell.run(argv, capture=False, check=True)

    def exec_in_pod(self, namespace: str, pod_name: str, command: list,
                    container: str = None, interactive: bool = True,
                    capture: bool = False) -> "shell.CommandResult":
        """
        Run a command inside a container of a pod.

        Args:
            namespace (str): Namespace of the pod.
            pod_name (str): Pod to run in, usually from :meth:`get_pod_name_for_deployment`.
            command (list): The command and its arguments, one entry each. Never a string.
            container (str, optional): Container to run in, required for a multi-container pod.
            interactive (bool): Give the child stdin and a tty, which is what a shell needs.
                A caller running non-interactively should pass False; the CLI decides that
                from the terminal, not this method.
            capture (bool): Collect the output instead of letting it stream.

        Returns:
            shell.CommandResult: The command's verdict and, when captured, its output.
        """
        argv = ["kubectl", *self.kubectl_args, "--namespace", namespace, "exec"]
        if interactive:
            argv += ["--stdin", "--tty"]
        argv.append(pod_name)
        if container:
            argv += ["--container", container]
        argv.append("--")
        argv += list(command)
        return shell.run(argv, capture=capture, check=True)

    def open_deployment_shell(self, namespace: str, deployment: str, container: str = None,
                              command: list = None) -> "shell.CommandResult":
        '''
        Put the operator in a shell inside one pod of a deployment, until they leave it.

        This is `ssh_into_helm_deployment.sh`: find the pod, run a shell in it. Both halves are
        reused rather than rewritten, because the pod lookup already knows the answer that is
        easy to get wrong -- a deployment is addressed by its helm release name in
        `app.kubernetes.io/instance` (`DEPLOYMENT_LABEL`), and a second selector written here
        would find the pods a manual `kubectl label` left behind. `command` exists so a command
        other than a shell can be run through the same lookup; nothing today needs it, and the
        default is what the script always ran.

        The call is not captured. `kubectl exec --stdin --tty` is the terminal, and capturing it
        would show the operator nothing until the session ended -- which is when they press
        Ctrl+D, not when the command inside the pod finishes.

        Args:
            namespace (str): Namespace the deployment lives in.
            deployment (str): Deployment to attach to, addressed by its release name.
            container (str, optional): Container to enter, required for a multi-container pod.
            command (list, optional): What to run, one entry per argument.
                Defaults to :data:`DEPLOYMENT_SHELL_COMMAND`.

        Returns:
            shell.CommandResult: The verdict of the session, once the operator has left.

        Raises:
            NamespaceNotFoundError: When the namespace was never created. Checked first, so a
                typo does not arrive as "no pod found" for a namespace that has no pods because
                it has no anything.
            PodNotFoundError: When the namespace is there and the deployment is not.
        '''
        self._require_namespace(namespace)
        pod_name = self.get_pod_name_for_deployment(
            namespace, f"{DEPLOYMENT_LABEL}={deployment}")
        logging.info(f"Attaching a shell to '{pod_name}' in namespace '{namespace}'"
                     f"{f' (container {container})' if container else ''}. It ends when you exit.")
        return self.exec_in_pod(namespace, pod_name, list(command or DEPLOYMENT_SHELL_COMMAND),
                                container=container, interactive=True, capture=False)

    def stream_deployment_logs(self, namespace: str, deployment: str, container: str = None,
                              tail_lines: int = None) -> "shell.CommandResult":
        '''
        Follow the logs of a deployment, to the terminal, until the operator interrupts it.

        The other half of what the two helm scripts did: `tail_logs_for_helm_deployment.sh` was
        one `kubectl logs -l 'app.kubernetes.io/instance'=<name> --timestamps=true -f`, which is
        :meth:`stream_logs` with the selector that names a deployment filled in. Like the shell,
        this blocks and is not captured -- Ctrl+C is how it ends.

        Args:
            namespace (str): Namespace the deployment lives in.
            deployment (str): Deployment to read logs from, addressed by its release name.
            container (str, optional): Container within the selected pods.
            tail_lines (int, optional): Where to start. Absent means from the beginning, which
                is what the script did and what someone debugging a failed rollout wants.

        Returns:
            shell.CommandResult: The verdict of `kubectl logs`, once it stopped.

        Raises:
            NamespaceNotFoundError: When the namespace was never created, rather than as a
                `kubectl logs` failure that names only the namespace in passing.
        '''
        self._require_namespace(namespace)
        return self.stream_logs(namespace, f"{DEPLOYMENT_LABEL}={deployment}",
                                container=container, tail_lines=tail_lines)

    def _require_namespace(self, namespace: str) -> None:
        '''
        Stop a namespace that is not there before anything reaches for its pods.

        Both halves of these two commands read through `kubectl` (`get pod -l`, `logs -l`), and
        kubectl answers a namespace that does not exist with an error that could mean anything.
        The client can answer it exactly, so it is asked first and the answer goes out as
        :class:`NamespaceNotFoundError` -- the same sentence every other namespace-typing
        command gives.

        This is a read the scripts did not make. It is also the only read here that says "the
        namespace is missing" in those words, which is why it is worth the one call.

        Args:
            namespace (str): Namespace to confirm.

        Raises:
            NamespaceNotFoundError: When the cluster says it has no such namespace.
        '''
        try:
            self.core_api.read_namespace(name=namespace)
        except client.exceptions.ApiException as error:
            if error.status == 404:
                raise NamespaceNotFoundError(
                    f"Namespace '{namespace}' does not exist on this cluster.") from error
            raise
