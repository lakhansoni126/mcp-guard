"""Claude Desktop launches this in place of a local MCP server: it relays stdio and gates requests.

Claude <-stdin/stdout-> proxy <-pipes-> real server. Each gated request waits for a decision on its own
thread, so other traffic (and other pending requests) keeps flowing; JSON-RPC ids match the replies.
"""
import os
import shutil
import subprocess
import sys
import threading

from . import approval, jsonrpc


def run(name, command):
    exe = shutil.which(command[0]) or command[0]  # resolves npx.cmd / uvx.exe on Windows
    child = subprocess.Popen([exe, *command[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    locks = {sys.stdout.buffer: threading.Lock(), child.stdin: threading.Lock()}

    def send(stream, data):
        try:
            with locks[stream]:
                stream.write(data)
                stream.flush()
        except OSError:
            pass  # the other side went away; the exit paths below handle shutdown

    def server_to_client():
        for line in child.stdout:
            send(sys.stdout.buffer, line)
            for msg in jsonrpc.parse(line) or []:
                approval.log_message(name, "server -> client", msg)
        os._exit(child.wait())  # server exited: end the proxy so Claude sees the server stop

    cancelled = set()  # ids Claude gave up on (notifications/cancelled) while their popup was open

    def decide(msg, info):
        allow, reason = approval.ask({**info, "server": name, "client": os.environ.get("MCPGUARD_CLIENT", "Claude Desktop") + " (stdio)"})
        if msg["id"] in cancelled:  # the spec says to drop it: neither run it nor answer it
            return approval.audit({"event": "dropped", "server": name, "id": msg["id"], "why": "cancelled by client"})
        if allow:
            send(child.stdin, jsonrpc.encode(msg))
        else:
            reply = jsonrpc.denial(msg, reason)
            send(sys.stdout.buffer, jsonrpc.encode(reply))
            approval.log_message(name, "guard -> client", reply)

    threading.Thread(target=server_to_client, daemon=True).start()
    for line in sys.stdin.buffer:
        msgs = jsonrpc.parse(line)
        if msgs is None:  # not JSON: let the server answer with its own parse error
            send(child.stdin, line)
            continue
        for msg in msgs:
            info = jsonrpc.describe(msg)
            approval.log_message(name, "client -> server", msg)
            if isinstance(msg, dict) and msg.get("method") == "notifications/cancelled":
                cancelled.add((msg.get("params") or {}).get("requestId"))
            if info:
                threading.Thread(target=decide, args=(msg, info), daemon=True).start()
            else:
                send(child.stdin, jsonrpc.encode(msg))
    child.stdin.close()  # Claude closed our stdin: stop the server cleanly, then force it
    try:
        sys.exit(child.wait(timeout=5))
    except subprocess.TimeoutExpired:
        child.kill()
        sys.exit(1)
