# bot/handlers/settings_subscriptions.py
from __future__ import annotations

from typing import List
from datetime import datetime, timezone

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ShareMemberStatus

settings_router = Router(name="settings_subscriptions")

PREFIX = "settings"
PAGE_SIZE = 7


# ---------- Keyboards ----------
def kb_settings_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔗 Ввести код", callback_data=f"{PREFIX}:subs_enter_code")
    kb.button(text="📋 Мои подписки", callback_data=f"{PREFIX}:subs_list:1")
    kb.button(text="🗓 Календарь подписок", callback_data="settings:subs_cal")
    kb.button(text="↩️ Назад", callback_data=f"{PREFIX}:menu")
    kb.adjust(1)
    return kb.as_markup()


def kb_enter_code():
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Отмена", callback_data=f"{PREFIX}:subs_enter_cancel")
    kb.adjust(1)
    return kb.as_markup()


def _slice(items: list, page: int, size: int = PAGE_SIZE):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    s, e = (page - 1) * size, (page - 1) * size + size
    return items[s:e], page, pages, total


def kb_subs_list_page(member_ids: List[int], page: int, pages: int):
    kb = InlineKeyboardBuilder()
    for mid in member_ids:
        kb.row(
            types.InlineKeyboardButton(
                text=f"📌 Подписка #{mid}",
                callback_data=f"{PREFIX}:subs_item:{mid}:{page}",
            )
        )
    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:subs_list:{page - 1}"))
    nav.append(types.InlineKeyboardButton(text=f"{page}/{pages}", callback_data=f"{PREFIX}:noop"))
    if page < pages:
        nav.append(types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:subs_list:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(types.InlineKeyboardButton(text="↩️ Настройки", callback_data=f"{PREFIX}:menu"))
    return kb.as_markup()


def kb_sub_item(member_id: int, return_page: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(
            text="❌ Отписаться",
            callback_data=f"{PREFIX}:subs_unsub_confirm:{member_id}:{return_page}",
        )
    )
    kb.row(types.InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{PREFIX}:subs_list:{return_page}"))
    kb.row(types.InlineKeyboardButton(text="↩️ Настройки", callback_data=f"{PREFIX}:menu"))
    return kb.as_markup()


def kb_unsub_confirm(member_id: int, return_page: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(
            text="✅ Да, отписаться",
            callback_data=f"{PREFIX}:subs_unsub:{member_id}:{return_page}",
        )
    )
    kb.row(types.InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"{PREFIX}:subs_item:{member_id}:{return_page}"))
    return kb.as_markup()


# ---------- Главное меню ----------
@settings_router.callback_query(F.data == f"{PREFIX}:subs")
async def on_subs_menu(cb: types.CallbackQuery):
    text = (
        "📬 <b>Подписки</b>\n\n"
        "— Введите код, чтобы подписаться на чужое расписание\n"
        "— Просмотрите или удалите существующие подписки"
    )
    await cb.message.edit_text(text, reply_markup=kb_settings_menu())
    await cb.answer()


# ---------- Ввод кода ----------
try:
    from bot.handlers.settings_inline import SettingsStates  # type: ignore
except Exception:
    from aiogram.fsm.state import StatesGroup, State
    class SettingsStates(StatesGroup):
        waiting_sub_code = State()


@settings_router.callback_query(F.data == f"{PREFIX}:subs_enter_code")
async def on_subs_enter_code_start(cb: types.CallbackQuery, state: FSMContext):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await state.set_state(SettingsStates.waiting_sub_code)
    prompt = await cb.message.answer(
        "Введите код подписки (без пробелов, регистр не важен):",
        reply_markup=kb_enter_code(),
    )
    await state.update_data(prompt_msg_id=prompt.message_id, prompt_chat_id=prompt.chat.id)
    await cb.answer()


@settings_router.callback_query(F.data == f"{PREFIX}:subs_enter_cancel")
async def on_subs_enter_code_cancel(cb: types.CallbackQuery, state: FSMContext):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await state.clear()
    await cb.message.answer("Отменено. Возвращаю в настройки.", reply_markup=kb_settings_menu())
    await cb.answer()


@settings_router.message(SettingsStates.waiting_sub_code)
async def on_subs_enter_code_message(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    prompt_chat_id = data.get("prompt_chat_id") or msg.chat.id

    code = (msg.text or "").strip().replace(" ", "").upper()
    if not code:
        await msg.answer("Пустой код. Пришлите строку с кодом или нажмите «Отмена».")
        return

    user_id = msg.from_user.id
    ok = False
    already = False
    err_text = None

    try:
        async with new_uow() as uow:
            link = await uow.share_links.get_by_code_active(code, now_utc=datetime.now(timezone.utc))
            if not link:
                err_text = "❌ Код не найден, истёк или исчерпан."
            else:
                member = await uow.share_members.find(share_id=link.id, subscriber_user_id=user_id)
                if member and member.status == ShareMemberStatus.ACTIVE:
                    already = True
                else:
                    if member:
                        await uow.share_members.set_status(member_id=member.id, status=ShareMemberStatus.ACTIVE)
                    else:
                        await uow.share_members.create(share_id=link.id, subscriber_user_id=user_id)

                    await uow.share_links.increment_uses(link.id)
                    await uow.commit()
                    ok = True
    except Exception:
        err_text = "⚠️ Не удалось обработать код. Попробуйте ещё раз."

    try:
        if prompt_msg_id:
            if ok:
                new_text = "✅ Подписка оформлена!"
            elif already:
                new_text = "ℹ️ Вы уже подписаны по этому коду."
            else:
                new_text = err_text or "❌ Код отклонён."

            await msg.bot.edit_message_text(
                chat_id=prompt_chat_id,
                message_id=prompt_msg_id,
                text=new_text,
            )
    except Exception:
        pass
    finally:
        try:
            if prompt_msg_id:
                await msg.bot.edit_message_reply_markup(
                    chat_id=prompt_chat_id,
                    message_id=prompt_msg_id,
                    reply_markup=None,
                )
        except Exception:
            pass

    await state.clear()

    if ok:
        await msg.answer("Готово! Открою меню настроек.", reply_markup=kb_settings_menu())
    elif already:
        await msg.answer("Вы уже подписаны. Открою меню настроек.", reply_markup=kb_settings_menu())
    else:
        await msg.answer((err_text or "Не получилось.") + "\n\nВозвращаю в меню настроек.", reply_markup=kb_settings_menu())


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:subs_list:"))
async def on_subs_list(cb: types.CallbackQuery):
    try:
        page = int(cb.data.split(":")[-1])
    except Exception:
        page = 1

    user_id = cb.from_user.id
    async with new_uow() as uow:
        members = await uow.share_members.list_by_user(user_id)

    items, page, pages, total = _slice(list(members), page, PAGE_SIZE)
    if total == 0:
        await cb.message.edit_text(
            "У вас пока нет подписок.\n\nВы можете ввести код подписки.",
            reply_markup=kb_settings_menu(),
        )
        await cb.answer()
        return

    # подтягиваем заголовки ссылок
    async with new_uow() as uow:
        titles: List[str] = []
        for m in items:
            share = await uow.share_links.get(m.share_id)
            title = getattr(share, "title", None) or f"Подписка #{m.id}"
            status = getattr(m, "status", "UNKNOWN")
            titles.append(f"• <b>{title}</b> — {status}")

    text = "📋 <b>Мои подписки</b>:\n\n" + "\n".join(titles)
    await cb.message.edit_text(text, reply_markup=kb_subs_list_page([m.id for m in items], page, pages))
    await cb.answer()


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:subs_item:"))
async def on_subs_item(cb: types.CallbackQuery):
    _, _, _, member_id_str, return_page_str = cb.data.split(":")
    member_id = int(member_id_str)
    return_page = int(return_page_str)

    async with new_uow() as uow:
        m = await uow.share_members.get_with_relations(member_id)
        if not m:
            await cb.answer("Подписка не найдена", show_alert=True)
            cb2 = types.CallbackQuery(id=cb.id, from_user=cb.from_user, chat_instance=cb.chat_instance, message=cb.message, data=f"{PREFIX}:subs_list:{return_page}")
            await on_subs_list(cb2)
            return

        share = m.share
        title = getattr(share, "title", None) or "Без названия"
        allow = "отмечать можно" if share.allow_complete_default else "отмечать нельзя"
        hist = "история видна" if share.show_history_default else "история скрыта"

    text = (
        f"<b>{title}</b>\n"
        f"Права: {allow}, {hist}\n\n"
        "Вы можете отписаться от этой подписки."
    )
    await cb.message.edit_text(text, reply_markup=kb_sub_item(m.id, return_page))
    await cb.answer()


# ---------- Подтверждение отписки ----------
@settings_router.callback_query(F.data.startswith(f"{PREFIX}:subs_unsub_confirm:"))
async def on_subs_unsub_confirm(cb: types.CallbackQuery):
    _, _, _, member_id_str, return_page_str = cb.data.split(":")
    member_id = int(member_id_str)
    return_page = int(return_page_str)

    async with new_uow() as uow:
        m = await uow.share_members.get_with_relations(member_id)
        if not m:
            await cb.answer("Подписка не найдена", show_alert=True)
            return

        share = m.share
        title = getattr(share, "title", None) or "Без названия"

    await cb.message.edit_text(f"Точно отписаться от «{title}»?", reply_markup=kb_unsub_confirm(member_id, return_page))
    await cb.answer()


# ---------- Отписка ----------
@settings_router.callback_query(F.data.startswith(f"{PREFIX}:subs_unsub:"))
async def on_subs_unsub(cb: types.CallbackQuery):
    _, _, _, member_id_str, return_page_str = cb.data.split(":")
    member_id = int(member_id_str)
    return_page = int(return_page_str)

    async with new_uow() as uow:
        m = await uow.share_members.get(member_id)
        if not m:
            await cb.answer("Подписка не найдена", show_alert=True)
            return

        await uow.share_members.set_status(member_id=m.id, status=ShareMemberStatus.REMOVED)
        await uow.commit()

    await cb.answer("Подписка удалена")
    cb2 = types.CallbackQuery(id=cb.id, from_user=cb.from_user, chat_instance=cb.chat_instance, message=cb.message, data=f"{PREFIX}:subs_list:{return_page}")
    await on_subs_list(cb2)


# ---------- No-op ----------
@settings_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_noop(cb: types.CallbackQuery):
    await cb.answer()