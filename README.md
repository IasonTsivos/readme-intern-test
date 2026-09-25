# readme-intern

<!--
  GIF (docs/demo.gif, ~20s, 1200px wide). Shot list:
  1. Terminal, a repo on screen. Type: "does my README actually work?"
  2. Two seconds later: 4 red static findings with README line numbers.
  3. Walkthrough table filling in: ✅ ✅ ✅ ❌ at `cp .env.example .env`.
  4. Split view: README diff (4 lines) + Makefile `. .env` -> `. ./.env`.
  5. Re-run: 6/6 ✅, "server answered HTTP 200 on :8000 in 6s".
  6. End card: the green "README works" badge.
  Record with the demo repo: bash examples/make-demo.sh
-->
![readme-intern getting stuck on a broken quickstart, then fixing it](docs/intern.png)

**We gave your README to an intern on their first day. Here's where they got stuck.**

readme-intern is a Claude skill that plays the new intern: a fresh laptop, zero tribal knowledge, and a habit of doing *exactly* what your README says. It clones your repo into a clean container, pastes your commands one by one, and shows you the line where a newcomer gets stuck. Then it fixes the README (or the bug behind it) and tries again until the quickstart works.

```text
Static check (nothing executed yet):
  🔴 line 15  `.env.example` is gitignored, so `cp .env.example .env` fails on a fresh clone
  🔴 line 16  `scripts/seed.py` does not exist (it's scripts/seed_db.py)
  🔴 line 17  `make run` is not a Makefile target (it's `serve`)
  🔴 README says Python 3.9+, pyproject.toml requires 3.11

Clean run in python:3.11:  ❌ the intern gets stuck at step 4 of 6
After fixes:               ✅ 6/6 steps pass, server answers HTTP 200 on :8000 (6s)
```

## Install

**Claude Code** (plugin), typed inside Claude Code:

```text
/plugin marketplace add IasonTsivos/readme-intern
/plugin install readme-intern@readme-intern
```

**Any agent that reads skills folders** (Claude Code, Codex, Cursor and others):

```bash
git clone https://github.com/IasonTsivos/readme-intern
mkdir -p ~/.claude/skills
cp -r readme-intern/skills/readme-intern ~/.claude/skills/
```

Needs [Claude Code](https://claude.com/claude-code) (or another agent that loads skills), Python 3.8+ and git. Docker is strongly recommended; without it the skill runs in a temp folder and tells you the result is weaker.

## 30-second quickstart

Open Claude Code in any repo and say:

```text
does my README actually work?
```

No repo handy? Use the demo, which works perfectly on its author's machine and nowhere else:

```bash
bash readme-intern/examples/make-demo.sh
cd /tmp/readme-intern-demo && claude "does my README actually work?"
```

You get the static findings in seconds, a clean run, the fixes, and `.readme-intern/REPORT.md`.

## Why this exists

The person who wrote the README is the one person who can't test it. Their machine already has the `.env`, the global tools, the renamed script, the right Node version. Everyone else hits the wall.

- Studies of open source builds keep finding the same thing: [38 to 59% of Java projects failed to build locally](https://arxiv.org/pdf/2306.09665), with dependencies as a top cause, and only [56% of sampled GitHub Dockerfiles produced any image at all](https://arxiv.org/html/2602.17678v1).
- Across ML papers, ["silent wrong setup" is the largest category of reproduction issues](https://arxiv.org/html/2606.18237), ahead of crashes.
- When one team handed an agent nothing but their README, it [hit a whole list of broken onboarding steps](https://github.com/TNG/oh-my-agentic-coder/issues/98) that the maintainers' CI had quietly worked around.
- And you rarely hear about it. People who can't get your project running just leave.

Claude is good at reading a README. It's also good at quietly filling in the gaps a real reader would get stuck on. This skill makes it follow the README literally instead, in an environment with nothing extra, so the gaps show up.

## What it catches

| Found instantly (no execution) | Found by the clean run |
|---|---|
| `npm run dev` but there's no `dev` script | Env vars the code needs that the README never mentions |
| `make run` but no `run` target | Native build deps (`gcc`, `libpq-dev`, `pkg-config`) |
| `cp .env.example .env` but the file is gitignored | Prompts that hang (`npm init`, `create-*` without `--yes`) |
| `python scripts/x.py` that doesn't exist | Servers that start and then crash or return 500 |
| README says Node 18, `.nvmrc` says 20 | Shell bugs hidden by your shell (`. .env` under dash) |
| `npm install` but the lockfile is pnpm's | Missing seed/migrate steps, stale lockfiles |
| `./run.sh` committed without the executable bit | Tools you have globally that readers don't |

It also reads "save this as `app.py`" blocks and writes the file, keeps `cd`, `export` and `source .venv/bin/activate` across steps like a real terminal, runs `npm run dev`-style servers in the background and probes the port, and reads inside `make` targets and npm scripts to find what really runs.

## Keep it green

After a passing run, the skill offers to add a GitHub Action that re-runs the same check on every PR and once a week (dependencies drift even when nobody touches the README). No LLM and no API key in CI: just three small Python scripts and Docker. Then you can add an honest, live badge:

```markdown
[![README works](https://github.com/OWNER/REPO/actions/workflows/readme-intern.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/readme-intern.yml)
```

## How it works

1. `extract_steps.py` parses the README into ordered shell steps, skips sections a newcomer wouldn't run (Contributing, Deploy), and runs static checks against your repo.
2. `run_steps.py` snapshots the repo through git (so gitignored files are gone but your uncommitted README edits are included), starts a container from the version the README tells readers to install, and runs each step with shell state carried over.
3. Claude reads the failing step's log, finds the root cause, fixes the README first and code only when that's the real bug, and re-runs. It never makes the check pass by skipping a step.
4. `report.py` writes a Markdown report with the walkthrough, logs, diagnoses and diff.

Everything runs on your machine. Nothing is uploaded anywhere.

## How it compares

| | readme-intern | [ghost.dev](https://github.com/SujalXplores/ghost.dev) | [Runme](https://github.com/runmedev/runme) | [repo-runner](https://github.com/Jia-ben00/repo-runner) | README-writer skills |
|---|---|---|---|---|---|
| Runs README as a newcomer in a clean env | ✅ | ✅ | runs annotated blocks | runs the repo, not the docs | ❌ |
| Needs README annotations | no | no | yes | no | n/a |
| Fixes the README and re-verifies | ✅ | suggests edits | ❌ | ❌ | writes, doesn't verify |
| CI check without an LLM | ✅ | needs API key | ✅ | security gate only | ❌ |
| Works inside your agent, no extra API key | ✅ | ❌ | n/a | ✅ | ✅ |

## Limits

- The clean run is Linux. macOS-only and Windows-only steps are reported, not executed.
- `docker compose up` quickstarts need Docker inside Docker, which the default run doesn't do. Use `--local` for your own trusted repo, or test the from-source path.
- Steps that need real credentials, GPUs or paid APIs are skipped with a reason, and the skill checks the README tells readers what they need.
- On claude.ai there is no Docker, so it runs in local mode there.

## Safety

For repos you don't own, the skill reads install hooks first and only runs inside Docker. It never uses local mode on untrusted code, never asks for real secrets, and uses dummy values for placeholders.

## Contributing

`bash tests/smoke.sh --local` runs the end-to-end test. Found a README pattern it misreads? Open an issue with the snippet; false findings are bugs.

If readme-intern found something in your README, a ⭐ helps other maintainers find it.

MIT © Iason Tsivos
