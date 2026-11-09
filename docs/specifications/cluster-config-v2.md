# Specification: environments as clusters — cluster addressing for kubementat v2

**Date:** 2026-11-01
**Status:** specification for review. This is the "own plan" that `docs/plans/finish-python-migration.md:1663`
and `TODOs.md` both deferred — *"cluster-context/multi-cluster support … deserves its own plan"* — and it is
**in scope for v2**, not deferred from it.
**One command in this file no longer exists, and it is named here so a reader does not go looking for it.**
`kmt initialize` is deleted: `kmt config init <directory>` prepares a configuration directory in its place and
`kmt install` is what touches a cluster. Everything this spec says that command *writes* is now written by the
verb that prepares the directory, so §4.4 and owner decision 15 describe retired machinery with the same intent.
Read those two as `config init`; read nothing else in this file differently, because everything else it settles
— that an environment is exactly one cluster, that a kubeconfig is the credential store, that a context is named
per command — still holds and still refuses for the same reasons.
**Supersedes, on this topic:** the `Non-goals` row of `finish-python-migration.md`, and §2.2 of
[`../research/specification-externalize-configuration-10-30-2026.md`](../research/specification-externalize-configuration-10-30-2026.md),
which lists multi-cluster out of scope. That out-of-scope is correct for that document and wrong for the product.
**Input it respects:** `research-kubementat-improvements-post-python-migration-10-27-2026.md` row **N7**
(*"`load_kube_config()` falls back to `load_incluster_config()`; global `--context`/`--kubeconfig`"*), which
measured this as a prerequisite before anyone could build on top of it.

### Owner decisions this spec encodes

Taken in a design interview on 2026-11-01, in the order that made each one cheap or expensive:

1. **An environment is exactly one cluster** (1:1), and that is a constraint kmt enforces, not a convention it assumes.
2. **The kubeconfig stays the credential store.** kubementat's configuration *names* a cluster; it does not
   hold credentials for it.
3. `environment` becomes the **universal first positional argument** of every command that touches a cluster.
4. **Per-command selection now.** The library-for-many-clusters API that follows is the reason the design must
   be free of global cluster state; it is not the reason for this change.
5. `KubernetesUtils` takes a **target**, constructs its clients **lazily**, and no part of `src/` calls
   `config.load_kube_config()` again.
6. A missing cluster key is an **error**, not a warning, and the current-context **fallback is gone**.
7. Cluster identity is the **server address** — no cluster contact, so the check costs nothing and fails
   before anything is written.
8. helm and helmfile receive the target as **argv flags**, never through the environment.
9. The kubectl conversion is scoped by *why the call exists* (§9): reads that decide something become the
   client, sessions stay `kubectl`.
10. `ClusterTarget` has **two constructors** — kubeconfig and connection material — and never renders a credential.
11. Tests **inject** the fake client, and the cluster joins the parity record.
12. A collision is a **hard block**, with no override flag.
13. Every service class takes one **`Context`**, not `(environment, team)` plus a config each.
14. `cli.kmt.Config` is **deleted**; the `Context` is built per command by a decorator.
15. `kmt initialize` writes `CLUSTER_KUBE_CONTEXT: null` and **says what to do about it**.
16. Unresolvable environments are **reported and skipped** during the check; the capability is a command,
    `kmt environments list`.
17. `from_service_account()` is **the last item of the plan**, not part of its middle.
18. The check runs in `Context.build()`, for **every** command.
19. No `DEFAULT_TEAM`. **kmt never guesses what a command means** — at the level of clusters *or* teams.
20. Config keys are `CLUSTER_KUBE_CONTEXT` / `CLUSTER_KUBECONFIG_PATH`; the flag stays `--context`.

**Explicitly stated context for the whole spec:** this is a **v2 rewrite**. There is no upgrade path, no
shim, no back-compat obligation, and new clusters only. Several of the decisions above (6, 19) would be
wrong for a 0.x release and are right for this one, because they give up convenience rather than give up
correctness.

---

## 1. Problem and goal

### 1.1 What is wrong today

Two separate defects, one root cause.

**The root cause is that `environment` and `cluster` are not the same word in this codebase, and nothing says
which one is which.** `docs/README_PLATFORM_CONFIG.md:3-4` defines an environment as *"configuration for the
according environments. Each sub-directory represents an ENVIRONMENT (dev or prod)"* — i.e. a directory of
config. `templates/environment/static.json.template:12` carries `"ENVIRONMENT": "dev"` and **no cluster key of
any kind**: no context, no server address, no kubeconfig path. So the config tree describes namespaces, images,
domains and versions, and is silent about the single most consequential fact about a run: which cluster.

The cluster is therefore decided outside kubementat, implicitly:

```
src/kubementat/kubernetes_utils.py:209   config.load_kube_config()        # no context argument
src/kubementat/kubernetes_utils.py:233   config.list_kube_config_contexts()[1]   # only to log the answer
```

`load_kube_config()` with no arguments resolves the ambient `current-context`, and — this is the part that
matters for everything downstream — it writes the result into the **global** `client.Configuration` default.
Every API object in `src/` is then constructed bare (`client.CoreV1Api()`, `client.CustomObjectsApi()`), which
reads that global. So: **one process, one cluster, whichever `kubectl` was last pointed at.** Selecting another
one means editing a kubeconfig or exporting `$KUBECONFIG` outside kubementat's own surface.

**The second defect is that the CLI cannot express the difference.** Seven commands touch a cluster and do not
take an environment *at all*: `analyze-deployment`, `pod-images`, `cluster-capacity`, `deploy-ssh`,
`deploy-logs`, `service-account-token`, `create-secret-from-json`, plus `kmt tunnel pod`
(`tunnels.py:126-130`, which builds `KubernetesUtils()` with no environment and no team). Each of them takes a
namespace and inherits the cluster.

And the write path is not exempt. `helmfile_apply` (`helm_utils.py:118`) assembles:

```
["helmfile", "apply", "--color", "-f", <path>, "-l", <label filter>]
```

**with no `--kube-context`.** The command that installs the entire platform is addressed by ambient state.

There is already a comment in the tree that states the situation honestly, and that this spec reverses.
`src/cli/commands/kubernetes.py:178-181`, explaining why `pvc-usage` echoes its environment instead of using
it:

> *"there is no configuration key that says which cluster a PVC read goes to, and inventing one would be a
> command deciding to talk to something other than what the kubeconfig says."*

That is the correct decision **for a tool that addresses one cluster**. This specification is the deliberate
retirement of it: a key is invented, on purpose, and the command's obligation becomes to say *which cluster it
chose and how*, rather than to hide the choice.

### 1.2 Goal

**kmt knows which cluster every command means, from kubementat's own configuration, and refuses to act on one
while claiming another.**

The test that decides the whole spec (**TD-1**): a machine with two environments in one `platform_config`, each
declared against a different cluster, can run `kmt install dev` and `kmt install prod` back to back and get the
platform each names — with the cluster named in the output of both, in a sentence that is true. And:
**`kmt install` into an environment whose cluster is already claimed by another environment fails, writes
nothing, and names both environments.**

> **Measured in the lane at `2c28eca`, and TD-1 has a half that this rig cannot prove.** The collision half —
> *fails, writes nothing, names both environments* — is asserted twice: in the unit
> (`tests/test_two_environments_that_address_one_cluster_are_refused.py`) and against a real k3s
> (`two-environments-one-cluster-are-refused`, then `refused-collision-install-wrote-nothing`, which reads the
> release set back and proves the namespace the install would have created does not exist). The *two different
> clusters* half is not covered and cannot be on this rig, which is one virt-runner machine with one k3s
> installation. The lane prints that itself in `result.json` as `not_covered`
> (`tests/e2e/harness/config.py:294-300`) instead of letting a reader infer a fifth row from four covered ones.
> A report that says "TD-1 passed" without that field overstates the run.

Two secondary tests that follow from it:

- **TD-2** — `kmt environments list` answers *"what environments do I have, and what cluster is each one?"* in
  one command, without contacting any cluster.
- **TD-3** — no code path in `src/` can produce a Kubernetes client whose cluster is not the one its
  `Context` carries. Global state is structurally unavailable, not merely discouraged.

---

## 2. Scope

### 2.1 In scope

* The 1:1 model and what enforces it (§3).
* The two configuration keys, the resolution order, and the removal of the fallback (§4).
* `ClusterTarget` and `Context`: what they hold and where they are built (§5–§6).
* The client-binding change: from global config to per-target clients (§7).
* The 1:1 identity check: what it compares, when, against what, and what it refuses (§8).
* The CLI surface: `environment` universality, the `@kmt_context` decorator, `kmt environments list`, the
  deletion of `cli.kmt.Config` and of `DEFAULT_TEAM` (§9, §12).
* The kubectl → Python-client conversion, scoped (§10).
* helm / helmfile / kubectl argv plumbing (§11).
* Test strategy: injection, and the cluster inside the parity record (§12).

### 2.2 Out of scope (explicit)

* **Fan-out across clusters.** One command, one cluster. `kmt status --all` is not in this spec, and the
  design deliberately keeps its door open (§13.2) without opening it.
* **A cluster registry.** kubementat has no object named "cluster" that a human adds, renames, or deletes.
  The inventory *is* `platform_config`, and the command that shows it is `kmt environments list` for exactly
  that reason (§9.4). A future `kmt cluster add` feature would write into the *kubeconfig*, not into a
  kubementat registry — and it is not this spec's to design.
* **kubementat as a credential store.** Credentials remain in the kubeconfig (§3.3). `from_connection` (§6.2)
  exists for the library caller that already holds credentials, and is not a configuration option.
* **In-cluster resolution** (`load_incluster_config`) as part of the working design. It is the last item of
  the plan (§14) and the reason it is last is a real, measured defect.
* **Replacing helm or helmfile.** They remain dependencies, and they are precisely the part of a cluster
  operation that no amount of Python converts.
* **`kmt doctor`**, which the research report proposes as X3. `kmt environments list` is the read-only slice of
  it this spec needs; building the rest is that document's decision.

---

## 3. The model

### 3.1 An environment is one cluster

`platform_config/{environment}/` *is* the cluster's configuration, and one cluster is configured by exactly one
environment. This is enforced (§8), not assumed.

The reasoning that made this cheap rather than merely convenient:

* `Config` is already keyed by `(environment, team)`, and every namespace in the product resolves through
  `Config.component_namespace(component)`. An environment mapping to two clusters would make
  `component_namespace` answer twice to one question — which is the failure this project's documentation
  names repeatedly, e.g. the namespace tables in `docs/README_PLATFORM_CONFIG.md`.
* `dev` and `prod` in practice *are* different clusters.
* With 1:1, **selecting an environment already is selecting a cluster**, so no new address space, registry or
  `use`-the-ambient-state command is needed. §9.4 records why a `kmt clusters use` was rejected.
* It is cheap to relax later. The reverse is not.

### 3.2 What 1:1 does *not* mean

It does not mean "the environment is a cluster's name." `dev` is a directory that *resolves* to a cluster, and
the resolution is what §4 defines. Two environments may of course be configured on one machine; they may not
resolve to one cluster.

### 3.3 The kubeconfig remains the credential store

A cluster is reached by a **kubeconfig file plus a context name inside it**. kubementat's configuration names
the context; it never stores a token, a client certificate, or a CA.

Three consequences worth stating because they are decisions, not side effects:

* **Exec plugins continue to work.** `aws eks get-token`, `gke-gcloud-auth-plugin`, `oidc` flows — all of them
  are processes the kubeconfig runs, and none of them are expressible as configuration. Putting credentials
  into `static.encrypted.json` would have silently cut off exactly the managed-cloud users this product wants.
* **A fresh machine needs a kubeconfig before `kmt install dev` works.** Accepted, and stated so here rather
  than discovered later. `kmt cluster add` (§2.2) is the eventual answer to that ergonomics problem.
* **A wrong kubeconfig is still possible**, which is why §8 exists: the tool cannot make the kubeconfig
  correct, only verify that what it resolved is what was configured.

---

## 4. Configuration

### 4.1 The keys

Two keys, both environment-level (`env_static`, i.e. `platform_config/{environment}/static.json`, materialised
from `templates/environment/static.json.template`):

| key | meaning | absent / null |
|---|---|---|
| `CLUSTER_KUBE_CONTEXT` | the kubeconfig context that reaches this environment's cluster | **error** (§4.3) |
| `CLUSTER_KUBECONFIG_PATH` | the file that context lives in | when unset, the environment's own `kubeconfig.encrypted.yaml` ahead of `$KUBECONFIG`, then `~/.kube/config` |

The `CLUSTER_` prefix follows `CLUSTER_MANAGER_EMAIL`, the one existing environment key that is *about the
cluster rather than a component*, and keeps the two new keys grouped next to it in a file a human reads
top-to-bottom. It also keeps them distinct from the process-level variables `KUBEMENTAT_*` and `KUBECONFIG`,
which are environment rather than configuration, and which a similar name would blur.

Why a key and not a value in `static.encrypted.json`: it is not a secret, and a cluster context that belongs
in the unencrypted file keeps the encrypted file's diff small enough to read.

### 4.2 Resolution order

One implementation, in one place (§6.3), for every command:

```
1. --context / --kubeconfig           (the command's own flags)
2. CLUSTER_KUBE_CONTEXT / CLUSTER_KUBECONFIG_PATH   (process environment variables)
3. env_static["CLUSTER_KUBE_CONTEXT"] / [..._PATH]  (platform_config/{environment}/static.json)
4. the material the kubelet mounts — §14's `from_service_account`, added by `eca4c31`
5. otherwise: error naming the environment and the file that should have contained it
```

**Layer 4 was added after this list was written, and it is not a fourth declaration.** §14's last layer is a
construction rather than something an operator declared, so `Context.build` asks for it itself after the three
declared layers come back silent. That is why the numbered list above and §5's enumeration of `source` values
are both one item short of the code: measured at `eca4c31`, `source` can also be `"service-account"`. A target
built that way names neither context nor file, so the session calls in §10.2 refuse when it is the answer —
client paths work, sessions do not. `docs/README_PLATFORM_CONFIG.md` carries the order as it now runs.

The **change in behaviour is still the error that stands where the fallback was** — layer 5 now, layer 4
when this was written. Layer 4 as listed here is new alongside it, and it is last among the layers that can
answer, which is what §14's ordering demanded.

> **The list above and the code comments disagree about these numbers, as of `eca4c31`.** The renumbering is
> only in this document. `Context.build` still calls the mounted material "§4.2's fifth layer"
> (`cluster.py:1033`, `cluster.py:1116`) and still calls the terminal error "§4.2's layer 4" (`cluster.py:97`,
> `cluster.py:1182`) — the numbering that was true before the mounted layer existed. A reader who follows a
> code comment into this section therefore arrives one ordinal off: the comment's "fifth layer" is the list's
> layer 4, and the comment's "layer 4" is the list's layer 5. Nothing about the order or the rule differs, and
> neither side is wrong about what it means; only the number moved. The sentences a reader needs are the note
> above, §14, and `docs/README_PLATFORM_CONFIG.md` (*Cluster addressing*), which carries the same five layers in
> the same order.

The old `current-context` fallback is removed.

**Why the fallback is gone, and why it is the most load-bearing decision in this spec.** Three reasons, and the
third is the one that would have made any other answer wrong:

1. *It is the bug being fixed.* `kmt install dev` silently installing into whatever kubectl is pointed at is
   precisely the complaint this spec answers. A design that preserves that fallback preserves the defect.
2. *It is v2.* The fallback's only value was protecting existing configurations, and there are none.
3. **It weakens §8's check.** An environment that resolved by fallback declared nothing, so the check would
   compare claims against guesses. With no fallback, every environment declares its cluster and the check is
   comparing declarations. §20 in the decision list records that this was debated against the alternative
   (checking only declared environments), and this is the reason the harder option won.

The trade is stated plainly: there is no longer a way to point kubementat at an arbitrary kubeconfig and look
around without a `platform_config` behind it. The diagnostics commands need real configuration now. That use
case was always ambiguous about which cluster it meant, and it is given up deliberately.

### 4.3 Failure text is part of the design

Because the fallback is gone, the error is the interface. It follows the fail-closed pattern `kmt users`
already established (`tests/test_the_users_commands_refuse_a_team_they_were_not_given.py`: *"Guessing is not
an option when the action is create a credential"*) and must name:

* the environment,
* the file it was read from, with a path,
* the key and the value found,
* and — when the named context is not in the resolved kubeconfig — **the list of contexts that are available
  in that file.** An operator who typed `prod-eu` instead of `prod-europe` gets the answer, not a mystery.

### 4.4 What `kmt initialize` writes

`_write_environment_static_json` (`automation.py:378-398`) populates `static.json` from
`KUBEMENTAT_ENVIRONMENT_CONFIGURATION`, and it does so with:

```python
if not os.environ[env_name]:
    raise Exception(...)
```

— which is itself only reachable for a variable that is *set but empty*; a variable absent from `os.environ`
raises `KeyError` at the indexing line. Two things follow, both encoded here:

* `CLUSTER_KUBE_CONTEXT` is **not** added to `KUBEMENTAT_ENVIRONMENT_CONFIGURATION`. initialize does not
  require it, because requiring a cluster at the moment a config tree is created would make `kmt initialize`
  the strictest command in the product for the least benefit.
* The template ships `"CLUSTER_KUBE_CONTEXT": null`, and **initialize prints what that means**: the
  environment exists but does not yet address a cluster, the file to edit, and the command that shows the
  current state of all environments.

So a freshly initialized environment is *intentionally* invalid until a human names the cluster. That is the
point of the sentence initialize prints, not a bug in the template: the alternative — initialize recording
whatever kubectl is pointed at — would be kubementat inventing a cluster claim it did not earn, which is the
D11 failure class `config.py:66` exists to prevent.

The `KeyError` line above is a real, unrelated defect in the same function and is fixed as part of this work,
in the same style `config.py` already uses: a named exception when a required variable is missing.

`tests/test_everything_initialize_writes_is_a_key_the_template_declares.py` is the guard that keeps template
and writer honest, and it is where the new key is pinned.

---

## 5. `ClusterTarget`

A frozen value object. Its whole purpose is that a cluster is described **once**, carries its own address, and
knows where it came from.

```
ClusterTarget
    environment      str            "dev" | "prod" — the name the operator typed
    context          str | None     the kubeconfig context; None when built from connection material
    kubeconfig_path  Path | None    None means "the library's own resolution"
    address          str            the server URL. This is the identity §8 compares
    source           str            "flag" | "env" | "static.json:<path>" | "connection" | "service-account"
                                 ↑ five, not four — see the note under "Plus:"
```

Plus:

```
ClusterTarget.from_kubeconfig(path, context, environment)     # the CLI path
ClusterTarget.from_connection(host, ca, token_or_cert, environment)   # the library path
ClusterTarget.client() -> ApiClient                            # the only behaviour it owns
__repr__  # redacts. Nothing here prints a credential.
```

> **Measured at `eca4c31`, not decided when this section was written.** `source` takes a fifth value,
> `"service-account"`, and there is a third constructor (`ClusterTarget.from_service_account`) sitting behind
> the three declared layers of §4.2. Neither addition changes anything the enumeration above argued for —
> `source` still exists so that "resolved from `--context`" and "resolved from
> `platform_config/prod/static.json`" are distinguishable claims — it only means the list of answers is longer
> than the list of claims anyone typed, which a reader of §4.2 or §5 alone cannot see. The consequence that
> does need saying: a target built from the mounted material names neither context nor file, so the session
> calls of §10.2 (`logs -f`, `exec -i -t`, `port-forward`) refuse when that is the answer, and every client
> path works. The tree document carries the order as it runs (`docs/README_PLATFORM_CONFIG.md`,
> *Cluster addressing*); this section stays the record of the design.

Three points that are not obvious and must be recorded:

* **`address` is a field, not a method.** The 1:1 check has to run for every environment; making the address
  something a method computes invites a call site that forgets to compare it. A field is compared by the
  check and by nothing else, and it means the check needs no kubeconfig lookup of its own.
* **`source` is what makes the answer verifiable.** "Resolved from `platform_config/prod/static.json`" is a
  different claim from "resolved from `--context`", and when the wrong cluster is blamed for something, that
  difference is the debugging session.
* **`from_connection` is a constructor, not a configuration option.** It exists so a library caller holding
  credentials can produce a target (§13.1). Nothing in `src/`'s resolution order (§4.2) ever selects it, and
  that boundary is what keeps kubementat out of the credential business (§3.3).

### 5.1 `from_connection`'s honest limits

Recorded so they are not discovered:

* It **cannot express exec plugins** (§3.3). A library caller using EKS or GKE with a refresh-based credential
  must supply something already obtained.
* It means `ClusterTarget` may hold something sensitive in the `Configuration` it builds, hence the redacting
  `__repr__` — same reasoning `create-secret-from-json` already applies when it refuses to echo values.
* It skips the file-resolution rules, so `kubeconfig_path` is `None` by definition and `source` is `"connection"`.

---

## 6. `Context`

```
Context(environment, team, config, target)
```

Every service class takes this instead of `(environment, team)` plus a `Config` it builds itself.

| today | after |
|---|---|
| `Automation.__init__(environment, team)` then `Config(environment, team)` | `Automation.__init__(ctx)` |
| same for `TektonUtils`, `HelmUtils`, `CiUtils`, `IngressGenerator`, `user_management`, `routing` | all of them |

`config` answers *what to deploy*; `target` answers *where*. Pairing them is the whole argument for this
object: they arrive together or the command does not run, so the pair cannot drift. §8 was designed to check
at runtime what `Context` makes structural.

> **Two shapes arrived in the code that this signature does not show, both measured at HEAD.** (i) **`config`
> is always there, even for a command with no team.** `Context.build(environment, team=None)` resolves the
> cluster and hands back the environment's `Config`; a team-scoped read from that object raises rather than
> inventing a path (`Config._team_scoped_path`, `config.py`). That reverses the ruling ticket 2 left behind
> ("`Config` always needs a team") at `86b1aed`, and it is what keeps §6.4's commands honest: they take no
> `--team`, and they are not handed a configuration that was built from a name nobody gave. (ii) **The object
> carries a fifth field, `resolution_report`,** which is not in the signature above. It is the §9.5 lines as
> data rather than as output, so the decorator prints what the check decided instead of asking again; it is
> `compare=False`, because a report is not part of what a `Context` *is*. Related, and the same measure:
> `require_team` and `require_target` exist (`cluster.py`) as the guards that say what the nullable halves
> mean — `require_team` at three call sites (`tekton_utils.py:1020`, `ci_utils.py:78`,
> `user_management.py:316`), `require_target` where the client is actually built and at the two plan
> call sites (`kubernetes_utils.py:364`, `automation.py:2266`, `ci_utils.py:121`).

### 6.1 Why not pass the target alongside `(environment, team)`

The alternative — keep every signature and add a `target` argument — was rejected on evidence rather than
taste. Six classes build six `Config` instances, and `routing.py:275-278` already apologises for one of them
in prose:

> *"Two `Config` reads rather than one cached value: the namespace and the class come from the same file and
> the same instance, so the pair is at least internally consistent, but it is a second instance from the one
> `HelmUtils` builds."*

Under a cluster-per-environment model that confession becomes a defect: six independent resolutions of "which
cluster", and an identity check that runs six times per install. `Context` collapses that to one, and puts the
check before any service exists.

### 6.2 Where it is built

One decorator, `@kmt_context`, applied under each command's arguments — the same shape `helpers.team_option`
already uses:

```
@click.command(name='install', ...)
@click.argument('environment', envvar='ENVIRONMENT')
@team_option()
@kmt_context
def install(ctx, environment, team, ...):
```

**Why per-command and not on the Click group.** The group runs before argument parsing, so it cannot see
`--context` — which is layer 1 of the resolution order (§4.2). Building the `Context` in `cli()` would resolve
the cluster from process variables alone and silently drop the flag precedence this spec chose. That is not a
preference; it is forced by §4.2.

The decorator is also the only place the following happen:

* resolution, in the order §4.2 defines,
* the 1:1 check (§8),
* the resolution report — *which cluster, from what, verified against whom* — printed before any write.

### 6.3 Deletions in `src/cli/kmt.py`

`src/cli/kmt.py:30-42` currently defines a second `Config` class holding exactly one field
(`kubementat_main_dir`) and assigns it to `ctx.obj`. Verified exhaustively:

* **no command reads it.** 32 commands take `config` via `@click.pass_obj` and none of them touch it.
* **nothing in `src/` reads `kubementat_main_dir` from the context object.** `utilities.py:16` carries a
  comment claiming commands need it; that comment is false and is corrected.
* it is referenced by exactly two tests, and they are asserting an invariant rather than the class:
  `test_import_hygiene.py:94-96` (CLI root == checkout root) and `test_config_path_resolution.py:118-122`
  (CLI root follows `KUBEMENTAT_HOME`). Both exist to prevent the D11 class of bug where a `kmt` installed into
  site-packages drives the wrong checkout.

Therefore: the class is deleted, the 32 `@click.pass_obj` are deleted with it, and **both tests are rewritten
against `default_kubementat_main_dir()`** — same invariant, same failure if the CLI and the library ever drift.
The two docstrings that describe "the other `Config` in the tree" are corrected rather than left standing.

### 6.4 `Context` and team-less commands

`kmt uninstall`, `cluster-capacity`, `pvc-usage` and the diagnostics commands have no team semantics. Under
`@kmt_context` they continue to have none — a command that cannot act on a team does not accept a
`--team` that resolves to a default and then goes unused.

---

## 7. Client binding

The mechanical core, and the requirement that makes decision 4 non-negotiable.

### 7.1 The rule

**No `config.load_kube_config()` anywhere in `src/`.** No `client.<Name>Api()` constructed with no arguments.
Both facts are pinned by guard tests (§12.3).

Instead:

```
src/kubementat/cluster.py            ClusterTarget, Context, EnvironmentRegistry
src/kubementat/kubernetes_utils.py   KubernetesUtils(target, *, api_client=None)
```

> **The signature runs wider than this as of `97e1164`:** `KubernetesUtils(ctx_or_target, *, api_client=None)`
> (`kubernetes_utils.py:328`). A `ClusterTarget` is accepted, exactly as written above, and so is a `Context`
> — which is what every command has in hand, and what makes the argument of six constructors match the
> argument of §6.1. Anything else raises `TypeError` rather than resolving a cluster to make itself work. This
> is not the constructor loosening back into the resolution order §4.2 owns: given a `Context` it takes
> `ctx.target` and does nothing else with the `Context`, and given a target it starts from that. Both shapes
> carry exactly one cluster and neither one resolves, so the rule that decides something here — *this class
> binds, it does not resolve* — holds on both paths and the wider shape is what §12.1's injection needs
> anyway: a test addressing a cluster it never contacts hands over the target, the same way
> `KubernetesUtils(TARGET_DEV, api_client=recorder)` does.

`KubernetesUtils` resolves nothing about *which* cluster — it is given a target and builds clients from it:

```
ApiClient(target.client()) → client.CoreV1Api(api_client), client.AppsV1Api(api_client),
                             client.RbacAuthorizationV1Api(api_client),
                             client.CustomObjectsApi(api_client)
```

constructed **lazily**, per API object, on first use.

### 7.2 Why lazy

`Automation.__init__` (`automation.py:275-279`) currently loads a kubeconfig eagerly, which means `kmt
initialize` — a command that writes JSON files and touches no cluster — fails on a host without a kubeconfig.
The research report flags this separately (`automation.py:96` / review 1.3), and laziness fixes it without
separate ceremony. It is also required by the check's placement: an eager constructor would pay the kubeconfig
resolution for environments it will never address.

> **`kmt initialize` is served by a second shape of the same argument, measured at `365c10a`:**
> `Context.for_configuration(environment, team)` (`cluster.py:973`). It resolves nothing, builds no
> `ClusterTarget` (`target=None`), prints no report, and runs no 1:1 check — not because §8.3 has an exception,
> but because there is no claim to compare. `Automation` is built on it (`kubementat.py:23`), so the command
> that writes the very file a resolution reads is not the strictest command in the product for the least
> benefit. Its cost is a state that must not be mistaken for "no cluster": `require_target` is what says so at
> the one place a caller might try, and `kmt initialize` therefore cannot address a cluster by accident.
> Nothing in §7.2 argues against it — the same sentence that makes the constructor lazy makes this object
> legal — but §7.2 alone would not tell a reader it exists.

### 7.3 Why `api_client=None` is on the signature

A seam, deliberately. Tests inject a recorder instead of patching a global (§12.1), and the library API can
supply a pre-built client. It is a parameter, not a module-level "current target" — because a module-level
target is the same global state under a different name, and it would break the library use case that is the
reason this design exists.

### 7.4 What this buys the library

`ClusterTarget` and `Context` are values. Nothing in the construction path reads or writes process state, so
two `Context` objects for two clusters can be alive in one process at once. That is the property §13.2 says
the future API needs, and it is bought here rather than paid for later, because later it would be a sweep of
`kubernetes_utils.py` instead of a constructor.

---

## 8. The 1:1 check

### 8.1 What it compares

The **server address** (§5's `address` field), resolved locally from the kubeconfig. No cluster contact.

> **Measured at HEAD, the equality is a trimmed string and one trailing slash, not a raw string compare**
> (`_normalise_address`, `cluster.py:166`): whitespace and exactly one trailing `/` are dropped, so
> `https://host:6443/` and `https://host:6443` are one cluster, and `https://host/api` is still not
> `https://host`. It is an implementation detail that has an honest consequence, which is why it is named here:
> the identity §8.1 defends is "the server string minus one slash", and a server that differs only by a second
> trailing slash, or by a different port spelling, is treated as two. This is not the fingerprint's absence —
> that decline is recorded below and stands — it is what the string compare actually does.

The alternative — the cluster's own fingerprint (the `kube-system` namespace UID, which is unchangeable) — is
strictly more correct: it catches the case where one cluster is addressed through two DNS names or a load
balancer, which a string comparison cannot see. It was **declined for now**, and the decline is recorded as a
limitation rather than a design: an address-only check can be defeated by a cluster that has more than one
name. Adding the fingerprint later is an extension of `address`, not a change of model, which is why the field
carries a value that has a single honest meaning.

### 8.2 What it is compared against

Every other environment in the same `platform_config` — because the invariant is *"no two environments address
the same cluster"*, not *"this environment matches this kubeconfig."*

Enumeration is defined as: **directories at depth 1 of `platform_config` that contain a `static.json`.** That
falls out of the existing shape without new rules — `platform_config/{env}/{team}/static.json` is depth 2, and
`kubementat_components/` (`NON_TEAM_ENVIRONMENT_DIRECTORIES`, `config.py:43`) has no `static.json` at all.

### 8.3 When it runs

In `Context.build()`, for every command (§18 in the decision list). Simple, and the reasoning for not being
clever about it: a check that runs "only on writes" needs a notion of purpose on `Context`, and the difference
between that and running everywhere is N local kubeconfig reads — no cluster contact, no latency worth the
complexity. Reads being cheap is not worth a class of bug where the check is skipped on the read that turned
out to matter.

### 8.4 What it refuses, and what it says

* **Collision → hard block, no override flag.** The message names both environments, both addresses, and the
  command that shows the whole picture (`kmt environments list`). Nothing in v2 says "I know these are the
  same cluster and I accept the consequences."
* **Unresolvable environment → reported and skipped.** An environment whose context is missing, whose
  kubeconfig is gone, or whose file cannot be parsed does not block the command; it appears in the report as
  *not verified*, by name, with the reason. A check that silently omits environments would be a check that
  lies about its coverage. A check that blocks on them would make `kmt routing status dev` depend on
  `staging`'s config being valid.

The report line is therefore allowed to say exactly one of:

```
cluster dev: https://10.0.1.9:6443 (platform_config/dev/static.json) — verified against 1 other environment
cluster dev: https://10.0.1.9:6443 (flag --context) — not verified: staging could not be resolved (no such context 'staging-eu' in ~/.kube/config)
cluster dev: https://10.0.1.9:6443 (service-account /var/run/secrets/kubernetes.io/serviceaccount) — not verified: prod is the cluster this command is running on (https://10.0.1.9:6443)
```

Anything else is an overstatement of what was checked, and this project's own rule about not claiming more
than was measured applies here as much as it does to a lane run.

> **The third line is §14's rule, written down rather than implied.** It was measured at `eca4c31`. The
> exclusion is not a *not verified* about a failure to read — nothing failed; kubementat declined to compare
> one environment against itself. It is reported in the *not verified: <reason>* shape rather than as a silent
> subtraction, because a line that said *verified against 1 other environment* would claim a comparison that
> deliberately did not happen, and `running_cluster_address()` feeds the refusal and the report from one
> function, so the two cannot disagree about whether they looked at each other. A command that is not standing
> on a cluster gets the same two shapes it got before.

### 8.5 What the check does *not* protect

It is a check between environments kubementat was configured to know about. A person who edits
`~/.kube/config`, changes a context's server, and then installs — is checked, because the address comes from
the file at build time — but only against *other declared environments*. Nothing here protects against
kubernetes-level surprises, and no design in this document claims to.

---

## 9. CLI surface

### 9.1 `environment` becomes the universal first positional argument

Every command that touches a cluster takes `environment` first, as a positional argument with `envvar='ENVIRONMENT'`.
Ten commands change shape — the nine below, plus `helm-version` after the table:

| command | today | after |
|---|---|---|
| `analyze-deployment` | `<namespace> <deployment>` | `<environment> <namespace> <deployment>` |
| `pod-images` | `<namespace>` | `<environment> <namespace>` |
| `cluster-capacity` | *(nothing)* | `<environment>` |
| `pvc-usage` | `<environment>` (echoed only) | `<environment>` — now actually used |
| `deploy-ssh` | `<namespace> <deployment> [--container]` | `<environment> <namespace> <deployment> [--container]` |
| `deploy-logs` | `<namespace> <deployment> …` | `<environment> <namespace> <deployment> …` |
| `service-account-token` | `<namespace> <service-account>` | `<environment> <namespace> <service-account>` |
| `create-secret-from-json` | `<namespace> <secret-name> <json-file>` | `<environment> <namespace> <secret-name> <json-file>` |
| `tunnel pod` | `<namespace> <pod-name>` | `<environment> <namespace> <pod-name>` |

Two existing commands get into line rather than out of it: `create-ingress` (environment currently
`required=False, default=''`, positioned fifth) and `helm-version` (environment second). Both are corrected so
the rule "first positional is the environment" holds without exception.

> **Counted against the diff at `4f2eb26`, the ten are not the ten this table names.** Ten commands' argv
> changed shape, and they are the eight table rows **except** `pvc-usage`, plus `create-ingress` and
> `helm-version`. `pvc-usage` is the row the table overstates: its environment was already the first positional
> and already `envvar='ENVIRONMENT'`, so its *shape* did not move — what moved is that `@kmt_context` now
> resolves it and reports it, which is the behaviour its old comment apologised for (`kubernetes.py:181-190`).
> Reading the column as a census of shape changes therefore double-counts one command and omits two. The rule
> itself is met: every command that touches a cluster takes the environment first (`test_cli_surface.py`'s
> positional table pins 11 commands).

> **One family does not meet the rule as written, and it is the one family that already had an option.** The
> three `kmt users` team-account commands (`create-team-account`, `create-team-account-read-only`,
> `delete-team-account`) take the environment as `--environment`, not as the first positional
> (`users.py:26-31`, `:74`, `:86`, `:98`) — the positional that leads is the service-account name. They touch a
> cluster: they create a service account, bind RBAC, and print a kubeconfig. The sentence above says
> "positioned first, as a positional argument", and these three do not, so RC-5's rule is met everywhere except
> here; they do refuse an environment they were not given, so nothing is guessed, but the surface is not
> uniform. `test_cli_surface.py`'s positional table does not cover them either, which is why the divergence was
> invisible rather than merely deferred. Making them take the environment first would be a breaking argv change
> and no ticket in this epic made it; that decision is not this document's to record as taken.

**Why the namespace stays an explicit positional** rather than being derived from a team: the diagnostics
commands read *component* namespaces (`traefik`, `grafana`, `tekton-pipelines`, `kube-system`), which are not
teams. Deriving a namespace from `--team` would be correct for exactly one of the four namespace sources and
wrong for the rest, and `ci_utils.run_ci_container` using `str(self.team)` as the namespace is the tempting
precedent that makes the wrongness easy to miss. Two things with different jobs stay two things.

**A concrete dependency this creates.** `tekton_ci/tasks/create-secret-from-json.yml:56-60` invokes the command
with three positionals and the Task declares **no environment param**. That Task gains a param and gains the
argument, in the same change. Expect the same shape for any Task that runs a command whose signature moved.
This is the most common kind of collateral in this spec and is called out rather than discovered.

> **Done, and pinned, measured at `4f2eb26`.** `tekton_ci/tasks/create-secret-from-json.yml` declares an
> `environment` param (`:17-20`) and passes it first (`:67-68`), which is the whole of the dependency this
> paragraph asked for. What is not left to discovery: `test_the_ci_task_manifests_pass_the_positionals_their_commands_take.py`
> walks `tekton_ci/tasks/` and runs each Task's command against the command's own signature, so the next Task
> that calls a command whose shape moves fails a test instead of failing at runtime. The other Task that calls a
> `kmt`, `run-tekton-pipeline.yml`, needed nothing — `kmt tekton run-pipeline` already took its environment
> first.

### 9.2 `@kmt_context`

Per §6.2. It replaces `@click.pass_obj` everywhere, and it is what makes the parameter that 32 commands
currently ignore mean something.

### 9.3 No default team

`src/cli/helpers.py:23` (`DEFAULT_TEAM = "dev1"`) is deleted, and `team_option` loses its default. `TEAM`
remains as a process variable.

This is decision 19, and it is the same rule as §4.2 applied one level down. The comment in that file records
why the default existed — *"what the CLI hardcoded before D9… keeping it as the default means every existing
invocation keeps working"* — and the reason it existed (`tunnel-*` commands silently reading `dev1`) is exactly
the bug class v2 refuses. `kmt users` already refuses a team it was not given; the rest of the CLI now agrees.

Cost: more arguments typed, and more `--team` in the CI manifests. Stated, not hidden.

> **Corrected by measurement at `3c9ad23`, and the surface moved further than this section said.** The stated
> cost about the CI manifests did not happen: a grep over `tekton_ci/` found exactly two `kmt` invocations and
> neither takes a team, so no manifest gained a `--team`. The other half of the cost is real, and bigger — the
> census then found that `require_team` was being called at nine places and **six of them were refusing work
> they could not justify**: `helm_utils.py:101`, `ingress_generator.py:134`, `routing.py:228` and
> `grafana_accounts.py:204/246/268` read no `team_static` anywhere in their files. `install_routing` used a
> team for exactly two values (`component_namespace('traefik')` and `INGRESS_CLASS`) and both come from
> `env_static`, and the helmfile it applies is `platform_config/<environment>/kubementat_components/…`, which
> is environment-scoped — so **`kmt routing install` needs no team**, the e2e harness line that had been
> running it that way was right, and the code was wrong. Three guards remain, each next to the line that earns
> it (`tekton_utils.py:1020` reads `team_static.PIPELINE_NAMESPACE`; `ci_utils.py:78` supplies the `-n` value at
> `:133`; `user_management.py:316` interpolates the team into a context name at `:315`) and the last two say
> so in the message rather than claiming a file they never read. Consequently **eight commands lost a `--team`
> they could not act on** — `helm apply`, `helm-version`, `component install`, `component uninstall`,
> `routing install`, `create-ingress`, and both `users create-grafana-*` — and `team_option` went from 16 uses
> to 4. A reader of this section alone will not know that, which is why it is stated here and in
> `docs/README_PLATFORM_CONFIG.md` rather than left in a commit message.

> **Measured further, and two things that happened are not in the note above.** First, `126cc15` removed four
> more `@team_option()`s than the eight listed: `tunnel grafana`, `tunnel tekton`, `tunnel openbao-ui` and
> `routing status`. All four resolve their namespaces through `component_namespace` and read no `team_static`,
> so the flag guarded nothing and its only effect was printing `TEAM:` — deleting it is §6.4, and `--team` now
> errors at the parser. The census across the whole surface is therefore **16 uses to 4**, i.e. twelve commands
> lost a team they could not act on, not eight. Second, decision 19 was applied to *clusters* as well as to
> teams, which §9.3's text says nothing about: `default='dev'` was removed from **seven** environment arguments
> (`kubernetes.py:172`, `tunnels.py:65/82/101`, `users.py:145/167` plus the `--environment` option at
> `users.py:26`) in the same commit that deleted `DEFAULT_TEAM` (`3c9ad23`). `default='dev'` and
> `DEFAULT_TEAM = "dev1"` were one decision wearing two flags, which is why they could not be removed apart.
> Measured cost, in the numbers the tickets actually returned: `3c9ad23` 1129 → 1140 tests (+11, one test
> deleted, eight had to name a team); `126cc15` 1151 → 1146 (five collected cases vanished from
> command-parametrised lists, no assertion deleted); `dc28ea2` 1146 → 1169 (+23). The repair cost **29 minutes
> and ~80k output tokens for ticket 12b**, against **~15 minutes for the read-only census** that told it which
> of the nine guards were real — see `docs/lessons-learned/2026-11-02-cluster-addressing.md`.

### 9.4 `kmt environments list`

The capability is a real primitive — `EnvironmentRegistry.all()` — with one command on top of it.
The name follows what exists: there is no cluster registry, so a command called `kmt clusters list` would imply
an inventory kubementat does not own.

```
kmt environments list
ENVIRONMENT  CLUSTER_ADDRESS          CONTEXT          SOURCE                              CHECK
dev          https://10.0.1.9:6443    k3s              platform_config/dev/static.json     ok
prod         https://10.0.7.4:6443    prod-eu          flag --context                      collides with dev? no
staging      (unresolved)             staging-eu       platform_config/staging/static.json no such context
```

The same primitive backs §8.2, so the command and the check can never disagree about what the environments are.

> **Measured at HEAD, the listing prints a CHECK value that answers nothing, and one it never prints at all.**
> A resolved row's CHECK is the literal `ok` (`environments.py:80-85`); an unresolved row's is the reason
> (`environments.py:86-90`). So the example's `ok` for `dev` is what the code does, and the example's
> **`collides with dev? no` for `prod` is not** — no code path in `src/` produces that string, and the listing
> runs no collision check: `EnvironmentRegistry.all()` resolves every environment and compares none, which is
> why it can answer TD-2 in one local read. The collision question is answered by §8's check, at the commands
> that address a cluster — TD-2's promise is met, and this particular cell is decoration. What the column
> *does* answer, for a resolved environment, is "did you manage to resolve me", which is a weaker question
> than the header invites. Two consequences worth knowing: a root that contains a collision lists both sides
> `ok` (the refusal happens when a command runs, not when you look), and `kmt environments list` builds no
> `Context`, so no §9.5 report is printed for it either.

**Rejected:** `kmt clusters use`. It would re-add ambient state — the exact thing being removed — and it would
make "which cluster" depend on what a previous command did. If `kmt cluster add` arrives later (§2.2), it
arranges a kubeconfig entry; the *selection* is still an environment argument.

### 9.5 The resolution report

Every command prints, before its first cluster write, what §8.4 defines. `automation.py:2190` currently prints
`Kube context: {plan['context']}` where the context comes from `kubectl config current-context`
(`automation.py:2183`). That read is deleted: the answer is a property of the `Context`, not a question asked
of kubectl.

Same for `ci_utils.print_current_context` (`ci_utils.py:100`), whose docstring already concedes
*"`kubectl config current-context` is a read of the local kubeconfig, not of the cluster"* — it becomes a read
of the `Context`, which is what that function was reaching for.

> **Where it prints is stated by the code and not by this section, measured at `2c6d84c`:** the report goes to
> **stderr**, for every command (`click.echo(line, err=True)`, `cli/context.py:100-101`). The reason is §9.1's
> own rule about output shapes, not a new one: stdout is what a pipe and a `jq` consume, and two commands put
> their *result* there — `create-ingress` prints the manifest, `service-account-token` prints a credential.
> A report sharing that stream with the thing being piped would corrupt the thing being piped. The practical
> consequence for a reader and for a test: the report is a claim about the run rather than part of the run's
> output, so anything that asserts it does so against stderr.

---

## 10. kubectl conversion

The surface: **144 mentions of "kubectl" in `src/`, but 34 real invocation sites across 11 files.** The other
~110 are docstrings and help text, and most of them are accurate. The three buckets are separated by *why the
call exists*, not by how hard it is.

### 10.1 Converted — reads that decide something

These are the calls where kubementat **branches on the answer**, which is what makes parsing kubectl's text a
correctness risk rather than a style preference.

| site | becomes |
|---|---|
| `routing.py:76`, `traefik_presence.py:90-91` — `get ingressclass` / `get pods -A` | `NetworkingV1Api.list_ingress_class`, `CoreV1Api.list_pod_for_all_namespaces` |
| `automation.py:2381, 2397` — ingressclass get/delete | client, with the 404 tolerance `kubernetes_utils` already implements |
| `automation.py:2444, 2483, 2606, 2623, 2813, 2834` — pvc / crd / generic get+delete | client |
| `automation.py:891` — `cluster-info` | `VersionApi`, or `/healthz` |
| `automation.py:914-919`, `user_management.py:146` — `auth can-i` ×7 | `AuthorizationV1Api.create_self_subject_access_review` |
| `automation.py:2183`, `ci_utils.py:100` — `config current-context` | deleted (§9.5) |
| `kubernetes_utils.py:1065` — `get pod -l … -o json` | `list_namespaced_pod(label_selector=…)` |
| `kubernetes_utils.py:1361` — `api-resources --output=json` | discovery (`DynamicClient.resources`) |
| `helm_utils.py:565, 579` — get/delete PVC | client |

**A live divergence this fixes, not a new claim.** `automation.py` already deletes claims through the client
while `helm_utils.py:565-579` performs the same job with `kubectl get/delete pvc`. Two answers to one question
is the failure this codebase's own documentation names most often; converging them is part of this bucket's
value, independent of anything about clusters.

`DynamicClient.server_side_apply(resource, body, name, namespace, force_conflicts=…)` was verified against the
pinned `kubernetes==33.1.0`. This is worth recording because `finish-python-migration.md`'s risk table states
the opposite (*"The Kubernetes client can't fully replicate `kubectl apply` for CRs → prefer `kubectl apply -f -`
via `shell.run`… and document it as a deliberate exception"*) and was written from assumption rather than
measurement. The exception below is still kept, for a different and better reason.

> **Two measured facts about this table.** (1) **The first row converted later than the rest.** The kubectl
> conversion ticket (`948b721`) left `routing.py:76` and the two `traefik_presence.py` reads running as argv and
> said so; they became client calls in `018050d`, the helm/argv ticket. So for one commit the table's first row
> was not true of the tree, and the guard that had been excusing this module's `kubectl` reads had nothing left
> to excuse (`traefik_presence.py:71`, `:523`). Nothing in the finished tree contradicts the table; only the
> ordering differs, and anyone bisecting between those two commits will find the row empty. (2) **The probe
> record changed shape when the reads did.** `ProbeOutcome` carried `argv`; it now carries `read` (the argv
> *or* the client method, `traefik_presence.py:169`) plus `channel` (`COMMAND_CHANNEL` / `API_CHANNEL`,
> `:659-681`), because after this conversion the same table could be filled by either channel and a report
> that printed "the command line" would print nothing for a client call. §10.1's table describes what the reads
> became, and is right about that; it does not say that the thing which *records* which read answered had to
> change with it.

### 10.2 Stays kubectl — deliberate exceptions

* **`kubectl apply -f <url>`** (`tekton_utils.py:1081`, the pinned Tekton release manifests). It downloads a
  multi-document upstream YAML and applies it. Server-side apply through `DynamicClient` *is* available, and
  converting means owning the download and the document splitting — for a call that currently works, where
  the risk is that a document applies in the wrong order or a status subresource is skipped. Keeping it is a
  decision, documented here, not an oversight.
* **`kubectl logs -f`, `kubectl exec -i -t`, `kubectl port-forward`.** All three are sessions rather than
  calls, and the tree already asserts the rule that they are never captured
  (`tests/test_a_shell_in_a_pod_and_a_log_stream_are_never_captured.py`). Converting them means writing a
  stream bridge and testing it against a fake — real work with no correctness payoff for this spec.

  Two findings to record rather than re-derive: the Python client **does** have port-forward
  (`kubernetes.stream.portforward` over `connect_get_namespaced_pod_portforward`, returning a `PortForward`
  with `.socket(port)`), contrary to the assumption stated in earlier analysis of this area — but the local
  listener and copy loop remain the caller's job. And `logs -f` / `exec` are available through
  `kubernetes.stream.stream`, with the same caveat that terminal raw-mode handling is not free.

  **These calls still carry the target.** A `kubectl` invocation that is not given `--context`/`--kubeconfig`
  is a bug in this design, not a permitted shortcut — same rule as §11, and the same guard test covers both.

> **This is a list of the *why*, not a census of the tree, measured at HEAD.** Five more `kubectl` sites
> survived than the three bullets above name, and each of the four kinds below is one the exception list does
> not cover: `kubectl run` (`ci_utils.py:134`, the CI container, which is a session the operator attaches to),
> `kubectl config view --flatten --minify` (`user_management.py:257`, which must go through the binary because
> it re-inlines certificate authorities), and two `kubectl apply -f -` calls in `routing.py:461` and
> `:530` — a server-side CRD apply piped from helm's output, and the values document applied from stdin. All
> five carry the target, by `kubectl_target_args` or `KubernetesUtils.kubectl_args`, so they are addressed and
> not ambient, which is what this section's last sentence actually requires. The number to trust is the guard
> table's, not this bullet list's: `tests/test_no_kubectl_is_addressed_by_the_process_any_more.py` walks
> `src/` and fails on any invocation that does not appear in its table, addressed or exempt — including a table
> entry that has gone stale, which is what makes the ratchet stricter than §12.3's wording.

### 10.3 The dependency consequence, stated

`kubectl` therefore **remains** in `KUBEMENTAT_DEPENDENCIES` (`automation.py:232`) for all of v2. This spec
does not remove the binary, the dependency check, or the operator's need to have it installed. Anyone reading
"convert kubectl to Python" as "drop kubectl" should read this line instead.

---

## 11. helm and helmfile

Four cluster-touching call sites go through binaries rather than the client:

| site | |
|---|---|
| `helm_utils.py:118` | `helmfile apply` |
| `helm_utils.py:39` | `HELM_LIST_RELEASES_ARGV` (`helm list --all-namespaces`) |
| `helm_utils.py:200`, `automation.py:2430` | `helm uninstall` |

Both binaries accept the target: `helmfile --kube-context/--kubeconfig` (1.7.0) and
`helm --kube-context/--kubeconfig` (v4.3.1) — both measured on this machine.

**The rule: argv flags only, never environment.** Local, cluster-touching helm calls (`helm repo update`,
`helm repo list`, `helm show crds`, `helm search repo`, `helm version`, `helm diff version`) are exempt and get
nothing.

> **Three measured details of this rule, as of `018050d` and `eca4c31`.** (1) The flags are written in one
> place, `helm_target_args` (`core/identity/target.py`, moved there from `kubernetes_utils.py` by
> P2-4), and it spells the context `--kube-context` because helm
> rejects kubectl's `--context` — the same sentence `kubectl_target_args` writes in kubectl's spelling, and the
> reason a guard that recognised only one spelling would report an addressed call as unaddressed. (2) **The
> exempt list the guard declares has a seventh entry:** `helmfile --version`, which the dependency check runs
> and which asks no cluster (`tests/cluster_parity.py:65`). It is exempt for the same reason `helm version` is,
> and the list lives in `tests/cluster_parity.py`, shared with the §12.2 reducer, rather than inside the guard
> test — so there is one answer to "which helm calls are local" and not two. (3) **A target that names neither
> context nor file is refused here, not merely reported.** `helm_target_args` raises `ClusterNotConfiguredError`
> when `target.context` and `target.kubeconfig_path` are both `None`, which is exactly §14's in-cluster answer.
> So the sentence §5's note states — *client paths work, sessions do not* — is incomplete on its own: a
> `ClusterTarget` resolved from the mounted service account drives every client read and write this spec
> converts, and **also** refuses `helmfile apply`, `helm list`, `helm uninstall`, `kubectl logs`/`exec`/
> `port-forward`, `kubectl run` and `kubectl config view`. The command that still cannot run inside its own pod
> is `kmt install`, which is not a client-path command. That is the honest size of §14's last layer, and it is
> stated in §14 for that reason.

The reason is not style. `helm_utils.py:81-85` already mutates `os.environ` for Grafana's credentials, so the
env-var route is right there and it is the same disease `load_kube_config` had: process-wide state that makes
two clusters in one process impossible, which is exactly the future this spec is enabling. And argv is what
makes the choice visible in the log the operator reads.

**The failure this is guarding.** Cluster operations now happen over two channels — the Python client, bound
to a `ClusterTarget`, and the helm binaries, given a target as an argument. If one is wired and the other is
not, the result is the worst failure this design can produce: **the namespace created on cluster A and the
release applied on cluster B.** Silent, partial, and only visible at uninstall. Hence a guard test (§12.3),
not a review habit.

---

## 12. Test strategy

### 12.1 Injection, not patching

Five places in `tests/conftest.py` and four test modules neutralise the global with:

```python
monkeypatch.setattr("kubernetes_utils.config.load_kube_config", lambda *a, **k: None)
```

plus `conftest.py:542`, which patches `KUBE_CONFIG_DEFAULT_LOCATION` because the library captured the constant
at import time and `$KUBECONFIG` in the fixture arrives too late. Every one of those exists **only because the
code read global state**. They are deleted, not rewritten, and replaced by:

```python
KubernetesUtils(TARGET_DEV, api_client=recorder)
```

`ClusterTarget`'s plain constructor is trivial to build in a test, so no kubeconfig file and no `KUBECONFIG`
monkeypatch is needed to address a cluster that is never contacted. This is the change that makes ~30 tests
independent of the machine running pytest.

### 12.2 The cluster joins the parity record

`tests/cluster_parity.py` reduces both sides of every comparison to:

```
ClusterOperation(verb, kind, namespace, name, facts)
```

**and carries no cluster field.** That was fine while one process meant one cluster. It is now the one place a
test could hide the exact bug this spec exists to prevent: a `create secret` on `dev` and a `create secret` on
`prod` reduce identically, so a regression that edits the wrong cluster passes.

So: `cluster` becomes part of the record's identity, and the reducer must be able to see it on **both** sides.
The shims already accept `--context` and `--kubeconfig` (`tests/shims/_recorder.sh:69`) and **discard them** —
so today the harness cannot tell which cluster an operation targeted, and never could. Making the reducer
recognise them is what lets a parity test assert that the client path and the kubectl path hit the *same*
cluster, which is §11's failure mode, tested.

The module's own rule is the argument for doing this rather than working around it: *"anything the reducer
cannot recognise raises `ParityError` instead of being skipped."* A target it cannot see is exactly that.

**Cost, stated:** adding a field to the record touches every parity test's expected set once, because
mismatches that were invisible become failures. That is the point of the change, not a side effect.

> **Measured at `8fe2b68`, and the cost paid for itself before the ticket closed.** The first real divergence
> the record caught was not a planted mutation: three parity files were addressing their own client calls as
> `cluster_fixture.TARGET_DEV` — context `dev` — while the other half of the same comparison ran against a
> fixture declaring `fixture`. One comparison, two clusters, and the record could not see it until the field
> existed. That is this section's failure mode arriving through a door nobody opened on purpose, and it is why
> the cluster is recorded on the client side as well as the argv side (`ClusterOperation.cluster`,
> `tests/cluster_parity.py:396`): a comparison whose two halves name different clusters is now a `ParityError`
> and not a passing test. What moved on the harness side is the reducer, not the recorder: the shims always
> recorded full argv (`tests/shims/_recorder.sh:36-41`), so `--context`/`--kubeconfig` were already in the log;
> what changed is that the reducer reads them out of it (`cluster_identity`, `tests/cluster_parity.py:129`)
> instead of stepping over them, and the shim's own flag-skipping list had to learn `--kube-context` for the same
> reason — otherwise the reducer is handed a flag's value as if it were the verb.

### 12.3 Guard tests

This project pins decisions with tests rather than prose, and this spec adds three:

1. **`config.load_kube_config` is absent from `src/`.**
2. **No `client.<Name>Api()` is constructed with zero arguments.**
3. **Every `kubectl` and `helm`/`helmfile` invocation that touches a cluster carries `--context` or
   `--kubeconfig`** — with the local-only helm calls in §11's exempt list declared in the test, the same way
   `test_no_cli_command_starts_a_script_by_path.py` declares the guard it enforces.

And two rewrites rather than new guards: the `default_kubementat_main_dir()` assertions of §6.3, and the false
comment at `utilities.py:16`.

> **What "absent" means is the interesting part, measured at `97e1164`.** The three rules are enforced by
> walking the syntax tree of every shipped file under `src/`, and the first rule matches the **called name
> exactly** (`test_no_client_is_built_without_a_target.py:45-60`). So `ClusterTarget.client()`'s one call —
> `kubernetes.config.load_kube_config_from_dict` — is not a violation, because it is a different name, and it is
> the call that makes `from_connection` (§5.1) and §13.1's library caller possible at all. That is why this
> guard declares **no exempt list**: a substring rule would either flag the one loader the design wants or be
> loosened until it caught nothing. "Absent" therefore means *no call expression whose callee is
> `load_kube_config`*, in any shipped file — imports and prose are not calls, and nothing here grants an
> exemption. Rule 2 is shape-based for the same reason: any zero-argument call to a name ending in `Api` fails,
> not just the four `Api` classes this spec names. Rule 3 is a **ratchet in both directions**: the
> `kubectl`/`helm` tables list every cluster-touching invocation in the tree, and a stale entry — a line that
> was converted, renamed or deleted — fails the test as loudly as a missing flag would, so the table cannot rot
> silently. Three rules, four modules: rules 1 and 2 share a file because they are the same sentence in the
> negative (§7.1), and rule 3 is split across `test_no_kubectl_is_addressed_by_the_process_any_more.py` and
> `test_no_helm_call_is_addressed_by_the_process_any_more.py` because the two channels read argv differently.
> The exemption §11 names lives in `tests/cluster_parity.py`, shared with the reducer, so the guard and the
> comparison cannot disagree about which helm calls touch a cluster — which is again §13's one-answer rule
> rather than a guard declaring its own list.

---

## 13. The library contract

### 13.1 What the future API needs

A service that manages many clusters needs three things, and this spec supplies all three:

* a cluster is a **value** it can construct without touching a filesystem (`from_connection`, §5.1);
* a cluster and its configuration arrive **together** (`Context`, §6);
* two clusters can be **addressed in one process** without interference (§7.4).

### 13.2 What this spec deliberately does not give it

**Fan-out.** `kmt status --all` is not here, and one command remains one cluster. But the design keeps the door
open in the only way that matters: nothing in the construction path is process-global, so a fan-out is a
scheduling problem rather than a rewrite. Had selection been built on the ambient-context model, fan-out would
cost a sweep of `kubernetes_utils.py` (1641 lines) later, and the first two clusters touched would silently
share the last-loaded configuration.

---

## 14. Last item of the plan: in-cluster resolution

Deferred by decision 17, and deferred for a reason that is a defect rather than a preference.

**Measured:** `kubernetes.config.load_kube_config()` in the pinned 33.1.0 has **no in-cluster fallback** — the
symbol does not mention `incluster` and there is no `ConfigException` branch. `tekton_ci/tasks/create-secret-from-json.yml`
declares no `KUBECONFIG` and no `serviceAccountName`. Therefore `kmt kubernetes create-secret-from-json` running in that
pod calls `load_kube_config()` against a kubeconfig that does not exist and raises before reaching the cluster.

So this path is not "deferred while it still works." It is **already broken and nobody has noticed**, which is
consistent with `finish-python-migration.md` row 1 stating FIN-50 is *unverified*: nobody has started that pod.
Saying it plainly is better than implying that the design leaves something that currently functions.

> **Annotating this section as of `eca4c31`, because it is a claim about a commit and not about the library.**
> It was measured at `d245e4b` and it is true of `d245e4b`: `load_kube_config()` in `kubernetes==33.1.0` raises
> `ConfigException: Invalid kube-config file. No configuration found.` against that pod's kubeconfig-less
> filesystem, so the failure was a Python exception and nobody was watching. **At HEAD the same pod hits a
> named refusal, not a `ConfigException`** — `Context.build` raises `ClusterFileUnreadableError` or
> `ClusterNotConfiguredError` before any loader is asked (`Context.for_configuration` never builds a target for
> `initialize`). Two things survive the distinction: the library fact above, re-measured with
> `inspect.getsource` at `eca4c31`, and the consequence — that pod still cannot reach the cluster, which is the
> only reason "broken" mattered. What changes is the sentence's tense and the commit it points at.

The fix, at the end of the plan:

```
ClusterTarget.from_service_account()   # token + CA from the mounted paths, host from KUBERNETES_SERVICE_HOST
```

placed **last** in the resolution order (§4.2), so a pod that does have an explicit `CLUSTER_KUBE_CONTEXT` uses
it and in-cluster is never a silent winner.

> **Implemented at `eca4c31`, with a signature this block does not carry and a limit that needs stating.**
> `ClusterTarget.from_service_account(environment, *, mount_root=None)` (`cluster.py:403`) — **not** the
> zero-argument constructor drawn above. `environment` is required because §5 says a value must name its
> environment, and that is what makes the `source` string `"service-account"` attach to the right row of a
> listing. `mount_root` is not an operator-facing option and not a configuration key: it is the file seam that
> stands where §12.1's `api_client` seam stands for API objects, so a test can point the constructor at a
> directory of its own instead of patching the library's path handling. Its other detail: the host comes from
> `KUBERNETES_SERVICE_HOST` *and* `KUBERNETES_SERVICE_PORT` (default 443), where the block above names only the
> host. **The limit, which no other section states:** the layer makes every client path work and leaves the
> argv channel's rules exactly as §10.2 and §11 wrote them, because the target carries no context and no
> file. `helmfile apply`, `helm list`, `helm uninstall`, `helm diff`, `kubectl logs`/`exec`/`port-forward`,
> `kubectl run` and `kubectl config view` all refuse such a target with `ClusterNotConfiguredError` naming the
> source. That means §14's last layer is honest about what it fixes — a pod that calls the client reads or
> writes the cluster it is standing on, which is what was broken here — and does not make `kmt install`,
> `kmt uninstall` or a tunnel runnable from inside that pod. Fixing that half would need a kubeconfig-shaped
> credential, which is exactly the line §3.3 refuses to cross.

**The sharp edge, which needs a rule rather than a note.** In-cluster is the only resolution path where the
target is the cluster the code is running *on*. Combined with §8.4's hard block, a pod that resolves
in-cluster and an environment that resolves to the same address are one cluster — so the check would report a
collision against itself and refuse to install. The rule: **an environment whose resolved address equals the
running cluster's own address is excluded from the comparison, and the report says so.** Otherwise the check
that protects operators from the wrong cluster would prevent kubementat from touching its own.

---

## 15. What changes in the tree, in prose

* **New:** `src/kubementat/cluster.py` (`ClusterTarget`, `Context`, `EnvironmentRegistry`);
  `src/cli/context.py` (`@kmt_context`); `kmt environments list`.
* **Changed signatures:** `KubernetesUtils`, `Automation`, `TektonUtils`, `HelmUtils`, `CiUtils`,
  `IngressGenerator`, `user_management`, `routing`, `grafana_accounts`, and the command bodies that build them.
* **Deleted:** `config.load_kube_config()` and every bare `client.*Api()`; `class Config` in `cli/kmt.py` and
  its 32 `pass_obj`; `DEFAULT_TEAM`; `kubectl config current-context` in both call sites; the ~15 converted
  kubectl sites in §10.1.
* **Moved:** `apply -f <url>` (stays kubectl, gains the target), the three session calls (same), and the
  cluster-touching helm calls (gain argv flags).
* **Comments that become false and must be rewritten, not left:** `kubernetes.py:178-181` (the "inventing a key
  would be wrong" paragraph — this spec invents it on purpose), `utilities.py:16` (false),
  `ci_utils.py:93-97` (the read it describes is now a `Context` property), `routing.py:275-278` (the "second
  instance" apology is resolved), `helpers.py:21-24` (the `DEFAULT_TEAM` rationale), and
  `test_no_test_loads_config_from_the_checkout_root.py:34` (the "other `Config`" sentence).
* **Docs that become wrong:** `docs/README_PLATFORM_CONFIG.md:3-4` (the environment definition), the CI Task
  manifests named in §9.1, and the `Non-goals` row of `finish-python-migration.md` that deferred this work.

> **The six comments above were checked line by line at HEAD, not assumed.** All six are rewritten, and where
> each one landed: `kubernetes.py:181-190` (`pvc_usage` — the paragraph that justified *ignoring* the environment
> is gone, and the replacement names §4.1's key and §9.5's stderr line instead of apologising); `utilities.py:16-20`
> (`run-ci-container` — the claim that commands need `kubementat_main_dir` from a context object is replaced by
> the fact that there is no longer one, `6d3b785`); `ci_utils.py:10-11` and `:117-121` (`print_current_context`
> is deleted outright, so the comment at the old `:93-97` no longer has a function to describe, `2c6d84c`);
> `routing.py:291-299` (the "second instance" apology is resolved and says why the read is now one, not two);
> `helpers.py:3-13` (`DEFAULT_TEAM`'s rationale now explains why it was removed, and covers the `default='dev'`
> half of the same habit too, `3c9ad23`); and
> `tests/test_no_test_loads_config_from_the_checkout_root.py:30-40`, whose sentence about "the other `Config` in
> the tree" now says there is none. The rule §15 states — rewrite, do not leave — held everywhere it was tested.
> One of them (the `routing.py` apology) is the kind this bullet list can only point at: it was true of a
> design, false of a tree, and it took the whole of §7.1 to make the sentence unnecessary.

---

## 16. Requirements

| id | requirement | decided by | verified by |
|---|---|---|---|
| RC-1 | every command's cluster is determined by kubementat configuration, never ambient state | §4.2, §7.1 | guard tests §12.3 |
| RC-2 | no two environments address the same cluster | §8 | unit: every pair, collision and non-collision; TD-1 |
| RC-3 | a collision blocks the command and writes nothing | §8.4 | unit; a lane assert once one exists |
| RC-4 | the check reports environments it could not verify, by name and reason | §8.4 | unit: one environment unreadable |
| RC-5 | `environment` is the first positional of every cluster-touching command | §9.1 | `test_cli_surface.py`, and the CI manifests that call them |
| RC-6 | no command guesses a team | §9.3 | existing `users` tests, extended to the rest of the surface |
| RC-7 | a helm/helmfile call that touches a cluster carries the target | §11 | guard test §12.3, with the local-call exemption declared |
| RC-8 | the parity record carries the cluster | §12.2 | every parity test, which moves once |
| RC-9 | two targets can coexist in one process | §7.4 | unit: two `Context`s, clients do not interfere |
| RC-10 | `kmt environments list` answers TD-2 | §9.4 | new test module |

> **What each `verified by` cell means at HEAD, read as measured rather than as written.**
>
> * **RC-1** — true. The §12.3 guards, plus `test_no_client_is_built_without_a_target.py`, which is what makes
>   §7.2's laziness checkable: constructing is legal, asking for a client is the sentence.
> * **RC-2** — **half true.** `tests/test_two_environments_that_address_one_cluster_are_refused.py` covers every
>   pair, collision and non-collision, and the lane asserts the collision as well. The *two different clusters*
>   half of TD-1 is not covered and cannot be on this rig — see the note under §1.2. Reading "TD-1" in this cell
>   as "TD-1 passed" reads more than the lane proved.
> * **RC-3** — true, and stronger than the cell. The lane assert the row was waiting for exists (`2c28eca`:
>   `two-environments-one-cluster-are-refused`, then `refused-collision-install-wrote-nothing`, which reads the
>   release set back and proves the namespace the install would have created does not exist). So "a lane assert
>   once one exists" is stale prose for a satisfied requirement.
> * **RC-4** — true, plus the lane's `unresolvable-environment-is-reported-and-skipped`.
> * **RC-5** — true for the 11 commands `test_cli_surface.py` pins, plus the CI Task check
>   (`test_the_ci_task_manifests_pass_the_positionals_their_commands_take`, inside `test_cli_surface.py`).
>   It does not cover the three
>   `kmt users` commands that still keep the environment as an option — §9.1's note says where the rule stops.
> * **RC-6** — true. `test_the_users_commands_refuse_a_team_they_were_not_given.py`,
>   `test_the_commands_that_refused_a_team_they_do_not_read.py` and
>   `test_the_commands_refuse_an_environment_they_were_not_given.py` extend the users pattern across the surface.
> * **RC-7** — true, with the exempt list declared once in `tests/cluster_parity.py` and imported by the guard.
> * **RC-8** — true, and it caught a real bug: three parity files were comparing `dev` against `fixture` with
>   the record unable to see it (`8fe2b68`, §12.2's note).
> * **RC-9** — true in substance, tested in a slightly different shape than the cell implies.
>   `test_two_clusters_can_be_addressed_by_one_process_at_once.py` addresses two clusters through two
>   `ClusterTarget`s and two `KubernetesUtils`, asks `dev` again after `prod` is built, and asserts that no
>   process-wide `Configuration` was installed. Two live `Context`s is §7.4's sentence, not the test's shape;
>   the property is identical because a `Context` adds resolution and nothing else.
> * **RC-10** — true: `test_the_environments_command_lists_what_the_check_resolves.py`, plus lane asserts, and
>   the listing contacts nothing (`fake_binaries`' log stays empty through the run).
>
> One inference to keep out of the table: every row above is verified by tests that run without a cluster,
> except RC-3's writes-nothing half and the TD-2/RC-10 lane asserts, which run against one real k3s. Nothing in
> this table has been verified against two.

---

## 17. Open questions

Flagged rather than answered, because they are not decisions this spec can make alone.

1. **Live preflight.** §8's check is local: it proves no other *configured* environment claims this address,
   and nothing about whether the target is reachable or healthy. `preflight_installed_tekton_api_versions`
   established the pattern of asserting against the cluster before applying — should `kmt install` make one
   live connection first, the way it asserts the Tekton API version? The argument for: a wrong-but-unclaimed
   cluster is still a wrong cluster. The argument against: it adds cluster contact to the 1:1 check's
   zero-cost property.
2. **Where `Context` is validated for write vs read.** §18 chose "always" for simplicity. If the check ever
   becomes expensive — which the fingerprint option in §8.1 would make it — the simplicity may stop being
   free, and this is the assumption that would have to be revisited.
3. **`CLUSTER_KUBECONFIG_PATH` and encrypted kubeconfigs.** **Answered, 2026-11-05: inside, as
   `<environment>/kubeconfig.encrypted.yaml`.** The key stayed a path — kubementat never writes credential
   material, and that boundary was not worth breaking for a tidier tree — but the directory now has a name
   that means *this environment's own kubeconfig*, read ahead of `$KUBECONFIG`. Two things drove the choice.
   The first is precedence: a variable that outvotes a named environment is the same defect the configuration
   layers close everywhere else, and an operator who typed `--environment dev` should not be silently
   re-pointed by a shell in their session. The second is encryption, and it decided the filename: a prepared
   tree declares `*.encrypted.*` for git-crypt, so a file that matches is protected where it lives, while a
   file that does not is plaintext in a directory full of secrets and raises no error to anyone. The trade is
   that kubementat now reads a file it does not write, which is why `config init` was changed to call it
   expected material rather than foreign — a guard that quarantines the file the advice sends you to create
   would be a guard that refuses its own documentation. `~/.kube/config` and `$KUBECONFIG` still answer for a
   tree that carries no file, which is what keeps this an addition and not a break.
4. **The CI pod's own address.** §14's rule (skip the environment that resolves to the running cluster) is
   easy to state and needs the in-cluster constructor to exist before it can be implemented. The ordering is
   forced by the dependency, not chosen.
5. **Does the 1:1 check run against *this* `platform_config` root only?** `KUBEMENTAT_PLATFORM_CONFIG_DIR`
   means one machine can hold more than one root. Two roots that name the same cluster are two claims. The
   check's scope as written is one root, which is probably right and is not measured.

> **Measured after the build, the list is not five equally open questions.** Two are answered, one is partly,
> two are untouched:
>
> * **Q4 is answered and closed.** The constructor exists (`ClusterTarget.from_service_account`, `eca4c31`), it
>   sits last in the order, and the exclusion rule is implemented and reported rather than assumed (§8.4's third
>   report shape). The ordering was forced exactly as written here, and it held.
> * **Q2 got a partial answer, and it is not the one this question expected.** The code did not split validation
>   between write commands and read commands; it split it by *whether there is a claim to compare*. A resolved
>   `Context` runs §8 for every command, as chosen. `Context.for_configuration` — `kmt initialize` — carries no
>   target, so the check has nothing to read and runs none: §8.3's condition, not an exception (§7.2's note).
>   The expense question stays open; the write/read split never became a thing.
> * **Q1's premise is now half-documented, not half-built.** `kmt install` *does* make one live connection
>   before the first write: `validate_cluster_prompt` asks the API server for its version through `VersionApi`
>   and stops if it does not answer (`automation.py:949`, `kubernetes_utils.py:486` — the read that replaced
>   `kubectl cluster-info` in §10.1). So "does install contact the cluster first" has been yes since that
>   conversion. What is still not built is the *identity* half: no live check compares the address §8's check
>   compared, which is what this question was actually asking. `preflight_installed_tekton_api_versions` is not
>   a cluster check and does not become one.
> * **Q3 is answered, on 2026-11-05, and the answer changed behaviour.** See the question above: the
>   environment's own `kubeconfig.encrypted.yaml` is read ahead of `$KUBECONFIG`, and the name was picked so
>   git-crypt's existing `*.encrypted.*` filter protects the contents. `~/.kube/config` and `$KUBECONFIG` still
>   answer for a tree that carries no file of its own, so a tree that never heard of this keeps working. The one
>   thing that had to be changed rather than added is `config init`: its foreign-material guard had to learn
>   that a file kubementat reads is not a file something else wrote.
> * **Q5 is untouched, and the scope is still one root.** `EnvironmentRegistry` takes exactly one
>   `platform_config_dir`, so two roots naming one cluster are still two claims no command will ever see side
>   by side. Nothing measures it, and nothing should pretend otherwise.
>
> Also correct as this question is numbered: *§18 chose "always"* in Q2 is §8.3, not §18 — §18 is the sources
> list, which is where a reader following that cross-reference will end up looking for a decision that is not
> there.

---

## 18. Sources

**Code measured for this document** (all line numbers as of `d245e4b`): `src/kubementat/kubernetes_utils.py`,
`config.py`, `automation.py`, `helm_utils.py`, `tekton_utils.py`, `ci_utils.py`, `user_management.py`,
`ingress_generator.py`, `grafana_accounts.py`, `automations/routing/*`, `src/cli/kmt.py`, `src/cli/helpers.py`,
`src/cli/commands/*`; `templates/environment/static.json.template`;
`templates/environment/kubementat_components/helmfile.yaml.gotmpl`;
`tekton_ci/tasks/create-secret-from-json.yml`; `tests/conftest.py`, `tests/cluster_parity.py`,
`tests/shims/_recorder.sh`, `tests/e2e/harness/config.py`, `tests/e2e/stages/01-provision.sh`,
`tests/test_the_users_commands_refuse_a_team_they_were_not_given.py`,
`tests/test_import_hygiene.py`, `tests/test_config_path_resolution.py`.

**Library facts, verified against the pinned dependency** (`kubernetes==33.1.0`): `new_client_from_config`
signature; `DynamicClient` including `server_side_apply(..., force_conflicts=…)` and `resources`;
`AuthorizationV1Api`; `kubernetes.stream.portforward` / `stream`; the **absence** of an in-cluster fallback in
`load_kube_config`. Binary flags verified against `helm v4.3.1` and `helmfile 1.7.0` on this machine.

**Pre-existing analysis this spec builds on:**
`docs/research/research-kubementat-improvements-post-python-migration-10-27-2026.md` (row N7, and the X3
`kmt doctor` proposal this spec declines to expand); `docs/plans/finish-python-migration.md` (§1.2 and the
Non-goals row that deferred this, plus the risk-table claim about `kubectl apply` that §10.1 corrects);
`TODOs.md` Features ("really implement support for separate environments aka kubernetes clusters");
`docs/README_PLATFORM_CONFIG.md`;
`docs/research/specification-externalize-configuration-10-30-2026.md` §2.2.
