"""Remember what was already sent to HR, so the next download of the same month matches it.

The machine always exports from the 1st with the raw punches. When the 1-15 file has been
fixed and sent, the 1-25 download must show days 1-15 exactly as they were sent; only the
new days need checking.

One JSON file per month in the `memory` folder:
{
  "sends": {"2026-10-15": "2026-10-15 14:20:03"},
  "days": {"<user id>": {"2026-10-01": {"name": "kuna", "times": ["12:43", ...],
                                       "original": ["12:56", ...], "sent_in": "2026-10-15"}}}
}
"""
import datetime
import json
import os

from .record import to_min, to_text


class MonthMemory(object):
    def __init__(self, folder, month_date):
        self.path = os.path.join(folder, "%04d-%02d.json" % (month_date.year, month_date.month))
        self.data = {"sends": {}, "days": {}}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            self.data.setdefault("sends", {})
            self.data.setdefault("days", {})

    def last_sent_before(self, cutoff):
        """Latest cutoff already sent that is earlier than `cutoff` (or None)."""
        earlier = [d for d in self.data["sends"] if d < cutoff.isoformat()]
        return datetime.date(*[int(x) for x in max(earlier).split("-")]) if earlier else None

    def locked(self, cutoff):
        """{(user id, date): times} for days sent in an earlier period than `cutoff`."""
        out = {}
        for uid, days in self.data["days"].items():
            for iso, entry in days.items():
                if entry.get("sent_in", "") < cutoff.isoformat():
                    date = datetime.date(*[int(x) for x in iso.split("-")])
                    out[(uid, date)] = [to_min(t) for t in entry["times"]]
        return out

    def entry(self, uid, date):
        return self.data["days"].get(uid, {}).get(date.isoformat())

    def restore(self, record, cutoff):
        """Put back the days already sent in an earlier period.

        Returns [(employee, date, punches in the new download, punches as sent)] for the days
        where the two differ, i.e. where an earlier fix had to be re-applied.
        """
        changed = []
        locked = self.locked(cutoff)
        for emp in record.employees:
            for date in record.dates():
                key = (emp.user_id, date)
                if key not in locked:
                    continue
                raw = list(emp.days.get(date, []))
                sent = locked[key]
                if raw != sent:
                    record.set_day(emp, date, sent)
                    changed.append((emp, date, raw, sent))
        return changed

    def record_send(self, record, cutoff, originals):
        """Remember every day up to `cutoff` as it is in `record` now.

        `originals` maps (user id, date) to the punches the machine had before fixing.
        Days sent in an earlier period stay as they were; a re-send of the same (or a later)
        period replaces what was remembered for it.
        """
        c = cutoff.isoformat()
        self.data["sends"] = dict((k, v) for k, v in self.data["sends"].items() if k < c)
        self.data["sends"][c] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for uid, days in self.data["days"].items():
            for iso in [i for i, e in days.items() if e.get("sent_in", "") >= c]:
                del days[iso]
        for emp in record.employees:
            days = self.data["days"].setdefault(emp.user_id, {})
            for date in record.dates():
                if date > cutoff:
                    continue
                times = emp.days.get(date, [])
                if date.isoformat() in days:
                    # Sent in an earlier period: only a deliberate re-edit changes it.
                    days[date.isoformat()]["times"] = [to_text(t) for t in times]
                    continue
                orig = originals.get((emp.user_id, date), times)
                days[date.isoformat()] = {
                    "name": emp.name,
                    "times": [to_text(t) for t in times],
                    "original": [to_text(t) for t in orig],
                    "sent_in": c,
                }

    def forget(self):
        self.data = {"sends": {}, "days": {}}
        if os.path.exists(self.path):
            os.remove(self.path)

    def save(self):
        folder = os.path.dirname(self.path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rename(tmp, self.path)
