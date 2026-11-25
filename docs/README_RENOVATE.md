# Version freshness scanning

The product keeps every default version in exactly one file —
`src/kubementat/core/assets/versions.yaml` — and its consumers resolve that file rather than
copying numbers from it. That is also what makes the scan small: Renovate's custom managers all
target the one file, and each of them contributes only the half a version file cannot carry —
which upstream answers a newer version of this key, from which registry, and how bumps group into
pull requests. The Python dependencies and the Actions the unit lane runs on are the remaining
pins outside that file, and the native managers own those. **The scan reports, and no verb of
`kmt` depends on it.**

- `renovate.json` at the repo root — which upstream answers each key and how bumps group.
- `src/kubementat/core/assets/versions.yaml` — where every scanned version is written.
- `tools/renovate/run-renovate-scan.sh` — the one command that re-runs the scan.
- [docs/concourse-ci.md](concourse-ci.md) — the Concourse release whose two lines this scan groups.

## How it works

Two phases, in one process. First **extraction**: the config's rules read the tree and build a list
of "thing plus version" pairs. Then **lookup**: every pair is asked of its upstream — a helm chart
index, a container registry, the Python index, a GitHub release list — and the newer candidates are
reported. Extraction needs nothing but the tree; lookup needs network egress, and answers nothing
about the surfaces whose upstream wants a token.

A version file is not a format Renovate's manager catalogue knows, so the config carries `regex`
managers over `versions.yaml`: one per block, or per key where the keys need different upstreams,
each saying what to match and where to ask. The blocks are:

| Block in versions.yaml | What it holds | How it is read |
|---|---|---|
| `charts:` | one version per release, eleven of them | one `regex` entry per chart, each carrying its Helm registry URL and package name |
| `charts.concourse` | the image tag beside the chart version, both lines of the same release | a `regex` entry on the `image_tag:` line, container-registry datasource, grouped with the chart entry below |
| `binaries:` | the `helm` and `helmfile` versions kubementat execs | `github-releases` datasource; `helmfile` also matches the CI-image key below, under one depName |
| `tekton:` | the three Tekton releases `install` applies | `github-releases` per release, since the release URLs are built from these keys |
| `ci_image:` | the build args the CI image is built with | `github-releases` or container registry per tool; `extractVersion` reduces kubectl tags to the served minor and keeps Linkerd's `edge-*` releases from reordering the list |
| `images:` | the container images the shipped tasks render and the harness runs | one entry, five match strings capturing the image name, so the block is covered as a block |
| `e2e:` | the k3s version the provision stage installs | `github-releases`; the `+k3sN` build metadata orders under semver-coerced without help |

Python stays with the native `pep621` manager (`pyproject.toml` and `uv.lock`, the lock refreshed by
lock-file maintenance), and the workflow pins with the native `github-actions` manager. Every
version in the file carries the shape it is consumed in — v-prefixes, build metadata, edge tags,
channel tags — and `semver-coerced` orders all of them as one kind of string.

Two exclusions are part of the design rather than oversights. The `build/` tree is ignored because
it is a stale mirror of `src/` carrying copies of pins, and `ignorePresets` drops
`:ignoreModulesAndTests` because `config:recommended` otherwise merges that preset's ignore list
with this file's and silently removes every pin under `tests/` and `examples/` from a run that
lists files through git — which is every run against a real checkout.

Separately, four of the catalogue's managers are switched off by `packageRules`, because a dry run
against this tree showed them extracting what they should not:

- **ansible** mis-reads this tree's Tekton YAML as ansible tasks — 26 dependencies that are not
  dependencies.
- **pyenv** reads `.python-version`, which states the Python *floor* the package supports. Raising
  a floor is a compatibility decision, not a freshness pin, so there is nothing for it to keep fresh.
- **dockerfile** has nothing left to read since the CI image took its versions from build args:
  its `FROM` lines resolve from `ci_image:`, and the pairs it still extracts resolve to no branch.
- **helm-values** finds image repositories without tags — in the example charts' values and in the
  shipped values alike. A tagless dependency can never resolve, and the images that do carry
  versions live in `versions.yaml`.

The rule that disables something now states the dry run that proved it, so a future reader can
re-run the scan and check the claim instead of trusting the sentence.

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

Because Renovate's CLI runs once and exits, repetition is the scheduler's job, and the scan has
two schedulers it may live under — pick one for the PR-mode half, because two schedulers open two
pull request streams that disagree about the same file.

The host's crontab drives the script:

```cron
17 3 * * *  tools/renovate/run-renovate-scan.sh --dry-run
5  3 * * 1  tools/renovate/run-renovate-scan.sh --pr
```

The Monday-03:05 slot is not arbitrary: the lock-file refresh carries a schedule gate of "before
4am on monday", so a PR-mode run anywhere else never refreshes `uv.lock` and the lock file rots
without an error. Keep the two lines in the same window if the window moves.

`.github/workflows/renovate.yml` is the same scheduling without a host: GitHub Actions starts the
full image once a day at 03:17, inside that same window, so Monday's run carries the lock-file
refresh, and opens its pull requests through the platform API against GitHub rather than against
Forgejo. While that workflow is enabled, the host's `--pr` line goes quiet; the `--dry-run` line
harmlessly duplicates work. A bump PR opened with the workflow's own token needs a maintainer's
approval before its first lane runs — the price of not keeping a write-privileged token in a
secret, stated on the workflow's own door.

## Concourse, one release on two lines

A Concourse release is written down twice, and since the central file moved these two lines sit
next to each other: the chart version and the image tag that is that chart's `appVersion`. A bump
that moves only one of them runs a binary under a chart nobody judged against it — the drift the
pairing exists to prevent — so the config groups both dependencies into a single branch, and the
whole of a Concourse bump is one PR with two adjacent edits.

The grouped branch is the mechanism, not the verdict. Renovate chooses the image candidate as the
newest published tag, which is not necessarily the new chart's `appVersion` — so the check on a
Concourse bump is that the tag it writes equals that `appVersion`, and the reviewer sets it there
before merging. If only one of the two has a newer upstream entry, the branch still carries the
other line unchanged; the pairing is a review rule, and this page is where it is stated.

## What the scan does not see

- **Releases hosted on GitHub without a token.** Every dependency whose freshness is answered by
  GitHub's release API — the pinned binaries, the Tekton releases, k3s, the Linkerd edge channel,
  the Actions — reports `github-token-required` in a tokenless run. Pass `GITHUB_TOKEN` to the
  runner when those answers matter; until then they are skipped, and that is a config gap to
  report, not a clean scan. Note that this covers `helmfile`, whose single depName carries both
  the binary key and the CI-image key.
- **Anything with no version written down.** The untagged images in the example pipelines (the
  `helm-values` manager is disabled, for the reason above) and the apt-channel tools of the CI
  image have no current value to compare against. Pinning or digesting them is a one-off decision,
  and a recurring scan has nothing to say about them.
- **Generated configuration outside this checkout.** The per-environment mirrored image list lives
  in the operator's configuration tree, not in this repository, and its tags ride the chart bumps
  the scan already proposes.
- **Patch churn inside a channel.** The `kubectl` key reduces upstream tags to their minor, so the
  scan proposes "a newer stable minor is served" and never churns on patches the URL already
  installs. The same shape holds for the Linkerd edge channel, where only `edge-*` releases enter
  the candidate set.
- **Documentation tables.** Research pages that quote versions are prose, not pins.
