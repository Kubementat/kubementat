"""The lane reads a sentence out of `kmt routing install`'s output, so that sentence is a contract.

`tests/e2e/harness/stages.py` decides "was kubementat's Traefik applied?" in a skip by looking for
the phrase `was not applied` in the command's output. That is a string match against prose, and
prose is what a summary is made of, so the moment nobody protects it the failure arrives in the
expensive place: twenty-five minutes into a `--lane routing` run, three quarters of the way through
a stage, on the one assert that says a skip really skipped. Nothing in the unit suite pinned the
wording, so rewording a summary was free -- until it was not.

Pinning it is cheaper than the alternative, which is the lane matching on a machine-readable field
no one wants. `RoutingInstallResult` carries `installs_traefik`, and `--format json` could have been
made to carry it, but the lane's whole vocabulary is "run the command the operator runs and read
what it says"; a lane that ran a different command was the trade already refused in favour of
reason `status` echoes the report rather than re-summarising it). So the sentence stays, and this
file is what makes changing it a two-second failure with the lane named in it.

The point is not the exact words. It is that the two files that must agree do agree, and that the
test says so in terms of *which* consumer depends on it.
"""

from kubementat.automations.routing import routing, traefik_presence

# What `tests/e2e/harness/stages.py` looks for. If this line changes there, change it here first and
# watch this test fail; that is the whole mechanism.
LANE_MATCHES_ON = "was not applied"


def _result_for_skip(installs_traefik, classification, update=False):
    """Build the result object the skip path builds, without touching a cluster.

    Constructed directly rather than through `install_routing` because the question here is about the
    summary's wording, and a cluster read is not needed to answer it. Everything else about the
    object is what the real skip produces: `installs_traefik=False`, a foreign classification, no
    CRD step.
    """
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
    """The skip path's sentence, on the object the real decision produces.

    Built rather than run, because the failure this guards is a reword, not a misdecision -- and the
    decision itself is covered by `test_routing_install_skips_traefik_when_the_cluster_already_has_one.py`
    running against a fake cluster. What is not duplicated here is the cluster read: this object is
    the one `install_routing` returns for a foreign controller, fields and all.
    """
    assert LANE_MATCHES_ON in _result_for_skip(False, traefik_presence
                                               .CLASSIFICATION_FOREIGN).summary()


def test_an_install_that_applied_traefik_says_the_opposite_and_still_names_the_filter():
    """The other branch of the same sentence, so the pair cannot be edited in only one place.

    A summary that said `was not applied` on a run that applied Traefik would be worse than the
    reword this file exists to guard: the lane would pass while the product had installed a second
    controller. Both branches are asserted because both branches are read.
    """
    installed = _result_for_skip(True, traefik_presence.CLASSIFICATION_NONE)
    summary = installed.summary()
    assert "was applied" in summary
    assert LANE_MATCHES_ON not in summary, \
        "the skip sentence is being printed for a run that applied the release"
    assert routing.INSTALL_LABEL_FILTER in summary, \
        "the filter that decided the run is not in the output, so a scripted run cannot read it"
