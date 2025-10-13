# bot/services/calendar_feed.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from math import ceil
from typing import Optional, Literal, Dict, List

import pytz

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import (
    User, Plant, Schedule, ActionType, ScheduleType, ActionSource, ActionStatus,
    ShareLink, ShareMember, ShareMemberStatus,
)
from .rules import next_by_interval, next_by_weekly

Mode = Literal["upc", "hist"]

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
    start_naive = datetime.combine(d, time.min)
    end_naive = datetime.combine(d, time.max)
    start_local = tz.localize(start_naive)
    end_local = tz.localize(end_naive)
    return start_local, end_local


async def get_feed(
    user_tg_id: int,
    action: Optional[ActionType],
    plant_id: Optional[int],
    mode: Mode,
    page: int,
    days_per_page: int,
) -> FeedPage:
    async with new_uow() as uow:
        user: User = await uow.users.get(user_tg_id)
        try:
            plants: List[Plant] = await uow.plants.list_by_user_with_relations(user.id)
        except AttributeError:
            plants: List[Plant] = await uow.plants.list_by_user(user.id)

        if plant_id:
            plants = [p for p in plants if p.id == plant_id]

        tz = _safe_tz(getattr(user, "tz", None))
        tz_name = getattr(user, "tz", "UTC")
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

        start_local_dt, end_local_dt = _localize_day_bounds(tz, start_local_day)
        if end_local_day != start_local_day:
            _, end_local_dt = _localize_day_bounds(tz, end_local_day)

        start_utc = start_local_dt.astimezone(pytz.UTC)
        end_utc = end_local_dt.astimezone(pytz.UTC)

        items: List[FeedItem] = []

        for p in plants:
            p_schedules: List[Schedule] = list(getattr(p, "schedules", []) or [])
            schedules: List[Schedule] = [
                s for s in p_schedules
                if getattr(s, "active", True) and (action is None or s.action == action)
            ]
            if not schedules:
                continue

            last_by_schedule: Dict[int, tuple[Optional[datetime], Optional[ActionSource]]] = {}
            for s in schedules:
                last_by_schedule[s.id] = await uow.action_logs.last_effective_done(s.id)

            for s in schedules:
                base_now: datetime = start_utc - timedelta(seconds=1)
                cursor: Optional[datetime] = None
                last_dt_utc, last_src = last_by_schedule.get(s.id, (None, None))
                plant_name = getattr(p, "name", None) or f"#{getattr(p, 'id', 0)}"

                for _ in range(days_per_page * 8):
                    last_anchor = last_dt_utc if cursor is None else cursor

                    if s.type == ScheduleType.INTERVAL:
                        nxt = next_by_interval(
                            last_anchor,
                            s.interval_days,
                            s.local_time,
                            tz_name,
                            base_now,
                        )
                    else:
                        nxt = next_by_weekly(
                            last_done_utc=last_anchor,
                            last_done_source=last_src,
                            weekly_mask=s.weekly_mask,
                            local_t=s.local_time,
                            tz_name=tz_name,
                            now_utc=base_now,
                        )

                    if nxt > end_utc:
                        break

                    d_loc = nxt.astimezone(tz).date()
                    if start_local_day <= d_loc <= end_local_day:
                        items.append(
                            FeedItem(
                                dt_utc=nxt,
                                dt_local=nxt.astimezone(tz),
                                plant_id=p.id,
                                plant_name=plant_name,
                                action=s.action,
                                schedule_id=s.id,
                            )
                        )

                    cursor = nxt
                    base_now = nxt
                    if s.type != ScheduleType.INTERVAL:
                        last_src = ActionSource.SCHEDULE

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

from math import ceil
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List
import pytz

# ожидается, что эти сущности/утилиты уже есть в проекте:
# new_uow, _safe_tz, _localize_day_bounds, next_by_interval, next_by_weekly
# UPC_MAX_DAYS, HIST_MAX_DAYS
# User, Plant, Schedule, ShareLink, ShareMember, ShareMemberStatus, ActionSource, ActionType, ScheduleType
# FeedPage, FeedDay, FeedItem


def _mode_str(mode: object) -> str:
    if isinstance(mode, str):
        s = mode.lower()
    else:
        s = (getattr(mode, "value", None) or getattr(mode, "name", "") or "").lower()
    return "hist" if s == "hist" else "upc"


async def get_feed_subs(
    user_tg_id: int,
    action: Optional[ActionType],
    mode,
    page: int,
    days_per_page: int,
) -> FeedPage:
    """
    Фид по событиям из ПОДПИСОК:
    - учитывает активность подписки и ссылки
    - показывает историю только при разрешении show_history (эффективном)
    - вычисляет наступления строго по тем же правилам, что get_feed
    """
    async with new_uow() as uow:
        user: User | None = await uow.users.get(user_tg_id)
        if not user:
            return FeedPage(page=1, pages=1, days=[])

        # --- TZ и окна периода ---
        tz = _safe_tz(getattr(user, "tz", None))
        tz_name = getattr(tz, "zone", None) or getattr(user, "tz", "UTC") or "UTC"
        today_local = datetime.now(tz).date()
        now_utc = datetime.now(pytz.UTC)

        mode_str = _mode_str(mode)
        page = max(1, int(page))
        days_per_page = max(1, int(days_per_page))

        if mode_str == "upc":
            start_local_day = today_local + timedelta(days=(page - 1) * days_per_page)
            end_local_day = start_local_day + timedelta(days=days_per_page - 1)
            max_days = UPC_MAX_DAYS
        else:
            end_local_day = today_local - timedelta(days=(page - 1) * days_per_page + 1)
            start_local_day = end_local_day - timedelta(days=days_per_page - 1)
            max_days = HIST_MAX_DAYS

        total_pages = max(1, ceil(max_days / days_per_page))

        start_local_dt, _ = _localize_day_bounds(tz, start_local_day)
        _, end_local_dt = _localize_day_bounds(tz, end_local_day)
        start_utc = start_local_dt.astimezone(pytz.UTC)
        end_utc = end_local_dt.astimezone(pytz.UTC)

        # --- активные и не приглушённые подписки пользователя ---
        members: List[ShareMember] = await uow.share_members.list_by_user(user.id)
        members = [
            m for m in (members or [])
            if getattr(m, "status", None) == ShareMemberStatus.ACTIVE
            and not getattr(m, "muted", False)
        ]
        if not members:
            return FeedPage(page=page, pages=total_pages, days=[])

        # --- валидные ссылки + эффективные права истории ---
        effective_show_history_by_share: Dict[int, bool] = {}
        effective_share_ids: List[int] = []

        for m in members:
            link: ShareLink | None = await uow.share_links.get(m.share_id)
            if not link:
                continue
            if not getattr(link, "is_active", True):
                continue
            exp = getattr(link, "expires_at_utc", None)
            if exp is not None and exp <= now_utc:
                continue

            show_history = (
                m.show_history_override
                if m.show_history_override is not None
                else bool(getattr(link, "show_history_default", True))
            )
            if mode_str == "hist" and not show_history:
                continue

            effective_share_ids.append(link.id)
            effective_show_history_by_share[link.id] = bool(show_history)

        if not effective_share_ids:
            return FeedPage(page=page, pages=total_pages, days=[])

        # --- schedules, доступные через эти ссылки ---
        link_schedules: List = []
        for sid in effective_share_ids:
            part = await uow.share_links.list_by_share(sid)
            if part:
                link_schedules.extend(part)

        sched_ids = list({ls.schedule_id for ls in link_schedules})
        if not sched_ids:
            return FeedPage(page=page, pages=total_pages, days=[])

        # --- сами расписания (фильтруем активные и по action) ---
        schedules: List[Schedule] = await uow.schedules.list_by_ids(sched_ids)
        if action is not None:
            schedules = [s for s in schedules if s.action == action]
        schedules = [s for s in schedules if getattr(s, "active", True)]
        if not schedules:
            return FeedPage(page=page, pages=total_pages, days=[])

        plant_ids = {s.plant_id for s in schedules}
        plant_name_cache: Dict[int, str] = {}
        if plant_ids:
            plants: List[Plant] = await uow.plants.list_by_ids(list(plant_ids))
            plant_name_cache = {
                p.id: (getattr(p, "name", None) or f"#{getattr(p, 'id', 0)}")
                for p in (plants or [])
            }

        last_by_schedule: Dict[int, tuple[datetime | None, ActionSource | None]] = {}
        for s in schedules:
            last_by_schedule[s.id] = await uow.action_logs.last_effective_done(s.id)

        items: List[FeedItem] = []
        share_ids_by_sched: Dict[int, List[int]] = {}
        for ls in link_schedules:
            share_ids_by_sched.setdefault(ls.schedule_id, []).append(ls.share_id)

        max_steps_per_schedule = max(8 * days_per_page, days_per_page + 3)

        for s in schedules:
            if mode_str == "hist":
                if not any(
                    effective_show_history_by_share.get(sh, False)
                    for sh in share_ids_by_sched.get(s.id, [])
                ):
                    continue

            base_now: datetime = start_utc - timedelta(seconds=1)
            cursor: datetime | None = None
            last_dt_utc, last_src = last_by_schedule.get(s.id, (None, None))

            plant_name = plant_name_cache.get(s.plant_id, f"#{getattr(s, 'plant_id', 0)}")

            for _ in range(max_steps_per_schedule):
                last_anchor = last_dt_utc if cursor is None else cursor

                if s.type == ScheduleType.INTERVAL:
                    nxt = next_by_interval(
                        last_anchor,
                        s.interval_days,
                        s.local_time,
                        tz_name,
                        base_now,
                    )
                else:
                    nxt = next_by_weekly(
                        last_done_utc=last_anchor,
                        last_done_source=last_src,
                        weekly_mask=s.weekly_mask,
                        local_t=s.local_time,
                        tz_name=tz_name,
                        now_utc=base_now,
                    )

                if nxt > end_utc:
                    break

                d_loc = nxt.astimezone(tz).date()
                if start_local_day <= d_loc <= end_local_day:
                    items.append(
                        FeedItem(
                            dt_utc=nxt,
                            dt_local=nxt.astimezone(tz),
                            plant_id=s.plant_id,
                            plant_name=plant_name,
                            action=s.action,
                            schedule_id=s.id,
                        )
                    )

                cursor = nxt
                base_now = nxt
                if s.type != ScheduleType.INTERVAL:
                    last_src = ActionSource.SCHEDULE

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