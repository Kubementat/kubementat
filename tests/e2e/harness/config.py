"""Static configuration for the kubementat E2E suite.

Everything a reviewer would otherwise have to fish out of stage code lives here:
lane shapes, stage budgets, the capability table, the dummy credential set, the
tunnel signature table, the resource kinds used for the idempotency hash, and the
known product blockers the suite is expected to trip over.

The suite speaks to exactly two things: the `virt-runner` CLI on the host, and one
hardened ssh command line inside the guest. Nothing else.

Requirements this file implements: the capability table, the image pin, the helmfile
installation groups, the dummy credential set, the tunnel signature table, the resource
kinds the idempotency hash uses, the stage budgets and the VM shapes.
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------- host / guest

DISTRO = "ubuntu"
RELEASE = "resolute"  # Ubuntu 26.04, pinned explicitly
GUEST_USER = "ubuntu"
GUEST_TREE = "/var/tmp/kmt-e2e"  # the guest copy of the checkout
GUEST_MOUNT = "/mnt/kubementat"  # read-write virtiofs share of the host tree
PROVISION_STATE_DIR = "/var/tmp/kmt-e2e-provision"
# The one configuration answer the lane cannot spell as `KEY=VALUE`: `GRAFANA_USERS` is a list of rows
# and one of its fields is a password generated for this run. The verb's `--from-file` is exactly
# that case, so the document is staged here and only the path crosses the wire -- which keeps the
# credential out of a command line, out of `ps`, and out of every artifact, and keeps the write on the
# verb's door
# instead of on a `jq` the lane would have to write itself. `/var/tmp` and not `/tmp` for the reason
# `stages.py` gives for its own staging: `/tmp` can be swept mid-run.
GRAFANA_ANSWER_DOCUMENT = "/var/tmp/kmt-e2e-encrypted-answers.json"

# ------------------------------------------------------------------ lane shapes


@dataclass(frozen=True)
class Lane:
    name: str
    vcpu: int
    ram_gib: int
    disk_gib: int
    install_groups: tuple  # ordered --helmfile-installation-group values
    features: tuple  # feature ids run by stage 05-features
    # stage name -> hard timeout in seconds; NFR 6.1
    timeouts: dict = field(default_factory=dict)
    # The configuration root every command this lane runs reads its answers from, named once here.
    #
    # It is a field and not a constant because a lane is allowed to want a tree of its own: preparing a
    # directory with `config init` and then installing against that directory is a claim about the whole
    # bootstrap, and it cannot be made against a tree three other lanes are also installing into. The
    # lanes that do not want one keep this value, so the flag the flag replaces is unchanged.
    #
    # The reason it is a flag and not the environment variable: every command that reads a root takes
    # `--config-dir`, so a root that is not stated is a root the harness found rather than chose.
    config_dir: str = f"{GUEST_TREE}/platform_config"



# Budgets. Measured ones are marked in docs/README_E2E_TESTS.md; a stage that
# exceeds 1.5x its target is recorded as `slow` and still passes (NFR 6.6).
SMOKE = Lane(
    name="smoke",
    vcpu=4,
    ram_gib=6,
    disk_gib=25,
    install_groups=("standard",),
    features=("tunnel_openbao_negative", "tekton_list"),
    timeouts={
        # 01-provision includes the VM create. Warm cache measures 16-30 s; the
        # budget is wide once because a cold image cache (~860 MB download) is the
        # only thing that can dominate it, and it is unmeasured (research F15).
        "01-provision": 600,
        "02-install": 600,
        "03-run": 240,
        "04-rerun": 600,
        "05-features": 420,
        # Two uninstalls with a reinstall between them: the reinstall is what proves the first
        # uninstall left nothing that blocks the next install.
        "99-teardown": 1200,
    },
)

FULL = Lane(
    name="full",
    vcpu=8,
    ram_gib=16,
    disk_gib=50,
    install_groups=("standard", "routing"),  # ", "extra" is disabled: see the note below
    # `extra` is commented out of this lane's groups rather than deleted from the harness, and that
    # distinction is the whole point. Adding it found a product bug older than this branch:
    #
    #   `kmt install --helmfile-installation-group extra` installs OpenBao, the release reports
    #   `deployed`, the pod is Running, and it never becomes ready. Standalone OpenBao comes up
    #   UNINITIALIZED -- the container log repeats `core: security barrier not initialized` /
    #   `seal configuration missing, not initialized` every five seconds -- the readiness probe is
    #   `bao status`, and nothing in kubementat runs `operator init` or unseals. The component
    #   therefore installs and permanently cannot serve.
    #
    # Left installed, the lane would be red forever on a fix nobody has scoped, so the group is out of
    # the shape for now. Everything that found the bug stays: `WORKLOAD_ASSERTED_GROUPS`, the assert
    # `_expect_group_workloads` runs over it, and its unit tests. An unasserted group is a group nobody
    # will re-enable, so the config entry outlives the lane entry on purpose. The condition for putting
    # `extra` back is written in TODOs.md: init/unseal decided and implemented, or `extra` declared
    # unsupported in the documentation that an operator reads before installing it.
    features=(
        # First, not last. This feature is the only place the lane asserts what the cluster key does,
        # and it installs a second environment against the same address for the length of one command.
        # Any fixture that outlives it would make every later command on `dev` refuse for a collision
        # the lane invented, so it belongs where nothing else has to be re-run around it.
        "cluster_addressing",
        "tunnels",
        "namespace_rename",
        "tunnel_pod",
        "tekton_list",
        # What a command scoped to one team writes, and which directory it writes it in. The
        # counterpart claim -- that a team layer which cannot be read names its own path -- is asserted
        # by the unit suite, not here, because the only verb that could reach that state clears a pair of
        # files and so never makes one missing. See feature_config_scope.
        "config_scope",
        "user_accounts",
        "grafana_accounts",
        "cleanup_job",
    ),
    timeouts={
        "01-provision": 630,
        "02-install": 1200,
        "03-run": 240,
        "04-rerun": 1020,
        "05-features": 1680,
        "99-teardown": 1500,
    },
)

# The separately startable case: one lane whose whole subject is the ingress controller kubementat
# installs. It is shaped like `full` and run with `--lane routing --scope full`.
#
# What is registered here is the *shape*, and the steps behind it now exist. Stage 02 turns k3s's
# bundled Traefik off as a stage step (a `disable:` entry in `/etc/rancher/k3s/config.yaml`, the
# mechanism k3s documents for a component that is already installed), waits for it to stop, installs
# the `standard` group and records that it installed no controller, then runs `kmt routing install`
# and asserts what the cluster says in reply. Stage 05 runs the two features: `routing_ingress`
# issues a certificate through cert-manager and makes Traefik terminate TLS with it, `routing_guard`
# proves `kmt install` keeps its hands off and that a second `routing install` skips without
# deleting anything. Stage 09 says so when it uninstalls.
#
# The gate is the *feature*, not the lane name: `routing_ingress` in `features` is what turns the
# steps on, so no other lane walks one of them. That is also why `install_groups` stays
# `("standard",)` -- routing is not part of `kmt install` (plan decision 1), and putting the group
# name here would describe a mechanism this lane does not use.
#
# The VM shape mirrors `full` rather than a guess: this lane installs the same components, and an
# unmeasured smaller shape is what the preflight guards (NFR 6.3) would find out about first.
# The budgets are estimates, not measurements -- they get pinned against a real run by the ticket
# that adds the steps (NFR 6.1 marks measured ones).
ROUTING = Lane(
    name="routing",
    vcpu=8,
    ram_gib=16,
    disk_gib=50,
    install_groups=("standard",),
    features=("routing_ingress", "routing_guard"),
    timeouts={
        "01-provision": 630,
        # A standard install, the disable-and-wait step for k3s's controller, the routing install,
        # and the ingress asserts -- all in the one stage, so it gets more than `full`'s 1200.
        "02-install": 1500,
        "03-run": 240,
        "04-rerun": 1020,
        "05-features": 1800,
        "99-teardown": 1500,
    },
)

# The lane that answers the question the other lanes leave unasked: does the product's build
# pipeline actually build and push? It installs a standard cluster, and stage 05 runs one
# PipelineRun of the product's own docker-build pipeline against fixtures the lane owns -- a git
# repository the VM's own sshd serves, a registry the cluster itself hosts -- and reports the
# outcome and nothing else: the run reached Succeeded, and an image tagged for this run exists in
# the registry. *How* the build step builds is the product's business, and replacing the builder
# inside the product is supposed to leave this lane's verdict untouched, so no check reads the
# build step's parameters, images or logs. Heavier than the pipeline run it is named after, so it
# belongs in stage 05 and is run with `--lane tekton --scope full`; the stage budgets mirror
# `smoke`'s measured ones for everything the smoke lane already does and one wide window for the
# build, because that window is where the first-time image pulls land.
TEKTON = Lane(
    name="tekton",
    vcpu=4,
    ram_gib=6,
    disk_gib=25,
    install_groups=("standard",),
    features=("tekton_docker_build",),
    timeouts={
        "01-provision": 600,
        "02-install": 600,
        "03-run": 240,
        "04-rerun": 600,
        # clone, build, push, plus the first pull of every image the run references; wide until
        # measured (NFR 6.1).
        "05-features": 1800,
        "99-teardown": 1200,
    },
)

# The lane that answers the question no other lane answers: does `config init` alone leave a directory
# somebody could work in, and does `kmt install` then run against it with nothing written by hand in
# between. It is not cheaper than the others where that costs -- the last assert installs into k3s, so
# the VM shape is `smoke`'s and nothing smaller -- what is cheaper is the stage list: it prepares,
# asserts, installs once, and stops. No pipeline run, no rerun, no feature stage, and no uninstall,
# because the claim ends at "installed" and the VM is destroyed either way.
#
# It prepares its own directory, outside the guest copy of the checkout, for a reason that is not
# tidiness. The third property of the readiness checklist says the deployer keypair is a property of
# the configuration directory *and not of the program*, and the half you cannot see by looking in one
# file is that the keypair is absent from the checkout. A lane that prepares inside the tree it is
# asserting about cannot read that absence at all, so this lane's root is elsewhere.
BOOTSTRAP = Lane(
    name="bootstrap",
    vcpu=4,
    ram_gib=6,
    disk_gib=25,
    install_groups=("standard",),
    features=(),
    # Nothing here is measured yet, and the plan is explicit that the first run is what tightens it.
    # `config init` generates a real GPG keypair, which is the one step whose cost is dominated by
    # entropy the VM has not got yet; the install budget is `smoke`'s, plus the second preparation the
    # idempotence row has to run. The first number is wide on purpose: a stage killed by its own budget
    # is reported as a product timeout, which is the worst kind of wrong answer.
    timeouts={
        "01-provision": 630,
        "02-bootstrap": 1800,
    },
    config_dir=f"{GUEST_TREE}-config",
)

LANES = {"smoke": SMOKE, "full": FULL, "routing": ROUTING, "tekton": TEKTON, "bootstrap": BOOTSTRAP}

# How much of the lifecycle a run walks, independent of the lane's VM shape and install groups.
# `install` is the everyday question - does kubementat prepare, install correctly and come off
# the cluster clean - and is the default. `full` adds the pipeline run, the rerun idempotency
# check, the features, the uninstall confirmation probes and the reinstall-after-uninstall cycle.
# The VM is destroyed at the end of either, unless --keep-vm says otherwise.
SCOPES = {
    "install": ("01-provision", "02-install", "99-teardown"),
    "full": ("01-provision", "02-install", "03-run", "04-rerun", "05-features", "99-teardown"),
    # The bootstrap lane's own lifecycle: it prepares a directory, asserts the ten properties against
    # the real filesystem, installs once into the cluster that already exists by then, and stops. No
    # teardown stage, because the claim ends at "installed" and the VM is destroyed in the finally
    # regardless -- reaching for teardown here would mean editing the install branch for a claim nobody
    # is making, and the everyday uninstall assertions belong to the install scope.
    "bootstrap": ("01-provision", "02-bootstrap"),
}
DEFAULT_SCOPE = "install"

SLOW_FACTOR = 1.5

# Preflight guards. Measured: `virsh pool-info` lies about free
# space by ~2x against `df` on sparse qcow2, so only df/free are consulted.
MIN_HOST_FREE_DISK_GIB = 20
HOST_RAM_HEADROOM_GIB = 4
LOCK_PATH = "/tmp/kmt-e2e.lock"
LOCK_QUEUE_TIMEOUT_S = 4 * 3600  # NFR 6.2
RUNS_ROOT = "~/.kmt-e2e/runs"
OWNED_FILE = "~/.kmt-e2e/owned.json"
VM_NAME_PREFIX = "kmt-e2e-"
QUEUE_MSG_EVERY_S = 15

# ------------------------------------------------------- dummy identity
# Nothing here is a real credential (D7). .invalid is the reserved TLD that can
# never resolve, so a run that accidentally tries to use it fails loudly.
BASE_DOMAIN = "example.invalid"
AUTOMATION_GIT_URL = "git@git.example.invalid:kubementat/kubementat.git"
AUTOMATION_GIT_SERVER_HOST = "git.example.invalid"
AUTOMATION_GIT_SERVER_PORT = "22"
AUTOMATION_GIT_SERVER_SSH_USER = "git"
DOCKER_REGISTRY_BASE_URL = "docker.io"
CLUSTER_MANAGER_EMAIL = "kmt-e2e@example.invalid"
# The CI image the hello-world task runs. The registry base url and the image name/tag used to be
# answers `initialize` seeded from the environment and a template, so the harness rewrites all
# three after the preparation (see stages.stage_install): an isolated VM has no registry
# of its own, and pointing at a real public image is what lets the suite keep proving
# that pipeline runs reach Succeeded.
#
# It has to be an image with bash and /usr/bin/env, because that is the shebang the
# product's own `echo` task uses (`#!/usr/bin/env bash`) - busybox or alpine exit 127.
# ubuntu is also what `ubuntu-ci-minimal` is built from.
CI_IMAGE_NAME = "library/ubuntu"
CI_IMAGE_TAG = "latest"
CI_IMAGE = "%s/%s:%s" % (DOCKER_REGISTRY_BASE_URL, CI_IMAGE_NAME, CI_IMAGE_TAG)

# The lane that proves the product's build pipeline builds and pushes owns a registry of its own,
# and everything that lane is allowed to know about that registry lives here: the fixture image,
# the port the Service pins to (it is also written in `tests/e2e/fixtures/tekton/docker-build/
# registry.yaml`, where the cluster objects are spelled), the CI image the pipeline's steps run as,
# and the image the build step is told to be. The last one is the shape of that swap, not its
# history: which builder the product's build task currently invokes is a fact the task owns, the
# lane only hands the image down as a parameter, so replacing it changes one line here and nothing
# the lane asserts. The CI image has to carry `git`, `ssh` and `bash` for the clone step,
# which is why it is the git-bearing buildpack image and not the ubuntu one the hello-world
# task is content with. It is spelled `library/x` and tagged something other than `latest`
# for one shared reason: the lane seeds it into the node's container store under the exact
# reference the pipeline names (see `stages.py::_dockerbuild_ci_image_seeded`), and a
# non-`latest` tag is what lets the kubelet accept the seeded image without pulling again --
# which is also why the lane's registry is a plain, push-capable `registry:2` and not a
# pull-through cache: a cache-mode distribution is the same software with pushes switched off.
DOCKERBUILD_NAMESPACE = "kmt-e2e-dockerbuild"
DOCKERBUILD_REGISTRY_IMAGE = "registry:2"
DOCKERBUILD_REGISTRY_PORT = 31500
DOCKERBUILD_BUILDER_IMAGE = "moby/buildkit:v0.34.2-rootless"
DOCKERBUILD_CI_IMAGE_NAME = "library/buildpack-deps"
DOCKERBUILD_CI_IMAGE_TAG = "scm"

# Grafana's admin pair is not in this table and is not exported to the guest, on two separate
# decisions. `GRAFANA_ADMIN_PASSWORD` used to be randomized into the guest's environment by
# `suite.py` on every run, which put a credential in through the two doors that deleted the ambient
# answer and the ambient generation;
# what answers it is `kmt config init`, which *generates* one into the environment's encrypted file
# (`constants.GENERATED_ENCRYPTED_ANSWERS`), so there is nothing for the lane to supply. And
# `GRAFANA_ADMIN_USER` is a name and not a secret, already answered by the shipped layer, so
# exporting a lane spelling of it only ever made the guest's environment disagree with the tree.
# Stages that need either read it back through `kmt config show`, which tags every key it prints with
# the layer and the file that answers it.
#
# The lane's copy of the product's generation table. The bootstrap stage asserts that exactly one
# credential is invented at preparation and that the one invented is this one, and it cannot say that
# from the tree alone -- the tree answers both a seeded default and a generated token, and which is
# which is a fact about the program and not about the file. Naming it here is also the way a change to
# the product's table shows up: the assert goes red against a tree that still generates correctly,
# which is the cheapest place to notice the two lists disagree.
GENERATED_ENCRYPTED_ANSWER_NAMES = ("GRAFANA_ADMIN_PASSWORD",)

# The one grafana account the lane declares. `create-grafana-accounts` refuses to act when
# the environment's `static.encrypted.json` declares none, so a lane that declares none has a
# command it can never execute -- the owner's option A, and the reason the harness writes this row
# before the accounts stage (`stages.py::_seed_grafana_account_row`). The email is lane identity,
# not a credential, and it uses the reserved TLD for the same reason `CLUSTER_MANAGER_EMAIL` does:
# nothing can ever be delivered to it. Its password is *not* here; it is generated for the run when
# the row is written, so the tree never names a login. See `stages.py`.
GRAFANA_ACCOUNT_EMAIL = "kmt-e2e-account@example.invalid"
GRAFANA_ACCOUNT_NAME = "kmt-e2e lane account"

# The identity the lane's deployer keypair is generated under. `kmt config init` refuses to invent
# one (the answer is the caller's to give), so the lane spells it the way it spells every other
# fixture address -- the reserved `.invalid` TLD, like GRAFANA_ACCOUNT_EMAIL above, because nothing
# can ever be delivered to it. It is lane identity, not a credential.
DEPLOYER_GPG_EMAIL = "kmt-e2e-deployer@example.invalid"

# The variables the guest is told about. The first block used to be `initialize`'s requirement
# list; the command is deleted and nothing in `src/` reads those names any more, so
# exporting them is inert -- they stay only because the lane's own prose reads the matching
# constants above. What was *not* inert was the credential pair, and it is gone: a lane that
# exports an answer configures from outside the tree, which is the whole defect that rule closes.
# The only variable that is load-bearing here is `KUBEMENTAT_PLATFORM_CONFIG_DIR`, because a
# configuration root is named and never located, and the guest's `kmt` is not handed
# `--config-dir` by the capability table.
DUMMY_ENV = {
    "BASE_DOMAIN": BASE_DOMAIN,
    "AUTOMATION_GIT_URL": AUTOMATION_GIT_URL,
    "AUTOMATION_GIT_SERVER_HOST": AUTOMATION_GIT_SERVER_HOST,
    "AUTOMATION_GIT_SERVER_PORT": AUTOMATION_GIT_SERVER_PORT,
    "AUTOMATION_GIT_SERVER_SSH_USER": AUTOMATION_GIT_SERVER_SSH_USER,
    "DOCKER_REGISTRY_BASE_URL": DOCKER_REGISTRY_BASE_URL,
    "CLUSTER_MANAGER_EMAIL": CLUSTER_MANAGER_EMAIL,
    "KUBEMENTAT_PLATFORM_CONFIG_DIR": f"{GUEST_TREE}/platform_config",
}

ENVIRONMENT = "dev"
TEAM = "smoke"  # fixed identity in both lanes

# ---------------------------------------------- the cluster this lane is addressing
#
# Where the lane's cluster claim comes from, spelled once. The same reason every other table in this
# file exists: a stage that remembers its own context name, its own kubeconfig and its own fixture
# directories is a stage nobody can audit, and one half of a single claim disagreeing with the other
# half is a lane that addresses a cluster no file in `platform_config` says it meant.
#
# `kmt config init` writes `"CLUSTER_KUBE_CONTEXT": null` and says out loud what that means: the
# environment exists, and addresses nothing. A lane that leaves the file alone installs
# into nothing, because the current-context fallback is gone, and a lane that exports
# `$KUBECONFIG` to make the run work has fixed the defect by reproducing it. So the rig names the
# context itself, in the file the environment's own `static.json` answers from, and says which one it
# chose and why -- see
# `stages.py::_declare_the_cluster`, which logs that sentence and records it in `result.json`.

# The two environment-level keys that name a cluster from the environment's file. The harness reads
# and writes both, and a key the
# harness spells twice is a key that goes stale in one of the two places.
CLUSTER_KUBE_CONTEXT_KEY = "CLUSTER_KUBE_CONTEXT"
CLUSTER_KUBECONFIG_PATH_KEY = "CLUSTER_KUBECONFIG_PATH"
# The variable that the process environment answers from before any file is read, and the one thing
# the rig promises not to set in order to
# make a run work. It is named here for the assert that proves it: `stages.py` reads the guest's
# environment rather than asserting the absence of a string in a file nobody ran.
KUBECONFIG_ENVIRONMENT_VARIABLE = "KUBECONFIG"

# The rig's two fixture environments. Both are environments in the sense the check enumerates -- a
# directory at depth 1
# of `platform_config` that carries a `static.json` of its own -- so the 1:1 check reads them exactly
# as it reads `dev`. That is the point: a fixture the check never had to enumerate would be a fixture
# that proves nothing, and both are removed before the feature returns, with
# `cluster-fixtures-are-removed` saying that it was.
COLLISION_ENVIRONMENT = "kmt-e2e-collision"
UNRESOLVABLE_ENVIRONMENT = "kmt-e2e-unreachable"
# The context that is not in any kubeconfig the rig resolves, for the report-and-skip row. Named
# as a fixture rather than inline so the assert that quotes the *reason* can say which name the
# report was talking about.
UNRESOLVABLE_CONTEXT = "kmt-e2e-no-such-context"
# The second context, and the rig-owned kubeconfig it lives in. It names the *same* cluster as the
# k3s install's own context, because the identity the check compares is the server address and not
# the name: two
# environments claiming one cluster under two different context names is the collision this row
# exists to catch, and a fixture that reused the one existing name would only have proved that the
# two strings compared equal.
COLLISION_CONTEXT = "kmt-e2e-alias"
# `/var/tmp` for the same reason `GUEST_STAGING_DIR` is `/var/tmp`: `/tmp` can be swept mid-run. And
# it is the rig's own file -- a copy of the resolved kubeconfig with one context added to it --
# because `~/.kube/config` belongs to the machine, not to a lane.
COLLISION_KUBECONFIG = "/var/tmp/kmt-e2e-collision.kubeconfig"

# The one question this rig cannot answer, spelled once here so the line a human reads in the stage
# output and the line `result.json` carries are the same sentence. The stage that cannot answer it
# prints it (`stages.py::feature_cluster_addressing`); a report that listed four covered rows and let
# a reader infer the fifth is the failure this string exists to prevent.
NOT_COVERED_TWO_CLUSTERS = (
    "not covered: the other half of the two-cluster question -- `kmt install dev` and `kmt install prod` back to back into "
    "two different clusters. This rig is one virt-runner machine with one k3s installation, so it can "
    "prove that a command addresses the cluster its own environment declares, and that two "
    "environments claiming that one cluster are refused and write nothing; it cannot prove a second "
    "cluster, and no assertion in this lane claims that it does."
)

# The service accounts stage 05 creates. Two distinct names on purpose: the read-only
# account is compared against the admin account, so sharing a name would overwrite the
# first one's role bindings. Must be lowercase DNS-shaped: the stage greps the emitted
# `serviceaccount/<name>` line out of the script output.
TEAM_ACCOUNT = "kmt-e2e-user"
TEAM_ACCOUNT_READ_ONLY = "kmt-e2e-readonly"

# Placeholders `argv_for` can resolve from the lane identity alone. A stage adds the
# per-call ones (see STAGE_PLACEHOLDERS) on top of this, so a typo in a capability's argv
# has exactly one place to show up: tests/test_harness_capability_argv.py.
LANE_PLACEHOLDERS = {
    "env": ENVIRONMENT,
    "team": TEAM,
    "workdir": GUEST_TREE,
    "account": TEAM_ACCOUNT,
    "read_only_account": TEAM_ACCOUNT_READ_ONLY,
    # The configuration root. Thirteen rows used to spell `{workdir}/platform_config` inside their argv,
    # which meant a lane that wanted a tree of its own had to either install into the shared one or grow a
    # second row per verb -- two rows for one command is two answers for one question, and they drift the
    # first time somebody edits the verb. The root is a lane-level answer, so it lives with the lane
    # shape, and `LaneRunner.ctx` puts it here so a capability row can name it without knowing the lane.
    "config_dir": f"{GUEST_TREE}/platform_config",
}

# Placeholders a stage supplies per call rather than from the lane identity.
#
# `answer` is one `KEY=VALUE` the verb is handed, and `key` is a key with no answer attached --
# `config unset` takes exactly that shape and belongs to the same family of verb that `config.set` is
# in, so it cannot be spelled out in a stage as an argv nobody audited.
# `answers_document` is a path and not a value: it is the staged JSON document `config set --from-file`
# reads, which is how a credential stays out of a command line and out of every artifact.
STAGE_PLACEHOLDERS = ("group", "port", "ns", "name", "pod", "answer", "key", "answers_document",
                      "run_file")

# ------------------------------------------------------- capability table
# Keyed by capability, never by CLI name: the flat commands are being renamed to
# Click groups with no aliases, so a rename is one row of this table. Rows whose
# current name still has to be grouped carry the future argv in `fin62`, and a run
# reports them as rows that still need the grouped CLI form.
#
# argv templates:
#   {env} {team}        fixed identity
#   {workdir}           the guest copy of the checkout
#   {group}             helmfile installation group
#   {port}              tunnel local port
#   {ns} {name}         namespace / resource name


@dataclass
class Cap:
    capability: str
    argv: list
    lanes: str = "S+F"  # S, F, S+F
    mutating: bool = False  # executed twice by stage 04-rerun
    fin62: str = ""  # non-empty when the current name still needs the grouped form
    negative: str = ""  # expected failure contract instead of a success contract
    stdin: str = ""  # only where the product prompts and has no flag yet (see notes)
    note: str = ""


def _c(**kw):
    return Cap(**kw)


CAPABILITIES = {
    # The row for this verb, spelling the verb the way it takes its arguments: the root is
    # `--config-dir` like every other verb here, the layer names are flags, and the deployer identity is
    # the lane's own fixture address.
    #
    # This row needs no marker deletion, and the reason is not the one the sentence used to give. It
    # used to say that preparing a directory that already holds its files is a skipped step, and that
    # was false -- the guard that keeps product shape out of a tree refuses exactly that tree, which is
    # what a VM run found. What is true
    # is narrower: the checkout ships no `platform_config/` any more, so there is nothing here for the
    # guard to trip on, and the verb is handed a directory that does not exist yet.
    "config.init": _c(
        capability="config.init",
        argv=["kmt", "config", "init",
              "--environment", "{env}", "--team", "{team}", "--gpg-email", DEPLOYER_GPG_EMAIL,
              # The answers this lane owes are asked at init and not patched afterwards.
              # Only two keys appear here, and the rule for which ones is that an identity key
              # gets a `--set` only when its value is *not* what the layers already answer.
              # `DOCKER_REGISTRY_BASE_URL` has no L0 at all, and `TEKTON_CI_IMAGE_NAME` differs from
              # the shipped `ubuntu-ci-minimal`; `TEKTON_CI_IMAGE_TAG` is `latest` in both, so it
              # stays in this file for the lane's own assertions and is not an argument anybody was
              # asked for. Every other answer the tree needs is either generated by the verb itself
              # (grafana's admin password) or named by a later stage through its own verb.
              "--set", "DOCKER_REGISTRY_BASE_URL=" + DOCKER_REGISTRY_BASE_URL,
              "--set", "TEKTON_CI_IMAGE_NAME=" + CI_IMAGE_NAME, "--config-dir", "{config_dir}"],
        mutating=True,
        note="prepares the configuration directory end to end: the tree, the deployer keypair "
             "inside it, and git-crypt made live there (the initialize row and its "
             "marker-deleting exception died with the command). The lane's own answers stay "
             "inside the command that prepares the directory, so nothing edits the tree it "
             "just wrote.",
    ),
    # -------------------------------------------------------------------------------------
    # The verbs that stand between the lane and the configuration tree.
    #
    # Before these rows the lane wrote configuration with `jq`: a `jq --arg` for one key, a
    # `jq '.[key]=null'` for the state `unset` is the only way to leave, and a `jq --slurpfile`
    # that merged an account row into the encrypted file. Those assertions proved that `jq` works.
    # A lane configures as a *consumer* of `kmt config` and not as a writer, so every edit below is one
    # row of this table, and a stage that spells its own argv here is a
    # second writer with no owner -- and one writer of one tree that no one owns is a defect.
    #
    # `config set` is spelled three ways because the two flags change what the verb *does*, not
    # merely how it reads: `--team` targets the team layer, `--encrypted` targets the encrypted file
    # of that layer, and `unset` has no `--encrypted` at all because clearing one file of a pair is
    # something the reader never sees (`cli/commands/config.py:46`).
    # -------------------------------------------------------------------------------------
    "config.set": _c(
        capability="config.set",
        argv=["kmt", "config", "set", "{env}", "{answer}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="answers one key on the environment layer. This is the only way the lane names an "
             "answer, and the assert that follows it is evidence about kubementat and not about jq.",
    ),
    "config.set.team": _c(
        capability="config.set.team",
        argv=["kmt", "config", "set", "{env}", "{answer}",
              "--team", "{team}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="the team layer of the same verb, for a key `Config` resolves one directory deeper.",
    ),
    "config.set.encrypted": _c(
        capability="config.set.encrypted",
        argv=["kmt", "config", "set", "{env}", "{answer}",
              "--encrypted", "--config-dir", "{config_dir}"],
        mutating=True,
        note="the encrypted half of the environment layer. A credential the lane declares travels "
             "this way or not at all: there is no ambient door to a credential.",
    ),
    "config.set.encrypted.team": _c(
        capability="config.set.encrypted.team",
        argv=["kmt", "config", "set", "{env}", "{answer}",
              "--team", "{team}", "--encrypted", "--config-dir", "{config_dir}"],
        mutating=True,
        note="the encrypted half of the team layer, so a lane never has to choose between the "
             "direction and the file.",
    ),
    "config.set.encrypted.document": _c(
        capability="config.set.encrypted.document",
        argv=["kmt", "config", "set", "{env}", "--encrypted",
              "--from-file", "{answers_document}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="the encrypted half of `config set`, answered from a document instead of an argv. Two keys "
             "and one run, which is what the document is for; and a password that only exists as a path "
             "on the guest, which is what it protects.",
    ),
    "config.set.team.encrypted.document": _c(
        capability="config.set.team.encrypted.document",
        argv=["kmt", "config", "set", "{env}", "--team", "{team}",
              "--encrypted", "--from-file", "{answers_document}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="a team layer's encrypted answers, from a document: what a fixture that gives a second team "
             "an SSH deploy key travels through, because an argv is not where a key goes.",
    ),
    # What this row is about: the state a refusal that names the file, the key and the value is about is
    # the state `config init` left behind,
    # and `unset` is the verb that leaves it. A `jq '.[key]=null'` produced a file no product verb
    # ever writes, so the refusal was measured against a shape the product does not produce.
    "config.unset": _c(
        capability="config.unset",
        argv=["kmt", "config", "unset", "{env}", "{key}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="removes one key from the layer above, which is the real not-yet-named state. It takes "
             "no --encrypted, because clearing one file of a pair is a change the reader never sees.",
    ),
    "config.unset.team": _c(
        capability="config.unset.team",
        argv=["kmt", "config", "unset", "{env}", "{key}",
              "--team", "{team}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="the same removal on the team layer.",
    ),
    # The read-back: `show` prints every key the tree answers, each line tagged with the layer and
    # the file that answers it, so a stage gets the answer and its provenance from one run. A stage
    # that greps a value out of the file is a stage with a second opinion about its own fixture.
    "config.show": _c(
        capability="config.show",
        argv=["kmt", "config", "show", "{env}", "--config-dir", "{config_dir}"],
        note="every key the environment's tree answers, each tagged `(layer from path)`. The value is "
             "separated from that tag by three spaces, so a stage that cuts the line at one space "
             "reads the tag into the answer.",
    ),
    "config.show.team": _c(
        capability="config.show.team",
        argv=["kmt", "config", "show", "{env}", "{team}", "--config-dir", "{config_dir}"],
        note="the same listing one layer deeper.",
    ),
    # The verdict verb. It had no row, because no stage ever called it: the suite used to read the tree
    # itself and decide whether it was usable, which is the second opinion this surface refuses to give.
    # The bootstrap checklist asks for it directly -- one property is that the tree a bootstrap leaves is
    # *usable*, and usable is a claim the product's own verdict answers and not one a stage can assemble
    # out of file checks.
    "config.validate": _c(
        capability="config.validate",
        argv=["kmt", "config", "validate", "{env}", "{team}", "--config-dir", "{config_dir}"],
        note="one verdict, every fault named in the same run. Reads and writes nothing, addresses no "
             "cluster, execs nothing. The team is positional here and not a flag because the verb "
             "resolves nothing.",
    ),
    # A fixture environment, which is what the collision lane needs: two environments addressing one
    # cluster. Copying a directory (`cp -a`) is a writer of configuration, and the rule that there is one
    # a stage which "just" copies a JSON file is a defect and not a shortcut, so the fixture is built
    # by the verb that creates environments and then answers its own keys.
    "config.add-team": _c(
        capability="config.add-team",
        argv=["kmt", "config", "add-team", "{env}", "{team}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="one team layer, with its pair of files and its tekton directories, and nothing answered. "
             "The scope check's fixture is a second team, and `mkdir -p` plus two `write_file`s would "
             "have made a team only this harness believes in.",
    ),
    "config.add-environment": _c(
        capability="config.add-environment",
        argv=["kmt", "config", "add-environment", "{env}", "--config-dir", "{config_dir}"],
        mutating=True,
        note="one more environment, with its two static files and its two tekton directories, and "
             "nothing copied from anywhere else.",
    ),
    # The environments listing. It is in the table rather than spelled out in a stage for the reason every row
    # is here: it is how the lane answers "what environments do I have, and what cluster is each one"
    # without contacting a cluster, and it is the read the 1:1 check itself runs (`EnvironmentRegistry`
    # backs both), so a stage that invented its own argv would be a second opinion about a primitive.
    # It takes no environment and no team because it resolves nothing -- see `Context.build` in
    # `kubementat/core/identity/context.py`.
    "environments.list": _c(
        capability="environments.list",
        argv=["kmt", "environments", "list"],
        note="one row per environment in the root: its address, the context that reaches it, the file "
             "that declared it, and what the check compared. An environment it could not resolve is "
             "named with its reason rather than left off the list, which is what keeps the "
             "listing and the refusal from disagreeing about what this `platform_config` contains.",
    ),
    "install": _c(
        capability="install",
        argv=["kmt", "install", "{env}", "{team}", "--helmfile-installation-group", "{group}",
              "--config-dir", "{config_dir}", "--yes"],
        mutating=True,
        note="--yes skips the confirmation; without it install fails closed on a non-terminal stdin",
    ),
    "uninstall": _c(
        capability="uninstall",
        argv=["kmt", "uninstall", "{env}", "--config-dir", "{config_dir}", "--yes"],
        mutating=True,
        # The scripted escape hatch. Without it the
        # CLI refuses to run when it cannot prompt, so nothing here needs stdin.
        note="--yes skips the confirmation; without it install fails closed on a non-terminal stdin",
    ),
    "uninstall.confirm": _c(
        capability="uninstall.confirm",
        argv=["kmt", "uninstall", "{env}"],
        lanes="-",  # never run as a mutation; it is the confirmation probe
        note="the uninstall capability with its --yes flag removed: "
        "answering 'no' must delete nothing, and no-terminal must fail closed",
    ),
    "secrets.setup": _c(
        capability="secrets.setup",
        argv=["kmt", "setup-secrets", "{env}", "{team}"],
        mutating=True,
    ),
    "registry.access": _c(
        capability="registry.access",
        argv=["kmt", "setup-docker-registry-access", "{env}", "{team}"],
        mutating=True,
    ),
    "tunnel.pod": _c(
        capability="tunnel.pod",
        # The environment first, because that is what the command asks for now: a
        # port-forward is opened *against a cluster*, and `{ns}`/{pod} only say where inside it.
        argv=["kmt", "tunnel", "pod", "{env}", "{ns}", "{pod}", "--local-port", "{port}",
              "--remote-port", "{port}"],
        lanes="F",
    ),
    "tunnel.grafana": _c(
        capability="tunnel.grafana",
        argv=["kmt", "tunnel", "grafana", "{env}", "--local-port", "{port}"],
        lanes="F",
    ),
    "tunnel.tekton": _c(
        capability="tunnel.tekton",
        argv=["kmt", "tunnel", "tekton", "{env}", "--local-port", "{port}"],
        lanes="F",
    ),
    # Renamed with the component: `vault` left the platform and OpenBao took the `extra` group. It is
    # a **negative** case, and the reason is now true again everywhere: `extra` is installed by no lane
    # (it was in `full` for one day, found the init/unseal gap, and came back out), so the tunnel has
    # nothing to reach wherever it runs. Do not make it positive without also putting `extra` back in
    # the lane's groups -- a tunnel that claims to reach a component no lane installs is asserting a
    # fact about a cluster the lane never built.
    "tunnel.openbao-ui": _c(
        capability="tunnel.openbao-ui",
        argv=["kmt", "tunnel", "openbao-ui", "{env}", "--local-port", "{port}"],
        negative="component not installed",
        note="negative case: `extra` is in no lane's install_groups, so there is nothing to tunnel to",
    ),
    "tekton.run-pipeline": _c(
        capability="tekton.run-pipeline",
        argv=["kmt", "tekton", "run-pipeline", "{env}", "{team}", "global/hello-world-pipeline-run.yml"],
        mutating=True,
    ),
    # The same command, with the run file named by the stage instead of pinned to the hello-world
    # global. A stage that renders its own run cannot address it through a table row whose third
    # argument is a constant, and a stage that passes a path is already following the convention the
    # command's own help states.
    "tekton.run-pipeline-file": _c(
        capability="tekton.run-pipeline-file",
        argv=["kmt", "tekton", "run-pipeline", "{env}", "{team}", "{run_file}"],
        mutating=True,
    ),
    "tekton.list": _c(
        capability="tekton.list",
        argv=["kmt", "tekton", "list", "{env}", "{team}"],
    ),
    "tekton.cleanup-pipeline-runs": _c(
        capability="tekton.cleanup-pipeline-runs",
        argv=["kmt", "tekton", "cleanup-pipeline-runs", "{env}", "{team}", "--filter", "succeeded"],
        lanes="F",
        mutating=True,
    ),
    "tekton.setup-pipelines": _c(
        capability="tekton.setup-pipelines",
        argv=["kmt", "tekton", "setup-pipelines", "{env}", "{team}"],
        mutating=True,
    ),
    "tekton.setup-triggers": _c(
        capability="tekton.setup-triggers",
        argv=["kmt", "tekton", "setup-triggers", "{env}", "{team}"],
        mutating=True,
    ),
    "tekton.uninstall-pipelines": _c(
        capability="tekton.uninstall-pipelines",
        argv=["kmt", "tekton", "uninstall-pipelines", "{env}", "{team}", "-y"],
        lanes="F",
        mutating=True,
    ),
    "helm.helmfile-apply": _c(
        capability="helm.helmfile-apply",
        argv=["kmt", "helm", "apply", "{env}", "--helmfile-label-filter", "group={group}", "-y"],
        lanes="F",
        mutating=True,
        note="--helmfile-label-filter is what decides which releases the apply touches, so it is "
             "the flag this row exists to carry; -y applies without asking, because an ssh lane has "
             "no terminal and helmfile reads the EOF of a prompt it was given as a refusal. No "
             "--team: every path this command derives is environment-level, so the option was "
             "deleted with the guard behind it ",
    ),
    # Routing is not part of `kmt install` (plan decision 1), and it is not applied by a label
    # selector either: `kmt routing install` reads the cluster first and declines when a Traefik it
    # does not own is already serving the node. A `--helmfile-label-filter group=routing` can only
    # ever apply, which is why the group gets its own row instead of a `helm.helmfile-apply` -- see
    # `GROUP_APPLY_CAPABILITIES` below for the row that answers "how is `routing` applied".
    "routing.install": _c(
        capability="routing.install",
        argv=["kmt", "routing", "install", "{env}", "--yes"],
        lanes="F",  # and by the `routing` lane, which has no letter here and is named in LANES
        mutating=True,
        note="the only row that installs a controller. --yes because an ssh lane has no terminal to "
             "answer the confirmation with, and the command fails closed rather than assume a yes",
    ),
    "routing.status": _c(
        capability="routing.status",
        argv=["kmt", "routing", "status", "{env}"],
        lanes="F",
        mutating=False,  # three reads and no write, so stage 04 does not re-run it
        note="read-only, so no --yes: there is nothing to confirm. It is the question an operator "
             "asks before the row above, and the assert that answers it on the cluster rather than "
             "on stdout",
    ),
    # `--update` is a row of its own rather than a flag some stage remembers to add, because that is
    # the difference between a lane that runs the escape hatch and one that re-applies the release.
    # Only the routing lane runs it: on the everyday lane the release it would upgrade is not
    # kubementat's, and `--update` there means a second controller next to k3s's, which is a decision the everyday lane has no business taking.
    "routing.install.update": _c(
        capability="routing.install.update",
        argv=["kmt", "routing", "install", "{env}", "--yes", "--update"],
        lanes="F",  # and by the `routing` lane, which has no letter here and is named in LANES
        mutating=True,
        note="the same command with the escape hatch. Stage 04 does not re-run it: the rerun loop "
             "walks install_groups[1:], and this lane puts routing in no group of its own, so the "
             "row is run exactly once by the feature that asks for it",
    ),
    # Script execution: no lane runs it, because there is nothing left for a lane to run. The Task
    # that took a script path and executed it, and the one script it existed to run, are both
    # deleted, and `kmt run-script` — the CLI's own door to a script by path — is deleted
    # here too, so neither half of the capability is open any more and what a pipeline needs
    # it runs inline.
    #
    # That is the line the owner drew: a Task that does its own job stays, and a Task whose only job
    # is to execute a path you hand it goes. `create-mysql-database.yml` and `execute-mysql-file.yml`
    # are the counter-example and both survive - each runs `mysql -h ... -e` in its own step, so
    # neither needs a script handed to it.
    #
    # What the deleted pair offered is retired rather than migrated, and the consequence is stated
    # in its present tense: there is no longer any way for kubementat to create databases and users
    # from configuration. The key that row configured went first - `MYSQL_DATABASE_CONFIGURATION` was
    # removed from `templates/environment/team/static.encrypted.json.template`
    # and from `automation.py` (8d9c318) - and the script that read it goes with this pair, so the two
    # sides of the capability are both absent: no key declares it, nothing reads it, and no command or
    # Task replaced the reading. What survives is narrower
    # and parameter-shaped: the two mysql Tasks create exactly the database or file each run names,
    # and a team that needs more than that declares it itself. A replacement via other helm charts
    # is a separate project and gets its own rows.
    #
    # The backing-service rows (task.install-mysql / -redis / -nginx) are gone with the install
    # automation they drove (owner decision 2026-10-27,
    # docs/plans/lane-verification-3e8b3f6.md section 2.1: the pinned bitnami images return NotFound
    # on a clean cluster, so all three were red on every full lane).
    # Team accounts. These used to run through `kmt run-script`, which
    # executed a shell script that read stdin itself - so the lane was the only thing that
    # could reach them, and a lane run had no terminal to answer the confirmation with. They
    # are now `kmt users ...` commands: same `--yes` contract `uninstall` established, and the
    # lane runs them the way a person does.
    "task.create-team-account": _c(
        capability="task.create-team-account",
        argv=["kmt", "users", "create-team-account", "{account}",
              "--environment", "{env}", "--team", "{team}", "--yes"],
        lanes="F",
        mutating=True,
        note="the command confirms before editing unless --yes is given, and fails closed when "
             "there is no terminal to ask in",
    ),
    "task.create-team-account-read-only": _c(
        capability="task.create-team-account-read-only",
        argv=["kmt", "users", "create-team-account-read-only", "{read_only_account}",
              "--environment", "{env}", "--team", "{team}", "--yes"],
        lanes="F",
        mutating=True,
        note="a distinct service account, otherwise it collides with the admin account it is compared against",
    ),
    "task.delete-team-account": _c(
        capability="task.delete-team-account",
        argv=["kmt", "users", "delete-team-account", "{account}",
              "--environment", "{env}", "--team", "{team}", "--yes"],
        lanes="F",
        mutating=True,
        note="deletes the bindings and the service account; it also deletes the token Secret, "
             "which the bash left behind and which keeps working after the account is gone",
    ),
    "task.create-grafana-accounts": _c(
        capability="task.create-grafana-accounts",
        argv=["kmt", "users", "create-grafana-accounts", "{env}", "--yes"],
        lanes="F",
        mutating=True,
        # The whole reason this row is not vacuous any more: the address is resolved rather than
        # guessed. `--grafana-url` first, then the grafana service's ClusterIP read through
        # GRAFANA_SERVICE_SELECTOR, and a failure when neither answers -- there is no default and
        # no empty-body-and-carry-on.
        note="reaches grafana through the HTTP client, not curl: the address comes from "
             "--grafana-url or the service ClusterIP, so a grafana that is not there fails the "
             "row instead of printing a skip. The row used to run "
             "scripts/user_management/create_grafana_accounts.sh through run-script, whose "
             "127.0.0.1:31827 default made it green while it created nothing.",
    ),
    "task.cleanup-job": _c(
        capability="task.cleanup-job",
        argv=["kmt", "tekton", "setup-cleanup-job", "{env}", "--team", "{team}"],
        lanes="F",
        mutating=True,
        note="the namespace is read from CLUSTER_CRONJOBS_DEPLOYMENT_NAMESPACE, so a run that "
             "landed the job somewhere else than the assert looks is a config answer, not a place",
    ),
}

CAP_ORDER = list(CAPABILITIES)

# Rows that still need the grouped CLI form.
NEEDS_FIN62 = [name for name, cap in CAPABILITIES.items() if cap.fin62]


def is_runnable(lane, scope):
    """True when this lane can run this scope, which is a question about budgets and nothing else.

    A scope names stages; a lane names what each of its stages costs it. A pair is therefore only
    runnable where the lane has measured every stage that scope selects -- and this is the one place
    that answers it, so the command line refuses the pair before a VM exists and the stage dispatcher
    cannot disagree with it afterwards.
    """
    return not missing_budgets(lane, scope)


def missing_budgets(lane, scope):
    """The stages `scope` runs that `lane` has no budget for. Empty means runnable.

    Named as a defect rather than a property because that is what it is: the reason a lane that skips
    the run, re-run and feature stages is a lane that must not be forced to borrow numbers for work it
    never does.
    """
    return [stage for stage in SCOPES[scope] if stage not in lane.timeouts]


def argv_for(capability, **ctx):
    """Resolve a capability into an argv list. Stages never spell out a command."""
    cap = CAPABILITIES[capability]
    out = []
    for token in cap.argv:
        for key, value in ctx.items():
            token = token.replace("{%s}" % key, str(value))
        out.append(token)
    return out


# --------------------------------------------- how a helmfile group gets applied
# "How is this group applied" has exactly one answer, and it is this table rather than whichever
# stage happens to be walking the list. `helm.helmfile-apply` is what a group with no entry gets --
# today's behaviour, unchanged, so a new helmfile group needs no harness change to keep working --
# and `routing` is what a selector cannot express: the group is applied by the command that reads
# the cluster and declines, not by a label filter that has no way to decline.
#
# Both places that apply a later group resolve through here: `stage_install`'s loop over
# `install_groups[1:]`, and `stage_rerun` through `_rerun_capabilities`. That second one is the
# point. A stage that rebuilds the later groups as bare applies would apply `routing` one way at
# install and another way at rerun -- the rerun would install a second controller while the
# idempotency line reads it as a quiet cluster.
HELMFILE_APPLY = "helm.helmfile-apply"
GROUP_APPLY_CAPABILITIES = {
    "routing": "routing.install",
}

# The groups whose releases are checked for running *workloads* and not just for a `deployed` release
# record. A list rather than every group, because `standard` and `routing` have their own stage
# asserts and a pod still starting is a passing install for them, while a group whose StatefulSet
# nobody watched is precisely the gap this is for.
#
# `extra` is listed here even though `full` no longer installs it, so this is currently dormant -- and
# that is deliberate. The config entry is what makes the assert easy to re-enable when the OpenBao
# init/unseal decision lands; see the note on `Lane.install_groups` and the TODOs.md entry.
WORKLOAD_ASSERTED_GROUPS = ("extra",)


def apply_capability_for_group(group):
    """The capability that applies one helmfile installation group. Group name in, capability out."""
    return GROUP_APPLY_CAPABILITIES.get(group, HELMFILE_APPLY)


# ------------------------------------------------------ the routing lane's fixtures
# The routing lane needs two things on the cluster that kubementat does not install: a backend an
# Ingress can reach, and a certificate to terminate TLS with. They live here rather than inside a
# stage step for the same reason every other table in this file does -- a stage that spells out its
# own image and its own object names is a stage nobody can audit.
#
# `image` is `library/` on purpose: the self-audit's `no-personal-registry-namespace` row fails on a
# docker.io URL with a personal namespace, and `traefik/whoami` is exactly that shape. It is pinned
# to a tag that exists because an unpinned one is the difference between a red lane and a red lane
# whose detail says `ImagePullBackOff`.
#
# `host` uses the reserved `.invalid` TLD, the same choice `BASE_DOMAIN` makes further up. It is not
# decoration: nothing in this lane is asked to resolve anything, and an address that *cannot*
# resolve is what keeps the lane from pretending it proved real ACME issuance.
ROUTING_LANE = {
    "backend": "kmt-e2e-routing-backend",          # Deployment + Service, one namespace: `namespace`
    "image": "docker.io/library/nginx:1.27-alpine",
    "issuer": "kmt-e2e-routing-ca",                # cert-manager self-signed Issuer
    "certificate": "kmt-e2e-routing-tls",           # Certificate -> the Secret Traefik terminates with
    "ingress": "kmt-e2e-routing",                   # spec.tls names `certificate`, class names `class`
    "host": "kmt-e2e-routing.invalid",
    "namespace": "default",  # no new namespace: an uninstall must not have anything to say about objects that were ours
}


# --------------------------------------------------------------- idempotency

# Resource kinds whose whole set is hashed before/after a second execution of a
# mutating capability. Namespace+name only: `--no-headers` prints elapsed
# time, which changes between two passes and would make the hash meaningless.
IDEMPOTENCY_KINDS = [
    "namespace",
    "role",
    "rolebinding",
    "serviceaccount",
    "secret",
    "pipeline",
    "task",
    "eventlistener",
    "trigger",
    "triggerbinding",
]

# The snapshot is taken across the whole cluster, so a namespace kubementat never
# writes to must not count as a delta. k3s's own helm-controller installs and updates
# its bundled addons (traefik: `chart-values-*`, `sh.helm.release.v1.traefik*.v1` and the
# `helm-traefik` ServiceAccounts) whenever it feels like, which otherwise reads as if
# `install` had just created them. The tree references kube-system exactly once, in a
# commented-out helmfile line, so nothing here loses coverage of what we manage.
IDEMPOTENCY_EXCLUDED_NAMESPACES = ("kube-system", "kube-public", "kube-node-lease")

# Kinds whose *payload* is hashed as well as their names: a rerun that regenerates a password
# keeps every name and still hands out a new credential.
IDEMPOTENCY_CONTENT_KINDS = ["secret", "configmap"]

# Cluster-scoped kinds compared against the post-provision baseline after uninstall. No namespace
# delete reaches them, which is why they are what an uninstall forgets.
CLUSTER_SCOPED_RESIDUE_KINDS = [
    "customresourcedefinitions",
    "clusterroles",
    "clusterrolebindings",
    "mutatingwebhookconfigurations",
    "validatingwebhookconfigurations",
    "persistentvolumes",
]


@dataclass(frozen=True)
class ExpectedResidue:
    """One kind's declared leftovers, and the sentence that says why they are expected.

    Both halves print. The names tell a reader what the lane is excusing; the reason tells them
    why, so an excuse never has to be looked up in a ticket.
    """

    names: frozenset = frozenset()
    why: str = ""


# What the teardown is *expected* to leave behind, kind by kind. Two ClusterRoles are here because
# `869aeb8` deliberately does not reach them: `tekton-triggers-eventlistener-roles` and
# `tekton-triggers-eventlistener-clusterroles` are bound by the EventListener controller at run
# time, so no `ClusterRoleBinding` in the pinned release references them and they carry no
# `managed-by` label for the uninstall sweep to claim. `automations/uninstall/kubementat.py`'s
# `delete_cluster_scoped_rbac_kubementat_owns` states this in advance and refuses to hard-code
# upstream role names -- that rule stays where it is. The names belong in the harness that
# measures the residue, not in the product's sweep, and the assert still goes red for anything not
# listed: this table excuses what it names, nothing else. A kind listed with an empty `names` was
# measured against the post-provision baseline and nothing there is expected to survive.
# (Row 27, owner decision 2026-10-30: name the known residue, do not accept a permanent red and do
# not sweep it by name.)
CLUSTER_SCOPED_EXPECTED_RESIDUE = {
    "clusterroles": ExpectedResidue(
        names=frozenset(("tekton-triggers-eventlistener-roles",
                         "tekton-triggers-eventlistener-clusterroles")),
        why="the EventListener controller binds these at run time, so no binding in the pinned "
            "release references them and they carry no managed-by label the uninstall sweep could "
            "claim them with",
    ),
    "customresourcedefinitions": ExpectedResidue(),
    "clusterrolebindings": ExpectedResidue(),
    "mutatingwebhookconfigurations": ExpectedResidue(),
    "validatingwebhookconfigurations": ExpectedResidue(),
    "persistentvolumes": ExpectedResidue(),
}

# ------------------------------------------------------------- tunnel signals

TUNNEL_PORTS = {
    "tunnel.grafana": "3001",
    "tunnel.tekton": "9097",
    "tunnel.openbao-ui": "8205",
}

# A 200 from the wrong pod must fail, so the pass condition is the body. What is read,
# and what is looked for in it, belong in this table because they are one claim: the token has
# to be a byte of *that* document. Neither half of the old table was that kind of honest -
# `api/login`/`signin` are what a login form says once its bundle runs, not what Grafana
# returns for `/login`, and `PipelineRun` is a component name inside the tekton dashboard's JS
# bundle, so asserting it against the dashboard's `index.html` judged a page for a fact it never
# carries. Both rows now say which document the probe reads and where that document's shape was
# checked: `tekton`/`id="root"` against dashboard v0.61.0, which is what `TEKTON_VERSION_DASHBOARD`
# installs, and `grafanaBootData`/`reactRoot` re-read against Grafana v13.3.0 on 2026-10-30, which
# is what the pinned chart 14.2.0 ships. That re-read is a source read and not a lane result: both
# literals are still in Grafana's own `public/views/index.html`, the Go template the served `/`
# document is generated from (`<div id="reactRoot"></div>`, `window.grafanaBootData = {`), so
# nothing in the upgrade removes a token this row depends on. The last mile - what those versions
# serve over a real port-forward - is only a lane can say.
SIGNATURES = {
    "tunnel.grafana": {
        "read": "the SPA boot document behind grafana's 302 on /",
        "path": "/",
        "tokens": ["grafanaBootData", "reactRoot"],
    },
    "tunnel.tekton": {
        "read": "the dashboard's own index document, deliberately not its JS bundle",
        "path": "/",
        "tokens": ["tekton", 'id="root"'],
    },
}

# ------------------------------------------------------------- self-audit
# The requirements' own greps, run by the suite before it touches a VM. Patterns
# are written as regexes so the pattern table itself does not trip them.
SCRIPT_FLAG = "--" + "script"

FORBIDDEN_PATTERNS = [
    ("no-hardcoded-storage-class", r"\bhostpath\b"),
    ("no-beta-tekton-api", r"tekton\.dev/v1beta1"),
    # Real credentials only. OSS release tarballs served from github are not
    # credentials, so the ssh-remote/host forms are what this greps for.
    ("no-real-credentials", r"[Jj]ulianweberdev|gitlab\.com|github\.com:|github\.com/[A-Za-z0-9_-]+/kubementat"),
    # A docker.io URL that carries a personal namespace would be a real credential
    # context; the bare registry and the official library images are not.
    ("no-personal-registry-namespace", r"docker\.io/(?!library/)[A-Za-z0-9_-]+/"),
    ("no-interactive-prompts", r"read\s+-p"),
    ("no-context-gate", r"config\s+current-context"),
    ("ssh-literal-outside-primitive", r"ssh\s+-i\s"),
]

# ------------------------------------------------------------------- redaction

# Fixed key list; values are masked wherever they appear in captured output.
REDACT_KEYS = [
    "GRAFANA_ADMIN_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "MYSQL_PASSWORD",
    "REDIS_PASSWORD",
    "GIT_DEPLOYER_PRIVATE_KEY_BASE64",
    "GIT_DEPLOYER_GPG_PRIVATE_KEY_BASE64",
    "password",
    "token",
    "private_key",
]

# ------------------------------------------------------------- known blockers
# Product findings the suite is *expected* to trip over. Matching one turns a
# stage failure into a named blocker instead of an anonymous harness bug.
# The suite never works around them; it reports them.
KNOWN_BLOCKERS = []
# Nothing is expected to fail any more. The last row here blamed `linkerd (uninstall|viz)`, which
# was a description of a product gap that stopped existing: kubementat does not install Linkerd,
# and a lane that trips over that message now sees a real regression rather than a documented one.
# The grafana row above it is gone for the same reason -- the credential check says so out loud.

# Assertion classes (NFR 6.6). Only these infra predicates may retry, once.
RETRYABLE_INFRA_PATTERNS = [
    "lease-timeout",
    "ssh-timeout",
    "image-download-failed",
    "image-verification-failed",
    "connection refused",
    "temporary failure in name resolution",
    # A mutating command that lands while a newly installed webhook is still warming
    # up gets a 500 from the API. That is cluster readiness, not product logic. The
    # retry re-runs the same command once, which is only acceptable because install
    # is required to be idempotent - stage 04 relies on that same property.
    "no endpoints available for service",
]

# ------------------------------------------------------------------- debug dump
# What gets appended to the artifact dir on failure (NFR 6.5). Never stdout.
FAILURE_DUMP = [
    "kubectl get pods -A -o wide",
    "kubectl get pvc -A",
    "helm list -A",
    "kubectl get events --all-namespaces --sort-by=.lastTimestamp | tail -n 50",
]

# Files whose host-side content must be byte-identical after a run.
INTEGRITY_FILES = [
    "deployer_ssh_key",
    "deployer_ssh_key.pub",
    "git_crypt_symmetric.key",
    "gpg_private.key",
    "gpg_public_key.gpg",
]
