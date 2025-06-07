import logging
import os
import random
import yaml
import json
import base64
import secrets
import shutil
import subprocess
import glob
from kubernetes import client
from kubementat.config import Config
from kubementat.kubernetes_utils import KubernetesUtils
from kubementat.helm_utils import HelmUtils
from kubementat.crypto_utils import CryptoUtils

class Automation:
    '''
    A class that provides Kubernetes automation capabilities for installing Kubementat components, setting up pipelines, managing secrets, and configuring Docker registry access.
    
    Parameters:

    - environment: str - The target environment (e.g., dev, prod)
    - team: str - The team identifier

    Returns:

    - None (performs side-effect operations)
    '''  
    KUBEMENTAT_DEPENDENCIES = [
        "kubectl",
        "helm",
        "helmfile",
        "jq",
        "yq",
        "git",
        "gpg",
        "linkerd",
        "python3",
        "pip"
    ]
    
    INITIALIZATION_SSH_KEY_NAME = "deployer_ssh_key"
    INITIALIZATION_FILENAME_GPG_PRIVATE_KEY = "gpg_private.key"
    INITIALIZATION_FILENAME_GPG_PUBLIC_KEY = "gpg_public_key.gpg"
    INITIALIZATION_FILENAME_GIT_CRYPT_SYMETRIC_KEY = "git_crypt_symmetric.key"

    def __init__(self, environment, team):
        self.environment = environment
        self.team = team
        self.config = Config(environment, team, preload_configs=False)
        self.kubernetes_utils = KubernetesUtils()
        self.helm_utils = HelmUtils(environment, team)

    ###########################
    # kubmentat config intialization
    ###########################
    def initialize(self):
        self.check_kubementat_dependencies()
        self.install_python_dependencies()
        self._initialize_check_required_environment_variables()
        self._check_is_already_initialized()

        # DEPLOYER SSH KEY SETUP
        if not os.path.exists(f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_SSH_KEY_NAME}"):
            logging.info("Generating git deployer ssh key ...")
            CryptoUtils().generate_ssh_key_pair(2048, self.INITIALIZATION_SSH_KEY_NAME, self.config.kubementat_main_dir)
            logging.info("Finished generating git deployer ssh key.")            
        else:
            logging.info("git deployer ssh key is already present on machine. Skipping generation.")

        # GPG setup
        ## generate a gpg key for the deployer user
        
        git_deployer_email = self._generate_deployer_email(os.getenv("BASE_DOMAIN"))
        # TODO: #REFACTOR
        os.environ["GIT_DEPLOYER_EMAIL"] = git_deployer_email

        self._configure_gpg_key(git_deployer_email)

        # TODO: #REFACTOR - better pass a config directory object or something like that
        # base64 encode keys for usage within json templates
        GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64 = self.base64_encode_key(f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GPG_PRIVATE_KEY}")
        os.environ["GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64"] = GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64
        GIT_DEPLOYER_PRIVATE_KEY_BASE64 = self.base64_encode_key(f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_SSH_KEY_NAME}")
        os.environ["GIT_DEPLOYER_PRIVATE_KEY_BASE64"] = GIT_DEPLOYER_PRIVATE_KEY_BASE64
        GIT_DEPLOYER_GPG_PUBLIC_KEY = open(f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GPG_PUBLIC_KEY}", 'r').read()
        os.environ["GIT_DEPLOYER_GPG_PUBLIC_KEY"] = GIT_DEPLOYER_GPG_PUBLIC_KEY

        ###################
        self._create_config_directories()

        kubementat_components_template_dir = f"{self.config.templates_dir}/environment/kubementat_components"
        logging.info(f"Configuring default platform configuration files for {self.environment} kubementat platform components ...")
        shutil.copy2(f"{kubementat_components_template_dir}/helmfile.yaml.template", f"{self.config.platform_config_dir}/{self.environment}/kubementat_components/helmfile.yaml")

        self._write_config_from_templates_for_directory(kubementat_components_template_dir, f"{self.config.platform_config_dir}/{self.environment}/kubementat_components")
        logging.info("#####################")

        logging.info(f"Configuring default platform configuration values files for team {self.team} ...")
        self._write_config_from_templates_for_directory(f"{self.config.templates_dir}/environment/team", f"{self.config.platform_config_dir}/{self.environment}/{self.team}")
        logging.info("#####################")
        
        # write static.json and static.encypted.json configs on env and team level
        self._write_environment_static_json()
        self._write_environment_static_encrypted_json()
        self._write_team_static_json()
        self._write_team_static_encrypted_json()
        
        # configure template for docker image mirroring
        shutil.copy2(f"{self.config.templates_dir}/environment/mirrored_docker_images.json.template", f"{self.config.platform_config_dir}/{self.environment}/mirrored_docker_images.json")
        logging.info("#####################")

        self._configure_git_crypt(git_deployer_email)

        self._initialize_show_summary()

    def generate_password(self, length = 32):
        return secrets.token_urlsafe(length)
            
    # Function to base64 encode keys for usage within json templates
    def base64_encode_key(self, key_filepath):
        readmode = 'rb'
        with open(key_filepath, readmode) as f:
            key_content = f.read()
            
        return base64.b64encode(key_content).decode('utf-8')

    def install_python_dependencies(self):
        logging.info("######################################################")
        logging.info("Installing python dependencies ...")
        subprocess.run(["pip", "install", "-r", "requirements.txt"], 
            cwd = self.config.kubementat_main_dir, check = True)
        logging.info("Installed python dependencies successfully.")
        logging.info("######################################################")

    def _write_environment_static_json(self):
        template_path = f"{self.config.templates_dir}/environment/static.json.template"
        output_path = f"{self.config.platform_config_dir}/{self.environment}/static.json"

        # Read the JSON template
        with open(template_path, 'r') as file:
            config_data = json.load(file)

        # Populate values from environment variables and files
        config_data["AUTOMATION_GIT_URL"] = os.environ["AUTOMATION_GIT_URL"]
        config_data["AUTOMATION_GIT_SERVER_HOST"] = os.environ["AUTOMATION_GIT_SERVER_HOST"]
        config_data["AUTOMATION_GIT_SERVER_PORT"] = os.environ["AUTOMATION_GIT_SERVER_PORT"]
        config_data["AUTOMATION_GIT_SERVER_SSH_USER"] = os.environ["AUTOMATION_GIT_SERVER_SSH_USER"]
        config_data["DOCKER_REGISTRY_BASE_URL"] = os.environ["DOCKER_REGISTRY_BASE_URL"]
        config_data["CLUSTER_MANAGER_EMAIL"] = os.environ["CLUSTER_MANAGER_EMAIL"]
        config_data["BASE_DOMAIN"] = os.environ["BASE_DOMAIN"]
        config_data["GIT_DEPLOYER_GPG_PUBLIC_KEY"] = os.environ["GIT_DEPLOYER_GPG_PUBLIC_KEY"]
        config_data["GIT_DEPLOYER_EMAIL"] = os.environ["GIT_DEPLOYER_EMAIL"]
        with open(f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_SSH_KEY_NAME}.pub", 'r') as key_file:
            config_data["GIT_DEPLOYER_PUBLIC_KEY"] = key_file.read()
        config_data["TEKTON_KUBERNETES_STORAGE_CLASS"] = os.environ["KUBERNETES_DEFAULT_STORAGE_CLASS"]

        # Write the updated JSON to file
        with open(output_path, 'w') as file:
            json.dump(config_data, file, indent=2)

    def _write_environment_static_encrypted_json(self):
        template_path = f"{self.config.templates_dir}/environment/static.encrypted.json.template"
        output_path = f"{self.config.platform_config_dir}/{self.environment}/static.encrypted.json"

        with open(template_path, 'r') as file:
            config_data = json.load(file)

        # Populate values from environment variables and generated passwords
        config_data["GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64"] = os.environ["GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64"]
        config_data["GIT_DEPLOYER_PRIVATE_KEY_BASE64"] = os.environ["GIT_DEPLOYER_PRIVATE_KEY_BASE64"]
        config_data["GRAFANA_ADMIN_PASSWORD"] = self.generate_password()

        with open(output_path, 'w') as file:
            json.dump(config_data, file, indent=2)

    def _write_team_static_json(self):
        template_path = f"{self.config.templates_dir}/environment/team/static.json.template"
        output_path = f"{self.config.platform_config_dir}/{self.environment}/{self.team}/static.json"

        with open(template_path, 'r') as file:
            config_data = json.load(file)

        # Populate storage class values
        storage_class = os.environ["KUBERNETES_DEFAULT_STORAGE_CLASS"]
        config_data["MYSQL_VOLUME_STORAGE_CLASS"] = storage_class
        config_data["REDIS_VOLUME_STORAGE_CLASS"] = storage_class
        config_data["APP_DEPLOYMENT_NAMESPACE"] = self.team
        config_data["PIPELINE_NAMESPACE"] = f"{self.team}-pipelines"

        with open(output_path, 'w') as file:
            json.dump(config_data, file, indent=2)

    def _write_team_static_encrypted_json(self):
        template_path = f"{self.config.templates_dir}/environment/team/static.encrypted.json.template"
        output_path = f"{self.config.platform_config_dir}/{self.environment}/{self.team}/static.encrypted.json"

        with open(template_path, 'r') as file:
            config_data = json.load(file)

        # Populate password and secret values
        config_data["MYSQL_DATABASE_CONFIGURATION"][0]["PASSWORD"] = self.generate_password()
        config_data["MYSQL_ROOT_PASSWORD"] = self.generate_password()
        config_data["REDIS_PASSWORD"] = self.generate_password()
        config_data["GITLAB_WEBHOOK_SECRET"] = self.generate_password()
        config_data["GITHUB_WEBHOOK_SECRET"] = self.generate_password()
        
        # TODO: refactor this when the redis master host and mysql host config is not part of the static.encrypted.json anymore
        config_data["MYSQL_HOST"] = config_data["MYSQL_HOST"].replace('REPLACEME_TEAM', self.team)
        config_data["REDIS_MASTER_HOST"] = config_data["REDIS_MASTER_HOST"].replace('REPLACEME_TEAM',self.team)    
        target_namespace = config_data["SSH_DEPLOY_KEYS"][0]['TARGET_NAMESPACE'].replace('REPLACEME_TEAM', self.team)
        config_data["SSH_DEPLOY_KEYS"][0]['TARGET_NAMESPACE'] = target_namespace

        with open(output_path, 'w') as file:
            json.dump(config_data, file, indent=2)

    def _create_config_directories(self):  
        dir = f"{self.config.platform_config_dir}/{self.environment}"
        logging.info(f"Creating environment config sub-directory for {self.environment}: {dir}")
        os.makedirs(dir, exist_ok=True)
    
        dir = f"{self.config.platform_config_dir}/{self.environment}/{self.team}"
        logging.info(f"Creating team config sub-directory for team: {self.team} : {dir}")
        os.makedirs(dir, exist_ok=True)
        
        dir = f"{self.config.kubementat_main_dir}/tekton_ci/triggers/{self.team}"
        logging.info(f"Creating tekton triggers configuration directory for team {self.team} : {dir}")
        os.makedirs(dir, exist_ok=True)
        
        dir = f"{self.config.platform_config_dir}/{self.environment}/kubementat_components"
        logging.info(f"Creating kubementat components config sub-directory for environment: {self.environment} : {dir}")
        os.makedirs(dir, exist_ok=True)

    # Function to check if kubementat has already been initialized
    def _check_is_already_initialized(self):
        files_to_check = [
            f"{self.config.platform_config_dir}/{self.environment}/static.encrypted.json",
            f"{self.config.platform_config_dir}/{self.environment}/static.json",
            f"{self.config.platform_config_dir}/{self.environment}/{self.team}/static.json",
            f"{self.config.platform_config_dir}/{self.environment}/{self.team}/static.encrypted.json"
        ]
        for file in files_to_check:
            if os.path.exists(file):
                logging.error(f"Found {file}. It seems that kubementat has been initialized already! Exiting.")
                exit(1)

    # Function to check required environment variables
    def _initialize_check_required_environment_variables(self):
        required_vars = [
            "BASE_DOMAIN", "AUTOMATION_GIT_URL", "AUTOMATION_GIT_SERVER_HOST",
            "AUTOMATION_GIT_SERVER_PORT", "AUTOMATION_GIT_SERVER_SSH_USER",
            "KUBERNETES_DEFAULT_STORAGE_CLASS", "DOCKER_REGISTRY_BASE_URL",
            "CLUSTER_MANAGER_EMAIL"
        ]
        for var in required_vars:
            variable_value = os.getenv(var)
            logging.info(f"Read: {var}={variable_value}")
            if not variable_value:
                logging.error(f"Unsufficient environment configuration provided! Exiting.")
                logging.error("Please define the required according ENVIRONMENT variables via exports:")
                logging.error("e.g.:")
                logging.error(f"export {var}='value'")
                exit(1)

    # Function to write config from templates for given directory
    def _write_config_from_templates_for_directory(self, source_directory, target_directory):
        logging.info(f"  template source directory: {source_directory}")
        logging.info(f"  target directory: {target_directory}")
        for root, dirs, files in os.walk(source_directory):
            for dir_name in dirs:
                new_dir_path = os.path.join(target_directory, dir_name)
                logging.info(f"Creating directory: {new_dir_path}")
                os.makedirs(new_dir_path, exist_ok=True)
                for template_file in glob.glob(os.path.join(root, f"{dir_name}/*.template")):
                    base_name = os.path.basename(template_file).replace(".template", "")
                    new_path = os.path.join(new_dir_path, base_name)
                    logging.info(f"  Copy template: {template_file} --> {new_path}")
                    shutil.copy2(template_file, new_path)

    # Function to show summary
    def _initialize_show_summary(self):
        logging.info("##############################")
        logging.info(f"Please ensure to push this repo to a git server:")
        logging.info(f"git remote add upstream {os.getenv('AUTOMATION_GIT_URL')}")
        logging.info("git add .")
        logging.info("git commit -m '00 - initial configuration'")
        logging.info("git push upstream master")
        logging.info("")
        logging.info(f"And configure the git server to allow at minimum read access to the generated ssh key :")
        with open(f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_SSH_KEY_NAME}.pub", "r") as f:
            logging.info(f.read())
        logging.info("")
        logging.info("##############################")
        logging.info("")
        logging.info("Finished initialization successfully!")
        logging.info("")
        logging.info("##############################")
        logging.info("")
        logging.info(f"You can now use this configuration to roll out the platform components on the cluster via:")
        logging.info("")
        logging.info(f"./kmt install {self.environment} {self.team}")
        logging.info("")
        logging.info("In case you encounter error messages you can just rerun the full setup script to continue where the error occured.")
        logging.info("The setup script is non-destructive")
        logging.info("##############################")
        logging.info("")
        logging.info("HAVE FUN WITH YOUR AUTOMATED KUBERNETES CLUSTER :D")
        logging.info("If you have any questions, feel free to create an issue via https://github.com/Kubementat/kubementat/issues")
        logging.info("P.S.: Also feel free to contact me (https://www.linkedin.com/in/julianweberdev/) for help regarding Kubementat or DevOps automation in general")

    # Function to configure git-crypt
    # TODO: REFACTOR: rethink this and find a better approach
    def _configure_git_crypt(self, git_deployer_email):
        if not os.path.exists(f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GIT_CRYPT_SYMETRIC_KEY}"):
            logging.info("Initializing git-crypt ...")
            # initialize (only executed when initializing a new repo with git crypt)
            subprocess.run(["git-crypt", "init"], cwd = self.config.kubementat_main_dir, check = True)

            # add previously generated deployer user to git-crypt keystore
            subprocess.run(["git-crypt", "add-gpg-user", git_deployer_email], cwd = self.config.kubementat_main_dir, check = True)

            # export a symmetric key for git crypt
            subprocess.run(["git-crypt", "export-key", f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GIT_CRYPT_SYMETRIC_KEY}"], cwd = self.config.kubementat_main_dir, check = True)

            logging.info("Finished initializing git-crypt.")
        else:
            logging.info(f"Found {self.INITIALIZATION_FILENAME_GIT_CRYPT_SYMETRIC_KEY} . Git crypt seems to be configured already. Skipping git-crypt configuration.")

    # Function to generate deployer email
    def _generate_deployer_email(self, base_domain):
        random_number = str(random.randint(1, 1000))
        return f"deployer{random_number}@{base_domain}"

    # Function to configure GPG key
    def _configure_gpg_key(self, git_deployer_email):
        private_key_filepath = f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GPG_PRIVATE_KEY}"
        public_key_filepath = f"{self.config.kubementat_main_dir}/{self.INITIALIZATION_FILENAME_GPG_PUBLIC_KEY}"
        
        if not os.path.exists(private_key_filepath):
            logging.info(f"Generating deployer gpg key ...")
            gpg_keys = CryptoUtils().generate_gpg_key(git_deployer_email, 
                private_key_filepath=private_key_filepath, 
                public_key_filepath=public_key_filepath
            )
            logging.info(f"Finished generating deployer gpg key.")
        else:
            logging.info("git deployer gpg key is already present on machine. Skipping generation.")
            return None
        return gpg_keys


    ###########################
    # kubementat cluster installation automation
    ###########################
    def install_kubementat(self, configure_tekton_pipelines=True,
        enable_linkerd=False, helmfile_installation_group='standard'):
        '''
        Main installation logic for Kubementat components including Tekton, Linkerd, and pipeline setup.

        Parameters:

        - configure_tekton_pipelines: bool = True - Whether to configure Tekton pipelines
        - enable_linkerd: bool = False - Whether to install Linkerd service mesh
        - helmfile_installation_group: str = 'standard' - Helmfile installation group

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If dependency checks fail or installation scripts encounter errors
        '''

        self.check_kubementat_dependencies()
        self.validate_cluster_prompt()
        self.check_cluster_permissions()

        # Get default variables from the environment if not set
        components_dir = os.path.join(self.config.kubementat_main_dir, 'scripts', 'automation', 'components')

        logging.info("######################################################")
        logging.info(f"CONFIGURING: Kubementat for environment {self.environment} and team {self.team}")
        logging.info("######################################################")

        # Tekton installation
        # TODO: #REFACTOR replace with python function call from this class once implemented
        run_file = os.path.join(components_dir, "install_tekton.sh")
        self._run_install_script("Tekton", components_dir, run_file)

        # helmfile apply
        group_filter = f"group={helmfile_installation_group}"
        self.helm_utils.helmfile_apply(
            environment_name=self.environment,
            helmfile_label_filter=group_filter, 
            interactive=False
        )

        # Setup pipelines and triggers
        if configure_tekton_pipelines:
            self.setup_pipelines()

            # TODO: #REFACTOR replace with python function call from this class once implemented
            automation_dir = os.path.join(self.config.kubementat_main_dir, "scripts", "automation", "tekton")
            run_file = os.path.join(automation_dir, "setup_triggers.sh")
            self._run_install_script("Tekton Setup Triggers", automation_dir, run_file)
        else:
            logging.info("Skipping Tekton pipeline configuration.")

        # Linkerd confirmation
        if enable_linkerd:
            run_file = os.path.join(components_dir, "install_linkerd.sh")
            self._run_install_script('Linkerd', run_dir=components_dir, run_file=run_file)
        else:
            logging.info("Skipping Linkerd service mesh installation.")

        self._print_install_finish_message()

    def check_kubementat_dependencies(self):
        '''
        Verifies that all required tools are installed.

        Parameters:

        - None

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If any required tool is missing

        '''
        # Check dependencies
        for dep in self.KUBEMENTAT_DEPENDENCIES:
            if shutil.which(dep) is None:
                logging.info(f"Error: {dep} not installed.")
                raise Exception(f"Installation requirements not met: {dep} is not installed!")

    def validate_cluster_prompt(self):
        """Validates cluster connectivity and prompts user confirmation before proceeding.

        This method checks if 'kubectl' can successfully run the cluster-info command,
        then asks for user confirmation to continue with installation. Exits immediately
        if either check fails or user doesn't confirm.
        """

        try:
            subprocess.run(["kubectl", "cluster-info"], check=True)
        except subprocess.CalledProcessError:
            logging.info("Error: kubectl cluster-info failed. Cannot proceed without a valid Kubernetes cluster connection.")
            exit(1)

        while True:
            yn = input("Do you really wish to install kubementat on this cluster? (yes/no): ")
            if yn.lower() in ['yes', 'y']:
                break
            elif yn.lower() in ['no', 'n']:
                logging.info("Cancelled install script.")
                exit(1)
            else:
                logging.info("Please answer yes or no.")

    def check_cluster_permissions(self):
        '''
        Verifies necessary Kubernetes permissions for cluster operations.
        '''
        try:
            subprocess.run(["kubectl", "auth", "can-i", "create", "namespace"], input="yes", text=True, check=True)
            subprocess.run(["kubectl", "auth", "can-i", "create", "deployment"], input="yes", text=True,
                        check=True)
            subprocess.run(["kubectl", "auth", "can-i", "create", "clusterrole"], input="yes", text=True,
                        check=True)
            subprocess.run(["kubectl", "auth", "can-i", "create", "role"], input="yes", text=True, check=True)
            subprocess.run(["kubectl", "auth", "can-i", "create", "daemonset"], input="yes", text=True, check=True)
            subprocess.run(["kubectl", "auth", "can-i", "create", "replicaset"], input="yes", text=True,
                        check=True)
        except subprocess.CalledProcessError as e:
            logging.info(f"Cluster permission check failed: {e}")
            raise e

    def _print_install_finish_message(self):
        logging.info("")
        logging.info("###############################################")
        logging.info("###############################################")
        logging.info("###############################################")
        logging.info("Installed kubementat to cluster successfully :D")
        logging.info("")
        logging.info("Now you are ready to start making it your own.")
        logging.info("")
        logging.info("You can run your first hello world pipeline right now by using the kmt cli tool:")
        logging.info("# View kmt help section: ")
        logging.info("./kmt --help")
        logging.info("")
        logging.info("# execute pipeline run via kmt")
        logging.info(f"./kmt tekton-run-pipeline {self.environment} {self.team} global/hello-world-pipeline-run.yml")
        logging.info("")
        logging.info("# View the results via the tekton dashboard by tunneling via the kmt cli:")
        logging.info("Once this command is executed you can visit http://127.0.0.1:9097 in your browser")
        logging.info("./kmt tunnel-tekton")
        logging.info("###############################################")

    def _run_install_script(self, install_component, run_dir, run_file):
        arguments = [run_file, self.environment, self.team]
        try:
            subprocess.run(arguments, check=True, cwd=run_dir)
        except subprocess.CalledProcessError as e:
            logging.info(f"{install_component} installation failed: {e}")
            raise e

    ###########################
    # setup pipelines automation
    ###########################
    def setup_pipelines(self):
      """
      Configure pipeline infrastructure in a Kubernetes cluster

      This method sets up the complete pipeline environment by:
      1. Creating necessary directories for pipeline runs
      2. Setting up Kubernetes namespaces
      3. Creating SSH and GPG secrets
      4. Configuring service accounts and role bindings
      5. Applying Tekton tasks and pipelines
      6. Displaying setup results

      Parameters:
          self: Automation class instance with access to configuration and Kubernetes utilities
          
      Returns:
          None (performs side-effect operations)
          
      Raises:
          Exception: If any Kubernetes operation fails
      """    
      logging.info(f"Starting setup_pipelines execution with environment: {self.environment} and team: {self.team}")

      helm_deployer_sa = self.config.get('team_static').get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')
      pipeline_ns = self.config.get('team_static').get('PIPELINE_NAMESPACE')
      app_deployment_ns = self.config.get('team_static').get('APP_DEPLOYMENT_NAMESPACE')
      git_ssh_key = self.config.get('env_static_encrypted').get('GIT_DEPLOYER_PRIVATE_KEY_BASE64')
      git_gpg_key = self.config.get('env_static_encrypted').get('GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64')
      team_pipeline_run_directory = os.path.join(self.config.kubementat_main_dir, 'tekton_ci', 'pipeline-runs', self.team)

      logging.info(f"Helm Deployer Service Account: {helm_deployer_sa}")
      logging.info(f"Pipeline Namespace: {pipeline_ns}")
      logging.info(f"App Deployment Namespace: {app_deployment_ns}")
      logging.info(f"Team Pipeline-run directory: {team_pipeline_run_directory}")
      
      # Create the pipeline-runs directory for the team
      logging.info("Creating team pipeline run directory: {team_pipeline_run_directory}")
      if not os.path.isdir(team_pipeline_run_directory):
        os.makedirs(team_pipeline_run_directory)

      # Create namespaces
      self.kubernetes_utils.create_namespace(pipeline_ns)
      self.kubernetes_utils.create_namespace(app_deployment_ns)

      # Setup secrets
      logging.info("Setting up SSH secret: git-deployer-ssh-key")
      self.kubernetes_utils.create_or_replace_secret(
          namespace=pipeline_ns,
          secret_name="git-deployer-ssh-key",
          key_type="kubernetes.io/ssh-auth",
          # The ssh key is already base64 encoded so we don't need to encode it again
          raw_data={"ssh-privatekey": git_ssh_key}
      )
      logging.info("Setting up GPG secret: git-deployer-gpg-key")
      self.kubernetes_utils.create_or_replace_secret(
          namespace=pipeline_ns,
          secret_name="git-deployer-gpg-key",
          key_type="Opaque",
          # HINT: The gpg key is binary so this needs to be double base64 encoded to work within containers as environment variable
          string_data={"private-key": git_gpg_key}
      )

      # Setup service account and roles
      logging.info(f"Setting up service account: {helm_deployer_sa}")
      self.kubernetes_utils.create_service_account(pipeline_ns, helm_deployer_sa,[
          {
              # add the git-deployer-ssh-key to the service account so it will be able to use the git-clone-with-ssh-auth task
              "name": "git-deployer-ssh-key"
          }
      ])

      logging.info(f"Setting up role bindings for {helm_deployer_sa}")
      self._setup_pipelines_setup_role_bindings(pipeline_ns, app_deployment_ns, helm_deployer_sa)

      # Apply kubementat tekton default tasks and pipelines from tekton_ci/ directory
      logging.info("Applying default tekton tasks...")
      self.apply_resources(f"{self.config.kubementat_main_dir}/tekton_ci/tasks", pipeline_ns, kind="Task", group=self.config.TEKTON_API_GROUP, version=self.config.TEKTON_API_VERSION)
      logging.info("Default Tasks applied successfully!")
      logging.info("Applying default tekton pipelines...")
      self.apply_resources(f"{self.config.kubementat_main_dir}/tekton_ci/pipelines", pipeline_ns, kind="Pipeline", group=self.config.TEKTON_API_GROUP, version=self.config.TEKTON_API_VERSION)
      logging.info("Default Pipelines applied successfully!")

      # Apply tasks and pipelines from env directory
      env_dir_tekton = f"{self.config.platform_config_dir}/{self.environment}/tekton"
      team_dir_tekton = f"{self.config.platform_config_dir}/{self.environment}/{self.team}/tekton"
      
      # Apply tekton tasks and pipelines from team directory in platform_config
      for dir in [ env_dir_tekton, team_dir_tekton]:
        tasks_dir = f"{dir}/tasks"
        pipelines_dir = f"{dir}/pipelines"
        if os.path.exists(tasks_dir):
            self.apply_resources(tasks_dir, pipeline_ns, kind="Task", group=self.config.TEKTON_API_GROUP, version=self.config.TEKTON_API_VERSION)
        else:
            logging.info(f"Tasks directory {tasks_dir} does not exist. Skipping.")
        if os.path.exists(pipelines_dir):
            self.apply_resources(pipelines_dir, pipeline_ns, kind="Pipeline", group=self.config.TEKTON_API_GROUP, version=self.config.TEKTON_API_VERSION)
        else:
            logging.info(f"Pipelines directory {pipelines_dir} does not exist. Skipping.")
      

      logging.info("Tekton Pipeline and Task Setup completed successfully!")
      self._setup_pipelines_show_results(pipeline_ns, app_deployment_ns, helm_deployer_sa)

    def _setup_pipelines_setup_role_bindings(self, pipeline_ns, app_ns, sa_name):
        '''
        Creates role bindings for pipeline namespace and app deployment.

        Parameters:

        - pipeline_ns: str - Pipeline namespace
        - app_ns: str - Application deployment namespace
        - sa_name: str - Service account name

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If role binding creation fails
        '''
        # Define the Role Binding for the app deployment
        # Create within app namespace but point to service account within pipeline namespace
        logging.info(f"Creating role binding for cluster role helm-deployer-cluster-role in namespace {app_ns} pointing to service account {sa_name} in namespace {pipeline_ns}")
        binding = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(name="helm-deployer-role-binding-app-deployment", labels={"managed-by": "kubementat"}),
            subjects=[client.RbacV1Subject(kind="ServiceAccount", name=sa_name, namespace=pipeline_ns)],
            role_ref=client.V1RoleRef(kind="ClusterRole", name="helm-deployer-cluster-role", api_group="rbac.authorization.k8s.io")
        )
        self.kubernetes_utils.create_or_replace_role_binding(app_ns, "helm-deployer-role-binding-app-deployment", binding)

        # Define Role Binding for pipeline namespace
        logging.info(f"Creating role binding for cluster role helm-deployer-cluster-role in namespace {pipeline_ns} pointing to service account {sa_name} in namespace {pipeline_ns}")
        binding = client.V1RoleBinding(
            metadata=client.V1ObjectMeta(name="helm-deployer-role-binding-pipeline-namespace-access", labels={"managed-by": "kubementat"}),
            subjects=[client.RbacV1Subject(kind="ServiceAccount", name=sa_name, namespace=pipeline_ns)],
            role_ref=client.V1RoleRef(kind="ClusterRole", name="helm-deployer-cluster-role", api_group="rbac.authorization.k8s.io")
        )
        self.kubernetes_utils.create_or_replace_role_binding(pipeline_ns, "helm-deployer-role-binding-pipeline-namespace-access", binding)

    def apply_resources(self, resource_dir, target_namespace, kind, group, version):
        '''
        Applies Kubernetes resources from specified directories.

        Parameters:

        - resource_dir: str - Directory containing resource files
        - target_namespace: str - Target namespace for resource application
        - kind: str - Resource type (Task/Pipeline)
        - group: str - API group
        - version: str - API version

        Returns:

        - None (performs side-effect operations)
        '''
        logging.info(f"Applying {kind} resources from directory: {resource_dir} in namespace: {target_namespace} ...")

        for filename in os.listdir(resource_dir):
            if filename.endswith('.yml'):
                logging.info(f"Processing file: {filename}")
                file_path = os.path.join(resource_dir, filename)

                try:
                    with open(file_path, 'r') as f:
                        resource_content = f.read()

                    # Parse the YAML content
                    resources = yaml.safe_load_all(resource_content)

                    for resource in resources:
                        if not isinstance(resource, dict):
                            continue  # Skip non-dictionary objects

                        logging.info(f"Resource name: {resource['metadata']['name']}")

                        self.kubernetes_utils.create_or_replace_custom_resource(
                            group=group,
                            version=version,
                            namespace=target_namespace,
                            kind=kind,
                            body=resource
                        )

                except Exception as e:
                    logging.error(f"Failed to apply {filename}: {str(e)}")
                    logging.error(f"Error details: {e.__traceback__}")
                    raise

        return

    def _setup_pipelines_show_results(self, pipeline_ns, app_ns, helm_deployer_sa):
        """Display the results of the setup including service account, role-bindings,
        Secrets, Tasks and Pipeline Custom Tekton Resources."""

        logging.info("=== Service Account ===")
        logging.info(f"Name: {helm_deployer_sa}")
        logging.info(f"Namespace: {pipeline_ns}")
        sa = self.kubernetes_utils.get_service_account(
            namespace=pipeline_ns,
            name=helm_deployer_sa
        )
        logging.info(sa)

        logging.info("=== Role Bindings ===")
        logging.info("Pipeline Namespace Role Binding:")
        rb = self.kubernetes_utils.get_role_binding(
            namespace=pipeline_ns,
            name="helm-deployer-role-binding-pipeline-namespace-access"
        )
        logging.info(rb)

        logging.info("App Namespace Role Binding:")
        rb_app = self.kubernetes_utils.get_role_binding(
            namespace=app_ns,
            name="helm-deployer-role-binding-app-deployment"
        )
        logging.info(rb_app)

        logging.info("=== Secrets ===")
        secrets = self.kubernetes_utils.list_secrets(namespace=pipeline_ns)
        for secret in secrets.items:
            logging.info(f"- {secret.metadata.name}")

        logging.info("=== Tasks ===")
        tasks = self.kubernetes_utils.list_custom_resources(
            namespace=pipeline_ns,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier="tasks"
        )

        for task in tasks['items']:
            logging.info(f"- {task['metadata']['name']}")

        logging.info("=== Pipelines ===")
        pipelines = self.kubernetes_utils.list_custom_resources(
            namespace=pipeline_ns,
            group=self.config.TEKTON_API_GROUP,
            version=self.config.TEKTON_API_VERSION,
            plural_identifier="pipelines"
        )
        for pipeline in pipelines['items']:
            logging.info(f"- {pipeline['metadata']['name']}")

        return None
    ###########################

    # secret setup automation
    ###########################
    def setup_secrets(self):
        '''
        Configures SSH and GPG secrets in the pipeline namespace.

        Parameters:

        - None (uses instance configuration)

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If secret creation fails
        '''
        namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')
        logging.info(f"Environment: {self.environment}")
        logging.info(f"Team: {self.team}")
        logging.info(f"Namespace: {namespace}")

        try:
            # Get the SSH keys from the config
            ssh_keys = self.config.get('team_static_encrypted').get('SSH_DEPLOY_KEYS')  # Returns list of SSH key dicts


            logging.info("Setting up secrets in namespace %s", namespace)

            # Iterate through each SSH key and create secret
            for key_data in ssh_keys:
                name = key_data['NAME']
                namespace = key_data['TARGET_NAMESPACE']
                secret_name = key_data['TARGET_SECRET_NAME']
                private_key_base64 = key_data['PRIVATE_KEY_BASE64']

                logging.info(f"Configuring SSH key: {name} as secret: {secret_name} in namespace: {namespace}")

                self.kubernetes_utils.create_or_replace_secret(
                namespace=namespace,
                secret_name=secret_name,
                key_type="kubernetes.io/ssh-auth",
                # The ssh key is already base64 encoded so we don't need to encode it again
                raw_data={"ssh-privatekey": private_key_base64}
                )

            logging.info(f"Secrets configured successfully in namespace {namespace} .")
            self._setup_secrets_show_results(namespace)
        except Exception as e:
            logging.error(f"Failed to configure secrets: {str(e)}")
            raise
        return None

    def _setup_secrets_show_results(self, pipeline_ns):
        """Display the results of the secrets configured"""

        logging.info(f"=== Secrets within namespace {pipeline_ns} ===")
        secrets = self.kubernetes_utils.list_secrets(namespace=pipeline_ns)
        for secret in secrets.items:
            logging.info(secret.metadata.name)

        return None

    # docker registry access automation
    ###########################
    def setup_docker_registry_access(self):
        '''
        Configures Docker registry access for specified namespaces.

        Parameters:

        - None (uses instance configuration)

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If Docker secret creation fails
        '''
        pipeline_namespace = self.config.get('team_static').get('PIPELINE_NAMESPACE')
        app_deployment_namespace = self.config.get('team_static').get('APP_DEPLOYMENT_NAMESPACE')
        helm_deployer_service_account_name = self.config.get('team_static').get('HELM_DEPLOYER_SERVICE_ACCOUNT_NAME')
        docker_registry_credentials = self.config.get('team_static_encrypted').get('DOCKER_REGISTRY_CREDENTIALS')

        logging.info(f"Environment: {self.environment}")
        logging.info(f"Team: {self.team}")
        logging.info(f"Pipeline Namespace: {pipeline_namespace}")
        logging.info(f"App Deployment Namespace: {app_deployment_namespace}")
        logging.info(f"Helm Deployer Service Account Name: {helm_deployer_service_account_name}")

        # Start constructing json array for service account patch calls
        image_pull_secrets = []

        # Process each namespace
        for namespace in [app_deployment_namespace, pipeline_namespace]:
            logging.info(f"Configuring namespace: {namespace} ...")

            logging.info("Configuring DOCKER_REGISTRY_CREDENTIALS ...")
            for credential in docker_registry_credentials:
                name = credential["NAME"]
                auth_url = credential["DOCKER_REGISTRY_AUTH_URL"]
                email = credential["DOCKER_REGISTRY_EMAIL"]
                password = credential["DOCKER_REGISTRY_PASSWORD"]
                username = credential["DOCKER_REGISTRY_USERNAME"]

                self.kubernetes_utils.create_or_replace_docker_secret(namespace, name, auth_url, username, password, email)

                # Add to image pull secrets only once
                if namespace == app_deployment_namespace and {"name": name} not in image_pull_secrets:
                    image_pull_secrets.append({"name": name})

        # Patch the service accounts with image pull secrets
        self.kubernetes_utils.patch_service_account_with_pull_secrets(pipeline_namespace, helm_deployer_service_account_name, image_pull_secrets)
        # TODO: REFACTOR: Make the app deployment namespace service account name configurable via platform_config
        self.kubernetes_utils.patch_service_account_with_pull_secrets(app_deployment_namespace, 'default', image_pull_secrets)

        logging.info("Finished configuring docker registry access.")
        self._setup_docker_registry_access_show_results(pipeline_namespace, app_deployment_namespace, helm_deployer_service_account_name)

    def _setup_docker_registry_access_show_results(self, pipeline_ns, app_ns, helm_deployer_sa):
        """
        Display the results of the script execution.
        Args:
            pipeline_ns (str): The namespace of the pipeline
            app_ns (str): The namespace of the app
            helm_deployer_sa (str): The name of the helm deployer service account
        """
        logging.info("########################")
        logging.info("Results:")
        logging.info("########################")
        logging.info("")
        logging.info("=== Service Accounts ===")
        logging.info(f"Name: {helm_deployer_sa}")
        logging.info(f"Namespace: {pipeline_ns}")
        sa = self.kubernetes_utils.get_service_account(
            namespace=pipeline_ns,
            name=helm_deployer_sa
        )
        logging.info(sa)

        logging.info(f"Namespace: {app_ns}")
        logging.info(f"Name: default")
        sa = self.kubernetes_utils.get_service_account(
            namespace=app_ns,
            name="default"
        )
        logging.info(sa)

    ###########################

    ## uninstall pipelines
    def uninstall_pipelines(self):
        '''
        Deletes the pipeline namespace to uninstall pipelines.

        Parameters:

        - None (uses instance configuration)

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If namespace deletion fails
        '''
        logging.info(f"Starting uninstall_pipelines execution with environment: {self.environment} and team: {self.team}")
        pipeline_ns = self.config.get('team_static').get('PIPELINE_NAMESPACE')
        logging.info(f"Pipeline Namespace: {pipeline_ns}")

        self.kubernetes_utils.delete_namespace(pipeline_ns)
        logging.info(f"Successfully uninstalled pipelines for environment: {self.environment} , team: {self.team}")


    ###########################

    ## tunnels
    def open_tunnel_grafana(self, local_port=3001, remote_port=3000, host='0.0.0.0'):
        '''
        Opens a tunnel to the Grafana dashboard.

        Parameters:

        - local_port: int = 3001 - Local port for tunnel
        - remote_port: int = 3000 - Remote port (Grafana default)
        - host: str = '0.0.0.0' - Host IP

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If tunneling fails
        
        '''
        # TODO: make this a config
        namespace = 'grafana'
        component_name = 'grafana'
        selector = f"app.kubernetes.io/name={component_name},app.kubernetes.io/instance={component_name}"
        pod_name = self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
        return self.kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)

    def open_tunnel_tekton_dashboard(self, local_port=9097, remote_port=9097, host='0.0.0.0'):
        '''
        Opens a tunnel to the Tekton dashboard.

        Parameters:

        - local_port: int = 9097 - Local port for tunnel
        - remote_port: int = 9097 - Remote port (Tekton default)
        - host: str = '0.0.0.0' - Host IP

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If tunneling fails

        '''
        # TODO: make this a config
        namespace = 'tekton-pipelines'
        selector = "app=tekton-dashboard"
        pod_name = self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
        return self.kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)

    def open_tunnel_polaris(self, local_port=8082, remote_port=8080, host='0.0.0.0'):
        '''
        Opens a tunnel to the Polaris service.

        Parameters:

        - local_port: int = 8082 - Local port for tunnel
        - remote_port: int = 8080 - Remote port (Polaris default)
        - host: str = '0.0.0.0' - Host IP

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If tunneling fails
        '''
        # TODO: make this a config
        namespace = 'polaris'
        selector = "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris"
        pod_name = self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
        return self.kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)

    def open_tunnel_vault(self, local_port=8205, remote_port=8200, host='0.0.0.0'):
        '''
        Opens a tunnel to the Vault service.

        Parameters:

        - local_port: int = 8205 - Local port for tunnel
        - remote_port: int = 8200 - Remote port (Vault default)
        - host: str = '0.0.0.0' - Host IP

        Returns:

        - None (performs side-effect operations)

        Raises:

        - Exception - If tunneling fails
        '''
        # TODO: make this a config
        namespace = 'vault'
        selector = "app.kubernetes.io/instance=vault,app.kubernetes.io/name=vault"
        pod_name = self.kubernetes_utils.get_pod_name_for_deployment(namespace, selector)
        return self.kubernetes_utils.open_tunnel(namespace, pod_name, local_port, remote_port, host)