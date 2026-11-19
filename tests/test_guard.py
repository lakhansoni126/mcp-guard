"""End-to-end tests: a real daemon with a scripted 'user', the real proxies, and a fake MCP server."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mcpguard import approval, install, jsonrpc
from mcpguard.http_proxy import make_server

FAKE = str(Path(__file__).with_name("fake_server.py"))
ROOT = str(Path(__file__).parents[1])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def user(request, done):
    """Scripted decisions: allow echo, block delete_file, answer 'slow' after a delay."""
    delay = 1.0 if request["arguments"].get("text") == "slow" else 0
    threading.Timer(delay, done, (request["target"] == "echo",)).start()


@pytest.fixture
def guard(tmp_path, monkeypatch):
    port = free_port()
    monkeypatch.setattr(approval, "PORT", port)
    monkeypatch.setattr(approval, "HOME", tmp_path)
    monkeypatch.setattr(approval, "TOKEN_FILE", tmp_path / "token")
    monkeypatch.setattr(approval, "AUDIT_FILE", tmp_path / "audit.jsonl")
    daemon = approval.Daemon(user, port=port, timeout=5).start()
    yield {**os.environ, "MCPGUARD_HOME": str(tmp_path), "MCPGUARD_PORT": str(port), "PYTHONPATH": ROOT,
           "MCPGUARD_AUTOSTART": "0"}  # never launch a real daemon (with popups) from the tests
    daemon.stop()


def blocked(reply):
    """The guard's message if the reply is a denial (tool calls get an isError result), else None."""
    result = reply.get("result", {})
    return result["content"][0]["text"] if result.get("isError") else reply.get("error", {}).get("message")


def call(i, tool, text="hi"):
    return {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": tool, "arguments": {"text": text}}}


def run_stdio(env, messages, expect):
    proc = subprocess.Popen([sys.executable, "-m", "mcpguard", "stdio", "--name", "fake", "--", sys.executable, FAKE],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True)
    for m in messages:
        proc.stdin.write(json.dumps(m) + "\n")
    proc.stdin.flush()
    replies = [json.loads(proc.stdout.readline()) for _ in range(expect)]
    proc.stdin.close()
    proc.wait(timeout=10)
    return replies


def test_stdio_allows_blocks_and_handles_concurrent_requests(guard):
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
    replies = run_stdio(guard, [init, call(1, "echo", "slow"), call(2, "delete_file"), call(3, "echo")], 4)
    by_id = {r["id"]: r for r in replies}
    assert "result" in by_id[0] and "echo ran" in by_id[1]["result"]["content"][0]["text"]
    assert blocked(by_id[2]).startswith("Blocked by MCP Guard") and not blocked(by_id[1])
    assert [r["id"] for r in replies].index(3) < [r["id"] for r in replies].index(1)  # slow one didn't block id 3
    log = [json.loads(line) for line in Path(guard["MCPGUARD_HOME"], "audit.jsonl").read_text().splitlines()]
    flows = {(e.get("flow"), e.get("kind"), e.get("id")) for e in log if e["event"] == "message"}
    assert {("client -> server", "request", 2), ("guard -> client", "error", 2), ("server -> client", "result", 1)} <= flows


def test_stdio_fails_closed_without_daemon(guard, tmp_path):
    env = {**guard, "MCPGUARD_PORT": str(free_port())}
    (reply,) = run_stdio(env, [call(1, "echo")], 1)
    assert "unavailable" in blocked(reply)


def test_stdio_drops_a_request_cancelled_while_waiting(guard):
    proc = subprocess.Popen([sys.executable, "-m", "mcpguard", "stdio", "--name", "fake", "--", sys.executable, FAKE],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=guard, text=True)
    cancel = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}
    for m in (call(1, "echo", "slow"), cancel, call(2, "echo")):
        proc.stdin.write(json.dumps(m) + "\n")
    proc.stdin.flush()
    assert json.loads(proc.stdout.readline())["id"] == 2
    time.sleep(1.5)  # the slow approval for id 1 lands now; it must be neither run nor answered
    proc.stdin.close()
    assert proc.stdout.read() == ""
    proc.wait(timeout=10)


def test_http_proxy_gates_posts_and_forwards_the_rest(guard):
    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            jsonrpc.send_json(self, {"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": self.path}})

        def log_message(self, *a):
            pass

    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    proxy = make_server(f"http://127.0.0.1:{up.server_port}", "remote", free_port())
    for s in (up, proxy):
        threading.Thread(target=s.serve_forever, daemon=True).start()

    def post(msg):
        req = urllib.request.Request(f"http://127.0.0.1:{proxy.server_port}/mcp", json.dumps(msg).encode(),
                                     {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=10))

    assert post(call(1, "echo"))["result"] == {"ok": "/mcp"}
    assert "denied" in blocked(post(call(2, "delete_file")))
    read = {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "file:///x"}}
    assert "denied" in blocked(post(read))  # non-tool requests still get a JSON-RPC error
    assert post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]
    up.shutdown(), proxy.shutdown()


def test_remembered_rule_skips_the_popup(guard):
    calls = []
    daemon = approval.Daemon(lambda r, done: (calls.append(r), done(True, True)), port=free_port(), timeout=1)
    req = {"server": "s", "target": "echo", "method": "tools/call", "arguments": {}}
    assert daemon.decide(req)["allow"] and daemon.decide(req)["reason"] == "allowed remembered"
    assert len(calls) == 1
    daemon.server.server_close()


def test_install_round_trip(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    original = {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "server-fs", "/tmp"]}, "web": {"url": "https://x"}}}
    cfg.write_text(json.dumps(original))
    assert install.install(cfg) == ["fs"] and install.install(cfg) == []  # idempotent
    assert json.loads(cfg.read_text())["mcpServers"]["fs"]["args"][:5] == [*install.WRAP, "fs"]
    assert install.uninstall(cfg) == ["fs"]
    assert json.loads(cfg.read_text()) == original
