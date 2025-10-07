# bot/handlers/reminder.py
from datetime import datetime
import pytz

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionStatus, ActionSource
from bot.scheduler import RemindCb, plan_next_for_schedule, logger

router = Router()

@router.callback_query(RemindCb.filter())
async def on_reminder_callback(cb: CallbackQuery, callback_data: RemindCb):
    sch_id = int(callback_data.schedule_id)
    status = ActionStatus.DONE if callback_data.action == "done" else ActionStatus.SKIPPED

    try:
        await cb.answer("Отмечаю… ✅" if status == ActionStatus.DONE else "Отмечаю… ⏭️", show_alert=False)

        try:
            if cb.message:
                await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        except Exception:
            logger.exception("[CB EDIT MARKUP ERR] schedule_id=%s", sch_id)

        async with new_uow() as uow:
            sch = await uow.schedules.get(sch_id)
            if not sch or not getattr(sch, "active", True):
                await cb.answer("Расписание неактивно", show_alert=False)
                return


            plant = await uow.plants.get(getattr(sch, "plant_id", None)) if getattr(sch, "plant_id", None) else None
            if not plant:
                await cb.answer("Растение не найдено", show_alert=True)
                return

            user = await uow.users.get(plant.user_id)

            if cb.from_user and getattr(user, "tg_user_id", None):
                if cb.from_user.id != user.tg_user_id:
                    await cb.answer("Недоступно", show_alert=True)
                    return


            await uow.action_logs.create(
                user_id=plant.user_id,
                plant_id=plant.id,
                schedule_id=sch.id,
                action=sch.action,
                status=status,
                source=ActionSource.SCHEDULE,
                done_at_utc=datetime.now(pytz.UTC),
                plant_name_at_time=plant.name,
                note=None,
            )
        try:
            await cb.answer("Готово ✅" if status == ActionStatus.DONE else "Пропущено ⏭️", show_alert=False)
        except Exception:
            pass

        try:
            await plan_next_for_schedule(sch_id)
        except Exception:
            logger.exception("[CB RESCHEDULE ERR] schedule_id=%s", sch_id)

    except Exception as e:
        logger.exception("[CB ERR] schedule_id=%s: %s", sch_id, e)
        try:
            await cb.answer("Ошибка, попробуйте ещё раз", show_alert=True)
        except Exception:
            pass