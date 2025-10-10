# bot/handlers/cal_shared.py
from __future__ import annotations
from typing import Optional
from datetime import timezone

from bot.db_repo.models import ActionType, ActionStatus

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

def action_label_ru(action: Optional[ActionType]) -> str:
    return "Все действия" if action is None else action.title_ru()

def render_feed_text(feed_page, *, show_status: bool = False) -> str:
    if not getattr(feed_page, "days", None):
        return "Пока пусто."
    lines: list[str] = []
    for day in feed_page.days:
        d = day.date_local
        lines.append(f"\n📅 <b>{d:%d.%m (%a)}</b>")
        for it in day.items:
            act = ActionType.from_any(getattr(it, "action", None))
            act_emoji = ACTION_TO_EMOJI.get(act, "•")
            t = (
                it.dt_local.strftime("%H:%M")
                if getattr(it, "dt_local", None)
                else it.dt_utc.astimezone(timezone.utc).strftime("%H:%M")
                if getattr(it, "dt_utc", None)
                else "—:—"
            )
            if show_status:
                raw_status = getattr(it, "status", ActionStatus.DONE)
                status = raw_status if isinstance(raw_status, ActionStatus) else ActionStatus(str(raw_status)) if raw_status else ActionStatus.DONE
                status_emoji = STATUS_TO_EMOJI.get(status, "✅")
                lines.append(f"  {t} {status_emoji} {act_emoji} {it.plant_name} (id:{it.plant_id})")
            else:
                lines.append(f"  {t} {act_emoji} {it.plant_name} (id:{it.plant_id})")
    return "\n".join(lines).lstrip()