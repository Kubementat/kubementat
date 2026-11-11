"""The five answers a team's name already gives.

Some answers are not preferences. Owner ruling, 2026-11-03: `APP_DEPLOYMENT_NAMESPACE` is the team's
name, `PIPELINE_NAMESPACE` is `<team>-pipelines`, `MYSQL_HOST` is `mysql-<team>`, `REDIS_MASTER_HOST`
is `redis-<team>`, and the deploy key's `TARGET_NAMESPACE` is the team's name again. They used to be
produced by a substitution that took the team's name into a `REPLACEME_TEAM` placeholder inside a
template, and what that left behind is an answer the operator cannot see and a tree that only works
because something copied it out. This module is the
rule instead of the copy: the same answer, computed when it is read.

**Derived is not generated, and the line is this module's reason for existing.** Both end in
"kubementat filled in the value", and the two live one line apart in the old code path, which is
where the distinction gets lost. `PIPELINE_NAMESPACE` is derived: exactly one answer is correct, so
computing it is a rule and *not* computing it is a bug. `MYSQL_ROOT_PASSWORD` is generated: two
correct answers differ, so picking one would be inventing, and `config set --generate` is what does
that with entropy. Nothing in this module imports `random`, `secrets` or `uuid`, and the
test that says so is not decoration — a rule that drifts is a rule that lies, and a generator hides
that drift behind a different answer every time.

**These are L0 answers, resolved at read time: the highest layer that declares a key answers it.**
They answer through the one walk `Config` does its resolution with, and only where a team
layer is in the chain, so a tree that answered nothing but the team name is still askable and an
operator who writes any one of them into `static.json` is believed. A derivation that beat the
operator's file would not be a rule; it would be a rule that became a lie, and it would pass every
derivation test in the tree.

**The team is the rule's whole input, so it is a precondition.** A run that named no team cannot be
answered from nothing: `require_team` raises `TeamNotSuppliedError` naming the key and saying what is
missing. `None` would be the silent answer `tekton_utils.py:59-68` had to be given a guard for, and
that guard's own docstring is the tree's best evidence that a silent `None` once shipped.

**One of the five is a structure, and it is the one a reader gets wrong.** `SSH_DEPLOY_KEYS` is a
list of entries, and the rule reaches inside an entry: it answers the entry's `TARGET_NAMESPACE` when
the entry itself said nothing, keeps the entry's other fields exactly as they were, keeps the list a
list, and never mutates what the file declared. It is also the one of the five that cannot be answered
out of the team's name alone: six of an entry's fields are the operator's key material, and inventing
them would be generation with a nicer name. So the rule has a precondition the other four do not —
an entry has to exist. Where no layer declares `SSH_DEPLOY_KEYS` there is no answer, and that is the
correct answer rather than a gap: a deploy key with a derived namespace and no public key is not a
key, and a stub entry would reach `automation.py:1396` as a `KeyError` inside a secret creation that
has already started.

The names here are the rules, not the files. The shipped templates still carry `REPLACEME_TEAM`
because the substitution that used to rewrite it died with the command that copied them, so the
placeholder is now a shape nothing reads. This module's own rule is what keeps the difference
honest: the derivation answers, a declared value outranks it, and no destructive path may obtain a
namespace from the rule.
"""

# The five keys, spelled once so a test and the read path agree about what a key is called. These are
# configuration keys and not Python names, which is why they carry their `static.json` spelling.
APP_DEPLOYMENT_NAMESPACE = "APP_DEPLOYMENT_NAMESPACE"
PIPELINE_NAMESPACE = "PIPELINE_NAMESPACE"
MYSQL_HOST = "MYSQL_HOST"
REDIS_MASTER_HOST = "REDIS_MASTER_HOST"
SSH_DEPLOY_KEYS = "SSH_DEPLOY_KEYS"

# The field inside a deploy-key entry that the team's name answers. Spelled out and not read from a
# template, because a reader that has to open a template to know which field a rule touches will guess.
TARGET_NAMESPACE = "TARGET_NAMESPACE"

# The mapping the read path consults is defined below, after the rules themselves, so the functions
# are the definition and the dictionary is only their index. `SSH_DEPLOY_KEYS` is deliberately absent
# from it: no team name computes an entry, only reaches into one (`ssh_deploy_key_entries`).


def require_team(key: str, team) -> None:
    """Refuse when the rule's only input is missing, and say what was asked for.

    `key` is the configuration key the answer belongs to, because a refusal that says "no team" asks a
    second question — no team, and which answer does that concern? `team` is what the run was given,
    which is the argument this module is a rule over and therefore the argument it checks.

    The import is inside the function and not at the top of the module: `TeamNotSuppliedError` belongs
    to `config.py`, and `config.py` imports this module. A module-level import would be a cycle that
    resolves only when the two are read in one order, which is not a rule anyone should have to
    remember before they fix a sentence.
    """
    if team is None or not str(team).strip():
        from kubementat.config import TeamNotSuppliedError

        raise TeamNotSuppliedError(
            f"{key} is a team-shaped answer: it follows from the team's name, and this run named no "
            f"team, so there is nothing to derive it from. kubementat does not answer a missing "
            f"answer with `None` — that is what wrote eight objects into a namespace called `null`. "
            f"Pass --team (or set TEAM) to name the team this answer belongs to."
        )


def app_deployment_namespace(team: str) -> str:
    """The namespace an application lands in is the team's own.

    The team and its applications share a namespace, which is what a deploy key pointing back at the
    team's namespace means; two names for one location is one fact with two owners.
    """
    require_team(APP_DEPLOYMENT_NAMESPACE, team)
    return team


def pipeline_namespace(team: str) -> str:
    """The pipelines live beside the team, under a name anybody could predict.

    `<team>-pipelines` and nothing else. The `-pipelines` suffix is what keeps a team's Tekton objects
    out of the namespace its applications run in, which is the half an operator could not restate by
    naming a team, and the reason this rule has a suffix where the other two namespace rules do not.
    """
    require_team(PIPELINE_NAMESPACE, team)
    return f"{team}-pipelines"


def mysql_host(team: str) -> str:
    """One team's MySQL is the `mysql-<team>` service.

    A database host is not a preference either: a team that names two mysql services has two
    databases and no way to say which one `create-mysql-database` meant. This is `mysql-<team>`, and
    it is stated that way rather than found in a template so the rule is what a
    reader reads.
    """
    require_team(MYSQL_HOST, team)
    return f"mysql-{team}"


def redis_master_host(team: str) -> str:
    """One team's Redis is the `redis-<team>` master.

    The same rule as MySQL, one word further along: both are per-team services, so the team's name is
    half the address and the other half is the product's own name for the service.
    """
    require_team(REDIS_MASTER_HOST, team)
    return f"redis-{team}"


def ssh_deploy_key_namespace(team: str) -> str:
    """The namespace a deploy key is installed into is the team's own.

    This is the rule that answers `SSH_DEPLOY_KEYS[0].TARGET_NAMESPACE`, and it is a
    namespace and not a string: the deploy key reaches the applications, which live in the team's own
    namespace (`app_deployment_namespace`), not in the pipelines directory's. It answers the same
    question that function answers, which is exactly why a reader who confuses the two is wrong.
    """
    require_team(SSH_DEPLOY_KEYS, team)
    return team


def ssh_deploy_key_entries(entries, team):
    """The deploy keys as they were declared, with the namespace inside each one answered.

    `entries` is whatever a layer declared under `SSH_DEPLOY_KEYS`; `team` is the team the run named.
    The list shape and every field an entry carries survive untouched, because the operator wrote
    them and this rule's one job is the field the team's name determines. Only the entries that said
    nothing about their namespace are answered: `TARGET_NAMESPACE` absent, `null`, or empty is a half
    sentence, and a half sentence about a namespace is what `jq -r` used to answer with the string
    `null`.

    A declared namespace is never replaced, which is the override half of the resolution rule and the
    reason this function asks about the field's *presence* rather than its correctness: a team that put its keys
    in a shared namespace is a team that means it.

    Anything that is not a list of entries is returned exactly as declared. A value that is a string
    is not this rule's to unwrap, and flattening a list into a string is the failure this function's
    shape exists to prevent.
    """
    if not isinstance(entries, list):
        return entries
    answered = []
    for entry in entries:
        if isinstance(entry, dict) and not str(entry.get(TARGET_NAMESPACE) or "").strip():
            entry = dict(entry)
            entry[TARGET_NAMESPACE] = ssh_deploy_key_namespace(team)
        answered.append(entry)
    return answered


def derived_declared(key: str, value, team):
    """The answer for a key a layer *did* declare, with the rules that reach inside it applied.

    Four of the five rules answer a key no layer declared; they are in `RULES`, and they never see
    this function. `SSH_DEPLOY_KEYS` is the fifth: the key is always the operator's, and the rule only
    answers a field inside it. So the read path calls this on every declared answer and the rules
    themselves decide whether the shape is theirs — which is why a value of the wrong shape comes back
    untouched instead of being corrected.
    """
    if key == SSH_DEPLOY_KEYS:
        return ssh_deploy_key_entries(value, team)
    return value


# The index, spelled last so a reader meets the five rules before meeting the table that holds them.
RULES = {
    APP_DEPLOYMENT_NAMESPACE: app_deployment_namespace,
    PIPELINE_NAMESPACE: pipeline_namespace,
    MYSQL_HOST: mysql_host,
    REDIS_MASTER_HOST: redis_master_host,
}
