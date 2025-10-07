# bot/handlers/plants_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.scheduler import scheduler as aps

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


async def _cascade_summary(plant_id: int) -> dict:
    sch_ids, log_ids = [], []
    async with new_uow() as uow:
        try:
            sch_list = await uow.schedules.list_by_plant(plant_id)
        except AttributeError:
            sch_list = []
        sch_ids = [getattr(s, "id", None) for s in (sch_list or []) if getattr(s, "id", None) is not None]

        try:
            logs_list = await uow.action_logs.list_by_plant(plant_id)
            log_ids = [getattr(a, "id", None) for a in (logs_list or []) if getattr(a, "id", None) is not None]
        except AttributeError:
            log_ids = []

    return {
        "schedules": sch_ids,
        "logs": log_ids,
        "counts": {"schedules": len(sch_ids), "logs": len(log_ids)},
    }


async def _cascade_delete_plant(user_tg_id: int, plant_id: int) -> dict:
    removed = {"schedules": 0, "plant": 0, "logs": 0}

    summary = await _cascade_summary(plant_id)
    for sid in summary["schedules"]:
        try:
            aps.remove_job(_job_id(sid))
        except Exception:
            pass

    async with new_uow() as uow:
        me = await uow.users.get_by_tg_id(user_tg_id)
        plant = await uow.plants.get(plant_id)
        if not plant or getattr(plant, "user_id", None) != getattr(me, "id", None):
            raise PermissionError("Недоступно")

        try:
            logs_list = await uow.action_logs.list_by_plant(plant_id)
        except AttributeError:
            logs_list = []
        for a in logs_list or []:
            try:
                await uow.action_logs.delete(a.id)
                removed["logs"] += 1
            except Exception:
                pass

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
                except Exception:
                    pass

        try:
            await uow.plants.delete(plant_id)
            removed["plant"] = 1
        except Exception:
            removed["plant"] = 0

    return removed


async def _species_usage_count(user_id: int, species_id: int) -> int:
    """Сколько растений этого пользователя привязано к данному виду."""
    async with new_uow() as uow:
        try:
            plants = await uow.plants.list_by_user(user_id)
        except AttributeError:
            plants = []
    return sum(1 for p in plants if getattr(p, "species_id", None) == species_id)


async def _species_delete_if_unused(user_tg_id: int, species_id: int) -> dict:
    """
    Удаляет вид, только если к нему не привязаны растения пользователя.
    Возвращает {'deleted': 1|0, 'blocked_by_usage': N}.
    """
    res = {"deleted": 0, "blocked_by_usage": 0}
    async with new_uow() as uow:
        me = await uow.users.get_by_tg_id(user_tg_id)
        sp = await uow.species.get(species_id)
        if not sp or getattr(sp, "user_id", None) != getattr(me, "id", None):
            raise PermissionError("Недоступно")

        use_cnt = await _species_usage_count(me.id, species_id)
        if use_cnt > 0:
            res["blocked_by_usage"] = use_cnt
            return res

        try:
            await uow.species.delete(species_id)
            res["deleted"] = 1
        except Exception:
            res["deleted"] = 0

    return res


# ---------- UI: список растений ----------
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
        types.InlineKeyboardButton(text="🗑 Удалить растения",
                                   callback_data=f"{PREFIX}:del_menu:{page}:{species_id or 0}"),
        types.InlineKeyboardButton(text="🗑 Удалить вид", callback_data=f"{PREFIX}:spdel_menu:1"),
    )
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
            user = await uow.users.get(cb.from_user.id)
            await uow.plants.create(user_id=user.id, name=plant_name, species_id=species_id)

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


    if action == "del_menu":
        try:
            page = int(parts[2]); species_id = int(parts[3]) or None
        except Exception:
            page, species_id = 1, None

        user = await _get_user(cb.from_user.id)
        plants = await _get_plants_with_filter(user.id, species_id)
        page_items, page, pages, total = _slice(plants, page)

        lines = ["🗑 <b>Удаление растений</b>", "Выберите номер для удаления:"]
        if page_items:
            for idx, p in enumerate(page_items, start=1):
                sp = f" · вид #{getattr(p, 'species_id', None)}" if getattr(p, "species_id", None) else ""
                lines.append(f"{idx:>2}. {p.name}{sp} (id:{p.id})")
        else:
            lines.append("(на этой странице нет растений)")

        kb = InlineKeyboardBuilder()
        for idx, p in enumerate(page_items, start=1):
            kb.button(text=str(idx), callback_data=f"{PREFIX}:del_pick:{p.id}:{page}:{species_id or 0}")
        if page_items:
            kb.adjust(5)

        # пагинация и выход
        kb.row(
            types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:del_menu:{max(1, page-1)}:{species_id or 0}"),
            types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
            types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:del_menu:{min(pages, page+1)}:{species_id or 0}"),
        )
        kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:page:{page}:{species_id or 0}"))

        await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
        return await cb.answer()

    if action == "del_pick":
        try:
            plant_id = int(parts[2]); page = int(parts[3]); species_id = int(parts[4]) or None
        except Exception:
            await cb.answer("Не получилось открыть подтверждение", show_alert=True)
            return

        # подтянем имя + права
        async with new_uow() as uow:
            plant = await uow.plants.get(plant_id)
            if not plant:
                await cb.answer("Растение не найдено", show_alert=True)
                return await show_plants_list(cb, page=page, species_id=species_id)
            me = await uow.users.get(cb.from_user.id)
            if getattr(plant, "user_id", None) != getattr(me, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

        summary = await _cascade_summary(plant_id)
        counts = summary["counts"]
        name = getattr(plant, "name", "—")
        text = (
            f"⚠️ <b>Удалить «{name}»?</b>\n\n"
            "Будут удалены связанные записи:\n"
            f"• расписания: <b>{counts['schedules']}</b>\n"
            f"• логи: <b>{counts['logs']}</b>\n\n"
            "Действие необратимо."
        )
        kb = InlineKeyboardBuilder()
        kb.row(
            types.InlineKeyboardButton(text="✅ Да", callback_data=f"{PREFIX}:del_confirm:{plant_id}:{page}:{species_id or 0}"),
            types.InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{PREFIX}:del_menu:{page}:{species_id or 0}"),
        )
        await cb.message.edit_text(text, reply_markup=kb.as_markup())
        return await cb.answer()

    if action == "del_confirm":
        try:
            plant_id = int(parts[2]); page = int(parts[3]); species_id = int(parts[4]) or None
        except Exception:
            await cb.answer("Не удалось удалить", show_alert=True)
            return

        try:
            res = await _cascade_delete_plant(cb.from_user.id, plant_id)
        except PermissionError:
            await cb.answer("Недоступно", show_alert=True)
            return await show_plants_list(cb, page=page, species_id=species_id)
        except Exception:
            res = {"plant": 0, "schedules": 0, "logs": 0}

        await cb.answer(
            f"Удалено: растение {res.get('plant',0)}, расписаний {res.get('schedules',0)}, логов {res.get('logs',0)} ✅",
            show_alert=False
        )

        return await on_plants_callbacks(
            types.CallbackQuery(id=cb.id, from_user=cb.from_user, chat_instance=cb.chat_instance, message=cb.message, data=f"{PREFIX}:del_menu:{page}:{species_id or 0}"),
            state
        )


    if action == "spdel_menu":
        try:
            page = int(parts[2]) if len(parts) > 2 else 1
        except Exception:
            page = 1
        user = await _get_user(cb.from_user.id)
        species = await _get_species(user.id)
        page_items, page, pages, total = _slice(species, page)

        lines = ["🗑 <b>Удаление видов</b>", "Выберите номер вида для удаления:"]
        if page_items:
            for idx, sp in enumerate(page_items, start=1):
                use_cnt = await _species_usage_count(user.id, sp.id)
                lines.append(f"{idx:>2}. {sp.name} (id:{sp.id}) — привязано растений: {use_cnt}")
        else:
            lines.append("(видов на этой странице нет)")

        kb = InlineKeyboardBuilder()
        for idx, sp in enumerate(page_items, start=1):
            kb.button(text=str(idx), callback_data=f"{PREFIX}:spdel_pick:{sp.id}:{page}")
        if page_items:
            kb.adjust(5)
        kb.row(
            types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:spdel_menu:{max(1, page-1)}"),
            types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
            types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:spdel_menu:{min(pages, page+1)}"),
        )
        kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:page:1:0"))

        await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
        return await cb.answer()

    if action == "spdel_pick":
        try:
            species_id = int(parts[2]); page = int(parts[3])
        except Exception:
            await cb.answer("Не получилось открыть подтверждение", show_alert=True)
            return

        user = await _get_user(cb.from_user.id)
        use_cnt = await _species_usage_count(user.id, species_id)

        async with new_uow() as uow:
            sp = await uow.species.get(species_id)
            if not sp:
                await cb.answer("Вид не найден", show_alert=True)
                return await on_plants_callbacks(
                    types.CallbackQuery(id=cb.id, from_user=cb.from_user, chat_instance=cb.chat_instance, message=cb.message, data=f"{PREFIX}:spdel_menu:{page}"),
                    state
                )
            if getattr(sp, "user_id", None) != getattr(user, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

        if use_cnt > 0:
            text = (
                f"⚠️ Нельзя удалить вид «{getattr(sp, 'name', '—')}».\n\n"
                f"К нему привязано растений: <b>{use_cnt}</b>.\n"
                "Сначала удалите/измените эти растения."
            )
            kb = InlineKeyboardBuilder()
            kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:spdel_menu:{page}"))
            await cb.message.edit_text(text, reply_markup=kb.as_markup())
            return await cb.answer()

        text = (
            f"⚠️ <b>Удалить вид «{getattr(sp, 'name', '—')}»?</b>\n\n"
            "Привязанных растений нет. Вид будет удалён."
        )
        kb = InlineKeyboardBuilder()
        kb.row(
            types.InlineKeyboardButton(text="✅ Да", callback_data=f"{PREFIX}:spdel_confirm:{species_id}:{page}"),
            types.InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{PREFIX}:spdel_menu:{page}"),
        )
        await cb.message.edit_text(text, reply_markup=kb.as_markup())
        return await cb.answer()

    if action == "spdel_confirm":
        try:
            species_id = int(parts[2]); page = int(parts[3])
        except Exception:
            await cb.answer("Не удалось удалить", show_alert=True)
            return

        try:
            res = await _species_delete_if_unused(cb.from_user.id, species_id)
        except PermissionError:
            await cb.answer("Недоступно", show_alert=True)
            return await on_plants_callbacks(
                types.CallbackQuery(id=cb.id, from_user=cb.from_user, chat_instance=cb.chat_instance, message=cb.message, data=f"{PREFIX}:spdel_menu:{page}"),
                state
            )
        except Exception:
            res = {"deleted": 0, "blocked_by_usage": 0}

        if res.get("deleted"):
            await cb.answer("Вид удалён ✅", show_alert=False)
        else:
            await cb.answer("Не удалось удалить вид", show_alert=True)

        return await on_plants_callbacks(
            types.CallbackQuery(id=cb.id, from_user=cb.from_user, chat_instance=cb.chat_instance, message=cb.message, data=f"{PREFIX}:spdel_menu:{page}"),
            state
        )

    # ---------- оставшиеся ветки ----------
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
        user = await uow.users.get(m.from_user.id)
        sp = await uow.species.get_by_name(user_id=user.id, name=species_name)
        if not sp:
            sp = await uow.species.create(user_id=user.id, name=species_name)
        await uow.plants.create(user_id=user.id, name=plant_name, species_id=getattr(sp, "id", None))

    await state.clear()
    await m.answer(f"Создано: <b>{plant_name}</b> ({species_name}) ✅")
    await show_plants_list(m, page=1, species_id=None)