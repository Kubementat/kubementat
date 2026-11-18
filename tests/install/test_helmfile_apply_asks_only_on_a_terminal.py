"""Who answers helmfile's `[y/n]`: a terminal does, a script does not."""

import types
from dataclasses import replace

import pytest
from asset_paths import values_path
from click.testing import CliRunner
from cluster_fixture import DEV_SERVER, stamp_tree, write_environment, write_kubeconfig

from cli.commands import helm
from kubementat.core import constants
from kubementat.core.config.keys import component_config_key
from kubementat.core.identity.context import Context
from kubementat.infrastructure import helm as helm_utils
from kubementat.infrastructure.helm import HelmUtils

LABEL_FILTER = "group=routing"
# The team this scratch tree is written for. `kmt helm apply` takes no `--team` any more, and the
# reason is this file's own subject: the command derives every path it uses from the environment, so
# the option resolved nothing and the guard in front of it refused an apply that was about to read
# exactly one file. The name is kept because `ScratchConfig` below is built with a team the way the
# real `Config` would be, and because a team-less apply is what every test here now runs.
TEAM = "routing-team"


class RecordingShell:
    """Stands in for `kubementat.infrastructure.shell`: remembers the argv it was handed."""

    def __init__(self):
        self.calls = []

    def run(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv and argv[0] == "helmfile":
            return types.SimpleNamespace(returncode=0, ok=True, argv=argv, stdout="", stderr="")
        # The cluster reads an apply makes on the way to the apply (`helm list`, to refuse moving a
        # release that is already running). Earlier nothing asked, because the fixture had no
        # manifest to read releases out of; a cluster holding no releases is that same state, and
        # the recorder that answers "none" is answering the question rather than dodging it.
        return types.SimpleNamespace(returncode=0, ok=True, argv=argv, stdout="[]", stderr="")


class _Stream:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


class _Stdio:
    """A `sys` holding only the stdin the terminal check asks about."""

    def __init__(self, tty):
        self.stdin = _Stream(tty)


class HelmfileApply:
    """`kmt helm apply` in a scratch checkout, reporting the argv it produced."""

    def __init__(self, recorder, monkeypatch, context):
        self._recorder = recorder
        self._monkeypatch = monkeypatch
        self._context = context

    def helm_utils(self):
        """The `HelmUtils` this fixture installed, for the tests that call the library and not the CLI.
        """
        return helm.HelmUtils(self._context)

    def argv_for(self, extra_args=(), tty=None):
        """`tty=None` means the real `sys` decides; CliRunner hands it a non-terminal."""
        if tty is not None:
            # CliRunner always swaps in a non-terminal stream, so a terminal is the one
            # condition a unit test can only inject.
            self._monkeypatch.setattr(helm, "sys", _Stdio(tty=tty))
        result = CliRunner().invoke(
            helm.helmfile_apply,
            ["dev", "--helmfile-label-filter", LABEL_FILTER] + list(extra_args))
        assert result.exit_code == 0, result.output
        return self._recorder.calls[-1]

    @property
    def last_argv(self):
        return self._recorder.calls[-1]

    def invoke(self, extra_args):
        """Raw result, for the cases where the command is expected to refuse."""
        return CliRunner().invoke(helm.helmfile_apply, ["dev"] + list(extra_args))


@pytest.fixture
def helmfile_apply(monkeypatch, tmp_path):
    """HelmUtils against a scratch platform_config, executing into a recording shell."""
    main_dir = tmp_path / "checkout"
    platform_config = main_dir / "platform_config"
    # `main_dir` used to come into being as a side effect of creating the components directory.
    # Nothing creates that any more, so the checkout the scratch tree is built in gets created here.
    main_dir.mkdir(parents=True, exist_ok=True)
    # `kmt helm apply` resolves its cluster before it builds anything, so the scratch tree
    # has to declare one. `ScratchConfig` below is what `HelmUtils` reads; this is what the
    # `Context` in front of it reads, and the two are pointed at the same tree so a test that says
    # "helmfile was applied" also says "against the cluster this root named".
    write_environment(main_dir, "dev", context="helm", kubeconfig_path=write_kubeconfig(
        main_dir / "kubeconfig", {"helm": DEV_SERVER}))
    stamp_tree(main_dir)
    monkeypatch.setenv("KUBEMENTAT_HOME", str(main_dir))
    # No root, no command. `main_dir` alone is not an answer any more.
    monkeypatch.setenv("KUBEMENTAT_PLATFORM_CONFIG_DIR", str(main_dir / "platform_config"))
    for _name in ("CLUSTER_KUBE_CONTEXT", "CLUSTER_KUBECONFIG_PATH"):
        monkeypatch.delenv(_name, raising=False)

    class ScratchConfig:
        """What `HelmUtils` reads off `Config`, without a platform_config tree."""

        def __init__(self, environment, team, preload_configs=False):
            self.kubementat_main_dir = str(main_dir)
            self.platform_config_dir = str(platform_config)
            # Same reason `Config` carries it: `helmfile_paths` resolves the manifest from the
            # package, and a fake that answers every other read but this one does not fake `Config`.
            self.helmfile_asset_dir = str(values_path())

        def get(self, key):
            # HelmUtils only asks for `env_static_encrypted` and then reads the two Grafana
            # keys out of it, so one dict answers both lookups.
            return {"GRAFANA_ADMIN_USER": "admin", "GRAFANA_ADMIN_PASSWORD": "secret"}

        def component_namespace(self, component):
            return constants.DEFAULT_NAMESPACES[component]

        # ...and the two per-release knobs, answered as nothing for the same reason: `helmfile_apply`
        # exports them from the config now, this fake declares none, and a tree that declares nothing
        # gets the manifest's default. Whether helmfile *prompts* is the only thing under test here,
        # so a knob this scratch tree has not named has to cost nothing.
        def component_release_enabled(self, component):
            return None

        def component_release_version(self, component):
            return None

        # ...and the routing class the traefik release names. Nothing in this scratch tree declares
        # it, so the export hands over kubementat's default.
        def component_ingress_class(self, component):
            return constants.static_answer(component_config_key(component, "INGRESS_CLASS"))

    recorder = RecordingShell()
    monkeypatch.setattr(helm_utils, "shell", recorder)
    # Injection goes where the command reaches, so the scratch configuration is handed in
    # at `helm.HelmUtils` — the name the command builds with — and not by patching a `Config` name
    # inside the library. That patch only ever worked because the class built its own configuration
    # there, which it no longer does.
    scratch = ScratchConfig("dev", TEAM, preload_configs=False)
    monkeypatch.setattr(helm, "HelmUtils", lambda ctx: HelmUtils(replace(ctx, config=scratch)))
    # helmfile_apply exports the release namespaces into the process environment, because that is
    # where helmfile reads them from. `monkeypatch` puts them back at teardown, so no test here
    # decides what a later one thinks `os.environ` holds.
    for component in constants.DEFAULT_NAMESPACES:
        monkeypatch.delenv(component_config_key(component, "DEPLOYMENT_NAMESPACE"), raising=False)
    return HelmfileApply(recorder, monkeypatch, Context.build("dev", TEAM))


def test_the_library_never_prompts_when_it_is_not_told_to(helmfile_apply):
    """The default any caller gets without asking, which is what install and the lane rely on."""
    helmfile_apply.helm_utils().helmfile_apply(
        environment_name="dev", helmfile_label_filter=LABEL_FILTER)

    assert "--interactive" not in helmfile_apply.last_argv


def test_the_library_prompts_when_it_is_told_to(helmfile_apply):
    helmfile_apply.helm_utils().helmfile_apply(environment_name="dev",
                                               helmfile_label_filter=LABEL_FILTER, interactive=True)

    assert "--interactive" in helmfile_apply.last_argv
