#!/usr/bin/env bash

function base64_encode {
  TO_ENCODE="$1"
  if [[ "$(uname -a |grep -o Darwin | head -n1)" == "Darwin"  ]]; then
    ### OS X Variant
    encoded="$(echo "$TO_ENCODE" | openssl base64 -A)"
  else
    ### linux variant
    encoded="$(echo "$TO_ENCODE" | base64 -w 0)"
  fi
  echo "$encoded"
}