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
    await state.set_state(SettingsStates.waiting_sub_code)
    await cb.message.edit_text(
        "Введите код подписки (без пробелов, регистр не важен):",
        reply_markup=kb_enter_code(),
    )
    await cb.answer()


@settings_router.callback_query(F.data == f"{PREFIX}:subs_enter_cancel")
async def on_subs_enter_code_cancel(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await on_subs_menu(cb)
    await cb.answer("Отменено")


@settings_router.message(SettingsStates.waiting_sub_code)
async def on_subs_enter_code_message(msg: types.Message, state: FSMContext):
    code = (msg.text or "").strip().replace(" ", "").upper()
    if not code:
        await msg.answer("Пустой код. Пришлите строку с кодом или нажмите «Отмена».", reply_markup=kb_enter_code())
        return

    user_id = msg.from_user.id
    async with new_uow() as uow:
        # активный линк по коду
        link = await uow.share_links.get_by_code_active(code, now_utc=datetime.now(timezone.utc))
        if not link:
            await msg.answer("Код не найден, истёк или исчерпан. Проверьте правильность и попробуйте ещё раз.", reply_markup=kb_enter_code())
            return

        # уже есть членство?
        member = await uow.share_members.find(share_id=link.id, subscriber_user_id=user_id)

        if member and member.status == ShareMemberStatus.ACTIVE:
            await state.clear()
            await msg.answer("Вы уже подписаны по этому коду. Открою список подписок…")
            fake_cb = types.CallbackQuery(id="0", from_user=msg.from_user, chat_instance="", message=msg, data=f"{PREFIX}:subs_list:1")
            await on_subs_list(fake_cb)
            return

        # создаём/активируем
        if member:
            await uow.share_members.set_status(member_id=member.id, status=ShareMemberStatus.ACTIVE)
        else:
            await uow.share_members.create(share_id=link.id, subscriber_user_id=user_id)

        # учитываем использование кода (max_uses)
        await uow.share_links.increment_uses(link.id)
        await uow.commit()

    await state.clear()
    await msg.answer("✅ Подписка оформлена! Покажу ваши подписки…")
    fake_cb = types.CallbackQuery(id="0", from_user=msg.from_user, chat_instance="", message=msg, data=f"{PREFIX}:subs_list:1")
    await on_subs_list(fake_cb)


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


# ---------- Просмотр конкретной подписки ----------
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