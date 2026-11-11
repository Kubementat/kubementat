# What changed — cluster addressing (`cluster-config-v2`)

A one-page map for reviewing the branch. Spec: [`../specifications/cluster-config-v2.md`](../specifications/cluster-config-v2.md).
Detailed cost/measurement: [`../lessons-learned/2026-11-02-cluster-addressing.md`](../lessons-learned/2026-11-02-cluster-addressing.md).
Points where the implementation and the spec have diverged are appended at the end and are being reconciled
into the spec itself.

**Range:** `e80ac4b` → `d7a4285` · 17 commits · 115 files, +12,514 / −1,840 · 25 `src/` files, 89 `tests/` files (19 new test modules)
**Suite:** 1008 passed / 1 skipped → **1180 passed / 1 skipped** · full e2e lane: 128 assertions, 0 failed

## The one change everything else follows from

An environment is **exactly one cluster**, and kubementat resolves which one before it writes anything —
from its own configuration, not from whatever `kubectl` was last pointed at. Before, `config.load_kube_config()`
was called with no argument and every API object read a process-wide default; after, the cluster is a value that
travels through the call graph in one object.

## New code worth reading first

| file | what it is |
|---|---|
| `src/kubementat/cluster.py` | `ClusterTarget` (frozen: environment, context, kubeconfig_path, address, source), `Context`, `EnvironmentRegistry`, the resolution order, the 1:1 check, the three constructors (`from_kubeconfig`, `from_connection`, `from_service_account`) |
| `src/cli/context.py` | `@kmt_context` — resolves, runs the check, prints the report, hands the `Context` to the command |
| `src/cli/commands/environments.py` | `kmt environments list` — answers "what environments, what cluster", no cluster contact |

Everything else is the migration of what already existed onto those three.

## Configuration

Two keys in `platform_config/<environment>/static.json`, beside `CLUSTER_MANAGER_EMAIL`:

```
"CLUSTER_KUBE_CONTEXT": null,      "CLUSTER_KUBECONFIG_PATH": null
```

`initialize` writes `null` and prints why: a new environment exists but addresses nothing, and filling it in
from the local `kubectl` would be kubementat making a claim it did not earn. A missing/null key is an error that
names the environment, the file, the key and the value found — and lists the contexts the kubeconfig does have.

**Resolution order** (one implementation, `cluster.py`): `--context`/`--kubeconfig` → `CLUSTER_*` process
variables → the environment's `static.json` → the mounted service-account identity → error. The `current-context`
fallback is gone from every path.

## What a command does differently

- **`environment` is the first positional** of every command that reads a cluster — 11 pinned in
  `test_cli_surface.py`, 10 of which actually changed shape (the 11th was already shaped that way), and the rule
  now holds without exceptions except for the three `users` commands noted below. Namespaces stay explicit arguments; they are not derived from teams.
- **Before its first write, a command says** which cluster it chose, from what file or flag, and what that claim
  was checked against. It goes to **stderr** — it is a claim about the run, not the command's output.
- **Two environments that address one cluster are refused**: both named, both addresses, pointing at
  `kmt environments list`, no override flag, and nothing written.
- **An environment it could not resolve is reported, not dropped** — the command still runs.
- `kmt uninstall` takes no team and still resolves its cluster; commands that cannot act on a team no longer
  accept `--team`; nothing defaults to `dev` any more.
- `cli.kmt.Config` and its 32 `@click.pass_obj` are deleted, as are `DEFAULT_TEAM` and every `default='dev'`.

## Both channels, one target

The cluster is now addressed twice per operation — the Python client (built lazily from the target) and the
helm/kubectl binaries (given `--kube-context`/`--kubeconfig` as argv). Sessions (`logs -f`, `exec`,
`port-forward`) and `apply -f <url>` stay kubectl by decision, but never unaddressed: there is one function that
turns the rule into argv, and it refuses a target that names neither context nor file. `kubectl` remains a
declared dependency; this converted calls, it did not remove the binary.

## Guards (the part that stops this regressing)

No `config.load_kube_config()` in `src/`; no `client.<Api>()` built without a client; every kubectl/helm call
that touches a cluster carries the target — with the local-only helm calls declared in the test as exemptions
with reasons. The checks resolve the *called name* rather than matching text, so the one legitimate survivor
(`load_kube_config_from_dict`, for `from_connection`) isn't flagged and needs no exemption.

## Tests

+172 unit tests, 19 new modules, named by behaviour. Two things they now prove that they couldn't before: the
test suite builds a client without reading the machine (`uv run pytest -q` passes with `HOME=<empty> KUBECONFIG=`),
and the parity record knows which cluster an operation touched — which immediately found three test files that
had been comparing a `dev`-addressed client against a `fixture`-addressed one, i.e. two clusters in one
comparison, since those files were written.

The e2e lane proves, against a real k3s: the declared context is what the install used, `environments list`
answers TD-2, a missing key fails closed naming absolute paths, an unresolvable sibling is reported and skipped,
and a collision is refused **and writes nothing** (the release set is unchanged and the namespace the install
would have created does not exist).

## Spec ↔ implementation divergences

The spec carries 295 lines of dated annotations at the points where the code disagrees with it (`7de4414`);
this is the digest. Every one of the four grep-checkable claims was re-checked against the tree before that
commit — the worker that wrote them timed out before reporting.

- **A rule that stops short of uniform, and is now recorded as stopping.** The three `kmt users` team-account
  commands still take the environment as `--environment`, not as the first positional (the positional that leads
  is the service-account name). They touch a cluster, and RC-5 says the environment leads. They do refuse an
  environment they were not given, so nothing is guessed — but the surface is not uniform, the positional table
  never covered them, which is why the divergence was invisible rather than deferred. Fixing it is a breaking
  argv change that no ticket in this epic was authorised to make.
- **The listing's CHECK column is weaker than its header invites.** A resolved row prints the literal `ok`; what
  that answers is "did you resolve me", not "am I colliding". The spec's example table showed
  `collides with dev? no`, which **no code path produces** — the listing resolves every environment and compares
  none, because that is what lets it answer TD-2 from a local read. Consequence to know: a root that *contains*
  a collision lists both sides `ok`; you find out only when a command runs.
- **The layer numbering moved in the document and nowhere else.** Code comments still call the mounted material
  "§4.2's fifth layer" and the terminal error "layer 4" — both correct before layer 5 existed, both one ordinal
  off now. Nothing in the order or the rule differs; only the number moved, but following a code comment into
  §4.2 will land you one layer off.
- **"Ten commands reshaped" is not the ten that table names.** Ten commands' argv moved, and they are the eight
  rows **except** `pvc-usage`, plus `create-ingress` and `helm-version`. `pvc-usage`'s environment was already
  first; what changed there is that it resolves and reports now. So reading that column as a census
  double-counts one command and omits two. The rule itself holds for all 11 pinned commands.
- **§17 is not five open questions any more.** Q4 is closed (the constructor was built, sits last, and the
  exclusion rule is reported rather than assumed). Q2 got a partial answer and not the expected one: validation
  split by *whether there is a claim to compare*, not by read/write. Q1's premise is half true — `kmt install`
  does make one live API call before its first write (the version read that replaced `kubectl cluster-info`);
  only the *identity* half is missing. Q3 and Q5 are untouched.
- **Went further than the spec, on its own principles.** Decision 19 ("kmt never guesses") was written about the
  team. It also applied to clusters, so all `default='dev'` environment arguments went; the same rule (§6.4)
  removed `--team` from commands that read nothing team-scoped, and a census found **6 of 9 `require_team`
  guards were false refusals** (including `kmt routing install`, which needs no team — the e2e harness was right
  and the code was wrong).
- **More shapes than the spec promised.** `source` gained `"service-account"` (and `ClusterTarget` a third
  constructor where the spec lists two); the report gained a third state — *excluded, because this environment is
  the cluster we are standing on* — where the spec showed two; the resolution order gained a real fifth layer.
  `KubernetesUtils` accepts a `Context` as well as a target.
- **A factual correction.** §14's "already broken and nobody noticed" describes `d245e4b`. The library claim was
  verified (no in-cluster fallback in `kubernetes==33.1.0`), but at HEAD that loader is never reached — the same
  pod gets a named refusal. Still worth fixing; less alarming than stated.
- **Design that arrived after the spec.** `Context.config` always exists (a team-less `Context` reads the
  environment layer, and a team-scoped read raises), and `Context.for_configuration(...)` with `target=None`
  exists for `kmt initialize`. Neither is in §6.
- **Left as is, intentionally:** three §10.1 reads that convert to probes were later converted too; §10.2's
  exceptions unchanged; §17's open questions 1, 2, 3 and 5 remain open, only Q4's ordering was forced and built.
- **Not met / not provable here:** TD-1's "install `dev` and `prod` into two different clusters back to back" is
  not covered — the rig is one machine with one k3s. `result.json` prints that itself as `not_covered`, and no
  assertion anywhere claims otherwise. Live preflight (§17 Q1) was not built; §8.1's fingerprint limitation is
  unchanged, so a cluster with more than one DNS name can still defeat the address comparison.

## Suggested review order

1. `git show 08d513d -- src/kubementat/cluster.py` — the model.
2. `git show 97e1164` — the client binding and the guards.
3. `git show 2c28eca -- tests/e2e/harness/stages.py` — what is actually asserted against a real cluster.
4. `docs/lessons-learned/2026-11-02-cluster-addressing.md` — the two orchestration mistakes and the cost numbers.
