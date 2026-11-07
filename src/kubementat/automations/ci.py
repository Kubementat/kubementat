"""The CI container: what `kmt run-ci-container` asks the cluster for.

`scripts/utilities/run_ci_container.sh` was 34 lines of `jq` plus one `kubectl run`. It is
replaced here for the same reason every other script was: the CLI had to be able to name its
own arguments, and a script that takes `$1`/`$2` cannot say what happens when either is empty.

Two things the script did are kept exactly:

* **It prints which cluster it is about to edit.** bash did that with `set -e` in front of a
  `kubectl config current-context` read. The print stays and the read does not: which
  cluster this is is a property of the `Context` the command resolved, so there is nothing left
  for a local kubeconfig to answer wrongly.
* **The container is interactive.** `kubectl run --rm -i --tty` streams the terminal, so nothing
  here may `capture=` the result: a captured `bash` is a hung session, not a silent one. It is
  also why the cluster travels in the argv: a session kubementat does not address, it
  does not address.

The image is assembled from three keys of the environment's `static.json`, which is what
`Config` reads. bash read them with `jq -r`, so an absent key became the literal string `null`
and the pod went into `ImagePullBackOff` with a name that looked like a typo. Here a missing key
names itself and its file before a pod is created.
"""

import logging

from kubementat import errors
from kubementat.infrastructure import shell
from kubementat.core.identity.target import kubectl_target_args

# The pod the script always ran, under the name it always used. kubementat has never had more
# than one CI container to run, so a configurable name would be a knob for a question nobody asks.
CI_CONTAINER_NAME = "ubuntu-ci"

# What the pod runs when it starts. bash passed `--command /bin/bash`, which is an interactive
# shell and not the image's entrypoint - the point of the command is to debug, not to build.
CI_CONTAINER_COMMAND = "/bin/bash"

# `--rm` so nothing survives the session, `-i`/`--tty` so the terminal is attached.
CI_CONTAINER_FLAGS = ("--rm", "-i", "--tty")

# The three keys the image is made of, in the order they are assembled.
CI_IMAGE_KEYS = ("DOCKER_REGISTRY_BASE_URL", "TEKTON_CI_IMAGE_NAME", "TEKTON_CI_IMAGE_TAG")


class CiUtils:
    '''
    The CI image, run as a container inside the cluster.
    '''

    def __init__(self, ctx):
        # One `Context` and no second signature: the environment and the team arrive with the
        # `Config` they are keyed by, so there is exactly one resolution of "which cluster" behind
        # every service class. This is a `require_team` for the *namespace* reason and not the
        # configuration reason: the team is the value handed to `kubectl run -n` below, and `-n
        # None` is a pod in the wrong namespace rather than an error. Nothing on this path opens
        # `platform_config/{environment}/<team>/` -- `ci_image` reads `env_static` for the image
        # reference -- so the reason says the namespace and not a file. The `Config` itself is the
        # lazy construction `core/identity/registry.py` builds it with, `preload_configs=False`: not
        # reading a file until it is asked for, and every read going through `Config.get`, which
        # names the file a missing key lives in.
        self.environment = ctx.environment
        self.team = ctx.require_team("the namespace `-n` puts the CI container in")
        self.config = ctx.config
        # Carried because `kubectl run` is a session and stays kubectl, so the target has
        # to travel with it rather than be re-resolved at the one call site that needs it.
        self.context = ctx

    def ci_image(self) -> str:
        '''
        The image reference the environment configures: `<registry>/<name>:<tag>`.

        Returns:
            str: The full image reference.

        Raises:
            kubementat.errors.CiImageNotConfiguredError: If any of the three keys is absent or empty. A pull
                failure on somebody else's cluster is not how that is allowed to look.
        '''
        env_static = self.config.get('env_static')
        missing = [key for key in CI_IMAGE_KEYS
                   if not str(env_static.get(key) if env_static.get(key) is not None else "").strip()]
        if missing:
            raise errors.CiImageNotConfiguredError(missing, self.config.env_static_config_path)
        registry = str(env_static["DOCKER_REGISTRY_BASE_URL"]).rstrip("/")
        return f"{registry}/{env_static['TEKTON_CI_IMAGE_NAME']}:{env_static['TEKTON_CI_IMAGE_TAG']}"

    def run_ci_container(self):
        '''
        Start the CI container in the given environment and team namespace, attached to the terminal.

        Returns:
            kubementat.infrastructure.shell.CommandResult: Whatever `kubectl run` did once the session ended.

        Raises:
            kubementat.errors.CiImageNotConfiguredError: If the image is not configured, before any pod is created.
            kubementat.errors.CommandExecutionError: If `kubectl` refuses the pod. A non-zero exit
                here is the container's own exit code, so an interactive session the operator left
                is reported, not swallowed.
        '''
        image = self.ci_image()
        # Which cluster this is used to be a `kubectl config current-context` read, which answered
        # from whatever the local kubeconfig said and not from what this command decided. The
        # answer is a property of the Context, so there is no read left to do and nothing that can
        # fail between the print and the pod.
        target = self.context.require_target("the cluster the CI container is started in")

        logging.info("#########################")
        logging.info(f"Running the CI container in environment '{self.environment}' "
                     f"(context: {target.context}, address {target.address}, "
                     f"resolved from {target.source})")
        logging.info(f"IMAGE: {image}")
        logging.info("#########################")

        # The same pod, the same flags, the same order the script assembled - `kubectl run` is
        # picky about argument position, and an argv list that reorders them is a different pod.
        # The cluster comes first because this is a session, it stays kubectl, and an unaddressed
        # kubectl is the bug this design is built to make impossible.
        argv = (["kubectl", *kubectl_target_args(target), "run", CI_CONTAINER_NAME,
                 "-n", str(self.team)]
                + list(CI_CONTAINER_FLAGS)
                + [f"--image={image}", "--command", CI_CONTAINER_COMMAND])
        return shell.run(argv)
