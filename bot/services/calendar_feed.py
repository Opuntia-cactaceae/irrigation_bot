# bot/services/calendar_feed.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from math import ceil
from typing import Optional, Literal, Iterable, Dict, List

import pytz

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import User, Plant, Schedule, Event, ActionType
from .rules import next_by_interval, next_by_weekly

Mode = Literal["upc", "hist"]  # upcoming | history

# Горизонты (сколько дней максимум листаем)
UPC_MAX_DAYS = 90
HIST_MAX_DAYS = 180


@dataclass
class FeedItem:
    dt_utc: datetime
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
        plants: list[Plant] = await uow.plants.list_by_user_with_relations(user.id)

    # фильтрация растений
    if plant_id:
        plants = [p for p in plants if p.id == plant_id]

    # таймзона пользователя
    tz = pytz.timezone(user.tz)

    # Локальные границы страницы (по дням, локальная календарная сетка)
    today_local = datetime.now(tz).date()

    if mode == "upc":
        # Стр.1: сегодня..+N-1, стр.2: +N..+2N-1 ...
        page = max(1, page)
        start_local_day = today_local + timedelta(days=(page - 1) * days_per_page)
        end_local_day = start_local_day + timedelta(days=days_per_page - 1)
        max_days = UPC_MAX_DAYS
    else:
        # История: Стр.1: (вчера..-N+1), Стр.2: (-N..-2N+1) ...
        page = max(1, page)
        end_local_day = today_local - timedelta(days=(page - 1) * days_per_page + 1)
        start_local_day = end_local_day - timedelta(days=days_per_page - 1)
        max_days = HIST_MAX_DAYS

    total_pages = max(1, ceil(max_days / days_per_page))

    # Переводим локальные дни в UTC-диапазон
    start_local_dt = datetime.combine(start_local_day, time.min).replace(tzinfo=tz)
    end_local_dt = datetime.combine(end_local_day, time.max).replace(tzinfo=tz)

    start_utc = start_local_dt.astimezone(pytz.UTC)
    end_utc = end_local_dt.astimezone(pytz.UTC)

    # Сбор кандидатов в диапазоне [start_local_day .. end_local_day]
    items: list[FeedItem] = []

    for p in plants:
        # отбираем расписания
        schedules: list[Schedule] = [
            s for s in (p.schedules or [])
            if s.active and (action is None or s.action == action)
        ]
        if not schedules:
            continue

        # события для якорей
        by_action_last: dict[ActionType, Optional[datetime]] = {}
        for s in schedules:
            last = max(
                (e.done_at_utc for e in (p.events or []) if e.action == s.action),
                default=None
            )
            by_action_last[s.action] = last

        # для каждого расписания генерируем наступления в рамках диапазона
        for s in schedules:
            # маленький «толчок» назад, чтобы первая генерация могла попасть прямо в начало окна
            base_now = start_utc - timedelta(seconds=1)

            # шагать вперёд до выхода за край окна
            cursor: Optional[datetime] = None
            guard = 0
            while guard < 200:  # страховка от бесконечного цикла
                guard += 1
                # для первого шага last = фактический последний Event; далее last = предыдущая сгенерированная dt
                last_anchor = by_action_last[s.action] if cursor is None else cursor

                if s.type == "interval":
                    nxt = next_by_interval(
                        last_anchor, s.interval_days, s.local_time, user.tz, base_now
                    )
                else:
                    nxt = next_by_weekly(
                        last_anchor, s.weekly_mask, s.local_time, user.tz, base_now
                    )

                # если вылетели за окно — стоп
                if nxt > end_utc:
                    break

                # положим, если внутри окна
                nxt_local_day = nxt.astimezone(tz).date()
                if start_local_day <= nxt_local_day <= end_local_day:
                    items.append(
                        FeedItem(
                            dt_utc=nxt,
                            plant_id=p.id,
                            plant_name=p.name,
                            action=s.action,
                            schedule_id=s.id,
                        )
                    )

                # перейти к следующему
                cursor = nxt
                base_now = nxt  # следующий будет строго после текущего

                # небольшое ускорение: если локальный день уже превысил окно и расписание weekly,
                # дальше вряд ли попадём — но оставим общий break по end_utc.

    # Группировка по локальной дате и сортировка
    by_day: Dict[date, List[FeedItem]] = {}
    for it in items:
        d = it.dt_utc.astimezone(tz).date()
        by_day.setdefault(d, []).append(it)

    # Соберём дни по порядку от start_local_day до end_local_day
    days: List[FeedDay] = []
    cur = start_local_day
    while cur <= end_local_day:
        day_items = sorted(
            by_day.get(cur, []),
            key=lambda x: x.dt_utc
        )
        if day_items:
            days.append(FeedDay(date_local=cur, items=day_items))
        cur += timedelta(days=1)

    return FeedPage(page=page, pages=total_pages, days=days)