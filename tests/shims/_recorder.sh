#!/usr/bin/env bash
#
# The one recorder behind every fake cluster tool in tests/shims/.
#
# Each binary in this directory is a symlink to this file, so `kubectl` runs it with
# argv[0] ending in `kubectl` and the name it answers to is just `basename "$0"`. Recording
# the argv it was handed, in order, is the whole job: a parity test runs the old bash script
# and the new Python path against these and compares the two call lists.
#
# Environment it reads:
#   SHIM_LOG             required. Append target: one CALL line, then one ARG line per
#                        argument, in the order they arrived, then a STDIN line (base64)
#                        when something was piped in.
#   SHIM_RESPONSE_DIR    optional. Files here are what the fake prints instead of answering.
#                        `<dir>/<program>` is stdout for every call of that program and
#                        `<dir>/<program>.exit` is its exit code; a per-subcommand file
#                        (`kubectl.get`) wins over the program-wide one, which is how a
#                        script that calls kubectl twice for two different outputs works.
#
# It does not emulate behaviour. There is no cluster behind this and nothing may start
# pretending there is one: the recorder's only promises are "I recorded the arguments" and
# "I exited when I was told to".

set -u

program="$(basename "$0")"

# A missing log is not silence, it is a test that would pass for the wrong reason.
if [[ -z "${SHIM_LOG:-}" ]]; then
  echo "shim($program): SHIM_LOG is not set; refusing to run silently" >&2
  exit 78
fi

# One `write()` per call, in one append. `kubectl create ... | kubectl apply -f -` runs two
# shims at the same time, and line-by-line appends from them would interleave into one corrupted
# record — which a parity test would read as a call that never happened.
block=""
printf -v block 'CALL\t%s\t%s\n' "$program" "$PWD"
for argument in "$@"; do
  printf -v line 'ARG\t%s\n' "$argument"
  block="${block}${line}"
done

# A terminal on stdin means a developer's own terminal is on the other end and `cat` would
# wait for them. A pipe or a redirect is a script piping a generated manifest into
# `kubectl apply -f -`, which is exactly what a parity test wants to see.
if [[ ! -t 0 ]]; then
  payload="$(cat)"
  if [[ -n "$payload" ]]; then
    encoded="$(printf '%s' "$payload" | base64 -w 0)"
    printf -v line 'STDIN\t%s\n' "$encoded"
    block="${block}${line}"
  fi
fi

printf '%s' "$block" >> "$SHIM_LOG"

# The most specific answer that exists, most-specific-first: `kubectl.get.pod` before
# `kubectl.get` before `kubectl`.
answers=("$program")
subcommands=()
skip_value=0
for argument in "$@"; do
  if [[ "$skip_value" -eq 1 ]]; then
    skip_value=0
    continue
  fi
  case "$argument" in
    -n | -o | -l | -f | -p | --namespace | --output | --selector | --filename | --context | --kube-context | --kubeconfig | --template | --type | --patch | --field-selector | --sortby)
      skip_value=1
      ;;
    -*=*) ;;
    -*) ;;
    *) subcommands+=("$argument") ;;
  esac
done
path="$program"
for word in "${subcommands[@]}"; do
  path="$path.$word"
  answers+=("$path")
done

exit_code=0
answer=""
if [[ -n "${SHIM_RESPONSE_DIR:-}" && -d "${SHIM_RESPONSE_DIR:-}" ]]; then
  for key in "${answers[@]}"; do
    if [[ -f "$SHIM_RESPONSE_DIR/$key" ]]; then
      answer="$SHIM_RESPONSE_DIR/$key"
      break
    fi
  done
  if [[ -n "$answer" && -f "$answer.exit" ]]; then
    exit_code="$(cat "$answer.exit")"
  elif [[ -f "$SHIM_RESPONSE_DIR/$program.exit" ]]; then
    exit_code="$(cat "$SHIM_RESPONSE_DIR/$program.exit")"
  fi
  # A refusal is not a missing file: a real tool sends its reason on stderr and its payload on
  # stdout, and code that decides what a failed delete *meant* reads the first of those. Without
  # this the shim can only say "failed", and the branch that distinguishes an object already gone
  # from an object refused would be unreachable from a test.
  if [[ -n "$answer" && -f "$answer.stderr" ]]; then
    cat "$answer.stderr" >&2
  fi
fi

if [[ -n "$answer" ]]; then
  cat "$answer"
fi

exit "$exit_code"
