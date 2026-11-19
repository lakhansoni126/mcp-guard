"""Live monitor: a window that follows the audit log and shows every MCP message and every decision.

Runs on its own (`mcpguard monitor`) and only reads the log, so it can be opened and closed at any time
and sees traffic from every proxy, stdio and HTTP alike.
"""
import json
import tkinter as tk
from tkinter import ttk

from . import approval
from .ui import ACCENT, BG, FONT, SERIF, TEXT, sharp_on_windows

COLUMNS = {"time": 80, "server": 110, "flow": 140, "kind": 90, "what": 380, "id": 50}
COLORS = {"allowed": "#e2ead6", "blocked": "#f2d6cd", "error": "#f5e4c8", "request": "#f0eee6"}


class Monitor:
    def __init__(self, path=approval.AUDIT_FILE):
        self.path, self.offset, self.events = path, 0, {}
        self.methods = {}  # (pid, server, id) -> method, so a reply can be labelled with what it answers
        self.counts = dict(messages=0, asked=0, allowed=0, blocked=0)
        sharp_on_windows()
        self.root = tk.Tk()
        self.root.title(f"MCP Guard monitor  -  {path}")
        scale = float(self.root.tk.call("tk", "scaling")) / (96 / 72)  # 1.0 at 100% display scaling
        # fixed 16:9 window, sized for the display scale so it looks the same on every screen
        self.root.geometry(f"{int(1040 * scale)}x{int(585 * scale)}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        ttk.Style().configure("Treeview", rowheight=int(22 * scale), font=(FONT, 9))
        self.stats = tk.Label(self.root, anchor="w", bg=BG, fg=ACCENT, font=(SERIF, 12))
        self.stats.pack(fill="x", padx=10, pady=6)
        self.detail = tk.Text(self.root, height=8, font=("Consolas", 9), wrap="word", bg="#f0eee6", fg=TEXT,
                              relief="flat")
        self.detail.pack(side="bottom", fill="x", padx=10, pady=(6, 10))  # fixed height: the table gets the rest
        table = tk.Frame(self.root, bg=BG)
        table.pack(fill="both", expand=True, padx=10)
        self.tree = ttk.Treeview(table, columns=list(COLUMNS), show="headings")
        for col, width in COLUMNS.items():
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, width=int(width * scale), stretch=col == "what")
        for tag, color in COLORS.items():
            self.tree.tag_configure(tag, background=color)
        bar = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.root.after(0, self._poll)

    def _poll(self):
        """Read whatever was appended since last time (whole lines only) and add it to the table."""
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except FileNotFoundError:
            data = b""
        end = data.rfind(b"\n") + 1
        self.offset += end
        follow = self.tree.yview()[1] > 0.99  # keep scrolling only if the user is at the bottom
        for line in data[:end].splitlines():
            try:
                self._add(json.loads(line))
            except ValueError:
                pass
        if follow and self.tree.get_children():
            self.tree.see(self.tree.get_children()[-1])
        c = self.counts
        self.stats.config(text=f"{c['messages']} messages   |   {c['asked']} needed approval   |   "
                               f"{c['allowed']} allowed   |   {c['blocked']} blocked")
        self.root.after(500, self._poll)

    def _add(self, e):
        key = (e.get("pid"), e.get("server"), e.get("id"))
        if e.get("event") == "decision":
            verdict = "allowed" if e["allow"] else "blocked"
            self.counts["asked"] += 1
            self.counts[verdict] += 1
            row = (e.get("client", "?"), verdict.upper(), f"{e['method']}  {e['target']}   ({e['why']})")
            tag = verdict
        elif e.get("event") == "message":
            self.counts["messages"] += 1
            if e.get("kind") == "request":
                self.methods[key] = e.get("method")
            what = e.get("method") or f"reply to {self.methods.get(key, '?')}"
            row = (e.get("flow", ""), e.get("kind", ""), what + (f"  {e['target']}" if e.get("target") else ""))
            tag = e.get("kind") if e.get("kind") in COLORS else ""
        else:
            return
        item = self.tree.insert("", "end", values=(e.get("ts", "")[11:], e.get("server", ""), *row, e.get("id", "")),
                                tags=(tag,))
        self.events[item] = e

    def _select(self, _):
        e = self.events.get((self.tree.selection() or [None])[0], {})
        try:  # show the message itself, pretty-printed, rather than its escaped copy (unless it was cut off)
            e = {**e, "body": json.loads(e["body"])}
        except (KeyError, ValueError):
            pass
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", json.dumps(e, indent=2, ensure_ascii=False))

    def run(self):
        self.root.mainloop()
