"""Who answers helmfile's `[y/n]`: a terminal does, a script does not.

`helmfile apply` prints the diff and then asks. The CLI used to pass `interactive=True` as
its own default, so the prompt went out even when nobody could answer it — and off a terminal
helmfile reads the end of input as a refusal and exits 1. That is why every scripted use of
`kmt helm apply` failed.

The decision now lives in `kmt helm apply`, the one place that can see stdin: ask when
stdin is a terminal, do not ask when it is not, and `--yes`/`-y` is the explicit "do not ask"
for both cases. `HelmUtils.helmfile_apply` keeps a plain `interactive: bool` and appends
`--interactive` only when it is handed True.
"""

import types
from dataclasses import replace

import pytest
from click.testing import CliRunner

from cli.commands import helm
from cluster_fixture import DEV_SERVER, stamp_tree, write_environment, write_kubeconfig
from asset_paths import values_path
from kubementat import helm_utils
from kubementat.core import constants
from kubementat.cluster import Context
from kubementat.core.config.keys import component_config_key
from kubementat.helm_utils import HelmUtils

LABEL_FILTER = "group=routing"
# The team this scratch tree is written for. `kmt helm apply` takes no `--team` any more, and the
# reason is this file's own subject: the command derives every path it uses from the environment, so
# the option resolved nothing and the guard in front of it refused an apply that was about to read
# exactly one file. The name is kept because `ScratchConfig` below is built with a team the way the
# real `Config` would be, and because a team-less apply is what every test here now runs.
TEAM = "routing-team"


class RecordingShell:
    """Stands in for `kubementat.shell`: remembers the argv it was handed."""

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

        Same name the command reaches for, same patched-in scratch configuration, so a library call
        and a `kmt helm apply` in this file cannot be looking at two different configurations.
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
        """What `HelmUtils` reads off `Config`, without a platform_config tree.

        `component_namespace` is part of that surface: helmfile cannot deploy a release into a
        namespace the config does not name, so it comes from the same accessor the tunnels use.
        """

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
        # it, so the real `Config`'s answer is L0 and that is what the export hands over. Whether
        # helmfile *prompts* is the only thing under test here, so a class this tree has not named has
        # to cost nothing -- and leaving the accessor out would be a hole in the fake.
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


# ---------------------------------------------------------------------------
# the branch that used to be broken: nobody is there to answer the prompt
# ---------------------------------------------------------------------------
def test_a_run_without_a_terminal_applies_without_asking(helmfile_apply):
    """`< /dev/null`, a pipe, a cron job: no `--interactive`, so nothing waits on stdin."""
    argv = helmfile_apply.argv_for()

    assert "--interactive" not in argv, "a non-terminal run asked helmfile to prompt"
    assert argv[argv.index("-l") + 1] == LABEL_FILTER


def test_yes_applies_without_asking(helmfile_apply):
    assert "--interactive" not in helmfile_apply.argv_for(["--yes"])


def test_the_short_form_applies_without_asking(helmfile_apply):
    assert "--interactive" not in helmfile_apply.argv_for(["-y"])


def test_yes_applies_without_asking_even_at_a_terminal(helmfile_apply):
    """The explicit form beats the tty default, which is the whole point of having it."""
    assert "--interactive" not in helmfile_apply.argv_for(["-y"], tty=True)


# ---------------------------------------------------------------------------
# the case that still has to be asked
# ---------------------------------------------------------------------------
def test_an_operator_at_a_terminal_is_asked(helmfile_apply):
    assert "--interactive" in helmfile_apply.argv_for(tty=True), \
        "the operator lost the chance to say no before the diff lands"


# ---------------------------------------------------------------------------
# what the rule must not do on its way there
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("stale_flag", ["--interactive", "--non-interactive"])
def test_the_removed_flag_pair_is_rejected_instead_of_being_understood(helmfile_apply, stale_flag):
    """No alias, no fallback: the old spelling fails loudly instead of meaning something."""
    result = helmfile_apply.invoke([stale_flag])

    assert result.exit_code != 0
    assert "No such option" in result.output, result.output


def test_the_library_never_prompts_when_it_is_not_told_to(helmfile_apply):
    """The default any caller gets without asking, which is what install and the lane rely on."""
    helmfile_apply.helm_utils().helmfile_apply(
        environment_name="dev", helmfile_label_filter=LABEL_FILTER)

    assert "--interactive" not in helmfile_apply.last_argv


def test_the_library_prompts_when_it_is_told_to(helmfile_apply):
    helmfile_apply.helm_utils().helmfile_apply(environment_name="dev",
                                               helmfile_label_filter=LABEL_FILTER, interactive=True)

    assert "--interactive" in helmfile_apply.last_argv


def test_the_command_reports_whether_it_asked(helmfile_apply):
    """The echo is what tells you why a scripted run came out silent."""
    assert "interactive: False" in helmfile_apply.invoke([]).output
