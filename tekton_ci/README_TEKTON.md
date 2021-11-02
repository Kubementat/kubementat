# Tekton Pipelines
[Tekton](https://tekton.dev/) serves as the main CI tool for all automations running on the [kubernetes cluster](https://kubernetes.io/).
The information below describes the initial setup and usage of the automated tasks for building and running applications on the k8s cluster.

# Install prerequisites
- ssh tools installed
- openssl library and tools installed (https://www.openssl.org/)
- kubectl - https://kubernetes.io/docs/tasks/tools/
- tekton tkn cli - https://github.com/tektoncd/cli
- jq: https://stedolan.github.io/jq/download/
- yq: https://github.com/mikefarah/yq
- Optional: Install tekton VSCode Plugin
  - https://marketplace.visualstudio.com/items?itemName=redhat.vscode-tekton-pipelines
- Optional: Install kubernetes tools for VSCode Plugin
  - https://marketplace.visualstudio.com/items?itemName=ms-kubernetes-tools.vscode-kubernetes-tools

# Preparations (Only for new environments)
As the "dev" environment is already configured in platform_config/dev we can keep the configuration as is once we have git-crypt unlocked the repo (see README_GIT_CRYPT.md).

If you want to setup a new additional environment this configuration can be copied over and adjusted accordingly. The sub-topics below describe the key generation for a new env.

## SSH Deployer Key
If you want to setup a new environment with a new deployer key you need to generate the according key via:
```
# generate ssh key
ssh-keygen -t rsa -b 4096 -C "service-deployer" -f service-deployer.key

## base 64 encode ssh key

### ATTENTION: base64 behaves differently on some Operating systems
# OS X Variant
cat service-deployer.key | base64

# Arch linux variant
cat service-deployer.key | base64 -w 0
```

## Git-crypt deployer gpg key
If you need to add a new deployer gpg key
see README_GIT_CRYPT.md

# Initial setup and installation instructions
## Automated Tekton Installation
```
cd automation/components
# ./install_tekton.sh <ENVIRONMENT>
./install_tekton.sh dev
```

## Automated Tekton pipeline and task setup
The setup_pipelines.sh script will install all needed pipeline and task resources for the given environment and team.
You can execute the script via:

```
cd automation
# ./setup_pipelines.sh <ENVIRONMENT> <TEAM>

# e.g. for ENVIRONMENT=dev and TEAM=dev1
./setup_pipelines.sh dev dev1

# or for ENVIRONMENT=dev and TEAM=dev2
./setup_pipelines.sh dev dev2
```

## Automated tekton trigger setup
Tekton triggers are a feature of tekton that can be used to spin up endpoints for receiving gitlab/github/... webhook requests. This allows automatic starting of pipelines on git events like push, tag, ...

To setup the configured triggers for an environment run:
```
cd automation
# ./setup_triggers.sh <ENVIRONMENT> <TEAM>

# e.g. for ENVIRONMENT=dev and TEAM=dev1
./setup_triggers.sh dev dev1
```

## Adding new tekton triggers
This will setup a skeleton trigger template and other needed resources as a starting point for further customization. The files will be placed in the according triggers sub-directory (see the output of the script for more details).

```
cd automation

# example for app "nginx-example" using pipeline with name "build-pipeline-nginx-example"
export ENVIRONMENT='dev'
export TEAM='dev1'
export DOCKER_REGISTRY_BASE_URL='your.dockerregistry.com/your-namespace'
export APP_NAME='nginx-example'
export PIPELINE_NAME='build-pipeline-nginx-example'
export TRIGGER_TYPE='github'
./generate_trigger_config_from_templates.sh

```


# Usage instructions

## Access tekton dashboard via kubectl local tunneling
As the tekton dashboard is not accessible via ingress per default we need to open a tunnel via kubectl.
For opening the tunnel you can use the helper script as follows:
```
cd utilities
# general: ./open_tekton_dashboard_tunnel.sh <ENVIRONMENT>
./open_tekton_dashboard_tunnel.sh dev
# leave the console open

# in another console or the preferably the browser open:
curl http://127.0.0.1:9097/#/pipelines
```

## Run a pipeline
For running a pipeline (= starting a Tekton pipelinerun) you can use the run_pipeline.sh helper script to simplify pipeline runs.

```
cd automation

# For listing available pipeline runs you can call the run_pipeline.sh script without providing a pipeline run definition yaml file
# General: ./run_pipeline <ENVIRONMENT> <TEAM>

# Then after choosing the according pipeline run you wish to execute you can start the run via
# General usage: ./run_pipeline <ENVIRONMENT> <TEAM> <PIPELINE_RUN_DEFINITION_YAML> <OPTIONAL: ALLOW_PARALLEL_RUN (default: true)>

# e.g. for the build pipeline of the field boundary service in environment=dev and team=dev1
./run_pipeline dev dev1 ../pipeline-runs/dev1/deploy-pipeline-nginx-example-run.yml

# e.g. for the setup pipeline of the mysql backing service in environment=dev and team=dev2
./run_pipeline dev dev2 ../pipeline-runs/dev2/setup-pipeline-mysql-run.yml

# if you want to ensure that there is no parallel run of the pipeline you want to execute you can set the ALLOW_PARALLEL_RUN argument to false, e.g for the ci image build process:
./run_pipeline dev dev1 ../pipeline-runs/build-pipeline-ci-images-run.yml false
```

## List all tekton resources within an environment and team
The list_tekton_resources.sh script will list pipelines, tasks and pipeline-runs for the given environment and team. This can be very handy to get an overview of what is happening within the CI pipelines.

```
cd automation

# General: list_tekton_resources.sh <ENVIRONMENT_NAME> <TEAM>

# e.g. for environment=dev and team=dev1
list_tekton_resources.sh dev dev1
```

## Cleanup pipeline runs
Pipeline runs will stay within the k8s pipeline namespace after execution. This will also leave the pipeline run's workspaces intact, which uses kubernetes volumes as storage. So it is not recommended to keep all old pipeline runs as the cluster storage space will fill up if not cleaned up.

```
cd automation

# For cleaning up all successful pipeline runs for an environment and team:
# General: ./cleanup_successful_pipeline_runs.sh <ENVIRONMENT> <TEAM>

# e.g. for environment=dev and team=dev1
./cleanup_successful_pipeline_runs.sh dev dev1

# For cleaning up all pipeline runs (including failed ones) for an environment and team:
# General: ./cleanup_all_pipeline_runs.sh <ENVIRONMENT> <TEAM>

# e.g. for environment=dev and team=dev1
./cleanup_all_pipeline_runs.sh dev dev1
```

# Developer guide

## Directory structure
This section describes the general directory structure for the tekton related resources.
- automation: contains helper scripts that can be run from a developer machine for simplifying usage of the tekton setup and pipeline update/running (see sections above for more details)
- pipelines: contains the tekton pipeline definition yaml files chaining tasks together to a pipeline
- tasks: contains the tekton task definition yaml files describing the automations
- pipeline-runs: contains pipeline run configuration yaml files which are used by the run_pipeline.sh script described in the section above. It contains sub-directories for each team configuring the runs with the according team's configuration (currently dev1 and dev2). General tasks which are not dependending on team configuration are located in the pipeline-runs root directory (e.g. build-pipeline-ci-images-run.yml)
- scripts: contains scripts that are executed from within tekton tasks of the automation, e.g. install_mysql_helm_chart.sh
- examples: contains some tekton examples for getting started with tekton automation development, this is not used within the automation

## Making code changes
This section describes the general processes for extending the existing automations:

- In case you are adding or changing automation scripts in the "scripts" sub-directory you need to commit the changes to the master branch of the kubementat automation repository
- In case you are changing configuration in platform_config you also need to  commit the changes to the master branch of the kubementat automation repository. Like this the pipeline runs can read the up-to-date configuration and use it accordingly
- In case you are adding or changing tasks or pipelines in the "tasks" or "pipelines" sub-directory you need to re-run the "setup_pipelines.sh" script for your environments and teams as described in the section "Automated Tekton pipeline and task setup". This ensures that the task and pipeline definitions are updated on the k8s cluster and are running the latest versions of task definitions and your pipelines.

## Adding new tekton triggers
We created a script for generating basic trigger configuration from templates.
You can use the script as follows:
```
cd automation
./generate_trigger_config_from_templates.sh
```

## Adding pipeline run configuration yml files easily
We created a script for generating pipeline-run yml files from existing pipelines for an environment and team. The script will create the according file in pipeline-runs/TEAM.

You can use the script as follows:
```
# General
./generate_pipeline_run.sh <ENVIRONMENT> <TEAM> <PIPELINE_YML_FILE>
# e.g.:
./generate_pipeline_run.sh dev dev2 ../pipelines/build-pipeline-ci-images.yml
```

# Tekton triggers

## Test tekton triggers
```
kubectl logs -n "tekton-pipelines" -l eventlistener=nginx-example-event-listener -f
```

# Uninstall instructions

## Uninstall tekton pipelines and tasks
```
cd automation

# General usage: ./uninstall_pipelines.sh <ENVIRONMENT> <TEAM>

# for uninstalling resources in environment=dev and team=dev1
./uninstall_pipelines.sh dev dev1

# for uninstalling resources in environment=dev and team=dev2
./uninstall_pipelines.sh dev dev2
```

## Uninstall Tekton
```
# uninstall tekton dashboard
kubectl delete -f https://storage.googleapis.com/tekton-releases/dashboard/latest/tekton-dashboard-release.yaml

# uninstall tekton crds
kubectl delete -f https://storage.googleapis.com/tekton-releases/pipeline/latest/release.yaml

# (optional) delete ingress
# kubectl delete ingress/tekton-dashboard-ingress -n tekton-pipelines
```

# Optional: manual installation instructions
## Manual Installation of tekton Custom Resources (Not recommended)
```
# install crds to k8s
kubectl apply -f https://storage.googleapis.com/tekton-releases/pipeline/latest/release.yaml

# monitor installation
kubectl get pods --namespace tekton-pipelines --watch

# install tekton dashboard
kubectl apply -f https://storage.googleapis.com/tekton-releases/dashboard/latest/tekton-dashboard-release.yaml

# monitor installation
kubectl get pods --namespace tekton-pipelines --watch
```

## (optional) expose tekton dashboard
```
# DASHBOARD_URL=dashboard.domain.tld

kubectl apply -n tekton-pipelines -f - <<EOF
apiVersion: extensions/v1beta1
kind: Ingress
metadata:
  name: tekton-dashboard-ingress
  namespace: tekton-pipelines
  annotations:
    ingress.kubernetes.io/rewrite-target: /
#    kubernetes.io/tls-acme: "true"
#    nginx.ingress.kubernetes.io/force-ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
spec:
  rules:
  - http:
      paths:
      - path: /tekton
        backend:
          serviceName: tekton-dashboard
          servicePort: 9097
#  - host: $DASHBOARD_URL
#    http:
#      paths:
#      - backend:
#          serviceName: tekton-dashboard
#          servicePort: 9097
  tls:
   - secretName: tekton-dashboard-cert
     hosts:
       - 62.116.156.12
EOF
```


# Examples for manual tekton usage

### apply hello world task definition
```
kubectl apply -f task_examples.yml

### list tasks
tkn tasks list

task_name="echo-hello-world-task"
# task_name="input-example-task"

tkn task describe $task_name

### run hello world task
kubectl apply -f hello_world_task_run.yml

### run input task example task
kubectl apply -f input_example_task_run.yml

### view details for task run
tkn taskrun list
tkn taskrun describe "${task_name}-run"
tkn taskrun logs "${task_name}-run"

### delete a task run
tkn taskrun delete "${task_name}-run"
```

## pipelines
```
tkn pipeline list

### set pipeline
kubectl apply -f hello_world_pipeline.yml
tkn pipeline describe hello-world-pipeline

### run pipeline
kubectl apply -f hello_world_pipeline_run.yml
tkn pipelinerun list
tkn pipelinerun logs hello-world-pipeline-run -f

tkn pipelinerun describe hello-world-pipeline-run

### delete pipeline run
tkn pipelinerun delete hello-world-pipeline-run
```

# Links
- tkn - Tekton CLI: https://github.com/tektoncd/cli
- Installation: https://github.com/tektoncd/pipeline/blob/master/docs/install.md
- Tutorial: https://github.com/tektoncd/pipeline/blob/master/docs/tutorial.md
- Tekton Examples: https://github.com/tektoncd/pipeline/tree/master/examples
- Tekton task catalog: https://github.com/tektoncd/catalog
- kaniko - build docker images in k8s - https://github.com/GoogleContainerTools/kaniko
- Tekton tutorial VMWare
  - https://tanzu.vmware.com/developer/guides/ci-cd/tekton-gs-p1/
  - https://tanzu.vmware.com/developer/guides/ci-cd/tekton-gs-p2/
- Tekton triggers tutorial: https://dlorenc.medium.com/tekton-triggers-3aba132c6344
- Tekton k8s deployment tutorial: https://github.com/IBM/deploy-app-using-tekton-on-kubernetes/tree/master/tekton-pipeline