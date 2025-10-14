# bot/services/calendar_feed.py
from __future__ import annotations
from dataclasses import dataclass
from math import ceil
from datetime import datetime, timedelta, date, time
from typing import Optional, Dict, List, Literal, Iterator
import pytz

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import (
    User, Plant, Schedule, ActionType, ScheduleType, ActionSource, ActionStatus,
    ShareLink, ShareMember, ShareMemberStatus, ShareLinkSchedule,
)
from .rules import next_by_interval, next_by_weekly, _localize_day_bounds, compute_window, _safe_tz, _utc_from_local

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

@dataclass
class EffectiveLinks:
    share_ids: list[int]
    show_history_by_share: dict[int, bool]

async def get_effective_links(uow, user_id: int, mode_str: str, now_utc: datetime) -> EffectiveLinks:
    members: List[ShareMember] = await uow.share_members.list_by_user(user_id)
    members = [m for m in members or [] if getattr(m, "status", None) == ShareMemberStatus.ACTIVE and not getattr(m, "muted", False)]
    show_history_by_share: dict[int, bool] = {}
    share_ids: list[int] = []
    for m in members:
        link: ShareLink | None = await uow.share_links.get(m.share_id)
        if not link or not getattr(link, "is_active", True):
            continue
        if (exp := getattr(link, "expires_at_utc", None)) is not None and exp <= now_utc:
            continue
        show_history = m.show_history_override if m.show_history_override is not None else bool(getattr(link, "show_history_default", True))
        if mode_str == "hist" and not show_history:
            continue
        share_ids.append(link.id)
        show_history_by_share[link.id] = bool(show_history)
    return EffectiveLinks(share_ids, show_history_by_share)

async def build_plant_name_cache(uow, plant_ids: set[int]) -> dict[int, str]:
    if not plant_ids:
        return {}
    plants: List[Plant] = await uow.plants.list_by_ids(list(plant_ids))
    return {p.id: (getattr(p, "name", None) or f"#{getattr(p, 'id', 0)}") for p in (plants or [])}

def map_share_ids_by_schedule(link_schedules: list[ShareLinkSchedule]) -> dict[int, list[int]]:
    m: dict[int, list[int]] = {}
    for ls in link_schedules:
        m.setdefault(ls.schedule_id, []).append(ls.share_id)
    return m

def is_history_allowed_for_schedule(schedule_id: int,
                                    share_ids_by_sched: dict[int, list[int]],
                                    show_history_by_share: dict[int, bool]) -> bool:
    return any(show_history_by_share.get(sh, False) for sh in share_ids_by_sched.get(schedule_id, []))

def iter_interval_occurrences(*,
    last_dt_utc: datetime | None,
    interval_days: int,
    local_t: time,
    tz_name: str,
    tz: pytz.BaseTzInfo,
    start_utc: datetime,
    end_utc: datetime
) -> Iterator[datetime]:
    base_now = start_utc - timedelta(seconds=1)
    first_evt_utc = next_by_interval(last_dt_utc, interval_days, local_t, tz_name, base_now)
    if first_evt_utc > end_utc:
        return
    first_date_local = first_evt_utc.astimezone(tz).date()
    # поднять до начала окна, сохранив кратность шагу
    if first_date_local < (start_local := start_utc.astimezone(tz).date()):
        lag = (start_local - first_date_local).days
        k = (lag + interval_days - 1) // interval_days
        first_date_local += timedelta(days=k * interval_days)
    d = first_date_local
    while d <= end_utc.astimezone(tz).date():
        occ_local = datetime.combine(d, local_t)
        occ_utc = _utc_from_local(occ_local, tz_name)
        if occ_utc > end_utc:
            break
        if occ_utc >= start_utc:
            yield occ_utc
        d += timedelta(days=interval_days)

def iter_weekly_occurrences(*,
    last_dt_utc: datetime | None,
    last_src: ActionSource | None,
    weekly_mask: int,
    local_t: time,
    tz_name: str,
    tz: pytz.BaseTzInfo,
    start_utc: datetime,
    end_utc: datetime
) -> Iterator[datetime]:
    base_now = start_utc - timedelta(seconds=1)
    first_utc = next_by_weekly(
        last_done_utc=last_dt_utc,
        last_done_source=last_src,
        weekly_mask=weekly_mask,
        local_t=local_t,
        tz_name=tz_name,
        now_utc=base_now,
    )
    if first_utc > end_utc:
        return
    if first_utc >= start_utc:
        yield first_utc
    cur_date = first_utc.astimezone(tz).date()
    d = cur_date + timedelta(days=1)
    end_local_day = end_utc.astimezone(tz).date()
    while d <= end_local_day:
        if weekly_mask & (1 << d.weekday()):
            occ_local = datetime.combine(d, local_t)
            occ_utc = _utc_from_local(occ_local, tz_name)
            if occ_utc > end_utc:
                break
            if occ_utc >= start_utc:
                yield occ_utc
        d += timedelta(days=1)

def make_feed_item(dt_utc: datetime, tz: pytz.BaseTzInfo, s: "Schedule", plant_name: str) -> FeedItem:
    return FeedItem(
        dt_utc=dt_utc,
        dt_local=dt_utc.astimezone(tz),
        plant_id=s.plant_id,
        plant_name=plant_name,
        action=s.action,
        schedule_id=s.id,
    )

def group_feed_items_by_day(items: list[FeedItem]) -> list[FeedDay]:
    if not items:
        return []
    by_day: dict[date, list[FeedItem]] = {}
    tz = items[0].dt_local.tzinfo
    for it in items:
        by_day.setdefault(it.dt_local.date(), []).append(it)
    days: list[FeedDay] = []
    for d, arr in sorted(by_day.items()):
        days.append(FeedDay(date_local=d, items=sorted(arr, key=lambda x: x.dt_local)))
    return days

async def get_feed(
    user_tg_id: int,
    action: Optional["ActionType"],
    plant_id: Optional[int],
    mode,
    page: int,
    days_per_page: int,
) -> "FeedPage":
    async with new_uow() as uow:
        user: "User" = await uow.users.get(user_tg_id)

        try:
            plants: List["Plant"] = await uow.plants.list_by_user_with_relations(user.id)
        except AttributeError:
            plants: List["Plant"] = await uow.plants.list_by_user(user.id)

        if plant_id:
            plants = [p for p in plants if p.id == plant_id]

        tz = _safe_tz(getattr(user, "tz", None))
        tz_name = getattr(tz, "zone", None) or getattr(user, "tz", "UTC") or "UTC"
        today_local = datetime.now(tz).date()

        mode_str = _mode_str(mode)
        page = max(1, int(page))
        days_per_page = max(1, int(days_per_page))

        start_local_day, end_local_day, start_utc, end_utc = compute_window(
            mode_str, today_local, page, days_per_page, tz
        )
        max_days = UPC_MAX_DAYS if mode_str == "upc" else HIST_MAX_DAYS
        total_pages = max(1, ceil(max_days / days_per_page))

        items: List["FeedItem"] = []

        for p in plants:
            p_schedules: List["Schedule"] = list(getattr(p, "schedules", []) or [])
            schedules: List["Schedule"] = [
                s for s in p_schedules
                if getattr(s, "active", True) and (action is None or s.action == action)
            ]
            if not schedules:
                continue

            last_by_schedule: Dict[int, tuple[Optional[datetime], Optional["ActionSource"]]] = {}
            for s in schedules:
                last_by_schedule[s.id] = await uow.action_logs.last_effective_done(s.id)

            plant_name = getattr(p, "name", None) or f"#{getattr(p, 'id', 0)}"

            for s in schedules:
                last_dt_utc, last_src = last_by_schedule.get(s.id, (None, None))

                if s.type == ScheduleType.INTERVAL:
                    for occ_utc in iter_interval_occurrences(
                        last_dt_utc=last_dt_utc,
                        interval_days=s.interval_days,
                        local_t=s.local_time,
                        tz_name=tz_name,
                        tz=tz,
                        start_utc=start_utc,
                        end_utc=end_utc,
                    ):
                        items.append(make_feed_item(occ_utc, tz, s, plant_name))
                else:
                    for occ_utc in iter_weekly_occurrences(
                        last_dt_utc=last_dt_utc,
                        last_src=last_src,
                        weekly_mask=s.weekly_mask,
                        local_t=s.local_time,
                        tz_name=tz_name,
                        tz=tz,
                        start_utc=start_utc,
                        end_utc=end_utc,
                    ):
                        items.append(make_feed_item(occ_utc, tz, s, plant_name))

        days: List["FeedDay"] = group_feed_items_by_day(items)
        return FeedPage(page=page, pages=total_pages, days=days)


def _mode_str(mode: object) -> str:
    if isinstance(mode, str):
        s = mode.lower()
    else:
        s = (getattr(mode, "value", None) or getattr(mode, "name", "") or "").lower()
    return "hist" if s == "hist" else "upc"


async def get_feed_subs(
    user_tg_id: int,
    action: Optional["ActionType"],
    mode,
    page: int,
    days_per_page: int,
) -> "FeedPage":
    """
    Фид по событиям из ПОДПИСОК (реализация через хелперы):
    - INTERVAL: первое наступление у границы окна, далее арифметика по локальным датам
    - WEEKLY: первое наступление с MANUAL-скипом, далее скан дат окна по weekly_mask
    """
    async with new_uow() as uow:
        user: "User | None" = await uow.users.get(user_tg_id)
        if not user:
            return FeedPage(page=1, pages=1, days=[])

        tz = _safe_tz(getattr(user, "tz", None))
        tz_name = getattr(tz, "zone", None) or getattr(user, "tz", "UTC") or "UTC"
        today_local = datetime.now(tz).date()
        now_utc = datetime.now(pytz.UTC)

        mode_str = _mode_str(mode)
        page = max(1, int(page))
        days_per_page = max(1, int(days_per_page))

        start_local_day, end_local_day, start_utc, end_utc = compute_window(
            mode_str, today_local, page, days_per_page, tz
        )
        max_days = UPC_MAX_DAYS if mode_str == "upc" else HIST_MAX_DAYS
        total_pages = max(1, ceil(max_days / days_per_page))

        eff = await get_effective_links(uow, user.id, mode_str, now_utc)
        if not eff.share_ids:
            return FeedPage(page=page, pages=total_pages, days=[])

        link_schedules: List["ShareLinkSchedule"] = await uow.share_links.list_link_schedules(eff.share_ids)
        sched_ids = list({ls.schedule_id for ls in link_schedules})
        if not sched_ids:
            return FeedPage(page=page, pages=total_pages, days=[])
        schedules: List["Schedule"] = await uow.schedules.list_active_by_ids(sched_ids, action)
        if not schedules:
            return FeedPage(page=page, pages=total_pages, days=[])

        plant_ids = {s.plant_id for s in schedules}
        plant_name_cache = await build_plant_name_cache(uow, plant_ids)

        last_by_schedule: Dict[int, tuple[datetime | None, "ActionSource | None"]] = {}
        for s in schedules:
            last_by_schedule[s.id] = await uow.action_logs.last_effective_done(s.id)

        share_ids_by_sched = map_share_ids_by_schedule(link_schedules)

        items: List["FeedItem"] = []
        for s in schedules:
            if mode_str == "hist" and not is_history_allowed_for_schedule(
                s.id, share_ids_by_sched, eff.show_history_by_share
            ):
                continue

            last_dt_utc, last_src = last_by_schedule.get(s.id, (None, None))
            plant_name = plant_name_cache.get(s.plant_id, f"#{getattr(s, 'plant_id', 0)}")

            if s.type == ScheduleType.INTERVAL:
                for occ_utc in iter_interval_occurrences(
                    last_dt_utc=last_dt_utc,
                    interval_days=s.interval_days,
                    local_t=s.local_time,
                    tz_name=tz_name,
                    tz=tz,
                    start_utc=start_utc,
                    end_utc=end_utc,
                ):
                    items.append(make_feed_item(occ_utc, tz, s, plant_name))
            else:
                for occ_utc in iter_weekly_occurrences(
                    last_dt_utc=last_dt_utc,
                    last_src=last_src,
                    weekly_mask=s.weekly_mask,
                    local_t=s.local_time,
                    tz_name=tz_name,
                    tz=tz,
                    start_utc=start_utc,
                    end_utc=end_utc,
                ):
                    items.append(make_feed_item(occ_utc, tz, s, plant_name))

        days: List["FeedDay"] = group_feed_items_by_day(items)
        return FeedPage(page=page, pages=total_pages, days=days)