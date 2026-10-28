"""Shift rules, problem detection and human-looking suggestions."""
import random

from .record import to_text

# Each shift: start/end in minutes, number of breaks, and default punch times for every slot
# (in, [break out, break in]*, out). Defaults are replaced by each employee's own habits.
DEFAULT_SHIFTS = {
    "Full day": dict(start=10 * 60, end=21 * 60 + 45, breaks=2,
                     slots=["10:00", "13:30", "14:50", "18:30", "19:50", "21:45"]),
    "Morning": dict(start=10 * 60, end=19 * 60, breaks=1,
                    slots=["10:00", "13:30", "14:50", "19:00"]),
    "Noon": dict(start=12 * 60 + 45, end=21 * 60 + 45, breaks=1,
                 slots=["12:45", "17:00", "18:20", "21:45"]),
}
MAX_BREAK = 90
MISSING_COST = 60
EXTRA_COST = 120          # a punch that fits nowhere is rarer than a forgotten one
BREAK_WEIGHT = 0.25       # break times vary a lot between people; in/out times do not
UNUSUAL_SHIFT_COST = 20


def _m(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


class Rules(object):
    def __init__(self, shifts=None, max_break=MAX_BREAK, early_min=(2, 13), close_exact=True):
        self.shifts = {}
        for name, s in (shifts or DEFAULT_SHIFTS).items():
            self.shifts[name] = dict(start=s["start"], end=s["end"], breaks=s["breaks"],
                                     slots=[_m(x) if isinstance(x, str) else x for x in s["slots"]])
        self.max_break = max_break
        self.early_min = early_min        # punch in this many minutes before the shift starts
        self.close_exact = close_exact    # last punch-out exactly at closing time (21:45)


class Profile(object):
    """What a given employee usually does: favourite shift and typical punch time per slot."""

    def __init__(self, rules, emp):
        self.rules = rules
        self.slots = {name: list(s["slots"]) for name, s in rules.shifts.items()}
        self.usual = None
        counts = {}
        samples = {}
        for times in emp.days.values():
            shift, assign, cost = best_match(rules, times, self.slots, None)
            if shift is None or None in assign or len(times) != len(assign):
                continue
            counts[shift] = counts.get(shift, 0) + 1
            samples.setdefault(shift, []).append(assign)
        if counts:
            self.usual = max(counts, key=counts.get)
        for shift, rows in samples.items():
            if len(rows) < 2:
                continue
            n = len(self.slots[shift])
            for k in range(1, n - 1):   # only break slots; in/out come from the rules
                vals = sorted(r[k] for r in rows)
                self.slots[shift][k] = vals[len(vals) // 2]


def match(times, slots):
    """Order-preserving assignment of punches to slots minimising time distance.

    Returns (cost, assignment) where assignment[k] is the punch for slot k or None.
    Punches that fit no slot are dropped (counted as extra).
    """
    n, m = len(times), len(slots)
    INF = float("inf")
    # dp[i][j]: best cost using first i punches and first j slots
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            cur = dp[i][j]
            if cur == INF:
                continue
            if i < n and j < m:
                w = 1.0 if j in (0, m - 1) else BREAK_WEIGHT
                c = cur + w * abs(times[i] - slots[j])
                if c < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1], back[i + 1][j + 1] = c, "use"
            if j < m:
                c = cur + MISSING_COST
                if c < dp[i][j + 1]:
                    dp[i][j + 1], back[i][j + 1] = c, "skip_slot"
            if i < n:
                c = cur + EXTRA_COST
                if c < dp[i + 1][j]:
                    dp[i + 1][j], back[i + 1][j] = c, "drop"
    assign = [None] * m
    i, j = n, m
    while i > 0 or j > 0:
        step = back[i][j]
        if step == "use":
            assign[j - 1] = times[i - 1]
            i, j = i - 1, j - 1
        elif step == "skip_slot":
            j -= 1
        else:
            i -= 1
    return dp[n][m], assign


def best_match(rules, times, slots_by_shift, usual):
    best = (None, None, float("inf"))
    for name in rules.shifts:
        cost, assign = match(sorted(times), slots_by_shift[name])
        if usual is not None and name != usual:
            cost += UNUSUAL_SHIFT_COST
        if cost < best[2]:
            best = (name, assign, cost)
    return best


class Suggestion(object):
    def __init__(self, emp, date, original, shift, proposed, problems):
        self.emp = emp
        self.date = date
        self.original = list(original)
        self.shift = shift
        self.proposed = list(proposed)
        self.problems = problems
        self.status = "pending"      # pending / accepted / rejected / edited

    def describe(self, times):
        return "  ".join("%s-%s" % (to_text(times[i]), to_text(times[i + 1])) if i + 1 < len(times)
                         else "%s-?" % to_text(times[i]) for i in range(0, len(times), 2))


def _rng(emp, date, salt):
    return random.Random("%s|%s|%s" % (emp.user_id, date.isoformat(), salt))


def check_day(rules, profile, emp, date, times, shift=None, salt=0):
    """Return (shift, proposed_times, problems) for one day of punches."""
    times = sorted(times)
    if shift is None:
        shift, assign, _ = best_match(rules, times, profile.slots, profile.usual)
    else:
        _, assign = match(times, profile.slots[shift])
    sdef = rules.shifts[shift]
    typical = profile.slots[shift]
    rnd = _rng(emp, date, salt)
    problems = []

    used = [t for t in assign if t is not None]
    extras = [t for t in times if t not in used or used.count(t) < times.count(t)]
    if extras:
        problems.append("Extra punch %s" % ", ".join(to_text(t) for t in sorted(set(extras))))

    n = len(assign)
    missing = [k for k, t in enumerate(assign) if t is None]
    names = ["in"] + sum([["break %d out" % (b + 1), "break %d in" % (b + 1)]
                          for b in range(sdef["breaks"])], []) + ["out"]
    if missing:
        problems.append("Missing " + ", ".join(names[k] for k in missing))

    new = list(assign)
    # First punch in: humans arrive a few minutes early.
    first_in = new[0]
    if first_in is None or first_in > sdef["start"]:
        if first_in is not None:
            problems.append("Late in %s" % to_text(first_in))
        new[0] = sdef["start"] - rnd.randint(*rules.early_min)
    # Last punch out: at closing time (or a couple of minutes after for earlier shifts).
    last = new[-1]
    if last is None or last < sdef["end"]:
        if last is not None:
            problems.append("Left early %s" % to_text(last))
        if rules.close_exact and sdef["end"] >= 21 * 60:
            new[-1] = sdef["end"]
        else:
            new[-1] = sdef["end"] + rnd.randint(0, 4)
    # Breaks
    for b in range(sdef["breaks"]):
        ko, ki = 1 + 2 * b, 2 + 2 * b
        out_t, in_t = new[ko], new[ki]
        typical_len = max(45, min(rules.max_break - 3, typical[ki] - typical[ko]))
        if out_t is None and in_t is None:
            out_t = typical[ko] + rnd.randint(-7, 7)
            in_t = out_t + _break_len(rnd, typical_len, rules.max_break)
        elif out_t is None:
            out_t = in_t - _break_len(rnd, typical_len, rules.max_break)
        elif in_t is None:
            in_t = out_t + _break_len(rnd, typical_len, rules.max_break)
        elif in_t - out_t > rules.max_break:
            problems.append("Break %d too long (%s)" % (b + 1, _dur(in_t - out_t)))
            in_t = out_t + _break_len(rnd, typical_len, rules.max_break)
        new[ko], new[ki] = out_t, in_t

    new = _make_consistent(new, rules, rnd)
    if new != times and not problems:
        problems.append("Adjusted")
    return shift, new, problems


def _break_len(rnd, typical_len, max_break):
    return max(30, min(max_break - 2, typical_len + rnd.randint(-8, 6)))


def _dur(m):
    return "%dh%02d" % (m // 60, m % 60)


def _make_consistent(times, rules, rnd):
    """Keep punches strictly increasing with a sensible gap between them."""
    out = list(times)
    for k in range(1, len(out)):
        if out[k] <= out[k - 1] + 5:
            out[k] = out[k - 1] + 5 + rnd.randint(1, 9)
    # If the push overran the closing punch, pull earlier punches back instead.
    for k in range(len(out) - 2, -1, -1):
        if out[k] >= out[k + 1] - 5:
            out[k] = out[k + 1] - 5 - rnd.randint(1, 9)
    return out


def analyse(rules, record, cutoff=None, include_ok=False):
    """Suggestions for every employee/day that has punches, up to `cutoff`."""
    result = []
    for emp in record.employees:
        profile = Profile(rules, emp)
        for date in record.dates():
            if cutoff is not None and date > cutoff:
                continue
            times = emp.days.get(date)
            if not times:
                continue
            shift = None
            if date == record.end and profile.usual and times[-1] < rules.shifts[profile.usual]["end"]:
                # Download day: the rest of the day was not punched yet, assume the usual shift.
                shift = profile.usual
            shift, proposed, problems = check_day(rules, profile, emp, date, times, shift=shift)
            if problems or include_ok:
                result.append(Suggestion(emp, date, times, shift, proposed, problems))
    return result


def fill_day(rules, emp, date, shift, salt=0):
    """Make up a full natural-looking day for `shift` (for days keyed in by hand)."""
    profile = Profile(rules, emp)
    _, proposed, _ = check_day(rules, profile, emp, date, [], shift=shift, salt=salt)
    return proposed
