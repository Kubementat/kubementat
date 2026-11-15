#!/usr/bin/env python3
"""Regenerate references/command-reference.md from the live `kmt` binary.

The reference is a snapshot of what the CLI actually prints, so it cannot drift from the
CLI by more than one commit. Run it from the kubementat checkout (the repo where `kmt`
lives) whenever the command surface changes:

    ./scripts/kmt_reference.py > references/command-reference.md

The `kmt` binary is picked in this order:
    --kmt <path>          explicit path
    $KMT_BIN              environment variable
    `kmt` on PATH         global install
    `uv run kmt`          the checkout's own entry point (default, if uv is present)

Nothing here mutates the cluster; the script only asks the CLI for `--help`.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

COMMANDS_BLOCK = re.compile(r"^Commands:\s*$", re.MULTILINE)
COMMAND_ROW = re.compile(r"^  ([a-z][a-z0-9-]+)(?:\s{2,}\S.*)?$")


def resolve_kmt(explicit: str | None) -> list[str]:
    """The argv prefix that invokes kmt, or an empty list when kmt is unreachable."""
    if explicit:
        return [explicit]
    from_env = os.environ.get("KMT_BIN")
    if from_env:
        return [from_env]
    if shutil.which("kmt"):
        return ["kmt"]
    if shutil.which("uv"):
        return ["uv", "run", "kmt"]
    return []


def help_of(kmt: list[str], path: list[str]) -> tuple[str, bool]:
    """The help text for `kmt <path...>` and whether the call succeeded."""
    try:
        proc = subprocess.run(
            [*kmt, *path, "--help"], capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"error: {exc}", False
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout).strip(), False
    return proc.stdout.strip(), True


def subcommands(help_text: str) -> list[str]:
    """The names listed under `Commands:` in a help block (groups only)."""
    match = COMMANDS_BLOCK.search(help_text)
    if not match:
        return []
    names: list[str] = []
    for line in help_text[match.end():].splitlines():
        row = COMMAND_ROW.match(line)
        if row:
            names.append(row.group(1))
        elif names and not line.strip():
            break
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--kmt", help="Path to the kmt binary (default: $KMT_BIN, kmt, uv run kmt)")
    args = parser.parse_args()

    kmt = resolve_kmt(args.kmt)
    if not kmt:
        print("kmt not found on PATH and no uv available. Run this from the kubementat checkout.",
              file=sys.stderr)
        return 1

    print("# kmt command reference\n")
    print("Snapshot of `kmt --help` and every `kmt <command> --help`, regenerated from the live CLI.\n")
    print(f"Generated {date.today().isoformat()} by `scripts/kmt_reference.py`. "
          "The CLI is the source of truth: `uv run kmt <command> --help` outranks this file.\n")

    failures = 0
    for name in subcommands(help_of(kmt, [])[0]):
        header, ok = help_of(kmt, [name])
        children = subcommands(header)
        print(f"## `kmt {name}`\n\n```\n{header}\n```\n")
        failures += 0 if ok else 1
        for child in children:
            text, ok = help_of(kmt, [name, child])
            print(f"### `kmt {name} {child}`\n\n```\n{text}\n```\n")
            failures += 0 if ok else 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
