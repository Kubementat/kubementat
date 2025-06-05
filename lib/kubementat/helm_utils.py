import logging
import os
import subprocess
from kubementat.config import Config

class HelmUtils:
    """Utility class for Helm operations."""

    def __init__(self, environment, team):
        """Initialize Helm utilities class"""
        self.environment = environment
        self.config = Config(environment, team)
        self.components_dir = os.path.join(self.config.kubementat_main_dir, 'scripts', 'automation', 'components')
        return None
      
    def helmfile_apply(self, environment_name: str, helmfile_label_filter: str, interactive: bool = True):
        """
        Apply a Helmfile configuration
        """
        # Get default variables from the environment if not set
        # Tekton installation
        # TODO: #REFACTOR replace with python function call from this class once implemented
        run_file = os.path.join(self.components_dir, "helmfile_apply.sh")
        run_dir = self.components_dir
        arguments = [run_file, self.environment, helmfile_label_filter, str(interactive).lower()]
        try:
            subprocess.run(arguments, check=True, cwd=run_dir)
        except subprocess.CalledProcessError as e:
            logging.info(f"{run_file} exection failed: {e}")
            raise e