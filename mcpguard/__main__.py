"""Command line: mcpguard daemon | stdio | http | install | uninstall."""
import argparse
import signal

from . import approval, install


def main():
    parser = argparse.ArgumentParser(prog="mcpguard", description="Approve or block each MCP request from Claude.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daemon", help="run the approval service and popups (start this first)")
    stdio = sub.add_parser("stdio", help="wrap a local MCP server (used by Claude Desktop's config)")
    stdio.add_argument("--name", required=True)
    stdio.add_argument("command", nargs=argparse.REMAINDER, help="-- <server command> [args...]")
    http = sub.add_parser("http", help="reverse-proxy a remote MCP server (Claude Web / connectors)")
    http.add_argument("--upstream", required=True, help="e.g. https://mcp.example.com")
    http.add_argument("--name", help="label shown in the popup (default: upstream host)")
    http.add_argument("--port", type=int, default=8080)
    for name in ("install", "uninstall"):
        sub.add_parser(name, help=f"{name} the proxy in claude_desktop_config.json").add_argument("--config")
    args = parser.parse_args()

    if args.cmd == "daemon":
        from .ui import PopupUI  # Tk is only needed here
        ui = PopupUI(approval.TIMEOUT)
        daemon = approval.Daemon(ui.prompt).start()
        signal.signal(signal.SIGINT, lambda *_: ui.quit())
        print(f"MCP Guard listening on 127.0.0.1:{approval.PORT}; popups will appear on top. Ctrl+C to stop.")
        try:
            ui.run()
        finally:
            daemon.stop()
    elif args.cmd == "stdio":
        from .stdio_proxy import run
        run(args.name, args.command[1:] if args.command[:1] == ["--"] else args.command)
    elif args.cmd == "http":
        from .http_proxy import make_server
        name = args.name or args.upstream.split("//")[-1].split("/")[0]
        print(f"Proxying {args.upstream} on http://127.0.0.1:{args.port}")
        make_server(args.upstream, name, args.port).serve_forever()
    else:
        changed = getattr(install, args.cmd)(args.config)
        print(f"{args.cmd}ed: {', '.join(changed) or 'nothing to change'}. Restart Claude Desktop.")


if __name__ == "__main__":
    main()
