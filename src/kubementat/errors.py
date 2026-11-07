"""What kubementat refuses with.

A refusal is an object and not a stack trace, so the shape of what a command raises is product: the
message that names what is missing and says what to do about it is what an operator reads, and a bare
traceback is what they do not. This module is the one place that answers
"kubementat can refuse me with what" without opening the thirty-odd modules that raise things.

An error's own prose — the reason the refusal exists and the question it answers — belongs to the
error and travels with it here. A class that only restates the name is a comment nobody reads twice,
and the modules that raise these objects still say, at the raise, what that particular call site
found wrong.

`KubementatError` is what every domain error descends from, so that one catch can mean "a kubementat
refusal" instead of meaning one class at a time. It carries no behaviour on purpose: no message
convention, no exit code, no payload. A base class that carried behaviour would let a subclass that
forgot to carry it work by accident, and an accident with two owners is a bug nobody can find.
"""

from pathlib import Path
from typing import Optional, Union


class KubementatError(Exception):
    """A refusal kubementat means, as opposed to a Python error that escaped.

    Nothing more than the name, which is the whole job: a caller that wants to turn any domain
    refusal into a single clean answer needs one type to catch and not a list of them.
    """


class TeamNotSuppliedError(KubementatError):
    '''
    This work needs a team's configuration, and the run did not name one.

    Not a resolution failure: the cluster resolved, and a `Context` built without a team still
    carries it. What is missing is the other half of the pair — `platform_config/{environment}/<team>/`
    — and the rule is that kmt does not guess it, so the object says so instead of letting a
    service read a path built out of a name nobody gave it. Two calls raise it, for the same
    reason: `Config` when a team-scoped value is read from a `Config` that has no team, and
    `kubementat.core.identity.context.Context.require_team` when a service asks a Context for the half
    it was not given. One question, one sentence, and it lives here rather than in the module that
    builds the path the refusal is about, because that refusal outlives where the path happens to be
    assembled: the cluster layer asks it too, and neither module owns an answer that belongs to the
    pair.
    '''


# ---------------------------------------------------------------------------
# The cluster refusals.
#
# These seven answered "which cluster does this command mean" from inside
# `kubementat.cluster`. They travel here because that question is asked from the CLI, the check,
# and every service that touches a kubeconfig, and because the two the operator can feel —
# `ClusterResolutionError` and `ClusterIdentityCollisionError` — are converted into one `Error:`
# line and exit 1 by `cli/context.py`, which is the only place that decides how they are printed.
# The rest of the group is what those two wrap or refuse before a socket opens.
#
# The shape is deliberate: `ClusterResolutionError` is the middle base the five resolution failures
# descend from, so `except ClusterResolutionError` still means "no cluster was named" and nothing
# wider; `ClusterIdentityCollisionError` hangs off `KubementatError` beside it, because a caller
# that carries on past a resolution failure must not carry on past a collision.
# ---------------------------------------------------------------------------


class ClusterResolutionError(KubementatError):
    """The cluster a command asked for could not be named.

    Carries a `reason` as well as the message: the message is the text an operator acts on, which
    names the environment, and the reason is the same fact without the environment's name,
    which is what a report line that already named it needs.
    """

    def __init__(self, message: str, reason: Optional[str] = None):
        super().__init__(message)
        self.reason = reason or message


class ClusterNotConfiguredError(ClusterResolutionError):
    """Nothing in the resolution order declared this environment's cluster, and no fallback named one."""


class ClusterContextNotFoundError(ClusterResolutionError):
    """The declared context is not in the kubeconfig that was resolved.

    The exception is where the available contexts are listed, because that list is the answer to
    the mistake that raised it — `prod-eu` typed for `prod-europe`.
    """

    def __init__(self, message, reason=None, available=()):
        super().__init__(message, reason)
        self.available = tuple(available)


class ClusterFileUnreadableError(ClusterResolutionError):
    """A file the resolution was told to read is missing, unparseable, or says nothing.

    This is the failure class the 1:1 check reports as *not verified*: an environment refused
    because of a file that cannot be read is an environment that is reported, not dropped.
    """


class ServiceAccountNotMountedError(ClusterResolutionError):
    """In-cluster resolution was asked for where no service account is mounted.

    It is a `ClusterResolutionError`, not an `OSError`, for a reason worth stating: the mounted
    material is part of resolving which cluster a command means, and the fact that failed is a fact about
    *configuration* — this process is not running inside a cluster — not about a path somebody
    typed wrong. `FileNotFoundError` from the kubelet's own directory is the same sentence with no
    verb in it, and it names a file the operator cannot create.

    Its `reason` is what the last layer folds into the not-configured error when nothing above it
    declared a context either, so a refusal never claims a cluster exists and then dies on the read.
    """


class StatedClusterMismatchError(ClusterResolutionError):
    """What a run states about its cluster and what the environment declares are two claims.

    A Tekton pod answers "which cluster" twice. The cloned configuration declares one answer in
    `static.json`, and the TaskRun states another through the flags and variables above the file,
    and a precedence rule alone would take the statement and never look at the declaration. The two
    claims answer different questions: the file's claim is about **which environment** the work is
    for, the run's claim is about **where the work runs**, and when the two disagree kubementat is
    not the thing that gets to decide.

    It is a `ClusterResolutionError` and not a sibling of `ClusterIdentityCollisionError` for one
    reason: the collision is found *after* a cluster resolved, while this refusal is raised instead
    of resolving. Nothing above the file gets to be a hint, so the run that is refused resolved no
    cluster and touched no kubeconfig.

    The message carries both claims and the source that wrote each one, because the mistake is
    always in one of them and the reader has to be able to tell which. There is no override flag:
    a dial that lets a pipeline walk past this is a dial that lets it install one environment's
    platform into another environment's cluster.
    """

    def __init__(self, message: str, reason: Optional[str] = None, *, stated: str = "",
                 declared: str = ""):
        super().__init__(message, reason)
        self.stated = stated
        self.declared = declared


class ClusterIdentityCollisionError(KubementatError):
    """Two environments address the same cluster, which v2 refuses rather than accepts.

    It is not a `ClusterResolutionError`: both environments resolved perfectly well, and a
    caller that catches resolution failures and carries on would carry on into a cluster that
    belongs to someone else. The refusal is given no override flag, so there is nothing here to
    pass.
    """


# ---------------------------------------------------------------------------
# The refusals that name a thing which is not there.
#
# Four of these five are one sentence told about five different objects: the namespace, the pod, the
# service account's token, or the name a document should have carried is not on the cluster, and the
# refusal says so instead of letting the next line fail on it. They were raised from three different
# modules, and they travel here together because a caller cannot care which module a refusal arrived
# from — the sentence it has to answer is the same sentence either way, and none of the five is a
# fact about the caller's own plumbing.
#
# `CommandExecutionError` is in the group for one reason and not for company: when the program a
# command tries to start is not on the PATH, that is the same absence with a return code attached to
# it. Its payload is the argv and the exit rather than a name, which is why it carries those two
# fields.
#
# They share no family base of their own, and that is a different thing from sharing no base at all:
# each descends from `KubementatError`, because the promise that base makes is "one catch means a
# kubementat refusal", and a missing namespace is exactly such a refusal. A base that some domain
# errors decline to join is a base that lies to whoever writes `except KubementatError`, and it lies
# in the direction of the traceback rather than the answer. Nothing in the tree catches that base
# today, so joining it changes no behaviour; it is the consistency that makes it worth having.
# ---------------------------------------------------------------------------


class PodNotFoundError(KubementatError):
    """No pod matched the query, i.e. the component is not running on this cluster.

    "Component not installed" is an ordinary state, not a programming error, so it is
    reported as a message the operator can act on rather than as an IndexError.
    """


class ServiceAccountTokenNotFoundError(KubementatError):
    """A service account has no token secret to read.

    Clusters since 1.24 do not mint a token for a service account on its own, so the
    secret has to be requested once. This error names the command that does it.
    """


class NamespaceNotFoundError(KubementatError):
    """The namespace named on the command line is not on this cluster.

    Reading a namespace that was never created is an operator typo or a misremembered
    install, not a programming error, so it is a message that names the namespace rather
    than an ApiException whose status code means nothing without `kubectl explain`.
    """


class ResourceWithoutNameError(KubementatError):
    """A document that was supposed to be applied cannot be addressed.

    The alternative was a `KeyError` from inside the log line that prints the resource's name,
    which is the wrong diagnosis at the wrong address: it reads as kubementat's bug, not as a
    manifest that is missing the one field every Kubernetes object is identified by. Named here
    instead so a caller (and `setup_triggers`, which reuses this primitive) can decide whether
    one bad file should stop the whole directory.
    """


class CommandExecutionError(KubementatError):
    """A command exited non-zero, or could not be started.

    ``argv`` and ``returncode`` stay on the exception so callers can decide what to do
    without parsing the message.
    """

    def __init__(self, result, message=None):
        self.result = result
        self.argv = list(result.argv)
        self.returncode = result.returncode
        super().__init__(message or self._default_message())

    def _default_message(self):
        detail = (self.result.stderr or "").strip()
        lines = [
            f"Command failed with exit code {self.returncode}: {self.result.argv_display}",
        ]
        if detail:
            # Keep the operator's console readable; the whole log is where the noise lives.
            tail = detail if len(detail) <= 2000 else detail[-2000:]
            lines.append(f"stderr: {tail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The grafana refusals.
#
# Three answers to three different questions: the request went wrong (GrafanaError, and its status),
# the credential was refused (GrafanaUnauthorizedError), or the configuration could not express the
# call at all so nothing was sent (GrafanaConfigurationError). They come from
# `infrastructure/grafana/client.py`, and from `infrastructure/grafana/accounts.py` for the
# configuration half, because the two files are one client
# split by whether it needs the cluster.
#
# The shape is the tree's own: `GrafanaError` is the family base, carries the status code, and
# descends from `KubementatError` like everything else here; its two children say which kind of
# refusal it was, so `except GrafanaError` still catches all three and `except
# GrafanaConfigurationError` still means "edit a file, not a password".
# ---------------------------------------------------------------------------


class GrafanaError(KubementatError):
    """Something grafana did not accept, or did not answer.

    Carries the status code when there was one. A request that never got a response carries
    `status_code=None`, because "no connection" is a different fact from "403" and the advice that
    follows each is different.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GrafanaConfigurationError(GrafanaError):
    """The configuration cannot express this operation, so no request is sent.

    Raised for a missing credential key and for a missing address. Both are file-and-editor
    problems, not cluster problems, which is why they carry their own type: a caller that wants to
    say "open `static.encrypted.json`" needs to be able to tell them apart from a 401.
    """


class GrafanaUnauthorizedError(GrafanaError):
    """Grafana rejected the credential. Not the same as a wrong namespace or a closed port."""


# ---------------------------------------------------------------------------
# What the Ingress generator refuses to write.
#
# All three belong to one command's argument list, and all three used to be `Exception` with no
# parent. A caller that asks "did kubementat refuse me, or did Python break" cannot tell with that,
# which is the whole content of this group's edit: the classes, their messages, and their reasons are
# unchanged, and each now answers to `KubementatError`.
#
# `IngressTargetExistsError` is the overwrite rule, and the rule is `automations/ingress.py`'s:
# kubementat does not replace what it has already handed the operator. The other two are the
# argument checks the bash
# script made by passing `$1`-`$3` unquoted, where a missing argument and an empty one looked the same
# and a blank host applies and routes nothing.
# ---------------------------------------------------------------------------


class IngressTargetExistsError(KubementatError):
    """Generated output is already where this generator was asked to write it."""


class IngressArgumentNotSuppliedError(KubementatError):
    """One of the three arguments this command requires came in empty.

    bash checked all three because a missing `$1` and an empty `$1` looked the same to it, and a
    manifest with a blank host applies and routes nothing. Click refuses an absent argument by
    itself; this is the empty-string half, and it names the argument rather than printing the
    whole usage block.
    """


class IngressPortNotUsableError(KubementatError):
    """The port is not a number, so the manifest would name a port that does not exist.

    bash interpolated `$3` straight into `number:`, which meant `80` became a number and `http`
    became a string that the API server rejects. Refusing is the same answer with the failure
    moved before the apply, and it names the argument it refused.
    """


# ---------------------------------------------------------------------------
# A key the configuration was asked for and did not answer.
#
# The same refusal told about two different files, and it lives here rather than in either of them
# because the caller that has to print the advice is not the module that read the key. Each names the
# key, names the file it belongs in, and refuses to replace the missing answer with a plausible
# default — which is the whole point of both: a guessed registry reference gets pulled on somebody
# else's cluster, and a guessed database address opens a tunnel to a database nobody meant to reach.
# ---------------------------------------------------------------------------


class CiImageNotConfiguredError(KubementatError):
    '''
    The CI image cannot be named, which is now one of two different facts.

    `TEKTON_CI_IMAGE_NAME` and `TEKTON_CI_IMAGE_TAG` carry an L0 now — the same two values the
    shipped template carried, in `constants.DEFAULT_STATIC_ANSWERS` — so a tree that declares nothing
    runs `ubuntu-ci-minimal:latest` and never reaches this exception. What still raises is:

    * a key that was **declared and answers nothing** (`""`, `null`, a number). jq used to print the
      four characters `null` into the image reference and `kubectl run` accepted it, so a value that
      was written down and says nothing is refused rather than papered over with a default. That is
      That is the override rule cutting both ways: the operator's file outranks `constants.py`, and
      when it answers nothing it answers nothing.
    * `DOCKER_REGISTRY_BASE_URL`, which has **no L0** and was left out of the shipped defaults. It
      is reported and left alone.

    One key of three is a whole answer either way, and the failure this exception replaces was a pull
    error on somebody else's cluster instead of a missing key in a file.

    The sentence names three things and no more, because that is the done-when a refusal is held
    to: the key that is missing, the file it belongs in, and the verb that answers it. `routing._cluster_manager_email`
    has always ended this way; this is that shape, on the one key of the row that has no L0 and a
    live consumer.
    '''

    def __init__(self, missing_keys, config_path):
        self.missing_keys = list(missing_keys)
        self.config_path = config_path
        super().__init__(
            f"{', '.join(self.missing_keys)} is not answered in {self.config_path}, so kubementat "
            f"cannot name the CI image to run. A key nobody wrote is not what is missing: the name and"
            f" the tag have kubementat's own default, `ubuntu-ci-minimal:latest`. So either the key"
            f" was declared and says nothing — set it to an image this cluster can pull, or delete the"
            f" entry and let L0 answer — or DOCKER_REGISTRY_BASE_URL is unset, which kubementat does"
            f" not default at all. Put the registry in that file: kmt config set <dir>"
            f" <environment> DOCKER_REGISTRY_BASE_URL=<registry-url> is the verb, and nothing"
            f" rewrites an answer that is there."
        )


class MysqlNotConfiguredError(KubementatError):
    """A key the MySQL tunnel cannot guess is missing from the team's configuration.

    kubementat does not install MySQL any more (owner decision 2026-10-27, the Bitnami image
    that made the chart pullable disappeared), so there is no release the tooling can
    interrogate to find a database out. Guessing is the dangerous answer here: a namespace
    that happens to hold somebody else's MySQL would open a tunnel that reads and writes a
    database nobody meant to reach, so a key that is not there has to stop the command rather
    than be replaced by a plausible default. Named in the message is the key and the file it
    belongs to, so the operator has something to edit.
    """


# ---------------------------------------------------------------------------
# What the configuration tree itself refuses.
#
# These three refused from inside the modules that read or prepare the tree, each one descending
# from `click.ClickException` — the shape that says "a command printed this" rather than
# "kubementat refused", and the only gap left open by `cli/context.py`'s doctrine (the library
# stays Click-free to raise; only the CLI decides how a refusal is printed). They now descend from
# `KubementatError` like every other refusal, and the CLI renders them: the same one `Error:` line
# and exit 1 they got for free as `ClickException`s, produced where they are caught and re-raised
# rather than where they are raised. The prose below still says what each refusal means, because
# that is the part no raise site may rewrite locally.
# ---------------------------------------------------------------------------


class LockedConfigFileError(KubementatError):
    """A kubementat configuration file that is present but not readable — the git-crypt case.

    The reason this is a named refusal and not a codec error: by the time it is raised, the code
    that raised it has stopped understanding the bytes, and the operator's fix (`git-crypt
    unlock`) is not a code fix. The path is part of the message because a tree can hold four files
    with the same name.
    """

    # The half of the refusal that belongs to whoever owns the checkout, and not to kubementat.
    # Spelled the same way in every command that ever had to say it.
    UNLOCK_INSTRUCTION = (
        "This is the git-crypt case: the file is in the checkout and its contents are ciphertext, "
        "and kubementat reads a configuration it is only given — it never runs git-crypt unlock. "
        "Run `git-crypt unlock` in this checkout, or let the pipeline unlock after checkout, then "
        "ask again."
    )

    def __init__(self, path: Union[str, Path], why: Optional[str] = None) -> None:
        self.path = Path(path)
        self.why = why
        detail = f": {why}." if why else "."
        super().__init__(f"{self.path} is present and cannot be read as text{detail} "
                         f"{self.UNLOCK_INSTRUCTION}")


class DeployerKeyMissingError(KubementatError):
    """A configuration directory that has no deployer keypair yet.

    This is a named refusal rather than whatever the next layer would raise, because every site
    that raises it is one an operator will be standing in front of: `setup_pipelines` reads these
    files on its way to a Secret, and used to discover the absence three layers down inside the
    Kubernetes client's serialization. The name of the class is the sentence the operator needs,
    which is why the exception is what the guards propagate instead of a re-raise of something
    else.
    """


class ObsoleteTriggerDirectoryError(KubementatError):
    """A tree that still has the old trigger directory is refused, by name.

    The generated trigger configuration lived at `<team>/triggers/` until it moved under
    `tekton/`; a tree can hold both, and only the new one is read, so the old directory standing
    there is a set of manifests that are silently applied nowhere. Both points that would make
    that silent — the reader and the writer — refuse instead, and the message names what was
    found, what the new layout needs, and the verb that closes the gap.
    """


# ---------------------------------------------------------------------------
# What the Tekton install refuses to start with.
#
# Five answers from the Tekton install path -- raised now by whichever module of
# `infrastructure/tekton/` is asked -- and they used to be five bare `Exception`
# classes with no parent, which is the whole content of this group's edit: the classes, their
# messages and their reasons are unchanged, and each now answers to `KubementatError`, so a caller
# that asks "did kubementat refuse me, or did Python break" can tell.
#
# Three of the five are the `jq -r` shape, and it is worth naming once instead of three times: bash
# read a `static.json` key with `jq -r`, a key that was not there printed the four characters
# `null`, and the next line put that string into a URL (`TektonReleaseVersionNotConfiguredError`),
# into the namespace name eight RBAC objects were addressed to (`PipelineNamespaceNotConfiguredError`),
# or into the image reference of a CronJob that reported success and then failed on its schedule
# (`CleanupJobImageNotConfiguredError`). `kubectl` failed some way later, and the failure named
# neither the file nor the key. That is the sentence `CiImageNotConfiguredError` above already says
# about two of these very keys, told from the other side of the same file, and
# `CleanupJobImageNotConfiguredError` is deliberately not folded into it: same keys, two different
# images, and the advice an operator acts on is not the same advice.
#
# The other two are not about a key. `TektonControllerWithoutConfiguredApiError` refuses before a
# single manifest is sent, and it is refused in this group rather than the cluster group above
# because the question it answers is not *which cluster does this command mean* — that question
# resolved perfectly well, and then the cluster was refused for what it does not serve.
# `TektonWebhookNotReachableError` is a timing question about a Service name that is a constant in
# `infrastructure/tekton/install.py` and not something the operator picked; it is the answer the E2E
# suite was missing when an apply died on a webhook nobody had chosen.
# ---------------------------------------------------------------------------


class TektonControllerWithoutConfiguredApiError(KubementatError):
    """The cluster does not serve the `tekton.dev` API version kubementat speaks.

    This is raised before any resource is sent, so that a controller that is too old (or
    not installed at all) produces one explanation instead of a 404 for every Task,
    Pipeline and PipelineRun it would otherwise be asked to create.
    """


class PipelineNamespaceNotConfiguredError(KubementatError):
    """`PIPELINE_NAMESPACE` is not in the team's `static.json`.

    bash read it with `jq -r`, so an absent key answered `null` and every RBAC object went to a
    namespace with that literal name — where `kubectl` answered "not found", several times over,
    for a reason that named neither the file nor the key. Eight operations need one namespace,
    so this is asked for once, before any of them run.
    """


class TektonReleaseVersionNotConfiguredError(KubementatError):
    """A `TEKTON_VERSION_*` key the release URL is built from is not configured.

    bash read the same keys with `jq -r`, and a key that was not there printed `null` straight
    into the URL, so the install fetched a 404 and reported a non-zero exit. The missing thing
    is a line in `static.json`, so that is what the message names.
    """


class TektonWebhookNotReachableError(KubementatError):
    """The Pipelines webhook could not be reached before the next release was applied.

    The shape this replaces is the one the E2E suite caught: the apply of the *Triggers* release
    dies on `failed calling webhook "config.webhook.pipeline.tekton.dev": ... no endpoints
    available for service "tekton-pipelines-webhook"`, because the webhook that validates the
    Triggers objects was registered by the apply three seconds earlier and its pod is not yet
    serving. The failure lands in the middle of a list of `kubectl apply` calls, names a webhook
    the operator never chose, and points at a release that is not the one at fault.

    So the wait raises instead of letting that happen: it names the Service, the namespace, and
    the bound that expired, and it says which knob to turn. Nothing is half-applied to make this
    nicer to read -- the install stops where it is, and the message says so.
    """


class CleanupJobImageNotConfiguredError(KubementatError):
    """A `static.json` key the cleanup job's container image is built from is not configured.

    bash read these three keys with `jq -r`, so an absent one printed the four characters `null`
    into the image reference and the CronJob was created happily — with a pod that never started,
    because `ImagePullBackOff` is reported by the job and not by the apply that wrote it. The
    failure arrived days later, as an empty cronjob history.
    """


# ---------------------------------------------------------------------------
# What the Tekton generators refuse to write.
#
# The six classes the generators raise instead of writing a file — `generate_deploy_pipeline`,
# `generate_trigger_configs`, `generate_pipeline_run` — and, like the Ingress group above, all six
# used to be bare `Exception`s with no parent. The Ingress group is the same three questions about
# one command's three arguments: the target already exists, an argument came in empty, a value is
# unusable. This group is those three, plus what a template is, what it asked for, and what the
# input file turned out to be, told about the directory a generated pipeline lives in.
#
# No `GenerationError` base was added here, and the reason is the census and not the taste. The
# name would cover the six exactly, and it reads well. But nothing in the tree catches this family
# as a family: not one `except` in `src` or in `tests` names any of these eleven, and the CLI lets
# them travel untouched to the one place that prints a domain error. A base nobody catches is a
# promise about behaviour that does not exist, and each of the six already answers to
# `KubementatError`, so `except KubementatError` reaches all six today. The day a caller wants to
# say "the generators, and not the rest", that day adds the base, and adding it then is a one-line
# change with no call site to repoint.
# ---------------------------------------------------------------------------


class GenerationTemplateNotFoundError(KubementatError):
    """A generator template is not in the installed package.

    bash ran `cat` on a `../../../`-relative path, so the answer depended on which directory the
    operator happened to be standing in and the failure arrived as `sed: No such file or
    directory` — after which a redirect wrote an empty file and the command said it had finished.
    This is raised before anything is written, and it names the path that was read. The templates are
    package data now, so the honest reading of a miss is "this installation lost them",
    not "this checkout is incomplete".
    """


class GenerationPlaceholderNotConfiguredError(KubementatError):
    """A template asked for something kubementat has no value for.

    The rule this class exists to keep: an unsubstituted placeholder never reaches a manifest. bash
    substituted what it had and echoed what was left, so a typo in a template key left
    `TEAM_PLACEHOLDER-pipelines` in a namespace name that some cluster then happily created, and a
    `jq` miss left the word `null` standing in for an image tag.
    """


class GenerationArgumentNotSuppliedError(KubementatError):
    """A generator input that is not read from configuration was not supplied.

    The deploy pipeline's app-repository coordinates belong to the app, not to the platform, so no
    `static.json` key holds them and the script required them as exports. Requiring them is the
    honest shape of the same rule; the difference is that the answer says what to pass.
    """


class GenerationInputNotFoundError(KubementatError):
    """The file a generator was pointed at is not in this checkout.

    `generate_pipeline_run.sh` took the pipeline file as an argument and `cat`ed it after a
    `../../../` prefix, so the answer depended on which directory the operator stood in and a miss
    surfaced as `cat: No such file or directory` - which is easy to read as "the pipeline is empty"
    rather than "I read the wrong file". This is raised before anything is written, and it names the
    path it actually tried.
    """


class GenerationInputNotUsableError(KubementatError):
    """The input file is here, but it is not a Pipeline a run can be generated from.

    bash read `metadata.name` out of the file and used it for two things at once: the name of the
    run, and the `pipelineRef.name` the run starts. A file that declares no name therefore wrote a
    resource called `null-run` that pointed at a pipeline called `null`, and a file that is not a
    Pipeline at all - a Task, or the multi-document example files under `examples/` - wrote a run of
    something that cannot run. Both were silent. Both are refused here, with the file named.
    """


class GenerationTargetExistsError(KubementatError):
    """Generated output is already where this generator would write it.

    The two generators that write deploy pipelines and their runs overwrite, and they were right to:
    what those two write is a pure function of
    a tracked template and the configuration, so rerunning is the review loop. A generated pipeline
    *run* is not that. Its parameters are written as placeholders for whoever generated it to fill
    in, so a rerun over a file a team had already adjusted would delete their work without a word,
    and the script's `check_target_file_exists` is the only thing that stopped it. That refusal is
    kept here, and it is the one part of the port that is deliberately stricter than its siblings.
    """


# ---------------------------------------------------------------------------
# The refusal that says the work is not finished.
#
# `UninstallIncompleteError` is not about a key or an argument: the command ran, and something
# kubementat owned is still on the cluster. It is here because the operator hears it from
# `kmt uninstall`, which is the CLI's sentence and not the library's, and because what the message
# carries is a list of what is still there — the thing to act on — rather than a claim that
# something failed.
# ---------------------------------------------------------------------------


class UninstallIncompleteError(KubementatError):
    """Something kubementat owned survived `kmt uninstall`.

    The alternative is to log a warning and print `DONE`, which is precisely the failure this
    command exists to make impossible: a real lane said DONE while two helm releases were
    still running, and the residue assert had to be written against the cluster because nobody
    in the tooling could be believed. Named in the message is what is still there, so the
    operator has something to act on instead of a claim that the exit code was 0.
    """
