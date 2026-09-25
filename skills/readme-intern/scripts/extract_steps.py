#!/usr/bin/env python3
"""
extract_steps.py - turn a README into an ordered, classified list of shell steps
and run fast static checks that need no execution.

Usage:
  python3 extract_steps.py --repo . [--readme README.md] [--config .readme-intern/config.json] > plan.json

Output (JSON on stdout):
  {
    "readme": "README.md",
    "repo_name": "myproj",
    "prereqs": {"stated": {...}, "pinned": {...}},
    "suggested_image": "node:20-bookworm",
    "steps": [ {id, section, line, lang, cmd, include, kind, flags, reason, source} ],
    "static_findings": [ {severity, code, line, message, evidence} ]
  }

Pure standard library. Python 3.8+.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

SHELL_LANGS = {"bash", "sh", "shell", "zsh", "console", "shell-session", "shellsession",
               "terminal", "shellscript", "sh-session", "cli", "cmd-line", "text-sh", ""}
WINDOWS_LANGS = {"powershell", "ps1", "pwsh", "cmd", "bat", "batch", "bat-script"}

# Sections a first-time user follows, strongest first.
INCLUDE_SECTION = re.compile(
    r"(quick\s*-?\s*start|getting\s+started|install|setup|set\s+up|usage|run(ning)?\b|"
    r"develop(ment)?|build(ing)?|local|try\s+it|example|first\s+steps|how\s+to\s+use|"
    r"from\s+source|requirements|prerequisites|start)", re.I)
EXCLUDE_SECTION = re.compile(
    r"(contribut|deploy|release|publish|licen[cs]e|faq|troubleshoot|changelog|"
    r"acknowledg|credit|sponsor|citation|cite|roadmap|benchmark|docker\s*hub|"
    r"uninstall|migrat|upgrad|windows|macos only|kubernetes|helm|production|"
    r"api\s+reference|advanced|security|support|team|authors?)", re.I)

PLACEHOLDER = re.compile(
    r"(<[A-Za-z][A-Za-z0-9_ .\-/]*>|\bYOUR[_-][A-Z0-9_]+\b|\byour[-_ ]?(api[-_ ]?key|token|"
    r"username|user|password|project|org|repo|name|email|domain)\b|\[your[^\]]*\]|"
    r"\bxxxx+\b|\bINSERT[_ ]|\bREPLACE[_ ]?ME\b|\bCHANGE[_ ]?ME\b|<\.\.\.>|\s\.\.\.(\s|$))")
SERVER = re.compile(
    r"(^|\s|&&\s*|;\s*)("
    r"(npm|pnpm|yarn|bun)\s+(run\s+)?(dev|start|serve|preview|watch)\b|"
    r"npx\s+(vite|next\s+(dev|start)|serve|http-server|nodemon)\b|"
    r"next\s+(dev|start)\b|vite(\s|$)|nodemon\b|"
    r"(python3?\s+-m\s+)?(uvicorn|gunicorn|hypercorn|daphne)\b|"
    r"(python3?\s+-m\s+)?flask\s+run\b|fastapi\s+(dev|run)\b|streamlit\s+run\b|"
    r"gradio\b|panel\s+serve\b|jupyter\s+(lab|notebook)\b|"
    r"python3?\s+manage\.py\s+runserver\b|python3?\s+-m\s+http\.server\b|"
    r"(bundle\s+exec\s+)?rails\s+(s|server)\b|php\s+artisan\s+serve\b|php\s+-S\b|"
    r"hugo\s+server\b|jekyll\s+serve\b|mkdocs\s+serve\b|docusaurus\s+start\b|"
    r"docker[- ]compose\s+up(?!.*\s-d\b)(?!.*--detach)|"
    r"air\b|cargo\s+watch\b|dotnet\s+(run|watch)\b.*|mvn\s+spring-boot:run\b|"
    r"\./gradlew\s+bootRun\b|go\s+run\s+.*(server|serve|cmd/)"
    r")", re.I)
RISKY = [
    (re.compile(r"\bcurl\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b|\bwget\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b"), "pipe-to-shell"),
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME)(\s|$)"), "destructive-rm"),
    (re.compile(r"\bmkfs\b|\bdd\s+if="), "disk-write"),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"), "chmod-777"),
    (re.compile(r"(^|\s)sudo\s"), "sudo"),
]
MAC_ONLY = re.compile(r"(^|\s)(brew|port|open|pbcopy|xcode-select|mas)\s")
WIN_ONLY = re.compile(r"(^|\s)(choco|winget|scoop|set\s+\w+=|\.\\|[A-Za-z]:\\)")
INTERACTIVE_HINT = re.compile(
    r"\b(npm\s+init(?!.*\s-y)|yarn\s+init(?!.*\s-y)|npx\s+create-[\w-]+(?!.*(--yes|-y|--default))|"
    r"npm\s+create\s+[\w@/-]+(?!.*(--yes|-y|--\s))|gh\s+auth\s+login|aws\s+configure|"
    r"gcloud\s+(init|auth\s+login)|firebase\s+login|vercel\s+login|heroku\s+login|"
    r"docker\s+login|npm\s+login|huggingface-cli\s+login|wandb\s+login)")
CLONE = re.compile(r"\bgit\s+clone\s+(?:--?[\w-]+(?:[= ]\S+)?\s+)*(\S+)(?:\s+(\S+))?")


def sh(args, cwd):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def git_files(repo):
    out = sh(["git", "ls-files"], repo)
    if out:
        return set(out.splitlines())
    files = set()
    for root, dirs, fs in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", ".venv", "venv", "dist", "build"}]
        for f in fs:
            files.add(os.path.relpath(os.path.join(root, f), repo).replace(os.sep, "/"))
    return files


def git_ignored(repo, path):
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=repo).returncode == 0


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


# ----------------------------------------------------------------------------- markdown

def parse_blocks(text):
    """Yield (section_path, lang, first_body_line, body_lines, text_before_fence)."""
    lines = text.splitlines()
    headings = []  # stack of (level, title)
    i = 0
    in_html_comment = False
    while i < len(lines):
        line = lines[i]
        if "<!--" in line and "-->" not in line:
            in_html_comment = True
        if in_html_comment:
            if "-->" in line:
                in_html_comment = False
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            level = len(m.group(1))
            headings = [h for h in headings if h[0] < level] + [(level, m.group(2))]
            i += 1
            continue
        # setext headings
        if i + 1 < len(lines) and line.strip() and re.match(r"^(=+|-+)\s*$", lines[i + 1]) \
                and not line.lstrip().startswith(("-", "*", ">", "|")):
            level = 1 if lines[i + 1].startswith("=") else 2
            headings = [h for h in headings if h[0] < level] + [(level, line.strip())]
            i += 2
            continue
        f = re.match(r"^(\s*)(`{3,}|~{3,})\s*([\w+\-.#]*)[^\n]*$", line)
        if f:
            indent, fence, lang = len(f.group(1)), f.group(2), f.group(3).lower()
            start = i + 1
            body = []
            i += 1
            while i < len(lines) and not re.match(r"^\s*" + re.escape(fence[0]) + "{" + str(len(fence)) + ",}\s*$", lines[i]):
                body.append(lines[i][indent:] if lines[i][:indent].strip() == "" else lines[i])
                i += 1
            i += 1
            before = [l.strip() for l in lines[max(0, start - 5):start - 1] if l.strip()][-2:]
            yield [h[1] for h in headings], lang, start + 1, body, before  # 1-based line of first body line
            continue
        i += 1


def split_commands(lang, body, first_line):
    """Return list of (line_no, command) from a code block."""
    prompt_re = re.compile(r"^\s*(\$|>|%|❯|PS>|PS [A-Z]:\\[^>]*>)\s?")
    has_prompts = any(prompt_re.match(l) for l in body)
    is_console = lang in {"console", "shell-session", "shellsession", "terminal", "sh-session"} or has_prompts
    raw = "\n".join(body)
    # Constructs that must stay together.
    if re.search(r"<<-?\s*['\"]?\w+", raw) or re.search(r"^\s*(if|for|while|case|function)\b|\(\)\s*\{", raw, re.M):
        cmd = "\n".join(prompt_re.sub("", l, count=1) if is_console else l for l in body).strip()
        return [(first_line, cmd)] if cmd else []
    out = []
    buf, buf_line = "", None
    for idx, l in enumerate(body):
        ln = first_line + idx
        if is_console:
            if prompt_re.match(l):
                l = prompt_re.sub("", l, count=1)
            elif not buf:
                continue  # output line
        s = l.rstrip()
        if not buf and (not s.strip() or s.strip().startswith("#")):
            continue
        if buf_line is None:
            buf_line = ln
        if s.endswith("\\"):
            buf += s[:-1] + " "
            continue
        buf += s
        cmd = re.sub(r"\s+#\s[^'\"]*$", "", buf).strip()  # trailing comment
        if cmd:
            out.append((buf_line, cmd))
        buf, buf_line = "", None
    if buf.strip():
        out.append((buf_line, buf.strip()))
    return out


COMMON_CMDS = set("""git cd npm npx pnpm yarn bun bunx pip pip3 python python3 py uv uvx poetry pipx conda mamba
make cmake ctest cargo rustup go docker docker-compose podman curl wget brew apt apt-get dnf yum pacman apk sudo
export source cp mv mkdir cat echo node deno java javac mvn gradle bundle gem rails rake composer php dotnet
bash sh zsh chmod ln rm touch ls pytest tox nox just task helm kubectl terraform flask uvicorn gunicorn streamlit
jupyter Rscript julia swift flutter dart mix elixir stack cabal nvm fnm volta asdf mise corepack direnv
tar unzip gh hugo jekyll mkdocs deno vite next ng expo cordova ionic pod xcodebuild ansible vagrant""".split())


def looks_like_shell(cmd):
    """For code blocks with no language tag: accept only things a reader would type in a shell."""
    first = cmd.split()[0] if cmd.split() else ""
    if re.match(r"^[A-Z_][A-Z0-9_]*=", first):
        return True
    return first in COMMON_CMDS or first.startswith(("./", "~/", "$")) or bool(re.match(r"^\.?\w[\w.-]*/[\w./-]+$", first))


# ----------------------------------------------------------------------------- prereqs

def requirement_text(text):
    """Prose lines that state requirements. Skips code blocks, tables and quoted examples."""
    keep, in_code = [], False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_code = not in_code
            continue
        if in_code or line.lstrip().startswith(("|", ">")):
            continue
        if re.search(r"(require|need|prereq|depend|install|support|version|>=|\d\+|or\s+(higher|later|newer)|minimum|at\s+least|^\s*[-*]\s)", line, re.I) \
                or re.match(r"^\s*[-*]?\s*(node|python|go|java|ruby|rust)", line, re.I):
            keep.append(line)
    return "\n".join(keep)


def stated_prereqs(text):
    found = {}
    pats = {
        "node": r"node(?:\.?js)?\s*(?:version\s*)?(?:v|>=?\s*|\^|~)?\s*(\d{1,2})(?:\.\d+)*\s*(\+|or\s+(?:higher|later|newer)|and\s+up)?",
        "python": r"python\s*(?:version\s*)?(?:>=?\s*)?(3\.\d{1,2})(?:\.\d+)?\s*(\+|or\s+(?:higher|later|newer))?",
        "go": r"\bgo(?:lang)?\s*(?:version\s*)?(?:>=?\s*)?(1\.\d{1,2})",
        "java": r"\b(?:java|jdk|openjdk)\s*(?:version\s*)?(\d{1,2})\b",
        "ruby": r"\bruby\s*(?:version\s*)?(\d\.\d)",
        "rust": r"\brust(?:c)?\s*(?:version\s*)?(1\.\d{2,3})",
    }
    for k, p in pats.items():
        m = re.search(p, text, re.I)
        if m:
            found[k] = m.group(1)
    for tool in ["docker", "postgres", "postgresql", "redis", "mysql", "mongodb", "cuda", "ffmpeg", "make", "uv", "poetry", "pnpm", "yarn", "bun"]:
        if re.search(r"\b" + tool + r"\b", text, re.I):
            found.setdefault("mentions", []).append(tool)
    return found


def pinned_prereqs(repo, files):
    pins = {}
    def f(p):
        return read(os.path.join(repo, p)) if p in files else None
    v = f(".nvmrc") or f(".node-version")
    if v:
        m = re.search(r"(\d+)", v)
        if m:
            pins["node"] = (m.group(1), ".nvmrc" if ".nvmrc" in files else ".node-version")
    pj = f("package.json")
    if pj:
        try:
            eng = (json.loads(pj).get("engines") or {}).get("node")
            m = re.search(r"(\d+)", eng or "")
            if m and "node" not in pins:
                pins["node"] = (m.group(1), "package.json engines")
        except ValueError:
            pass
    v = f(".python-version")
    if v:
        m = re.search(r"(3\.\d+)", v)
        if m:
            pins["python"] = (m.group(1), ".python-version")
    pp = f("pyproject.toml")
    if pp and "python" not in pins:
        m = re.search(r"requires-python\s*=\s*['\"][^'\"]*?(3\.\d+)", pp)
        if m:
            pins["python"] = (m.group(1), "pyproject.toml requires-python")
    gm = f("go.mod")
    if gm:
        m = re.search(r"^go\s+(1\.\d+)", gm, re.M)
        if m:
            pins["go"] = (m.group(1), "go.mod")
    tv = f(".tool-versions")
    if tv:
        for line in tv.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                name = {"nodejs": "node"}.get(parts[0], parts[0])
                m = re.search(r"(\d+(?:\.\d+)?)", parts[1])
                if m and name not in pins:
                    pins[name] = (m.group(1), ".tool-versions")
    return pins


def suggest_image(stated, pinned, files):
    def pick(k):
        if k in stated:
            return stated[k]
        if k in pinned:
            return pinned[k][0]
        return None
    if "package.json" in files or "node" in stated:
        v = pick("node") or "20"
        return "node:%s-bookworm" % v.split(".")[0]
    if any(x in files for x in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile")) or "python" in stated:
        return "python:%s-bookworm" % (pick("python") or "3.12")
    if "go.mod" in files:
        return "golang:%s-bookworm" % (pick("go") or "1.22")
    if "Cargo.toml" in files:
        return "rust:1-bookworm"
    if "Gemfile" in files:
        return "ruby:%s-bookworm" % (pick("ruby") or "3.3")
    if "pom.xml" in files or "build.gradle" in files or "build.gradle.kts" in files:
        return "eclipse-temurin:%s-jdk" % (pick("java") or "21")
    return "buildpack-deps:bookworm"


# ----------------------------------------------------------------------------- static checks

def static_checks(repo, files, steps, stated, pinned, readme_text):
    findings = []
    prefixes = {os.path.basename(repo.rstrip("/"))} | {s.get("clone_dir") for s in steps if s.get("clone_dir")}

    def committed(p):
        p = p[2:] if p.startswith("./") else p
        for pre in prefixes:
            if pre and p.startswith(pre + "/"):
                p = p[len(pre) + 1:]
        return p in files or any(x.endswith("/" + p) for x in files) or any(fnmatch.fnmatch(x, p) for x in files)
    def add(sev, code, line, msg, ev=""):
        findings.append({"severity": sev, "code": code, "line": line, "message": msg, "evidence": ev})

    # Version drift: README states one version, the repo pins another.
    for k, v in stated.items():
        if k == "mentions" or k not in pinned:
            continue
        pv, where = pinned[k]
        if v.split(".")[:2] != pv.split(".")[:2] and not (k == "node" and v.split(".")[0] == pv.split(".")[0]):
            add("high", "version-drift", None,
                "README says %s %s but %s pins %s." % (k, v, where, pv), "%s %s vs %s" % (k, v, pv))

    pkg = {}
    if "package.json" in files:
        try:
            pkg = json.loads(read(os.path.join(repo, "package.json")) or "{}")
        except ValueError:
            add("high", "invalid-json", None, "package.json is not valid JSON.")
    scripts = pkg.get("scripts") or {}
    make_targets = set()
    for mf in ("Makefile", "makefile", "GNUmakefile"):
        if mf in files:
            make_targets |= set(re.findall(r"^([A-Za-z0-9_.\-/]+)\s*:(?!=)", read(os.path.join(repo, mf)) or "", re.M))
    just_recipes = set()
    if "justfile" in files or "Justfile" in files:
        jf = read(os.path.join(repo, "justfile" if "justfile" in files else "Justfile")) or ""
        just_recipes = set(re.findall(r"^([A-Za-z0-9_\-]+)(?:\s+[^:=]*)?:(?!=)", jf, re.M))

    lockfiles = {"package-lock.json": "npm", "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "bun.lockb": "bun", "bun.lock": "bun"}
    present_pm = {pm for lf, pm in lockfiles.items() if lf in files}

    for st in steps:
        if not st["include"]:
            continue
        cmd, line = st["cmd"], st["line"]
        # npm/pnpm/yarn/bun scripts referenced but missing
        for m in re.finditer(r"\b(npm|pnpm|yarn|bun)\s+(?:run\s+)?([A-Za-z0-9:_\-.]+)", cmd):
            pm, name = m.group(1), m.group(2)
            builtin = {"install", "i", "ci", "add", "remove", "exec", "dlx", "x", "create", "init", "link",
                       "publish", "test", "start", "run", "global", "update", "upgrade", "outdated", "audit",
                       "config", "set", "why", "list", "ls", "prune", "rebuild", "pack", "version", "login",
                       "-g", "--global", "install-clean", "uninstall", "help", "workspace", "workspaces", "-v", "--version"}
            if present_pm and pm not in present_pm and re.search(r"\b%s\s+(i|install|ci|add)\b" % pm, cmd):
                add("medium", "package-manager-mismatch", line,
                    "README uses `%s install` but the committed lockfile is for %s, so the reader gets different dependency versions than you tested." % (pm, "/".join(sorted(present_pm))), cmd)
            if name in builtin and not re.search(r"\brun\s+" + re.escape(name), cmd):
                if name in ("test", "start") and scripts and name not in scripts and not (name == "start" and "server.js" in files):
                    add("high", "missing-script", line, "README runs `%s %s` but package.json has no \"%s\" script." % (pm, name, name), cmd)
                continue
            if scripts and name not in scripts and not name.startswith("-"):
                add("high", "missing-script", line,
                    "README runs `%s %s` but package.json has no \"%s\" script (has: %s)." % (
                        pm, (("run " if "run " in m.group(0) else "") + name), name, ", ".join(sorted(scripts)) or "none"), cmd)
        for m in re.finditer(r"\bmake\s+([A-Za-z0-9_.\-/]+)", cmd):
            if make_targets and m.group(1) not in make_targets and not m.group(1).startswith("-"):
                add("high", "missing-make-target", line, "README runs `make %s` but the Makefile has no such target." % m.group(1), cmd)
            if not make_targets:
                add("high", "missing-makefile", line, "README runs `make` but there is no Makefile in the repo.", cmd)
        for m in re.finditer(r"\bjust\s+([A-Za-z0-9_\-]+)", cmd):
            if just_recipes and m.group(1) not in just_recipes:
                add("high", "missing-just-recipe", line, "README runs `just %s` but the justfile has no such recipe." % m.group(1), cmd)
        # Files the README tells you to copy or run that are not committed.
        for m in re.finditer(r"(?:^|\s|&&|;)(?:cp|copy|mv|source|\.)\s+(?:-\w+\s+)*([\w./-]+\.(?:example|sample|template|dist|env[\w.]*|sh|ya?ml|json|toml|ini|cfg|conf))\b", cmd):
            p = os.path.normpath(m.group(1)).replace(os.sep, "/")
            if p.startswith("..") or p.startswith("/") or p.startswith("~"):
                continue
            # Only the source of cp/mv must exist; the first path after the verb is the source.
            if p and not committed(p):
                ign = git_ignored(repo, p)
                add("high", "untracked-file", line,
                    "README uses `%s` but it is not committed%s. A fresh clone will not have it." % (
                        p, " (it matches .gitignore)" if ign else ""), cmd)
        for m in re.finditer(r"(?:^|&&|;|\s)(?:python3?|node|bash|sh|ruby|deno\s+run|bun|tsx|ts-node)\s+(?:-[\w-]+\s+)*([\w./-]+\.(?:py|js|mjs|cjs|ts|sh|rb))\b", cmd):
            p = m.group(1)
            p_norm = os.path.normpath(p).replace(os.sep, "/")
            if p_norm.startswith(".."):
                continue
            if not committed(p_norm):
                add("high", "missing-file", line, "README runs `%s` but no such file is committed." % p, cmd)
        for m in re.finditer(r"(?:^|\n|&&|;|\|\|)\s*(\./[\w./-]+)", cmd):
            p = m.group(1)[2:]
            if p not in files and not any(x.endswith("/" + p) for x in files):
                add("high", "missing-file", line, "README runs `%s` but no such file is committed." % m.group(1), cmd)
            elif os.path.exists(os.path.join(repo, p)) and not os.access(os.path.join(repo, p), os.X_OK):
                mode = sh(["git", "ls-files", "-s", p], repo).split(" ")[0]
                if mode and mode != "100755":
                    add("high", "not-executable", line, "README runs `%s` but the file is not committed as executable (mode %s)." % (m.group(1), mode), cmd)
        for m in re.finditer(r"\bdocker[- ]compose\b(?:\s+-f\s+(\S+))?", cmd):
            f = m.group(1)
            candidates = [f] if f else ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]
            if not any(c in files for c in candidates):
                add("high", "missing-file", line, "README runs docker compose but no compose file is committed.", cmd)
        if re.search(r"\bpip3?\s+install\s+(-r\s+|--requirement\s+)(\S+)", cmd):
            req = re.search(r"(-r\s+|--requirement\s+)(\S+)", cmd).group(2)
            if req not in files:
                add("high", "missing-file", line, "README installs from `%s` but it is not committed." % req, cmd)
        if "placeholder" in st["flags"]:
            add("low", "placeholder", line, "Step needs a value the reader must supply. Say where to get it, or make the step work without it.", cmd)
        if "macos-only" in st["flags"]:
            add("medium", "platform-gap", line, "Step uses a macOS-only tool with no Linux alternative nearby.", cmd)
        if "pipe-to-shell" in st["flags"]:
            add("low", "pipe-to-shell", line, "Step pipes a download into a shell. Readers who audit installs will stop here.", cmd)

    if not any(s["include"] for s in steps):
        add("high", "no-runnable-steps", None,
            "No runnable shell steps found in install/quickstart sections. A first-time user has nothing to copy.")
    # Deduplicate
    seen, uniq = set(), []
    for f in findings:
        k = (f["code"], f["line"], f["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


# ----------------------------------------------------------------------------- main

FILE_HINT_IN_BLOCK = re.compile(
    r"^\s*(?:#|//|--|;|<!--|/\*)\s*(?:save\s+(?:this\s+)?(?:as|to|in)|create|file(?:name)?\s*:|in)\s+`?([\w./-]+\.[A-Za-z0-9]+)`?", re.I)
FILE_HINT_BEFORE = re.compile(
    r"(?:save|create|add|put|write|make)\b[^.\n]{0,40}?(?:file\s+)?(?:called|named|as|to|in)?\s*`([\w./-]+\.[A-Za-z0-9]+)`", re.I)


def file_target(body, before):
    """Detect README blocks meant to be saved as a file: '# save this as app.py' or 'Create `app.py`:'."""
    first = next((l for l in body if l.strip()), "")
    m = FILE_HINT_IN_BLOCK.match(first)
    if m:
        return m.group(1).lstrip("./") or None
    if before:
        last = before[-1]
        m = FILE_HINT_BEFORE.search(last)
        if m and last.rstrip().endswith(":"):
            return m.group(1).lstrip("./") or None
    return None


def classify(cmd, lang, section_path, repo_name):
    flags = []
    kind = "run"
    if lang in WINDOWS_LANGS or WIN_ONLY.search(cmd):
        flags.append("windows-only")
    if MAC_ONLY.search(cmd):
        flags.append("macos-only")
    if PLACEHOLDER.search(cmd):
        flags.append("placeholder")
    for rx, name in RISKY:
        if rx.search(cmd):
            flags.append(name)
    if INTERACTIVE_HINT.search(cmd):
        flags.append("interactive")
    m = CLONE.search(cmd)
    if m:
        kind = "clone"
    elif SERVER.search(cmd) and not re.search(r"(--help|-h)\b|&\s*$", cmd):
        kind = "server"
    elif re.match(r"^\s*cd\s", cmd) and "&&" not in cmd:
        kind = "cd"
    elif re.search(r"\b(test|pytest|jest|vitest|go\s+test|cargo\s+test|rspec|phpunit|tox|nox)\b", cmd):
        kind = "test"
    elif re.search(r"\b(install|ci|sync|restore|bundle|add|get|fetch|download)\b", cmd):
        kind = "install"
    return kind, flags


def include_decision(section_path, flags, lang):
    joined = " / ".join(section_path)
    last = section_path[-1] if section_path else ""
    if lang in WINDOWS_LANGS or "windows-only" in flags:
        return False, "Windows-only step"
    if any(EXCLUDE_SECTION.search(s) for s in section_path[1:] or section_path):
        if not INCLUDE_SECTION.search(last):
            return False, "section '%s' is not part of first-run setup" % joined
    if not section_path or INCLUDE_SECTION.search(joined):
        return True, ""
    return True, "section '%s' has no install/usage heading; included by default" % joined


def resolve_aliases(repo, files, steps):
    """`make serve` / `npm run dev` hide what actually runs. Look inside so servers are detected."""
    scripts = {}
    if "package.json" in files:
        try:
            scripts = json.loads(read(os.path.join(repo, "package.json")) or "{}").get("scripts") or {}
        except ValueError:
            pass
    recipes = {}
    for mf in ("Makefile", "makefile", "GNUmakefile"):
        if mf in files:
            cur = None
            for line in (read(os.path.join(repo, mf)) or "").splitlines():
                m = re.match(r"^([A-Za-z0-9_.\-/]+)\s*:(?!=)", line)
                if m:
                    cur = m.group(1)
                    recipes[cur] = ""
                elif cur and line.startswith("\t"):
                    recipes[cur] += line.strip() + "\n"
    for st in steps:
        if st["kind"] not in ("run", "install"):
            continue
        body = ""
        m = re.search(r"\bmake\s+([A-Za-z0-9_.\-/]+)", st["cmd"])
        if m:
            body = recipes.get(m.group(1), "")
        m = re.search(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?([A-Za-z0-9:_\-.]+)", st["cmd"])
        if m and m.group(1) in scripts:
            body = scripts[m.group(1)]
        if body and (SERVER.search(body) or re.search(r"serve_forever|\.listen\(|http\.server|runserver|--watch", body)
                     or re.search(r"python3?\s+-m\s+[\w.]*(server|app|serve|web)\b", body)):
            st["kind"] = "server"
            st["resolved"] = body.strip()[:200]


def load_config(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def apply_config(steps, cfg):
    for st in steps:
        for rule in cfg.get("skip", []):
            if rule.get("match") and rule["match"] in st["cmd"]:
                st["include"], st["reason"] = False, "config skip: " + rule.get("reason", "")
        for rule in cfg.get("include", []):
            if rule.get("match") and rule["match"] in st["cmd"]:
                st["include"], st["reason"] = True, "config include"
        for rule in cfg.get("overrides", []):
            if rule.get("match") and rule["match"] in st["cmd"]:
                for k in ("kind", "port", "probe", "timeout", "expect_exit", "cwd"):
                    if k in rule:
                        st[k] = rule[k]
                if "replace" in rule:
                    st["original_cmd"], st["cmd"] = st["cmd"], rule["replace"]
    return steps


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--readme", default=None, help="path relative to repo (default: README.md or first README*)")
    ap.add_argument("--config", default=None, help="default: <repo>/.readme-intern/config.json if present")
    ap.add_argument("--all-sections", action="store_true", help="include steps from every section")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    files = git_files(repo)
    cfg_path = args.config or os.path.join(repo, ".readme-intern", "config.json")
    cfg = load_config(cfg_path)
    readme = args.readme or cfg.get("readme")
    if not readme:
        cands = sorted([f for f in files if "/" not in f and re.match(r"(?i)readme(\.(md|markdown|mdx|rst|txt))?$", f)],
                       key=lambda f: (not f.lower().endswith(".md"), f))
        readme = cands[0] if cands else "README.md"
    text = read(os.path.join(repo, readme))
    if text is None:
        print(json.dumps({"error": "README not found: %s" % readme}))
        sys.exit(2)

    repo_name = os.path.basename(repo.rstrip("/"))
    remote = sh(["git", "remote", "get-url", "origin"], repo).strip()
    if remote:
        repo_name = re.sub(r"\.git$", "", remote.rstrip("/").split("/")[-1].split(":")[-1])

    steps = []
    n = 0
    for section_path, lang, first_line, body, before in parse_blocks(text):
        fname = file_target(body, before)
        if fname and lang not in WINDOWS_LANGS:
            content = "\n".join(body)
            n += 1
            inc, reason = include_decision(section_path, [], lang)
            steps.append({"id": n, "section": " / ".join(section_path), "line": first_line, "lang": lang or "(none)",
                          "cmd": "mkdir -p \"$(dirname '%s')\" && cat > '%s' <<'READMEINTERN_EOF'\n%s\nREADMEINTERN_EOF" % (fname, fname, content),
                          "include": inc, "kind": "file", "flags": [], "reason": reason, "source": "readme-file",
                          "file": fname})
            continue
        if lang not in SHELL_LANGS and lang not in WINDOWS_LANGS:
            continue
        for line_no, cmd in split_commands(lang, body, first_line):
            if lang == "" and not looks_like_shell(cmd):
                continue
            kind, flags = classify(cmd, lang, section_path, repo_name)
            inc, reason = include_decision(section_path, flags, lang)
            if args.all_sections and lang not in WINDOWS_LANGS:
                inc, reason = True, ""
            n += 1
            st = {"id": n, "section": " / ".join(section_path), "line": line_no, "lang": lang or "(none)",
                  "cmd": cmd, "include": inc, "kind": kind, "flags": flags, "reason": reason, "source": "readme"}
            if kind == "clone":
                m = CLONE.search(cmd)
                st["clone_url"] = m.group(1)
                st["clone_dir"] = m.group(2) if m.group(2) and not m.group(2).startswith(("&", ";")) else \
                    re.sub(r"\.git$", "", m.group(1).rstrip("/").split("/")[-1])
            steps.append(st)

    resolve_aliases(repo, files, steps)
    first_clone = next((s for s in steps if s["kind"] == "clone" and s["include"]), None)
    for s in steps:
        if s["kind"] == "clone":
            base = re.sub(r"\.git$", "", s["clone_url"].rstrip("/").split("/")[-1].split(":")[-1])
            s["is_self"] = base.lower() == repo_name.lower() or (not remote and s is first_clone)
    steps = apply_config(steps, cfg)
    stated = stated_prereqs(requirement_text(text))
    pinned = pinned_prereqs(repo, files)
    out = {
        "readme": readme,
        "repo": repo,
        "repo_name": repo_name,
        "prereqs": {"stated": stated, "pinned": {k: {"version": v[0], "source": v[1]} for k, v in pinned.items()}},
        "suggested_image": cfg.get("image") or suggest_image(stated, pinned, files),
        "config": cfg_path if cfg else None,
        "steps": steps,
        "static_findings": static_checks(repo, files, steps, stated, pinned, text),
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
