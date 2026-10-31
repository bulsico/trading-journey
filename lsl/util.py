"""Shared helpers: UTC/ET time without tzdata, deterministic JSON, rounding."""
import datetime as dt
import json
import os

UTC = dt.timezone.utc


def F(x):
    """Lenient float: None/'' -> 0.0."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def to_utc(ts):
    """ISO string, epoch ms (int) or datetime -> aware UTC datetime. Naive = UTC."""
    if isinstance(ts, (int, float)):
        return dt.datetime.fromtimestamp(ts / 1000.0, UTC)
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        if " " in s and "T" not in s:
            s = s.replace(" ", "T", 1)
        # fromisoformat before 3.11 needs 0, 3 or 6 fractional digits
        if "." in s:
            head, rest = s.split(".", 1)
            i = 0
            while i < len(rest) and rest[i].isdigit():
                i += 1
            frac, tail = rest[:i], rest[i:]
            s = head + "." + (frac + "000000")[:6] + tail
        ts = dt.datetime.fromisoformat(s)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def iso(ts, seconds_only=False):
    t = to_utc(ts)
    if seconds_only:
        return t.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ms(ts):
    return int(round(to_utc(ts).timestamp() * 1000))


# ---- America/New_York without tzdata (US rules since 1987) ----
def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_sunday(year, month):
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def et_offset_hours(t):
    """-4 during US daylight time, else -5. t is aware UTC."""
    y = t.year
    if y >= 2007:
        start, end = _nth_sunday(y, 3, 2), _nth_sunday(y, 11, 1)
    else:
        start, end = _nth_sunday(y, 4, 1), _last_sunday(y, 10)
    # 2:00 local standard -> 07:00 UTC; 2:00 local daylight -> 06:00 UTC
    s = dt.datetime(start.year, start.month, start.day, 7, tzinfo=UTC)
    e = dt.datetime(end.year, end.month, end.day, 6, tzinfo=UTC)
    return -4 if s <= t < e else -5


def to_et(ts):
    t = to_utc(ts)
    return (t + dt.timedelta(hours=et_offset_hours(t))).replace(tzinfo=None)


def et_wall_to_utc(day, hour):
    """'YYYY-MM-DD' + local ET hour -> aware UTC."""
    d = dt.date.fromisoformat(day[:10])
    guess = dt.datetime(d.year, d.month, d.day, hour, tzinfo=UTC) + dt.timedelta(hours=5)
    off = et_offset_hours(guess)
    return dt.datetime(d.year, d.month, d.day, hour, tzinfo=UTC) - dt.timedelta(hours=off)


def et_day(ts):
    return to_et(ts).strftime("%Y-%m-%d")


def et_month(ts):
    return to_et(ts).strftime("%Y-%m")


def add_months(d, n):
    """date/datetime -> first day of the month n months later (n may be negative)."""
    m = d.year * 12 + (d.month - 1) + n
    return d.replace(year=m // 12, month=m % 12 + 1, day=1)


# ---- rounding + JSON ----
def r2(x):
    return None if x is None else round(float(x) + 0.0, 2) + 0.0


def r4(x):
    return None if x is None else round(float(x) + 0.0, 4) + 0.0


def dumps(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(dumps(obj))
    os.replace(tmp, path)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)
