# Development Flow

## Basic description
- Developer creates and builds feature in feature branch
- Developer tests locally
- Developer has finished local testing and development, decides to run tests on dev cluster
- Developer pushes his changes on top of the branch configured for his team (e.g. k8s/dev1, k8s/dev2)
- Gitlab Webhook calls tekton trigger
- Tekton starts the according pipeline for building, deploying and testing the app within the team contexts

## Unit suite in CI
- Every push and every pull request runs the unit suite: `uv run pytest -q`, the same command the
  "Local verification" section below gives a developer. The job is
  [`.github/workflows/unit.yml`](../.github/workflows/unit.yml) and it does four things — check out,
  install `uv`, run the suite, publish the report. Nothing else: no lint, no typecheck, no coverage,
  no formatter, and no E2E lane step.
- The interpreter is not configured in the workflow. It comes from `.python-version` (currently 3.9),
  so the file that already pins the project for a local checkout is the only place that pins it here.
- The suite takes ~55 s and needs no cluster. It is not a gate on the cluster lanes; see next section.

## Local verification
- Developer runs the E2E suite from the repo root on their own machine, manually, on the ticket that needs evidence:
  ```bash
  uv run python tests/e2e/suite.py --lane smoke    # ~7 min measured
  uv run python tests/e2e/suite.py --lane full     # ~75 min
  ```
  No CI job and no schedule for *these* — the workflow above gates the unit suite, never a lane, because a
  lane wants a VM and holds a host-wide lock. See [README_E2E_TESTS.md](README_E2E_TESTS.md) and `tests/README.md`.

## Naming conventions
- ENVIRONMENT: dev/prod - represents a cluster
- TEAM: devX/prodX where X is a number - represents a team in a cluster
- application branch naming: k8s/TEAM, e.g. for TEAM=dev1 k8s/dev1
- docker image tagging convention: k8s-TEAM, e.g. for TEAM=dev2 k8s-dev2