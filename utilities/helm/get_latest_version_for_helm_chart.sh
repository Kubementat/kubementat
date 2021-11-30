#!/usr/bin/env bash

#################################
#
# Displays the latest available version for a helm chart within a repo
#
#################################

set -e

CHART_PATH="$1"
if [[ "$CHART_PATH" == "" ]]; then
  echo "Usage: get_latest_version_for_helm_chart.sh <REPO>/<CHART_NAME>"
  echo "e.g.: get_latest_version_for_helm_chart.sh prometheus-community/prometheus"
  exit 1
fi

helm search repo -l "$CHART_PATH" | head -n 2 | tail -n 1 | awk '{ print $2 }'