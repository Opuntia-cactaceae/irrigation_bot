# bot/handlers/settings_inline.py
from __future__ import annotations

from typing import List, Tuple

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import Schedule, Plant, User, ActionType
from bot.db_repo.schedules import SchedulesRepo
from bot.db_repo.schedule_shares import ScheduleShareRepo
from bot.db_repo.schedule_subscriptions import ScheduleSubscriptionsRepo

settings_router = Router(name="settings_inline")

PREFIX = "settings"
PAGE_SIZE = 7


# ---------- FSM ----------
class SettingsStates(StatesGroup):
    waiting_sub_code = State()


# ---------- Utils ----------
def _slice(items: list, page: int, size: int = PAGE_SIZE):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    s, e = (page - 1) * size, (page - 1) * size + size
    return items[s:e], page, pages, total


def _weekly_mask_to_text(mask: int) -> str:
    days = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
    picked = [d for i, d in enumerate(days) if (mask >> i) & 1]
    return ",".join(picked) if picked else "—"


def _action_emoji(action: ActionType | str) -> str:
    val = action if isinstance(action, str) else action.value
    return {"watering": "💧", "fertilizing": "🧪", "repotting": "🪴", "custom": "🔖"}.get(val, "🔔")


async def _get_or_create_user_by_tg(tg_id: int) -> User:
    async with new_uow() as uow:
        return await uow.users.get_or_create(tg_id)


# ---------- Public entry ----------
async def show_settings_menu(target: types.CallbackQuery | types.Message):
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🔗 Поделиться расписанием", callback_data=f"{PREFIX}:share_menu:1"))
    kb.row(types.InlineKeyboardButton(text="🧩 Подписаться по коду", callback_data=f"{PREFIX}:sub_prompt"))
    kb.row(types.InlineKeyboardButton(text="📜 Мои подписки", callback_data=f"{PREFIX}:subs_list:1"))
    kb.row(types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"))

    text = "⚙️ <b>Настройки</b>\nВыберите действие:"
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb.as_markup())


# ---------- SHARE: список моих расписаний ----------
@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_menu:")))
async def on_share_menu(cb: types.CallbackQuery):
    # settings:share_menu:<page>
    try:
        page = int(cb.data.split(":")[2])
    except Exception:
        page = 1

    tg_id = cb.from_user.id
    # собираем через репозитории:
    async with new_uow() as uow:
        me = await uow.users.get_or_create(tg_id)
        plants = await uow.plants.list_by_user(me.id)

        # плоский список активных расписаний с именами растений
        items: List[dict] = []
        for p in plants:
            sch_list = await uow.schedules.list_by_plant(p.id)
            for s in sch_list:
                if not getattr(s, "active", True):
                    continue
                items.append({
                    "schedule": s,
                    "plant": p,
                })

    if not items:
        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:menu"))
        await cb.message.edit_text("Пока нет активных расписаний.", reply_markup=kb.as_markup())
        return await cb.answer()

    page_items, page, pages, _ = _slice(items, page)

    lines = ["🫂 <b>Поделиться расписанием</b>", "Нажмите «Поделиться №…» напротив нужного пункта.", ""]
    kb = InlineKeyboardBuilder()
    for i, it in enumerate(page_items, start=1):
        s: Schedule = it["schedule"]
        p: Plant = it["plant"]
        t = s.local_time.strftime("%H:%M")
        when = (f"каждые {s.interval_days} дн в {t}" if s.type == "interval" else f"{_weekly_mask_to_text(s.weekly_mask or 0)} в {t}")
        custom = f" — {s.custom_title}" if s.action == ActionType.CUSTOM and s.custom_title else ""
        lines.append(f"{i}. {p.name}{custom} · {when} {_action_emoji(s.action)}")
        kb.row(
            types.InlineKeyboardButton(
                text=f"🔗 Поделиться №{i}", callback_data=f"{PREFIX}:share_make:{s.id}:{page}"
            )
        )

    kb.row(
        types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:share_menu:{max(1, page-1)}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:share_menu:{min(pages, page+1)}"),
    )
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:menu"))

    await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    await cb.answer()


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_make:")))
async def on_share_make(cb: types.CallbackQuery):
    # settings:share_make:<schedule_id>:<return_page>
    parts = cb.data.split(":")
    try:
        schedule_id = int(parts[2]); return_page = int(parts[3])
    except Exception:
        await cb.answer("Не удалось создать код", show_alert=True)
        return

    tg_id = cb.from_user.id
    async with new_uow() as uow:
        me = await uow.users.get_or_create(tg_id)
        # проверим, что расписание моё
        sch = await uow.schedules.get(schedule_id)
        if not sch:
            await cb.answer("Расписание не найдено", show_alert=True)
            return
        plant = await uow.plants.get(sch.plant_id)
        if not plant or plant.user_id != me.id:
            await cb.answer("Недоступно: не твоё расписание", show_alert=True)
            return

        share_repo = ScheduleShareRepo(uow.session)
        share = await share_repo.create_share(
            owner_user_id=me.id,
            schedule_id=sch.id,
            note=None,
            allow_complete_by_subscribers=True,
            expires_at_utc=None,
        )
        await uow.commit()

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="⬅️ К списку", callback_data=f"{PREFIX}:share_menu:{return_page}"))
    kb.row(types.InlineKeyboardButton(text="↩️ Настройки", callback_data=f"{PREFIX}:menu"))

    text = (
        "✅ Код создан.\n\n"
        f"<b>Код:</b> <code>{share.code}</code>\n\n"
        "Передай код — по нему можно подписаться на напоминания.\n"
        "Подписчик по умолчанию может отмечать «выполнено»."
    )
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer("Код готов")


# ---------- SUBSCRIBE: ввод кода ----------
@settings_router.callback_query(F.data == f"{PREFIX}:sub_prompt")
async def on_sub_prompt(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(SettingsStates.waiting_sub_code)
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{PREFIX}:menu"))
    await cb.message.edit_text("🧩 Введите код подписки сообщением в чат.", reply_markup=kb.as_markup())
    await cb.answer()


@settings_router.message(SettingsStates.waiting_sub_code, F.text)
async def on_subscribe_enter_code(m: types.Message, state: FSMContext):
    code = (m.text or "").strip().replace(" ", "")
    tg_id = m.from_user.id

    async with new_uow() as uow:
        me = await uow.users.get_or_create(tg_id)
        sub_repo = ScheduleSubscriptionsRepo(uow.session)
        try:
            sub = await sub_repo.subscribe_with_code(subscriber_user_id=me.id, code=code)
            await uow.commit()
        except ValueError as e:
            await m.answer(f"❌ {e}")
            return

        # рендер инфо о подписке через репозитории
        sch = await uow.schedules.get(sub.schedule_id)
        plant = await uow.plants.get(sch.plant_id) if sch else None

    if sch and plant:
        t = sch.local_time.strftime("%H:%M")
        when = (f"каждые {sch.interval_days} дн в {t}"
                if sch.type == "interval"
                else f"{_weekly_mask_to_text(sch.weekly_mask or 0)} в {t}")
        custom = f" — {sch.custom_title}" if (sch.action == ActionType.CUSTOM and sch.custom_title) else ""
        await m.answer(f"✅ Подписка оформлена:\n<b>{plant.name}</b>{custom} · {when}")
    else:
        await m.answer("✅ Подписка оформлена.")

    await state.clear()
    await show_settings_menu(m)


# ---------- SUBSCRIPTIONS: список и удаление ----------
@settings_router.callback_query(F.data.startswith(f"{PREFIX}:subs_list:")))
async def on_subs_list(cb: types.CallbackQuery):
    # settings:subs_list:<page>
    try:
        page = int(cb.data.split(":")[2])
    except Exception:
        page = 1

    tg_id = cb.from_user.id
    async with new_uow() as uow:
        me = await uow.users.get_or_create(tg_id)
        subs_repo = ScheduleSubscriptionsRepo(uow.session)
        subs = list(await subs_repo.list_by_user(me.id))

        # соберём подписи: для каждого подписки — её расписание и растение
        items: List[dict] = []
        for s in subs:
            sch = await uow.schedules.get(s.schedule_id)
            if not sch:
                continue
            plant = await uow.plants.get(sch.plant_id)
            if not plant:
                continue
            items.append({"sub": s, "sch": sch, "plant": plant})

    if not items:
        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:menu"))
        await cb.message.edit_text("У тебя нет подписок.", reply_markup=kb.as_markup())
        return await cb.answer()

    page_items, page, pages, _ = _slice(items, page)
    lines = ["📜 <b>Мои подписки</b>", "Нажмите «Удалить №…», чтобы отписаться.", ""]
    kb = InlineKeyboardBuilder()

    for i, it in enumerate(page_items, start=1):
        s = it["sch"]; p = it["plant"]; sub = it["sub"]
        t = s.local_time.strftime("%H:%M")
        when = (f"каждые {s.interval_days} дн в {t}"
                if s.type == "interval"
                else f"{_weekly_mask_to_text(s.weekly_mask or 0)} в {t}")
        custom = f" — {s.custom_title}" if (s.action == ActionType.CUSTOM and s.custom_title) else ""
        lines.append(f"{i}. {p.name}{custom} · {when}")
        kb.row(types.InlineKeyboardButton(text=f"🗑 Удалить №{i}",
                                          callback_data=f"{PREFIX}:subs_del:{sub.id}:{page}"))

    kb.row(
        types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:subs_list:{max(1, page-1)}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:subs_list:{min(pages, page+1)}"),
    )
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:menu"))

    await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    await cb.answer()


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:subs_del:")))
async def on_subs_delete(cb: types.CallbackQuery):
    # settings:subs_del:<subscription_id>:<return_page>
    parts = cb.data.split(":")
    try:
        sub_id = int(parts[2]); return_page = int(parts[3])
    except Exception:
        await cb.answer("Не удалось удалить", show_alert=True)
        return

    tg_id = cb.from_user.id
    async with new_uow() as uow:
        me = await uow.users.get_or_create(tg_id)
        # проверим, что подписка моя
        subs_repo = ScheduleSubscriptionsRepo(uow.session)
        sub = await subs_repo.get(sub_id)
        if not sub or sub.subscriber_user_id != me.id:
            await cb.answer("Недоступно", show_alert=True)
            return
        await subs_repo.delete(sub_id)
        await uow.commit()

    await cb.answer("Подписка удалена")
    # вернёмся на ту же страницу
    await on_subs_list(
        types.CallbackQuery(id=cb.id, from_user=cb.from_user, chat_instance=cb.chat_instance, message=cb.message,
                            data=f"{PREFIX}:subs_list:{return_page}")
    )


# ---------- базовые ----------
@settings_router.callback_query(F.data == f"{PREFIX}:menu")
async def on_settings_menu(cb: types.CallbackQuery):
    await show_settings_menu(cb)

@settings_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_noop(cb: types.CallbackQuery):
    await cb.answer()