"""The names the package agrees on when a resource module would otherwise state them twice.

A constant that two areas read is a rule, not a value: the next change to it has to land in both
places or one of them is quietly describing the cluster the other one stopped addressing. This
file is where each of those rules is stated once, so that both sides and the callers above the
package read the same one.

Nothing here is used by a single area. A name with one reader travels with the module that reads
it, because a shared home for it is one more indirection between a reader and the sentence it is
reading.
"""

# A Secret that carries a service account's credentials is recognised by its type plus the
# annotation naming the service account it belongs to. Both halves are needed: the type
# alone cannot say whose token it is, and since 1.24 nothing exists without that
# annotation. Clusters older than 1.24 put the same annotation on the token secret the
# controller created automatically, so one rule finds both.
#
# The two sit together here because they are read as a pair and by two areas: the Secret that
# requests a token is written with both, and the read that finds a token matches on both.
SERVICE_ACCOUNT_TOKEN_SECRET_TYPE = "kubernetes.io/service-account-token"
SERVICE_ACCOUNT_NAME_ANNOTATION = "kubernetes.io/service-account.name"

# How kubementat addresses a deployment's pods, wherever a selector is needed (the same
# label stream_logs documents): the helm release name. Reading a deployment by anything
# else finds the pods a manual `kubectl label` left behind.
#
# The selector is built by the session area, by the report that reads a deployment's pods, and
# by the tunnels that address a component's deployment. It is one rule with more than one
# author, which is exactly the shape of the failure this file exists to prevent: the copy that
# is not updated is the one that keeps compiling.
DEPLOYMENT_LABEL = "app.kubernetes.io/instance"

# kubementat's claim on an object, in two parts because they are one fact and reading one of them
# from the air is how a sweep deletes somebody else's work. Every writer in this product sends the
# pair (`infrastructure/tekton/install.py`'s `helm_deployer_cluster_role`,
# `infrastructure/tekton/triggers.py`'s `_trigger_labels`,
# `infrastructure/tekton/runs.py`'s `CLEANUP_JOB_LABELS`, the role bindings in
# `automations/install/pipelines.py`'s `_setup_pipelines_setup_role_bindings`,
# the Secrets in `cli/commands/kubernetes.py`), and this module is where the pair is said to mean
# what it means: `create_or_replace_secret` documents that an unlabelled object is
# indistinguishable from one the operator wrote.
#
# The uninstall reads it back in the other direction -- see
# `delete_cluster_scoped_rbac_kubementat_owns` in `automations/uninstall/residue.py` -- which is the
# other half of the same
# claim: if a teardown is allowed to identify what it owns by a label, the label has to be written
# by the code that owns the object and not guessed at read time.
MANAGED_BY_LABEL = "managed-by"
MANAGED_BY_KUBEMENTAT = "kubementat"
