# Writing README fixes

Fixes are small, surgical edits. The goal is a quickstart a stranger can paste top to bottom.
Do not rewrite the README, change its voice, or add sections nobody asked for.

## Rules

1. **Change the fewest lines that make the run pass.** Keep the author's wording where it is right.
2. **Prerequisites name versions and how to get them.** `Node.js 20+ (see .nvmrc)` beats `Node.js`.
   If a tool is not usually preinstalled (pnpm, uv, make on macOS), give the install command.
3. **One copyable block per logical step.** In `bash`/`sh` blocks, no `$ ` prompts and no output
   lines mixed in, because readers copy the whole block. Use a `console` block if showing output.
4. **Every command runs from a stated directory.** If a command must run in a subfolder, include `cd`.
5. **Secrets and env vars**: say where the value comes from (link to the settings page), which ones are
   required vs optional, and make `.env.example` contain every required key with a safe dummy value.
6. **Services**: if the app needs Postgres/Redis, give one command that starts them
   (`docker compose up -d db`) and the port it expects.
7. **Say what success looks like** after the run step: "Open http://localhost:3000, you should see ...".
8. **Platform notes stay short**: Linux command first if the project targets Linux; macOS/Windows
   variants in the same block as comments or a short list, not a separate section.
9. **Never hide a failing step** to make CI green. Fix it or document the requirement.

## Diff format in the report

Show a unified diff of README.md, then code changes separately:

```diff
 ## Requirements

-Python 3.9+
+Python 3.11+

 ## Quickstart
@@
-python scripts/seed.py
-make run
+python scripts/seed_db.py
+make serve
```

Code changes (review these):
- `.gitignore`: added `!.env.example` so the template is committed.
- `Makefile`: `. .env` → `. ./.env` (`/bin/sh` on Debian/Ubuntu does not search the current directory).
