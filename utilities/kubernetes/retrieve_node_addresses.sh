#!/usr/bin/env bash

kubectl get nodes -o=json | jq '.items[].status.addresses'