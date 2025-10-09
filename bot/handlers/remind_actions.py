# bot/handlers/remind_actions.py
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionStatus, ActionSource, ScheduleType
from bot.scheduler import RemindCb, plan_next_for_schedule

router = Router(name="remind_actions")


@router.callback_query(RemindCb.filter())
async def on_remind_action(cb: types.CallbackQuery, callback_data: RemindCb):

    schedule_id = int(callback_data.schedule_id)
    action = callback_data.action
    status = ActionStatus.DONE if action == "done" else ActionStatus.SKIPPED

    try:
        if cb.message:
            await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    async with new_uow() as uow:

        sch = await uow.schedules.get(schedule_id)
        if not sch or not sch.active:
            await cb.answer("Расписание не найдено или отключено", show_alert=True)
            return

        plant = await uow.plants.get_with_relations(sch.plant_id)
        if not plant:
            await cb.answer("Растение не найдено", show_alert=True)
            return
        me = await uow.users.get(cb.from_user.id)
        if not me or plant.user_id != me.id:
            await cb.answer("Недоступно", show_alert=True)
            return

        await uow.action_logs.create(
            user_id=me.id,
            plant_id=plant.id,
            schedule_id=sch.id,
            action=sch.action,
            status=status,
            source=ActionSource.SCHEDULE,
            done_at_utc=datetime.now(timezone.utc),
            plant_name_at_time=plant.name,
            note=None,
        )


    suffix = "— отмечено ✅" if status == ActionStatus.DONE else "— пропущено ⏭️"
    try:
        if cb.message:
            old_text = cb.message.text or cb.message.caption or ""
            new_text = f"{old_text}\n\n{suffix}" if suffix not in old_text else old_text
            if cb.message.text is not None:
                await cb.message.edit_text(new_text, reply_markup=None)
            else:
                await cb.message.edit_caption(new_text, reply_markup=None)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            pass
    except Exception:
        pass

    if status == ActionStatus.DONE:
        try:
            async with new_uow() as uow:
                sch = await uow.schedules.get(schedule_id)
                if sch and sch.type == ScheduleType.INTERVAL:
                    await plan_next_for_schedule(schedule_id)
        except Exception:
            pass

    await cb.answer("Отмечено ✅" if status == ActionStatus.DONE else "Пропущено ⏭️", show_alert=False)