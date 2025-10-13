# bot/handlers/history_inline.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone as dt_tz
from typing import Optional, List, Dict

import pytz
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.models import ActionType, ActionStatus, User
from bot.db_repo.unit_of_work import new_uow
from bot.services.cal_shared import (
    CODE_TO_ACTION as ACT_MAP,
    ACTION_TO_EMOJI as ACT_TO_EMOJI,
    ACTION_TO_CODE as ACT_TO_CODE,
    STATUS_TO_EMOJI,
)

history_router = Router(name="history_inline")

PREFIX = "cal"

@dataclass
class HistoryItem:
    dt_local: datetime
    status: ActionStatus
    action: ActionType
    plant_id: Optional[int]
    schedule_id: Optional[int]
    plant_name: str
    is_shared: bool = False


@dataclass
class HistoryDay:
    date_local: date
    items: List[HistoryItem]


@dataclass
class HistoryWeek:
    week_offset: int
    start_local: date
    end_local: date
    days: List[HistoryDay]


def _safe_tz(name: Optional[str]):
    try:
        return pytz.timezone(name or "Europe/Amsterdam")
    except Exception:
        return pytz.timezone("Europe/Amsterdam")


def _local_day_bounds_utc(tz, d: date):
    start_local = tz.localize(datetime.combine(d, time(0, 0)))
    end_local_excl = start_local + timedelta(days=1)
    return start_local.astimezone(dt_tz.utc), end_local_excl.astimezone(dt_tz.utc)

async def _get_history_week(
    *,
    user_tg_id: int,
    action: Optional[ActionType],
    plant_id: Optional[int],
    week_offset: int = 0,
    shared_only: bool = False,
) -> HistoryWeek:
    async with new_uow() as uow:
        user: User = await uow.users.get(user_tg_id)
        tz = _safe_tz(getattr(user, "tz", None))

        today = datetime.now(tz).date()
        this_monday = today - timedelta(days=today.weekday())
        monday = this_monday + timedelta(weeks=week_offset)
        sunday = monday + timedelta(days=6)

        since_utc, _ = _local_day_bounds_utc(tz, monday)
        _, until_utc = _local_day_bounds_utc(tz, sunday + timedelta(days=1))

        own_logs = await uow.action_logs.list_by_user(
            user.id,
            action=action or None,
            status=None,
            since=since_utc,
            until=until_utc,
            limit=10_000,
            offset=0,
            with_relations=False,
        )

        shared_logs = await uow.action_logs.list_shared_for_subscriber(
            user.id,
            action=action or None,
            status=None,
            since=since_utc,
            until=until_utc,
            limit=10_000,
            offset=0,
            with_relations=False,
        )

        logs = list(own_logs) + list(shared_logs)

        if plant_id:
            logs = [lg for lg in logs if getattr(lg, "plant_id", None) == plant_id]

        if shared_only:
            logs = [lg for lg in logs if getattr(lg, "share_id", None)]

        bucket: Dict[date, List[HistoryItem]] = {}
        for lg in logs:
            dt_local = lg.done_at_utc.astimezone(tz)
            d = dt_local.date()
            if d < monday or d > sunday:
                continue

            item = HistoryItem(
                dt_local=dt_local,
                status=lg.status,
                action=lg.action,
                plant_id=getattr(lg, "plant_id", None),
                schedule_id=getattr(lg, "schedule_id", None),
                plant_name=(getattr(lg, "plant_name_at_time", None) or "(без растения)"),
                is_shared=bool(getattr(lg, "share_id", None)),  # метка «из подписки»
            )
            bucket.setdefault(d, []).append(item)

        days: List[HistoryDay] = []
        cur = monday
        while cur <= sunday:
            items = sorted(bucket.get(cur, []), key=lambda x: x.dt_local, reverse=True)
            days.append(HistoryDay(date_local=cur, items=items))
            cur += timedelta(days=1)

        return HistoryWeek(
            week_offset=week_offset,
            start_local=monday,
            end_local=sunday,
            days=days,
        )


async def show_history_root(
    target: types.Message | types.CallbackQuery,
    *,
    action: Optional[ActionType] = None,
    plant_id: Optional[int] = None,
    week_offset: int = 0,
    shared_only: bool = False,  # 👈 новый арг
):
    if isinstance(target, types.CallbackQuery):
        message = target.message
        user_id = target.from_user.id
    else:
        message = target
        user_id = target.from_user.id

    hist = await _get_history_week(
        user_tg_id=user_id,
        action=action,
        plant_id=plant_id,
        week_offset=week_offset,
        shared_only=shared_only,
    )

    header = _render_header(action, plant_id, hist.start_local, hist.end_local, shared_only=shared_only)
    body = _render_feed_text(hist)
    kb = _kb_history(
        week_offset=hist.week_offset,
        action=action,
        plant_id=plant_id,
        start=hist.start_local,
        end=hist.end_local,
        shared_only=shared_only,
    )

    text = header + "\n" + body
    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await message.answer(text, reply_markup=kb)


def _kb_history(
    *,
    week_offset: int,
    action: Optional[ActionType],
    plant_id: Optional[int],
    start: date,
    end: date,
    shared_only: bool,
):
    kb = InlineKeyboardBuilder()

    for text, code in (("💧", "w"), ("💊", "f"), ("🪴", "r"), ("👀", "all")):
        active = (ACT_TO_CODE.get(action) == code)
        mark = "✓ " if active and code != "all" else ""
        kb.button(
            text=f"{mark}{text}",
            callback_data=f"{PREFIX}:act:hist:{week_offset}:{code}:{plant_id or 0}:{int(shared_only)}",
        )
    kb.adjust(4)

    kb.row(
        types.InlineKeyboardButton(
            text=("👥 Только подписки ✓" if shared_only else "👥 Только подписки"),
            callback_data=f"{PREFIX}:shared_toggle:hist:{week_offset}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(not shared_only)}",
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            text="📌 Ближайшие",
            callback_data=f"{PREFIX}:feed:upc:0:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
        ),
        types.InlineKeyboardButton(
            text="📜 История ✓",
            callback_data=f"{PREFIX}:feed:hist:{week_offset}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
        ),
    )

    # пагинация недель
    prev_off = week_offset - 1
    has_next = week_offset < 0
    next_off = (week_offset + 1) if has_next else 0

    label = f"{start:%d.%m}–{end:%d.%m}"
    kb.row(
        types.InlineKeyboardButton(
            text="◀️",
            callback_data=f"{PREFIX}:page:hist:{prev_off}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
        ),
        types.InlineKeyboardButton(text=f"Неделя {label}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(
            text="▶️" if has_next else "⏺",
            callback_data=f"{PREFIX}:page:hist:{next_off}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
        ),
    )

    if week_offset != 0:
        kb.row(
            types.InlineKeyboardButton(
                text="🏠 К текущей неделе",
                callback_data=f"{PREFIX}:page:hist:0:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
            )
        )

    kb.row(types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"))
    return kb.as_markup()


def _render_header(
    action: Optional[ActionType],
    plant_id: Optional[int],
    start: date,
    end: date,
    *,
    shared_only: bool,
) -> str:
    act_label = {
        None: "Все действия",
        ActionType.WATERING: "Полив",
        ActionType.FERTILIZING: "Удобрения",
        ActionType.REPOTTING: "Пересадка",
    }[action]
    shared_lbl = " · 👥 Только из подписок" if shared_only else ""
    return (
        f"📅 <b>Календарь</b>\n"
        f"Фильтр: <b>{act_label}</b>{shared_lbl}\n"
        f"Раздел: <b>История</b> · Неделя <b>{start:%d.%m}–{end:%d.%m}</b>"
    )


def _render_feed_text(feed_week: HistoryWeek) -> str:
    if not getattr(feed_week, "days", None):
        return "Пока пусто."

    RU_WD = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
    lines: List[str] = []
    for day in feed_week.days:
        d = day.date_local
        wd = RU_WD[d.weekday()]
        lines.append(f"\n📅 <b>{d:%d.%m} ({wd})</b>")
        for it in day.items:
            act = ActionType.from_any(getattr(it, "action", None))
            act_emoji = ACT_TO_EMOJI.get(act, "•")

            raw_status = getattr(it, "status", ActionStatus.DONE)
            status = raw_status if isinstance(raw_status, ActionStatus) else ActionStatus.DONE
            status_emoji = STATUS_TO_EMOJI.get(status, "✅")

            t = it.dt_local.strftime("%H:%M")

            plant_lbl = it.plant_name
            pid = it.plant_id or 0
            sch = it.schedule_id or 0

            shared_mark = " 👥" if getattr(it, "is_shared", False) else ""

            lines.append(f"  {t} {status_emoji} {act_emoji}{shared_mark} {plant_lbl} (id:{pid}, sch:{sch})")
    return "\n".join(lines).lstrip()


@history_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_noop(cb: types.CallbackQuery):
    await cb.answer()


@history_router.callback_query(F.data.regexp(rf"^{PREFIX}:(feed|page|act|root|shared_toggle):hist:"))
async def on_history_callbacks(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    cmd = parts[1] if len(parts) > 1 else "noop"

    if cmd not in ("feed", "page", "act", "root", "shared_toggle"):
        return

    mode = (parts[2] if len(parts) > 2 else "upc")
    if mode != "hist":
        return

    try:
        week_offset = int(parts[3]) if len(parts) > 3 else 0
    except Exception:
        week_offset = 0

    act_code = parts[4] if len(parts) > 4 else "all"
    pid = int(parts[5]) if len(parts) > 5 else 0

    try:
        shared_only = bool(int(parts[6])) if len(parts) > 6 else False
    except Exception:
        shared_only = False

    action = ACT_MAP.get(act_code, None)
    plant_id = pid or None

    if week_offset > 0:
        week_offset = 0


    return await show_history_root(
        cb,
        action=action,
        plant_id=plant_id,
        week_offset=week_offset,
        shared_only=shared_only,
    )