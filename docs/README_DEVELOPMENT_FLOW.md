# Development Flow

## Basic description
- Developer creates and builds feature in feature branch
- Developer tests locally
- Developer has finished local testing and development, decides to run tests on dev cluster
- Developer pushes his changes on top of the branch configured for his team (e.g. k8s/dev1, k8s/dev2)
- Gitlab Webhook calls tekton trigger
- Tekton starts the according pipeline for building, deploying and testing the app within the team contexts

## Naming conventions
- ENVIRONMENT: dev/prod - represents a cluster
- TEAM: devX/prodX where X is a number - represents a team in a cluster
- application branch naming: k8s/TEAM, e.g. for TEAM=dev1 k8s/dev1
- docker image tagging convention: k8s-TEAM, e.g. for TEAM=dev2 k8s-dev2