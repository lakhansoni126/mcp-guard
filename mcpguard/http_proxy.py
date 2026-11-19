"""Reverse proxy for remote (Streamable HTTP) MCP servers, used by Claude Web and Desktop connectors.

Anthropic's cloud calls the connector URL; point that URL (e.g. through a tunnel) at this proxy. Every
path is forwarded unchanged (including OAuth discovery), responses are streamed back so SSE works, and a
POST that carries a gated request is held until the user decides.
"""
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import approval, jsonrpc

HOP_BY_HOP = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "accept-encoding"}


def make_server(upstream, name, port, host="127.0.0.1"):
    upstream = upstream.rstrip("/")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            msgs = jsonrpc.parse(body) or []
            client = f"remote: {self.headers.get('User-Agent', 'unknown client')}"
            denied = []
            for msg in msgs:
                approval.log_message(name, "client -> server", msg)
                info = jsonrpc.describe(msg)
                if info:
                    allow, reason = approval.ask({**info, "server": name, "client": client})
                    denied += [] if allow else [jsonrpc.denial(msg, reason)]
            for reply in denied:
                approval.log_message(name, "guard -> client", reply)
            if denied:  # batches were dropped from the spec; a denied call answers the whole POST
                return jsonrpc.send_json(self, denied[0] if len(denied) == 1 else denied)
            self._forward(body)

        def do_GET(self):  # SSE stream, OAuth metadata
            self._forward(None)

        do_DELETE = do_GET  # session termination

        def _forward(self, body):
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
            req = urllib.request.Request(upstream + self.path, data=body, headers=headers, method=self.command)
            try:
                resp = urllib.request.urlopen(req, timeout=300)
            except urllib.error.HTTPError as err:
                resp = err  # pass upstream errors (401 for OAuth, 404, ...) through unchanged
            except OSError as err:
                return jsonrpc.send_json(self, {"error": f"upstream unreachable: {err}"}, 502)
            with resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in HOP_BY_HOP:
                        self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                read, seen = getattr(resp, "read1", resp.read), b""
                while chunk := read(65536):  # read1 returns as soon as data arrives, keeping SSE live
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                    seen += chunk[:262144 - len(seen)] if body else b""  # replies to a POST, for the log
                self.wfile.write(b"0\r\n\r\n")
            # a reply is plain JSON or an SSE stream of "data: {...}" lines
            for msg in jsonrpc.parse(seen) or [m for line in seen.splitlines() if line.startswith(b"data:")
                                                for m in jsonrpc.parse(line[5:]) or []]:
                if isinstance(msg, dict) and "jsonrpc" in msg:  # skip OAuth and other plain JSON replies
                    approval.log_message(name, "server -> client", msg)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
