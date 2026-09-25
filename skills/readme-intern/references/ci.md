# Config, CI lock-in and badge

Contents: 1. config.json schema · 2. Installing the CI check · 3. Badge · 4. Hard cases

## 1. `.readme-intern/config.json`

Every adjustment lives here so the same run reproduces in CI with no LLM involved.
Rules match when `match` is a substring of the README command.

```json
{
  "readme": "README.md",
  "image": "node:20-bookworm",
  "step_timeout": 600,
  "server_wait": 90,
  "env": { "OPENAI_API_KEY": "sk-dummy-for-ci" },
  "setup": ["docker compose up -d db"],
  "skip":    [{ "match": "vercel deploy", "reason": "needs account" }],
  "include": [{ "match": "pytest" }],
  "overrides": [
    { "match": "make up", "kind": "server", "port": 8080, "probe": "/health" },
    { "match": "npm run build", "timeout": 1200 },
    { "match": "./check.sh", "expect_exit": 1 }
  ]
}
```

| Key | Meaning |
|---|---|
| `readme` | Which file to test (e.g. `docs/quickstart.md`) |
| `image` | Container image. Should match the version the README tells readers to install |
| `env` | Dummy values for placeholders. Never real secrets |
| `setup` | Commands run before the README, only for things the README itself says to do elsewhere |
| `skip` / `include` | Change which README commands run; `reason` is shown in the report |
| `overrides` | Per-step `kind`, `port`, `probe`, `timeout`, `expect_exit`, or `replace` (command substitution, use sparingly and explain why) |

## 2. Installing the CI check

```bash
mkdir -p .readme-intern/bin .github/workflows
cp SKILL_DIR/scripts/{extract_steps,run_steps,report}.py .readme-intern/bin/
cp SKILL_DIR/templates/readme-intern.yml .github/workflows/readme-intern.yml
printf '.readme-intern/run/\n.readme-intern/plan.json\n' >> .gitignore
```

The workflow runs on pushes that touch docs or manifests, on pull requests, and weekly, because
READMEs break when dependencies move even if nobody edits them. It writes the report to the job
summary and uploads step logs as an artifact. GitHub-hosted Ubuntu runners have Docker.

## 3. Badge

Only offer this after a passing run. Replace OWNER/REPO:

```markdown
[![README works](https://github.com/OWNER/REPO/actions/workflows/readme-intern.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/readme-intern.yml)
```

The badge is live: it turns red when the quickstart breaks, so it is honest by construction.

## 4. Hard cases

- **Monorepos**: test the README the user points at. Set `readme` to that path; steps start at the
  repo root unless the README has a `cd`.
- **Multiple quickstarts** (npm vs Docker vs from source): pick the one the README lists first,
  report which, and offer to test the others one at a time with `include`/`skip` rules.
- **GPU / large models / paid APIs**: run up to the step that needs them, skip that step with a
  reason, and check the README says what hardware or keys are needed.
- **Docker-based quickstarts** (`docker compose up`): Docker-in-Docker is not available in the
  default run. Run `--local` on a trusted repo instead, or test the from-source path.
- **Private dependencies**: pass tokens as CI secrets through `env` in the workflow, not in config.json.
