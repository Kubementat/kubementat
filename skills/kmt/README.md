# kmt — kubementat CLI skill

A skill for operating kubementat through its `kmt` CLI and for looking things up about it.
Short on purpose: the CLI's own `--help` is the authority, the skill tells you where to point
it and what not to break.

## What is in here

```
SKILL.md                          # the skill: rules, environment/team, command map, recipes
references/command-reference.md   # every `kmt <command> --help`, regenerated from the live CLI
scripts/kmt_reference.py          # regenerates that reference
install.sh                        # installer (auto-detects the platform)
```

## Install

```bash
./install.sh                                   # auto-detect, user level
./install.sh --all                             # every detected tool path
./install.sh --platform claude-code --project  # one platform, this project
git clone <repo-url> ~/.claude/skills/kmt      # or straight from git
```

Universal path (`~/.agents/skills/`): works for Codex CLI, Gemini CLI, Kiro, Antigravity, and
anything else that reads `.agents/skills/`. For Cursor run `./install.sh --platform cursor`
(produces `.cursor/rules/kmt.mdc`), for Copilot `--platform copilot`.

## Use

Open a new session and type:

```
/kmt install kubementat on dev for team dev1
/kmt which ingress controller is on this cluster?
```

## Keeping the reference honest

The reference file is a snapshot. After the command surface moves, regenerate it from a
kubementat checkout:

```bash
cd /path/to/kubementat   # the checkout whose `kmt` you want captured
python3 /path/to/skill/scripts/kmt_reference.py > /path/to/skill/references/command-reference.md
```

Set `KMT_BIN=/path/to/kubementat/.venv/bin/kmt` if `kmt` is neither on PATH nor reachable
through `uv run` in that directory.
