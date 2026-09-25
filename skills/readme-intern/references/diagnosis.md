# Diagnosing a failed step

Contents: 1. Triage order · 2. Signature table · 3. README bug or code bug? · 4. Not a finding

## 1. Triage order

1. Read the last 30 lines of `.readme-intern/run/step_<id>.log`. The real error is usually the first
   error line, not the last.
2. Ask: did an earlier step silently do the wrong thing? A step can "pass" and leave bad state
   (installed into the wrong Python, `cd` into the wrong dir). Check the previous step's log too.
3. Compare with the maintainer's machine: the step works there because of something absent here.
   Find that something. It is the finding.

## 2. Signature table

| Log signature | Usual root cause | Where the fix goes |
|---|---|---|
| `cp: cannot stat '.env.example'`, `No such file` for a file you have locally | File is gitignored or never committed (`git check-ignore -v <file>`) | Commit the file / add `!file` to .gitignore |
| `command not found: pnpm` / `yarn` / `uv` / `poetry` / `make` | Tool used but not listed in prerequisites | README prerequisites, with install command |
| `Unsupported engine`, `requires a different Python`, syntax errors on modern syntax (`match`, `:=`, `?.`) | README's stated version is older than the code needs | README version (match `engines` / `requires-python` / `.nvmrc`) |
| `Missing script: "dev"`, `No rule to make target` | README uses a renamed script/target | README (or restore the alias) |
| `ERR_PNPM_OUTDATED_LOCKFILE`, `npm ci` lockfile mismatch, `poetry.lock is not consistent` | Lockfile not updated with manifest | Code: regenerate and commit the lockfile |
| `ModuleNotFoundError` right after an install step | Installed into a different interpreter (no venv activation, `pip` vs `pip3`) or package missing from requirements | README (activation step) or requirements file |
| `.: .env: not found`, `source: not found` | `source`/`. file` in a Makefile or `sh` script: dash needs `./file` and has no `source` | Code: `. ./.env`, or `SHELL := bash` |
| `KeyError: 'X'`, `X is not set`, `Missing required env` | Env var needed but README never says so, or `.env.example` lacks it | README env section + `.env.example` |
| `ECONNREFUSED 127.0.0.1:5432` / `6379` / `27017` | Needs Postgres/Redis/Mongo; README doesn't say how to start it | README (compose command or install note) |
| `gyp ERR!`, `error: command 'gcc' failed`, `pkg-config not found`, `libpq-fe.h` | Native build dependency not listed | README prerequisites (`apt install build-essential libpq-dev` + macOS equivalent) |
| `EACCES` on global install, `externally-managed-environment` (PEP 668) | README uses global `npm i -g` / system `pip install` | README: use venv / `npx` / `pipx` |
| Step timed out with no output | Waiting on a prompt (`npm init`, `create-*`, login), or a server not detected | README: add `--yes`/non-interactive flag; or config override `kind: server` |
| Server step `still running ... no listening port` | Binds to a port not printed, or only after a long build | Config override `port`; or README says which URL to open |
| Server answered HTTP 500 | App starts but needs data/migrations/seed step missing from README | README: add the migrate/seed step |
| `Permission denied` running `./script.sh` | Executable bit not committed | Code: `git update-index --chmod=+x script.sh` |
| `fatal: could not read Username` / `Permission denied (publickey)` on clone | README uses SSH clone URL | README: HTTPS URL (keep SSH as alternative) |
| `\r': command not found` | Script committed with CRLF line endings | Code: `.gitattributes` `*.sh text eol=lf` |
| `No such file or directory` for a path containing `~/` or `/Users/` | Absolute path from the author's machine | Code or README: relative path |
| `docker: Cannot connect to the Docker daemon` inside the run | README needs Docker-in-Docker; not testable here | Skip in config with reason; keep a finding if README never says Docker is required |

## 3. README bug or code bug?

Ask what a maintainer would rather change. A renamed script is a README bug (one line). A lockfile
out of sync is a code bug (the README is right; the repo is broken). A missing file the README needs
is a code bug. A missing prerequisite is always a README bug. When both are plausible, fix the README
and mention the code option.

## 4. Not a finding

- Registry or network outage (5xx, `ETIMEDOUT` from a registry): rerun once.
- Out of disk or memory in the container: environment, not README. Say so.
- Steps that need real credentials, paid APIs, GPUs or specific hardware: skip with a reason in config,
  and check that the README tells the reader what they need. If it doesn't, that is the finding.
