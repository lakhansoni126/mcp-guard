"""Minimal stdio MCP server for tests and demos: one `echo` tool and one `delete_file` tool (both harmless)."""
import json
import sys

TOOLS = [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}
         for n, d in (("echo", "Echo the text back"), ("delete_file", "Pretends to delete a file"))]

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue  # notifications need no reply
    method, params = msg.get("method"), msg.get("params") or {}
    result = {
        "initialize": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                       "serverInfo": {"name": "fake", "version": "1"}},
        "tools/list": {"tools": TOOLS},
        "tools/call": {"content": [{"type": "text", "text": f"{params.get('name')} ran with {params.get('arguments')}"}]},
    }.get(method)
    reply = {"result": result} if result else {"error": {"code": -32601, "message": "method not found"}}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], **reply}), flush=True)
