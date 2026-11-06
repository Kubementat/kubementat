import json
import logging
import os
from pathlib import Path

import click

from kubementat.core.config import derived as config_derived
from kubementat.core.config import paths
from kubementat.core.config import keys
from kubementat.core import constants
from kubementat.core.assets import asset_path
from kubementat.core.config.reads import read_decrypted
from kubementat.errors import TeamNotSuppliedError

# Importing this module configures logging and nothing else. The `logging.basicConfig`
# call that used to sit here ran for every library consumer — importing `kubementat`
# for a unit test or a script silently installed a root handler and raised the level to
# INFO (plan D10). The CLI opts in to the same configuration in `cli.kmt:main`, which is
# the only place that has a human reading stdout.

# One API version for the whole `tekton.dev` group, with no override: kubementat writes
# v1 and reads v1. `tekton.dev/v1beta1` was removed, so a tree that mixed the two would
# apply half its manifests and 404 on the rest.
TEKTON_API_VERSION = "v1"
TEKTON_API_GROUP = "tekton.dev"

# The Triggers group is the one beta API kubementat still speaks, and it is still a
# constant with no override. Triggers 0.37.2 serves `v1alpha1` and `v1beta1` and no `v1`
# (measured from its release.yaml: every CRD lists v1beta1 as served and stored, several
# still carry a v1alpha1 entry), so writing `v1` here would produce six trigger manifests
# no controller in this tree serves. See the shipped trigger templates under
# `Config.tekton_trigger_template_dir`.
TRIGGERS_API_VERSION = "v1beta1"
TRIGGERS_API_GROUP = "triggers.tekton.dev"

# Directories under `platform_config/{environment}/` that are not a team. A team is a directory
# that carries a `static.json` of its own, because that file is what makes it addressable: no
# file, no namespaces to name, nothing to install or uninstall. So "every subdirectory" is not the
# same question as "every team", and the directory this rule names is the one kubementat used to
# write itself.
#
# That directory is product data inside the package, and no verb writes anything by that
# name into a configuration tree, which is why this constant is now a belt and not a brace: the
# `static.json` test in `Config.teams` already refuses a `kubementat_components/` an older tree left
# behind, because it
# never carried one. The one case where removing it would change an answer is a stale directory
# that somehow does carry a `static.json`, and it is not worth deleting a rule for.
NON_TEAM_ENVIRONMENT_DIRECTORIES = frozenset({"kubementat_components"})

# The two categories that only exist inside a team's directory. They are spelled here so the
# refusal is one rule and not one per reader, and so a new team-scoped category has to be added to
# the list rather than remembered by whoever hits it first.
TEAM_SCOPED_CATEGORIES = ("team_static", "team_static_encrypted")


class Config:
    '''
    The Config class loads configuration values from JSON files stored in specific directories based on environment and team.

    Parameters
    ----------
    environment : str
        The environment name (e.g., 'dev', 'prod')
    team : str
        The team name. None is legal and means "no team was named": the environment-level
        files still resolve, the team-scoped paths are not built, and a read of a team-scoped
        category raises `TeamNotSuppliedError` rather than reading a path made out of the word
        `None`.
    kubementat_main_dir : Path, optional
        Main directory path for Kubementat project. If not provided, it defaults to
        `KUBEMENTAT_HOME`, and to the checkout this module lives in when that is unset.
    platform_config_dir : Path, optional
        Directory where platform configuration files are stored. If not provided, it
        defaults to `KUBEMENTAT_PLATFORM_CONFIG_DIR` and to nothing else: a run that names
        neither that variable nor `--config-dir` gets a refusal, not a root guessed out of
        where the package was installed from.
    tekton_global_pipeline_run_dir : Path, optional
        Global Directory for Tekton pipeline runs. If not provided, it defaults to
        'tekton_ci/pipeline-runs' directory within kubementat_main_dir.
    tekton_team_pipeline_run_dir : Path, optional
        Team Directory for Tekton pipeline runs for the according team. If not provided, it defaults to
        'tekton_ci/pipeline-runs/{TEAM_NAME}' directory within kubementat_main_dir.
    preload_configs : bool, optional
        If set to False, configuration values are not loaded from JSON files directly and are lazy loaded on access later. Default is True.

   Important Methods
    -------
    get(category):
        Returns configuration values for specified category (team_static, team_static_encrypted,
        env_static, env_static_encrypted).

    '''

    def __init__(self, environment, team, kubementat_main_dir=None, platform_config_dir=None, tekton_global_pipeline_run_dir=None, preload_configs = True):
        '''
        Initializes the Config instance with given parameters and sets up default paths.

        Parameters
        ----------
        environment : str
            Environment name (e.g., 'dev', 'prod')
        team : str
            Team name
        kubementat_main_dir : Path, optional
            Main directory path for Kubementat project. If not provided, it defaults to
            `KUBEMENTAT_HOME`, and to the checkout this module lives in when unset.
        platform_config_dir : Path, optional
            Directory where platform configuration files are stored. If not provided, it
            defaults to `KUBEMENTAT_PLATFORM_CONFIG_DIR`; with neither the argument and the
            variable, there is no root and the constructor refuses. `kubementat_main_dir` is
            never consulted for it.
        tekton_global_pipeline_run_dir : Path, optional
            Directory for Tekton pipeline runs. If not provided, it defaults to
            'tekton_ci/pipeline-runs' directory within kubementat_main_dir.
        '''
        self.TEKTON_API_VERSION = TEKTON_API_VERSION
        self.TEKTON_API_GROUP = TEKTON_API_GROUP
        self.TRIGGERS_API_VERSION = TRIGGERS_API_VERSION
        self.TRIGGERS_API_GROUP = TRIGGERS_API_GROUP
        self.environment = environment
        self.team = team
        
        # The root a caller handed is the answer, and the environment is consulted only when no
        # caller handed one. The order matters more than it looks: this used to call
        # `default_kubementat_main_dir()` first and override it second, so a `Config` that had
        # *been told* which tree to use still asked the process's own `KUBEMENTAT_HOME` on the way
        # through — and an empty-but-set `KUBEMENTAT_HOME` refused a command that had a root
        # threaded into it, over a dial nobody had consulted. Reading a dial you
        # were not asked about is also what makes the answer unstable: the same object means two
        # different things in two different shells.
        #
        # Precedence is therefore argument > environment variable > checkout-relative default,
        # so a test or a one-off run can point one instance somewhere without setting the variable
        # for the whole machine. Nothing here softens the refusal rule: with neither the argument
        # nor the variable, `default_kubementat_main_dir()` answers with the checkout this module
        # was installed from (D11's honest default for the *output* root), and the configuration
        # root below is the one that has no default to fall back to.
        if kubementat_main_dir is not None:
            self.kubementat_main_dir = Path(kubementat_main_dir).resolve()
        else:
            self.kubementat_main_dir = paths.default_kubementat_main_dir()

        # `resolve_platform_config_dir` is the whole rule: the argument is the `--config-dir`
        # route, the variable is the container's route, and with neither there is no root to build
        # paths under. It is one function and not two spellings because this rule had a second
        # owner in `src/cli/commands/environments.py`, which spelled the same precedence beside its
        # own `EnvironmentRegistry` with no `Config` in sight — agreement is not the same thing as
        # one rule, and the day either one moves, the listing and a command's refusal start
        # disagreeing about which tree this operator configured.
        #
        # `kubementat_main_dir` is not consulted for this — it names the tree that still holds
        # checkout-relative *output* (`tekton_ci/pipeline-runs/`), while everything the product
        # ships is package data and resolves from inside the package. Reading
        # configuration out of it is the bug this rule is here to stop.
        self.platform_config_dir = paths.resolve_platform_config_dir(platform_config_dir)

        # Pipeline runs are **output**: a run is written by whoever generated it and read back by
        # `kmt tekton run`, so it lives under the root it was generated under and never ships inside
        # the package. Do not "correct" this to `asset_path`: the asset side of `tekton_ci/` moved
        # under `src/kubementat/core/assets/` and this line is the half that stayed.
        self.tekton_global_pipeline_run_dir = Path(f"{self.kubementat_main_dir}/tekton_ci/pipeline-runs").resolve()
        if tekton_global_pipeline_run_dir is not None:
            self.tekton_global_pipeline_run_dir = Path(tekton_global_pipeline_run_dir).resolve()
        
        # The four `tekton_ci/**` directories split two ways, and the split is what these accessors
        # are for. `tasks/`, `pipelines/` (with their `templates/`) and `triggers/templates/` are
        # product assets: tracked files that belong to everybody and to no checkout in particular,
        # so they ship inside the package and resolve from inside it, exactly the way `templates/`
        # has always been package data. `pipeline-runs/` is output, and it is the line above that says so.
        #
        # Reading them through `kubementat_main_dir` was the bug this ticket fixes: `setup_pipelines`
        # applied `{root}/tekton_ci/{tasks,pipelines}`, so a `kmt` installed from a wheel applied a
        # directory that does not exist there, logged "Global Tasks applied successfully!", and
        # installed nothing. `apply_resources` raises `FileNotFoundError` for a missing directory,
        # so an asset root that is really there is the only thing this can be built from.
        self.templates_dir = asset_path("templates")

        # The install's entry point and its shipped defaults, one attribute because the two are one
        # directory: helmfile resolves a release's relative `values:` path against the directory of
        # the manifest it was handed, so the manifest and the ten values files it names travel
        # together or a release installs with chart defaults. Nothing writes under it and nothing
        # reads it out of a checkout: this is the manifest `helmfile_apply` applies, and
        # `config_reads_index.helmfile_paths_for` is its only reader in `src/` -- reached from
        # `HelmUtils.helmfile_paths` and from the config report alike, which is the one reason a
        # read of this attribute cannot end up pointed at two files.
        self.helmfile_asset_dir = asset_path("values")

        # The two roots a global `setup_pipelines` applies, as accessors for the same reason the
        # team dirs are accessors: there has to be one sentence about where the product's own
        # manifests are, or the next reader re-derives them off a root that is not there.
        self.tekton_task_asset_dir = asset_path("tekton_ci", "tasks")
        self.tekton_pipeline_asset_dir = asset_path("tekton_ci", "pipelines")

        # The generator *templates*, spelled as accessors too: the two generators below read them,
        # and `apply_resources` deliberately skips any directory named `templates/` (it is generator
        # input, not a resource to apply). The pipeline templates sit *inside* the pipeline root the
        # previous line names, which is why that skip is load-bearing rather than defensive — the
        # walk reaches `templates/` on its way through a real resource root. The pair of *output*
        # dirs next to them (`tekton_team_pipeline_dir`, `tekton_team_trigger_dir`) is therefore
        # never the same directory, and collapsing them would put a half-finished manifest in front
        # of a cluster.
        self.tekton_pipeline_template_dir = self.tekton_pipeline_asset_dir / "templates"
        self.tekton_trigger_template_dir = asset_path("tekton_ci", "triggers", "templates")

        # team dirs — built on access, never here. `Config(environment, None)` is a legal object —
        # a command that sweeps every team belongs to none of them — and interpolating the
        # word `None` into these six strings is the defect, so with no team named they are not
        # built at all and reading one raises `TeamNotSuppliedError`. See the properties below.
        self.env_static_config_path = Path(f"{self.platform_config_dir}/{self.environment}/static.json").resolve()
        self.env_static_encrypted_config_path = Path(f"{self.platform_config_dir}/{self.environment}/static.encrypted.json").resolve()

        # Populated by _load_config with the categories whose team-level file was missing,
        # so that get() can fail with an error that names the file instead of handing a None
        # namespace to the Kubernetes client.
        self.missing_team_config_files = {}

        # The dictionaries the four categories were read from, kept apart from the layered views
        # `self.config` holds. `None` means "nothing has been read yet", which is the same
        # statement `self.config[category] is None` made before the views existed.
        self.config_sources = None

        # Keys this instance has already reported falling back for, so a component that is
        # read by five commands produces one warning instead of five.
        self.defaulted_config_keys = set()

        self.config = self._load_config(preload_configs)

    def _team_scoped_path(self, relative: str, what: str) -> Path:
        '''
        One of the paths that only exist inside a team's directory.

        With no team named this class has nothing to resolve, and every alternative is worse than
        a refusal: the string `None` interpolated into the path is the defect, and a path built
        from an empty team silently lands on the *environment's* own files, which is the same
        reading of the wrong file wearing a cleaner name. So the answer is a refusal that says what
        was wanted, where it would have lived, and which name is missing.
        '''
        if self.team is None:
            raise TeamNotSuppliedError(
                f"{what} lives in the team directory of environment '{self.environment}', and this "
                f"configuration was built for no team, so no path under "
                f"platform_config/{self.environment}/<team>/ can be named. A command that reads it "
                f"was not given a team; kubementat will not name one for it. Pass --team (or set "
                f"TEAM) to name it."
            )
        return Path(f"{self.platform_config_dir}/{self.environment}/{self.team}").joinpath(
            *relative.split("/")).resolve()

    @property
    def team_static_config_path(self) -> Path:
        """`static.json` for this team — not built when no team is named."""
        return self._team_scoped_path("static.json", "The team's static configuration")

    @property
    def team_static_encrypted_config_path(self) -> Path:
        """`static.encrypted.json` for this team — not built when no team is named."""
        return self._team_scoped_path("static.encrypted.json",
                                      "The team's encrypted static configuration")

    # The middle apply layer, the directory that had no home for a while. There are three, and the
    # order is the
    # whole rule: product (`tekton_task_asset_dir`/`tekton_pipeline_asset_dir`, package data), then
    # these two, then the team's own (`tekton_team_*`, generated output). A Task that appears in two
    # of them is not resolved by code — the later `apply_resources` call patches the earlier object,
    # because `create_or_replace_custom_resource` patches on 409. So a name index here would be a
    # second source of truth about an order that already exists in the call sequence.
    #
    # `triggers/` is deliberately absent here, and that is a measured decision and not an
    # omission: the read path that applies a trigger directory is team-only
    # (`tekton_utils.py:1488-1514`), so an environment `triggers/` directory would be a directory
    # that exists and is read by nothing — the dead `kubementat_components/` tree, recreated. The
    # *team* trigger directory sits under `tekton/` (`tekton_team_trigger_dir` below);
    # the environment half lands only when something actually opens one.
    @property
    def tekton_env_task_dir(self) -> Path:
        """This environment's own Tasks — the layer between the product and its teams."""
        return Path(f"{self.platform_config_dir}/{self.environment}/tekton/tasks").resolve()

    @property
    def tekton_env_pipeline_dir(self) -> Path:
        """This environment's own Pipelines — the layer between the product and its teams."""
        return Path(f"{self.platform_config_dir}/{self.environment}/tekton/pipelines").resolve()

    @property
    def tekton_team_pipeline_dir(self) -> Path:
        """Where this team's generated pipelines are written."""
        return self._team_scoped_path("tekton/pipelines", "The team's generated pipelines")

    @property
    def tekton_team_task_dir(self) -> Path:
        """Where this team's generated tasks are written."""
        return self._team_scoped_path("tekton/tasks", "The team's generated tasks")

    @property
    def tekton_team_pipeline_run_dir(self) -> Path:
        """Where this team's pipeline runs are written, and read back from by `kmt tekton run`."""
        return self._team_scoped_path("tekton/pipeline-runs", "The team's pipeline runs")

    @property
    def tekton_team_trigger_dir(self) -> Path:
        """Where this team's trigger configurations are written, and read back from.

        Under `tekton/`, like its three siblings. A tree that still has the old `<team>/triggers/`
        directory is refused by name, at
        the point of reading (`tekton_utils.py`'s `_apply_team_trigger_resources`) and at the
        point of scaffolding (`config_init`), because silently applying nothing is the failure
        the move would otherwise create. kubementat records no rename table and rewrites nothing, so
        moving that content is the operator's and is decided beside this path, not by it.
        """
        return self._team_scoped_path("tekton/triggers", "The team's trigger configurations")

    def _load_config(self, preload_configs=True):
        '''
        Loads configuration values from JSON files into a dictionary.

        The function loads both static and encrypted configuration files for the environment
        and team. If no team-specific files are found, it logs a warning naming the path that
        was expected and proceeds with only the environment-level configurations; the
        team-level categories are then empty mappings, and a later read of one of them fails
        with an error naming that same file (see get()).

        Returns
        -------
        dict
            Configuration values organized by category (team_static, team_static_encrypted,
            env_static, env_static_encrypted)

        Raises
        ------
        FileNotFoundError
            If an environment-level configuration file is not found
        '''
        
        # skip early if preloading is not activated (e.g. for a verb that writes a tree
        # without resolving a cluster, which is what `config init` is)
        if not preload_configs:
            self.missing_team_config_files = {}
            self.config_sources = None
            return {
                'team_static': None,
                'team_static_encrypted': None,
                'env_static': None,
                'env_static_encrypted': None
            }
        
        try:
            # Load static and encrypted environment configs
            env_static = self._load_json_file(self.env_static_config_path)
            env_static_encrypted = self._load_json_file(self.env_static_encrypted_config_path)

            # Team-level files are optional: bind both names up front, because leaving them
            # unbound made the return below crash for every team that has no team-level
            # config (UnboundLocalError, plan D6).
            team_static = {}
            team_static_encrypted = {}
            self.missing_team_config_files = {}

            # With no team named there is nothing to scan, and nothing *missing* either: a
            # `missing_team_config_files` entry would be a claim that a file the run never asked
            # for is absent. The refusal belongs to the read, and `get()` is where it lives.
            if self.team is not None:
                if os.path.exists(self.team_static_config_path):
                    team_static = self._load_json_file(self.team_static_config_path)
                else:
                    logging.warning(f"No team static config found at {self.team_static_config_path}")
                    self.missing_team_config_files['team_static'] = str(self.team_static_config_path)

                if os.path.exists(self.team_static_encrypted_config_path):
                    team_static_encrypted = self._load_json_file(self.team_static_encrypted_config_path)
                else:
                    logging.warning(
                        f"No team static encrypted config found at {self.team_static_encrypted_config_path}")
                    self.missing_team_config_files['team_static_encrypted'] = str(
                        self.team_static_encrypted_config_path)

            logging.info(f"Loaded configuration for environment: {self.environment} and team: {self.team}")
            logging.info(f"Kubementat Main Directory: {self.kubementat_main_dir}")
            logging.info(f"Platform Config Directory: {self.platform_config_dir}")
            logging.info(f"Kubementat Templates Directory: {self.templates_dir}")
            logging.info(f"Tekton Global Pipeline Run Directory: {self.tekton_global_pipeline_run_dir}")
            # The environment layer of the apply order, printed here for the same reason the team's
            # dirs are: it is the directory a reader of this log is trying to find, and it exists as
            # a path whether or not anyone has ever put a manifest in it.
            logging.info(f"Tekton Environment Tasks Directory: {self.tekton_env_task_dir}")
            logging.info(f"Tekton Environment Pipelines Directory: {self.tekton_env_pipeline_dir}")
            # The four team directories are what a reader of this log is looking for, and with no
            # team named there is nothing to print — so the log says the one true thing instead of
            # raising on its way past a command that has done nothing wrong.
            if self.team is not None:
                logging.info(f"Tekton Team Pipelines Directory: {self.tekton_team_pipeline_dir}")
                logging.info(f"Tekton Team Tasks Directory: {self.tekton_team_task_dir}")
                logging.info(f"Tekton Team Pipeline Runs Directory: {self.tekton_team_pipeline_run_dir}")
                logging.info(f"Tekton Team Triggers Directory: {self.tekton_team_trigger_dir}")

            # The raw dictionaries are kept beside the views, because the layered answer is a
            # function of *both* files of a layer and the view throws that distinction away. The
            # views are what `get()` returns, so the layering happens here — where the categories
            # are loaded — and no call site has to know it exists.
            sources = {
                'team_static': team_static,
                'team_static_encrypted': team_static_encrypted,
                'env_static': env_static,
                'env_static_encrypted': env_static_encrypted,
            }
            self.config_sources = sources
            derived = (self.team, config_derived.RULES)
            return {category: keys._layered_view(keys.CATEGORY_LAYERS[category], sources,
                                                 self._config_path_of, derived,
                                                 constants.DEFAULT_STATIC_ANSWERS)
                    for category in keys.CATEGORY_LAYERS}

        except FileNotFoundError as e:
            logging.error(f"Configuration file not found - {e}")
            raise
    
    def get(self, category) -> dict:
        '''
        Returns configuration values for the specified category.

        The team-shaped rules answer here, because this is the door every placement question
        comes through. For what the files themselves say, use `declared`.

        Parameters
        ----------
        category : str
            The category of configuration to retrieve. Options are:
                - team_static
                - team_static_encrypted
                - env_static
                - env_static_encrypted

        Returns
        -------
        dict
            Configuration values for the specified category

        Raises
        ------
        click.ClickException
            If the category comes from a team-level file that does not exist. The warning
            already named that file when the config was loaded; this is the same file named
            in an error the user can act on.
        TeamNotSuppliedError
            If the category is a team-scoped one and this `Config` was built for no team.
            Returning an empty mapping would be the quieter failure, and it is the failure this
            refuses: a namespace that comes back empty is a namespace nobody chose.
        '''
        if self.team is None and category in TEAM_SCOPED_CATEGORIES:
            raise TeamNotSuppliedError(
                f"{category} is the team's own configuration, and this command was given no team "
                f"for environment '{self.environment}'. It is not missing a file: a team-level read "
                f"needs platform_config/{self.environment}/<team>/, which is where namespaces, "
                f"service accounts and deploy keys are named. kubementat will not name a team a "
                f"command did not name. Pass --team (or set TEAM) to name it."
            )
        self._ensure_config_loaded()
        missing_file = self.missing_team_config_files.get(category)
        if missing_file is not None:
            raise click.ClickException(
                f"No {category} config found at {missing_file}. "
                f"Environment: {self.environment}, team: {self.team} - create the file or "
                f"pass the environment and team you actually mean."
            )
        return self.config[category]

    def declared(self, category) -> dict:
        '''
        What `category`'s **files** answer, with no rule filling a hole. Same layers, same order.

        This is the question a destructive path has to ask, and it is a different question from
        `get()`. Five answers are consequences of a team's name, which is the right thing when
        kubementat is deciding *where to put something*: `pandas` team's pipelines namespace is
        `pandas-pipelines`, and asking the operator to type that is asking for a typo. It is the wrong
        thing when kubementat is deciding *what it may delete*, because the rule yields an address and
        not a deed — a cluster can hold a namespace called `pandas` that this configuration never
        created, and a name derived from a directory's name is not evidence that anybody here made it.
        So a namespace that no file named is never swept, and the log says that is why.

        It is deliberately not a flag on `get()`. A `derived=False` would be a knob any future caller
        could reach for to mean several different things, and the sentence that needs to exist is
        "this code only deletes what a file named" — a name carries that, and it is one method longer
        than a boolean.

        Same three guards as `get()` — no team, no file, nothing loaded — so the two doors cannot give
        two different failures for one problem.
        '''
        if self.team is None and category in TEAM_SCOPED_CATEGORIES:
            raise TeamNotSuppliedError(
                f"{category} is the team's own configuration, and this command was given no team "
                f"for environment '{self.environment}'. A deletion needs the namespace a file names, "
                f"and a team-less read has no file to name it from. Pass --team (or set TEAM)."
            )
        self._ensure_config_loaded()
        missing_file = self.missing_team_config_files.get(category)
        if missing_file is not None:
            raise click.ClickException(
                f"No {category} config found at {missing_file}. "
                f"Environment: {self.environment}, team: {self.team} - create the file or "
                f"pass the environment and team you actually mean."
            )
        return keys._layered_view(keys.CATEGORY_LAYERS[category], self.config_sources,
                                  self._config_path_of)

    def _ensure_config_loaded(self):
        """Read the files once, before any answer is given.

        The check used to be spelled inside `get()` against the requested category. It is a method
        now because a component read no longer goes through `get()`, and a lazy `Config` must not
        answer a namespace out of `constants.py` on the strength of a call that never loaded
        anything: one gate for both readers, and it cannot be skipped by the reader that forgot.
        """
        if self.config_sources is None:
            self.config = self._load_config(True)

    def _config_path_of(self, category):
        """The file `category` was read from, or `None` when this instance could not build one.

        A team-scoped category of a team-less `Config` has no path, and a resolution that
        needs no path is a resolution that says nothing about where an answer came from — the
        message is what this is for, so it is built here and never by a caller.
        """
        if category == "team_static":
            return self.team is not None and self.team_static_config_path or None
        if category == "team_static_encrypted":
            return self.team is not None and self.team_static_encrypted_config_path or None
        if category == "env_static":
            return self.env_static_config_path
        return self.env_static_encrypted_config_path

    def _resolved(self, key, layers):
        """The answer for `key` over `layers`, plus the file it came from. One path, two readers.

        `get()` materializes this for whole categories at load time; `_component_value` calls it for
        one key, because a component's namespace belongs to no file the caller was told about. Either
        way the walk is `_declare_in_layers`' and the order is `CATEGORY_LAYERS`' — a second
        implementation of the order is the bug census E found, so there is only this one. The team-
        shaped rules ride along on the same walk, which is the whole reason they answer the same way
        whichever door a caller came through.
        """
        self._ensure_config_loaded()
        return keys._declare_in_layers(key, layers, self.config_sources, self._config_path_of,
                                       (self.team, config_derived.RULES))

    def component_namespace(self, component: str) -> str:
        '''
        The namespace `component` is deployed into.

        Reads `<COMPONENT>_DEPLOYMENT_NAMESPACE` from the environment-level `static.json` and
        falls back to `kubementat.core.constants.DEFAULT_NAMESPACES`, warning once per instance
        when it does. Earlier the answer lived in the code (`automation.py` had four
        `namespace = '...'` lines, `uninstall_kubementat.sh` had fifteen), so renaming one
        namespace meant finding every copy of it — which is exactly what the
        `namespace-rename-tunnel-still-correct` lane assert used to catch.

        Parameters
        ----------
        component : str
            Component name, lower-case with hyphens, as `constants.DEFAULT_NAMESPACES` is
            keyed ('grafana', 'cert-manager', 'tekton').

        Returns
        -------
        str
            The namespace to address.

        Raises
        ------
        KeyError
            If `component` is not a kubementat component. A typo has to say so rather than
            silently deploy into a namespace named after the typo.
        '''
        return self._component_value(component, 'DEPLOYMENT_NAMESPACE', constants.DEFAULT_NAMESPACES)

    def component_release_enabled(self, component: str):
        r'''
        Whether `component`'s release is installed, answered by `<COMPONENT>_ENABLED`.

        One half of the pair the owner decision of 2026-11-03 settled: a release carries two
        facts an operator has to be able to state without editing a template — whether it runs,
        and which chart version of it — and both become `static.json` answers the same way the
        namespace already did. The read is `_resolved` over the component layers and
        nothing else, so the team's `false` outranks the environment's `"true"` the same way a
        team's namespace does.

        **L0 for this key is the shipped manifest and not `constants.py`, and that is the one
        break in an otherwise uniform rule.** The shipped `helmfile.yaml.gotmpl` is the entry point
        that applies the releases, so every chart-version literal is already owned there; copying
        those literals into `constants.py` would give one version string two owners and guarantee
        they disagree the first time a pin moves. The `default "true"` the manifest carries is
        this key's L0, and `None` from here is what makes it answer. The absence of a code table
        is the design rather than an unfinished one, so it is stated here and not "completed".

        Parameters
        ----------
        component : str
            Component name, lower-case with hyphens, as the manifest's `component_name` labels
            are spelled ('grafana', 'cert-manager', 'prometheus-blackbox-exporter').

        Returns
        -------
        str or None
            `"true"` or `"false"`, lower-cased — helmfile's `installed:` field resolves only
            those two, and a quoted `"False"` is a string that lands nowhere. `None` when no
            layer declares the key, which is a decision not to override and so is answered
            silently: there is no "you forgot" sentence to tell.

        Raises
        ------
        click.ClickException
            When the key is declared as a number, a list or a dict. There is no valid answer for
            this knob that is not a boolean or a string, so a mis-typed value is refused where it
            was typed and not papered over with the default.
        '''
        return self._release_knob(keys.component_config_key(component, 'ENABLED'), component)

    def component_release_version(self, component: str):
        r'''
        The chart version `component`'s release installs, answered by `<COMPONENT>_VERSION`.

        The other half of the pair, read through the same one path so that the two knobs a
        release carries cannot disagree about which layer answered. Nothing in `constants.py`
        is consulted, and that is deliberate rather than a gap: the version kubementat ships is
        spelled in the manifest that applies it (see
        `component_release_enabled` for why that is the owner), and this function's only job is
        to say what the operator overrode. `None` here is the manifest answering for itself.

        Parameters
        ----------
        component : str
            Component name, lower-case with hyphens, as the manifest's `component_name` labels
            are spelled.

        Returns
        -------
        str or None
            The version string as declared, stripped. `None` when no layer declares the key.

        Raises
        ------
        click.ClickException
            When the key is declared as a number, a list or a dict. A version that reads like
            `1.0` is the shape this refusal exists for: JSON would have stored it as the number
            `1.0`, which is not the string `"1.0"` and resolves to a chart helm has never seen.
            `helmfile` quotes the manifest's own default for the same reason.
        '''
        return self._release_knob(keys.component_config_key(component, 'VERSION'), component)

    def _release_knob(self, key: str, component: str):
        r'''
        The one implementation behind both release-knob accessors: the layers answer, or nothing does.

        `key` is spelled by the accessor that owns it, through `component_config_key`, so there is no
        third place that has to agree with what a component is called. `component` rides along for the
        one sentence that has to be actionable: which knob was refused, and where the edit belongs.

        This deliberately does **not** go through `_component_value`, which is the accessor that
        owns namespaces and selectors. That function tests `isinstance(value, str)` on the way to
        its default, and a JSON `false` is not a string — so the highest layer that declared
        `<COMPONENT>_ENABLED: false` would fall straight through to the default of `true` and log a
        warning about defaulting, i.e. an operator who said "do not install this" would get it
        installed and told the opposite. An accessor that answers a boolean is therefore a different
        reader, and this is the only one. `_resolved` is still the walk, `COMPONENT_LAYERS` is still
        the order, and `_component_layers` is still what decides whether the team half exists.

        There is no default to fall back to here, and that is the second half of the same choice:
        the fallback lives in the manifest's `default`, next to the literal it belongs to, so a
        layer that declares nothing costs nothing and prints nothing.

        Raises
        ------
        click.ClickException
            A declared value that is a number, list or dict. The message names the key, the file
            that declared it, and the two files of that layer the key could have lived in, since
            "which file did I edit" is the only question left once the type is known to be wrong.
        '''
        value, declared_in = self._resolved(key, self._component_layers())

        # An absent key and a key declared `null` are the same answer to this question — nobody
        # decided — and both reach the manifest's default. `False` is not in this branch, which is
        # the whole point: a declared `false` is a decision and has to survive the walk.
        if value is keys._NOTHING_DECLARED or value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str):
            # Blank is the half-typed edit `_component_value` warns about; here it is the same
            # nothing, because there is no code default to warn about.
            return value.strip() or None
        where = (str(declared_in) if declared_in is not None
                 else "a file this configuration read")
        pair = (" or ".join(str(declared_in.parent / name)
                            for name in ("static.json", "static.encrypted.json"))
                if declared_in is not None
                else self._layers_to_check(self._component_layers()))
        raise click.ClickException(
            f"{key} is a {type(value).__name__} in {where}. kubementat reads that knob as a "
            f"boolean or a string and nothing else, so there is no default here to rescue it and "
            f"the run stops instead of installing something nobody chose. The key can only live in "
            f"{pair}, and the encrypted file of a layer answers before the plain one. Set it to "
            f"true or false (for ENABLED) or to a quoted chart version (for VERSION), or delete "
            f"the entry to let the manifest answer."
        )

    def teams(self) -> list:
        '''
        Every team that has configuration under this environment.

        The answer is a directory listing, not a list written in code. `uninstall_kubementat.sh`
        named `dev1` and `smoke` because those were the teams whoever wrote it had, and a third
        team's namespaces therefore survived `kmt uninstall` while its own documentation claimed
        otherwise. Enumerating `platform_config/{environment}/*/static.json` says what is true
        about this installation instead.

        Returns
        -------
        list[str]
            Team names, sorted, empty when the environment has no teams configured yet.

        Raises
        ------
        click.ClickException
            If there is no configuration directory for the environment at all. An environment
            that has never been prepared is not the same thing as an environment with no
            teams, and guessing the difference would delete nothing while reporting success.
        '''
        environment_directory = self.platform_config_dir / self.environment
        if not environment_directory.is_dir():
            raise click.ClickException(
                f"No configuration directory for environment '{self.environment}' at"
                f" {environment_directory}. Run kmt config init <dir> --environment"
                f" {self.environment}, or pass the environment you actually mean."
            )
        return sorted(
            entry.name
            for entry in environment_directory.iterdir()
            if entry.is_dir()
            and entry.name not in NON_TEAM_ENVIRONMENT_DIRECTORIES
            and (entry / "static.json").is_file()
        )

    def component_service_selector(self, component: str) -> str:
        '''
        The selector that finds `component`'s pods, read the same way as the namespace.

        The tunnels need both to reach a pod, so both come from the same place. A key that
        exists but is not read anywhere would be the second place to disagree.
        '''
        return self._component_value(component, 'SERVICE_SELECTOR', constants.DEFAULT_SERVICE_SELECTORS)

    def component_ingress_class(self, component: str) -> str:
        r'''
        The `ingressClassName` `component`'s controller serves, read the same way as the namespace.

        This key is the one answer the tree had three spellings of and one reader. `constants.py`
        answered it as L0, the Python readers reached it through `_component_value`, and
        `helmfile.yaml.gotmpl` answered it a third time with an inline `default` of its own -- which
        is why an environment that declared a class got a release configured with `traefik`.
        The manifest half is settled there: the variable this answer is exported into is what the
        release reads, so there is one owner, the layering walk below, and no second one next to the
        line that uses the value.

        The read is spelled as an accessor for the same reason `component_namespace` is: a caller that
        assembles `component_config_key` and a fallback table by hand is a caller that drifts, and this
        key's fallback table used to live in `automations/routing/routing.py` for exactly that reason.
        Both readers that already existed keep their own call for now, and all three paths go through
        this method's one table, so the answer is the same whichever door a caller came through.

        Raises
        ------
        KeyError
            When `constants.py` answers nothing for `component`'s class. That is not a missing
            declaration an environment could fix by editing a file: it is the table the walk consults
            last, and a default invented here would be the second owner this method exists to remove.
        '''
        key = keys.component_config_key(component, 'INGRESS_CLASS')
        default = constants.static_answer(key)
        if default is None:
            raise KeyError(
                f"No default is defined for kubementat component '{component}' under {key}. Add it to"
                f" kubementat.core.constants.DEFAULT_STATIC_ANSWERS, which is the table this read's"
                f" resolution walk consults last, or answer it in the environment's static.json"
                f" with kmt config set. Writing a value here instead would put a second owner on"
                f" an answer that has one.")
        return self._component_value(component, 'INGRESS_CLASS', {component: default})

    def _component_value(self, component: str, suffix: str, defaults: dict) -> str:
        '''
        The one implementation behind the `component_*` accessors: the layers answer first, the
        code answers second, and only one of them gets to say which file to edit.

        Earlier this was the tree's only two-layer read: `env_static`, then `constants.py`,
        never the team's pair. It is now one of two readers of `_resolved`; the other is `get()`,
        which materializes the same walk into the category dictionaries. Which file a caller
        addresses is not a question about the value, so the accessor that owns a component's
        namespace asks the layers instead, and stays the only place that answers for those keys.
        '''
        try:
            default = defaults[component]
        except KeyError:
            raise KeyError(
                f"No default is defined for kubementat component '{component}'. Add it to"
                f" kubementat.core.constants, and answer "
                f"{keys.component_config_key(component, suffix)} in"
                f" this environment's own static.json (which is what this read answers from)."
            )

        key = keys.component_config_key(component, suffix)
        layers = self._component_layers()
        value, declared_in = self._resolved(key, layers)
        if isinstance(value, str) and value.strip():
            if declared_in is None:
                # No layer declared it and an L0 answered, which is the same fact the branch below
                # reports. It looks different now because the walk itself can answer: an
                # L0 that lives in `constants.DEFAULT_STATIC_ANSWERS` comes back through
                # `_declare_in_layers`, and the table under this call's `defaults` is what the
                # namespace and selector families still use. Silence here would be a regression
                # against the operator-visible sentence `ingress.py` relies on.
                self._warn_once_about_default(key, layers, None, value.strip(), component)
            return value.strip()

        self._warn_once_about_default(key, layers, declared_in, default, component)
        return default

    def _warn_once_about_default(self, key, layers, declared_in, default, component) -> None:
        """The once-per-`Config` sentence that says an answer did not come from a file.

        One gate for both shapes of that fact — nothing declared it, which is how an L0 answer
        arrives, and something
        declared it unusably (`_defaulted_warning`'s original reason) — so the count stays what
        `config_answers.py` silences it with and one read cannot print it twice.
        """
        if key in self.defaulted_config_keys:
            return
        self.defaulted_config_keys.add(key)
        logging.warning(self._defaulted_warning(key, layers, declared_in, default, component))

    def _component_layers(self) -> tuple:
        """The chain a component's keys are answered from: L2 over L1, with no L2 when no team
        was named. A `Config` built without a team has no team layer to consult, and trying to
        name that layer's paths would raise the team-not-named refusal over a question about a
        namespace.
        """
        return keys.COMPONENT_LAYERS if self.team is not None else keys.ENVIRONMENT_PAIR

    def _layers_to_check(self, layers) -> str:
        """Where this read looked, highest first, spelled for whoever has to act on it.

        Paths and not categories, because the sentence is a direction: "`GRAFANA_DEPLOYMENT_NAMESPACE`
        is not set in .../dev/static.json" is actionable and "L1 env_static" is not.
        """
        parts = []
        for layer, category in layers:
            path = self._config_path_of(category)
            if path is not None:
                parts.append(f"{layer} {path}")
        if not parts:
            return "nowhere - no configuration file was read"
        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + " or " + parts[-1]

    def _defaulted_warning(self, key, layers, declared_in, default, component) -> str:
        '''
        The warning that fires once per `Config` when nothing declared `key` in a usable form.

        Two facts it has to carry, and census E is why: **which layers were consulted**, so the
        operator edits the layer that answers rather than the one they were thinking of, and **which
        file of a layer would have answered**, since a layer is a pair and the encrypted file of it
        wins. Naming the key once is also load-bearing: the tree's own once-per-config test counts
        occurrences of the key in the log, and a sentence that recites it twice reads as a bug.

        The fallback below is L0. For the keys this warning covers, L0 is
        `src/kubementat/constants.py` and nothing else; the exception is the five team-shaped answers
        in `config_derived.py`, which are not defaults and do not warn, because they were
        never a question. The packaged `defaults/static.json` is not consulted here
        and does not exist in this tree, and must not be added in this phase: its source is the
        environment template, 40 keys of which 8 are `REPLACEME_*`, so promoting it to a default
        source would answer with placeholders and give the namespaces a second owner beside
        `constants.py`. Phase 3 is where that template stops being copied. Do not "complete" this
        fallback from the spec.
        '''
        if declared_in is None:
            opening = f"{key} is not set in {self._layers_to_check(layers)}"
            which = ("A team's pair answers before the environment's, and inside one layer the "
                     "encrypted file answers before the plain one, so the highest layer that "
                     "declares it is what this run would have used")
        else:
            opening = (f"{key} is set but empty in {declared_in}, so no layer of "
                       f"{self._layers_to_check(layers)} answers")
            which = (f"the layer that declared it is {declared_in.name} - an empty value is a "
                     f"half-typed edit, not a decision to use the default")
        return (
            f"{opening} - using kubementat's built-in default '{default}' for {component}. {which}."
            f" Edit that file to run {component} in a different namespace; the default only exists"
            f" so a config written before this was configurable still works."
        )
    
    def _load_json_file(self, file_path):
        # One read, one sentence. A file whose bytes will not decode is refused as
        # git-crypt's and not as malformed JSON, and the sentence is the seam's, because four sites
        # used to say this four different ways. `FileNotFoundError` still comes from here, since the
        # optional-team-file handling in `_load_config` below is what makes a missing layer degrade
        # and not explode.
        return json.loads(read_decrypted(file_path, "static layer file"))
