# services/calendar.py
from datetime import datetime, timedelta, time
import calendar as pycal
import pytz
from .rules import next_by_interval, next_by_weekly

def month_overview(user, plants, year: int, month: int):
    tz = pytz.timezone(user.tz)
    first = datetime(year, month, 1, tzinfo=pytz.UTC)
    # соберём набор дат, когда запланированы поливы (на основе правил + last_watered)
    marks = {}  # date -> list[str plant names]
    now_utc = datetime.now(pytz.UTC)
    for p in plants:
        sch = p.schedule
        if not sch or not sch.active: continue
        # накинем до 6 появлений вперёд, чтобы покрыть месяц
        nxt = None
        last = max((e.watered_at_utc for e in p.events), default=None)
        for _ in range(12):
            if sch.type == "interval":
                nxt = next_by_interval(last, sch.interval_days, sch.local_time, user.tz, now_utc if not nxt else nxt - timedelta(seconds=1))
            else:
                nxt = next_by_weekly(last, sch.weekly_mask, sch.local_time, user.tz, now_utc if not nxt else nxt - timedelta(seconds=1))
            d_local = nxt.astimezone(tz).date()
            if d_local.month == month and d_local.year == year:
                marks.setdefault(d_local, []).append(p.name)
            if (d_local.year, d_local.month) > (year, month):
                break
            last = nxt  # шагнём дальше
    # рендер сетки
    cal = pycal.Calendar(firstweekday=0)
    lines = ["План поливов на {:%B %Y}:".format(first.astimezone(tz))]
    for week in cal.monthdatescalendar(year, month):
        row = []
        for d in week:
            lab = f"{d.day:2d}"
            if d in marks:
                lab = f"*{lab}"
            row.append(lab)
        lines.append(" ".join(row))
    # список дат с растениями
    for d, names in sorted(marks.items()):
        lines.append(f"{d:%d.%m}: " + ", ".join(names))
    return "\n".join(lines)