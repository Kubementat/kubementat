"""Grafana's admin account comes from `static.encrypted.json`, and kubementat says so before it
applies anything.

The pair used to be read out of that file and assigned to the process environment without being
looked at, which had two consequences. A value the operator exported in a shell was overwritten, so
the template's own header comment ("export GRAFANA_ADMIN_USER=...") was advice that could not be
followed — and a configuration that named no admin reached a child process as `None`, which is a
`TypeError` rather than a message. Both halves are settled now: the file is the source of truth, and
an unset key is caught here, where the file is known, instead of two frames away inside helmfile.

The tests are deliberately about the moment of the decision, not the string in a template: what is
applied, what is exported, and what is said when nothing may be applied.
"""

import json
import logging

import pytest
from click.exceptions import ClickException

from cluster_fixture import configuration_only_context
from asset_paths import values_path
from kubementat import helm_utils
from kubementat.core import constants
from kubementat.config import component_config_key
from kubementat.config_init import init_configuration
from kubementat.helm_utils import HelmUtils

LABEL_FILTER = "group=standard"
CONFIGURED_USER = "grafadmin"


class RecordingShell:
    """`kubementat.shell` for these tests: remembers argv, runs nothing."""

    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        # The cluster read a helmfile apply makes before it applies, answered as a cluster holding
        # no releases. It is the state these fixtures were in before the manifest moved into the
        # package -- nothing could be read out of it, so nothing was asked -- and these tests are
        # about which grafana credentials helmfile is handed, not about what the cluster already has.
        return type("Result", (), {"returncode": 0, "ok": True, "argv": argv,
                                   "stdout": "[]" if argv and argv[0] != "helmfile" else "",
                                   "stderr": ""})()


def a_helmfile_that_reads(encrypted, monkeypatch, tmp_path):
    """A `HelmUtils` whose environment config contains exactly `encrypted`.

    Only the accessors `helmfile_apply` actually uses are present. Everything else about `Config`
    is a detail these tests should not have to survive.
    """

    class ScratchConfig:
        def __init__(self, environment, team, preload_configs=False):
            self.kubementat_main_dir = str(tmp_path)
            self.platform_config_dir = str(tmp_path / "platform_config")
            # `Config` carries the packaged helmfile root, and this fake is answering for
            # `Config`; leaving the attribute out is a gap in the fake, not a fact about the apply.
            self.helmfile_asset_dir = str(values_path())

        def get(self, key):
            return dict(encrypted) if key == "env_static_encrypted" else {}

        def component_namespace(self, component):
            return constants.DEFAULT_NAMESPACES[component]

        # And the two per-release knobs. Nothing here declares them, which is what the real `Config`
        # answers for a tree that names no knob, so the export helmfile_apply now runs writes no
        # variable and `installed` falls to the manifest's own `default "true"`. These tests are
        # about the admin pair, and this is the one line that keeps them about that.
        def component_release_enabled(self, component):
            return None

        def component_release_version(self, component):
            return None

        # ...and the routing class the traefik release names, answered as L0 for the same reason: this
        # fake is standing in for a `Config` that declares nothing, and `helmfile_apply` asks for that
        # answer on the way to the grafana check that is what these tests are about.
        def component_ingress_class(self, component):
            return constants.static_answer(component_config_key(component, "INGRESS_CLASS"))

    for key in ["GRAFANA_ADMIN_USER", "GRAFANA_ADMIN_PASSWORD"]:
        monkeypatch.delenv(key, raising=False)
    for component in constants.DEFAULT_NAMESPACES:
        monkeypatch.delenv(component_config_key(component, "DEPLOYMENT_NAMESPACE"), raising=False)
    shell = RecordingShell()
    monkeypatch.setattr(helm_utils, "shell", shell)
    # The configuration these tests plant goes into the `Context` `HelmUtils` is built
    # with, instead of patching a `Config` name the class no longer reaches for. Same object, same
    # accessors, and the read under test is unchanged.
    return shell, HelmUtils(configuration_only_context(
        "dev", "dev1", ScratchConfig("dev", "dev1")))


def apply_it(utils):
    return utils.helmfile_apply(environment_name="dev", helmfile_label_filter=LABEL_FILTER)


def test_the_admin_user_the_configuration_names_is_the_one_helmfile_is_given(monkeypatch, tmp_path):
    """What is true in a shell does not outrank what is true in a file.

    The export that used to be documented is not a second authority, so the test is not "did the
    operator's value win" but "did the configuration's value win, while an operator value was
    sitting there". That is the whole decision, measured at the only place it can be.
    """
    shell, utils = a_helmfile_that_reads({"GRAFANA_ADMIN_USER": CONFIGURED_USER,
                                   "GRAFANA_ADMIN_PASSWORD": "configured-password"},
                                  monkeypatch, tmp_path)
    monkeypatch.setenv("GRAFANA_ADMIN_USER", "what-an-operator-exported")

    apply_it(utils)

    assert shell.calls, "helmfile was expected to run and did not, so nothing is being tested"
    import os
    assert os.environ["GRAFANA_ADMIN_USER"] == CONFIGURED_USER, \
        "the exported value won over the file that decides"
    assert "helmfile" in shell.calls[-1]


@pytest.mark.parametrize("value", [None, "", "   "], ids=["absent", "empty", "blank"])
def test_an_environment_that_names_no_admin_user_applies_nothing(monkeypatch, tmp_path, caplog,
                                                                  value):
    """No admin, no install, and not one release touched on the way to that conclusion.

    `requiredEnv` in the template would have aborted the file, and the assignment that used to
    precede it would have raised `TypeError` on `None`. Neither is a decision kubementat made, and
    both happen after work the operator has to undo.
    """
    encrypted = {"GRAFANA_ADMIN_PASSWORD": "still-a-secret"}
    if value is not None:
        encrypted["GRAFANA_ADMIN_USER"] = value
    shell, utils = a_helmfile_that_reads(encrypted, monkeypatch, tmp_path)

    with caplog.at_level(logging.WARNING), pytest.raises(ClickException) as raised:
        apply_it(utils)

    assert not shell.calls, "it refused and then applied anyway"
    assert "GRAFANA_ADMIN_USER" in str(raised.value)
    assert "static.encrypted.json" in str(raised.value), \
        "the message has to name the file the operator can fix, not the exception it avoided"
    assert any("GRAFANA_ADMIN_USER" in record.message for record in caplog.records), \
        "the failure is also warned about, so a log without the CLI's last line still says what is wrong"


def test_a_missing_password_stops_the_install_it_would_have_failed(monkeypatch, tmp_path, caplog):
    """An absent password is refused, and this is a correction of the file's own earlier claim.

    This test used to assert the opposite: warn, assign an empty string to the environment, and keep
    going, on the theory that kubementat does not render the password itself. The shipped template

        - adminPassword: {{ requiredEnv "GRAFANA_ADMIN_PASSWORD" | quote }}

    says that theory is false. `requiredEnv` aborts the whole helmfile file, so the warning's promise --
    "the release will install, but kubementat cannot read grafana's API" -- was the one thing that was
    not going to happen. A warning that describes an outcome the code prevents is a bug, not a
    nicety, and this is where the record says so.

    The assert is that nothing is applied. Refusing first also means the operator gets kubementat's
    sentence, which names the configuration file, instead of helmfile's, which names a line number.
    """
    shell, utils = a_helmfile_that_reads({"GRAFANA_ADMIN_USER": CONFIGURED_USER}, monkeypatch, tmp_path)

    with caplog.at_level(logging.WARNING), pytest.raises(ClickException) as raised:
        apply_it(utils)

    assert not shell.calls, "it warned that nothing would be applied, and then applied it"
    message = str(raised.value)
    assert "GRAFANA_ADMIN_PASSWORD" in message
    assert "static.encrypted.json" in message, \
        "the refusal must name the file, which is the one thing helmfile's own message cannot"
    assert "config set" in message and "--encrypted" in message, \
        "a refusal that names no verb is a referral, not an answer"


def test_the_generated_key_is_what_keeps_a_fresh_tree_out_of_this_refusal(tmp_path):
    """`config init` generates this key, so the refusal above is for an edited tree and not a built one.

    If init and the template ever diverge this is the assert that says so: a freshly initialized tree
    has to arrive at helmfile with both halves present, and the alternative is every fresh install
    failing the way the first real VM run did.
    """
    root = tmp_path / "cfg"
    init_configuration(root=root, environment="dev")

    written = json.loads((root / "dev" / "static.encrypted.json").read_text(encoding="utf-8"))

    assert written["GRAFANA_ADMIN_USER"] == "grafadmin"
    assert written["GRAFANA_ADMIN_PASSWORD"].strip(), \
        "init generated no password; helmfile's requiredEnv will abort this tree's first install"
    assert written["GRAFANA_ADMIN_PASSWORD"] != "grafadmin", \
        "the generated secret reused the seeded admin name, which is not a secret at all"


def test_the_template_no_longer_shows_an_export_as_the_way_to_set_the_admin():
    """The header comment is documentation, and documentation that contradicts the code is a bug.

    It used to tell an operator to `export` the pair. Nothing enforces that line except this test,
    so it is stated where a reader of the template would otherwise repeat it.
    """
    template = values_path("helmfile.yaml.gotmpl")
    text = template.read_text()

    assert "export GRAFANA_ADMIN_USER=" not in text, \
        "the template is advising a second source for a value that has one"
