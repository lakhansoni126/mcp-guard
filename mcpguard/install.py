"""Wrap every local (stdio) server in claude_desktop_config.json with the MCP Guard proxy, or undo it."""
import json
import os
import shutil
import sys
from pathlib import Path

WRAP = ["-m", "mcpguard", "stdio", "--name"]


def config_path():
    if sys.platform == "win32":
        path = Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
        # The Microsoft Store (MSIX) build keeps its AppData inside the package folder
        store = sorted(Path(os.environ["LOCALAPPDATA"]).glob("Packages/Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
        return path if path.exists() or not store else store[0]
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def _update(path, change):
    """Apply `change(name, server)` to every stdio server and save, keeping a .bak of the original."""
    path = Path(path or config_path())
    config = json.loads(path.read_text(encoding="utf-8"))
    shutil.copy(path, path.with_name(path.name + ".bak"))
    touched = [name for name, server in config.get("mcpServers", {}).items()
               if "command" in server and change(name, server)]
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return touched


def _wrap(name, server):
    args = server.get("args", [])
    if args[:4] == WRAP:
        return False
    server["args"] = [*WRAP, name, "--", server["command"], *args]
    server["command"] = sys.executable
    return True


def _unwrap(name, server):
    args = server.get("args", [])
    if args[:4] != WRAP:
        return False
    server["command"], *server["args"] = args[args.index("--") + 1:]
    return True


def install(path=None):
    return _update(path, _wrap)


def uninstall(path=None):
    return _update(path, _unwrap)
