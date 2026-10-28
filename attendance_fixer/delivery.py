"""Reporting period, file names and email subject for HR."""
import calendar
import datetime
import json
import os
import shutil

REPORT_NAMES = {
    "abnormal": "Abnormal Attendance Statistic Report",
    "attendance": "Attendance Report",
    "record": "Employee Attendance Record",
}

DEFAULT_SETTINGS = {
    "outlet": "MRT",
    "hr_email": "hr@mptwatches.com",
    "subject_prefix": "MPT ATTENDANCE",
    "max_break_minutes": 90,
    "shifts": {
        "Full day": {"start": "10:00", "end": "21:45", "breaks": 2,
                     "slots": ["10:00", "13:30", "14:50", "18:30", "19:50", "21:45"]},
        "Morning": {"start": "10:00", "end": "19:00", "breaks": 1,
                    "slots": ["10:00", "13:30", "14:50", "19:00"]},
        "Noon": {"start": "12:45", "end": "21:45", "breaks": 1,
                 "slots": ["12:45", "17:00", "18:20", "21:45"]},
    },
}


def load_settings(path):
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
        except (ValueError, OSError):
            pass
    else:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except OSError:
            pass
    return settings


def cutoff_options(any_date):
    """The three sending dates of a month: 15th, 25th and the last day."""
    last = calendar.monthrange(any_date.year, any_date.month)[1]
    return [any_date.replace(day=d) for d in (15, 25, last)]


def default_cutoff(file_end):
    """Latest sending date that the downloaded data already reaches."""
    opts = [d for d in cutoff_options(file_end) if d <= file_end]
    return opts[-1] if opts else file_end


def period(cutoff):
    return cutoff.replace(day=1), cutoff


def file_name(kind, outlet, cutoff):
    start, end = period(cutoff)
    return "%s %s %s-%s.xls" % (REPORT_NAMES[kind], outlet,
                                start.strftime("%d.%m.%y"), end.strftime("%d.%m.%y"))


def subject(prefix, cutoff):
    start, end = period(cutoff)
    return "%s %s - %s" % (prefix, start.strftime("%d.%m.%Y"), end.strftime("%d.%m.%Y"))


def output_folder(base_dir, prefix, cutoff):
    return os.path.join(base_dir, subject(prefix, cutoff))


def copy_report(src, kind, folder, outlet, cutoff):
    dst = os.path.join(folder, file_name(kind, outlet, cutoff))
    shutil.copyfile(src, dst)
    return dst


def parse_hhmm(text):
    h, m = text.strip().split(":")
    return int(h) * 60 + int(m)


def shifts_from_settings(settings):
    out = {}
    for name, s in settings["shifts"].items():
        out[name] = dict(start=parse_hhmm(s["start"]), end=parse_hhmm(s["end"]),
                         breaks=int(s["breaks"]), slots=list(s["slots"]))
    return out


def today():
    return datetime.date.today()
