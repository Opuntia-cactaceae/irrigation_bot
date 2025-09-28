# bot/services/calendar_feed.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, date
from typing import List, Optional, Literal, Iterable, Tuple

import pytz

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType
from .rules import next_by_interval, next_by_weekly  # уже есть у тебя

Mode = Literal["upc", "hist"]  # upcoming / history


@dataclass
class FeedItem:
    plant_id: int
    plant_name: str
    action: ActionType
    dt_utc: datetime
    dt_local: datetime


@dataclass
class DayGroup:
    date_local: date
    items: List[FeedItem]


@dataclass
class FeedPage:
    page: int
    pages: int
    days: List[DayGroup]


# ----- helpers ---------------------------------------------------------------

def _slice_days(grouped: List[DayGroup], page: int, per_page: int) -> Tuple[List[DayGroup], int, int]:
    total = max(1, len(grouped))
    pages = (total + per_page - 1) // per_page
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    end = start + per_page
    return grouped[start:end], page, pages


def _group_by_local_day(items: Iterable[FeedItem]) -> List[DayGroup]:
    bucket: dict[date, List[FeedItem]] = {}
    for it in items:
        d = it.dt_local.date()
        bucket.setdefault(d, []).append(it)
    days: List[DayGroup] = []
    for d in sorted(bucket.keys()):
        # внутри дня отсортируем по локальному времени
        day_items = sorted(bucket[d], key=lambda x: x.dt_local)
        days.append(DayGroup(date_local=d, items=day_items))
    return days


async def _get_user_and_tz(user_tg_id: int) -> tuple[object, pytz.BaseTzInfo]:
    async with new_uow() as uow:
        user = await uow.users.get_or_create(user_tg_id)
        tzname = getattr(user, "tz", None) or "UTC"
    try:
        tz = pytz.timezone(tzname)
    except Exception:
        tz = pytz.UTC
    return user, tz


async def _iter_user_schedules(user_id: int):
    """Пытаемся получить активные расписания пользователя.
    Ожидается метод list_by_user(user_id) -> Iterable[Schedule].
    Если у тебя другой — подправь тут один раз."""
    async with new_uow() as uow:
        try:
            sch = await uow.schedules.list_by_user(user_id)
        except AttributeError:
            sch = []
    return [s for s in sch if getattr(s, "active", True)]


async def _get_last_event_dt_utc(plant_id: int, action: ActionType) -> Optional[datetime]:
    """Пытаемся достать последний факт события для plant+action.
    Под разные реализации репозитория делаем несколько попыток."""
    async with new_uow() as uow:
        # 1) специализированный метод
        try:
            dt = await uow.events.last_dt_for(plant_id=plant_id, action=action)
            if dt:
                return dt
        except AttributeError:
            pass
        # 2) общий список, берём максимум
        try:
            evs = await uow.events.list_by_plant_action(plant_id=plant_id, action=action, limit=1, order="desc")
            if evs:
                e = evs[0]
                return getattr(e, "dt_utc", None) or getattr(e, "created_at_utc", None)
        except AttributeError:
            pass
    return None


# ----- public API ------------------------------------------------------------

async def get_feed(
    *,
    user_tg_id: int,
    action: Optional[ActionType],
    plant_id: Optional[int],
    mode: Mode,
    page: int,
    days_per_page: int,
) -> FeedPage:
    """Строит ленту календаря.
    - upc: по каждому активному расписанию считает ближайшую дату от текущего момента
    - hist: (заглушка) возвращает пустую ленту — можно дополнить выборкой событий
    """

    user, tz = await _get_user_and_tz(user_tg_id)

    if mode == "hist":
        # Заглушка истории — вернём пустую ленту, чтобы UI не падал
        grouped: List[DayGroup] = []
        days, page, pages = _slice_days(grouped, page=1, per_page=days_per_page)
        return FeedPage(page=page, pages=pages, days=days)

    # -------- UPCOMING --------
    items: List[FeedItem] = []
    schedules = await _iter_user_schedules(getattr(user, "id", 0))

    now_utc = datetime.now(pytz.UTC)

    for sch in schedules:
        # фильтры
        sch_action: ActionType = getattr(sch, "action", None)
        if action is not None and sch_action != action:
            continue

        sch_plant_id: Optional[int] = getattr(sch, "plant_id", None) or getattr(getattr(sch, "plant", None), "id", None)
        if plant_id is not None and sch_plant_id != plant_id:
            continue

        if not sch_plant_id:
            continue  # некорректное расписание

        plant_name = getattr(getattr(sch, "plant", None), "name", None) or f"#{sch_plant_id}"

        # исходные параметры расписания
        sch_type = getattr(sch, "type", None)  # "interval" | "weekly"
        local_time = getattr(sch, "local_time", None)
        interval_days = getattr(sch, "interval_days", None)
        weekly_mask = getattr(sch, "weekly_mask", None)

        # последняя зафиксированная дата события
        last_dt_utc = await _get_last_event_dt_utc(sch_plant_id, sch_action)

        # вычисляем следующий раз
        try:
            if sch_type == "interval":
                nxt_utc = next_by_interval(
                    last_dt_utc, interval_days, local_time, tz.zone, now_utc
                )
            else:
                nxt_utc = next_by_weekly(
                    last_dt_utc, weekly_mask, local_time, tz.zone, now_utc
                )
        except Exception:
            # если что-то не так с полями — пропускаем расписание
            continue

        dt_local = nxt_utc.astimezone(tz)

        items.append(
            FeedItem(
                plant_id=sch_plant_id,
                plant_name=plant_name,
                action=sch_action,
                dt_utc=nxt_utc,
                dt_local=dt_local,
            )
        )

    # группируем по локальным дням и режем на страницы
    grouped = _group_by_local_day(items)
    days, page, pages = _slice_days(grouped, page=page, per_page=days_per_page)

    return FeedPage(page=page, pages=pages, days=days)