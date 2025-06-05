import logging
import os
import subprocess
from kubementat.config import Config
from pathlib import Path

class HelmUtils:
    """Utility class for Helm operations."""

    def __init__(self, environment, team):
        """Initialize Helm utilities class"""
        self.environment = environment
        self.config = Config(environment, team)
        self.components_dir = os.path.join(self.config.kubementat_main_dir, 'scripts', 'automation', 'components')
        return None
      
    def helmfile_apply(self, environment_name: str, helmfile_label_filter: str, interactive: bool = False, helmfile_working_directory=None, helmfile_path=None):
        """
        Apply a Helmfile configuration
        """
        # Get default variables from the environment if not set
        # Access encrypted static JSON from env_static_encrypted
        grafana_admin_user = self.config.get('env_static_encrypted').get('GRAFANA_ADMIN_USER')
        grafana_admin_password = self.config.get('env_static_encrypted').get('GRAFANA_ADMIN_PASSWORD')

        # Set environment variables for Grafana credentials
        os.environ['GRAFANA_ADMIN_USER'] = grafana_admin_user
        os.environ['GRAFANA_ADMIN_PASSWORD'] = grafana_admin_password

        # Determine the helmfile working directory
        if helmfile_working_directory is None:
            helmfile_working_directory = f"{self.config.platform_config_dir}/{self.environment}/kubementat_components"  
        if helmfile_path is None:
            helmfile_path = f"{helmfile_working_directory}/helmfile.yaml"
            
        helmfile_path = Path(helmfile_path).absolute()
        helmfile_working_directory = Path(helmfile_working_directory).absolute()
        
        logging.info(f"Environment name: {self.environment}")
        logging.info(f"Grafan admin user: {grafana_admin_user}")
        logging.info(f"Helmfile working directory: {helmfile_working_directory}")
        logging.info(f"Helmfile path: {helmfile_path}")
        logging.info(f"Helmfile label filter: {helmfile_label_filter}")
                     
        # Prepare the helmfile apply command
        interactive_flag = '--interactive' if interactive else ''
        command = f"helmfile apply --color -f {helmfile_path} -l {helmfile_label_filter} {interactive_flag}"

        # Execute the command
        logging.info(f"Running command: {command}")
        subprocess.run([command], check=True, shell=True, cwd=helmfile_working_directory)