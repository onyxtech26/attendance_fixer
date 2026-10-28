"""The 'Employee Attendance Record' export: read punches per employee/day and write them back."""
import datetime
import re

from . import biff, cfb

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s*~\s*(\d{4})-(\d{2})-(\d{2})")
TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def to_min(text):
    m = TIME_RE.match(text)
    if not m:
        raise ValueError("Bad time: %r" % text)
    return int(m.group(1)) * 60 + int(m.group(2))


def to_text(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


class Employee(object):
    def __init__(self, user_id, name, dept, header_row):
        self.user_id = user_id
        self.name = name
        self.dept = dept
        self.header_row = header_row
        self.day_row = header_row + 1
        self.punch_rows = []       # sheet rows holding "in\nout" pairs
        self.days = {}             # date -> list of minutes (sorted)

    def __repr__(self):
        return "Employee(%s %s)" % (self.user_id, self.name)


class AttendanceRecord(object):
    def __init__(self, path):
        self.path = path
        self.cf = cfb.read(path)
        self.wb = biff.Workbook(self.cf.get("Workbook"))
        idx = 0
        for i, name in enumerate(self.wb.sheet_names):
            if "attendance record" in name.lower():
                idx = i
        self.sheet = self.wb.sheet(idx)
        self.start = self.end = None
        self.date_cell = None
        self.day_cols = {}         # date -> column (shared by all employees)
        self.employees = []
        self._parse()

    # ------------------------------------------------------------------ read
    def _parse(self):
        sh = self.sheet
        for (r, c), cell in sh.cells.items():
            if cell.kind == "text" and "Attendance date" in cell.value:
                m = DATE_RE.search(cell.value)
                if m:
                    y1, m1, d1, y2, m2, d2 = [int(x) for x in m.groups()]
                    self.start = datetime.date(y1, m1, d1)
                    self.end = datetime.date(y2, m2, d2)
                    self.date_cell = (r, c)
        if self.start is None:
            raise ValueError("This does not look like an Employee Attendance Record "
                             "(no 'Attendance date' found).")
        max_r = sh.max_row()
        max_c = sh.max_col()
        headers = []
        for r in range(max_r + 1):
            for c in range(max_c + 1):
                if str(sh.value(r, c)).strip() == "User ID:":
                    headers.append(r)
                    break
        if not headers:
            raise ValueError("No employees found in the Employee Attendance Record.")
        for n, r in enumerate(headers):
            fields = {}
            for c in range(max_c + 1):
                v = str(sh.value(r, c)).strip()
                if v.endswith(":"):
                    for c2 in range(c + 1, max_c + 1):
                        v2 = sh.value(r, c2)
                        if v2 != "":
                            fields[v] = v2
                            break
            uid = fields.get("User ID:", "")
            if isinstance(uid, float):
                uid = str(int(uid))
            emp = Employee(str(uid), str(fields.get("Name:", "")), str(fields.get("Department:", "")), r)
            end_row = headers[n + 1] if n + 1 < len(headers) else max_r + 1
            emp.punch_rows = list(range(r + 2, end_row))
            self.employees.append(emp)

        # Rows below the last employee can be a footer, not punches: keep only rows
        # formatted like punch rows of the other employees.
        first_col = min([c for (r, c) in sh.cells if r == self.employees[0].day_row and
                         isinstance(sh.value(r, c), float)] or [1])
        punch_xfs = set()
        for emp in self.employees[:-1]:
            for r in emp.punch_rows:
                cell = sh.cells.get((r, first_col))
                if cell is not None:
                    punch_xfs.add(cell.xf)
        last = self.employees[-1]
        if punch_xfs and len(self.employees) > 1:
            keep = []
            for r in last.punch_rows:
                cell = sh.cells.get((r, first_col))
                has_text = any(isinstance(sh.value(r, c), str) and ":" in sh.value(r, c)
                               for c in range(first_col, max_c + 1))
                if (cell is not None and cell.xf in punch_xfs) or has_text:
                    keep.append(r)
                else:
                    break
            last.punch_rows = keep or last.punch_rows[:1]

        # Day columns from the first employee's day-number row
        first = self.employees[0]
        date = self.start
        for c in range(max_c + 1):
            v = sh.value(first.day_row, c)
            if isinstance(v, float) and v >= 1:
                while date.day != int(v) and date <= self.end:
                    date += datetime.timedelta(days=1)
                if date > self.end:
                    break
                self.day_cols[date] = c
                date += datetime.timedelta(days=1)

        for emp in self.employees:
            for date, c in self.day_cols.items():
                times = []
                for r in emp.punch_rows:
                    v = sh.value(r, c)
                    if isinstance(v, str):
                        for part in v.replace("\r", "\n").split("\n"):
                            if part.strip():
                                try:
                                    times.append(to_min(part))
                                except ValueError:
                                    pass
                if times:
                    emp.days[date] = times

    def dates(self):
        return sorted(self.day_cols)

    def employees_by_id(self):
        return dict((e.user_id, e) for e in self.employees)

    # ----------------------------------------------------------------- write
    def _extra_row_formats(self, emp):
        """Cell formats for an added punch row: copy a 2nd/3rd punch row of any employee."""
        sh = self.sheet
        for e in self.employees:
            if len(e.punch_rows) >= 2:
                src = e.punch_rows[-1]
                return dict((c, cell.xf) for (r, c), cell in sh.cells.items() if r == src)
        src = emp.punch_rows[-1] if emp.punch_rows else emp.day_row
        return dict((c, cell.xf) for (r, c), cell in sh.cells.items() if r == src)

    def set_day(self, emp, date, times):
        times = sorted(times)
        if times:
            emp.days[date] = times
        else:
            emp.days.pop(date, None)

    def apply(self, cutoff=None):
        """Write every employee's punches into the sheet (and trim days after `cutoff`)."""
        sh = self.sheet
        if cutoff is not None and cutoff < self.end:
            self._trim(cutoff)
        # Grow employee blocks that need more punch rows (bottom-up so row numbers stay valid)
        for emp in reversed(self.employees):
            need = max([1] + [(len(t) + 1) // 2 for t in emp.days.values()])
            have = len(emp.punch_rows)
            if need > have:
                at = (emp.punch_rows[-1] + 1) if emp.punch_rows else emp.day_row + 1
                template = self._extra_row_formats(emp)
                sh.insert_rows(at, need - have)
                for later in self.employees:
                    if later.header_row >= at:
                        later.header_row += need - have
                        later.day_row += need - have
                        later.punch_rows = [r + need - have for r in later.punch_rows]
                new_rows = list(range(at, at + need - have))
                for r in new_rows:
                    for c, xf in template.items():
                        sh.cells[(r, c)] = biff.Cell("blank", xf)
                emp.punch_rows += new_rows
        for emp in self.employees:
            for date, col in self.day_cols.items():
                times = emp.days.get(date, [])
                pairs = [times[i:i + 2] for i in range(0, len(times), 2)]
                for k, r in enumerate(emp.punch_rows):
                    text = "\n".join(to_text(t) for t in pairs[k]) if k < len(pairs) else ""
                    if sh.value(r, col) != text:
                        sh.set_text(r, col, text)

    def _trim(self, cutoff):
        sh = self.sheet
        drop = [d for d in self.day_cols if d > cutoff]
        rows = set()
        for emp in self.employees:
            rows.add(emp.day_row)
            rows.update(emp.punch_rows)
            for d in drop:
                emp.days.pop(d, None)
        for d in drop:
            c = self.day_cols.pop(d)
            for r in rows:
                sh.remove(r, c)
        r, c = self.date_cell
        text = sh.value(r, c)
        new = DATE_RE.sub(lambda m: "%s~%s" % (self.start.isoformat(), cutoff.isoformat()), text)
        sh.set_text(r, c, new)
        self.end = cutoff

    def save(self, path, cutoff=None):
        self.apply(cutoff)
        self.cf.set("Workbook", self.wb.serialize())
        cfb.write(self.cf, path)


def report_date_range(path):
    """Return (start, end) of the 'Date:' range found in any .xls report, or None."""
    cf = cfb.read(path)
    wb = biff.Workbook(cf.get("Workbook"))
    for i in range(len(wb.sheet_names)):
        sh = wb.sheet(i)
        for cell in sh.cells.values():
            if cell.kind == "text":
                m = DATE_RE.search(cell.value)
                if m:
                    y1, m1, d1, y2, m2, d2 = [int(x) for x in m.groups()]
                    return datetime.date(y1, m1, d1), datetime.date(y2, m2, d2)
    return None


def detect_kind(path):
    """'record', 'attendance' or 'abnormal' based on the sheets inside the file."""
    cf = cfb.read(path)
    wb = biff.Workbook(cf.get("Workbook"))
    names = [n.lower() for n in wb.sheet_names]
    if any("attendance record" in n for n in names):
        return "record"
    if any("abnormal" in n for n in names):
        return "abnormal"
    if any("statistic" in n or "shift setting" in n for n in names):
        return "attendance"
    return None
