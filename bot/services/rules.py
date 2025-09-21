from datetime import datetime, time, timedelta
import pytz

# weekly_mask: Пн=1<<0 ... Вс=1<<6
def _utc_from_local(local_dt, tz_name: str) -> datetime:
    tz = pytz.timezone(tz_name)
    localized = tz.localize(local_dt)
    return localized.astimezone(pytz.UTC)

def next_by_interval(last_watered_utc: datetime | None, interval_days: int, local_t: time, tz_name: str, now_utc: datetime) -> datetime:
    tz = pytz.timezone(tz_name)
    base_local = (last_watered_utc.astimezone(tz) if last_watered_utc else now_utc.astimezone(tz))
    # следующий «день+время» от точки отсчёта
    target_local_date = (base_local.date() + timedelta(days=interval_days))
    target_local = datetime.combine(target_local_date, local_t)
    target_utc = _utc_from_local(target_local, tz_name)
    if target_utc <= now_utc:
        # если уже прошло — докрутить на шаги по interval_days
        while target_utc <= now_utc:
            target_local = target_local + timedelta(days=interval_days)
            target_utc = _utc_from_local(target_local, tz_name)
    return target_utc

def next_by_weekly(last_watered_utc: datetime | None, weekly_mask: int, local_t: time, tz_name: str, now_utc: datetime) -> datetime:
    tz = pytz.timezone(tz_name)
    start_local = (last_watered_utc.astimezone(tz) if last_watered_utc else now_utc.astimezone(tz))
    # искать ближайший день недели, помеченный в маске, с указанным временем
    for d in range(0, 14):  # максимум 2 недели поиска
        cand = start_local + timedelta(days=d)
        bit = 1 << (cand.weekday())  # Mon=0
        if weekly_mask & bit:
            cand_local = datetime.combine(cand.date(), local_t)
            cand_utc = _utc_from_local(cand_local, tz_name)
            if cand_utc > now_utc:
                return cand_utc
    # fallback: на неделю вперёд
    return _utc_from_local(datetime.combine((start_local + timedelta(days=7)).date(), local_t), tz_name)