# bot/handlers/remind_actions.py
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionStatus, ActionSource
from bot.scheduler import RemindCb, plan_next_for_schedule

router = Router(name="remind_actions")


@router.callback_query(RemindCb.filter())
async def on_remind_action(cb: types.CallbackQuery, callback_data: RemindCb):
    """
    Логика:
    - Разрешения: владелец всегда может; подписчик — только если share разрешает.
    - Если подписчик пометил "Пропустить", владелец может позже пометить "Сделано" (override).
    - Если владелец пометил "Пропустить", больше никто не может менять статус.
    - При установке статуса обновляем ВСЕ сообщения по pending: снимаем клавиатуру и добавляем суффикс.
    """
    pending_id = int(callback_data.pending_id)
    action = callback_data.action
    status = ActionStatus.DONE if action == "done" else ActionStatus.SKIPPED
    actor_id = cb.from_user.id

    try:
        if cb.message:
            await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    except Exception:
        pass

    async with new_uow() as uow:
        pending = await uow.action_pendings.get(pending_id)
        if not pending:
            await cb.answer("Напоминание не найдено", show_alert=True)
            return

        sch = await uow.jobs.get_schedule(pending.schedule_id)
        if not sch or not getattr(sch, "active", True):
            await cb.answer("Расписание не найдено или отключено", show_alert=True)
            return

        plant = sch.plant
        owner_user_id = pending.owner_user_id
        is_owner = (owner_user_id == actor_id)

        allowed = is_owner
        source: ActionSource = ActionSource.SCHEDULE if is_owner else ActionSource.SHARED
        if not allowed:
            try:
                shares = await uow.share_links.list_links(sch.id)
            except Exception:
                shares = []
            for share in shares or []:
                if not getattr(share, "is_active", True):
                    continue
                try:
                    members = await uow.share_members.list_active_by_share(share.id)
                except Exception:
                    members = []
                for m in members:
                    if m.subscriber_user_id != actor_id:
                        continue
                    if getattr(m, "muted", False):
                        continue
                    can_complete = (
                        m.can_complete_override
                        if m.can_complete_override is not None
                        else bool(getattr(share, "allow_complete_default", False))
                    )
                    if can_complete:
                        allowed = True
                        source = ActionSource.SHARED
                        break
                if allowed:
                    break

        if getattr(pending, "resolved_status", None) == ActionStatus.DONE:
            await cb.answer("Уже отмечено ✅", show_alert=False)
            return

        if (
            getattr(pending, "resolved_status", None) == ActionStatus.SKIPPED
            and getattr(pending, "resolved_by_user_id", None) == owner_user_id
        ):
            await cb.answer("Владелец пропустил — отметить нельзя", show_alert=True)
            return

        if getattr(pending, "resolved_status", None) == ActionStatus.SKIPPED:
            if is_owner and status == ActionStatus.DONE:
                allowed = True
                source = ActionSource.SCHEDULE
            else:
                await cb.answer("Недоступно", show_alert=True)
                return

        if not allowed:
            await cb.answer("Недоступно", show_alert=True)
            return

        try:
            log = await uow.action_logs.create(
                user_id=actor_id,
                plant_id=plant.id,
                schedule_id=sch.id,
                action=sch.action,
                status=status,
                source=source,
                done_at_utc=datetime.now(timezone.utc),
                plant_name_at_time=plant.name,
                note=None,
            )
            log_id = getattr(log, "id", None)
        except Exception:
            await cb.answer("Не удалось сохранить действие", show_alert=True)
            return

        try:
            await uow.action_pendings.mark_resolved(
                pending_id=pending_id,
                status=status,
                source=source,
                by_user_id=actor_id,
                at_utc=datetime.now(timezone.utc),
                log_id=log_id,
            )
        except Exception:
            await cb.answer("Не удалось обновить напоминание", show_alert=True)
            return

        emoji = sch.action.emoji()
        title = sch.action.title_ru()
        base_text = f"{emoji} {title}: {plant.name}"
        owner_mention = (
            f"@{plant.user.tg_username}" if getattr(plant.user, "tg_username", None) else f"id{plant.user.id}"
        )
        sub_text = f"{base_text}\n\n(Уведомление из расписания пользователя {owner_mention})"
        suffix = "— отмечено ✅" if status == ActionStatus.DONE else "— пропущено ⏭️"

        try:
            msgs = await uow.action_pending_messages.list_by_pending(pending_id)
        except Exception:
            msgs = []


    for m in msgs or []:
        chat_id = getattr(m, "chat_id", None)
        message_id = getattr(m, "message_id", None)
        if not chat_id or not message_id:
            continue

        text = base_text if getattr(m, "is_owner", False) else sub_text
        new_text = f"{text}\n\n{suffix}"

        try:
            await cb.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=new_text,
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            try:
                await cb.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=new_text,
                    reply_markup=None,
                )
            except TelegramBadRequest:
                if "message is not modified" not in str(e).lower():
                    pass
            except Exception:
                pass
        except Exception:
            pass

    if status == ActionStatus.DONE:
        try:
            await plan_next_for_schedule(sch.id)
        except Exception:
            pass

    await cb.answer("Отмечено ✅" if status == ActionStatus.DONE else "Пропущено ⏭️", show_alert=False)