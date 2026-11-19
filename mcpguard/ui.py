"""Always-on-top Tk confirmation popups, one window per pending request (stacked when several arrive)."""
import json
import queue
import re
import tkinter as tk

# warm, quiet palette in the style of the Claude apps: cream background, terracotta accent, serif question
FONT, SERIF = "Segoe UI", "Georgia"
BG, PANEL, TEXT, MUTED = "#faf9f5", "#f0eee6", "#3d3929", "#73726c"
ACCENT, WARN = "#c96442", "#b53333"
# tool names that change or send things get a warning line; it is only a visual hint, every call is still asked
RISKY = re.compile(r"delete|remove|drop|write|exec|run|shell|kill|send|post|pay|transfer|update|create|move", re.I)


def sharp_on_windows():
    """Tell Windows this process scales itself, so Tk text is crisp instead of bitmap-stretched on HiDPI."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (ImportError, AttributeError, OSError):
        pass  # not Windows, or an old one


class PopupUI:
    def __init__(self, timeout):
        sharp_on_windows()
        self.root, self.pending, self.timeout = tk.Tk(), queue.Queue(), int(timeout)
        self.root.withdraw()  # no main window: the app lives in the background until a request arrives
        self.root.after(100, self._drain)

    def prompt(self, request, done):
        """Called from daemon threads; Tk is not thread-safe, so hand over to the UI thread via a queue."""
        self.pending.put((request, done))

    def _drain(self):
        while not self.pending.empty():
            self._show(*self.pending.get())
        self.root.after(100, self._drain)

    def _show(self, req, done):
        win, remember, closed = tk.Toplevel(self.root, bg=BG), tk.BooleanVar(), []
        offset = 24 * (len(self.root.winfo_children()) - 1)  # stack several pending popups
        win.title("MCP Guard")
        win.attributes("-topmost", True)
        win.resizable(False, False)

        def finish(allow, why="by user"):
            if not closed:
                closed.append(1)
                win.destroy()
                done(allow, remember.get(), why)

        def label(text, fg=TEXT, size=9, weight="normal", family=FONT, **pack):
            tk.Label(win, text=text, bg=BG, fg=fg, font=(family, size, weight), justify="left").pack(anchor="w", padx=16, **pack)

        label(f"MCP GUARD  ·  {req.get('client', '?')}", ACCENT, 8, "bold", pady=(12, 2))
        label(f"Allow {req['target']} on {req.get('server', '?')}?", size=13, family=SERIF)
        if RISKY.search(str(req.get("target"))):
            label("This tool can change or send data.", WARN, pady=(2, 0))
        args = tk.Text(win, width=50, height=4, font=("Consolas", 9), relief="flat", bg=PANEL, fg=TEXT, padx=8, pady=6)
        args.insert("1.0", json.dumps(req.get("arguments"), indent=2, ensure_ascii=False)[:5000])
        args.configure(state="disabled")
        args.pack(padx=16, pady=(8, 4), fill="x")
        tk.Checkbutton(win, text="Remember for this tool", variable=remember, bg=BG, fg=MUTED, activebackground=BG,
                       font=(FONT, 9), highlightthickness=0).pack(anchor="w", padx=12)
        timer = tk.Canvas(win, height=3, bg=PANEL, highlightthickness=0)
        timer.pack(fill="x", padx=16, pady=(6, 0))
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill="x", padx=16, pady=(8, 14))
        countdown = tk.Label(bar, bg=BG, fg=MUTED, font=(FONT, 8))
        countdown.pack(side="left")
        for text, bg, fg, allow in (("Allow", ACCENT, "white", True), ("Block", PANEL, TEXT, False)):
            tk.Button(bar, text=text, bg=bg, fg=fg, activebackground=bg, activeforeground=fg, relief="flat",
                      font=(FONT, 9, "bold"), padx=14, pady=3, cursor="hand2",
                      command=lambda allow=allow: finish(allow)).pack(side="right", padx=(6, 0))
        win.bind("<Escape>", lambda _: finish(False))
        win.bind("<Control-Return>", lambda _: finish(True))  # no plain-Enter allow: a stray keypress must not approve
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))  # closing the window counts as Block

        def tick(left):
            if closed:
                return
            if left <= 0:
                return finish(False, "timed out")
            timer.delete("all")
            timer.create_rectangle(0, 0, timer.winfo_width() * left / self.timeout, 3, fill=ACCENT, width=0)
            countdown.config(text=f"Blocks in {left}s  ·  Esc block  ·  Ctrl+Enter allow")
            win.after(1000, tick, left - 1)

        win.update_idletasks()  # top-right corner, out of the way; placed after layout so its real width is known
        win.geometry(f"+{win.winfo_screenwidth() - win.winfo_reqwidth() - 24 - offset}+{60 + offset}")
        win.after(50, tick, self.timeout)  # after layout, so the timer bar knows its width
        win.lift()
        win.focus_force()
        win.bell()

    def run(self):
        self.root.mainloop()

    def quit(self):
        self.root.after(0, self.root.quit)
