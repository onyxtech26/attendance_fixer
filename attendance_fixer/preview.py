"""Preview window: the Employee Attendance Record as it will be saved, laid out like the sheet."""
import tkinter as tk
from tkinter import ttk

from . import rules
from .record import to_text

DAY_W = 54
NAME_W = 0
LINE_H = 15
PAD = 3

COLORS = {
    "changed": "#fff2b3",    # changed by this send
    "sent": "#dcefdc",       # kept as already sent to HR
    "problem": "#f8d0d0",    # still has a problem
    "normal": "#ffffff",
}
LEGEND = (("changed", "Changed now"), ("sent", "Kept as sent before"),
          ("problem", "Still has a problem"), ("normal", "From the machine"))


def describe(times):
    return "  ".join("%s-%s" % (to_text(times[i]), to_text(times[i + 1])) if i + 1 < len(times)
                     else "%s-?" % to_text(times[i]) for i in range(0, len(times), 2)) or "(none)"


class PreviewWindow(object):
    """`rec` is the record as it will be saved, `originals` the machine punches,
    `sent_days` the (user id, date) keys restored from an earlier send."""

    def __init__(self, parent, rec, originals, sent_days, rules_obj, cutoff, title):
        self.rec = rec
        self.originals = originals
        self.sent_days = sent_days
        self.cutoff = cutoff
        self.dates = [d for d in rec.dates() if d <= cutoff]
        self.problems = {}
        for s in rules.analyse(rules_obj, rec, cutoff):
            self.problems[(s.emp.user_id, s.date)] = s.problems

        top = self.top = tk.Toplevel(parent)
        top.title(title)
        w = min(top.winfo_screenwidth() - 40, 40 + DAY_W * len(self.dates))
        h = min(top.winfo_screenheight() - 80, 680)
        top.geometry("%dx%d+10+10" % (max(w, 700), h))

        head = ttk.Frame(top)
        head.pack(fill="x", padx=8, pady=6)
        ttk.Label(head, text="Attendance date: %s ~ %s" % (rec.start.isoformat(), cutoff.isoformat()),
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        for key, label in reversed(LEGEND):
            ttk.Label(head, text=label).pack(side="right", padx=(2, 10))
            tk.Label(head, width=2, background=COLORS[key], relief="solid", borderwidth=1).pack(side="right")

        body = ttk.Frame(top)
        body.pack(fill="both", expand=True, padx=8)
        self.canvas = tk.Canvas(body, background="#ffffff", highlightthickness=0)
        xs = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        ys = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="we")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.info = ttk.Label(top, text="Click a day to see what the machine had and what will be saved.",
                              foreground="#444")
        self.info.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="Close", command=top.destroy).pack(side="right", padx=8, pady=(0, 8))

        self.cells = {}
        self._draw()
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

    def _kind(self, emp, date):
        key = (emp.user_id, date)
        if key in self.problems:
            return "problem"
        if key in self.sent_days:
            return "sent"
        if self.originals.get(key, []) != emp.days.get(date, []):
            return "changed"
        return "normal"

    def _draw(self):
        c = self.canvas
        y = 4
        x0 = 4
        width = x0 + DAY_W * len(self.dates)
        for emp in self.rec.employees:
            c.create_rectangle(x0, y, width, y + 22, fill="#eef2fb", outline="#3355cc")
            c.create_text(x0 + 6, y + 11, anchor="w", font=("Segoe UI", 9, "bold"), fill="#2244aa",
                          text="User ID: %s      Name: %s      Department: %s" % (emp.user_id, emp.name, emp.dept))
            y += 22
            for i, date in enumerate(self.dates):
                x = x0 + i * DAY_W
                c.create_rectangle(x, y, x + DAY_W, y + 20, fill="#f6f8fd", outline="#3355cc")
                c.create_text(x + DAY_W / 2, y + 10, font=("Segoe UI", 8, "bold"), fill="#2244aa",
                              text=date.strftime("%d %a"))
            y += 20
            rows = max([1] + [(len(emp.days.get(d, [])) + 1) // 2 for d in self.dates])
            h = rows * (2 * LINE_H + PAD) + PAD
            for i, date in enumerate(self.dates):
                x = x0 + i * DAY_W
                kind = self._kind(emp, date)
                c.create_rectangle(x, y, x + DAY_W, y + h, fill=COLORS[kind], outline="#3355cc")
                times = emp.days.get(date, [])
                for k in range(0, len(times), 2):
                    pair = [to_text(t) for t in times[k:k + 2]]
                    c.create_text(x + DAY_W / 2, y + PAD + (k // 2) * (2 * LINE_H + PAD), anchor="n",
                                  font=("Segoe UI", 8), text="\n".join(pair))
                self.cells[(x, y, x + DAY_W, y + h)] = (emp, date)
            y += h + 10
        c.configure(scrollregion=(0, 0, width + 8, y + 4))

    def _click(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for (x1, y1, x2, y2), (emp, date) in self.cells.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                key = (emp.user_id, date)
                text = "%s  %s   machine: %s   ->   will be saved: %s" % (
                    emp.name, date.strftime("%a %d.%m.%Y"), describe(self.originals.get(key, [])),
                    describe(emp.days.get(date, [])))
                if key in self.sent_days:
                    text += "   (kept as sent before)"
                if key in self.problems:
                    text += "   PROBLEM: " + "; ".join(self.problems[key])
                self.info.config(text=text)
                return
