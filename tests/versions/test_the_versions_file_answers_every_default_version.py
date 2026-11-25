"""`versions.yaml` is the owner of every default version, and its reader answers every key asked."""

import pytest

from kubementat.core import constants, versions
from kubementat.core.versions import _main
from kubementat.errors import VersionNotDeclaredError

# The keys the consumers actually ask for. The list is the consumer census, not a wish list: a key
# here exists because some `src/` reader opens it, and an accessor that answered for a key nobody
# asks about would be testing the file's imagination.
CONSUMERS = {
    "binaries": (versions.binary_version, ("helm", "helmfile")),
    "tekton": (versions.tekton_version, ("pipeline", "dashboard", "triggers")),
    "images": (versions.image_ref, ("buildkit", "maven", "registry", "nginx", "alpine")),
    "e2e": (versions.e2e_version, ("k3s",)),
}


def test_the_shipped_file_answers_every_key_its_readers_ask_for():
    """The census, run against the real file: no reader is one bump away from a refusal."""
    for block, (accessor, names) in CONSUMERS.items():
        for name in names:
            value = accessor(name)
            assert isinstance(value, str) and value.strip(), f"{block}.{name} answers {value!r}"
    for release in versions.chart_names():
        assert versions.chart_version(release).strip(), f"charts.{release}.version answers nothing"
    assert versions.chart_image_tag("concourse").strip(), \
        "the concourse entry lost the image tag that must move with its chart version"
    assert versions.ci_image_args(), "the ci_image block answers nothing the build can pass"


def test_the_build_args_are_the_arg_names_of_the_block_and_every_value_is_a_string():
    """The build interface, as the file's shape rather than a copy of the Dockerfile's."""
    args = versions.ci_image_args()
    assert all(name == name.upper() for name in args), "Docker ARG names are upper-case"
    assert all(isinstance(value, str) and value for name, value in args.items()), \
        f"a build arg is not a string: {args}"


def test_the_printed_build_args_are_the_block_one_flag_per_line(capsys):
    args = versions.ci_image_args()
    _main(["--print-build-args"])
    lines = capsys.readouterr().out.splitlines()
    assert lines == sorted(f"--build-arg {name}={value}" for name, value in sorted(args.items())), \
        "the script and the file would build different images with the same entry"


def test_get_answers_one_value_the_way_a_python_consumer_answers_it(capsys):
    _main(["--get", "charts.grafana.version"])
    assert capsys.readouterr().out.strip() == versions.chart_version("grafana")


def test_the_readers_of_the_tree_answer_from_the_file_and_not_from_their_own_literals():
    """The step this file exists for: a consumer that used to copy the number now resolves it.

    A table that reads versions.py and a table that copies it look identical while both agree, so the
    equality against the file's own values is what proves the tables are readers and not owners.
    """
    assert constants.PINNED_BINARY_VERSIONS == {
        "helm": versions.binary_version("helm"),
        "helmfile": versions.binary_version("helmfile"),
    }
    for key, name in (("TEKTON_VERSION_PIPELINE", "pipeline"),
                      ("TEKTON_VERSION_DASHBOARD", "dashboard"),
                      ("TEKTON_VERSION_TRIGGERS", "triggers")):
        assert constants.DEFAULT_STATIC_ANSWERS[key] == versions.tekton_version(name)


# ---------------------------------------------------------------------------------------------
# The reader refuses, it never defaults.
# ---------------------------------------------------------------------------------------------


def test_a_key_the_file_does_not_answer_refuses_by_naming_the_file_and_the_path():
    """Both halves the operator needs: which file to open, and which line of it is missing."""
    with pytest.raises(VersionNotDeclaredError) as raised:
        versions.chart_version("no-such-release")
    message = str(raised.value)
    assert "versions.yaml" in message, message
    assert "charts.no-such-release" in message, message


def test_a_missing_leaf_key_names_the_whole_walk_not_just_the_block(monkeypatch):
    """The error is about the path that failed, because `charts.grafana` existing means nothing."""
    monkeypatch.setattr(versions, "_DOCUMENT", {"charts": {"grafana": {}}})
    with pytest.raises(Exception) as raised:
        versions.chart_version("grafana")
    assert "charts.grafana.version" in str(raised.value)


def test_a_release_that_owns_no_image_tag_of_its_own_answers_none_and_not_an_error():
    """`None` says the shape, an error says the typo; a release that is not in the file is the typo.
    """
    assert versions.chart_image_tag("prometheus") is None
    with pytest.raises(VersionNotDeclaredError):
        versions.chart_image_tag("no-such-release")


def test_a_dotted_path_that_walks_off_the_file_refuses_the_same_way():
    with pytest.raises(VersionNotDeclaredError) as raised:
        versions.get("charts.grafana.colour")
    assert "charts.grafana.colour" in str(raised.value)


def test_the_chart_names_are_the_release_set_the_configuration_walk_falls_back_to():
    """Membership is decided by the file, so the walk cannot out-rank its own L0 by guessing."""
    names = versions.chart_names()
    assert "grafana" in names and "concourse" in names
    assert all(versions.chart_version(name) for name in names)
