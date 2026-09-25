#!/usr/bin/env bash
# Creates a small repo whose README works on the author's machine and nowhere else.
# Try readme-intern on it:   bash examples/make-demo.sh && cd /tmp/readme-intern-demo && claude
#                           > /readme-intern
set -euo pipefail
DEMO="${1:-/tmp/readme-intern-demo}"
rm -rf "$DEMO" && mkdir -p "$DEMO" && cd "$DEMO"
git init -q
git config user.name demo && git config user.email demo@example.com

mkdir -p weatherdash scripts
cat > pyproject.toml <<'EOF'
[project]
name = "weatherdash"
version = "0.1.0"
requires-python = ">=3.11"
EOF

cat > weatherdash/__init__.py <<'EOF'
EOF

cat > weatherdash/server.py <<'EOF'
import csv, json, os, http.server, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("PORT", "8000"))

def load():
    token = os.environ.get("WEATHER_TOKEN")
    if not token:
        raise SystemExit("WEATHER_TOKEN is not set (see .env.example)")
    with open(ROOT / "data" / "cities.csv") as fh:
        return list(csv.DictReader(fh))

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(CITIES).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    CITIES = load()
    print(f"weatherdash listening on http://localhost:{PORT}", flush=True)
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
EOF

cat > scripts/seed_db.py <<'EOF'
import csv, pathlib
out = pathlib.Path(__file__).resolve().parent.parent / "data"
out.mkdir(exist_ok=True)
with open(out / "cities.csv", "w", newline="") as fh:
    w = csv.writer(fh); w.writerow(["city", "temp_c"])
    w.writerows([["Rotterdam", 14], ["Athens", 24], ["Toronto", 9]])
print("seeded", out / "cities.csv")
EOF

cat > Makefile <<'EOF'
serve:
	. .env && python -m weatherdash.server
EOF

cat > .gitignore <<'EOF'
.env
.env.*
data/
.venv/
__pycache__/
EOF

cat > README.md <<'EOF'
# weatherdash

Tiny JSON weather dashboard.

## Requirements

Python 3.9+

## Quickstart

```bash
git clone https://github.com/example/weatherdash.git
cd weatherdash
python3 -m venv .venv && source .venv/bin/activate
cp .env.example .env
python scripts/seed.py
make run
```

Then open http://localhost:8000.

## Contributing

```bash
pip install -r requirements-dev.txt
pytest
```
EOF

# The author's machine has these, so everything "works for me":
echo 'export WEATHER_TOKEN=abc123' > .env.example
cp .env.example .env
mkdir -p data && python3 scripts/seed_db.py >/dev/null

git add -A && git commit -qm "weatherdash: initial commit"
echo "Demo repo ready at $DEMO"
echo "It runs fine here. Ask Claude: /readme-intern"
