<div align="center">

# readme-intern

**We gave your README to an intern on their first day. Here's where they got stuck.**

<img src="docs/internlogo.png" alt="readme-intern: a puzzled intern working through a README" width="280">

<p>
  <a href="#30-second-quickstart">Quickstart</a> ·
  <a href="#what-it-catches">What it catches</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#keep-it-green">Keep it green</a>
</p>

</div>

---

<div align="center">
  <img src="docs/header.png" alt="A puzzled intern trying to follow a README" width="100%">
</div>

## The idea

`readme-intern` is a Claude skill that plays the new intern: a fresh laptop, zero tribal knowledge, and a habit of doing **exactly** what your README says.

It clones your repo into a clean container, follows the commands one by one, and shows you the exact step where a newcomer gets stuck. Then Claude fixes the README — or the bug behind it — and tries again until the quickstart works.

### A typical run

```text
Static check (nothing executed yet):

  🔴 line 15  .env.example is gitignored, so `cp .env.example .env` fails on a fresh clone
  🔴 line 16  `scripts/seed.py` does not exist (it's `scripts/seed_db.py`)
  🔴 line 17  `make run` is not a Makefile target (it's `serve`)
  🔴 line 17  README says Python 3.9+, pyproject.toml requires 3.11

Clean run in python:3.11:  ❌ the intern gets stuck at step 4 of 6

After fixes:               ✅ 6/6 steps pass
                           server answers HTTP 200 on :8000 (6s)
```

## 30-second quickstart

Open Claude Code in any repo and say:

```text
does my README actually work?
```

No repo handy? Try the demo:

```bash
bash readme-intern/examples/make-demo.sh

cd /tmp/readme-intern-demo
claude "does my README actually work?"
```

You'll get the static findings, a clean run, the fixes, and `.readme-intern/REPORT.md`.

---

## Install

### Claude Code

Inside Claude Code:

```text
/plugin marketplace add IasonTsivos/readme-intern
/plugin install readme-intern@readme-intern
```

### Any agent that loads skills folders

Works with Claude Code, Codex, Cursor, and other agents that load skills:

```bash
git clone https://github.com/IasonTsivos/readme-intern

mkdir -p ~/.claude/skills
cp -r readme-intern/skills/readme-intern ~/.claude/skills/
```

### Requirements

- Claude Code, or another agent that loads skills
- Python 3.8+
- Git
- Docker strongly recommended

Without Docker, the skill runs in a temporary folder and reports that the result is weaker.

---

## Why this exists

The person who wrote the README is usually the one person who can't test it like a newcomer.

Their machine already has the `.env`, the global tools, the renamed script, the right Node version, and all the little bits of tribal knowledge that a fresh clone doesn't have.

`readme-intern` removes that invisible context and asks a simpler question:

> **Can someone follow the README literally from a clean environment?**

Some relevant research and examples:

- Studies of open-source builds have found substantial local build failures: [38–59% of Java projects failed to build locally](https://arxiv.org/pdf/2306.09665), with dependencies among the major causes.
- A study of GitHub Dockerfiles found that only [56% of sampled Dockerfiles produced any image](https://arxiv.org/html/2602.17678v1).
- Across ML papers, ["silent wrong setup" was reported as the largest category of reproduction issues](https://arxiv.org/html/2606.18237).
- A real-world issue in [oh-my-agentic-coder](https://github.com/TNG/oh-my-agentic-coder/issues/98) documents an agent encountering broken onboarding steps that maintainers' existing environment had masked.

The point isn't that every README is broken. It's that **README correctness is easy to assume and surprisingly easy to stop checking**.

---

## What it catches

| Found instantly — no execution | Found during the clean run |
|---|---|
| `npm run dev` but there is no `dev` script | Environment variables the code needs but the README never mentions |
| `make run` but there is no `run` target | Native build dependencies such as `gcc`, `libpq-dev`, `pkg-config` |
| `cp .env.example .env` but the file is gitignored | Prompts that hang, such as `npm init` without `--yes` |
| `python scripts/x.py` but the file doesn't exist | Servers that start and then crash or return 500 |
| README says Node 18, `.nvmrc` says 20 | Shell bugs hidden by your usual shell |
| `npm install` but the lockfile belongs to pnpm | Missing seed/migrate steps and stale lockfiles |
| `./run.sh` is committed without the executable bit | Tools you have globally that a fresh environment doesn't |

It also:

- writes files from README blocks such as “save this as `app.py`”
- keeps `cd`, `export`, and `source .venv/bin/activate` state across steps
- runs `npm run dev`-style servers in the background and probes their ports
- reads inside `make` targets and npm scripts to find what actually runs
- distinguishes static problems from failures discovered by execution

---

## The demo

The included demo intentionally contains a few common README failures.

```bash
bash examples/make-demo.sh
cd /tmp/readme-intern-demo
claude "does my README actually work?"
```

The walkthrough goes roughly like this:

```text
README
  │
  ├── static checks
  │     ├── missing files
  │     ├── wrong commands
  │     ├── version mismatches
  │     └── env / lockfile issues
  │
  ├── clean execution
  │     └── 6 steps → find the first real failure
  │
  ├── Claude fixes the root cause
  │
  └── run again
        └── 6/6 ✅ + HTTP 200
```

---

## Keep it green

Once a run passes, the skill can offer to add a GitHub Action that:

- runs the same check on every PR
- runs it once a week
- requires no LLM or API key in CI
- uses the same small Python scripts and Docker workflow

That lets you add a live badge to the repository:

```markdown
[![README works](https://github.com/OWNER/REPO/actions/workflows/readme-intern.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/readme-intern.yml)
```

Dependencies drift even when nobody touches the README. A scheduled check catches that too.

---

## How it works

### 1. Extract

`extract_steps.py` parses the README into ordered shell steps, skips sections a newcomer wouldn't normally run (such as Contributing or Deploy), and performs static checks against the repository.

### 2. Run clean

`run_steps.py` snapshots the repo through git so ignored files disappear while your uncommitted README edits are still included.

It starts a container from the version the README tells readers to install and runs each step with shell state carried over.

### 3. Fix and verify

Claude reads the failing step's log, finds the root cause, fixes the README first, and changes code only when the code is actually the problem.

It then runs the check again.

**It never makes the check pass by silently skipping a step.**

### 4. Report

`report.py` writes `.readme-intern/REPORT.md` with:

- the walkthrough
- command output
- diagnoses
- fixes
- the final diff

Everything runs on your machine. Nothing is uploaded anywhere.

---

## How it compares

| | readme-intern | [ghost.dev](https://github.com/SujalXplores/ghost.dev) | [Runme](https://github.com/runmedev/runme) | [repo-runner](https://github.com/Jia-ben00/repo-runner) | README-writer skills |
|---|---|---|---|---|---|
| Runs README as a newcomer in a clean environment | ✅ | ✅ | Runs annotated blocks | Runs the repo, not the docs | ❌ |
| Needs README annotations | No | No | Yes | No | N/A |
| Fixes the README and re-verifies | ✅ | Suggests edits | ❌ | ❌ | Writes, doesn't verify |
| CI check without an LLM | ✅ | Needs API key | ✅ | Security gate only | ❌ |
| Works inside your agent without another API key | ✅ | ❌ | N/A | ✅ | ✅ |

---

## Limits

- The clean run is Linux. macOS-only and Windows-only steps are reported rather than executed.
- `docker compose up` quickstarts require Docker inside Docker, which the default run does not do. Use `--local` for your own trusted repo, or test the from-source path.
- Steps that require real credentials, GPUs, or paid APIs are skipped with a reason, and the skill checks what the README tells readers they need.
- On claude.ai there is no Docker, so the skill runs in local mode there.

## Safety

For repositories you don't own, the skill reads install hooks first and only runs inside Docker.

It never uses local mode on untrusted code, never asks for real secrets, and uses dummy values for placeholders.

---

## Contributing

Run the end-to-end smoke test:

```bash
bash tests/smoke.sh --local
```

Found a README pattern it misreads? Open an issue with the snippet. False findings are bugs.

If `readme-intern` found something in your README, a ⭐ helps other maintainers find it.

---

<div align="center">

**MIT © Iason Tsivos**

</div>
