# bot/handlers/reminder.py
from aiogram import Router
from aiogram.types import CallbackQuery

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionStatus, ActionSource
from bot.scheduler import RemindCb, plan_next_for_schedule, logger

router = Router()


@router.callback_query(RemindCb.filter())
async def on_reminder_callback(cb: CallbackQuery, callback_data: RemindCb):
    sch_id = callback_data.schedule_id
    status = ActionStatus.DONE if callback_data.action == "done" else ActionStatus.SKIPPED

    try:
        async with new_uow() as uow:
            sch = await uow.jobs.get_schedule(sch_id)
            if not sch or not sch.active:
                await cb.answer("Расписание неактивно", show_alert=False)
                if cb.message:
                    await cb.message.edit_reply_markup(reply_markup=None)
                return

            await uow.logs.create_from_schedule(
                schedule=sch,
                status=status,
                source=ActionSource.SCHEDULE,
            )
            await uow.session.commit()

        await cb.answer("Отмечено как выполнено ✅" if status == ActionStatus.DONE else "Отмечено как пропущено ⏭️")
        try:
            if cb.message:
                await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        if status == ActionStatus.DONE:
            try:
                await plan_next_for_schedule(sch_id)
            except Exception:
                logger.exception("[CB RESCHEDULE ERR] schedule_id=%s", sch_id)

    except Exception as e:
        logger.exception("[CB ERR] schedule_id=%s: %s", sch_id, e)
        await cb.answer("Ошибка, попробуйте ещё раз", show_alert=True)