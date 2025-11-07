# bot/handlers/remind_actions.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionStatus, ActionSource
from bot.scheduler import RemindCb, plan_next_for_schedule, logger

router = Router(name="remind_actions")


async def safe_answer(cb: types.CallbackQuery, text: str | None = None, show_alert: bool = False):
    try:
        await cb.answer(text or "", show_alert=show_alert)
    except TelegramBadRequest as e:
        s = str(e).lower()
        if "query is too old" in s or "query id is invalid" in s:
            return
        raise


async def safe_edit_text_or_caption(
    bot: types.Bot,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    keep_reply_markup: bool = False,
):
    try:
        if keep_reply_markup:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
        else:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=None)
        return
    except TelegramBadRequest as e1:
        s1 = str(e1).lower()
        if "message is not modified" in s1:
            return
        logger.exception(s1)
    except Exception:
        return

@router.callback_query(RemindCb.filter())
async def on_remind_action(cb: types.CallbackQuery, callback_data: RemindCb):
    """
    Логика:
    - Владелец всегда может; подписчик — если разрешено.
    - Если подписчик пометил "Пропустить", владелец может позже пометить "Сделано".
    - Если владелец пометил "Пропустить", больше никто не может менять статус.
    - При установке статуса обновляем все сообщения по pending: снимаем клавиатуру и добавляем суффикс.
    """

    pending_id = int(callback_data.pending_id)
    action = callback_data.action
    status = ActionStatus.DONE if action == "done" else ActionStatus.SKIPPED
    actor_id = cb.from_user.id

    logger.info("[PENDING RESOLVE] pending_id=%s | incoming cb from %s", pending_id, actor_id)

    asyncio.create_task(safe_answer(cb))

    async with new_uow() as uow:
        pending = await uow.action_pendings.get(pending_id)
        if not pending:
            await safe_answer(cb, "Напоминание не найдено", show_alert=True)
            return

        sch = await uow.jobs.get_schedule(pending.schedule_id)
        if not sch or not getattr(sch, "active", True):
            await safe_answer(cb, "Расписание не найдено или отключено", show_alert=True)
            return

        plant = sch.plant
        owner_user_id = pending.owner_user_id
        is_owner = (owner_user_id == actor_id)

        allowed = is_owner
        source: ActionSource = ActionSource.SCHEDULE if is_owner else ActionSource.SHARED
        granted_share = None
        granted_member = None

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
                    if m.subscriber_user_id != actor_id or getattr(m, "muted", False):
                        continue
                    can_complete = (
                        m.can_complete_override
                        if m.can_complete_override is not None
                        else bool(getattr(share, "allow_complete_default", False))
                    )
                    if can_complete:
                        allowed = True
                        source = ActionSource.SHARED
                        granted_share = share
                        granted_member = m
                        break
                if allowed:
                    break

        if getattr(pending, "resolved_status", None) == ActionStatus.DONE:
            await safe_answer(cb, "Уже отмечено ✅")
            return

        if (
            getattr(pending, "resolved_status", None) == ActionStatus.SKIPPED
            and getattr(pending, "resolved_by_user_id", None) == owner_user_id
        ):
            await safe_answer(cb, "Владелец пропустил — отметить нельзя", show_alert=True)
            return

        if getattr(pending, "resolved_status", None) == ActionStatus.SKIPPED:
            if is_owner and status == ActionStatus.DONE:
                allowed = True
                source = ActionSource.SCHEDULE
                granted_share = None
                granted_member = None
            else:
                await safe_answer(cb, "Недоступно", show_alert=True)
                return

        if not allowed:
            await safe_answer(cb, "Недоступно", show_alert=True)
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
                share_id=(granted_share.id if source == ActionSource.SHARED and granted_share else None),
                share_member_id=(granted_member.id if source == ActionSource.SHARED and granted_member else None),
                note=None,
            )
            log_id = getattr(log, "id", None)
        except Exception as e:
            await safe_answer(cb, "Не удалось сохранить действие", show_alert=True)
            logger.exception(e)
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
            logger.info(
                "[PENDING RESOLVE] pending_id=%s | status=%s | source=%s | by_user_id=%s | log_id=%s",
                pending_id, status, source, actor_id, log_id,
            )
        except Exception as e:
            await safe_answer(cb, "Не удалось обновить напоминание", show_alert=True)
            logger.exception(e)
            return

        emoji = sch.action.emoji()
        title = sch.action.title_ru()
        base_text = f"{emoji} {title}: {plant.name}"
        owner_mention = (
            f"@{plant.user.tg_username}" if getattr(plant.user, "tg_username", None) else f"id{plant.user.id}"
        )
        sub_text = f"{base_text}\n\n(Уведомление из расписания пользователя {owner_mention})"
        actor_is_owner = (actor_id == owner_user_id)
        actor_mention = f"@{cb.from_user.username}" if getattr(cb.from_user, "username", None) else f"id{actor_id}"
        suffix_default = "— отмечено ✅" if status == ActionStatus.DONE else "— пропущено ⏭️"
        suffix_for_owner_when_subscriber = (
            f"— отмечено подписчиком {actor_mention} ✅" if status == ActionStatus.DONE
            else f"— пропущено подписчиком {actor_mention} ⏭️"
        )
        suffix_for_others_when_subscriber = suffix_default

        try:
            msgs = await uow.action_pending_messages.list_by_pending(pending_id)
        except Exception as e:
            logger.exception(e)
            msgs = []

    tasks = []
    for m in msgs or []:
        chat_id = getattr(m, "chat_id", None)
        message_id = getattr(m, "message_id", None)
        if not chat_id or not message_id:
            continue

        is_msg_for_owner = bool(getattr(m, "is_owner", False))
        text = base_text if is_msg_for_owner else sub_text

        if actor_is_owner:
            suffix = suffix_default
            keep = False
        else:
            suffix = suffix_for_owner_when_subscriber if is_msg_for_owner else suffix_for_others_when_subscriber
            keep = is_msg_for_owner and status == ActionStatus.SKIPPED

        new_text = f"{text}\n\n{suffix}"
        tasks.append(asyncio.create_task(
            safe_edit_text_or_caption(
                cb.bot,
                chat_id=chat_id,
                message_id=message_id,
                text=new_text,
                keep_reply_markup=keep,
            )
        ))
    await asyncio.gather(*tasks, return_exceptions=True)

    if status == ActionStatus.DONE:
        try:
            await plan_next_for_schedule(sch.id)
            logger.info("[PENDING RESOLVE] next planned for schedule %s", sch.id)
        except Exception as e:
            logger.exception(e)

    await safe_answer(cb, "Отмечено ✅" if status == ActionStatus.DONE else "Пропущено ⏭️", show_alert=False)