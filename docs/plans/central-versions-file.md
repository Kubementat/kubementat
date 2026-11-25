# Plan: central `versions.yaml` (Plan A)

## The problem

Every default version kubementat runs with is pinned where it is consumed — eleven chart
defaults in `helmfile.yaml.gotmpl`, binary and Tekton releases in `constants.py`, the
Concourse image tag in shipped values, eight tool versions in the CI image Dockerfile,
Tekton task image defaults, and the end-to-end harness strings. None of them owns its
number; they are copies, kept agreeable by Renovate, and `renovate.json` has grown to
~30 custom managers to pay for that agreement. A second owner that "happens to agree" is
the shape of every drift bug this repository has already decided against: one owner per
fact, and every other place *resolves*, never copies.

## The decision

One data file owns every default version:

`src/kubementat/core/assets/versions.yaml`

It ships with the package (assets already ship), so runtime consumers reach it through
`importlib.resources` and a checkout reaches it at a fixed path. Shape:

```yaml
# Every default version kubementat runs with. The single owner — consumers resolve, never copy.
charts:
  prometheus:                    {version: "29.40.1"}
  prometheus-blackbox-exporter:  {version: "11.20.2"}
  prometheus-helm-exporter:      {version: "1.4.0+e2eee28"}
  grafana:                       {version: "14.2.0"}
  loki:                          {version: "20.2.0"}
  alloy:                         {version: "1.15.0"}
  polaris:                       {version: "6.0.1"}
  cert-manager:                  {version: "v1.19.2"}
  traefik:                       {version: "39.0.1"}
  openbao:                       {version: "3.1.0"}
  concourse:                     {version: "8.3.3", image_tag: "8.3.3"}
binaries:
  helm: "4.3.1"
  helmfile: "1.7.0"
tekton:
  pipeline: "1.17.1"
  dashboard: "0.73.0"
  triggers: "0.37.0"
ci_image:                        # consumed only as Docker build args, keyed by ARG name
  BASE_IMAGE_URL: "ubuntu:26.04"
  KUBECTL_MINOR_VERSION: "1.37"
  TKN_CLI_VERSION: "0.46.2"
  YQ_VERSION: "4.54.1"
  HELMFILE_VERSION: "1.8.2"
  HELM_DIFF_VERSION: "3.15.15"
  LINKERD_VERSION: "edge-26.10.1"
  NODE_VERSION: "24.22.1"
  UV_VERSION: "0.13.6"
images:
  buildkit: "moby/buildkit:v0.34.2-rootless"
  maven: "maven:3.9.9"
  registry: "registry:2"
  nginx: "docker.io/library/nginx:1.29-alpine"
  alpine: "docker.io/library/alpine:3.22"
e2e:
  k3s: "v1.33.5+k3s1"
```

The seed values must be taken from each file's current state (the Renovate `matchStrings`
are the census), not from this document's memory.

A new reader, `src/kubementat/core/versions.py`:

- loads the file with `importlib.resources`; a missing key raises a named error that
  names the file — the reader refuses, it never defaults;
- accessors: `chart_version`, `binary_version`, `tekton_version`, `image_ref`,
  `e2e_version`;
- `python -m kubementat.core.versions --print-build-args` prints `--build-arg K=V` lines
  from `ci_image:` — this keeps YAML parsing in one place and is how `build.sh` consumes
  the file without assuming `yq`; `--get <path>` answers one value.
- `versions.py` imports nothing from `constants.py` (it is the leaf); `constants.py`
  imports it.

## Steps

### Step 1 — File and reader
Create `versions.yaml` and `versions.py` as above, seeded from the actual current pins.

### Step 2 — Python consumers
- `constants.py`: `PINNED_BINARY_VERSIONS` (helm, helmfile) and the three
  `TEKTON_VERSION_*` entries of `DEFAULT_STATIC_ANSWERS` become reads through
  `versions.py`. Public function names stay so callers do not churn.
- `config.py`: the `L0` fallback for `*_VERSION` component knobs answers from the file,
  so a layer the operator wrote still outranks it. `component_release_version` must never
  answer `None` for a known release anymore — that feeds Step 3.

### Step 3 — helmfile and Concourse
- `helmfile.yaml.gotmpl`: delete the eleven `default "<version>"` halves. ENABLED
  defaults stay; only versions move. A standalone `helmfile -f` run gets an empty version
  and fails loudly — correct, because the version is product shape and the product's half
  is now `versions.yaml`, not the manifest.
- `helm.py` `export_component_release_knobs`: drop the export-nothing-on-`None` rule.
  The apply must always hand helmfile the answer, because the manifest no longer holds a
  default. This deliberately inverts
  `test_a_release_knob_nobody_declared_exports_nothing`: an undeclared knob now exports
  the file's L0 answer, a declared knob its own.
- Concourse: `values/ci/concourse/values.yaml` drops `imageTag:`; the gotmpl's concourse
  release gains a `set:` entry bound to `env "CONCOURSE_IMAGE_TAG"`, exported next to the
  chart knob. The file keeps chart version and image tag adjacent on purpose, so one bump
  is one edit of one release's two halves.

### Step 4 — Dockerfile and build.sh
- Dockerfile: strip the default values from all version `ARG` lines
  (`ARG HELMFILE_VERSION`), leaving the `ARG` declarations and every `RUN` that uses them.
  A bare `docker build .` fails with empty-version errors instead of silently building
  stale pins.
- `build.sh`: resolve the arguments once and pass them to every `buildx build` call:
  ```bash
  mapfile -t BUILD_ARGS < <(uv run python -m kubementat.core.versions --print-build-args)
  docker buildx build --progress=plain --platform "$PLATFORMS" "${BUILD_ARGS[@]}" .
  ```
- `test.sh`: stops reading the ARG defaults out of the Dockerfile; build.sh exports the
  expected versions into the environment instead.

### Step 5 — End-to-end harness
- `tests/e2e/harness/config.py` and `stages.py`: the image strings become
  `versions.image_ref(...)` reads.
- `tests/e2e/stages/01-provision.sh`:
  `K3S_VERSION="$(uv run python -m kubementat.core.versions --get e2e.k3s)"`
  (the VM has kmt installed by then).
- Buildkit is one entry in `images:` read by both the shipped tasks and the harness, so
  the e2e lane exercises the tag the product ships.

### Step 6 — Tekton asset tasks render at apply time
The shipped pipeline YAMLs keep the image `default:` params, and
`automations/install/pipelines.py` fills the tag from `versions.py` at the point where it
applies the assets to the cluster. The asset files keep the image name; the tag is the
product's answer, not the file's.

### Step 7 — Renovate
The custom managers collapse to one regex (or jsonata) manager targeting
`versions.yaml`, keeping each dependency's datasource, registry and extraction metadata
beside its depName, plus the native `uv` manager for `pyproject.toml`/`uv.lock` and the
native GitHub Actions manager. All file-targeted managers and the helm-values/dockerfile
workarounds die. Grouping survives as `packageRules` on the same file: chart minor/patch
grouped, majors alone, Concourse chart+tag one PR.

### Step 8 — Tests and changelog
Guards that state product facts:
- `versions.yaml` parses and answers every key any reader asks for;
- no version literal survives a `*_VERSION` `default` in `helmfile.yaml.gotmpl`;
- the Dockerfile's version `ARG`s carry no defaults, and `build.sh`'s arg set equals the
  `ci_image:` keys;
- the rendered Tekton task defaults equal the file.

`CHANGELOG.md`: undeclared VERSION knobs now export L0, and a `docker build` without args
fails — one entry per change. Whether this is MINOR or MAJOR turns on the semver rule
("an existing configuration tree resolves the same"); the tree's answers do not change,
the L0 behind them only changes owner, so MINOR — to be settled at release review.

## Order and gates

Steps 1 → 2 → 3 land together (the reader, the Python consumers, the manifest half is one
coherent change: `uv run pytest -q` green). Steps 4–6 are independent of each other and
each gates on its own lane (image build, harness stage). Step 7 only makes sense after
every pin the old managers knew about lives in the file. The merge gate is the e2e lane
(install/uninstall touch the export change) with its `result.json` attached.

## Open risks

- The Concourse `set:` value must reach helmfile's env the way the `version:` line does
  today — verify in the gotmpl before deleting the values-half pin.
- The inverted export test is the intended behaviour change, not breakage; the rest of the
  install suite will say the same.
- `helmfile.yaml.gotmpl` rendering with an unset `*_VERSION` env: confirm helmfile's
  `env` handling gives an empty string and the release fails on it (it does today when
  nobody exports a bad name).
