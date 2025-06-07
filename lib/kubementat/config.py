import json
import logging
import os
from pathlib import Path

# Add logging configuration at the top
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# For now this is a fixed setting, but it will make migration to v1 much easier
TEKTON_API_VERSION = "v1beta1"
TEKTON_API_GROUP = "tekton.dev"

class Config:
    '''
    The Config class loads configuration values from JSON files stored in specific directories based on environment and team.

    Parameters
    ----------
    environment : str
        The environment name (e.g., 'dev', 'prod')
    team : str
        The team name
    kubementat_main_dir : Path, optional
        Main directory path for Kubementat project. If not provided,
        it defaults to the parent directory of the lib folder.
    platform_config_dir : Path, optional
        Directory where platform configuration files are stored. If not provided,
        it defaults to 'platform_config' directory within kubementat_main_dir.
    tekton_pipeline_run_dir : Path, optional
        Directory for Tekton pipeline runs. If not provided, it defaults to
        'tekton_ci/pipeline-runs' directory within kubementat_main_dir.
    tekton_team_pipeline_run_dir : Path, optional
        Directory for Tekton pipeline runs for the according team. If not provided, it defaults to
        'tekton_ci/pipeline-runs/{TEAM_NAME}' directory within kubementat_main_dir.
    preload_configs : bool, optional
        If set to False, configuration values are not loaded from JSON files directly and are lazy loaded on access later. Default is True.

   Important Methods
    -------
    get(category):
        Returns configuration values for specified category (team_static, team_static_encrypted,
        env_static, env_static_encrypted).

    '''

    def __init__(self, environment, team, kubementat_main_dir=None, platform_config_dir=None, tekton_pipeline_run_dir=None, tekton_team_pipeline_run_dir=None, preload_configs = True):
        '''
        Initializes the Config instance with given parameters and sets up default paths.

        Parameters
        ----------
        environment : str
            Environment name (e.g., 'dev', 'prod')
        team : str
            Team name
        kubementat_main_dir : Path, optional
            Main directory path for Kubementat project. If not provided,
            it defaults to the parent directory of the lib folder.
        platform_config_dir : Path, optional
            Directory where platform configuration files are stored. If not provided,
            it defaults to 'platform_config' directory within kubementat_main_dir.
        tekton_pipeline_run_dir : Path, optional
            Directory for Tekton pipeline runs. If not provided, it defaults to
            'tekton_ci/pipeline-runs' directory within kubementat_main_dir.
        tekton_team_pipeline_run_dir : Path, optional
            Directory for Tekton pipeline runs for the according team. If not provided, it defaults to
            'tekton_ci/pipeline-runs/{TEAM_NAME}' directory within kubementat_main_dir.
        '''
        self.TEKTON_API_VERSION = TEKTON_API_VERSION
        self.TEKTON_API_GROUP = TEKTON_API_GROUP
        self.environment = environment
        self.team = team
        
        # Set default paths and override with provided values if any
        self.kubementat_main_dir = Path(__file__).absolute().parent.parent.parent.resolve()
        if kubementat_main_dir is not None:
            self.kubementat_main_dir = Path(kubementat_main_dir).resolve()

        self.platform_config_dir = Path(f"{self.kubementat_main_dir}/platform_config").resolve()
        if platform_config_dir is not None:
            self.platform_config_dir = Path(platform_config_dir).resolve()

        self.tekton_pipeline_run_dir = Path(f"{self.kubementat_main_dir}/tekton_ci/pipeline-runs").resolve()
        if tekton_pipeline_run_dir is not None:
            self.tekton_pipeline_run_dir = Path(tekton_pipeline_run_dir).resolve()

        self.tekton_team_pipeline_run_dir = Path(f"{self.kubementat_main_dir}/tekton_ci/pipeline-runs/{self.team}").resolve()
        if tekton_team_pipeline_run_dir is not None:
            self.tekton_team_pipeline_run_dir = Path(tekton_team_pipeline_run_dir).resolve()
        
        self.templates_dir = Path(f"{self.kubementat_main_dir}/templates").resolve()
        
        self.env_static_config_path = Path(f"{self.platform_config_dir}/{self.environment}/static.json").resolve()
        self.env_static_encrypted_config_path = Path(f"{self.platform_config_dir}/{self.environment}/static.encrypted.json").resolve()
        self.team_static_config_path = Path(f"{self.platform_config_dir}/{self.environment}/{self.team}/static.json").resolve()
        self.team_static_encrypted_config_path = Path(f"{self.platform_config_dir}/{self.environment}/{self.team}/static.encrypted.json").resolve()
        
        self.config = self._load_config(preload_configs)

    def _load_config(self, preload_configs=True):
        '''
        Loads configuration values from JSON files into a dictionary.

        The function loads both static and encrypted configuration files for the environment
        and team. If no team-specific files are found, it logs a warning and proceeds with
        only the environment-level configurations.

        Returns
        -------
        dict
            Configuration values organized by category (team_static, team_static_encrypted,
            env_static, env_static_encrypted)

        Raises
        ------
        FileNotFoundError
            If any configuration file is not found
        '''
        
        # skip early if preloading is not activated (e.g. for kmt initialize)
        if not preload_configs:
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

            # Load team-specific configs if they exist
            if os.path.exists(self.team_static_config_path):
                team_static = self._load_json_file(self.team_static_config_path)
            else:
                logging.warning(f"No team static config found at {self.team_static_config_path}")

            if os.path.exists(self.team_static_encrypted_config_path):
                team_static_encrypted = self._load_json_file(self.team_static_encrypted_config_path)
            else:
                logging.warning(f"No team static encrypted config found at {self.team_static_encrypted_config_path}")

            logging.info(f"Loaded configuration for environment: {self.environment} and team: {self.team}")
            logging.info(f"Kubementat Main Directory: {self.kubementat_main_dir}")
            logging.info(f"Platform Config Directory: {self.platform_config_dir}")
            logging.info(f"Tekton Pipeline Run Directory: {self.tekton_pipeline_run_dir}")
            logging.info(f"Tekton Team Pipeline Run Directory: {self.tekton_team_pipeline_run_dir}")

            return {
                'team_static': team_static,
                'team_static_encrypted': team_static_encrypted,
                'env_static': env_static,
                'env_static_encrypted': env_static_encrypted
            }

        except FileNotFoundError as e:
            logging.error(f"Configuration file not found - {e}")
            raise
    
    def get(self, category) -> dict:
        '''
        Returns configuration values for the specified category.

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
        '''
        if self.config[category] is None:
            self.config = self._load_config(True)
        return self.config[category]
    
    def _load_json_file(self, file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
