# Platform configuration

This directory contains configuration for the according environments.
Each sub-directory represents and ENVIRONMENT (dev or prod). These directories contain another layer of grouping which we call TEAMS for separating team namespaces. Under the team directories you will also find a sub-directory for each application that the team is deploying. Those are used for storing application specific settings.

Here is an example structure for the configuration of the nginx-example in ENVIRONMENT=dev and TEAM=dev1:
- dev/dev1/nginx-example/static.encrpyted.json -> database credential configuration
- dev/dev1/nginx-example/values.encrpyted.yaml -> the helm deployment configuration


All relevant env and team specific configuration is contained in json files and read by the automation scripts and the pipeline execution scripts. The jq command line tool is used for reading out information from those configuration files.

All files with the *.encrypted.* format are encrypted via git-crypt (See README_GIT_CRYPT.md for more details on git-crypt usage).

# List of configuration options

# static.json
TODO:


## static.encrypted.json
TODO: