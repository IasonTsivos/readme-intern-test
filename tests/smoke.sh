#!/usr/bin/env bash
# End-to-end smoke test: the demo must fail as built, then pass after the known fixes.
# Usage: bash tests/smoke.sh [--local | --image IMAGE]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
S="$ROOT/skills/readme-intern/scripts"
MODE=("${@:---image}")
[ "${MODE[0]}" = "--image" ] && [ ${#MODE[@]} -eq 1 ] && MODE=(--image python:3.11-bookworm)
DEMO="$(mktemp -d)/demo"
bash "$ROOT/examples/make-demo.sh" "$DEMO" >/dev/null
cd "$DEMO"

python3 "$S/extract_steps.py" --repo . > plan.json
codes=$(python3 -c "import json;print(' '.join(sorted({f['code'] for f in json.load(open('plan.json'))['static_findings']})))")
for want in version-drift untracked-file missing-file missing-make-target; do
  [[ " $codes " == *" $want "* ]] || { echo "FAIL: static finding $want missing (got: $codes)"; exit 1; }
done
echo "ok  static findings: $codes"

set +e
python3 "$S/run_steps.py" --plan plan.json --out run1 "${MODE[@]}" 2>/dev/null
rc=$?
set -e
[ $rc -eq 0 ] && { echo "FAIL: broken demo passed"; exit 1; }
[ $rc -eq 2 ] && { echo "FAIL: environment did not start: $(python3 -c "import json;print(json.load(open('run1/results.json')).get('setup_error'))")"; exit 2; }
first=$(python3 -c "import json;print(json.load(open('run1/results.json'))['summary']['first_failure']['id'])")
[ "$first" = "4" ] || { echo "FAIL: expected first failure at step 4, got $first"; exit 1; }
echo "ok  broken README fails at step 4"

sed -i.bak 's/Python 3.9+/Python 3.11+/; s#python scripts/seed.py#python scripts/seed_db.py#; s/make run/make serve/' README.md
sed -i.bak 's#\. \.env#. ./.env#' Makefile
printf '!.env.example\n' >> .gitignore
git add .gitignore .env.example
rm -f README.md.bak Makefile.bak
python3 "$S/extract_steps.py" --repo . > plan.json
python3 "$S/run_steps.py" --plan plan.json --out run2 "${MODE[@]}" 2>/dev/null \
  || { echo "FAIL: fixed demo did not pass"; cat run2/results.json; exit 1; }
port=$(python3 -c "import json;print(json.load(open('run2/results.json'))['steps'][-1].get('port'))")
[ "$port" = "8000" ] || { echo "FAIL: server not detected on 8000 (got $port)"; exit 1; }
echo "ok  fixed README passes, server on :$port"
python3 "$S/report.py" --plan plan.json --results run2/results.json | head -3
