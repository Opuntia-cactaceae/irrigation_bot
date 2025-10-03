# bot/handlers/plants_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.scheduler import scheduler as aps  # для снятия APS-job

plants_router = Router(name="plants_inline")

PREFIX = "plants"
PAGE_SIZE = 10


class AddPlantStates(StatesGroup):
    waiting_name = State()
    waiting_species_mode = State()
    waiting_species_text = State()


def _slice(items: list, page: int, size: int = PAGE_SIZE):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    s, e = (page - 1) * size, (page - 1) * size + size
    return items[s:e], page, pages, total


async def _get_user(user_tg_id: int):
    async with new_uow() as uow:
        return await uow.users.get_or_create(user_tg_id)


async def _get_plants_with_filter(user_id: int, species_id: int | None):
    async with new_uow() as uow:
        items = await uow.plants.list_by_user(user_id)
        if species_id:
            items = [p for p in items if getattr(p, "species_id", None) == species_id]
        return list(items)


async def _get_species(user_id: int):
    async with new_uow() as uow:
        return list(await uow.species.list_by_user(user_id))


def kb_plants_list(page: int, pages: int, species_id: int | None):
    """
    Базовая пагинация и действия списка (оставлена на случай переиспользования).
    В текущей версии список строится прямо в show_plants_list с кнопками удаления.
    """
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️", callback_data=f"{PREFIX}:page:{max(1, page - 1)}:{species_id or 0}")
    kb.button(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop")
    kb.button(text="▶️", callback_data=f"{PREFIX}:page:{min(pages, page + 1)}:{species_id or 0}")
    kb.row(types.InlineKeyboardButton(text="🧬 Фильтр по виду", callback_data=f"{PREFIX}:filter_species:{species_id or 0}"))
    kb.row(
        types.InlineKeyboardButton(text="➕ Добавить растение", callback_data=f"{PREFIX}:add"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )
    return kb.as_markup()


def kb_species_list(species, selected_id: int | None, page: int = 1, page_size: int = 10, *, for_add_flow: bool = False):
    items, page, pages, _ = _slice(species, page, page_size)
    kb = InlineKeyboardBuilder()

    if for_add_flow:
        kb.button(text="(без вида)", callback_data=f"{PREFIX}:add_pick_species:0")
        for s in items:
            kb.button(text=s.name, callback_data=f"{PREFIX}:add_pick_species:{s.id}")
    else:
        mark = "✓ " if not selected_id else ""
        kb.button(text=f"{mark}Все виды", callback_data=f"{PREFIX}:set_species:0:{page}")
        for s in items:
            mark = "✓ " if (selected_id == s.id) else ""
            kb.button(text=f"{mark}{s.name}", callback_data=f"{PREFIX}:set_species:{s.id}:{page}")

    kb.adjust(2)

    if pages > 1:
        if for_add_flow:
            kb.row(
                types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:add_species_page:{max(1, page-1)}"),
                types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
                types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:add_species_page:{min(pages, page+1)}"),
            )
        else:
            kb.row(
                types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:species_page:{max(1, page - 1)}:{selected_id or 0}"),
                types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
                types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:species_page:{min(pages, page + 1)}:{selected_id or 0}"),
            )

    if for_add_flow:
        kb.row(
            types.InlineKeyboardButton(text="✍️ Ввести свой вид", callback_data=f"{PREFIX}:species_add_text"),
            types.InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{PREFIX}:back_to_list:1"),
        )
    else:
        kb.row(
            types.InlineKeyboardButton(text="➕ Добавить вид (текстом)", callback_data=f"{PREFIX}:species_add_text"),
            types.InlineKeyboardButton(text="↩️ К списку", callback_data=f"{PREFIX}:back_to_list:1"),
        )

    return kb.as_markup()


def kb_add_species_mode():
    kb = InlineKeyboardBuilder()
    kb.button(text="🧬 Выбрать из списка", callback_data=f"{PREFIX}:species_pick_list")
    kb.button(text="✍️ Ввести свой вид", callback_data=f"{PREFIX}:species_add_text")
    kb.adjust(1)
    return kb.as_markup()


# ---------- APS job id для расписаний ----------
def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"


# ---------- Вспомогательные каскадные функции ----------
async def _cascade_summary(plant_id: int) -> dict:
    """
    Возвращает словарь:
      { 'schedules': [ids...], 'events': [ids...], 'counts': {'schedules': N, 'events': M} }
    """
    sch_ids, ev_ids = [], []
    async with new_uow() as uow:
        # расписания
        try:
            sch_list = await uow.schedules.list_by_plant(plant_id)
        except AttributeError:
            sch_list = []
        sch_ids = [getattr(s, "id", None) for s in (sch_list or []) if getattr(s, "id", None) is not None]

        # события
        try:
            ev_list = await uow.events.list_by_plant(plant_id)
            ev_ids = [getattr(e, "id", None) for e in (ev_list or []) if getattr(e, "id", None) is not None]
        except AttributeError:
            ev_ids = []

    return {
        "schedules": sch_ids,
        "events": ev_ids,
        "counts": {"schedules": len(sch_ids), "events": len(ev_ids)},
    }


async def _cascade_delete_plant(user_tg_id: int, plant_id: int) -> dict:
    """
    Удаляет всё связанное с растением:
      - снимает APS-джобы расписаний
      - удаляет/деактивирует события и расписания
      - удаляет/деактивирует само растение
    Возвращает dict с количеством удалённых сущностей.
    """
    removed = {"schedules": 0, "events": 0, "plant": 0}

    # Снимем APS-job по id расписаний заранее
    summary = await _cascade_summary(plant_id)
    for sid in summary["schedules"]:
        try:
            aps.remove_job(_job_id(sid))
        except Exception:
            pass

    async with new_uow() as uow:
        # Проверка владельца
        me = await uow.users.get_or_create(user_tg_id)
        plant = await uow.plants.get(plant_id)
        if not plant or getattr(plant, "user_id", None) != getattr(me, "id", None):
            raise PermissionError("Недоступно")

        # Удаление событий
        try:
            await uow.events.delete_by_plant(plant_id)
            removed["events"] = summary["counts"]["events"]
        except AttributeError:
            try:
                ev_list = await uow.events.list_by_plant(plant_id)
            except AttributeError:
                ev_list = []
            for e in ev_list or []:
                try:
                    await uow.events.delete(e.id)
                    removed["events"] += 1
                except AttributeError:
                    try:
                        await uow.events.update(e.id, active=False)
                        removed["events"] += 1
                    except AttributeError:
                        pass

        # Удаление расписаний
        try:
            await uow.schedules.delete_by_plant(plant_id)
            removed["schedules"] = summary["counts"]["schedules"]
        except AttributeError:
            try:
                sch_list = await uow.schedules.list_by_plant(plant_id)
            except AttributeError:
                sch_list = []
            for s in sch_list or []:
                try:
                    await uow.schedules.delete(s.id)
                    removed["schedules"] += 1
                except AttributeError:
                    try:
                        await uow.schedules.update(s.id, active=False)
                        removed["schedules"] += 1
                    except AttributeError:
                        pass

        # Удаление растения
        try:
            await uow.plants.delete(plant_id)
            removed["plant"] = 1
        except AttributeError:
            try:
                await uow.plants.update(plant_id, active=False)
                removed["plant"] = 1
            except AttributeError:
                removed["plant"] = 0

    return removed


# ---------- UI: список растений с кнопками удаления ----------
async def show_plants_list(target: types.Message | types.CallbackQuery, page: int = 1, species_id: int | None = None):
    if isinstance(target, types.CallbackQuery):
        user_id = target.from_user.id
        message = target.message
    else:
        user_id = target.from_user.id
        message = target

    user = await _get_user(user_id)
    plants = await _get_plants_with_filter(user.id, species_id)
    page_items, page, pages, total = _slice(plants, page)

    header = "🌿 <b>Растения</b>"
    sub = f"Всего: <b>{total}</b> | Вид: <b>{'Все' if not species_id else f'#{species_id}'}</b>"
    text = header + "\n" + sub + "\n\n" + "Список ваших растений."

    lines = []
    kb = InlineKeyboardBuilder()

    if page_items:
        for p in page_items:
            sp = f" · вид #{getattr(p, 'species_id', None)}" if getattr(p, "species_id", None) else ""
            lines.append(f"• {p.name}{sp} (id:{p.id})")
            # Кнопка удаления конкретного растения (с возвратом на текущие page/species)
            kb.row(
                types.InlineKeyboardButton(
                    text=f"🗑 Удалить «{p.name}»",
                    callback_data=f"{PREFIX}:del:{p.id}:{species_id or 0}:{page}"
                )
            )
    else:
        lines.append("(здесь пусто)")

    # Пагинация и общие действия
    kb.row(
        types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:page:{max(1, page - 1)}:{species_id or 0}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:page:{min(pages, page + 1)}:{species_id or 0}"),
    )
    kb.row(types.InlineKeyboardButton(text="🧬 Фильтр по виду", callback_data=f"{PREFIX}:filter_species:{species_id or 0}"))
    kb.row(
        types.InlineKeyboardButton(text="➕ Добавить растение", callback_data=f"{PREFIX}:add"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )

    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text + "\n" + "\n".join(lines), reply_markup=kb.as_markup())
        await target.answer()
    else:
        await message.answer(text + "\n" + "\n".join(lines), reply_markup=kb.as_markup())


# ---------- callbacks ----------
@plants_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_plants_callbacks(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    if action == "page":
        page = int(parts[2])
        species_id = int(parts[3]) or None
        return await show_plants_list(cb, page=page, species_id=species_id)

    if action == "filter_species":
        species_id = int(parts[2]) or None
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        text = "🧬 <b>Фильтр по видам</b>\nВыберите вид или добавьте новый."
        return await cb.message.edit_text(text, reply_markup=kb_species_list(species, species_id, page=1))

    if action == "species_page":
        page = int(parts[2])
        selected = int(parts[3]) or None
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        text = "🧬 <b>Фильтр по видам</b>\nВыберите вид или добавьте новый."
        return await cb.message.edit_text(text, reply_markup=kb_species_list(species, selected, page=page))

    if action == "set_species":
        species_id = int(parts[2]) or None
        return await show_plants_list(cb, page=1, species_id=species_id)

    if action == "add":
        await state.set_state(AddPlantStates.waiting_name)
        b = InlineKeyboardBuilder()
        b.button(text="↩️ Отмена", callback_data=f"{PREFIX}:back_to_list:1")
        await cb.message.edit_text("✍️ Введите <b>название</b> растения сообщением (свободный текст).", reply_markup=b.as_markup())
        return await cb.answer()

    if action == "species_pick_list":
        await state.set_state(AddPlantStates.waiting_species_mode)
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        await cb.message.edit_text("🧬 Выберите <b>вид</b> из списка или введите свой.", reply_markup=kb_species_list(species, selected_id=None, page=1, for_add_flow=True))
        return await cb.answer()

    if action == "add_species_page":
        page = int(parts[2])
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        await cb.message.edit_text("🧬 Выберите <b>вид</b> из списка или введите свой.", reply_markup=kb_species_list(species, selected_id=None, page=page, for_add_flow=True))
        return await cb.answer()

    if action == "add_pick_species":
        species_id = int(parts[2]) or None
        data = await state.get_data()
        plant_name = data.get("new_plant_name")
        if not plant_name:
            await state.clear()
            await cb.answer("Сначала введите имя растения", show_alert=False)
            return await show_plants_list(cb, page=1, species_id=None)

        async with new_uow() as uow:
            user = await uow.users.get_or_create(cb.from_user.id)
            try:
                await uow.plants.create(user_id=user.id, name=plant_name, species_id=species_id)
            except TypeError:
                plant = await uow.plants.create(user_id=user.id, name=plant_name)
                if species_id:
                    try:
                        await uow.plants.set_species(plant.id, species_id)
                    except AttributeError:
                        pass

        await state.clear()
        await cb.answer("Создано ✅", show_alert=False)
        return await show_plants_list(cb, page=1, species_id=None)

    if action == "species_add_text":
        await state.set_state(AddPlantStates.waiting_species_text)
        b = InlineKeyboardBuilder()
        b.button(text="↩️ Отмена", callback_data=f"{PREFIX}:back_to_list:1")
        await cb.message.edit_text("✍️ Введите название <b>вида</b> сообщением.", reply_markup=b.as_markup())
        return await cb.answer()

    if action == "back_to_list":
        await state.clear()
        page = int(parts[2]) if len(parts) > 2 else 1
        return await show_plants_list(cb, page=page, species_id=None)

    # ---------- удаление растения: подтверждение ----------
    if action == "del":
        # формат: plants:del:<plant_id>:<species_id|0>:<page>
        try:
            plant_id = int(parts[2]); species_id = int(parts[3]) or None; page = int(parts[4])
        except Exception:
            await cb.answer("Не получилось открыть удаление", show_alert=True)
            return

        # подтянем имя и проверим владельца
        async with new_uow() as uow:
            plant = await uow.plants.get(plant_id)
            if not plant:
                await cb.answer("Растение не найдено", show_alert=True)
                return await show_plants_list(cb, page=page, species_id=species_id)
            me = await uow.users.get_or_create(cb.from_user.id)
            if getattr(plant, "user_id", None) != getattr(me, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

        summary = await _cascade_summary(plant_id)
        counts = summary["counts"]
        name = getattr(plant, "name", "—")
        text = (
            f"⚠️ <b>Удалить растение «{name}»?</b>\n\n"
            "Будут удалены/отключены связанные записи:\n"
            f"• расписания: <b>{counts['schedules']}</b>\n"
            f"• события: <b>{counts['events']}</b>\n\n"
            "Действие необратимо."
        )
        kb = InlineKeyboardBuilder()
        kb.row(
            types.InlineKeyboardButton(
                text="✅ Да, удалить",
                callback_data=f"{PREFIX}:del_confirm:{plant_id}:{species_id or 0}:{page}"
            ),
            types.InlineKeyboardButton(
                text="↩️ Отмена",
                callback_data=f"{PREFIX}:page:{page}:{species_id or 0}"
            ),
        )
        await cb.message.edit_text(text, reply_markup=kb.as_markup())
        return await cb.answer()

    if action == "del_confirm":
        try:
            plant_id = int(parts[2]); species_id = int(parts[3]) or None; page = int(parts[4])
        except Exception:
            await cb.answer("Не удалось удалить", show_alert=True)
            return

        # каскад
        try:
            res = await _cascade_delete_plant(cb.from_user.id, plant_id)
        except PermissionError:
            await cb.answer("Недоступно", show_alert=True)
            return await show_plants_list(cb, page=page, species_id=species_id)
        except Exception:
            res = {"plant": 0, "schedules": 0, "events": 0}

        await cb.answer(
            f"Удалено: растения {res.get('plant',0)}, расписаний {res.get('schedules',0)}, событий {res.get('events',0)} ✅",
            show_alert=False
        )
        return await show_plants_list(cb, page=page, species_id=species_id)

    await cb.answer()


# ---------- messages (вводы) ----------
@plants_router.message(AddPlantStates.waiting_name)
async def input_plant_name(m: types.Message, state: FSMContext):
    name = (m.text or "").strip()
    if not name:
        return await m.answer("Название пустое. Введите ещё раз или нажмите «Отмена».")
    await state.update_data(new_plant_name=name)
    await state.set_state(AddPlantStates.waiting_species_mode)
    await m.answer(f"Ок, имя: <b>{name}</b>\nТеперь выберите способ указать вид:", reply_markup=kb_add_species_mode())


@plants_router.message(AddPlantStates.waiting_species_text)
async def input_species_text(m: types.Message, state: FSMContext):
    species_name = (m.text or "").strip()
    if not species_name:
        return await m.answer("Вид пустой. Введите ещё раз или нажмите «Отмена».")
    data = await state.get_data()
    plant_name = data.get("new_plant_name")
    if not plant_name:
        await state.clear()
        return await m.answer("Что-то пошло не так. Начните сначала через «Растения».")

    async with new_uow() as uow:
        user = await uow.users.get_or_create(m.from_user.id)
        sp = await uow.species.get_or_create(user_id=user.id, name=species_name)
        try:
            await uow.plants.create(user_id=user.id, name=plant_name, species_id=getattr(sp, "id", None))
        except TypeError:
            plant = await uow.plants.create(user_id=user.id, name=plant_name)
            try:
                await uow.plants.set_species(plant.id, getattr(sp, "id", None))
            except AttributeError:
                pass

    await state.clear()
    await m.answer(f"Создано: <b>{plant_name}</b> ({species_name}) ✅")
    await show_plants_list(m, page=1, species_id=None)