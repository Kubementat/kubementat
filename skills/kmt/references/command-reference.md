# kmt command reference

Snapshot of `kmt --help` and every `kmt <command> --help`, regenerated from the live CLI.

Generated 2026-11-15 by `scripts/kmt_reference.py`. The CLI is the source of truth: `uv run kmt <command> --help` outranks this file.

## `kmt analyze-deployment`

```
Usage: kmt analyze-deployment [OPTIONS] NAMESPACE DEPLOYMENT

  Analyze a deployment: its pods, their status, and their logs.

Options:
  --help  Show this message and exit.
```

## `kmt cluster-capacity`

```
Usage: kmt cluster-capacity [OPTIONS]

  Cluster capacity: requested versus allocatable per node, plus usage when
  metrics-server is installed.

Options:
  --help  Show this message and exit.
```

## `kmt component`

```
Usage: kmt component [OPTIONS] COMMAND [ARGS]...

  Install or uninstall one component kubementat owns. install applies the
  release the environment names after <component> through helmfile, by its
  component_name label; uninstall takes that release and its claims off the
  cluster. Components that are only a namespace (tekton, goldilocks, vpa,
  cluster-cronjobs, goldilocks and friends) cannot be installed by helm and
  are refused, and traefik is refused beside a controller kubementat did not
  install.

Options:
  --help  Show this message and exit.

Commands:
  install    Apply one component's helm release: `helmfile apply -l...
  uninstall  Uninstall one helm component from the cluster: its...
```

### `kmt component install`

```
Usage: kmt component install [OPTIONS] ENVIRONMENT COMPONENT

  Apply one component's helm release: `helmfile apply -l
  component_name=<component>`. It applies without asking, the same way `kmt
  install` applies its group, so there is no --yes here: there is no
  confirmation to skip. The exception is traefik, where the cluster is read
  first: a Traefik this command did not put there is named and left alone, and
  the command that can be asked to change it is `kmt routing install
  --update`.

Options:
  --team TEXT  Team whose platform_config/{environment}/<team> config this
               command uses. Also reads the TEAM environment variable.
               [default: dev1]
  --help       Show this message and exit.
```

### `kmt component uninstall`

```
Usage: kmt component uninstall [OPTIONS] ENVIRONMENT COMPONENT

  Uninstall one helm component from the cluster: its release(s) and the claims
  they made. Absence is a normal answer, not a warning.

Options:
  --team TEXT  Team whose platform_config/{environment}/<team> config this
               command uses. Also reads the TEAM environment variable.
               [default: dev1]
  --help       Show this message and exit.
```

## `kmt create-ingress`

```
Usage: kmt create-ingress [OPTIONS] DOMAIN SERVICE_NAME SERVICE_PORT
                          [ENVIRONMENT]

  Generate an Ingress manifest for a service, and say which ingress class it
  routes through.

  The manifest goes to stdout so it can be piped or redirected, and any
  warning about the class goes to stderr so it cannot land in the YAML. The
  class is read from TRAEFIK_INGRESS_CLASS for the environment; when nothing
  answers, kubementat writes its own default and says so.

Options:
  --team TEXT        Team whose platform_config/{environment}/<team> config
                     this command uses. Also reads the TEAM environment
                     variable.  [default: dev1]
  -o, --output FILE  Write the manifest to this file instead of stdout.
                     Refuses if the file exists.
  --help             Show this message and exit.
```

## `kmt create-secret-from-json`

```
Usage: kmt create-secret-from-json [OPTIONS] NAMESPACE SECRET_NAME
                                   JSON_FILE_PATH

  Create or replace a Secret whose data entries are the keys of a JSON file.

Options:
  --help  Show this message and exit.
```

## `kmt deploy-logs`

```
Usage: kmt deploy-logs [OPTIONS] NAMESPACE DEPLOYMENT [CONTAINER]

  Follow the logs of a helm deployment's pods (Ctrl+C to stop).

Options:
  --tail INTEGER  Start this many lines from the end instead of from the
                  beginning.
  --help          Show this message and exit.
```

## `kmt deploy-ssh`

```
Usage: kmt deploy-ssh [OPTIONS] NAMESPACE DEPLOYMENT [CONTAINER]

  Open a shell in one pod of a helm deployment. Ends when you exit it.

Options:
  --help  Show this message and exit.
```

## `kmt helm`

```
Usage: kmt helm [OPTIONS] COMMAND [ARGS]...

  Apply and read the helm releases an environment runs, through helmfile.

Options:
  --help  Show this message and exit.

Commands:
  apply  Apply helmfile.yaml.gotmpl for the environment with helmfile.
```

### `kmt helm apply`

```
Usage: kmt helm apply [OPTIONS] ENVIRONMENT

  Apply helmfile.yaml.gotmpl for the environment with helmfile.

Options:
  --helmfile-label-filter TEXT    e.g. group=standard or
                                  component_name=openbao
  -y, --yes                       Apply without asking. Defaults to asking
                                  when stdin is a terminal, and to not asking
                                  when it is not.
  --helmfile-working-directory TEXT
                                  The directory where helmfile cli should be
                                  executed
  --helmfile-path TEXT            The path to the helmfile.yaml.gotmpl file to
                                  be applied
  --team TEXT                     Team whose
                                  platform_config/{environment}/<team> config
                                  this command uses. Also reads the TEAM
                                  environment variable.  [default: dev1]
  --help                          Show this message and exit.
```

## `kmt helm-version`

```
Usage: kmt helm-version [OPTIONS] CHART_PATH ENVIRONMENT

  Print the newest version of a helm chart that this machine can see.

Options:
  --team TEXT  Team whose platform_config/{environment}/<team> config this
               command uses. Also reads the TEAM environment variable.
               [default: dev1]
  --help       Show this message and exit.
```

## `kmt initialize`

```
Usage: kmt initialize [OPTIONS] ENVIRONMENT TEAM

  Initialize kubementat configuration as initial install preparation

Options:
  --help  Show this message and exit.
```

## `kmt install`

```
Usage: kmt install [OPTIONS] ENVIRONMENT TEAM

  Install kubementat on a k8s cluster

Options:
  --configure-tekton-pipelines / --no-configure-tekton-pipelines
  --helmfile-installation-group TEXT
  -y, --yes                       Do not ask for user input. For scripting
                                  purposes.
  --help                          Show this message and exit.
```

## `kmt pod-images`

```
Usage: kmt pod-images [OPTIONS] NAMESPACE

  List the pods in a namespace and every image they run.

Options:
  --help  Show this message and exit.
```

## `kmt pvc-usage`

```
Usage: kmt pvc-usage [OPTIONS] [ENVIRONMENT]

  Disk usage of every claim the kubelets report: one row per PVC, per node.

  Each node's own stats summary is read through the client, so no local proxy
  is started and no size is a guess. Sizes are bytes unless --human (-h) is
  given: that flag is the script's -h, which meant human-readable and not
  help. A node that answers nothing is named, not skipped in silence.

Options:
  -h, --human  Print the sizes as Ki/Mi/Gi instead of bytes.
  --help       Show this message and exit.
```

## `kmt routing`

```
Usage: kmt routing [OPTIONS] COMMAND [ARGS]...

  Read and install the routing kubementat installs: the traefik controller and
  the cert-manager that fronts it. Not part of kmt install -- routing is
  applied here, or not at all.

Options:
  --help  Show this message and exit.

Commands:
  install  Apply the routing group -- the traefik controller, the...
  status   Read the cluster and report which Traefik it is running, whose...
```

### `kmt routing install`

```
Usage: kmt routing install [OPTIONS] ENVIRONMENT

  Apply the routing group -- the traefik controller, the cert-manager that
  fronts it, and the two ClusterIssuers -- installing Traefik only when the
  cluster has none. A controller that is already there is left alone and
  named, and the rest of the group is applied either way, because a skip that
  suppressed the group would take the platform issuer down with it. This is
  not part of kmt install, and kmt install takes neither routing nor --update.

Options:
  --team TEXT  Team whose platform_config/{environment}/<team> config this
               command uses. Also reads the TEAM environment variable.
               [default: dev1]
  -y, --yes    Do not ask for confirmation. On a cluster that already runs a
               Traefik kubementat did not install, it is also the answer to
               the second-controller warning; without it and without a
               terminal to ask in, nothing is applied.
  --update     Upgrade Traefik in place instead of leaving it alone, applying
               the chart's CustomResourceDefinitions before the release -- the
               half Helm will not do on its own. On a foreign controller this
               installs kubementat's own release next to it, which means a
               second controller, class and status writer.
  --help       Show this message and exit.
```

### `kmt routing status`

```
Usage: kmt routing status [OPTIONS] ENVIRONMENT

  Read the cluster and report which Traefik it is running, whose it is, and
  what kmt routing install would do with it. Read-only: nothing is installed,
  changed, or deleted, so there is no flag that skips a confirmation.

Options:
  --team TEXT  Team whose platform_config/{environment}/<team> config this
               command uses. Also reads the TEAM environment variable.
               [default: dev1]
  --help       Show this message and exit.
```

## `kmt run-ci-container`

```
Usage: kmt run-ci-container [OPTIONS] ENVIRONMENT TEAM

  Runs a container with the automation image used by tekton in the given
  environment and team namespace

Options:
  --help  Show this message and exit.
```

## `kmt service-account-token`

```
Usage: kmt service-account-token [OPTIONS] NAMESPACE SERVICE_ACCOUNT_NAME

  Print the token a service account authenticates with.

Options:
  --help  Show this message and exit.
```

## `kmt setup-docker-registry-access`

```
Usage: kmt setup-docker-registry-access [OPTIONS] ENVIRONMENT TEAM

  Setup secrets for accessing docker registries

Options:
  --help  Show this message and exit.
```

## `kmt setup-secrets`

```
Usage: kmt setup-secrets [OPTIONS] ENVIRONMENT TEAM

  Setup secrets for the given team in the k8s cluster. See
  platform_config/ENV/TEAM/static.encrypted.json -> SSH_DEPLOY_KEYS

Options:
  --help  Show this message and exit.
```

## `kmt tekton`

```
Usage: kmt tekton [OPTIONS] COMMAND [ARGS]...

  Run, list, set up and remove the tekton pipelines and tasks of an
  environment.

Options:
  --help  Show this message and exit.

Commands:
  cleanup-pipeline-runs  Cleanup resources (containers, tekton resources)...
  list                   List tekton resources
  run-pipeline           Run a tekton pipeline.
  setup-pipelines        Setup tekton-pipelines and tasks
  setup-triggers         Setup tekton webhook triggers
  uninstall-pipelines    Uninstall tekton-pipelines and tasks, including...
```

### `kmt tekton cleanup-pipeline-runs`

```
Usage: kmt tekton cleanup-pipeline-runs [OPTIONS] ENVIRONMENT TEAM

  Cleanup resources (containers, tekton resources) of executed pipeline runs

Options:
  --filter TEXT  Optional Filter for cleaning up specific pipeline runs.
                 options are: succeeded (default), all
  --help         Show this message and exit.
```

### `kmt tekton list`

```
Usage: kmt tekton list [OPTIONS] ENVIRONMENT TEAM

  List tekton resources

Options:
  --help  Show this message and exit.
```

### `kmt tekton run-pipeline`

```
Usage: kmt tekton run-pipeline [OPTIONS] ENVIRONMENT TEAM
                               [PIPELINE_RUN_IDENTIFIER]

  Run a tekton pipeline. PIPELINE_RUN_IDENTIFIER is either a PATH or an
  IDENTIFIER: an absolute path, or any argument that contains a path separator
  and exists there, is used as it was given; a bare name resolves under the
  team's platform_config/<environment>/<team>/tekton/pipeline-runs, and
  global/<name>.yml resolves under tekton_ci/pipeline-runs. Without
  PIPELINE_RUN_IDENTIFIER, the runs this team can start are listed and nothing
  is applied.

Options:
  --parallel               Allow parallel running of runs for the same
                           pipeline?
  --wait                   Block until the started run finishes, instead of
                           returning straight after it is applied. Exits non-
                           zero when the run failed or was never started. Off
                           by default, so a terminal behaves as it always did;
                           this is what tekton_ci/tasks/run-tekton-
                           pipeline.yml runs.
  --poll-interval INTEGER  Seconds between reads of the run, with --wait.
                           [default: 3]
  --timeout INTEGER        Seconds to wait for the run before giving up on it,
                           with --wait.  [default: 1800]
  --help                   Show this message and exit.
```

### `kmt tekton setup-pipelines`

```
Usage: kmt tekton setup-pipelines [OPTIONS] ENVIRONMENT TEAM

  Setup tekton-pipelines and tasks

Options:
  --help  Show this message and exit.
```

### `kmt tekton setup-triggers`

```
Usage: kmt tekton setup-triggers [OPTIONS] ENVIRONMENT TEAM

  Setup tekton webhook triggers

Options:
  --help  Show this message and exit.
```

### `kmt tekton uninstall-pipelines`

```
Usage: kmt tekton uninstall-pipelines [OPTIONS] ENVIRONMENT TEAM

  Uninstall tekton-pipelines and tasks, including the teams pipeline namespace

Options:
  -y, --yes  Do not ask for user input. For scripting purposes.
  --help     Show this message and exit.
```

## `kmt tekton-generate-deploy-pipeline`

```
Usage: kmt tekton-generate-deploy-pipeline [OPTIONS] ENVIRONMENT TEAM APP_NAME

  Generate a deploy pipeline for an app from the template, into the team's own
  pipeline directory. Review it: the generator cannot know what the app's
  database setup and seeding need.

Options:
  --git-url TEXT              The git url of the app repository, e.g.
                              ssh://github.com/julweber/nginx-example.git
  --git-project-name TEXT     The local project directory name of the app
                              repository, e.g. nginx-example
  --git-server-host TEXT      The app repository's git server host, e.g.
                              github.com
  --git-server-port TEXT      The app repository's git server ssh port, e.g.
                              22
  --git-server-ssh-user TEXT  The app repository's git ssh user, e.g. git
  --help                      Show this message and exit.
```

## `kmt tekton-generate-pipeline-run`

```
Usage: kmt tekton-generate-pipeline-run [OPTIONS] ENVIRONMENT TEAM
                                        PIPELINE_FILE

  Generate a pipeline run for a pipeline the checkout has, into the team's own
  pipeline-run directory that tekton-run-pipeline then reads. Review it: the
  parameters that belong to the app rather than to the platform are written as
  TODO, because kubementat cannot know them.

Options:
  --help  Show this message and exit.
```

## `kmt tekton-generate-triggers`

```
Usage: kmt tekton-generate-triggers [OPTIONS] ENVIRONMENT TEAM APP_NAME

  Generate an app's webhook trigger manifests from the git host templates,
  into the team's trigger directory that tekton-setup-triggers then reads.

Options:
  --pipeline-name TEXT  The pipeline a webhook for this app may start, e.g.
                        build-nginx-example
  --trigger-type TEXT   Which git host's templates to generate from: github or
                        gitlab
  --help                Show this message and exit.
```

## `kmt tekton-setup-cleanup-job`

```
Usage: kmt tekton-setup-cleanup-job [OPTIONS] ENVIRONMENT

  Setup the cronjob that prunes completed pipelineruns, in the namespace the
  environment config names for cluster cronjobs

Options:
  --team TEXT  Team whose platform_config/{environment}/<team> config this
               command uses. Also reads the TEAM environment variable.
               [default: dev1]
  --help       Show this message and exit.
```

## `kmt tunnel`

```
Usage: kmt tunnel [OPTIONS] COMMAND [ARGS]...

  Open a network tunnel to a component kubementat runs, or to any pod you
  name.

Options:
  --help  Show this message and exit.

Commands:
  grafana     open a network tunnel to the grafana UI
  openbao-ui  open a network tunnel to the openbao UI
  pod         open a network tunnel to the specified pod
  tekton      open a network tunnel to the tekton UI
```

### `kmt tunnel grafana`

```
Usage: kmt tunnel grafana [OPTIONS] [ENVIRONMENT]

  open a network tunnel to the grafana UI

Options:
  --local-port TEXT
  --remote-port TEXT
  --host TEXT
  --team TEXT         Team whose platform_config/{environment}/<team> config
                      this command uses. Also reads the TEAM environment
                      variable.  [default: dev1]
  --help              Show this message and exit.
```

### `kmt tunnel openbao-ui`

```
Usage: kmt tunnel openbao-ui [OPTIONS] [ENVIRONMENT]

  open a network tunnel to the openbao UI

Options:
  --local-port TEXT
  --remote-port TEXT
  --host TEXT
  --team TEXT         Team whose platform_config/{environment}/<team> config
                      this command uses. Also reads the TEAM environment
                      variable.  [default: dev1]
  --help              Show this message and exit.
```

### `kmt tunnel pod`

```
Usage: kmt tunnel pod [OPTIONS] NAMESPACE POD_NAME

  open a network tunnel to the specified pod

Options:
  --local-port TEXT   [required]
  --remote-port TEXT  [required]
  --host TEXT
  --help              Show this message and exit.
```

### `kmt tunnel tekton`

```
Usage: kmt tunnel tekton [OPTIONS] [ENVIRONMENT]

  open a network tunnel to the tekton UI

Options:
  --local-port TEXT
  --remote-port TEXT
  --host TEXT
  --team TEXT         Team whose platform_config/{environment}/<team> config
                      this command uses. Also reads the TEAM environment
                      variable.  [default: dev1]
  --help              Show this message and exit.
```

## `kmt uninstall`

```
Usage: kmt uninstall [OPTIONS] ENVIRONMENT

  Uninstall kubementat from a k8s cluster

Options:
  -y, --yes  Do not ask for user input. For scripting purposes.
  --help     Show this message and exit.
```

## `kmt users`

```
Usage: kmt users [OPTIONS] COMMAND [ARGS]...

  Manage the team accounts kubementat gives its teams.

Options:
  --help  Show this message and exit.

Commands:
  create-grafana-accounts         Create every grafana account the...
  create-grafana-read-only-account
                                  Create one grafana account, the way...
  create-team-account             Create a team account that can do...
  create-team-account-read-only   Create a team account that can read...
  delete-team-account             Delete a team account, its token, and...
```

### `kmt users create-grafana-accounts`

```
Usage: kmt users create-grafana-accounts [OPTIONS] [ENVIRONMENT]

  Create every grafana account the environment's static.encrypted.json
  declares, over HTTP, and report which of them this run made.

Options:
  --team TEXT        Team whose platform_config/{environment}/<team> config
                     this command uses. Also reads the TEAM environment
                     variable.  [default: dev1]
  --grafana-url URL  Address grafana answers on. Without it kubementat reads
                     the grafana service's ClusterIP through
                     GRAFANA_SERVICE_SELECTOR, and fails rather than guess
                     one: the scripts this replaces defaulted to
                     http://127.0.0.1:31827, which is a port-forward nobody
                     opens.
  -y, --yes          Do not ask for user input. For scripting purposes.
  --help             Show this message and exit.
```

### `kmt users create-grafana-read-only-account`

```
Usage: kmt users create-grafana-read-only-account [OPTIONS] [ENVIRONMENT]
                                                  EMAIL [NAME]

  Create one grafana account, the way the read-only script did. Note that the
  script set no role, so this does not grant one either; see the plan.

Options:
  --team TEXT        Team whose platform_config/{environment}/<team> config
                     this command uses. Also reads the TEAM environment
                     variable.  [default: dev1]
  --grafana-url URL  Address grafana answers on. Without it kubementat reads
                     the grafana service's ClusterIP through
                     GRAFANA_SERVICE_SELECTOR, and fails rather than guess
                     one: the scripts this replaces defaulted to
                     http://127.0.0.1:31827, which is a port-forward nobody
                     opens.
  -y, --yes          Do not ask for user input. For scripting purposes.
  --help             Show this message and exit.
```

### `kmt users create-team-account`

```
Usage: kmt users create-team-account [OPTIONS] SERVICE_ACCOUNT_NAME

  Create a team account that can do anything in the team namespaces, and print
  the kubeconfig to paste into ~/.kube/config.

Options:
  --environment TEXT  Environment whose platform_config this command edits.
                      [default: dev]
  --team TEXT         Team whose platform_config/{environment}/<team> config
                      this command uses. Also reads the TEAM environment
                      variable.  [default: dev1]
  -y, --yes           Do not ask for user input. For scripting purposes.
  --help              Show this message and exit.
```

### `kmt users create-team-account-read-only`

```
Usage: kmt users create-team-account-read-only [OPTIONS] SERVICE_ACCOUNT_NAME

  Create a team account that can read everything and change nothing.

Options:
  --environment TEXT  Environment whose platform_config this command edits.
                      [default: dev]
  --team TEXT         Team whose platform_config/{environment}/<team> config
                      this command uses. Also reads the TEAM environment
                      variable.  [default: dev1]
  -y, --yes           Do not ask for user input. For scripting purposes.
  --help              Show this message and exit.
```

### `kmt users delete-team-account`

```
Usage: kmt users delete-team-account [OPTIONS] SERVICE_ACCOUNT_NAME

  Delete a team account, its token, and every binding that let it act.

Options:
  --environment TEXT  Environment whose platform_config this command edits.
                      [default: dev]
  --team TEXT         Team whose platform_config/{environment}/<team> config
                      this command uses. Also reads the TEAM environment
                      variable.  [default: dev1]
  -y, --yes           Do not ask for user input. For scripting purposes.
  --help              Show this message and exit.
```

