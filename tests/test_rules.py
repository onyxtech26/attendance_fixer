import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attendance_fixer import cfb, delivery, rules  # noqa: E402
from attendance_fixer.record import Employee, to_min  # noqa: E402

D = datetime.date(2026, 10, 2)


def t(*xs):
    return [to_min(x) for x in xs]


def employee(days):
    e = Employee("9", "test", "COMPANY", 0)
    for day, times in days.items():
        e.days[datetime.date(2026, 10, day)] = t(*times)
    return e


FULL = ("09:50", "13:30", "14:50", "18:30", "19:50", "21:45")


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.r = rules.Rules()

    def check(self, emp, times, shift=None):
        return rules.check_day(self.r, rules.Profile(self.r, emp), emp, D, t(*times), shift=shift)

    def assert_valid(self, shift, times):
        sdef = self.r.shifts[shift]
        self.assertEqual(len(times), 2 + 2 * sdef["breaks"])
        self.assertEqual(times, sorted(times))
        self.assertLessEqual(times[0], sdef["start"])
        self.assertGreaterEqual(times[-1], sdef["end"])
        for k in range(1, len(times) - 1, 2):
            self.assertLessEqual(times[k + 1] - times[k], self.r.max_break)

    def test_clean_full_day_has_no_problem(self):
        emp = employee({1: FULL})
        shift, new, problems = self.check(emp, FULL)
        self.assertEqual((shift, problems), ("Full day", []))
        self.assertEqual(new, t(*FULL))

    def test_full_day_with_early_breaks_is_not_noon(self):
        times = ("09:50", "12:03", "13:28", "17:00", "18:25", "21:45")
        shift, _, problems = self.check(employee({1: times}), times)
        self.assertEqual((shift, problems), ("Full day", []))

    def test_noon_and_morning_detected(self):
        noon = ("12:40", "17:29", "17:51", "21:45")
        morning = ("09:46", "13:00", "14:02", "19:03")
        self.assertEqual(self.check(employee({}), noon)[0], "Noon")
        self.assertEqual(self.check(employee({}), morning)[0], "Morning")

    def test_long_break_is_shortened(self):
        times = ("09:50", "13:30", "15:30", "18:30", "19:50", "21:45")
        shift, new, problems = self.check(employee({1: FULL}), times)
        self.assertTrue(any("too long" in p for p in problems))
        self.assert_valid(shift, new)
        self.assertEqual(new[1], to_min("13:30"))

    def test_late_in_moved_a_few_minutes_early(self):
        times = ("10:12",) + FULL[1:]
        shift, new, problems = self.check(employee({1: FULL}), times)
        self.assertIn("Late in 10:12", problems)
        self.assertTrue(to_min("09:45") <= new[0] < to_min("10:00"))

    def test_download_day_is_completed(self):
        # Downloaded at 2pm: only the morning punches exist.
        shift, new, problems = self.check(employee({1: FULL}), ("09:55", "13:31", "14:49"), shift="Full day")
        self.assertTrue(problems)
        self.assert_valid(shift, new)
        self.assertEqual(new[:3], t("09:55", "13:31", "14:49"))

    def test_filled_days_look_human(self):
        emp = employee({1: FULL})
        days = [rules.fill_day(self.r, emp, datetime.date(2026, 10, d), "Full day") for d in range(1, 11)]
        for times in days:
            self.assert_valid("Full day", times)
        self.assertGreater(len(set(d[0] for d in days)), 3, "arrival times should vary")


class DeliveryTest(unittest.TestCase):
    def test_names(self):
        cut = datetime.date(2026, 10, 25)
        self.assertEqual(delivery.file_name("record", "MRT", cut),
                         "Employee Attendance Record MRT 01.10.26-25.10.26.xls")
        self.assertEqual(delivery.file_name("abnormal", "MRT", cut),
                         "Abnormal Attendance Statistic Report MRT 01.10.26-25.10.26.xls")
        self.assertEqual(delivery.subject("MPT ATTENDANCE", cut), "MPT ATTENDANCE 01.10.2026 - 25.10.2026")

    def test_default_cutoff(self):
        self.assertEqual(delivery.default_cutoff(datetime.date(2026, 10, 28)), datetime.date(2026, 10, 25))
        self.assertEqual(delivery.default_cutoff(datetime.date(2026, 10, 15)), datetime.date(2026, 10, 15))
        self.assertEqual(delivery.default_cutoff(datetime.date(2026, 2, 28)), datetime.date(2026, 2, 28))


class FakeRecord(object):
    def __init__(self, employees, last_day):
        self.employees = employees
        self._dates = [datetime.date(2026, 10, d) for d in range(1, last_day + 1)]

    def dates(self):
        return self._dates

    def set_day(self, emp, date, times):
        if times:
            emp.days[date] = sorted(times)
        else:
            emp.days.pop(date, None)


class MemoryTest(unittest.TestCase):
    def test_sent_days_come_back_in_next_download(self):
        import tempfile
        from attendance_fixer import memory
        folder = tempfile.mkdtemp()
        c15, c25 = datetime.date(2026, 10, 15), datetime.date(2026, 10, 25)
        raw = {5: ("10:20",) + FULL[1:], 15: ("09:55", "13:31")}

        # 15th: download, fix day 5 (late) and day 15 (download day), send.
        rec = FakeRecord([employee(raw)], 15)
        originals = dict(((rec.employees[0].user_id, d), list(v)) for d, v in rec.employees[0].days.items())
        rec.set_day(rec.employees[0], datetime.date(2026, 10, 5), t(*FULL))
        rec.set_day(rec.employees[0], datetime.date(2026, 10, 15), t(*FULL))
        mem = memory.MonthMemory(folder, c15)
        mem.record_send(rec, c15, originals)
        mem.save()

        # 25th: new download has the raw punches again (day 15 now complete, but different).
        raw[15] = ("09:55", "13:31", "14:49", "18:40", "19:58", "21:45")
        rec2 = FakeRecord([employee(raw)], 25)
        mem2 = memory.MonthMemory(folder, c25)
        self.assertEqual(mem2.last_sent_before(c25), c15)
        changed = mem2.restore(rec2, c25)
        self.assertEqual(len(changed), 2)
        self.assertEqual(rec2.employees[0].days[datetime.date(2026, 10, 5)], t(*FULL))
        self.assertEqual(rec2.employees[0].days[datetime.date(2026, 10, 15)], t(*FULL))

        # Re-doing the 15th itself does not lock its own days.
        self.assertEqual(memory.MonthMemory(folder, c15).locked(c15), {})


class CompoundFileTest(unittest.TestCase):
    def test_round_trip(self):
        cf = cfb.CompoundFile()
        cf.streams = [["Workbook", os.urandom(30000)], ["GLOG", os.urandom(7000)],
                      ["\x05SummaryInformation", os.urandom(212)], ["\x01CompObj", os.urandom(107)]]
        back = cfb.loads(cfb.dumps(cf))
        self.assertEqual(back.streams, cf.streams)


@unittest.skipUnless(os.environ.get("ATTENDANCE_SAMPLE"), "set ATTENDANCE_SAMPLE to a real export to run")
class RealFileTest(unittest.TestCase):
    def test_fix_and_save(self):
        import tempfile
        from attendance_fixer import record
        rec = record.AttendanceRecord(os.environ["ATTENDANCE_SAMPLE"])
        r = rules.Rules()
        cut = delivery.default_cutoff(rec.end)
        for s in rules.analyse(r, rec, cut):
            rec.set_day(s.emp, s.date, s.proposed)
        out = os.path.join(tempfile.mkdtemp(), "out.xls")
        rec.save(out, cut)
        again = record.AttendanceRecord(out)
        self.assertEqual(again.end, cut)
        self.assertEqual(rules.analyse(r, again, cut), [])


if __name__ == "__main__":
    unittest.main()
