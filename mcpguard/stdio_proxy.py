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
        os._exit(child.wait())  # server exited: end the proxy so Claude sees the server stop

    def decide(msg, info):
        allow, reason = approval.ask({**info, "server": name, "client": os.environ.get("MCPGUARD_CLIENT", "Claude Desktop") + " (stdio)"})
        if allow:
            send(child.stdin, jsonrpc.encode(msg))
        else:
            send(sys.stdout.buffer, jsonrpc.encode(jsonrpc.denial(msg, reason)))

    threading.Thread(target=server_to_client, daemon=True).start()
    for line in sys.stdin.buffer:
        msgs = jsonrpc.parse(line)
        if msgs is None:  # not JSON: let the server answer with its own parse error
            send(child.stdin, line)
            continue
        for msg in msgs:
            info = jsonrpc.describe(msg)
            approval.audit({"event": "message", "server": name, "method": msg.get("method") if isinstance(msg, dict) else None})
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
