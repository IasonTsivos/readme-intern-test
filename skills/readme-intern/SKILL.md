---
name: readme-intern
description: Proves whether a repository's README install and quickstart actually work for a first-time user. Re-clones the repo into a clean container, follows the README commands literally, finds exactly where a newcomer gets stuck, fixes the README (or the code behind it) until the quickstart passes, and can add a CI check and badge so it stays working. Use when the user asks to test, verify, check or audit a README, quickstart, install or setup instructions; asks "does my README work", "will this run on a fresh machine", "works on my machine", "check onboarding/getting started"; is preparing a repo for launch, release or open-sourcing; or reports that people cannot get their project running from the docs.
---

# readme-intern

Play the new intern on their first day: fresh clone, clean machine, zero tribal knowledge, and
copy-paste each README command exactly as written. Report the first place they get stuck, fix it,
repeat until the quickstart works. The intern never fills gaps from experience; that is the point.

`SKILL_DIR` below means the directory containing this file. All scripts are Python 3.8+ stdlib.

## Workflow

Copy this checklist into your task list and work through it:

```
- [ ] 1. Static check (seconds, nothing runs)
- [ ] 2. Review the plan and set up config
- [ ] 3. Clean run
- [ ] 4. Diagnose, fix, re-run (max 5 rounds)
- [ ] 5. Report
- [ ] 6. Offer the CI lock-in
```

### 1. Static check

```bash
mkdir -p .readme-intern
python3 SKILL_DIR/scripts/extract_steps.py --repo . > .readme-intern/plan.json
```

Read `static_findings` and `steps` from the JSON. Tell the user the high-severity static findings
right away, one line each with the README line number. This is the fastest useful result.

If the target is a GitHub URL instead of the current repo, `git clone` it into a scratch directory
first and pass `--repo <that dir>`.

### 2. Review the plan

Check every step's `include`, `kind` and `flags` against what a reader would actually do:

- **Excluded sections**: Contributing, Deploy, Release and similar are skipped by default. If the
  user asked to test a specific section, add an `include` rule to config.
- **`kind: server`** runs in the background and passes when a port answers. If a long-running step
  was not detected (custom script, `make up`), add an override with `"kind": "server"` and, if known,
  `"port"`.
- **`placeholder`** flag (`<your-api-key>`, `YOUR_TOKEN`): supply a dummy value through config `env`
  so the run continues, and keep the finding. Never put real secrets in config.
- **`macos-only` / `windows-only`**: the run is Linux. Record the missing Linux path as a finding.
- **Services** (Postgres, Redis) the README says to start separately: add them to config `setup`
  only if the README tells the reader how; otherwise that gap is a finding.

Write adjustments to `.readme-intern/config.json` (schema: [references/ci.md](references/ci.md)),
never by hand-editing plan.json, so CI can reproduce the run. Then re-run step 1.

**Image choice**: use the version the README states (the plan's `suggested_image` does this). If the
README states nothing, use the repo's pinned version and record "prerequisite not stated" as a finding.
A reader only has what the README told them to install.

**Safety**: for a repo the user does not own, read install hooks first (`postinstall`, `setup.py`,
`Makefile`, `curl | sh` targets) and only run in Docker. Never run `--local` on untrusted code.

### 3. Clean run

```bash
docker info >/dev/null 2>&1 && echo docker-ok
python3 SKILL_DIR/scripts/run_steps.py --plan .readme-intern/plan.json --out .readme-intern/run
```

- Without Docker, add `--local` and tell the user the result is weaker (their global tools are visible).
- `--keep-going` continues after a failure. Use it once early to see every independent break;
  otherwise stop at the first failure like a real reader does.
- On Apple Silicon, add `--platform linux/amd64` only if an image lacks arm64.
- Exit 2 means the environment failed to start (image pull, Docker down). That is not a README
  finding; fix the environment or switch modes.

The repo is re-cloned from git, so gitignored files (`.env`, `node_modules`, build output) are absent.
Uncommitted edits to tracked files and new non-ignored files ARE included, so README fixes can be
tested before committing.

### 4. Diagnose, fix, re-run

For each failed step, read `.readme-intern/run/step_<id>.log` and find the root cause in the repo.
[references/diagnosis.md](references/diagnosis.md) maps common failure signatures to causes.

Fix rules:
1. Prefer fixing the README. The reader's instructions are the product.
2. Fix code or config only when that is the real bug (an ignored file the README needs, a script
   that only works in one shell, a missing lockfile). List these separately so the user reviews them.
3. Never make the check pass by weakening it: no skipping a failing step, no deleting a quickstart
   command, no `|| true`. If a step cannot work in CI (needs a paid API key, GPU, hardware), say so
   and skip it in config with a written reason.
4. Rerun a network failure (registry 5xx, timeout on download) once before calling it a finding.

Write fixes following [references/fixing.md](references/fixing.md), then re-run step 3. Stop after 5
rounds and report what is left.

### 5. Report

```bash
python3 SKILL_DIR/scripts/report.py --plan .readme-intern/plan.json \
  --results .readme-intern/run/results.json > .readme-intern/REPORT.md
```

Replace every `<!-- diagnosis:N -->` with 1 to 3 sentences: what broke, why, what fixed it. Replace
`<!-- fixes -->` with the README diff and a separate list of any code changes. Keep the
first-run result (before fixes) at the top of the report, since that is what a reader saw.
See [references/example-report.md](references/example-report.md) for a finished report.

Reply to the user with: the verdict line (for example "A new user got stuck at step 4 of 6"), the
findings that caused it, what you changed, and the final run result. Link the report file.

### 6. Offer the CI lock-in

Ask before adding files beyond the README fixes. If the user agrees, follow
[references/ci.md](references/ci.md): vendor the three scripts to `.readme-intern/bin/`, add the
workflow from `SKILL_DIR/templates/readme-intern.yml`, commit `config.json`, add `.readme-intern/run/`
and `.readme-intern/plan.json` to `.gitignore`, and offer the status badge.

## Output example

Input: "does my README actually work?" on a Python repo.

```
Static check (no execution):
  line 15  `.env.example` is gitignored, so `cp .env.example .env` fails on a fresh clone
  line 16  `scripts/seed.py` does not exist (the file is scripts/seed_db.py)
  line 17  `make run` has no Makefile target (the target is `serve`)
  README says Python 3.9+, pyproject.toml requires 3.11

Clean run in python:3.11: a new user gets stuck at step 4 of 6.
Fixed 4 README lines, un-ignored .env.example, and changed `. .env` to `. ./.env` in the
Makefile (dash can't find `.env` without the ./). Re-run: 6/6 steps pass, server answers
HTTP 200 on :8000 in 6s. Full report: .readme-intern/REPORT.md
```
