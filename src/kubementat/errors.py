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
    carries it. What is missing is the other half of the pair — `<root>/{environment}/<team>/`
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
    1.x template carried, now in `constants.DEFAULT_STATIC_ANSWERS` — so a tree that declares nothing
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
            f" not default at all. Put the registry in that file: kmt config set <environment>"
            f" DOCKER_REGISTRY_BASE_URL=<registry-url> --config-dir <configuration-root> is the verb,"
            f" and nothing"
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
# These refused from inside the modules that read or prepare the tree, and they used to descend
# from `click.ClickException` — the shape that says "a command printed this" rather than
# "kubementat refused", and the gap `cli/context.py`'s doctrine cannot accept (the library stays
# Click-free to raise; only the CLI decides how a refusal is printed). They all descend from
# `KubementatError` now, and the CLI renders them: the same one `Error:` line and exit 1 they got
# for free as `ClickException`s, produced where they are caught and re-raised rather than where
# they are raised. The prose below still says what each refusal means, because that is the part no
# raise site may rewrite locally.
#
# The grouping is deliberate and it is not by file: `core/config/` raised fifteen of these, and
# they are named here by *the question the operator has to answer*, not by which module was asked.
# A tree with no root, a tree stamped twice behind, a team that named no namespace — those are the
# fifteen, and a class per raise site would restate fifteen messages in fifteen places. Where two
# sites raise the same sentence-shaped refusal they raise one class, and where a refusal has a
# sibling that reads differently (`config.py`'s two "no layer file here" doors, `get` and
# `declared`) the two stay one class because the operator cannot tell them apart either.
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


class ConfigurationRootNotNamedError(KubementatError):
    """Nothing named the configuration root, so there is no tree to read.

    A configuration root is *named, never located*: the `--config-dir` flag or the variable a
    container already sets. This is that refusal, and it is the one place in the library that says
    it — `core/config/paths.default_platform_config_dir`, which is what every reader falls back to.
    The deleted fallback (the default that sat beside wherever the wheel was installed) answered every
    command that had been told nothing, from a tree nobody pointed at; that is the D11 bug in its
    last remaining form, which is why the answer is one line naming the two spellings and not a
    guess. The same sentence reaches `resolve_platform_config_dir`, so a `--config-dir` that was
    never passed and a library argument that was never handed are refused in one voice.
    """


class RootDirectoryVariableIsEmptyError(KubementatError):
    """A directory variable that is set to nothing — which is not the same as unset.

    Shared by both roots, because both roots read the same rule: `KUBEMENTAT_HOME` for the tree a
    run writes into, `KUBEMENTAT_PLATFORM_CONFIG_DIR` for the tree it reads. A variable set to the
    empty string used to fall back to the built-in default, which read configuration out of
    site-packages while saying nothing about it; the refusal is here so the empty case is decided
    once and not in each root's own words.
    """


class ConfigurationStampMissingError(KubementatError):
    """A directory that carries no `kubementat.yaml`, so it does not say what prepared it.

    A missing stamp is a refusal and not a default. Assuming `schema_version: 1` when the file is
    absent rescues a tree that was never stamped, which is the same mistake as a library function
    silently bumping it, so the reader refuses and names the verb that writes the file.
    """


class ConfigurationStampInvalidError(KubementatError):
    """The stamp exists and is not the two keys this product stamps.

    Four readings are one refusal, and the message says which of the four it found: bytes that are
    not YAML, a document that is not a mapping, a mapping that declares neither key, and a
    `schema_version` that is not an integer. All four answer the same operator question — *what does
    this tree say it was prepared for?* — and none of them can be answered by guessing, which is
    why none of them is a warning. `config validate` collects this one as a finding instead of
    ending the run, which is the only place the difference in severity is allowed to show.
    """


class SchemaVersionTooOldError(KubementatError):
    """A tree two or more schema steps behind is refused before anything touches the cluster.

    The severity is the requirement: a version string that drifted warns and continues, a schema
    that far behind means every answer this tree gives that its own layer does not name is coming
    from a built-in default, so the cluster is configured by kubementat's assumptions and not by the
    operator's. The bump that closes the gap is a decision, and no command makes it — hence a
    refusal with a remedy sentence instead of a default.
    """


class TeamConfigurationFileMissingError(KubementatError):
    """A team-scoped read whose layer file the loader had to mark missing.

    The warning at load time already named that file; this is the same file named in an error the
    operator can act on, raised by the door that reads the answer rather than the door that logged
    it. Both doors use this class: `Config.get` (which fills the rule over a team's name) and
    `Config.declared` (which asks only what the files say). A destructive path asks the second
    question, and a refusal that differed between the two would be a second owner of one rule.
    """


class EnvironmentConfigurationFileMissingError(KubementatError):
    """The environment's own `static.json` is not there, so nothing is addressable.

    The namespaces a team is addressed through are read from that file, so a create, a bind or a
    sweep against this environment is a guess — and a guess is the answer this refusal exists to
    forbid. It lives beside `TeamConfigurationFileMissingError` rather than under it because the
    two files are different layers and the remediation verbs differ.
    """


class EnvironmentConfigurationDirectoryMissingError(KubementatError):
    """There is no directory for the environment at all — which is not an environment with no teams.

    The distinction is the refusal: a listing that treated the absent directory as an empty one
    would report success on a teardown that deleted nothing. Enumerating the teams is a directory
    listing and nothing else, so when the directory is not there the answer is the verb that lays
    it down.
    """


class ReleaseKnobNotABooleanOrStringError(KubementatError):
    """A release knob declared as a number, a list or a dict.

    Both knobs and both accessors come through the one implementation, so one class answers for
    `<COMPONENT>_ENABLED` and `<COMPONENT>_VERSION`. There is no valid answer for either that is not
    a boolean or a string, and the shape is what the refusal is about: a version that reads like `1.0`
    is a JSON number, which is not the string `"1.0"` and resolves to a chart helm has never seen, and
    a `false` that falls through a type test becomes the default of `true` — the operator who said
    "do not install this" gets it installed and is told the opposite. So it stops where it was typed,
    and the message names the key, the file that declared it, and the two files of that layer the key
    could have lived in.
    """


class TeamConfigurationUnreadableError(KubementatError):
    """A team's `static.json` will not parse, so it names no namespaces.

    A file that will not parse is not a file that says nothing, and the tolerant reader's answer —
    skip the directory and sweep the rest — is not an answer a command asked to bind a Role can take.
    Letting a `JSONDecodeError` through is how a configuration problem arrives as a traceback of the
    tool's own stack, so it is refused with the path, the team and the parse error in one sentence.
    """


class TeamNamespaceNotConfiguredError(KubementatError):
    """A team's configuration names fewer than both namespaces a command has to bind in.

    The rule over a team's name yields an address and not a deed: `pandas` team's pipelines
    namespace is `pandas-pipelines` whether or not a file says so, and a Role bound there is bound on
    the strength of a directory name. A strict reader therefore asks for the key that is *set*, and
    refuses when it is not. The sweep is the tolerant one by comparison — it deletes only what a file
    named — which is why the two readers share the file and the keys and not the appetite.
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

    The message is not assembled inside the sweep any more. `SweepResult` carries the residue as
    a field and builds this sentence from it, so the CLI, a caller and a test all read one list --
    which is what lets a partially-succeeded uninstall be reported as a result and not only as an
    exception. Same class, same words, same exit code; the string just has one owner now.
    """


# ---------------------------------------------------------------------------
# What the configuration verbs refuse — the `config_init` / `config_validate` half.
#
# These forty-odd refusals used to be `click.ClickException`s, raised from inside the modules that
# read or lay down a configuration tree. That shape said "a command printed this" and not
# "kubementat refused", and it put a CLI type on every library consumer of those modules. They
# descend from `KubementatError` now and the CLI renders them: the same one `Error:` line and exit
# 1 they got for free before, produced by `cli/context.py`'s allowlist and `cli/kmt.py`'s group
# class rather than by Click's own handler.
#
# As in the `core/config/` group above, the grouping is by the question the operator has to answer
# and not by the module that was asked. `set_answer` and `unset_answer` ask the same question about
# a layer that is not there and raise one class; `config init` and `config validate` ask the same
# question about a tree that is not there and raise one class. Where two sites raise the same
# sentence they raise one class, and where a refusal has a sibling that reads differently they stay
# one class when the operator cannot tell them apart from the terminal.
# ---------------------------------------------------------------------------


class AnswerArgumentNotKeyValueError(KubementatError):
    """A `KEY=VALUE` the operator handed a verb was not a key, an equals sign, and a value.

    What the tree on disk has to do with this is nothing, which is why it is refused where the
    argument is parsed rather than where the file is written: the typo is in the command line, and
    no edit to a configuration directory would have answered it. `--set` and `--encrypted-set` take
    it, and the message names the flag that was given the malformed item.
    """


class DuplicateAnswerKeyError(KubementatError):
    """One answer was given twice in a single run.

    A JSON object cannot hold a key twice, so the two answers would have to be reconciled
    somewhere, and the reconciliation is whichever flag happened to be parsed last. Refusing is the
    only answer that does not make the order of the operator's flags decide a configuration.
    """


class LayerNameUnusableError(KubementatError):
    """An environment or team name that could not be one directory's name.

    A layer is exactly one directory under the root (or one under an environment), so a name that is
    empty, a path, two directories, padded with whitespace, or carrying a NUL is not a shortened
    name but a name no reader finds again. Refused before any write, so the tree is left exactly as
    it was found.
    """


class LayerNameAlreadyTakenError(KubementatError):
    """The name an environment or team was to take is already one of kubementat's own.

    An environment named `secrets` is read as the root's `secrets` directory and not as a layer, and
    a team named `values` is a directory two rules both claim. The listing already filters
    `kubementat_components` out for the same reason; this says it to whoever picked the name.
    """


class EnvironmentNotSuppliedError(KubementatError):
    """The verb needs to know which environment it is about, and none was given.

    `config init` writes every file but the manifest under `<root>/<environment>/`, so naming none
    is not "all of them" — it is a tree with no place to put an answer. `config validate` asks the
    same question because it answers the files of one environment and the root that holds them.
    """


class TeamWithoutEnvironmentError(KubementatError):
    """A team was named and the environment it sits under was not.

    A team is a layer under one environment, so the request has no answer in the tree: the directory
    would have no parent to be under. Both `kmt config init` and the legacy `initialize` reach this
    one sentence.
    """


class ForeignConfigurationMaterialError(KubementatError):
    """The directory already holds files kubementat did not write.

    `init` lays its own files beside them only when the operator passes `force`, because a tree that
    is half somebody else's is not a tree this verb can describe: it cannot tell an edited answer
    from a half-written one, so it lists what it found and says which of the two flags answers this.
    """


class ConfigurationFileDiffersError(KubementatError):
    """A file kubementat was about to write is already there and says something else.

    This is not a conflict to overwrite. Someone wrote that file, and the difference between an
    edited answer and a half-written one is not something a flag can tell apart, so no flag lifts
    the refusal — `force` is about foreign material, never about an answer that is already there.
    """


class ConfigurationRootNotUsableError(KubementatError):
    """The directory that was named is not a configuration directory.

    The layer verbs and the keypair verb are additive: they put one layer, or one keypair, on a tree
    that already exists. The tree is therefore a precondition and not something either would create,
    which makes this a refusal and not a bootstrap. The same sentence is shared with
    `config_keys.ensure_deployer_keypair`, which cannot write a keypair into a directory that is not
    there either, and the claim about the verb is spelled into the message so a wrong directory is
    told which verb to come back with.
    """


class ConfigurationTreeNotLaidDownError(KubementatError):
    """A directory that exists is not a kubementat tree, because `kubementat.yaml` is not in it.

    One verb writes the manifest, and it is what says which product the tree is for. Everything else
    adds a layer to what that verb laid down, so a tree with no manifest is refused rather than
    completed: guessing the manifest's answer would be a second product definition in a
    subdirectory.
    """


class StaticLayerFileUnreadableError(KubementatError):
    """A static layer file is present and cannot be read.

    `config set` and `config unset` edit the file that is there and write nothing else, so they
    cannot go on without it. It is refused as a `KubementatError` and not an `OSError` for that
    reason: the reason may be git-crypt, a permission, or a file that is a symlink to nothing, and
    all three are the operator's to fix rather than a stack to trace.
    """


class StaticLayerFileInvalidError(KubementatError):
    """A static layer file is there, readable, and not the document a layer is.

    Either it is not JSON at all, or it parses into something other than the object keyed by answer
    name that every reader of a layer expects. kubementat will not rewrite a file it cannot read and
    call the result a configuration, and it will not put a key into a JSON list.
    """


class TeamLayerCarriesClusterKeyError(KubementatError):
    """A cluster key was answered on the team layer, where nothing reads it.

    One directory is exactly one cluster: what resolves a cluster reads the environment's own static
    file and no other path, so a team-layer answer is read by nothing and leaves the tree with two
    accounts of the same key. The message names the file that does answer and the verb that writes
    it there.
    """


class NullAnswerNotAcceptedError(KubementatError):
    """`config set` was told to answer a key with `null`.

    null is the absence of an answer, and one verb should be able to create it. Held open with a
    placeholder, a key answers nothing while looking answered, which is the state every later
    "is this configured" question gets wrong.
    """


class KeyDeclaredByNoLayerError(KubementatError):
    """`config unset` was asked to take away an answer no layer of this tree declares.

    That is a different fact from "the answer is null", and the message says so: the file that
    declares a key is the file that answers it, so nothing is written for a key that was never there.
    The searched paths are named, because the honest next question is which file to look in.
    """


class AnswerAlreadyPresentError(KubementatError):
    """`--generate` was asked for a key that already answers.

    kubementat keeps a value that is there rather than rewriting it in place: a verb that generated
    while it wrote would make the act of touching a directory the way a credential is lost. The
    message gives the rotation, because that is the act the operator actually meant.
    """


# ---------------------------------------------------------------------------
# What the deployer keypair and git-crypt refuse.
#
# `config_keys` and `config_bootstrap` lay down the four keypair files and make git-crypt live in
# the directory, and every one of these is decided before a byte is written — the promise the module
# makes and the one `initialize` broke by refusing four steps in. They are one group because they
# answer one question: is there a deployer identity, and is it in the directory in the shape the
# next step reads it in.
# ---------------------------------------------------------------------------


class DeployerIdentityNotNamedError(KubementatError):
    """A gpg keypair had to be made and no identity was given to make one under.

    `config init` derives nothing from the environment for this: deriving the deployer's email from
    a variable is what `initialize` did, and the caller owns that answer. So the refusal says which
    flag carries it, and that the directory can be left exactly as it is until it does.
    """


class GpgKeyNotImportableError(KubementatError):
    """A file that was handed over as a gpg key is not one this run's keyring can import.

    The point of naming it is what did *not* happen: nothing was generated in its place, so the
    directory does not end up with a key nobody asked for wearing the name of the one that was
    missing.
    """


class GpgKeyWithoutPublicKeyError(KubementatError):
    """A key imported cleanly and then exported no public half.

    `gpg_public_key.gpg` is the file whose whole job is to be handed to git, so an imported key that
    cannot answer for itself in public is not a deployer key. Nothing was generated to paper over
    it.
    """


class GpgKeyFileMissingError(KubementatError):
    """The `--gpg-key` flag named a file that is not there.

    A flag that points at a missing file is a typo, and the version of this bug that reports itself
    as success is the one where a generated key answers in its place.
    """


class DeployerKeyPairIncompleteError(KubementatError):
    """One half of a keypair is in the directory and the other is not.

    Two halves are one unit: a pair that is half there is a key that cannot be used and cannot be
    completed without saying which half the operator meant. This is the same claim `DeployerKeyMissingError`
    makes about a directory with neither half, told from the side where one file was found.
    """


class GpgBinaryNotRunnableError(KubementatError):
    """The deployer keypair needs the `gpg` binary and it is not runnable here.

    This is not a new dependency — `CryptoUtils` needs the same binary — it is the first place that
    says so out loud when the binary is not on the machine instead of letting the next step die on
    an `OSError` three layers down.
    """


class GitCryptSymmetricKeyMissingError(KubementatError):
    """The pipeline that has to open the configuration was handed a directory with no key to open it.

    `git_crypt_symmetric.key` is what `git-crypt unlock` reads, exported by `config init` as its
    eighth step and then gitignored. A clone therefore carries none, which is the reason a clone
    `git-clone-configuration` makes arrives locked: the answer is to run init here and not to copy a
    key in from somewhere else.
    """


class DeployerPublicKeyMissingError(KubementatError):
    """The step that registers a collaborator has no public half to read a name from.

    Refused rather than skipped, because a directory that is ready except for its collaborators still
    looks ready, and the next operator to read that tree in a second checkout is the one who finds
    out.
    """


class GpgCollaboratorNotRegistrableError(KubementatError):
    """git-crypt has no collaborator to register, from the key that is in the directory.

    Either the file yields no fingerprint, or the running keyring will not hold it. Both are refused
    and not reported, because step 7 skipping quietly is the failure this function exists to remove.
    """


# ---------------------------------------------------------------------------
# What helm refuses.
#
# The four `latest-version` refusals are one group because the operator's question is "what is the
# newest version of this chart" and the four answers are told apart by what to do next: name the
# chart, add the repository, fix the name, or accept that `helm` could not be asked. The rest are
# the writes: a release that is already running somewhere else, a cluster that cannot say where its
# releases are, a component that would not come off, and grafana, which is not installable from an
# incomplete admin pair.
# ---------------------------------------------------------------------------


class DeployedReleaseNamespaceConflictError(KubementatError):
    """A release this apply reaches is already deployed in a namespace other than the one now named.

    A deployed release's namespace is convention and not configuration: helm stamps the
    cluster-scoped objects a release creates with `meta.helm.sh/release-namespace`, so applying the
    same objects under the new name belongs to no release and helm refuses them while the release
    that is serving keeps serving where it is. kubementat does not move a deployed release between
    namespaces, and it will not take one off to make the new name fit — which is why the message
    says so before the traceback helm would have produced.
    """


class HelmReleaseListingFailedError(KubementatError):
    """The cluster could not say which namespace each of its releases is in.

    The check that refuses a namespace rename is a read and not a guess, so when it cannot be
    answered nothing is applied. The alternative is to guess, and a guessed namespace is applied to
    a running release.
    """


class HelmChartNotSuppliedError(KubementatError):
    """No chart was named, so there is no version to look up.

    The form the flag takes is spelled in the message because the answer the operator wants is a
    command line and not a description.
    """


class HelmSearchFailedError(KubementatError):
    """`helm search` could not be run, or refused the target.

    What helm said is passed through verbatim, because the usual reasons — no index, no network, a
    repository that moved — are in helm's own sentence and kubementat cannot improve on it.
    """


class HelmRepositoryNotConfiguredError(KubementatError):
    """The chart's repository is not added to this machine.

    This is a different refusal from `HelmChartNotFoundError` and it is deliberately named apart,
    because the fix is `helm repo add` and not "check the chart name". An unadded repository answers
    a search with an empty list rather than an error, so the empty list alone could not tell the two
    apart; the repository list is what makes the question answerable.
    """


class HelmChartNotFoundError(KubementatError):
    """The repository answers, and not with this chart.

    The advice is the one that applies once the repository is known to be added: the name, or an
    index that is stale because a chart that exists upstream is not visible here until
    `helm repo update`.
    """


class GrafanaAdminCredentialsNotConfiguredError(KubementatError):
    """grafana's admin pair is not answered in the environment's encrypted file.

    Its admin account is built from both halves, and helmfile aborts the whole file without the
    password, so nothing would be applied. The message names the file and the verb that answers it,
    and says not to answer it in the shell, where kubementat cannot see it. It does not descend from
    `GrafanaError`, which is the family `kmt users` catches to report account work over HTTP; this
    is a configuration key and is answered where configuration keys are answered.
    """


class CommandAnswerNotParseableError(KubementatError):
    """A command answered something whose shape cannot be read.

    Either its output is not JSON, or it parsed into a document of the wrong shape. Either way the
    answer it carries is not one anything downstream can trust, and finding out inside a `for` loop
    is how the same failure arrives five times with no name on it.
    """


# ---------------------------------------------------------------------------
# Confirmations, cluster permission, and the kubeconfig a credential is copied from.
#
# Three of these are the same question asked by three commands: is anyone standing here to answer,
# and what did they answer. `kmt users create-team-account`, `kmt routing install --update` and
# `kmt uninstall` all fail closed when there is no terminal, and all three cancel on "no" in the
# same words, so they raise the same two classes.
# ---------------------------------------------------------------------------


class ConfirmationUnavailableError(KubementatError):
    """The run needed confirmation, and stdin is not a terminal.

    A prompt that reads end-of-file is answered by the empty string, and the bash that this replaced
    then died on `set -e` with no message at all. So the answer is a refusal that names `--yes` and
    edits nothing, whether the caller is a pipe, a cron entry, or a library that built no terminal.
    """


class ConfirmationDeclinedError(KubementatError):
    """The operator answered no.

    This is a decision and not a bug, so it gets a sentence and not a traceback: what was not done,
    that the cluster is untouched, and how to go ahead. The bash printed one line and exited 1, and
    declining has had the same voice since.
    """


class ClusterAccessDeniedError(KubementatError):
    """The cluster will not grant kubementat the verbs this run needs.

    Checked before the first write, and listed in full, so that fixing the table and re-running is
    cheaper than discovering the next denial one run at a time. A half-configured account that
    failed on a binding it had just been told it did not have is the failure this refuses.
    """


class ResolvedKubeconfigUnusableError(KubementatError):
    """The kubeconfig this command resolved is not a document this command can copy from.

    Three doors lead here — it will not parse, it declares no cluster, or it declares one without
    both a server address and a certificate authority — and they raise one class because the
    operator's question is the same from all three: which kubeconfig did you just read. The account
    kubeconfig is a document and not a string, so what is missing here is what makes the printed
    output something a person can paste.
    """


class ClusterManagerEmailNotConfiguredError(KubementatError):
    """`CLUSTER_MANAGER_EMAIL` is not answered in the environment's file, or is still a placeholder.

    Both ClusterIssuers register an ACME account with it, so an issuer applied without one cannot
    issue a certificate — an issuance failure that stays invisible until a certificate expires.
    There is no built-in default for an email address, which makes this the one key the rule that
    answers the others cannot answer for.
    """
