#!/usr/bin/env python3
"""
run_steps.py - execute README steps exactly as a first-time user would, on a clean machine.

  python3 run_steps.py --plan plan.json --out .readme-intern/run [--image node:20-bookworm | --local]

What "clean" means here:
  * The repo is re-cloned from git, so gitignored files (.env, node_modules, .venv, build output)
    and anything only on your machine are NOT present. Uncommitted edits to tracked files and new
    untracked (non-ignored) files ARE included, so you can test README fixes before committing.
  * Docker mode (default): a fresh container from --image, root user, CI=1, stdin closed.
    A `sudo` shim is installed if the image lacks sudo, since most readers have it.
  * Local mode (--local): a temp dir with a fresh $HOME. Your global tools still leak in,
    so results are weaker. The report says so.

Shell state (cd, export, `source venv/bin/activate`) carries across steps like a real terminal.
Server steps (npm run dev, uvicorn, ...) run in the background, are probed for a listening port,
and stay up so later steps (curl localhost:3000) work.

Writes <out>/results.json and one log per step. Exit code: 0 all included steps passed,
1 a step failed, 2 setup error. Pure standard library, Python 3.8+.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

URL_PORT = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]|\[::1\]):(\d{2,5})|"
                      r"(?:listening|running|serving|started|available|ready)[^\n]{0,60}?\bport\s*[:=]?\s*(\d{2,5})", re.I)


def now():
    return time.time()


def tail(text, n=60):
    lines = text.splitlines()
    return "\n".join(lines[-n:])


# ----------------------------------------------------------------------------- snapshot

def snapshot_repo(repo, dest):
    """Clone repo into dest, then overlay uncommitted tracked changes and untracked, non-ignored files."""
    repo = os.path.abspath(repo)
    if not os.path.isdir(os.path.join(repo, ".git")):
        shutil.copytree(repo, dest, ignore=shutil.ignore_patterns("node_modules", ".venv", "venv", "__pycache__", ".env"))
        return {"mode": "copy (not a git repo)", "dirty": []}
    r = subprocess.run(["git", "clone", "-q", "--no-hardlinks", repo, dest], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git clone of snapshot failed: " + r.stderr)
    def git_lines(*a):
        return subprocess.run(["git"] + list(a), cwd=repo, capture_output=True, text=True).stdout.splitlines()
    has_head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=repo, capture_output=True).returncode == 0
    base = "HEAD" if has_head else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # empty tree
    # staged + unstaged edits to tracked files, plus new untracked files that are not gitignored
    changed = git_lines("diff", "--name-only", "--diff-filter=d", base) + git_lines("ls-files", "-o", "--exclude-standard")
    deleted = git_lines("diff", "--name-only", "--diff-filter=D", base)
    own = (".readme-intern/run/", ".readme-intern/plan.json", ".readme-intern/REPORT.md")
    changed = [c for c in changed if not c.startswith(own)]
    for rel in changed:
        src = os.path.join(repo, rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(dest, rel)) or dest, exist_ok=True)
            shutil.copy2(src, os.path.join(dest, rel))
    for rel in deleted:
        p = os.path.join(dest, rel)
        if os.path.exists(p):
            os.remove(p)
    if changed or deleted:
        env = dict(os.environ, GIT_AUTHOR_NAME="readme-intern", GIT_AUTHOR_EMAIL="readme-intern@localhost",
                   GIT_COMMITTER_NAME="readme-intern", GIT_COMMITTER_EMAIL="readme-intern@localhost")
        subprocess.run(["git", "add", "-A"], cwd=dest, capture_output=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "readme-intern: working tree snapshot", "--no-verify"],
                       cwd=dest, capture_output=True, env=env)
    return {"mode": "git snapshot", "dirty": sorted(set(changed) | set(deleted))}


# ----------------------------------------------------------------------------- step script

def step_script(cmd, state_dir):
    """Bash wrapper that restores cwd/env from the previous step and saves them after this one."""
    return """#!/usr/bin/env bash
__RP_STATE=%(state)s
[ -f "$__RP_STATE/env" ] && source "$__RP_STATE/env" >/dev/null 2>&1
cd "$(cat "$__RP_STATE/cwd")" 2>/dev/null || true
__rp_save() { export -p > "$__RP_STATE/env.tmp" 2>/dev/null && mv "$__RP_STATE/env.tmp" "$__RP_STATE/env"; pwd > "$__RP_STATE/cwd"; }
trap __rp_save EXIT
set -o pipefail
%(cmd)s
""" % {"state": shlex.quote(state_dir), "cmd": cmd}


# ----------------------------------------------------------------------------- backends

class Backend:
    def exec(self, script_path, timeout):
        raise NotImplementedError

    def start_bg(self, script_path, log_path):
        raise NotImplementedError

    def alive(self, handle):
        raise NotImplementedError

    def listening_ports(self):
        raise NotImplementedError

    def probe(self, port, path="/"):
        raise NotImplementedError

    def read_log(self, handle):
        raise NotImplementedError

    def close(self):
        pass


PROC_PORTS_SNIPPET = r"""
for f in /proc/net/tcp /proc/net/tcp6; do
  [ -r "$f" ] || continue
  while read -r _sl local _rem st _rest; do
    [ "$st" = "0A" ] && echo $((16#${local##*:}))
  done < <(tail -n +2 "$f")
done | sort -un
"""
PROC_PORTS_PY = r"""
import sys
ports=set()
for f in ("/proc/net/tcp","/proc/net/tcp6"):
    try:
        for line in open(f).read().splitlines()[1:]:
            p=line.split()
            if len(p)>3 and p[3]=="0A": ports.add(int(p[1].split(":")[1],16))
    except OSError: pass
print("\n".join(map(str,sorted(ports))))
"""
PROBE_SNIPPET = r"""
p=%(port)s; path=%(path)s
if command -v curl >/dev/null 2>&1; then curl -s -o /dev/null -m 5 -w '%%{http_code}' "http://127.0.0.1:$p$path"; exit 0; fi
if command -v wget >/dev/null 2>&1; then wget -q -S -O /dev/null -T 5 "http://127.0.0.1:$p$path" 2>&1 | awk '/HTTP\//{c=$2} END{print c}'; exit 0; fi
if command -v python3 >/dev/null 2>&1; then python3 -c "import urllib.request,sys
try: print(urllib.request.urlopen('http://127.0.0.1:$p$path',timeout=5).status)
except Exception as e: print(getattr(e,'code','tcp'))"; exit 0; fi
(exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo tcp || echo none
"""


class DockerBackend(Backend):
    def __init__(self, image, snapshot, repo_name, network, env, platform=None, pull=True):
        self.name = "readme-intern-" + uuid.uuid4().hex[:8]
        self.state = "/tmp/.readme-intern"
        args = ["docker", "run", "-d", "--name", self.name, "--init",
                "-e", "CI=1", "-e", "DEBIAN_FRONTEND=noninteractive", "-e", "TERM=dumb",
                "-e", "PIP_DISABLE_PIP_VERSION_CHECK=1", "-e", "NO_COLOR=1",
                "-v", "%s:/src:ro" % snapshot, "-w", "/work"]
        if platform:
            args += ["--platform", platform]
        if network == "none":
            args += ["--network", "none"]
        for k, v in env.items():
            args += ["-e", "%s=%s" % (k, v)]
        args += ["--entrypoint", "sh", image, "-c", "mkdir -p /work && sleep infinity"]
        if not shutil.which("docker"):
            raise RuntimeError("docker is not installed. Install Docker, or re-run with --local (less clean).")
        if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            raise RuntimeError("the Docker daemon is not running. Start Docker Desktop / dockerd, or re-run with --local.")
        if pull and subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
            p = subprocess.run(["docker", "pull", image], capture_output=True, text=True)
            if p.returncode != 0:
                raise RuntimeError("docker pull %s failed: %s" % (image, p.stderr.strip()[-400:]))
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("docker run failed: " + r.stderr.strip()[-400:])
        # Copy without keeping host ownership: on CI the files belong to the runner user, and git
        # refuses to clone a repo owned by someone else ("detected dubious ownership").
        r = self._sh("mkdir -p /work %s && cp -R /src /work/.rp-src && chown -R \"$(id -u):$(id -g)\" /work/.rp-src" % self.state)
        if r.returncode != 0:
            raise RuntimeError("could not copy the repo into the container: " + (r.stderr or r.stdout).strip()[-300:])
        # sudo shim: most human readers have sudo; containers usually run as root without it.
        self._sh("command -v sudo >/dev/null 2>&1 || { printf '#!/bin/sh\\nwhile [ \"${1#-}\" != \"$1\" ]; do shift; done\\nexec \"$@\"\\n' > /usr/local/bin/sudo && chmod +x /usr/local/bin/sudo; }")
        self.image = image
        self.bg = []

    def _sh(self, cmd, timeout=120):
        return subprocess.run(["docker", "exec", self.name, "sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)

    def put(self, path, content):
        p = subprocess.run(["docker", "exec", "-i", self.name, "sh", "-c", "cat > %s && chmod +x %s" % (path, path)],
                           input=content, text=True, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError("could not write %s in container: %s" % (path, p.stderr))

    def init_state(self, cwd):
        self._sh("mkdir -p %s && echo %s > %s/cwd && : > %s/env" % (self.state, shlex.quote(cwd), self.state, self.state))

    def has_shell(self):
        return self._sh("command -v bash").returncode == 0

    def exec(self, script_path, timeout):
        t0 = now()
        try:
            p = subprocess.run(["docker", "exec", self.name, "timeout", "-k", "5", str(timeout), "bash", script_path],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout + 30)
            out = (p.stdout + p.stderr).decode("utf-8", "replace")
            rc = p.returncode
        except subprocess.TimeoutExpired as e:
            out, rc = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace"), 124
        return rc, out, now() - t0

    def start_bg(self, script_path, log_path):
        cmd = "nohup setsid bash %s > %s 2>&1 < /dev/null & echo $!" % (script_path, log_path)
        pid = self._sh(cmd).stdout.strip()
        self.bg.append(pid)
        return {"pid": pid, "log": log_path}

    def alive(self, h):
        return self._sh("kill -0 %s 2>/dev/null" % h["pid"]).returncode == 0

    def listening_ports(self):
        r = subprocess.run(["docker", "exec", self.name, "bash", "-c", PROC_PORTS_SNIPPET],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or not r.stdout.strip():
            r = self._sh("command -v python3 >/dev/null && python3 -c %s" % shlex.quote(PROC_PORTS_PY))
        return {int(x) for x in r.stdout.split() if x.isdigit()}

    def probe(self, port, path="/"):
        r = self._sh(PROBE_SNIPPET % {"port": int(port), "path": shlex.quote(path)}, timeout=20)
        return r.stdout.strip() or "none"

    def read_log(self, h):
        return self._sh("cat %s 2>/dev/null" % h["log"]).stdout

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


class LocalBackend(Backend):
    def __init__(self, snapshot, env):
        self.root = tempfile.mkdtemp(prefix="readme-intern-")
        self.work = os.path.join(self.root, "work")
        self.home = os.path.join(self.root, "home")
        self.state = os.path.join(self.root, "state")
        for d in (self.work, self.home, self.state):
            os.makedirs(d)
        shutil.copytree(snapshot, os.path.join(self.work, ".rp-src"), symlinks=True)
        keep = {"PATH", "LANG", "LC_ALL", "TERM", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy",
                "https_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS",
                "PIP_CERT", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO", "TMPDIR"}
        self.env = {k: v for k, v in os.environ.items() if k in keep}
        # Drop per-user tool dirs from PATH so ~/.local/bin, pyenv/nvm shims etc. do not leak in.
        home = os.path.expanduser("~")
        self.env["PATH"] = os.pathsep.join(p for p in self.env.get("PATH", "/usr/bin:/bin").split(os.pathsep)
                                           if not p.startswith(home))
        self.env.update({"HOME": self.home, "CI": "1", "NO_COLOR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                         "npm_config_cache": os.path.join(self.home, ".npm"),
                         "XDG_CACHE_HOME": os.path.join(self.home, ".cache")})
        self.env.update(env)
        self.bg = []

    def put(self, path, content):
        with open(path, "w") as fh:
            fh.write(content)
        os.chmod(path, 0o755)

    def init_state(self, cwd):
        with open(os.path.join(self.state, "cwd"), "w") as fh:
            fh.write(cwd + "\n")
        open(os.path.join(self.state, "env"), "w").close()

    def has_shell(self):
        return shutil.which("bash") is not None

    def exec(self, script_path, timeout):
        t0 = now()
        p = subprocess.Popen(["bash", script_path], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=self.env, cwd=self.work, start_new_session=True)
        try:
            out, _ = p.communicate(timeout=timeout)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, 9)
            out, _ = p.communicate()
            rc = 124
        return rc, out.decode("utf-8", "replace"), now() - t0

    def start_bg(self, script_path, log_path):
        fh = open(log_path, "w")
        p = subprocess.Popen(["bash", script_path], stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                             env=self.env, cwd=self.work, start_new_session=True)
        self.bg.append(p)
        return {"proc": p, "log": log_path}

    def alive(self, h):
        return h["proc"].poll() is None

    def listening_ports(self):
        ports = set()
        for f in ("/proc/net/tcp", "/proc/net/tcp6"):
            try:
                for line in open(f).read().splitlines()[1:]:
                    p = line.split()
                    if len(p) > 3 and p[3] == "0A":
                        ports.add(int(p[1].split(":")[1], 16))
            except OSError:
                pass
        if not ports and shutil.which("lsof"):
            out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True).stdout
            ports = {int(m) for m in re.findall(r":(\d+) \(LISTEN\)", out)}
        return ports

    def probe(self, port, path="/"):
        import urllib.request
        import urllib.error
        try:
            return str(urllib.request.urlopen("http://127.0.0.1:%d%s" % (int(port), path), timeout=5).status)
        except urllib.error.HTTPError as e:
            return str(e.code)
        except Exception:
            import socket
            try:
                socket.create_connection(("127.0.0.1", int(port)), timeout=3).close()
                return "tcp"
            except OSError:
                return "none"

    def read_log(self, h):
        try:
            return open(h["log"], encoding="utf-8", errors="replace").read()
        except OSError:
            return ""

    def close(self):
        for p in self.bg:
            try:
                os.killpg(p.pid, 15)
                p.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(p.pid, 9)
                except Exception:
                    pass
        shutil.rmtree(self.root, ignore_errors=True)


# ----------------------------------------------------------------------------- runner

def rewrite_clone(st, repo_name, workdir):
    """Point `git clone <this repo>` at the snapshot so we test the reader's path, not the network."""
    url = st.get("clone_url", "")
    target = st.get("clone_dir") or repo_name
    base = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1].split(":")[-1])
    if not st.get("is_self", base.lower() == repo_name.lower()):
        return None  # cloning some other repo: run literally
    rest = st["cmd"][st["cmd"].find(url) + len(url):]
    # keep anything chained after the clone, e.g. "&& cd repo"
    after = ""
    m = re.search(r"(&&|;).*$", rest, re.S)
    if m:
        after = " " + m.group(0)
    return "git clone -q %s %s%s" % (shlex.quote(os.path.join(workdir, ".rp-src")), shlex.quote(target), after)


def run_server(backend, st, script, log, wait, baseline):
    h = backend.start_bg(script, log)
    t0 = now()
    port, code, reason = st.get("port"), None, ""
    while now() - t0 < wait:
        time.sleep(1.5)
        text = backend.read_log(h)
        if not backend.alive(h):
            return {"status": "fail", "rc": None, "reason": "server process exited", "output": text,
                    "duration": now() - t0}
        cand = []
        if port:
            cand = [int(port)]
        else:
            for m in URL_PORT.finditer(text):
                cand.append(int(m.group(1) or m.group(2)))
            cand += sorted(backend.listening_ports() - baseline)
        for p in cand:
            code = backend.probe(p, st.get("probe", "/"))
            if code not in ("none", "000", ""):
                ok = code == "tcp" or (code.isdigit() and int(code) < 500)
                return {"status": "pass" if ok else "fail", "rc": None, "port": p, "http": code,
                        "reason": "" if ok else "server answered HTTP %s" % code,
                        "output": backend.read_log(h), "duration": now() - t0, "handle": h}
    text = backend.read_log(h)
    return {"status": "warn", "rc": None, "reason": "still running after %ds but no listening port found" % wait,
            "output": text, "duration": now() - t0, "handle": h}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="JSON from extract_steps.py (edited or not)")
    ap.add_argument("--repo", default=None, help="default: plan['repo']")
    ap.add_argument("--out", default=".readme-intern/run")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--image", default=None, help="container image (default: plan suggested_image)")
    g.add_argument("--local", action="store_true", help="no Docker: temp dir + fresh HOME (less clean)")
    ap.add_argument("--platform", default=None, help="docker --platform, e.g. linux/amd64")
    ap.add_argument("--network", choices=["on", "none"], default="on")
    ap.add_argument("--step-timeout", type=int, default=None, help="seconds per step (default 600)")
    ap.add_argument("--server-wait", type=int, default=None, help="seconds to wait for a server port (default 90)")
    ap.add_argument("--keep-going", action="store_true", help="continue after a failed step")
    ap.add_argument("--only", default=None, help="comma list of step ids to run (debugging)")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE, repeatable (dummy secrets)")
    args = ap.parse_args()

    with open(args.plan) as fh:
        plan = json.load(fh)
    cfg = {}
    if plan.get("config") and os.path.exists(plan["config"]):
        with open(plan["config"]) as fh:
            cfg = json.load(fh)
    repo = os.path.abspath(args.repo or plan.get("repo") or ".")
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    step_timeout = args.step_timeout or cfg.get("step_timeout", 600)
    server_wait = args.server_wait or cfg.get("server_wait", 90)
    env = dict(cfg.get("env", {}))
    for kv in args.env:
        k, _, v = kv.partition("=")
        env[k] = v
    only = {int(x) for x in args.only.split(",")} if args.only else None
    repo_name = plan.get("repo_name") or os.path.basename(repo)

    snap_root = tempfile.mkdtemp(prefix="readme-intern-snap-")
    snap = os.path.join(snap_root, repo_name)
    results = {"repo": repo, "readme": plan.get("readme"), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "steps": [], "environment": {}}
    backend = None
    exit_code = 0
    try:
        info = snapshot_repo(repo, snap)
        results["environment"]["snapshot"] = info
        if args.local:
            backend = LocalBackend(snap, env)
            workdir = backend.work
            results["environment"].update({"mode": "local", "clean": False,
                                           "note": "Local mode: host-installed tools and system packages were visible. "
                                                   "Re-run with Docker for a true first-time-user result."})
        else:
            image = args.image or plan.get("suggested_image") or "buildpack-deps:bookworm"
            backend = DockerBackend(image, snap, repo_name, args.network, env, args.platform)
            workdir = "/work"
            results["environment"].update({"mode": "docker", "image": image, "network": args.network, "clean": True})
        if not backend.has_shell():
            raise RuntimeError("bash not found in the environment; pick an image with bash (most -bookworm images)")

        steps = [s for s in plan["steps"] if s.get("include", True) and (only is None or s["id"] in only)]
        has_clone = any(s.get("kind") == "clone" and rewrite_clone(s, repo_name, workdir) for s in steps)
        start_cwd = workdir if has_clone else os.path.join(workdir, repo_name)
        if not has_clone:
            setup = step_script("git clone -q %s %s" % (shlex.quote(os.path.join(workdir, ".rp-src")), shlex.quote(repo_name)),
                                backend.state)
            p = os.path.join(backend.state, "setup.sh")
            backend.put(p, setup)
            backend.init_state(workdir)
            rc, o, _ = backend.exec(p, 120)
            if rc != 0:
                raise RuntimeError("could not place repo in workdir: " + o)
        backend.init_state(start_cwd)
        results["environment"]["start_cwd"] = start_cwd
        results["environment"]["readme_has_clone_step"] = has_clone

        for pre in cfg.get("setup", []):
            p = os.path.join(backend.state, "pre_%d.sh" % (cfg["setup"].index(pre)))
            backend.put(p, step_script(pre, backend.state))
            rc, o, d = backend.exec(p, step_timeout)
            results["steps"].append({"id": "setup", "cmd": pre, "status": "pass" if rc == 0 else "fail", "rc": rc,
                                     "duration": round(d, 1), "output_tail": tail(o), "source": "config"})
            if rc != 0:
                raise RuntimeError("config setup command failed: %s\n%s" % (pre, tail(o, 20)))

        baseline = backend.listening_ports()
        t_all = now()
        for st in steps:
            cmd = st["cmd"]
            if st.get("kind") == "clone":
                rw = rewrite_clone(st, repo_name, workdir)
                if rw:
                    cmd = rw
            script = os.path.join(backend.state, "step_%s.sh" % st["id"])
            backend.put(script, step_script(cmd, backend.state))
            log_name = "step_%s.log" % st["id"]
            rec = {"id": st["id"], "line": st.get("line"), "section": st.get("section"), "cmd": st["cmd"],
                   "executed": cmd if cmd != st["cmd"] else None, "kind": st.get("kind"), "source": st.get("source", "readme")}
            if st.get("kind") == "server":
                r = run_server(backend, st, script, os.path.join(backend.state, log_name), st.get("timeout", server_wait), baseline)
                r.pop("handle", None)
            else:
                rc, o, d = backend.exec(script, st.get("timeout", step_timeout))
                expect = st.get("expect_exit", 0)
                status = "pass" if rc == expect else ("timeout" if rc == 124 else "fail")
                r = {"status": status, "rc": rc, "output": o, "duration": d,
                     "reason": "timed out after %ss (waiting for input?)" % st.get("timeout", step_timeout) if rc == 124 else ""}
            with open(os.path.join(out_dir, log_name), "w") as fh:
                fh.write(r["output"])
            rec.update({"status": r["status"], "rc": r.get("rc"), "duration": round(r["duration"], 1),
                        "reason": r.get("reason", ""), "output_tail": tail(r["output"]), "log": log_name})
            for k in ("port", "http"):
                if k in r:
                    rec[k] = r[k]
            results["steps"].append(rec)
            mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "timeout": "TIME"}[r["status"]]
            print("[%s] #%s L%s  %s  (%.1fs)%s" % (mark, st["id"], st.get("line"), st["cmd"][:90], r["duration"],
                                                   ("  -> " + rec["reason"]) if rec["reason"] else ""), file=sys.stderr)
            if r["status"] in ("fail", "timeout"):
                exit_code = 1
                if not args.keep_going:
                    break
        results["total_seconds"] = round(now() - t_all, 1)
    except Exception as e:  # setup problems are reported, not raised
        results["setup_error"] = str(e)
        print("[SETUP ERROR] %s" % e, file=sys.stderr)
        exit_code = 2
    finally:
        if backend:
            backend.close()
        shutil.rmtree(snap_root, ignore_errors=True)

    ran = results["steps"]
    first_fail = next((s for s in ran if s["status"] in ("fail", "timeout")), None)
    results["summary"] = {
        "verdict": "setup-error" if exit_code == 2 else ("pass" if exit_code == 0 else "fail"),
        "steps_run": len([s for s in ran if s["id"] != "setup"]),
        "steps_planned": len([s for s in plan["steps"] if s.get("include", True)]),
        "first_failure": {"id": first_fail["id"], "line": first_fail.get("line"), "cmd": first_fail["cmd"]} if first_fail else None,
        "warnings": [s["id"] for s in ran if s["status"] == "warn"],
    }
    with open(os.path.join(out_dir, "results.json"), "w") as fh:
        json.dump(results, fh, indent=2)
    print(json.dumps(results["summary"]), file=sys.stderr)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
