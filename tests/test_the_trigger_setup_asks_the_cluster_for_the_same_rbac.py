"""Parity: `TektonUtils.setup_triggers()` and the 269-line script that used to do it.

The bash half here is a **record**, not a run: `setup_triggers.sh` is deleted by this commit, and
what it asked the cluster for is written down below, in the heredocs it piped into `kubectl apply
-f -`. `git show b551def:scripts/automation/tekton/setup_triggers.sh` still finds the blob, and the
Python half is executed for real against the recording client, so this is still a comparison and
not a claim about itself.

The record is reduced by the same reducer the live comparisons use, from the same shape the shims
would have logged, so a change to the record has to be a change to what the script said.

The comparison is over cluster operations, which is what the reducer reduces, and that is also how
the two halves that are *not* cluster operations stay out: the script's `ls` of the old generated
location (an earlier decision moved it to `platform_config/{env}/{team}/triggers/`, and a later
move took it
under `tekton/`) and its `jq` of the two webhook keys are reads of the filesystem, made through
a binary the Python replacement does not call. Where they do show up is in what each side
concludes, so they are asserted that way — in
`test_an_unconfigured_token_writes_no_secret_on_either_side` and in
`test_the_generated_manifests_are_read_from_platform_config_and_not_the_checkout`, which is a unit
test for exactly that reason.
"""

from conftest import FIXTURE_ENVIRONMENT, RecordedCall
from cluster_parity import compare, operations_from_calls
from kubementat.tekton_utils import WEBHOOK_SECRETS
from cluster_fixture import configuration_only_cluster, configuration_only_context

# The cluster both sides address, for the same reason the cleanup-job comparison spells it out:
# the Python side gets it from its `Context`, and the record of the script's heredocs is declared
# with it because a script addressed its cluster through the environment and carried nothing in its
# argv for the reducer to read.
CLUSTER = configuration_only_cluster(FIXTURE_ENVIRONMENT)

NAMESPACE = "fixture-pipelines"
TEAM = "fixture"

# Every document the deleted script sent, as it sent it: a `kubectl apply -f -` whose stdin was a
# heredoc, in the order the script ran them. Namespaced applies carried `-n "$PIPELINE_NAMESPACE"`;
# the ClusterRole and the ClusterRoleBinding did not, because they have no namespace.
RBAC_HEREDOCS = [
    (None, """kind: ClusterRole
apiVersion: rbac.authorization.k8s.io/v1
metadata:
  name: tekton-triggers-bindings-and-interceptors-clusterrole
  labels:
    managed-by: kubementat
rules:
- apiGroups: ["triggers.tekton.dev"]
  resources: ["clustertriggerbindings", "clusterinterceptors", "interceptors"]
  verbs: ["get", "list", "watch"]
"""),
    (NAMESPACE, """apiVersion: v1
kind: ServiceAccount
metadata:
  name: tekton-triggers-sa
  labels:
    managed-by: kubementat
    team: fixture
"""),
    (NAMESPACE, """apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tekton-triggers-role
  labels:
    managed-by: kubementat
    team: fixture
rules:
- apiGroups: ["triggers.tekton.dev"]
  resources: ["eventlisteners", "triggerbindings", "triggertemplates", "triggers"]
  verbs: ["get", "list", "watch"]
- apiGroups: [""]
  resources: ["configmaps"]
  verbs: ["get", "list", "watch"]
- apiGroups: ["tekton.dev"]
  resources: ["pipelineruns", "pipelineresources", "taskruns"]
  verbs: ["create"]
- apiGroups: [""]
  resources: ["serviceaccounts"]
  verbs: ["impersonate"]
- apiGroups: ["policy"]
  resources: ["podsecuritypolicies"]
  resourceNames: ["tekton-triggers"]
  verbs: ["use"]
- apiGroups: [""]
  resources: ["events"]
  verbs: ["create", "patch"]
"""),
    (NAMESPACE, """apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tekton-triggers-sa-to-role-binding
  labels:
    managed-by: kubementat
    team: fixture
subjects:
- kind: ServiceAccount
  name: tekton-triggers-sa
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tekton-triggers-role
"""),
    (None, """apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: tekton-triggers-sa-to-tekton-triggers-bindings-and-interceptors-clusterrole-binding-fixture
  labels:
    managed-by: kubementat
    team: fixture
subjects:
- kind: ServiceAccount
  name: tekton-triggers-sa
  namespace: fixture-pipelines
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: tekton-triggers-bindings-and-interceptors-clusterrole
"""),
    (NAMESPACE, """apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tekton-triggers-createwebhook-role
  labels:
    managed-by: kubementat
    team: fixture
rules:
- apiGroups:
  - ""
  resources:
  - secrets
  verbs:
  - get
  - list
  - create
  - update
  - delete
- apiGroups:
  - triggers.tekton.dev
  resources:
  - eventlisteners
  verbs:
  - get
  - list
  - create
  - update
  - delete
- apiGroups:
  - extensions
  resources:
  - ingresses
  verbs:
  - create
  - get
  - list
  - delete
  - update
"""),
    (NAMESPACE, """apiVersion: v1
kind: ServiceAccount
metadata:
  name: tekton-triggers-createwebhook-sa
  labels:
    managed-by: kubementat
    team: fixture
"""),
    (NAMESPACE, """apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tekton-triggers-createwebhook-rolebinding
  labels:
    managed-by: kubementat
    team: fixture
subjects:
  - kind: ServiceAccount
    name: tekton-triggers-createwebhook-sa
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tekton-triggers-createwebhook-role
"""),
]

# The two webhook Secrets, as the heredocs wrote them: an Opaque Secret whose only data key is
# `secretToken`, holding `base64` of whatever `static.encrypted.json` had.
def webhook_secret_document(name, token):
    from base64 import b64encode

    return f"""apiVersion: v1
kind: Secret
metadata:
  name: {name}
  labels:
    managed-by: kubementat
    team: {TEAM}
type: Opaque
data:
  secretToken: |
    {b64encode(token.encode()).decode()}
"""


def the_script_asks(tokens=()):
    """What the deleted script asked, reduced the way the shims would have reduced it.

    `tokens` is what `static.encrypted.json` answered for the two webhook keys — only the providers
    named in it get a Secret, which is the branch both sides have to agree on.
    """
    calls = [RecordedCall("kubectl",
                          ["apply"] + (["-n", namespace] if namespace else []) + ["-f", "-"],
                          cwd="/", stdin=document)
             for namespace, document in RBAC_HEREDOCS]
    for secret_name, key in WEBHOOK_SECRETS:
        if secret_name in tokens:
            calls.append(RecordedCall("kubectl", ["apply", "-n", NAMESPACE, "-f", "-"], cwd="/",
                                      stdin=webhook_secret_document(secret_name, tokens[secret_name])))
    return operations_from_calls(calls, cluster=CLUSTER)


def the_python_setup(fixture_repo, tokens=None):
    """`setup_triggers()` against the recording client, with the fixture's configuration.

    `tokens` is what the team's `static.encrypted.json` answers for the two webhook keys, because
    the webhook half is judged by which Secrets arrive and what is in them. None leaves the
    fixture's generated passwords in place.
    """
    from kubementat.tekton_utils import TektonUtils

    if tokens is not None:
        fixture_repo.team_static_encrypted(**tokens)
    TektonUtils(configuration_only_context(fixture_repo.environment, fixture_repo.team)).setup_triggers()
    return None


def compare_the_two(fixture_repo, recorded, tokens=None):
    """One comparison, run against the record above and the operations just written."""
    the_python_setup(fixture_repo, tokens)
    compare(the_script_asks(tokens or {}), recorded.operations(),
            context="what setup_triggers.sh asked the cluster, against what "
                    "TektonUtils.setup_triggers() asks it now")


def test_the_rbac_the_python_writes_is_the_rbac_the_heredocs_wrote(fixture_repo, dry_run_env,
                                                                   recorded_api):
    """Eight objects, same kinds, same namespaces, same rules, same order.

    The order is compared because the objects depend on each other: the ClusterRole before the
    binding that names it, the Role and its ServiceAccount before the RoleBinding that binds the
    two of them.
    """
    compare_the_two(fixture_repo, recorded_api, tokens={})


def test_a_configured_webhook_token_becomes_the_same_secret_either_way(
        fixture_repo, dry_run_env, recorded_api):
    """Both providers configured, so both Secrets are in the comparison.

    What is compared is the type and the data keys, which is everything a Secret is: the value is
    a base64 of the same token either way, and the key both sides write is `secretToken`.
    """
    tokens = {"gitlab-trigger-webhook-secret": "gitlab-token",
              "github-trigger-webhook-secret": "github-token"}
    encrypted = {"GITLAB_WEBHOOK_SECRET": "gitlab-token", "GITHUB_WEBHOOK_SECRET": "github-token"}
    the_python_setup(fixture_repo, encrypted)

    compare(the_script_asks(tokens), recorded_api.operations(),
            context="the webhook Secret both implementations write")

    written = [op for op in recorded_api.operations() if op.kind == "secret"]
    assert sorted(op.name for op in written) == sorted(tokens), written
    for operation in written:
        facts = dict(operation.facts)
        assert facts["type"] == "Opaque" and facts["data"] == ("secretToken",)


def test_an_unconfigured_token_writes_no_secret_on_either_side(fixture_repo, dry_run_env,
                                                               recorded_api):
    """One provider in use, and the record's heredoc for the other one was conditional too.

    bash guarded it with `if [[ "$GITLAB_WEBHOOK_SECRET" != "" ]]`, which `jq -r` made nearly
    meaningless (an absent key answered `null`, not the empty string, so the Secret was written
    anyway). The Python side guards it by checking what the key actually holds. That is a
    deliberate difference and it is declared here rather than hidden: with both keys present the
    two sides agree exactly, and this is where the disagreement lives.
    """
    the_python_setup(fixture_repo, {"GITLAB_WEBHOOK_SECRET": "gitlab-token",
                                    "GITHUB_WEBHOOK_SECRET": ""})

    written = sorted(op.name for op in recorded_api.operations() if op.kind == "secret")
    assert written == ["gitlab-trigger-webhook-secret"], written


def test_the_two_writes_are_idempotent_and_the_record_only_had_to_be_run_once(
        fixture_repo, dry_run_env, recorded_api):
    """`kubectl apply` is create-or-update, and so is every call the replacement makes.

    The second run is what a real install hits — the objects already exist — and the record cannot
    show it, because running the script twice is not a thing this harness can do against a fake
    cluster. What the Python side does about it is: same operations, no errors, nothing skipped.
    """
    the_python_setup(fixture_repo)
    first = recorded_api.operations()
    recorded_api.calls.clear()

    the_python_setup(fixture_repo)
    second = recorded_api.operations()

    assert [op.compare_key().__dict__ for op in first] == \
           [op.compare_key().__dict__ for op in second], \
        "the second run is not the same run"
