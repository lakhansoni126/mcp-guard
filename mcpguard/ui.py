"""Always-on-top Tk confirmation popups, one window per pending request (stacked when several arrive)."""
import json
import queue
import tkinter as tk


class PopupUI:
    def __init__(self, timeout):
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
        win, remember, closed = tk.Toplevel(self.root), tk.BooleanVar(), []
        offset = 60 + 28 * (len(self.root.winfo_children()) - 1)
        win.title("MCP Guard - allow this request?")
        win.geometry(f"+{offset}+{offset}")
        win.attributes("-topmost", True)

        def finish(allow, why="by user"):
            if not closed:
                closed.append(1)
                win.destroy()
                done(allow, remember.get(), why)

        text = (f"{req.get('client', '?')}  ->  {req.get('server', '?')}\n"
                f"{req['method']}:  {req['target']}")
        tk.Label(win, text=text, justify="left", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        args = tk.Text(win, width=64, height=12, font=("Consolas", 10))
        args.insert("1.0", json.dumps(req.get("arguments"), indent=2, ensure_ascii=False)[:5000])
        args.configure(state="disabled")
        args.pack(padx=12, fill="both", expand=True)
        tk.Checkbutton(win, text="Remember for this tool until MCP Guard restarts",
                       variable=remember).pack(anchor="w", padx=8, pady=4)
        bar = tk.Frame(win)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        countdown = tk.Label(bar, fg="gray")
        countdown.pack(side="left")
        tk.Button(bar, text="Block", width=10, command=lambda: finish(False)).pack(side="right")
        tk.Button(bar, text="Allow", width=10, command=lambda: finish(True)).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))  # closing the window counts as Block

        def tick(left):
            if closed:
                return
            if left <= 0:
                return finish(False, "timed out")
            countdown.config(text=f"Blocks automatically in {left}s")
            win.after(1000, tick, left - 1)

        tick(self.timeout)
        win.lift()
        win.focus_force()
        win.bell()

    def run(self):
        self.root.mainloop()

    def quit(self):
        self.root.after(0, self.root.quit)
