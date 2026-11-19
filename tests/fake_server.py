"""Minimal MCP server for tests and demos: one `echo` tool and one `delete_file` tool (both harmless).

stdio by default (Claude Desktop); `--http PORT` serves Streamable HTTP on any path (Claude Web, via a tunnel).
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}
         for n, d in (("echo", "Echo the text back"), ("delete_file", "Pretends to delete a file"))]


def handle(msg):
    """The reply to one JSON-RPC message, or None for notifications."""
    if "id" not in msg:
        return None
    method, params = msg.get("method"), msg.get("params") or {}
    result = {
        "initialize": {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                       "serverInfo": {"name": "fake", "version": "1"}},
        "tools/list": {"tools": TOOLS},
        "tools/call": {"content": [{"type": "text", "text": f"{params.get('name')} ran with {params.get('arguments')}"}]},
        "ping": {},
    }.get(method)
    reply = {"result": result} if result is not None else {"error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": msg["id"], **reply}


class Http(BaseHTTPRequestHandler):
    def do_POST(self):
        reply = handle(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))))
        data = json.dumps(reply).encode() if reply else b""
        self.send_response(200 if reply else 202)  # notifications are only acknowledged
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # this demo has no server-initiated stream
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--http"]:
        print(f"fake MCP server on http://127.0.0.1:{sys.argv[2]}/mcp", flush=True)
        ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), Http).serve_forever()
    for line in sys.stdin:
        if reply := handle(json.loads(line)):
            print(json.dumps(reply), flush=True)
