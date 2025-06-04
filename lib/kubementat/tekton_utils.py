from kubementat.kubernetes_utils import KubernetesUtils
from kubementat.config import Config
import logging
from pathlib import Path
import random
import string
import glob
import yaml
import random
import string

class TektonUtils:
    def __init__(self, environment, team):
        self.kubernetes_utils = KubernetesUtils()
        self.environment = environment
        self.team = team
        self.config = Config(environment, team)
        self.pipeline_namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')

    def list_pipelines(self):
        return self._list_tekton_resource(self.pipeline_namespace, "pipelines")

    def list_tasks(self):
        return self._list_tekton_resource(self.pipeline_namespace, "tasks")

    def list_taskruns(self):
        return self._list_tekton_resource(self.pipeline_namespace, "taskruns")

    def list_pipeline_runs(self):
        return self._list_tekton_resource(self.pipeline_namespace, "pipelineruns")

    def list_all_tekton_resources(self):
        self.list_pipelines()
        self.list_tasks()
        self.list_pipeline_runs()
        self.list_taskruns()
        return None

    def _list_tekton_resource(self, namespace, plural_identifier):
        objects = self.kubernetes_utils.list_custom_resources(
            namespace=namespace,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier=plural_identifier
        )
        logging.info(f"### Listing {plural_identifier} in namespace: {namespace}")

        if len(objects['items']) < 1:
            logging.info("No resources found.")
            return None

        for object in objects['items']:
            logging.info(f"    {object['metadata']['name']} - created at: {object['metadata']['creationTimestamp']}")

        return objects

    def cleanup_pipeline_runs(self, filter: str):
        runs = self.list_pipeline_runs()
        logging.info(f"Cleaning up pipeline runs in namespace: {self.pipeline_namespace} with filter: {filter}")

        if runs is None:
            logging.info("No pipeline runs found.")
            return None

        for run in runs['items']:
            run_name = run['metadata']['name']
            self.cleanup_pipeline_run(run_name, filter, run_object=run)
        return None

    def cleanup_pipeline_run(self, run_name: str, filter="all", run_object=None):
        should_be_deleted=False
        if run_object is None:
            should_be_deleted = True
        if filter == "all":
            should_be_deleted = True

        if not should_be_deleted and run_object is not None:
            run_condition_type = run_object['status']['conditions'][0]['type']
            run_condition_status = run_object['status']['conditions'][0]['status']
            if run_condition_type == "Succeeded" and run_condition_status == "True":
                should_be_deleted = True
                logging.info(f"Pipeline run {run_name} is in a successful state, deleting it")

        if not should_be_deleted:
            return None

        logging.info(f"Deleting pipeline run {run_name}")
        self.kubernetes_utils.delete_custom_resource(resource_name=run_name,
            namespace=self.pipeline_namespace,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier="pipelineruns"
        )
        return None

    def generate_random_postfix(self, length: int = 6) -> str:
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length)).lower()


    def list_available_pipeline_runs(self) -> None:
        """List available pipeline run files for the given team."""
        logging.info(f"Team '{self.team}' pipeline runs:")
        team_dir = Path(f"{self.config.tekton_team_pipeline_run_dir}")
        if team_dir.exists():
            for yml_file in team_dir.glob("*.yml"):
                logging.info(f"  {Path(yml_file).name}")
        else:
            logging.info(f"  No team-specific pipeline runs found for {self.team}")

        logging.info("##############")
        logging.info("Global pipeline runs:")
        global_dir = Path(self.config.tekton_pipeline_run_dir)
        if global_dir.exists():
            for yml_file in global_dir.glob("*.yml"):
                logging.info(f"  global/{Path(yml_file).name}")
        else:
            logging.info("  No global pipeline runs found")
        logging.info("##############")

    def check_running_pipelines(self, pipeline_name: str):
        """Check if there are any running instances of the given pipeline."""
        pipeline_runs = self.list_pipeline_runs()
        if not pipeline_runs or 'items' not in pipeline_runs:
            return []

        running_pipelines = []
        for run in pipeline_runs['items']:
            spec = run.get('spec', {})
            pipeline_ref = spec.get('pipelineRef', {})
            status = run.get('status', {}).get('conditions', [{}])[0]

            if (pipeline_ref.get('name') == pipeline_name and
                status.get('reason') == "Running"):
                running_pipelines.append(run['metadata']['name'])

        return running_pipelines

    def run_pipeline(self, pipeline_run_file_identifier: str, allow_parallel_run: bool = False):
        """
        Run a Tekton pipeline using the specified parameters.

        Args:
            pipeline_run_file_identifier: Identifier for the the pipeline run YAML file e.g. smoke/bla-pipeline-run.yml or for global pipeline-runs hello-pipeline-run.yml
            allow_parallel_run: Whether to allow parallel runs of the same pipeline
        """

        # Load configuration values
        env_static = self.config.get('env_static')
        team_static = self.config.get('team_static')

        pipeline_namespace = team_static.get('PIPELINE_NAMESPACE')

        # Print configuration information
        logging.info("#########################")
        logging.info("Loading configuration from platform_config ...")
        logging.info(f"ENVIRONMENT: {self.environment}")
        logging.info(f"TEAM: {self.team}")
        logging.info(f"PIPELINE_NAMESPACE: {pipeline_namespace}")
        logging.info(f"TEKTON_KUBERNETES_STORAGE_CLASS: {env_static.get('TEKTON_KUBERNETES_STORAGE_CLASS')}")
        logging.info("")
        logging.info(f"DOCKER_REGISTRY_BASE_URL: {env_static.get('DOCKER_REGISTRY_BASE_URL')}")
        logging.info(f"TEKTON_CI_IMAGE_NAME: {env_static.get('TEKTON_CI_IMAGE_NAME')}")
        logging.info(f"TEKTON_CI_IMAGE_TAG: {env_static.get('TEKTON_CI_IMAGE_TAG')}")
        logging.info("")
        logging.info(f"AUTOMATION_GIT_URL: {env_static.get('AUTOMATION_GIT_URL')}")
        logging.info(f"AUTOMATION_GIT_PROJECT_NAME: {env_static.get('AUTOMATION_GIT_PROJECT_NAME')}")
        logging.info(f"AUTOMATION_GIT_REVISION: {env_static.get('AUTOMATION_GIT_REVISION')}")
        logging.info(f"AUTOMATION_GIT_SERVER_HOST: {env_static.get('AUTOMATION_GIT_SERVER_HOST')}")
        logging.info(f"AUTOMATION_GIT_SERVER_PORT: {env_static.get('AUTOMATION_GIT_SERVER_PORT')}")
        logging.info(f"AUTOMATION_GIT_SERVER_SSH_USER: {env_static.get('AUTOMATION_GIT_SERVER_SSH_USER')}")
        logging.info("")
        logging.info(f"HELM_DEPLOYER_SERVICE_ACCOUNT_NAME: {team_static.get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')}")
        logging.info("")

        logging.info("#########################")
        logging.info(f"Running pipeline run file {pipeline_run_file_identifier} in {pipeline_namespace} namespace ...")

        if pipeline_run_file_identifier.startswith("global/"):
            logging.info(f"Running global pipeline!")
            pipeline_run_file_path = f"{self.config.tekton_pipeline_run_dir}/{pipeline_run_file_identifier.replace('global/', '')}"
        else:
            pipeline_run_file_path = f"{self.config.tekton_team_pipeline_run_dir}/{pipeline_run_file_identifier}"
        logging.info(f"Running file: {pipeline_run_file_path}")

        # Read the pipeline run file
        try:
            with open(pipeline_run_file_path, 'r') as f:
                pipeline_run_content = f.read()
                pipeline_run_yaml = yaml.safe_load(pipeline_run_content)
        except Exception as e:
            logging.error(f"Error reading pipeline run file: {e}")
            return 1

        # Extract pipeline information
        name_in_file = pipeline_run_yaml['metadata']['name']
        pipeline_name = pipeline_run_yaml['spec']['pipelineRef']['name']
        logging.info(f"Pipeline run name: {name_in_file}")
        logging.info(f"Pipeline run target pipeline: {pipeline_name}")

        # Check for running pipelines
        if not allow_parallel_run:
            running_pipelines = self.check_running_pipelines(pipeline_name)
            if running_pipelines:
                logging.info("##############################")
                logging.info(f"There are running tasks for pipeline {pipeline_name} within the namespace {pipeline_namespace}")
                logging.info(f"Running pipelines: {', '.join(running_pipelines)}")
                logging.info("##############################")
                logging.info("Listing runs for reference")
                pipeline_runs = self.list_pipeline_runs()
                if pipeline_runs and 'items' in pipeline_runs:
                    for run in pipeline_runs['items']:
                        status_reason = "Unknown"
                        if run.get('status', {}).get('conditions'):
                            status_reason = run['status']['conditions'][0].get('reason', 'Unknown')
                        logging.info(f"  {run['metadata']['name']} - Status: {status_reason}")
                logging.info("##############################")
                logging.info("CANCELLED TO AVOID PARALLEL RUN!")
                return 1

        # Generate pipeline run name with random postfix
        random_postfix = self.generate_random_postfix()
        pipeline_run_name = f"{name_in_file}-{random_postfix}"
        logging.info(f"Generated pipeline-run name: {pipeline_run_name}")

        # Update the name in the YAML
        pipeline_run_yaml['metadata']['name'] = pipeline_run_name

        self._replace_placeholders_in_dict(pipeline_run_yaml)

        # Display the pipeline run
        logging.info("#########################")
        logging.info("Applying pipeline run:")
        logging.info(yaml.dump(pipeline_run_yaml))
        logging.info("#########################")

        # Apply the pipeline run using KubernetesUtils
        try:
            self.kubernetes_utils.create_or_replace_custom_resource(
                group=self.config.TEKTON_API_GROUP,
                version=self.config.TEKTON_API_VERSION,
                namespace=pipeline_namespace,
                kind="PipelineRun",
                body=pipeline_run_yaml,
                plural_identifier="pipelineruns"
            )

            # List pipeline runs using TektonUtils
            logging.info("Pipeline runs in namespace:")
            self.list_pipeline_runs()
            logging.info("######################")
            logging.info("You can open a tunnel to the tekton ui using:")
            logging.info("./kmt tunnel-tekton")
            logging.info("######################")
            return 0
        except Exception as e:
            logging.error(f"Error applying pipeline run: {e}")
            return 1

    # helper method for run_pipeline: Replace placeholders in the YAML recursively
    def _replace_placeholders_in_dict(self, data) -> None:
        # TODO: #REFACTOR: refactor this massively :D
        # Create placeholder values from configuration
        placeholder_values = {
            "DOCKER_REGISTRY_BASE_URL": self.config.get('env_static').get('DOCKER_REGISTRY_BASE_URL'),
            "TEKTON_CI_IMAGE_NAME": self.config.get('env_static').get('TEKTON_CI_IMAGE_NAME'),
            "TEKTON_CI_IMAGE_TAG": self.config.get('env_static').get('TEKTON_CI_IMAGE_TAG'),
            "STORAGE_CLASS": self.config.get('env_static').get('TEKTON_KUBERNETES_STORAGE_CLASS'),
            "AUTOMATION_GIT_PROJECT_NAME": self.config.get('env_static').get('AUTOMATION_GIT_PROJECT_NAME'),
            "AUTOMATION_GIT_REVISION": self.config.get('env_static').get('AUTOMATION_GIT_REVISION'),
            "AUTOMATION_GIT_SERVER_HOST": self.config.get('env_static').get('AUTOMATION_GIT_SERVER_HOST'),
            "AUTOMATION_GIT_SERVER_PORT": self.config.get('env_static').get('AUTOMATION_GIT_SERVER_PORT'),
            "AUTOMATION_GIT_SERVER_SSH_USER": self.config.get('env_static').get('AUTOMATION_GIT_SERVER_SSH_USER'),
            "AUTOMATION_GIT_URL": self.config.get('env_static').get('AUTOMATION_GIT_URL'),
            "HELM_DEPLOYER_SERVICE_ACCOUNT_NAME": self.config.get('team_static').get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')
        }

        if isinstance(data, dict):
            for key, value in list(data.items()):
                if isinstance(value, (dict, list)):
                    self._replace_placeholders_in_dict(value)
                elif isinstance(value, str):
                    for placeholder_key, placeholder_value in placeholder_values.items():
                        if placeholder_value and f"{placeholder_key}_PLACEHOLDER" in value:
                            data[key] = value.replace(f"{placeholder_key}_PLACEHOLDER", placeholder_value)
        elif isinstance(data, list):
            for item in data:
                self._replace_placeholders_in_dict(item)