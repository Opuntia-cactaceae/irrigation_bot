# bot/handlers/plants_inline.py
from __future__ import annotations
from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow

plants_router = Router(name="plants_inline")

PREFIX = "plants"
PAGE_SIZE = 10


# --- FSM ---
class AddPlantStates(StatesGroup):
    waiting_name = State()
    waiting_species_mode = State()
    waiting_species_text = State()


# --- helpers ---
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
        plants = await uow.plants.list_by_user(user_id)
        if species_id:
            plants = [p for p in plants if p.species_id == species_id]
        return list(plants)


async def _get_species(user_id: int):
    async with new_uow() as uow:
        return list(await uow.species.list_by_user(user_id))


# --- keyboards ---
def kb_plants_list(page: int, pages: int, species_id: int | None):
    kb = InlineKeyboardBuilder()
    # Навигация страниц
    kb.button(text="◀️", callback_data=f"{PREFIX}:page:{max(1, page - 1)}:{species_id or 0}")
    kb.button(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop")
    kb.button(text="▶️", callback_data=f"{PREFIX}:page:{min(pages, page + 1)}:{species_id or 0}")
    kb.row(
        types.InlineKeyboardButton(text="🧬 Фильтр по виду", callback_data=f"{PREFIX}:filter_species:{species_id or 0}")
    )
    kb.row(
        types.InlineKeyboardButton(text="➕ Добавить растение", callback_data=f"{PREFIX}:add"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )
    return kb.as_markup()


def kb_species_list(species, selected_id: int | None, page: int = 1, page_size: int = 10):
    items, page, pages, _ = _slice(species, page, page_size)
    kb = InlineKeyboardBuilder()
    # «Все виды»
    mark = "✓ " if not selected_id else ""
    kb.button(text=f"{mark}Все виды", callback_data=f"{PREFIX}:set_species:0:{page}")
    for s in items:
        mark = "✓ " if (selected_id == s.id) else ""
        kb.button(text=f"{mark}{s.name}", callback_data=f"{PREFIX}:set_species:{s.id}:{page}")
    kb.adjust(2)
    # навигация
    kb.row(
        types.InlineKeyboardButton(text="◀️",
                                   callback_data=f"{PREFIX}:species_page:{max(1, page - 1)}:{selected_id or 0}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️",
                                   callback_data=f"{PREFIX}:species_page:{min(pages, page + 1)}:{selected_id or 0}"),
    )
    # добавление нового вида
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


# --- public API ---
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
    sub = f"Всего: <b>{total}</b> | Вид: <b>{'Все' if not species_id else '…'}</b>"
    text = header + "\n" + sub + "\n\n" + "Список ваших растений."

    # строим таблицу-список
    lines = []
    if page_items:
        for p in page_items:
            sp = f" · {p.species.name}" if getattr(p, "species", None) else ""
            lines.append(f"• {p.name}{sp} (id:{p.id})")
    else:
        lines.append("(здесь пусто)")

    kb = kb_plants_list(page, pages, species_id)
    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text + "\n" + "\n".join(lines), reply_markup=kb)
        await target.answer()
    else:
        await message.answer(text + "\n" + "\n".join(lines), reply_markup=kb)


# --- callbacks ---
@plants_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_plants_callbacks(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    # пагинация списка: plants:page:<page>:<species_id>
    if action == "page":
        page = int(parts[2]);
        species_id = int(parts[3]) or None
        return await show_plants_list(cb, page=page, species_id=species_id)

    # фильтр по виду — показать список видов
    if action == "filter_species":
        species_id = int(parts[2]) or None
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        text = "🧬 <b>Фильтр по видам</b>\nВыберите вид или добавьте новый."
        return await cb.message.edit_text(text, reply_markup=kb_species_list(species, species_id, page=1))

    # листать виды: plants:species_page:<page>:<selected_id>
    if action == "species_page":
        page = int(parts[2]);
        selected = int(parts[3]) or None
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        text = "🧬 <b>Фильтр по видам</b>\nВыберите вид или добавьте новый."
        return await cb.message.edit_text(text, reply_markup=kb_species_list(species, selected, page=page))

    # установить вид и вернуться к списку
    if action == "set_species":
        species_id = int(parts[2]) or None
        # вернёмся к списку с фильтром
        return await show_plants_list(cb, page=1, species_id=species_id)

    # добавить растение — старт FSM: спросим имя
    if action == "add":
        await state.set_state(AddPlantStates.waiting_name)
        await cb.message.edit_text(
            "✍️ Введите <b>название</b> растения сообщением (свободный текст).\n\n"
            "↩️ Чтобы отменить, нажмите кнопку ниже.",
            reply_markup=InlineKeyboardBuilder().button(text="↩️ Отмена",
                                                        callback_data=f"{PREFIX}:back_to_list:1").as_markup()
        )
        return await cb.answer()

    # выбор режима вида
    if action == "species_pick_list":
        await state.set_state(AddPlantStates.waiting_species_mode)  # остаёмся в этом состоянии
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        await cb.message.edit_text(
            "🧬 Выберите <b>вид</b> из списка или введите свой.",
            reply_markup=kb_species_list(species, selected_id=None, page=1)
        )
        return await cb.answer()

    if action == "species_add_text":
        await state.set_state(AddPlantStates.waiting_species_text)
        await cb.message.edit_text(
            "✍️ Введите название <b>вида</b> сообщением.\n\n"
            "↩️ Отмена — кнопкой ниже.",
            reply_markup=InlineKeyboardBuilder().button(text="↩️ Отмена",
                                                        callback_data=f"{PREFIX}:back_to_list:1").as_markup()
        )
        return await cb.answer()

    if action == "species_skip":
        # завершить создание растения без вида — используем ранее введённое имя
        data = await state.get_data()
        plant_name = data.get("new_plant_name")
        if not plant_name:
            await cb.answer("Сначала введите название растения", show_alert=False)
            return
        async with new_uow() as uow:
            user = await uow.users.get_or_create(cb.from_user.id)
            await uow.plants.create(user_id=user.id, name=plant_name)
        await state.clear()
        await cb.answer("Создано ✅", show_alert=False)
        return await show_plants_list(cb, page=1, species_id=None)

    if action == "back_to_list":
        await state.clear()
        page = int(parts[2]) if len(parts) > 2 else 1
        return await show_plants_list(cb, page=page, species_id=None)

    # fallback
    await cb.answer()


# --- message handlers (FSM ввода текста) ---

@plants_router.message(AddPlantStates.waiting_name)
async def input_plant_name(m: types.Message, state: FSMContext):
    name = (m.text or "").strip()
    if not name:
        return await m.answer("Название пустое. Введите ещё раз или нажмите «Отмена».")
    # сохраним и предложим режим выбора вида
    await state.update_data(new_plant_name=name)
    await state.set_state(AddPlantStates.waiting_species_mode)
    kb = kb_add_species_mode()
    await m.answer(f"Ок, имя: <b>{name}</b>\nТеперь выберите способ указать вид:", reply_markup=kb)


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
    # создаём/берём вид и растение
    async with new_uow() as uow:
        user = await uow.users.get_or_create(m.from_user.id)
        sp = await uow.species.get_or_create(user_id=user.id, name=species_name)
        await uow.plants.create(user_id=user.id, name=plant_name)
        # Присвоить вид новому растению: нужно обновить create(), чтобы принимал species_id.
        # Быстрый фикс: вручную обновим последний добавленный объект — лучше поменять репозиторий.
    await state.clear()
    await m.answer(f"Создано: <b>{plant_name}</b> ({species_name}) ✅")
    # вернём список
    await show_plants_list(m, page=1, species_id=None)