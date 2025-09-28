# bot/services/calendar_feed.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from math import ceil
from typing import Optional, Literal, Dict, List

import pytz

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import User, Plant, Schedule, ActionType
from .rules import next_by_interval, next_by_weekly

Mode = Literal["upc", "hist"]  # upcoming | history

# Горизонты (сколько дней максимум листаем)
UPC_MAX_DAYS = 90
HIST_MAX_DAYS = 180


@dataclass
class FeedItem:
    dt_utc: datetime
    dt_local: datetime
    plant_id: int
    plant_name: str
    action: ActionType
    schedule_id: int


@dataclass
class FeedDay:
    date_local: date
    items: List[FeedItem]


@dataclass
class FeedPage:
    page: int
    pages: int
    days: List[FeedDay]


def _safe_tz(name: Optional[str]) -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(name or "UTC")
    except Exception:
        return pytz.UTC


def _localize_day_bounds(tz: pytz.BaseTzInfo, d: date) -> tuple[datetime, datetime]:
    """Границы локального дня с корректной локализацией (DST-safe)."""
    start_naive = datetime.combine(d, time.min)
    end_naive = datetime.combine(d, time.max)
    # pytz требует localize()
    start_local = tz.localize(start_naive, is_dst=None)
    end_local = tz.localize(end_naive, is_dst=None)
    return start_local, end_local


async def get_feed(
    user_tg_id: int,
    action: Optional[ActionType],
    plant_id: Optional[int],
    mode: Mode,
    page: int,
    days_per_page: int,
) -> FeedPage:
    """
    Сформировать ленту календаря.

    :param user_tg_id: Telegram id пользователя
    :param action: фильтр по типу действия (None = все)
    :param plant_id: фильтр по растению (None = все)
    :param mode: 'upc' (ближайшие) или 'hist' (история)
    :param page: номер страницы (>=1)
    :param days_per_page: сколько локальных дней показываем на странице
    """
    async with new_uow() as uow:
        user: User = await uow.users.get_or_create(user_tg_id)
        # ожидается, что этот метод подтягивает p.schedules и p.events;
        # если у тебя его нет — сделай .list_by_user(user.id) и отдельно подтяни связи
        plants: List[Plant] = await uow.plants.list_by_user_with_relations(user.id)

    # фильтрация растений
    if plant_id:
        plants = [p for p in plants if p.id == plant_id]

    tz = _safe_tz(getattr(user, "tz", None))
    today_local = datetime.now(tz).date()

    if mode == "upc":
        page = max(1, page)
        start_local_day = today_local + timedelta(days=(page - 1) * days_per_page)
        end_local_day = start_local_day + timedelta(days=days_per_page - 1)
        max_days = UPC_MAX_DAYS
    else:
        page = max(1, page)
        end_local_day = today_local - timedelta(days=(page - 1) * days_per_page + 1)
        start_local_day = end_local_day - timedelta(days=days_per_page - 1)
        max_days = HIST_MAX_DAYS

    total_pages = max(1, ceil(max_days / days_per_page))

    # Переведём локальные границы в UTC
    start_local_dt, end_local_dt = _localize_day_bounds(tz, start_local_day)
    # если окно больше одного дня — правая граница = конец последнего дня
    if end_local_day != start_local_day:
        _, end_local_dt = _localize_day_bounds(tz, end_local_day)

    start_utc = start_local_dt.astimezone(pytz.UTC)
    end_utc = end_local_dt.astimezone(pytz.UTC)

    items: List[FeedItem] = []

    for p in plants:
        # аккуратно обработаем отсутствие связей
        p_schedules: List[Schedule] = list(getattr(p, "schedules", []) or [])
        p_events = list(getattr(p, "events", []) or [])

        # отбираем расписания
        schedules: List[Schedule] = [
            s for s in p_schedules
            if getattr(s, "active", True) and (action is None or s.action == action)
        ]
        if not schedules:
            continue

        # последние события по действию
        by_action_last: Dict[ActionType, Optional[datetime]] = {}
        for s in schedules:
            last = max(
                (getattr(e, "done_at_utc", None) for e in p_events if e.action == s.action),
                default=None
            )
            by_action_last[s.action] = last

        # генерируем наступления в рамках окна
        for s in schedules:
            base_now = start_utc - timedelta(seconds=1)
            cursor: Optional[datetime] = None
            for _ in range(200):  # страховка от бесконечного цикла
                last_anchor = by_action_last[s.action] if cursor is None else cursor

                if s.type == "interval":
                    nxt = next_by_interval(last_anchor, s.interval_days, s.local_time, getattr(user, "tz", "UTC"), base_now)
                else:
                    nxt = next_by_weekly(last_anchor, s.weekly_mask, s.local_time, getattr(user, "tz", "UTC"), base_now)

                if nxt > end_utc:
                    break

                d_loc = nxt.astimezone(tz).date()
                if start_local_day <= d_loc <= end_local_day:
                    items.append(
                        FeedItem(
                            dt_utc=nxt,
                            dt_local=nxt.astimezone(tz),
                            plant_id=p.id,
                            plant_name=p.name,
                            action=s.action,
                            schedule_id=s.id,
                        )
                    )

                cursor = nxt
                base_now = nxt  # следующий строго после текущего

    # Группировка по локальной дате и сортировка
    by_day: Dict[date, List[FeedItem]] = {}
    for it in items:
        d = it.dt_local.date()
        by_day.setdefault(d, []).append(it)

    days: List[FeedDay] = []
    cur = start_local_day
    while cur <= end_local_day:
        day_items = sorted(by_day.get(cur, []), key=lambda x: x.dt_local)
        if day_items:
            days.append(FeedDay(date_local=cur, items=day_items))
        cur += timedelta(days=1)

    return FeedPage(page=page, pages=total_pages, days=days)