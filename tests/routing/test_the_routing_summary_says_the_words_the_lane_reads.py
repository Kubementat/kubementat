"""The lane reads a sentence out of `kmt routing install`'s output, so that sentence is a contract.
"""

from kubementat.automations.routing import routing, traefik_presence

# What `tests/e2e/harness/stages.py` looks for. If this line changes there, change it here first and
# watch this test fail; that is the whole mechanism.
LANE_MATCHES_ON = "was not applied"


def _result_for_skip(installs_traefik, classification, update=False):
    """Build the result object the skip path builds, without touching a cluster."""
    return routing.RoutingInstallResult(
        environment="dev",
        namespace="traefik",
        ingress_class="traefik",
        classification=classification,
        action=routing.ACTION_SKIP,
        installs_traefik=installs_traefik,
        label_filter=routing.SKIP_LABEL_FILTER,
        update=update,
        crds_applied=False,
        releases_applied=True,
        issuers_applied=True,
        consent=routing.CONSENT_NOT_NEEDED,
        presence=None,
    )


def test_the_summary_holds_the_phrase_the_routing_lane_matches_on():
    """The skip path's sentence, on the object the real decision produces."""
    assert LANE_MATCHES_ON in _result_for_skip(False, traefik_presence
                                               .CLASSIFICATION_FOREIGN).summary()
