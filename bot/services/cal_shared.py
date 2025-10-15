# bot/handlers/cal_shared.py
from __future__ import annotations
from typing import Optional, Literal, Any
from datetime import timezone, datetime

from bot.db_repo.models import ActionType, ActionStatus, ScheduleType

CODE_TO_ACTION: dict[str, Optional[ActionType]] = {
    "all": None,
    "w": ActionType.WATERING,
    "f": ActionType.FERTILIZING,
    "r": ActionType.REPOTTING,
}
ACTION_TO_CODE: dict[Optional[ActionType], str] = {
    None: "all",
    ActionType.WATERING: "w",
    ActionType.FERTILIZING: "f",
    ActionType.REPOTTING: "r",
}
ACTION_TO_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}
STATUS_TO_EMOJI = {
    ActionStatus.DONE: "✅",
    ActionStatus.SKIPPED: "⏭️",
}

WEEK_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _as_value(x):
    return getattr(x, "value", x)


def _fmt_date_label(dt_local: datetime) -> str:
    """Возвращает дату в формате: 'Ср 09.10'."""
    dow = WEEK_RU[dt_local.weekday()]
    return f"{dow} {dt_local.day:02d}.{dt_local.month:02d}"


def _fmt_tail_for_quick_done(
    *,
    s_type: Any,
    weekly_mask: Optional[int],
    interval_days: Optional[int],
    dt_local: datetime,
) -> str:
    s_val = _as_value(s_type)
    interval_val = _as_value(ScheduleType.INTERVAL)

    if s_val == ScheduleType.INTERVAL or s_val == interval_val:
        d = int(interval_days or 0)
        return f"каждые {d} дн" if d > 0 else ""

    mask = int(weekly_mask or 0)
    if mask == 0:
        return ""
    days = [lbl for i, lbl in enumerate(WEEK_RU) if mask & (1 << i)]
    if len(days) == 1 and WEEK_RU[dt_local.weekday()] == days[0]:
        return ""
    return ",".join(days)


def _fmt_body_for_delete(
    *,
    s_type: Any,
    weekly_mask: Optional[int],
    interval_days: Optional[int],
    time_str: str,
) -> str:
    s_val = _as_value(s_type)
    interval_val = _as_value(ScheduleType.INTERVAL)

    if s_val == ScheduleType.INTERVAL or s_val == interval_val:
        d = int(interval_days or 0)
        d_txt = f"каждые {d} дн" if d > 0 else "каждые ? дн"
        return f"⏱ {d_txt} в {time_str}"

    mask = int(weekly_mask or 0)
    days = [lbl for i, lbl in enumerate(WEEK_RU) if mask & (1 << i)]
    days_txt = ",".join(days) if days else "—"
    return f"🗓 {days_txt} в {time_str}"


def format_schedule_line(
    *,
    idx: Optional[int],
    plant_name: str,
    action: Any,
    dt_local: datetime,
    s_type: Any,
    weekly_mask: Optional[int],
    interval_days: Optional[int],
    mode: Literal["delete", "quick_done"] = "quick_done",
) -> str:
    at = ActionType.from_any(action)
    emoji = at.emoji() if at else "•"
    t_str = dt_local.strftime("%H:%M")

    if mode == "quick_done":
        date_lbl = _fmt_date_label(dt_local)
        tail = _fmt_tail_for_quick_done(
            s_type=s_type,
            weekly_mask=weekly_mask,
            interval_days=interval_days,
            dt_local=dt_local,
        )
        core = f"{date_lbl} {t_str} {emoji} {plant_name}"
        line = f"{core} {tail}".rstrip()
    else:
        body = _fmt_body_for_delete(
            s_type=s_type,
            weekly_mask=weekly_mask,
            interval_days=interval_days,
            time_str=t_str,
        )
        line = f"{emoji} {plant_name} · {body}"

    if idx is not None:
        return f"{idx:>2}. {line}"
    return line

