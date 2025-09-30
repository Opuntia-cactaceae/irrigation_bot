# bot/handlers/schedule_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import time

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType, ScheduleType
from bot.scheduler import plan_next_for_schedule

router = Router(name="schedule_inline")

PREFIX = "sch"
PAGE_SIZE = 8
WEEK_EMOJI = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class SchStates(StatesGroup):
    choosing_plant = State()
    choosing_action = State()
    choosing_kind = State()
    editing_interval = State()
    editing_weekly = State()


def _slice(items, page: int, size: int):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    return items[(page - 1) * size:(page - 1) * size + size], page, pages, total


def _action_from_code(code: str) -> ActionType:
    return {"w": ActionType.WATERING, "f": ActionType.FERTILIZING, "r": ActionType.REPOTTING}[code]


def _action_to_code(a: ActionType) -> str:
    return {"watering": "w", "fertilizing": "f", "repotting": "r"}[a.value]


async def show_schedule_wizard(target: types.Message | types.CallbackQuery, state: FSMContext, page: int = 1):
    if isinstance(target, types.CallbackQuery):
        message = target.message
        tg_id = target.from_user.id
    else:
        message = target
        tg_id = target.from_user.id

    await state.clear()
    await state.set_state(SchStates.choosing_plant)

    async with new_uow() as uow:
        user = await uow.users.get_or_create(tg_id)
        try:
            plants = await uow.plants.list_by_user(user.id)
        except AttributeError:
            plants = []

    page_items, page, pages, total = _slice(plants, page, PAGE_SIZE)

    text = "🗓️ <b>Мастер расписаний</b>\nШаг 1/5: выберите растение."
    kb = InlineKeyboardBuilder()
    if page_items:
        for p in page_items:
            kb.button(text=f"🌿 {p.name}", callback_data=f"{PREFIX}:pick_plant:{p.id}:{page}")
        kb.adjust(1)
    else:
        kb.button(text="(список пуст)", callback_data=f"{PREFIX}:noop")
        kb.adjust(1)

    kb.row(
        types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:page:{max(1, page - 1)}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:page:{min(pages, page + 1)}"),
    )
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data="cal:feed:upc:1:all:0"))

    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        await message.answer(text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_schedule_callbacks(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    if action == "page":
        page = int(parts[2])
        return await show_schedule_wizard(cb, state, page=page)

    if action == "pick_plant":
        plant_id = int(parts[2])
        async with new_uow() as uow:
            plant = await uow.plants.get(plant_id)
            if not plant:
                await cb.answer("Растение не найдено", show_alert=True)
                return
            me = await uow.users.get_or_create(cb.from_user.id)
            if getattr(plant, "user_id", None) != getattr(me, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

        await state.update_data(plant_id=plant_id)
        await state.set_state(SchStates.choosing_action)
        return await _screen_choose_action(cb)

    if action == "set_action":
        a_code = parts[2]
        act = _action_from_code(a_code)
        await state.update_data(action=act.value)
        await state.set_state(SchStates.choosing_kind)
        return await _screen_choose_kind(cb)

    if action == "kind_interval":
        await state.update_data(kind="interval", interval_days=3, hh=9, mm=0)
        await state.set_state(SchStates.editing_interval)
        return await _screen_edit_interval(cb, state)

    if action == "kind_weekly":
        await state.update_data(kind="weekly", weekly_mask=0, hh=9, mm=0)
        await state.set_state(SchStates.editing_weekly)
        return await _screen_edit_weekly(cb, state)

    if action == "ival_inc":
        delta = int(parts[2])
        data = await state.get_data()
        days = max(1, min(365, int(data.get("interval_days", 3)) + delta))
        await state.update_data(interval_days=days)
        return await _screen_edit_interval(cb, state)

    if action == "time_h":
        delta = int(parts[2])
        data = await state.get_data()
        hh = (int(data.get("hh", 9)) + delta) % 24
        await state.update_data(hh=hh)
        st = await state.get_state()
        return await (_screen_edit_interval if st == SchStates.editing_interval.state else _screen_edit_weekly)(cb, state)

    if action == "time_m":
        delta = int(parts[2])
        data = await state.get_data()
        mm = (int(data.get("mm", 0)) + delta) % 60
        await state.update_data(mm=mm)
        st = await state.get_state()
        return await (_screen_edit_interval if st == SchStates.editing_interval.state else _screen_edit_weekly)(cb, state)

    if action == "weekly_toggle":
        idx = int(parts[2])
        data = await state.get_data()
        mask = int(data.get("weekly_mask", 0))
        bit = 1 << idx
        new_mask = (mask ^ bit)
        if new_mask == 0:
            await cb.answer("Должен быть выбран хотя бы один день", show_alert=False)
        else:
            await state.update_data(weekly_mask=new_mask)
        return await _screen_edit_weekly(cb, state)

    if action == "save":
        data = await state.get_data()
        try:
            plant_id = int(data["plant_id"])
            act = ActionType(data["action"])
            kind = data["kind"]
            hh = int(data["hh"]); mm = int(data["mm"])
        except Exception:
            await cb.answer("Не хватает данных", show_alert=True)
            return

        local_t = time(hour=hh, minute=mm)

        async with new_uow() as uow:
            # повторная проверка владельца без UsersRepo.get_by_id
            plant = await uow.plants.get(plant_id)
            if not plant:
                await cb.answer("Растение не найдено", show_alert=True)
                return

            me = await uow.users.get_or_create(cb.from_user.id)
            if getattr(plant, "user_id", None) != getattr(me, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

            sch = None
            if kind == "interval":
                interval_days = int(data["interval_days"])
                # сначала пробуем upsert_interval
                try:
                    sch = await uow.schedules.upsert_interval(
                        plant_id=plant_id, action=act,
                        interval_days=interval_days, local_time=local_t
                    )
                except AttributeError:
                    # совместимость: удалить старые и создать новое
                    try:
                        old_list = await uow.schedules.list_by_plant_action(plant_id, act)
                        for s in old_list:
                            await uow.schedules.delete(s.id)
                    except AttributeError:
                        pass
                    sch = await uow.schedules.create(
                        plant_id=plant_id, action=act,
                        type=ScheduleType.INTERVAL,
                        interval_days=interval_days,
                        local_time=local_t, active=True
                    )
            else:
                weekly_mask = int(data["weekly_mask"])
                try:
                    sch = await uow.schedules.upsert_weekly(
                        plant_id=plant_id, action=act,
                        weekly_mask=weekly_mask, local_time=local_t
                    )
                except AttributeError:
                    try:
                        old_list = await uow.schedules.list_by_plant_action(plant_id, act)
                        for s in old_list:
                            await uow.schedules.delete(s.id)
                    except AttributeError:
                        pass
                    sch = await uow.schedules.create(
                        plant_id=plant_id, action=act,
                        type=ScheduleType.WEEKLY,
                        weekly_mask=weekly_mask,
                        local_time=local_t, active=True
                    )

        # планирование вне UoW
        try:
            if sch and getattr(sch, "id", None) is not None:
                await plan_next_for_schedule(sch.id)
        except Exception:
            # не критично — пользователь всё равно сохранил расписание
            pass

        await state.clear()
        await cb.answer("Сохранено ✅", show_alert=False)
        act_code = _action_to_code(act)
        return await cb.message.edit_text(
            "✅ Расписание сохранено.\nВернитесь в календарь для просмотра.",
            reply_markup=InlineKeyboardBuilder()
                .row(
                    types.InlineKeyboardButton(text="📅 В календарь", callback_data=f"cal:feed:upc:1:{act_code}:0"),
                    types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
                ).as_markup()
        )

    if action == "cancel":
        await state.clear()
        await cb.answer()
        return await cb.message.edit_text(
            "Отменено.",
            reply_markup=InlineKeyboardBuilder()
                .row(
                    types.InlineKeyboardButton(text="📅 К календарю", callback_data="cal:feed:upc:1:all:0"),
                    types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
                ).as_markup()
        )

    await cb.answer()


async def _screen_choose_action(cb: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="💧 Полив",     callback_data=f"{PREFIX}:set_action:w")
    kb.button(text="💊 Удобрения", callback_data=f"{PREFIX}:set_action:f")
    kb.button(text="🪴 Пересадка", callback_data=f"{PREFIX}:set_action:r")
    kb.adjust(1)
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:page:1"))
    await cb.message.edit_text("Шаг 2/5: выберите <b>тип действия</b>.", reply_markup=kb.as_markup())
    await cb.answer()


async def _screen_choose_kind(cb: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="⏱ Каждые N дней", callback_data=f"{PREFIX}:kind_interval")
    kb.button(text="🗓 По дням недели", callback_data=f"{PREFIX}:kind_weekly")
    kb.adjust(1)
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:page:1"))
    await cb.message.edit_text("Шаг 3/5: выберите <b>тип расписания</b>.", reply_markup=kb.as_markup())
    await cb.answer()


async def _screen_edit_interval(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    days = int(data.get("interval_days", 3))
    hh = int(data.get("hh", 9))
    mm = int(data.get("mm", 0))

    text = (
        "Шаг 4/5: настройка интервала\n"
        f"• Каждый: <b>{days}</b> дн.\n"
        f"• Время: <b>{hh:02d}:{mm:02d}</b>"
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(text="− день", callback_data=f"{PREFIX}:ival_inc:-1"),
        types.InlineKeyboardButton(text="+ день", callback_data=f"{PREFIX}:ival_inc:1"),
    )
    kb.row(
        types.InlineKeyboardButton(text="Часы −", callback_data=f"{PREFIX}:time_h:-1"),
        types.InlineKeyboardButton(text="Часы +", callback_data=f"{PREFIX}:time_h:1"),
    )
    kb.row(
        types.InlineKeyboardButton(text="Минуты −5", callback_data=f"{PREFIX}:time_m:-5"),
        types.InlineKeyboardButton(text="Минуты +5", callback_data=f"{PREFIX}:time_m:5"),
    )
    kb.row(
        types.InlineKeyboardButton(text="✅ Сохранить", callback_data=f"{PREFIX}:save"),
        types.InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{PREFIX}:cancel"),
    )

    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


async def _screen_edit_weekly(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mask = int(data.get("weekly_mask", 0))
    hh = int(data.get("hh", 9))
    mm = int(data.get("mm", 0))

    text = (
        "Шаг 4/5: дни недели и время\n"
        "• Отмечайте нужные дни ниже.\n"
        f"• Время: <b>{hh:02d}:{mm:02d}</b>"
    )

    kb = InlineKeyboardBuilder()
    row = []
    for i, lbl in enumerate(WEEK_EMOJI):
        checked = bool(mask & (1 << i))
        mark = "✓ " if checked else ""
        row.append(types.InlineKeyboardButton(text=f"{mark}{lbl}", callback_data=f"{PREFIX}:weekly_toggle:{i}"))
        if (i % 4 == 3) or i == 6:
            kb.row(*row); row = []
    kb.row(
        types.InlineKeyboardButton(text="Часы −", callback_data=f"{PREFIX}:time_h:-1"),
        types.InlineKeyboardButton(text="Часы +", callback_data=f"{PREFIX}:time_h:1"),
    )
    kb.row(
        types.InlineKeyboardButton(text="Минуты −5", callback_data=f"{PREFIX}:time_m:-5"),
        types.InlineKeyboardButton(text="Минуты +5", callback_data=f"{PREFIX}:time_m:5"),
    )
    kb.row(
        types.InlineKeyboardButton(text="✅ Сохранить", callback_data=f"{PREFIX}:save"),
        types.InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{PREFIX}:cancel"),
    )

    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()