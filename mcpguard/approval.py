"""The approval daemon (a localhost HTTP service) and the ask() call both proxies use to reach it.

Proxies run as separate processes (one per MCP server), so decisions are centralised in one daemon that
owns the popups, the remembered rules and the audit log. Anything that goes wrong on the way fails closed.
"""
import json
import os
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .jsonrpc import send_json

HOME = Path(os.environ.get("MCPGUARD_HOME", Path.home() / ".mcpguard"))
PORT = int(os.environ.get("MCPGUARD_PORT", "8765"))
TIMEOUT = float(os.environ.get("MCPGUARD_TIMEOUT", "60"))  # seconds before an unanswered popup blocks
TOKEN_FILE, AUDIT_FILE = HOME / "token", HOME / "audit.jsonl"
_audit_lock = threading.Lock()


def audit(event):
    """Append one JSON line to the shared audit log (every message seen, every decision made)."""
    HOME.mkdir(parents=True, exist_ok=True)
    with _audit_lock, open(AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "pid": os.getpid(), **event}) + "\n")


def ask(request):
    """Wait for the user's decision on a request. Returns (allow, reason); any failure denies."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/decide", json.dumps(request).encode(),
            {"Content-Type": "application/json", "X-MCPGuard-Token": TOKEN_FILE.read_text().strip()})
        with urllib.request.urlopen(req, timeout=TIMEOUT + 15) as resp:
            out = json.load(resp)
        return out["allow"], out["reason"]
    except Exception as exc:  # daemon down, bad token, timeout: never forward silently
        return False, f"approval service unavailable ({type(exc).__name__})"


class Daemon:
    """Serves POST /decide. `prompt(request, done)` shows the request and later calls done(allow, remember)."""

    def __init__(self, prompt, port=PORT, timeout=TIMEOUT):
        HOME.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        TOKEN_FILE.write_text(self.token)
        TOKEN_FILE.chmod(0o600)  # only this user's processes can ask for decisions
        self.prompt, self.timeout, self.rules = prompt, timeout, {}
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.server.daemon_threads = True  # one thread per pending request = concurrent popups

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
