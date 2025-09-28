# bot/services/rules.py
from __future__ import annotations
from datetime import datetime, time, timedelta
from typing import Optional
import pytz
from pytz import AmbiguousTimeError, NonExistentTimeError

# weekly_mask: Пн=1<<0 ... Вс=1<<6

def _tz(tz_name: Optional[str]) -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(tz_name or "UTC")
    except Exception:
        return pytz.UTC


def _localize_safe(tz: pytz.BaseTzInfo, naive_dt: datetime) -> datetime:
    """
    Безопасно локализует наивный datetime в часовой пояс:
    - при двусмысленном времени (осень, откат часа) берём более поздний вариант (is_dst=False),
      чтобы не «откатывать» расписание назад.
    - при несуществующем времени (весна, пропуск часа) сдвигаем на +1 час вперёд.
    """
    try:
        return tz.localize(naive_dt, is_dst=None)
    except AmbiguousTimeError:
        # выбрать "поздний" вариант времени
        return tz.localize(naive_dt, is_dst=False)
    except NonExistentTimeError:
        # прыжок вперёд через «дыру» в один час
        bump = naive_dt + timedelta(hours=1)
        return tz.localize(bump, is_dst=None)


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

    # Базовая точка в локальном времени (последнее событие или "сейчас")
    base_local = last_utc.astimezone(tz) if last_utc else now_local

    # Первое «целевое» наступление: (дата base_local + N дней) в local_t
    first_target_local_date = base_local.date() + timedelta(days=interval_days)
    first_target_local = datetime.combine(first_target_local_date, local_t)

    # Переводим в UTC
    target_utc = _utc_from_local(first_target_local, tz_name)

    if target_utc > now_utc:
        return target_utc

    # Если уже прошло — докручиваем кратно интервалу, не гоняя while по одному шагу
    # Оценим, сколько "отрезков" надо добавить, ориентируясь на локальную шкалу времени
    target_local = first_target_local
    # насколько мы отстаём в сутках (округление вверх)
    # +1 гарантирует строго > now
    lag_days = (now_local.date() - first_target_local_date).days
    steps = lag_days // interval_days + 1 if interval_days > 0 else 1
    target_local = first_target_local + timedelta(days=steps * interval_days)

    return _utc_from_local(target_local, tz_name)


def next_by_weekly(
    last_utc: Optional[datetime],
    weekly_mask: int,
    local_t: time,
    tz_name: str,
    now_utc: datetime,
) -> datetime:
    """
    Следующее наступление по недельной маске:
    - Пн=1<<0 ... Вс=1<<6 (weekday(): Пн=0..Вс=6).
    - Берём ближайший отмеченный день с учётом local_t, результат строго > now_utc.
    """
    tz = _tz(tz_name)
    now_local = now_utc.astimezone(tz)
    base_local = last_utc.astimezone(tz) if last_utc else now_local

    # Ищем ближайшее попадание в ближайшие 2 недели (обычно хватает 7)
    for d in range(0, 14):
        cand_day = base_local + timedelta(days=d)
        bit = 1 << cand_day.weekday()
        if weekly_mask & bit:
            cand_local = datetime.combine(cand_day.date(), local_t)
            cand_utc = _utc_from_local(cand_local, tz_name)
            if cand_utc > now_utc:
                return cand_utc

    # Теоретически не должны сюда попадать, но на всякий случай — через неделю
    fallback_local = datetime.combine((base_local + timedelta(days=7)).date(), local_t)
    return _utc_from_local(fallback_local, tz_name)