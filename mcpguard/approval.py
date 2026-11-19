"""The approval daemon (a localhost HTTP service) and the ask() call both proxies use to reach it.

Proxies run as separate processes (one per MCP server), so decisions are centralised in one daemon that
owns the popups, the remembered rules and the audit log. Anything that goes wrong on the way fails closed.
"""
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .jsonrpc import send_json, summary

HOME = Path(os.environ.get("MCPGUARD_HOME", Path.home() / ".mcpguard"))
PORT = int(os.environ.get("MCPGUARD_PORT", "8765"))
TIMEOUT = float(os.environ.get("MCPGUARD_TIMEOUT", "60"))  # seconds before an unanswered popup blocks
AUTOSTART = os.environ.get("MCPGUARD_AUTOSTART", "1") != "0"  # a proxy launches the daemon if it is not running
TOKEN_FILE, AUDIT_FILE = HOME / "token", HOME / "audit.jsonl"
_audit_lock = threading.Lock()


def audit(event):
    """Append one JSON line to the shared audit log (every message seen, every decision made)."""
    HOME.mkdir(parents=True, exist_ok=True)
    with _audit_lock, open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getpid(), **event}) + "\n")


def log_message(server, flow, msg):
    """Audit one JSON-RPC message passing through a proxy; `flow` says which way it went."""
    audit({"event": "message", "server": server, "flow": flow, **summary(msg)})


def _post(request):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/decide", json.dumps(request).encode(),
        {"Content-Type": "application/json", "X-MCPGuard-Token": TOKEN_FILE.read_text().strip()})
    with urllib.request.urlopen(req, timeout=TIMEOUT + 15) as resp:
        return json.load(resp)


def _not_running(exc):
    return isinstance(exc, FileNotFoundError) or isinstance(getattr(exc, "reason", None), ConnectionRefusedError)


def start_daemon(wait=10):
    """Launch the daemon in the background (no console window) and wait until it accepts requests."""
    exe = Path(sys.executable)
    gui = exe.with_name(exe.name.replace("python", "pythonw"))  # Windows: no console window
    subprocess.Popen([str(gui if gui.exists() else exe), "-m", "mcpguard", "daemon"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), start_new_session=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", PORT), 0.5).close()
            time.sleep(0.3)  # the daemon writes its token right after binding the port
            return
        except OSError:
            time.sleep(0.2)


def ask(request):
    """Wait for the user's decision on a request. Returns (allow, reason); any failure denies."""
    try:
        try:
            out = _post(request)
        except (OSError, urllib.error.URLError) as exc:
            if not (AUTOSTART and _not_running(exc)):
                raise
            start_daemon()  # first request after a reboot, or the daemon was closed: bring it back, ask again
            out = _post(request)
        return out["allow"], out["reason"]
    except Exception as exc:  # still down, bad token, timeout: never forward silently
        return False, f"approval service unavailable ({type(exc).__name__})"


class Daemon:
    """Serves POST /decide. `prompt(request, done)` shows the request and later calls done(allow, remember)."""

    def __init__(self, prompt, port=PORT, timeout=TIMEOUT):
        HOME.mkdir(parents=True, exist_ok=True)
        self.prompt, self.timeout, self.rules = prompt, timeout, {}
        # bind first: if another daemon already owns the port this fails before touching its token
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.server.daemon_threads = True  # one thread per pending request = concurrent popups
        self.token = secrets.token_urlsafe(32)
        TOKEN_FILE.write_text(self.token)
        TOKEN_FILE.chmod(0o600)  # only this user's processes can ask for decisions

    def decide(self, request):
        key = (request.get("server"), request.get("target"))
        if key in self.rules:
            allow, why = self.rules[key], "remembered"
        else:
            answered, result = threading.Event(), {}

            def done(allow, remember=False, why="by user"):
                if not answered.is_set():
                    result.update(allow=allow, why=why)
                    if remember:
                        self.rules[key] = allow
                    answered.set()

            self.prompt(request, done)
            answered.wait(self.timeout + 5)  # the popup auto-blocks at `timeout`; this is a backstop
            done(False, why="timed out")
            allow, why = result["allow"], result["why"]
        audit({"event": "decision", "allow": allow, "why": why, **request})
        return {"allow": allow, "reason": f"{'allowed' if allow else 'denied'} {why}"}

    def _handler(self):
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                token = self.headers.get("X-MCPGuard-Token", "")
                if self.path != "/decide" or not secrets.compare_digest(token, daemon.token):
                    return send_json(self, {"error": "forbidden"}, 403)
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                send_json(self, daemon.decide(request))

            def log_message(self, *args):
                pass

        return Handler

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        TOKEN_FILE.unlink(missing_ok=True)
