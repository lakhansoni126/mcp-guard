"""JSON-RPC helpers shared by both proxies: spot requests that need approval and build denials."""
import json

GATED = {"tools/call", "resources/read", "prompts/get"}  # everything else (handshake, lists, pings) passes
BLOCKED = -32001


def parse(raw):
    """Return the messages in a JSON-RPC payload (batches split), or None if it is not JSON."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return msg if isinstance(msg, list) else [msg]


def describe(msg):
    """Summary of a request that needs the user's decision, or None if it can pass straight through."""
    if not isinstance(msg, dict) or msg.get("method") not in GATED or "id" not in msg:
        return None
    params = msg.get("params") or {}
    return {"method": msg["method"], "target": params.get("name") or params.get("uri") or "?",
            "arguments": params.get("arguments", {})}


def denial(msg, reason):
    """Reply sent instead of forwarding a blocked request.

    A blocked tool call gets a tool result with isError, as the MCP spec asks for tool failures, so the model
    reads the reason and tells the user (a protocol error only shows up as a generic "connector error").
    Resources and prompts have no such result shape, so they get a JSON-RPC error.
    """
    text = f"Blocked by MCP Guard: {reason}. The request was not sent to the server."
    if msg.get("method") == "tools/call":
        return {"jsonrpc": "2.0", "id": msg["id"],
                "result": {"content": [{"type": "text", "text": text}], "isError": True}}
    return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": BLOCKED, "message": text}}


def encode(msg):
    """One newline-delimited message, as the stdio transport expects."""
    return (json.dumps(msg) + "\n").encode()


def send_json(handler, obj, code=200):
    """Write a JSON response from a BaseHTTPRequestHandler."""
    data = json.dumps(obj).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
