# bot/services/rules.py
from __future__ import annotations
from datetime import datetime, time, timedelta
from bot.db_repo.models import ActionSource
from typing import Optional
import pytz
from pytz import AmbiguousTimeError, NonExistentTimeError


def _tz(tz_name: Optional[str]) -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(tz_name or "UTC")
    except Exception:
        return pytz.UTC


def _localize_safe(tz: pytz.BaseTzInfo, naive_dt: datetime) -> datetime:
    try:
        return tz.localize(naive_dt)
    except AmbiguousTimeError:
        return tz.localize(naive_dt)
    except NonExistentTimeError:
        bump = naive_dt + timedelta(hours=1)
        return tz.localize(bump)


def _utc_from_local(local_dt: datetime, tz_name: str) -> datetime:
    tz = _tz(tz_name)
    aware = _localize_safe(tz, local_dt)
    return aware.astimezone(pytz.UTC)


def next_by_interval(
    last_utc: Optional[datetime],
    interval_days: int,
    local_t: time,
    tz_name: str,
    now_utc: datetime,
) -> datetime:
    """
    Следующее наступление по интервалу (каждые N дней в локальное время local_t).
    Гарантирует строгоe будущее относительно now_utc.
    """
    tz = _tz(tz_name)
    now_local = now_utc.astimezone(tz)


    base_local = last_utc.astimezone(tz) if last_utc else now_local


    first_target_local_date = base_local.date() + timedelta(days=interval_days)
    first_target_local = datetime.combine(first_target_local_date, local_t)


    target_utc = _utc_from_local(first_target_local, tz_name)

    if target_utc > now_utc:
        return target_utc

    lag_days = (now_local.date() - first_target_local_date).days
    steps = lag_days // interval_days + 1 if interval_days > 0 else 1
    target_local = first_target_local + timedelta(days=steps * interval_days)

    return _utc_from_local(target_local, tz_name)

def _compose_local(dt_local_date, local_t: time) -> datetime:
    return datetime.combine(dt_local_date, local_t)

def _weekly_bitmask_hit(dt_local: datetime, weekly_mask: int) -> bool:
    return bool(weekly_mask & (1 << dt_local.weekday()))

def _next_weekly_after(ref_utc: datetime, weekly_mask: int, local_t: time, tz_name: str) -> datetime:

    tz = _tz(tz_name)
    ref_local = ref_utc.astimezone(tz)
    for d in range(0, 14):
        cand_day_local = ref_local + timedelta(days=d)
        if _weekly_bitmask_hit(cand_day_local, weekly_mask):
            cand_local = _compose_local(cand_day_local.date(), local_t)
            cand_utc = _utc_from_local(cand_local, tz_name)
            if cand_utc > ref_utc:
                return cand_utc
    fb_local = _compose_local((ref_local + timedelta(days=7)).date(), local_t)
    return _utc_from_local(fb_local, tz_name)

def _prev_weekly_at_or_before(ref_utc: datetime, weekly_mask: int, local_t: time, tz_name: str) -> datetime:

    tz = _tz(tz_name)
    ref_local = ref_utc.astimezone(tz)
    for d in range(0, 14):
        cand_day_local = ref_local - timedelta(days=d)
        if _weekly_bitmask_hit(cand_day_local, weekly_mask):
            cand_local = _compose_local(cand_day_local.date(), local_t)
            cand_utc = _utc_from_local(cand_local, tz_name)
            if cand_utc <= ref_utc:
                return cand_utc

    fb_local = _compose_local((ref_local - timedelta(days=7)).date(), local_t)
    return _utc_from_local(fb_local, tz_name)

def next_by_weekly(
    *,
    last_done_utc: Optional[datetime],
    last_done_source: Optional["ActionSource"],
    weekly_mask: int,
    local_t: time,
    tz_name: str,
    now_utc: datetime,
) -> datetime:

    next1 = _next_weekly_after(now_utc, weekly_mask, local_t, tz_name)

    if last_done_utc and last_done_source == ActionSource.MANUAL:
        prev_slot = _prev_weekly_at_or_before(now_utc, weekly_mask, local_t, tz_name)
        if prev_slot < last_done_utc < next1:
            return _next_weekly_after(next1, weekly_mask, local_t, tz_name)

    return next1