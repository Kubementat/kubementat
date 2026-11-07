"""Does this cluster speak the API version kubementat writes in its manifests.

Two questions and one refusal: is `tekton.dev/v1` served at all, and — after an install has
applied its manifests — is it served *now*. Nothing here applies anything; the whole module is a
read of the cluster's own discovery answers, which is why it is the leaf the run path and the
install path both point at rather than a helper one of them owns.

The one non-mechanical part is where the "already asked" answer lives. It used to be a class
attribute that the first successful check shadowed onto the instance, so it was per object and the
two clusters a process can address at once each got their own answer. A module-level flag would
have thrown that property away: it says "some cluster was verified", not "this one was", and the
second cluster in a command would have been skipped without ever being asked. So the answer is
keyed on the address of the cluster it was given about, which is the field `ClusterTarget` carries
for exactly that question, and a cluster it has never met is always asked.
"""

import logging
import time

from kubementat import errors
from kubementat.infrastructure.kubernetes import KubernetesUtils


# What this process already knows it does not have to ask again about, and the reason a bare flag
# could not hold it: `preflight_tekton_api_version` answers for a cluster, and a process can hold
# more than one. Keyed by address because that is `ClusterTarget`'s own answer to "which cluster"
# — the same field the client is built from, the same field a wrong-cluster report names — and not
# by the `KubernetesUtils` instance, which two `Automation` objects would each hand over for one
# cluster. What the cache does NOT hold: an answer that was refused (the write below only happens
# after the check passed), and anything from an object that is not a `KubernetesUtils` at all,
# where there is no address to read and the object stands in for itself.
_verified_clusters = set()


def _cluster_cache_key(kubernetes_utils):
    """The identity the cached answer belongs to: the address this object addresses.

    `KubernetesUtils` never resolves a cluster of its own — it is handed a `Context` or a
    `ClusterTarget` and asks it only when a client is built — so the address is read through the
    target that answers for it. Asking that question is what the check itself does one statement
    later, when it builds a client; nothing here resolves a kubeconfig earlier than the check
    would have, and a target that cannot be resolved raises the same
    `ClusterNotConfiguredError` here as it would there.

    Anything that is not a `KubernetesUtils` (a recorder, a stand-in) carries no target, and it is
    keyed by itself: one object, one answer, which is precisely the semantics the per-instance
    attribute had. The set holds that object, so a stand-in is not reclaimed for the life of the
    process — the same trade the attribute made by hanging it off the instance.
    """
    if isinstance(kubernetes_utils, KubernetesUtils):
        address = kubernetes_utils.target.address
        if address:
            return address
    return kubernetes_utils


def _preflight_tekton_api_version(kubernetes_utils, config) -> None:
    """Check once per cluster that the controller serves what kubementat is about to use.

    The once is per cluster, not per object and not per process: two clusters in one command each
    get asked, and the same cluster reached through a second `KubernetesUtils` does not get asked
    twice. Nothing is refreshed, so a controller upgraded in the middle of one long-lived process
    is not re-verified on the next read — the answer this module keeps is the one the first check
    gave for that address.

    Args:
        kubernetes_utils: The utilities that own the client the discovery goes through, and the
            target that says which cluster the answer is about.
        config: The configuration supplying TEKTON_API_GROUP/TEKTON_API_VERSION.

    Raises:
        kubementat.errors.TektonControllerWithoutConfiguredApiError: If this cluster serves
            nothing under that group/version.
    """
    key = _cluster_cache_key(kubernetes_utils)
    if key in _verified_clusters:
        return
    preflight_tekton_api_version(kubernetes_utils, config)
    _verified_clusters.add(key)


def preflight_tekton_api_version(kubernetes_utils, config) -> None:
    """Stop here if this cluster does not serve the `tekton.dev` version kubementat uses.

    A cluster whose Tekton controller only serves `tekton.dev/v1beta1` is the failure this
    guards: every manifest would be rejected one by one, and the forty-first 404 reads
    nothing like the one-line answer. Nothing falls back - kubementat writes `v1`, so an
    older controller is simply not supported here, and the message says which knob turns
    into a supported cluster.

    Args:
        kubernetes_utils: The utilities that own the API client doing the discovery.
        config: The configuration supplying TEKTON_API_GROUP/TEKTON_API_VERSION.

    Raises:
        kubementat.errors.TektonControllerWithoutConfiguredApiError: If the cluster serves nothing under
            that group/version.
    """
    if kubernetes_utils.api_version_is_served(config.TEKTON_API_GROUP, config.TEKTON_API_VERSION):
        return

    raise errors.TektonControllerWithoutConfiguredApiError(
        f"The cluster serves no resources under {config.TEKTON_API_GROUP}/{config.TEKTON_API_VERSION}, "
        "and kubementat speaks only that API version. Nothing was applied. Install or upgrade "
        f"Tekton Pipelines with `kmt install <environment> <team>`, or set "
        f"TEKTON_VERSION_PIPELINE in platform_config/<environment>/static.json to a version that "
        f"serves {config.TEKTON_API_GROUP}/{config.TEKTON_API_VERSION} (Pipelines 1.0 and newer). "
        f"The manifests kubementat ships, and the ones under examples/tekton_ci/, are written for "
        f"it, and so is this code."
    )


# How many times, and how far apart, the post-install assertion asks the discovery question.
# `kubectl apply` returns when the object is accepted, not when the API server has opened the
# `/apis/<group>/<version>` path in front of it, so the first request straight after an install
# can answer 404 on a cluster that is about to serve it. Waiting is not a fallback: the same
# answer is demanded at the end of the wait as at the start of it.
DISCOVERY_ATTEMPTS = 6
DISCOVERY_DELAY_SECONDS = 5.0


def _served_within_a_bounded_wait(kubernetes_utils, group, version, attempts, delay_seconds) -> bool:
    """Ask the discovery question until it says yes or the attempts run out."""
    for attempt in range(attempts):
        if kubernetes_utils.api_version_is_served(group, version):
            return True
        if attempt + 1 < attempts:
            logging.info(
                f"No {group}/{version} on this cluster yet, retrying discovery "
                f"in {delay_seconds}s (attempt {attempt + 1} of {attempts})"
            )
            time.sleep(delay_seconds)
    return False


def preflight_installed_tekton_api_versions(kubernetes_utils, config,
                                           attempts=DISCOVERY_ATTEMPTS,
                                           delay_seconds=DISCOVERY_DELAY_SECONDS) -> None:
    """Stop the install if what was just applied does not serve what kubementat speaks.

    The install ends with `kubectl apply` having exited 0, which is a claim about the manifests
    it sent and not about the controller that is now running. A pin that is wrong, a release URL
    that 404s partway through, or an image that never rolls out all leave the install printing
    success and the first pipeline run printing 404s. So this asks the discovery question for both
    groups the install is supposed to have brought up, once per group, before anything else in the
    install runs.

    The message names the version the install tried to apply, because that is the half the
    operator cannot see from the cluster: the install builds its release URLs from the
    `TEKTON_VERSION_*` keys, so a mismatch is always either that pin or that controller.
    Nothing here falls back to another API version, and there is no environment variable that
    turns the assertion off.

    Args:
        kubernetes_utils: The utilities that own the API client doing the discovery.
        config: The configuration supplying the groups/versions and the pinned component versions.
        attempts: How many times to ask before believing the answer.
        delay_seconds: Seconds between attempts.

    Raises:
        kubementat.errors.TektonControllerWithoutConfiguredApiError: If the cluster serves nothing under a group/
            version kubementat needs, once the bounded wait has run out.
    """
    env_static = config.get('env_static')
    checked = [
        {
            "component": "Tekton Pipelines",
            "group": config.TEKTON_API_GROUP,
            "version": config.TEKTON_API_VERSION,
            "pin_key": "TEKTON_VERSION_PIPELINE",
            "pin": env_static.get("TEKTON_VERSION_PIPELINE"),
            "what_we_speak": "writes and reads only that API version",
            "supported": "Pipelines 1.0 and newer serve tekton.dev/v1",
        },
        {
            "component": "Tekton Triggers",
            "group": config.TRIGGERS_API_GROUP,
            "version": config.TRIGGERS_API_VERSION,
            "pin_key": "TEKTON_VERSION_TRIGGERS",
            "pin": env_static.get("TEKTON_VERSION_TRIGGERS"),
            "what_we_speak": "writes every trigger resource in that API version",
            "supported": "Triggers 0.37 serves triggers.tekton.dev/v1alpha1 and v1beta1 and no v1",
        },
    ]

    for entry in checked:
        group_version = f"{entry['group']}/{entry['version']}"
        if _served_within_a_bounded_wait(kubernetes_utils, entry['group'], entry['version'],
                                         attempts, delay_seconds):
            logging.info(f"The cluster serves {group_version}, as {entry['component']} should.")
            continue

        pinned = f"{entry['component']} {entry['pin']}" if entry["pin"] else \
            f"{entry['component']} at the version pinned in {entry['pin_key']}"
        raise errors.TektonControllerWithoutConfiguredApiError(
            f"The cluster serves no resources under {group_version} after installing "
            f"{pinned}, and kubementat {entry['what_we_speak']}. Nothing was configured. "
            f"Either the install did not land its manifests for {entry['component']}, or that "
            f"release does not serve {group_version} ({entry['supported']}). Install or upgrade "
            f"with `kmt install <environment> <team>`, or set {entry['pin_key']} in "
            f"platform_config/<environment>/static.json to a version that serves {group_version}, "
            f"then run the install again. There is no fallback API version and no override."
        )
