# 0.4.0
- feature: add helmfile-apply to the kmt cli
- feature: kmt tekton-setup-pipelines - also load tekton tasks and pipelines from platform_config/ENV/tekton/(tasks|pipelines) and platform_config/ENV/TEAM/tekton/(tasks|pipelines)
- refactor - implement helfile_apply in HelmUtils and use it in install kubementat automation and in kmt cli calls instead of the bash version
- refactor - move tekton_ci/automation/components to scripts/automation/components
- refactor - moves tekton_ci/scripts directory to scripts/tasks , removes duplicate or obsolete scripts (general helm based approach for installing backing services will be implemented soon)

# 0.3.0
- Begins refactoring BASH utilities to python
  - adds the first iteration of the __kubementat lib__
  - refactors some base kubementat scripts to allow better portability
  - moves python requirements from cli to central requirements
- integrates __more functionality accesible via the kmt cli__
  - integrates already ported functionality to the kmt cli via the kubementat lib

# 0.2.0
- Goals for this release: 
  - Cleanup and removal of obsolete and umaintained parts
  - Support for running all pipeline tasks from self-owned registry images  
- Implements:
  - Refactors tasks to allow providing an image argument to make them more flexible and less tied to the kubementat specifics
  - ubuntu-ci-minimal image
    - update base image to ubuntu:24.04
    - adds skopeo tool
    - adds more output on build execution with all tool versions
  - removes unused and unmaintained kafka and cassandra support
    - this can be reintroduced via the helm deployment automations (see redis for example)
  - removes unused and unmaintained angular-ci image

# 0.1.0

- Initial version of the kubementat platform.