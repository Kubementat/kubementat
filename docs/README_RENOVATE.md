# Version freshness scanning

The product pins versions in more places than a reader can hold at once: chart versions in the
Go-template expressions of the helmfile manifest, binary versions in `constants.py`, tool versions
in the CI image's build arguments, container images in the Tekton asset parameters and in the
end-to-end harness, the Python dependencies and their lock file, and the Actions the unit lane runs
on. This page describes the configuration and the runner that ask the upstreams whether any of
those numbers are stale, and nothing else: **the scan reports, and no verb of `kmt` depends on it.**

- `renovate.json` at the repo root — where each version lives and which upstream answers it.
- `tools/renovate/run-renovate-scan.sh` — the one command that re-runs the scan.
- [docs/concourse-ci.md](concourse-ci.md) — the Concourse release whose two pins this scan groups.

## How it works

Two phases, in one process. First **extraction**: the config's rules read the tree and build a list
of "thing plus version" pairs. Then **lookup**: every pair is asked of its upstream — a helm chart
index, a container registry, the Python index, a GitHub release list — and the newer candidates are
reported. Extraction needs nothing but the tree; lookup needs network egress, and answers nothing
about the surfaces whose upstream wants a token.

Renovate's manager catalogue does most of the work, but not on this tree's shapes. Where a pin sits
outside a format the catalogue can parse, the config carries a `regex` manager that says what to
match, which name to attach, and where to ask.

| Surface | Where the version is | How it is read |
|---|---|---|
| Helm chart versions | the Go-template `default` expression of each release in `helmfile.yaml.gotmpl` | one `regex` manager per release, anchored on the environment-variable name, each carrying its chart index URL |
| Pinned binaries | `PINNED_BINARY_VERSIONS` in `core/constants.py` | `regex` manager over that file |
| Tekton releases | `DEFAULT_STATIC_ANSWERS` in `core/constants.py` | `regex` manager per key, since `install.py` builds its release URLs from them |
| The CI image's tools | `ARG` values in `docker_files/**/Dockerfile` whose download URLs are built from them | `regex` manager per tool; the arguments that name a base image are handled by the `dockerfile` manager |
| Concourse | the chart pin in the manifest **and** `imageTag` in `values/ci/concourse/values.yaml` | a `regex` manager each, grouped into one branch — see below |
| Tekton asset images | parameter defaults in `assets/tekton_ci/**` | `regex` manager, container registry datasource. The native `tekton` manager is not used: these tags are parameter defaults, not step images |
| Harness images | image strings in `tests/e2e/harness/` and the k3s version in the provision stage | `regex` manager per file |
| Python | `pyproject.toml` and `uv.lock` | the `pep621` manager, with `uv.lock` refreshed by lock-file maintenance |
| Example chart image | `examples/helm_charts/**/values.yaml` | the `helm-values` manager |
| CI workflow pins | `.github/workflows/*.yml` | the `github-actions` manager |

Two exclusions are part of the design rather than oversights. The `build/` tree is ignored because
it is a stale mirror of `src/` carrying copies of the same pins, and `ignorePresets` drops
`:ignoreModulesAndTests` because `config:recommended` otherwise merges that preset's ignore list
with this file's and silently removes every pin under `tests/` and `examples/` from a run that lists
files through git — which is every run against a real checkout.

## Running it

```
tools/renovate/run-renovate-scan.sh --dry-run     # the default: report, write nothing
tools/renovate/run-renovate-scan.sh --pr          # open branches against Forgejo
```

The dry run mounts the checkout read-only and uses no platform, so it is safe on any branch and
writes only a timestamped log, to `/tmp/renovate-scans` unless `RENOVATE_OUTPUT_DIR` says otherwise.
It runs the scan at debug level on purpose: in this mode Renovate names the candidate updates only
there, and a quieter run would report that it looked without saying what it found.
PR mode needs both `RENOVATE_ENDPOINT`
(the HTTPS web URL of the Forgejo instance, not the ssh remote) and `RENOVATE_TOKEN` (a token with
repository read+write and user read); it refuses to start without either, and clones through the
platform API rather than touching the local checkout.

Because Renovate's CLI runs once and exits, repetition is the scheduler's job. Two cron lines are
what makes the scan periodic:

```cron
17 3 * * *  tools/renovate/run-renovate-scan.sh --dry-run
5  3 * * 1  tools/renovate/run-renovate-scan.sh --pr
```

The Monday-03:05 slot is not arbitrary: the lock-file refresh carries a schedule gate of "before
4am on monday", so a PR-mode run anywhere else never refreshes `uv.lock` and the lock file rots
without an error. Keep the two lines in the same window if the window moves.

## Concourse, pinned twice

One Concourse release is written down twice in this tree: the chart in the helmfile manifest and
the image tag in the shipped component values, where the tag is the chart's `appVersion`. A bump
that changes only one of them runs a binary under a chart nobody judged against it, so the config
groups both dependencies into a single branch and both files move in the same commit.

The grouped branch is the mechanism, not the verdict. Renovate chooses the image candidate as the
newest published tag, which is not necessarily the new chart's `appVersion` — so the check on a
Concourse bump is that the tag it writes equals that `appVersion`, and the reviewer sets it there
before merging. If only one of the two has a newer upstream entry, the branch still carries the
other file unchanged; the pairing is a review rule, and this page is where it is stated.

## What the scan does not see

- **Releases hosted on GitHub without a token.** Every dependency whose freshness is answered by
  GitHub's release API — the pinned binaries, the Tekton releases, k3s, the Linkerd edge channel,
  the Actions — reports `github-token-required` in a tokenless run. Pass `GITHUB_TOKEN` to the
  runner when those answers matter; until then they are skipped, and that is a config gap to
  report, not a clean scan. Note that this covers the drift-critical `helmfile` pair.
- **Anything with no version written down.** The untagged images in the example pipelines and the
  apt-channel tools of the CI image have no current value to compare against. Pinning or digesting
  them is a one-off decision, and a recurring scan has nothing to say about them.
- **Generated configuration outside this checkout.** The per-environment mirrored image list lives
  in the operator's configuration tree, not in this repository, and its tags ride the chart bumps
  the scan already proposes.
- **Patch churn inside a channel.** The `kubectl` rule reduces upstream tags to their minor, so the
  scan proposes "a newer stable minor is served" and never churns on patches the URL already
  installs.
- **Documentation tables.** Research pages that quote versions are prose, not pins.
