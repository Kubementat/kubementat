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