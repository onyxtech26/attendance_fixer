"""Windows desktop app (Tkinter) for checking and fixing the Employee Attendance Record."""
import datetime
import os
import sys
import traceback
import webbrowser

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:  # pragma: no cover
    tk = None

try:
    from urllib.parse import quote
except ImportError:  # pragma: no cover
    from urllib import quote

from . import delivery, record, rules
from .record import to_min, to_text

APP_TITLE = "MPT Attendance Fixer"
KIND_LABELS = [
    ("record", "Employee Attendance Record (the one to fix)"),
    ("attendance", "Attendance Report"),
    ("abnormal", "Abnormal Attendance Statistic Report"),
]


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_times(text):
    parts = text.replace(",", " ").replace("-", " ").split()
    return sorted(to_min(p) for p in parts)


def day_label(date):
    return date.strftime("%d %a")


class EditDialog(object):
    """Edit the punches of one employee/day."""

    def __init__(self, app, emp, date, times, shift):
        self.app = app
        self.emp = emp
        self.date = date
        self.result = None
        self.salt = 100
        top = self.top = tk.Toplevel(app.root)
        top.title("Edit %s - %s" % (emp.name, date.strftime("%d.%m.%Y")))
        top.transient(app.root)
        top.grab_set()
        top.resizable(False, False)
        pad = dict(padx=8, pady=4)

        ttk.Label(top, text="%s  (ID %s)   %s" % (emp.name, emp.user_id, date.strftime("%A %d.%m.%Y")),
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", **pad)
        original = emp.days.get(date, [])
        ttk.Label(top, text="Punches in the file:  " + (" ".join(to_text(t) for t in original) or "(none)")) \
            .grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(top, text="Shift:").grid(row=2, column=0, sticky="e", **pad)
        self.shift = tk.StringVar(value=shift or list(app.rules.shifts)[0])
        ttk.Combobox(top, textvariable=self.shift, values=list(app.rules.shifts), state="readonly",
                     width=14).grid(row=2, column=1, sticky="w", **pad)
        ttk.Button(top, text="Make times for this shift", command=self.generate) \
            .grid(row=2, column=2, sticky="w", **pad)

        ttk.Label(top, text="Times:").grid(row=3, column=0, sticky="e", **pad)
        self.times = tk.StringVar(value=" ".join(to_text(t) for t in times))
        entry = ttk.Entry(top, textvariable=self.times, width=50)
        entry.grid(row=3, column=1, columnspan=2, sticky="w", **pad)
        ttk.Label(top, text="Type times separated by spaces, e.g. 09:52 13:30 14:48 18:31 19:50 21:45",
                  foreground="#666").grid(row=4, column=1, columnspan=2, sticky="w", padx=8)
        self.info = ttk.Label(top, text="", foreground="#a00")
        self.info.grid(row=5, column=0, columnspan=3, sticky="w", **pad)
        self.times.trace_add("write", lambda *a: self.validate())

        btns = ttk.Frame(top)
        btns.grid(row=6, column=0, columnspan=3, sticky="e", **pad)
        ttk.Button(btns, text="Clear day (absent)", command=self.clear).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="left", padx=4)
        ttk.Button(btns, text="Save", command=self.ok).pack(side="left", padx=4)
        self.validate()
        entry.focus_set()
        top.wait_window()

    def generate(self):
        self.salt += 1
        if self.emp.days.get(self.date):
            profile = rules.Profile(self.app.rules, self.emp)
            _, new, _ = rules.check_day(self.app.rules, profile, self.emp, self.date,
                                        self.emp.days[self.date], shift=self.shift.get(), salt=self.salt)
        else:
            new = rules.fill_day(self.app.rules, self.emp, self.date, self.shift.get(), salt=self.salt)
        self.times.set(" ".join(to_text(t) for t in new))

    def validate(self):
        try:
            times = parse_times(self.times.get())
        except ValueError:
            self.info.config(text="Times must look like 09:55")
            return None
        msgs = []
        if len(times) % 2:
            msgs.append("Odd number of punches (one is missing)")
        for k in range(1, len(times) - 1, 2):
            gap = times[k + 1] - times[k]
            if gap > self.app.rules.max_break:
                msgs.append("Break %s-%s is %d min (max %d)" % (to_text(times[k]), to_text(times[k + 1]),
                                                               gap, self.app.rules.max_break))
        self.info.config(text="   ".join(msgs), foreground="#a00" if msgs else "#080")
        if not msgs:
            self.info.config(text="Looks good" if times else "Day will be empty (absent)")
        return times

    def clear(self):
        self.result = (self.shift.get(), [])
        self.top.destroy()

    def ok(self):
        times = self.validate()
        if times is None:
            return
        self.result = (self.shift.get(), times)
        self.top.destroy()


class App(object):
    def __init__(self, root):
        self.root = root
        self.settings = delivery.load_settings(os.path.join(app_dir(), "settings.json"))
        self.rules = rules.Rules(shifts=delivery.shifts_from_settings(self.settings),
                                 max_break=int(self.settings.get("max_break_minutes", 90)))
        self.paths = {k: tk.StringVar() for k, _ in KIND_LABELS}
        self.record = None
        self.suggestions = []
        self.manual = {}            # (emp index, date) -> (shift, times) edited by hand
        root.title(APP_TITLE)
        root.geometry("1100x640")
        root.minsize(900, 520)
        style = ttk.Style()
        try:
            style.configure("Treeview", rowheight=24)
        except tk.TclError:
            pass
        self._build()

    # ------------------------------------------------------------------ layout
    def _build(self):
        root = self.root
        pad = dict(padx=8, pady=4)

        files = ttk.LabelFrame(root, text="1. Files downloaded from the attendance machine")
        files.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Button(files, text="Choose the 3 files...", command=self.pick_all).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)
        for i, (kind, label) in enumerate(KIND_LABELS):
            ttk.Label(files, text=label).grid(row=i + 1, column=0, sticky="w", **pad)
            ttk.Entry(files, textvariable=self.paths[kind], width=80).grid(row=i + 1, column=1, sticky="we", **pad)
            ttk.Button(files, text="Browse", command=lambda k=kind: self.pick_one(k)).grid(row=i + 1, column=2, **pad)
        files.columnconfigure(1, weight=1)

        per = ttk.LabelFrame(root, text="2. Period to send")
        per.pack(fill="x", padx=10, pady=4)
        ttk.Label(per, text="Send up to:").grid(row=0, column=0, **pad)
        self.cutoff = tk.StringVar()
        self.cutoff_box = ttk.Combobox(per, textvariable=self.cutoff, state="readonly", width=14)
        self.cutoff_box.grid(row=0, column=1, **pad)
        self.cutoff_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_period())
        ttk.Label(per, text="Outlet:").grid(row=0, column=2, **pad)
        self.outlet = tk.StringVar(value=self.settings["outlet"])
        ttk.Entry(per, textvariable=self.outlet, width=8).grid(row=0, column=3, **pad)
        self.outlet.trace_add("write", lambda *a: self.refresh_period())
        ttk.Button(per, text="Check attendance", command=self.check).grid(row=0, column=4, **pad)
        self.period_info = ttk.Label(per, text="Choose the files first.", foreground="#444")
        self.period_info.grid(row=1, column=0, columnspan=6, sticky="w", **pad)

        # Packed before the review list so it always stays visible on small screens.
        out = ttk.LabelFrame(root, text="4. Save and email")
        out.pack(side="bottom", fill="x", padx=10, pady=(4, 10))
        ttk.Button(out, text="Save files for HR", command=self.save).pack(side="left", padx=8, pady=6)
        self.out_info = ttk.Label(out, text="")
        self.out_info.pack(side="left", padx=8)

        rev = ttk.LabelFrame(root, text="3. Review the problems found (nothing changes until you accept)")
        rev.pack(fill="both", expand=True, padx=10, pady=4)
        cols = ("emp", "date", "shift", "now", "new", "problem", "status")
        heads = ("Employee", "Date", "Shift", "In the file now", "Suggested", "Problem", "Status")
        widths = (90, 70, 70, 240, 240, 260, 80)
        self.tree = ttk.Treeview(rev, columns=cols, show="headings", selectmode="extended")
        for c, h, w in zip(cols, heads, widths):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, stretch=(c in ("now", "new", "problem")))
        sb = ttk.Scrollbar(rev, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        rev.rowconfigure(0, weight=1)
        rev.columnconfigure(0, weight=1)
        self.tree.tag_configure("pending", background="#fff6d6")
        self.tree.tag_configure("accepted", background="#dff3df")
        self.tree.tag_configure("edited", background="#dce9fb")
        self.tree.tag_configure("rejected", background="#eeeeee", foreground="#888")
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())

        bar = ttk.Frame(rev)
        bar.grid(row=1, column=0, columnspan=2, sticky="we", pady=4)
        for text, cmd in (("Accept", self.accept), ("Accept all", self.accept_all),
                          ("Keep as is", self.reject), ("Other times", self.reroll),
                          ("Edit...", self.edit_selected), ("Add / edit any day...", self.add_day)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=3)
        self.summary = ttk.Label(bar, text="")
        self.summary.pack(side="right", padx=6)

    # ------------------------------------------------------------------- files
    def pick_all(self):
        paths = filedialog.askopenfilenames(title="Choose the 3 downloaded reports",
                                            filetypes=[("Excel 97-2003", "*.xls"), ("All files", "*.*")])
        unknown = []
        for p in paths:
            try:
                kind = record.detect_kind(p)
            except Exception:
                kind = None
            if kind:
                self.paths[kind].set(p)
            else:
                unknown.append(os.path.basename(p))
        if unknown:
            messagebox.showwarning(APP_TITLE, "Could not recognise:\n" + "\n".join(unknown))
        self.load_record()

    def pick_one(self, kind):
        p = filedialog.askopenfilename(filetypes=[("Excel 97-2003", "*.xls"), ("All files", "*.*")])
        if p:
            self.paths[kind].set(p)
            if kind == "record":
                self.load_record()

    def load_record(self):
        path = self.paths["record"].get()
        if not path:
            return
        try:
            self.record = record.AttendanceRecord(path)
        except Exception as e:
            self.record = None
            messagebox.showerror(APP_TITLE, "Could not read the Employee Attendance Record:\n%s" % e)
            return
        opts = delivery.cutoff_options(self.record.end)
        opts = [d for d in opts if d >= self.record.start]
        self.cutoff_box["values"] = [d.strftime("%d.%m.%Y") for d in opts]
        self.cutoff.set(delivery.default_cutoff(self.record.end).strftime("%d.%m.%Y"))
        self.suggestions = []
        self.manual = {}
        self.refresh_tree()
        self.refresh_period()

    def get_cutoff(self):
        try:
            return datetime.datetime.strptime(self.cutoff.get(), "%d.%m.%Y").date()
        except ValueError:
            return None

    def refresh_period(self):
        if not self.record:
            return
        cutoff = self.get_cutoff()
        if not cutoff:
            return
        lines = ["File covers %s to %s.   Email subject: %s" % (
            self.record.start.strftime("%d.%m.%Y"), self.record.end.strftime("%d.%m.%Y"),
            delivery.subject(self.settings["subject_prefix"], cutoff))]
        if cutoff > self.record.end:
            lines.append("WARNING: the file stops before %s - download again on that day."
                         % cutoff.strftime("%d.%m.%Y"))
        elif cutoff < self.record.end:
            lines.append("Days after %s will be removed from the Employee Attendance Record."
                         % cutoff.strftime("%d.%m.%Y"))
        self.period_info.config(text="\n".join(lines))

    # ------------------------------------------------------------------ review
    def check(self):
        if not self.record:
            self.load_record()
            if not self.record:
                messagebox.showinfo(APP_TITLE, "Choose the Employee Attendance Record first.")
                return
        cutoff = self.get_cutoff()
        self.suggestions = rules.analyse(self.rules, self.record, cutoff)
        self.manual = {}
        self.refresh_tree()
        if not self.suggestions:
            messagebox.showinfo(APP_TITLE, "No problems found.")

    def refresh_tree(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for i, s in enumerate(self.suggestions):
            self.tree.insert("", "end", iid=str(i), tags=(s.status,), values=(
                s.emp.name, day_label(s.date), s.shift, s.describe(s.original) or "(none)",
                s.describe(s.proposed) or "(absent)", "; ".join(s.problems), s.status.capitalize()))
        keep = [x for x in sel if self.tree.exists(x)]
        if keep:
            self.tree.selection_set(keep)
        counts = {}
        for s in self.suggestions:
            counts[s.status] = counts.get(s.status, 0) + 1
        self.summary.config(text="  ".join("%s: %d" % (k.capitalize(), v) for k, v in sorted(counts.items())))

    def selected(self):
        return [self.suggestions[int(i)] for i in self.tree.selection()]

    def accept(self):
        for s in self.selected():
            if s.status != "edited":
                s.status = "accepted"
        self.refresh_tree()

    def accept_all(self):
        for s in self.suggestions:
            if s.status == "pending":
                s.status = "accepted"
        self.refresh_tree()

    def reject(self):
        for s in self.selected():
            s.status = "rejected"
        self.refresh_tree()

    def reroll(self):
        for s in self.selected():
            profile = rules.Profile(self.rules, s.emp)
            salt = getattr(s, "salt", 0) + 1
            s.salt = salt
            _, s.proposed, _ = rules.check_day(self.rules, profile, s.emp, s.date, s.original,
                                               shift=s.shift, salt=salt)
            if s.status == "rejected":
                s.status = "pending"
        self.refresh_tree()

    def edit_selected(self):
        sel = self.selected()
        if not sel:
            return
        s = sel[0]
        dlg = EditDialog(self, s.emp, s.date, s.proposed, s.shift)
        if dlg.result:
            s.shift, s.proposed = dlg.result
            s.status = "edited"
            self.refresh_tree()

    def add_day(self):
        if not self.record:
            messagebox.showinfo(APP_TITLE, "Choose the Employee Attendance Record first.")
            return
        top = tk.Toplevel(self.root)
        top.title("Pick employee and day")
        top.transient(self.root)
        top.grab_set()
        names = ["%s (ID %s)" % (e.name, e.user_id) for e in self.record.employees]
        cutoff = self.get_cutoff() or self.record.end
        dates = [d for d in self.record.dates() if d <= cutoff]
        emp_var = tk.StringVar(value=names[0])
        date_var = tk.StringVar(value=dates[-1].strftime("%d.%m.%Y") if dates else "")
        ttk.Label(top, text="Employee:").grid(row=0, column=0, padx=8, pady=6, sticky="e")
        ttk.Combobox(top, textvariable=emp_var, values=names, state="readonly", width=24).grid(
            row=0, column=1, padx=8, pady=6)
        ttk.Label(top, text="Date:").grid(row=1, column=0, padx=8, pady=6, sticky="e")
        ttk.Combobox(top, textvariable=date_var, values=[d.strftime("%d.%m.%Y") for d in dates],
                     state="readonly", width=24).grid(row=1, column=1, padx=8, pady=6)
        chosen = {}

        def go():
            chosen["emp"] = self.record.employees[names.index(emp_var.get())]
            chosen["date"] = datetime.datetime.strptime(date_var.get(), "%d.%m.%Y").date()
            top.destroy()

        ttk.Button(top, text="Next", command=go).grid(row=2, column=1, padx=8, pady=8, sticky="e")
        top.wait_window()
        if not chosen:
            return
        emp, date = chosen["emp"], chosen["date"]
        for s in self.suggestions:
            if s.emp is emp and s.date == date:
                self.tree.selection_set(str(self.suggestions.index(s)))
                self.edit_selected()
                return
        times = emp.days.get(date, [])
        profile = rules.Profile(self.rules, emp)
        shift = profile.usual or list(self.rules.shifts)[0]
        if times:
            shift, _, _ = rules.best_match(self.rules, times, profile.slots, profile.usual)
        dlg = EditDialog(self, emp, date, times, shift)
        if dlg.result:
            s = rules.Suggestion(emp, date, times, dlg.result[0], dlg.result[1], ["Edited by hand"])
            s.status = "edited"
            self.suggestions.append(s)
            self.refresh_tree()

    # -------------------------------------------------------------------- save
    def save(self):
        if not self.record:
            messagebox.showinfo(APP_TITLE, "Choose the Employee Attendance Record first.")
            return
        cutoff = self.get_cutoff()
        if not cutoff:
            return
        missing = [label for kind, label in KIND_LABELS if not self.paths[kind].get()]
        if missing:
            if not messagebox.askyesno(APP_TITLE, "These files are not chosen:\n%s\n\nSave anyway?"
                                       % "\n".join(missing)):
                return
        pending = [s for s in self.suggestions if s.status == "pending"]
        if pending and not messagebox.askyesno(
                APP_TITLE, "%d problem(s) are not reviewed yet and will be left as they are.\n\nContinue?"
                % len(pending)):
            return
        warnings = []
        for kind in ("attendance", "abnormal"):
            p = self.paths[kind].get()
            if p:
                try:
                    rng = record.report_date_range(p)
                except Exception:
                    rng = None
                if rng and rng[1] != cutoff:
                    warnings.append("%s ends on %s, not %s." % (dict(KIND_LABELS)[kind],
                                                               rng[1].strftime("%d.%m.%Y"),
                                                               cutoff.strftime("%d.%m.%Y")))
        if warnings and not messagebox.askyesno(APP_TITLE, "\n".join(warnings) + "\n\nSave anyway?"):
            return

        try:
            # Re-read the original so saving twice never stacks changes.
            rec = record.AttendanceRecord(self.paths["record"].get())
            by_id = dict((e.user_id, e) for e in rec.employees)
            log = []
            for s in self.suggestions:
                if s.status in ("accepted", "edited"):
                    emp = by_id[s.emp.user_id]
                    rec.set_day(emp, s.date, s.proposed)
                    log.append("%s  %s  %s  ->  %s" % (s.emp.name, s.date.isoformat(),
                                                       s.describe(s.original) or "(none)",
                                                       s.describe(s.proposed) or "(absent)"))
            base = os.path.dirname(self.paths["record"].get())
            outlet = self.outlet.get().strip() or self.settings["outlet"]
            folder = delivery.output_folder(base, self.settings["subject_prefix"], cutoff)
            if not os.path.isdir(folder):
                os.makedirs(folder)
            saved = []
            dst = os.path.join(folder, delivery.file_name("record", outlet, cutoff))
            rec.save(dst, cutoff)
            saved.append(dst)
            for kind in ("attendance", "abnormal"):
                p = self.paths[kind].get()
                if p:
                    saved.append(delivery.copy_report(p, kind, folder, outlet, cutoff))
            self._write_log(cutoff, log)
        except Exception as e:
            traceback.print_exc()
            messagebox.showerror(APP_TITLE, "Saving failed:\n%s" % e)
            return
        self.out_info.config(text="Saved to: " + folder)
        self.show_send(folder, cutoff, saved)

    def _write_log(self, cutoff, lines):
        try:
            logs = os.path.join(app_dir(), "logs")
            if not os.path.isdir(logs):
                os.makedirs(logs)
            name = "changes %s.txt" % datetime.datetime.now().strftime("%Y-%m-%d %H%M%S")
            with open(os.path.join(logs, name), "w", encoding="utf-8") as f:
                f.write("Period up to %s\n\n" % cutoff.isoformat())
                f.write("\n".join(lines) or "No changes")
                f.write("\n")
        except OSError:
            pass

    def show_send(self, folder, cutoff, saved):
        subj = delivery.subject(self.settings["subject_prefix"], cutoff)
        to = self.settings["hr_email"]
        top = tk.Toplevel(self.root)
        top.title("Ready to email HR")
        top.transient(self.root)
        pad = dict(padx=8, pady=5)
        ttk.Label(top, text="Files saved. Now send them to HR:", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)
        for r, (label, value) in enumerate((("To:", to), ("Subject:", subj)), start=1):
            ttk.Label(top, text=label).grid(row=r, column=0, sticky="e", **pad)
            e = ttk.Entry(top, width=50)
            e.insert(0, value)
            e.grid(row=r, column=1, sticky="w", **pad)
            ttk.Button(top, text="Copy", command=lambda v=value: self.copy(v)).grid(row=r, column=2, **pad)
        ttk.Label(top, text="Attach these 3 files:").grid(row=3, column=0, columnspan=3, sticky="w", **pad)
        for i, p in enumerate(saved):
            ttk.Label(top, text="   " + os.path.basename(p)).grid(row=4 + i, column=0, columnspan=3,
                                                                 sticky="w", padx=8)
        btns = ttk.Frame(top)
        btns.grid(row=10, column=0, columnspan=3, sticky="e", **pad)
        ttk.Button(btns, text="Open folder", command=lambda: self.open_path(folder)).pack(side="left", padx=4)
        ttk.Button(btns, text="Open email program", command=lambda: webbrowser.open(
            "mailto:%s?subject=%s" % (to, quote(subj)))).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=top.destroy).pack(side="left", padx=4)
        self.copy(subj)

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    @staticmethod
    def open_path(path):
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            webbrowser.open("file://" + path)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
