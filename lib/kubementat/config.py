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

    This class is used to load the configuration files from the platform_config directory.
    It is a singleton class, so only one instance of the class can be created.
    The class is initialized with the environment and team name.
    The class has a method to load the configuration files from the platform_config directory.
    The class has a method to get the configuration value for a given key.
    '''
    _instance = None

    def __init__(self, environment, team, kubementat_main_dir=None, platform_config_dir=None, tekton_pipeline_run_dir=None):
        if Config._instance is not None:
            raise ValueError("Config instance already exists")

        # default -> set the kubementat main directory: ../.. above the lib directory
        # but allow overwriting the path
        self.kubementat_main_dir = Path(__file__).absolute().parent.parent.parent
        if kubementat_main_dir is not None:
            self.kubementat_main_dir = kubementat_main_dir

        # default platform config dir is the platform_config directory in the kubementat main directory
        self.platform_config_dir = f"{self.kubementat_main_dir}/platform_config"
        if platform_config_dir is not None:
            self.platform_config_dir = platform_config_dir

        # default pipeline run dir is the pipeline-runs directory in tekton_ci/pipeline-runs directory
        self.tekton_pipeline_run_dir = f"{self.kubementat_main_dir}/tekton_ci/pipeline-runs"
        if tekton_pipeline_run_dir is not None:
            self.tekton_pipeline_run_dir = tekton_pipeline_run_dir

        self.TEKTON_API_VERSION = TEKTON_API_VERSION
        self.TEKTON_API_GROUP = TEKTON_API_GROUP
        self.environment = environment
        self.team = team
        self.config = self.load_config()

        Config._instance = self

    @classmethod
    def get_environment(cls):
        return cls._instance.environment

    @classmethod
    def get_team(cls):
        return cls._instance.team

    @classmethod
    def get_kubementat_main_dir(cls):
        return cls._instance.kubementat_main_dir

    @classmethod
    def get_tekton_pipeline_run_dir(cls):
        return cls._instance.tekton_pipeline_run_dir

    @classmethod
    def get_instance(cls, environment=None, team=None):
        if cls._instance is None:
            # If no instance exists and parameters are provided, create new one
            if environment is not None and team is not None:
                cls._instance = cls(environment, team)
            else:
                raise ValueError("Config instance not initialized")
        return cls._instance

    def load_config(self):
        """
        Load configuration files from platform_config directory

        Args:
            environment (str): Environment name
            team (str): Team name

        Returns:
            dict: Configuration values loaded from JSON files
        """

        try:
            with open(f"{self.platform_config_dir}/{self.environment}/static.json", 'r') as f:
                env_static = json.load(f)
            with open(f"{self.platform_config_dir}/{self.environment}/static.encrypted.json", 'r') as f:
                env_static_encrypted = json.load(f)

            # TODO: #REFACTOR , rethink this and the consequences
            # load team config if present
            team_static = {}
            team_static_encrypted = {}

            team_static_path = f"{self.platform_config_dir}/{self.environment}/{self.team}/static.json"
            team_static_encrypted_path = f"{self.platform_config_dir}/{self.environment}/{self.team}/static.encrypted.json"

            if os.path.exists(team_static_path):
                with open(team_static_path, 'r') as f:
                    team_static = json.load(f)
            else:
                logging.warning(f"No team static config found at {team_static_path}")

            if os.path.exists(team_static_encrypted_path):
                with open(team_static_encrypted_path, 'r') as f:
                    team_static_encrypted = json.load(f)
            else:
                logging.warning(f"No team static config found at {team_static_encrypted_path}")

            logging.info(f"Loaded configuration for environment: {self.environment} and team: {self.team}")

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
        Get the configuration value for a given category.

        Args:
            category (str): The category to get the configuration value for.
            Available categories:
                - team_static
                - team_static_encrypted
                - env_static
                - env_static_encrypted

        Returns:
            The configuration dictionary for the given category.
        '''
        return self.config[category]