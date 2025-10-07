# bot/handlers/remind_actions.py
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router, F, types
from aiogram.exceptions import TelegramBadRequest

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionLog, ActionStatus, ActionSource
from bot.scheduler import RemindCb

router = Router(name="remind_actions")


@router.callback_query(RemindCb.filter())
async def on_remind_action(cb: types.CallbackQuery, callback_data: RemindCb):
    """
    Обработка кнопок из напоминаний планировщика.
    Нажатия НЕ влияют на расписания и НЕ создают Event — только пишут ActionLog.
    """
    schedule_id = int(callback_data.schedule_id)
    action = callback_data.action

    async with new_uow() as uow:
        sch = await uow.jobs.get_schedule(schedule_id)
        if not sch or not sch.active:
            await cb.answer("Расписание не найдено или отключено", show_alert=True)
            return

        me = await uow.users.get(cb.from_user.id)
        if getattr(sch.plant.user, "id", None) != getattr(me, "id", None):
            await cb.answer("Недоступно", show_alert=True)
            return

        status = ActionStatus.DONE if action == "done" else ActionStatus.SKIPPED
        log = ActionLog(
            user_id=me.id,
            plant_id=sch.plant.id,
            schedule_id=sch.id,
            action=sch.action,
            status=status,
            source=ActionSource.SCHEDULE,
            done_at_utc=datetime.now(timezone.utc),
            plant_name_at_time=sch.plant.name,
            note=None,
        )
        uow.session.add(log)
        await uow.session.flush()
        await uow.session.commit()

    suffix = "— отмечено ✅" if action == "done" else "— пропущено ⏭️"
    try:
        old_text = cb.message.text or cb.message.caption or ""
        new_text = f"{old_text}\n\n{suffix}" if suffix not in old_text else old_text
        if cb.message.text is not None:
            await cb.message.edit_text(new_text, reply_markup=None)
        else:
            await cb.message.edit_caption(new_text, reply_markup=None)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        else:
            pass

    await cb.answer("Отмечено ✅" if action == "done" else "Пропущено ⏭️", show_alert=False)