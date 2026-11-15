# kmt command reference

Snapshot of `kmt --help` and every `kmt <command> --help`, regenerated from the live CLI.

Generated 2026-11-15 by `scripts/kmt_reference.py` from kubementat ba0d478. The CLI is the source of truth: `uv run kmt <command> --help` outranks this file.

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
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt component uninstall`

```
Usage: kmt component uninstall [OPTIONS] ENVIRONMENT COMPONENT

  Uninstall one helm component from the cluster: its release(s) and the claims
  they made. Absence is a normal answer, not a warning.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

## `kmt config`

```
Usage: kmt config [OPTIONS] COMMAND [ARGS]...

  Create and edit a kubementat configuration directory. init lays a tree down;
  add-environment and add-team add one layer to a tree that already exists and
  write no answers, because config set is the verb that answers one key and
  config unset is the verb that takes one away. What is not here is
  deliberate: no config unlock (kubementat never unlocks), no config secrets
  (config validate answers that), no config generate (kmt tekton generate-* is
  that verb).

Options:
  --help  Show this message and exit.

Commands:
  add-environment  Add one environment layer to a configuration directory...
  add-team         Add one team layer under one environment of a...
  init             Prepare a configuration directory, for one...
  set              Answer one key in one layer of a configuration...
  show             Effective configuration for one environment: every key...
  unset            Take one answer away from one layer of a configuration...
  validate         Read a configuration tree and say whether it is...
```

### `kmt config add-environment`

```
Usage: kmt config add-environment [OPTIONS] ENVIRONMENT

  Add one environment layer to a configuration directory that already exists:
  its two static files and its two tekton layer directories, and nothing else.
  It copies no templates and writes no answers, and it will not bootstrap a
  tree -- kubementat.yaml says which product the tree is for and only config
  init writes it, so run that first. The directory is named by --config-dir or
  by $KUBEMENTAT_PLATFORM_CONFIG_DIR.

Options:
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt config add-team`

```
Usage: kmt config add-team [OPTIONS] ENVIRONMENT TEAM

  Add one team layer under one environment of a configuration directory that
  already exists: two empty static files and the team's four layer
  directories, and nothing else. It needs kmt config add-environment first: a
  team is a layer under one environment, and this verb does not make one. The
  directory is named by --config-dir or by $KUBEMENTAT_PLATFORM_CONFIG_DIR.

Options:
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt config init`

```
Usage: kmt config init [OPTIONS]

  Prepare a configuration directory, for one environment: the manifest and the
  static layers, the deployer keypair, and git-crypt made live in that
  directory -- .gitattributes declared before the first encrypted file, the
  collaborator registered, the symmetric key exported at 0600, and one commit
  of kubementat's own. It writes answers and no product shape, and it is
  idempotent: a second run writes no byte, commits nothing, and says which
  step it skipped. A fresh tree addresses no cluster until someone names one.
  The directory it prepares is the one --config-dir names, or the one
  $KUBEMENTAT_PLATFORM_CONFIG_DIR names, and it refuses with neither.

Options:
  --environment TEXT         The environment whose layer is laid down.
                             Required; there is no environment that means all
                             of them.
  --team TEXT                Also create this team's two empty files. Needs
                             --environment: a team is a layer under one
                             environment.
  --set KEY=VALUE            Answer one key into the environment static file.
                             The value is JSON when it parses and a string
                             otherwise, so false is a boolean and example.test
                             is not.
  --encrypted-set KEY=VALUE  Answer one key into the environment encrypted
                             static file, with the same typing as --set. What
                             lands there is encrypted by the git-crypt filter
                             this verb declares before it writes it.
  --gpg-key FILE             Import this gpg secret key file as the deployer
                             key instead of generating one. Nothing is
                             generated and nothing is invented when it is
                             given.
  --gpg-email EMAIL          The identity to generate the deployer gpg keypair
                             under. Needed when the directory holds no gpg key
                             and --gpg-key was not given; without either there
                             is no identity to make one under, and this verb
                             refuses before it writes.
  --git-url URL              Name this remote in the closing instruction to
                             push what was just committed. It configures
                             nothing: no remote is added and nothing is
                             pushed.
  --no-commit                Stage everything and commit nothing. The tree is
                             otherwise fully prepared, and the first commit is
                             the operator's to write.
  --force                    Accept material kubementat did not write, and
                             touch none of it. It is not an overwrite: a file
                             that differs from what this verb would write is
                             refused either way. It does not lift the refusal
                             of a .gitattributes or .gitignore somebody else
                             configured -- that refusal is about a rule and
                             not about material, and force was never its
                             remedy.
  --config-dir PATH          The configuration directory this command reads or
                             writes. Layer above
                             $KUBEMENTAT_PLATFORM_CONFIG_DIR; with neither,
                             the run refuses, because a configuration root is
                             named and not located.
  --help                     Show this message and exit.
```

### `kmt config set`

```
Usage: kmt config set [OPTIONS] ENVIRONMENT [KEY=VALUE]

  Answer one key in one layer of a configuration directory that already
  exists: the key lands in that layer's file and every other key in it keeps
  its value and its place. It is the first read-modify-write in kubementat and
  it is not a way to make a layer: kmt config init / add-environment / add-
  team make those. The answer is either a value you supply or --generate KEY,
  and the refusal that comes with the second is what makes generation a
  deliberate act instead of a side effect of touching a directory. The
  directory is named by --config-dir or by $KUBEMENTAT_PLATFORM_CONFIG_DIR.

Options:
  --team TEXT        Put the answer on the team layer instead of the
                     environment layer. The team must already exist: kmt
                     config add-team makes it.
  --encrypted        Answer the encrypted file of the layer rather than the
                     plain one. What belongs there is what git-crypt protects:
                     private keys and credentials.
  --generate KEY     Invents a random value for KEY where nothing currently
                     answers it. It refuses when the key already answers, and
                     it does not care what kind of key KEY is.
  --from-file PATH   Answer every key of a JSON object, in the order the
                     document lists them. The file is how a value that must
                     not sit in a process table gets answered: only the path
                     travels, and the value is read where the operator put it.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt config show`

```
Usage: kmt config show [OPTIONS] ENVIRONMENT [TEAM]

  Effective configuration for one environment: every key it answers, each one
  tagged default / environment / team and named by the file that declared it.
  It reads and writes nothing, addresses no cluster, and lists the keys that
  answer as defaults, not only the ones a file happens to mention. The
  directory is named by --config-dir or by $KUBEMENTAT_PLATFORM_CONFIG_DIR.

Options:
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt config unset`

```
Usage: kmt config unset [OPTIONS] ENVIRONMENT KEY

  Take one answer away from one layer of a configuration directory that
  already exists: both files of that layer, because the pair is one answer and
  clearing only the plain file is a change the reader will not see. The layer
  above then answers, which is the whole contract of this verb. The directory
  is named by --config-dir or by $KUBEMENTAT_PLATFORM_CONFIG_DIR.

Options:
  --team TEXT        Clear the key from the team layer instead of the
                     environment layer.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt config validate`

```
Usage: kmt config validate [OPTIONS] ENVIRONMENT [TEAM]

  Read a configuration tree and say whether it is usable: one verdict, every
  fault named in the same run, warnings printed and not refused. It reads and
  writes nothing, addresses no cluster, and execs nothing — the collision half
  of the answer comes from the same registry the 1:1 check uses. The directory
  it reads is named by --config-dir or by $KUBEMENTAT_PLATFORM_CONFIG_DIR.

Options:
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

## `kmt environments`

```
Usage: kmt environments [OPTIONS] COMMAND [ARGS]...

  List the environments this configuration directory has, and the cluster each
  addresses.

Options:
  --help  Show this message and exit.

Commands:
  list  Print one row per environment: its address, the context that...
```

### `kmt environments list`

```
Usage: kmt environments list [OPTIONS]

  Print one row per environment: its address, the context that reaches it, the
  file that declared it, and what was checked against it. Reads the
  configuration only — no cluster is contacted, so it answers on a machine
  that has nothing reachable configured.

Options:
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

## `kmt helm`

```
Usage: kmt helm [OPTIONS] COMMAND [ARGS]...

  Apply and read the helm releases an environment runs, through helmfile.

Options:
  --help  Show this message and exit.

Commands:
  apply           Apply helmfile.yaml.gotmpl for the environment with...
  latest-version  Print the newest version of a helm chart that this...
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
  --context NAME                  Kubeconfig context to use instead of the one
                                  the environment's static.json declares
                                  (CLUSTER_KUBE_CONTEXT). Layer 1 of the
                                  resolution order: it beats the variable and
                                  the file.
  --kubeconfig PATH               Kubeconfig file to read the context from.
                                  With none of --kubeconfig, the
                                  CLUSTER_KUBECONFIG_PATH variable or the
                                  environment's own key, kubementat reads
                                  <environment>/kubeconfig.encrypted.yaml
                                  inside the configuration directory when that
                                  file exists -- it is the environment's own
                                  kubeconfig, encrypted by the git-crypt
                                  filter the tree already installs, and it
                                  answers ahead of $KUBECONFIG so a stray
                                  shell export cannot re-point a command that
                                  named an environment. Only when no such file
                                  is there does Kubernetes' own rule apply:
                                  $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH               The configuration directory this command
                                  reads or writes. Layer above
                                  $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                                  neither, the run refuses, because a
                                  configuration root is named and not located.
  --help                          Show this message and exit.
```

### `kmt helm latest-version`

```
Usage: kmt helm latest-version [OPTIONS] ENVIRONMENT CHART_PATH

  Print the newest version of a helm chart that this machine can see.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
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
  --context NAME                  Kubeconfig context to use instead of the one
                                  the environment's static.json declares
                                  (CLUSTER_KUBE_CONTEXT). Layer 1 of the
                                  resolution order: it beats the variable and
                                  the file.
  --kubeconfig PATH               Kubeconfig file to read the context from.
                                  With none of --kubeconfig, the
                                  CLUSTER_KUBECONFIG_PATH variable or the
                                  environment's own key, kubementat reads
                                  <environment>/kubeconfig.encrypted.yaml
                                  inside the configuration directory when that
                                  file exists -- it is the environment's own
                                  kubeconfig, encrypted by the git-crypt
                                  filter the tree already installs, and it
                                  answers ahead of $KUBECONFIG so a stray
                                  shell export cannot re-point a command that
                                  named an environment. Only when no such file
                                  is there does Kubernetes' own rule apply:
                                  $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH               The configuration directory this command
                                  reads or writes. Layer above
                                  $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                                  neither, the run refuses, because a
                                  configuration root is named and not located.
  --help                          Show this message and exit.
```

## `kmt kubernetes`

```
Usage: kmt kubernetes [OPTIONS] COMMAND [ARGS]...

  Read and change what runs in the cluster an environment names: its
  deployments, claims, images, service accounts and Secrets.

Options:
  --help  Show this message and exit.

Commands:
  analyze-deployment       Analyze a deployment: its pods, their status,...
  cluster-capacity         Cluster capacity: requested versus allocatable...
  create-ingress           Generate an Ingress manifest for a service,...
  create-secret-from-json  Create or replace a Secret whose data entries...
  deployment-logs          Follow the logs of a deployment's pods (Ctrl+C...
  deployment-ssh           Open a shell in one pod of a deployment.
  pod-images               List the pods in a namespace and every image...
  pvc-usage                Disk usage of every claim the kubelets report:...
  service-account-token    Print the token a service account...
```

### `kmt kubernetes analyze-deployment`

```
Usage: kmt kubernetes analyze-deployment [OPTIONS] ENVIRONMENT NAMESPACE
                                         DEPLOYMENT

  Analyze a deployment: its pods, their status, and their logs.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes cluster-capacity`

```
Usage: kmt kubernetes cluster-capacity [OPTIONS] ENVIRONMENT

  Cluster capacity: requested versus allocatable per node, plus usage when
  metrics-server is installed.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes create-ingress`

```
Usage: kmt kubernetes create-ingress [OPTIONS] ENVIRONMENT DOMAIN SERVICE_NAME
                                     SERVICE_PORT

  Generate an Ingress manifest for a service, and say which ingress class it
  routes through.

  The manifest goes to stdout so it can be piped or redirected, and any
  warning about the class goes to stderr so it cannot land in the YAML. The
  class is read from TRAEFIK_INGRESS_CLASS for the environment; when nothing
  answers, kubementat writes its own default and says so.

Options:
  -o, --output FILE  Write the manifest to this file instead of stdout.
                     Refuses if the file exists.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes create-secret-from-json`

```
Usage: kmt kubernetes create-secret-from-json [OPTIONS] ENVIRONMENT NAMESPACE
                                              SECRET_NAME JSON_FILE_PATH

  Create or replace a Secret whose data entries are the keys of a JSON file.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes deployment-logs`

```
Usage: kmt kubernetes deployment-logs [OPTIONS] ENVIRONMENT NAMESPACE
                                      DEPLOYMENT [CONTAINER]

  Follow the logs of a deployment's pods (Ctrl+C to stop).

Options:
  --tail INTEGER     Start this many lines from the end instead of from the
                     beginning.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes deployment-ssh`

```
Usage: kmt kubernetes deployment-ssh [OPTIONS] ENVIRONMENT NAMESPACE
                                     DEPLOYMENT [CONTAINER]

  Open a shell in one pod of a deployment. Ends when you exit it.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes pod-images`

```
Usage: kmt kubernetes pod-images [OPTIONS] ENVIRONMENT NAMESPACE

  List the pods in a namespace and every image they run.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes pvc-usage`

```
Usage: kmt kubernetes pvc-usage [OPTIONS] ENVIRONMENT

  Disk usage of every claim the kubelets report: one row per PVC, per node.

  Each node's own stats summary is read through the client, so no local proxy
  is started and no size is a guess. Sizes are bytes unless --human (-h) is
  given: that flag is the script's -h, which meant human-readable and not
  help. A node that answers nothing is named, not skipped in silence.

Options:
  -h, --human        Print the sizes as Ki/Mi/Gi instead of bytes.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt kubernetes service-account-token`

```
Usage: kmt kubernetes service-account-token [OPTIONS] ENVIRONMENT NAMESPACE
                                            SERVICE_ACCOUNT_NAME

  Print the token a service account authenticates with.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
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
  -y, --yes          Do not ask for confirmation. On a cluster that already
                     runs a Traefik kubementat did not install, it is also the
                     answer to the second-controller warning; without it and
                     without a terminal to ask in, nothing is applied.
  --update           Upgrade Traefik in place instead of leaving it alone,
                     applying the chart's CustomResourceDefinitions before the
                     release -- the half Helm will not do on its own. On a
                     foreign controller this installs kubementat's own release
                     next to it, which means a second controller, class and
                     status writer.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt routing status`

```
Usage: kmt routing status [OPTIONS] ENVIRONMENT

  Read the cluster and report which Traefik it is running, whose it is, and
  what kmt routing install would do with it. Read-only: nothing is installed,
  changed, or deleted, so there is no flag that skips a confirmation.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

## `kmt run-ci-container`

```
Usage: kmt run-ci-container [OPTIONS] ENVIRONMENT TEAM

  Runs a container with the automation image used by tekton in the given
  environment and team namespace

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

## `kmt setup-docker-registry-access`

```
Usage: kmt setup-docker-registry-access [OPTIONS] ENVIRONMENT TEAM

  Setup secrets for accessing docker registries

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

## `kmt setup-secrets`

```
Usage: kmt setup-secrets [OPTIONS] ENVIRONMENT TEAM

  Setup secrets for the given team in the k8s cluster. See the team layer of
  the configuration directory -> static.encrypted.json -> SSH_DEPLOY_KEYS

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

## `kmt tekton`

```
Usage: kmt tekton [OPTIONS] COMMAND [ARGS]...

  Run, list, set up and remove the tekton pipelines and tasks of an
  environment.

Options:
  --help  Show this message and exit.

Commands:
  cleanup-pipeline-runs     Cleanup resources (containers, tekton...
  generate-deploy-pipeline  Generate a deploy pipeline for an app from...
  generate-pipeline-run     Generate a pipeline run for a pipeline the...
  generate-triggers         Generate an app's webhook trigger manifests...
  list                      List tekton resources
  run-pipeline              Run a tekton pipeline.
  setup-cleanup-job         Setup the cronjob that prunes completed...
  setup-pipelines           Setup tekton-pipelines and tasks
  setup-triggers            Setup tekton webhook triggers
  uninstall-pipelines       Uninstall tekton-pipelines and tasks,...
```

### `kmt tekton cleanup-pipeline-runs`

```
Usage: kmt tekton cleanup-pipeline-runs [OPTIONS] ENVIRONMENT TEAM

  Cleanup resources (containers, tekton resources) of executed pipeline runs

Options:
  --filter TEXT      Optional Filter for cleaning up specific pipeline runs.
                     options are: succeeded (default), all
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt tekton generate-deploy-pipeline`

```
Usage: kmt tekton generate-deploy-pipeline [OPTIONS] ENVIRONMENT TEAM APP_NAME

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
  --context NAME              Kubeconfig context to use instead of the one the
                              environment's static.json declares
                              (CLUSTER_KUBE_CONTEXT). Layer 1 of the
                              resolution order: it beats the variable and the
                              file.
  --kubeconfig PATH           Kubeconfig file to read the context from. With
                              none of --kubeconfig, the
                              CLUSTER_KUBECONFIG_PATH variable or the
                              environment's own key, kubementat reads
                              <environment>/kubeconfig.encrypted.yaml inside
                              the configuration directory when that file
                              exists -- it is the environment's own
                              kubeconfig, encrypted by the git-crypt filter
                              the tree already installs, and it answers ahead
                              of $KUBECONFIG so a stray shell export cannot
                              re-point a command that named an environment.
                              Only when no such file is there does Kubernetes'
                              own rule apply: $KUBECONFIG, then
                              ~/.kube/config.
  --config-dir PATH           The configuration directory this command reads
                              or writes. Layer above
                              $KUBEMENTAT_PLATFORM_CONFIG_DIR; with neither,
                              the run refuses, because a configuration root is
                              named and not located.
  --help                      Show this message and exit.
```

### `kmt tekton generate-pipeline-run`

```
Usage: kmt tekton generate-pipeline-run [OPTIONS] ENVIRONMENT TEAM
                                        PIPELINE_FILE

  Generate a pipeline run for a pipeline the checkout has, into the team's own
  pipeline-run directory that `kmt tekton run-pipeline` then reads. Review it:
  the parameters that belong to the app rather than to the platform are
  written as TODO, because kubementat cannot know them.

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt tekton generate-triggers`

```
Usage: kmt tekton generate-triggers [OPTIONS] ENVIRONMENT TEAM APP_NAME

  Generate an app's webhook trigger manifests from the git host templates,
  into the team's trigger directory that `kmt tekton setup-triggers` then
  reads.

Options:
  --pipeline-name TEXT  The pipeline a webhook for this app may start, e.g.
                        build-nginx-example
  --trigger-type TEXT   Which git host's templates to generate from: github or
                        gitlab
  --context NAME        Kubeconfig context to use instead of the one the
                        environment's static.json declares
                        (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution
                        order: it beats the variable and the file.
  --kubeconfig PATH     Kubeconfig file to read the context from. With none of
                        --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                        the environment's own key, kubementat reads
                        <environment>/kubeconfig.encrypted.yaml inside the
                        configuration directory when that file exists -- it is
                        the environment's own kubeconfig, encrypted by the
                        git-crypt filter the tree already installs, and it
                        answers ahead of $KUBECONFIG so a stray shell export
                        cannot re-point a command that named an environment.
                        Only when no such file is there does Kubernetes' own
                        rule apply: $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH     The configuration directory this command reads or
                        writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                        with neither, the run refuses, because a configuration
                        root is named and not located.
  --help                Show this message and exit.
```

### `kmt tekton list`

```
Usage: kmt tekton list [OPTIONS] ENVIRONMENT TEAM

  List tekton resources

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt tekton run-pipeline`

```
Usage: kmt tekton run-pipeline [OPTIONS] ENVIRONMENT TEAM
                               [PIPELINE_RUN_IDENTIFIER]

  Run a tekton pipeline. PIPELINE_RUN_IDENTIFIER is either a PATH or an
  IDENTIFIER: an absolute path, or any argument that contains a path separator
  and exists there, is used as it was given; a bare name resolves under the
  team's tekton/pipeline-runs inside the configuration directory, and
  global/<name>.yml resolves under tekton_ci/pipeline-runs at the output root.
  Without PIPELINE_RUN_IDENTIFIER, the runs this team can start are listed and
  nothing is applied.

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
  --context NAME           Kubeconfig context to use instead of the one the
                           environment's static.json declares
                           (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution
                           order: it beats the variable and the file.
  --kubeconfig PATH        Kubeconfig file to read the context from. With none
                           of --kubeconfig, the CLUSTER_KUBECONFIG_PATH
                           variable or the environment's own key, kubementat
                           reads <environment>/kubeconfig.encrypted.yaml
                           inside the configuration directory when that file
                           exists -- it is the environment's own kubeconfig,
                           encrypted by the git-crypt filter the tree already
                           installs, and it answers ahead of $KUBECONFIG so a
                           stray shell export cannot re-point a command that
                           named an environment. Only when no such file is
                           there does Kubernetes' own rule apply: $KUBECONFIG,
                           then ~/.kube/config.
  --config-dir PATH        The configuration directory this command reads or
                           writes. Layer above
                           $KUBEMENTAT_PLATFORM_CONFIG_DIR; with neither, the
                           run refuses, because a configuration root is named
                           and not located.
  --help                   Show this message and exit.
```

### `kmt tekton setup-cleanup-job`

```
Usage: kmt tekton setup-cleanup-job [OPTIONS] ENVIRONMENT

  Setup the cronjob that prunes completed pipelineruns, in the namespace the
  environment config names for cluster cronjobs

Options:
  --team TEXT        Team whose `environment/team` layer of the configuration
                     directory this command uses. Also reads the TEAM
                     environment variable. kubementat does not name a team for
                     a command that did not name one.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt tekton setup-pipelines`

```
Usage: kmt tekton setup-pipelines [OPTIONS] ENVIRONMENT TEAM

  Setup tekton-pipelines and tasks

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt tekton setup-triggers`

```
Usage: kmt tekton setup-triggers [OPTIONS] ENVIRONMENT TEAM

  Setup tekton webhook triggers

Options:
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt tekton uninstall-pipelines`

```
Usage: kmt tekton uninstall-pipelines [OPTIONS] ENVIRONMENT TEAM

  Uninstall tekton-pipelines and tasks, including the teams pipeline namespace

Options:
  -y, --yes          Do not ask for user input. For scripting purposes.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
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
Usage: kmt tunnel grafana [OPTIONS] ENVIRONMENT

  open a network tunnel to the grafana UI

Options:
  --local-port TEXT
  --remote-port TEXT
  --host TEXT
  --context NAME      Kubeconfig context to use instead of the one the
                      environment's static.json declares
                      (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                      it beats the variable and the file.
  --kubeconfig PATH   Kubeconfig file to read the context from. With none of
                      --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                      the environment's own key, kubementat reads
                      <environment>/kubeconfig.encrypted.yaml inside the
                      configuration directory when that file exists -- it is
                      the environment's own kubeconfig, encrypted by the git-
                      crypt filter the tree already installs, and it answers
                      ahead of $KUBECONFIG so a stray shell export cannot re-
                      point a command that named an environment. Only when no
                      such file is there does Kubernetes' own rule apply:
                      $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH   The configuration directory this command reads or
                      writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                      with neither, the run refuses, because a configuration
                      root is named and not located.
  --help              Show this message and exit.
```

### `kmt tunnel openbao-ui`

```
Usage: kmt tunnel openbao-ui [OPTIONS] ENVIRONMENT

  open a network tunnel to the openbao UI

Options:
  --local-port TEXT
  --remote-port TEXT
  --host TEXT
  --context NAME      Kubeconfig context to use instead of the one the
                      environment's static.json declares
                      (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                      it beats the variable and the file.
  --kubeconfig PATH   Kubeconfig file to read the context from. With none of
                      --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                      the environment's own key, kubementat reads
                      <environment>/kubeconfig.encrypted.yaml inside the
                      configuration directory when that file exists -- it is
                      the environment's own kubeconfig, encrypted by the git-
                      crypt filter the tree already installs, and it answers
                      ahead of $KUBECONFIG so a stray shell export cannot re-
                      point a command that named an environment. Only when no
                      such file is there does Kubernetes' own rule apply:
                      $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH   The configuration directory this command reads or
                      writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                      with neither, the run refuses, because a configuration
                      root is named and not located.
  --help              Show this message and exit.
```

### `kmt tunnel pod`

```
Usage: kmt tunnel pod [OPTIONS] ENVIRONMENT NAMESPACE POD_NAME

  open a network tunnel to the specified pod

Options:
  --local-port TEXT   [required]
  --remote-port TEXT  [required]
  --host TEXT
  --context NAME      Kubeconfig context to use instead of the one the
                      environment's static.json declares
                      (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                      it beats the variable and the file.
  --kubeconfig PATH   Kubeconfig file to read the context from. With none of
                      --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                      the environment's own key, kubementat reads
                      <environment>/kubeconfig.encrypted.yaml inside the
                      configuration directory when that file exists -- it is
                      the environment's own kubeconfig, encrypted by the git-
                      crypt filter the tree already installs, and it answers
                      ahead of $KUBECONFIG so a stray shell export cannot re-
                      point a command that named an environment. Only when no
                      such file is there does Kubernetes' own rule apply:
                      $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH   The configuration directory this command reads or
                      writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                      with neither, the run refuses, because a configuration
                      root is named and not located.
  --help              Show this message and exit.
```

### `kmt tunnel tekton`

```
Usage: kmt tunnel tekton [OPTIONS] ENVIRONMENT

  open a network tunnel to the tekton UI

Options:
  --local-port TEXT
  --remote-port TEXT
  --host TEXT
  --context NAME      Kubeconfig context to use instead of the one the
                      environment's static.json declares
                      (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                      it beats the variable and the file.
  --kubeconfig PATH   Kubeconfig file to read the context from. With none of
                      --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                      the environment's own key, kubementat reads
                      <environment>/kubeconfig.encrypted.yaml inside the
                      configuration directory when that file exists -- it is
                      the environment's own kubeconfig, encrypted by the git-
                      crypt filter the tree already installs, and it answers
                      ahead of $KUBECONFIG so a stray shell export cannot re-
                      point a command that named an environment. Only when no
                      such file is there does Kubernetes' own rule apply:
                      $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH   The configuration directory this command reads or
                      writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                      with neither, the run refuses, because a configuration
                      root is named and not located.
  --help              Show this message and exit.
```

## `kmt uninstall`

```
Usage: kmt uninstall [OPTIONS] ENVIRONMENT

  Uninstall kubementat from a k8s cluster

Options:
  -y, --yes          Do not ask for user input. For scripting purposes.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
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
Usage: kmt users create-grafana-accounts [OPTIONS] ENVIRONMENT

  Create every grafana account the environment's static.encrypted.json
  declares, over HTTP, and report which of them this run made.

Options:
  --grafana-url URL  Address grafana answers on. Without it kubementat reads
                     the grafana service's ClusterIP through
                     GRAFANA_SERVICE_SELECTOR, and fails rather than guess
                     one: the scripts this replaces defaulted to
                     http://127.0.0.1:31827, which is a port-forward nobody
                     opens.
  -y, --yes          Do not ask for user input. For scripting purposes.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt users create-grafana-read-only-account`

```
Usage: kmt users create-grafana-read-only-account [OPTIONS] ENVIRONMENT EMAIL
                                                  [NAME]

  Create one grafana account, the way the read-only script did. Note that the
  script set no role, so this does not grant one either; see the plan.

Options:
  --grafana-url URL  Address grafana answers on. Without it kubementat reads
                     the grafana service's ClusterIP through
                     GRAFANA_SERVICE_SELECTOR, and fails rather than guess
                     one: the scripts this replaces defaulted to
                     http://127.0.0.1:31827, which is a port-forward nobody
                     opens.
  -y, --yes          Do not ask for user input. For scripting purposes.
  --context NAME     Kubeconfig context to use instead of the one the
                     environment's static.json declares
                     (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                     it beats the variable and the file.
  --kubeconfig PATH  Kubeconfig file to read the context from. With none of
                     --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or the
                     environment's own key, kubementat reads
                     <environment>/kubeconfig.encrypted.yaml inside the
                     configuration directory when that file exists -- it is
                     the environment's own kubeconfig, encrypted by the git-
                     crypt filter the tree already installs, and it answers
                     ahead of $KUBECONFIG so a stray shell export cannot re-
                     point a command that named an environment. Only when no
                     such file is there does Kubernetes' own rule apply:
                     $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH  The configuration directory this command reads or writes.
                     Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR; with
                     neither, the run refuses, because a configuration root is
                     named and not located.
  --help             Show this message and exit.
```

### `kmt users create-team-account`

```
Usage: kmt users create-team-account [OPTIONS] SERVICE_ACCOUNT_NAME

  Create a team account that can do anything in the team namespaces, and print
  the kubeconfig to paste into ~/.kube/config.

Options:
  --environment TEXT  Environment this command edits. Required, like every
                      command that reaches a cluster; also reads ENVIRONMENT.
  --team TEXT         Team whose `environment/team` layer of the configuration
                      directory this command uses. Also reads the TEAM
                      environment variable. kubementat does not name a team
                      for a command that did not name one.
  -y, --yes           Do not ask for user input. For scripting purposes.
  --context NAME      Kubeconfig context to use instead of the one the
                      environment's static.json declares
                      (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                      it beats the variable and the file.
  --kubeconfig PATH   Kubeconfig file to read the context from. With none of
                      --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                      the environment's own key, kubementat reads
                      <environment>/kubeconfig.encrypted.yaml inside the
                      configuration directory when that file exists -- it is
                      the environment's own kubeconfig, encrypted by the git-
                      crypt filter the tree already installs, and it answers
                      ahead of $KUBECONFIG so a stray shell export cannot re-
                      point a command that named an environment. Only when no
                      such file is there does Kubernetes' own rule apply:
                      $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH   The configuration directory this command reads or
                      writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                      with neither, the run refuses, because a configuration
                      root is named and not located.
  --help              Show this message and exit.
```

### `kmt users create-team-account-read-only`

```
Usage: kmt users create-team-account-read-only [OPTIONS] SERVICE_ACCOUNT_NAME

  Create a team account that can read everything and change nothing.

Options:
  --environment TEXT  Environment this command edits. Required, like every
                      command that reaches a cluster; also reads ENVIRONMENT.
  --team TEXT         Team whose `environment/team` layer of the configuration
                      directory this command uses. Also reads the TEAM
                      environment variable. kubementat does not name a team
                      for a command that did not name one.
  -y, --yes           Do not ask for user input. For scripting purposes.
  --context NAME      Kubeconfig context to use instead of the one the
                      environment's static.json declares
                      (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                      it beats the variable and the file.
  --kubeconfig PATH   Kubeconfig file to read the context from. With none of
                      --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                      the environment's own key, kubementat reads
                      <environment>/kubeconfig.encrypted.yaml inside the
                      configuration directory when that file exists -- it is
                      the environment's own kubeconfig, encrypted by the git-
                      crypt filter the tree already installs, and it answers
                      ahead of $KUBECONFIG so a stray shell export cannot re-
                      point a command that named an environment. Only when no
                      such file is there does Kubernetes' own rule apply:
                      $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH   The configuration directory this command reads or
                      writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                      with neither, the run refuses, because a configuration
                      root is named and not located.
  --help              Show this message and exit.
```

### `kmt users delete-team-account`

```
Usage: kmt users delete-team-account [OPTIONS] SERVICE_ACCOUNT_NAME

  Delete a team account, its token, and every binding that let it act.

Options:
  --environment TEXT  Environment this command edits. Required, like every
                      command that reaches a cluster; also reads ENVIRONMENT.
  --team TEXT         Team whose `environment/team` layer of the configuration
                      directory this command uses. Also reads the TEAM
                      environment variable. kubementat does not name a team
                      for a command that did not name one.
  -y, --yes           Do not ask for user input. For scripting purposes.
  --context NAME      Kubeconfig context to use instead of the one the
                      environment's static.json declares
                      (CLUSTER_KUBE_CONTEXT). Layer 1 of the resolution order:
                      it beats the variable and the file.
  --kubeconfig PATH   Kubeconfig file to read the context from. With none of
                      --kubeconfig, the CLUSTER_KUBECONFIG_PATH variable or
                      the environment's own key, kubementat reads
                      <environment>/kubeconfig.encrypted.yaml inside the
                      configuration directory when that file exists -- it is
                      the environment's own kubeconfig, encrypted by the git-
                      crypt filter the tree already installs, and it answers
                      ahead of $KUBECONFIG so a stray shell export cannot re-
                      point a command that named an environment. Only when no
                      such file is there does Kubernetes' own rule apply:
                      $KUBECONFIG, then ~/.kube/config.
  --config-dir PATH   The configuration directory this command reads or
                      writes. Layer above $KUBEMENTAT_PLATFORM_CONFIG_DIR;
                      with neither, the run refuses, because a configuration
                      root is named and not located.
  --help              Show this message and exit.
```

