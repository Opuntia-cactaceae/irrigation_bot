# bot/handlers/plants_inline.py
from __future__ import annotations

from enum import Enum

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.keyboards.plants import (
    kb_species_list,
    kb_add_species_mode,
    kb_plants_list_page,
    kb_cancel_to_list,
    kb_delete_plants_menu,
    kb_confirm_delete_plant,
    kb_delete_species_menu,
    kb_back_to_spdel_menu,
    kb_confirm_delete_species,
)
from bot.db_repo.unit_of_work import new_uow
from bot.scheduler import scheduler as aps

plants_router = Router(name="plants_inline")

PREFIX = "plants"
PAGE_SIZE = 10




class AddPlantStates(StatesGroup):
    waiting_name = State()
    waiting_species_mode = State()
    waiting_species_text = State()

class AddPlantStep(Enum):
    NAME = "waiting_name"
    SPECIES_MODE = "waiting_species_mode"
    SPECIES_TEXT = "waiting_species_text"


STEP_TO_STATE = {
    AddPlantStep.NAME: AddPlantStates.waiting_name,
    AddPlantStep.SPECIES_MODE: AddPlantStates.waiting_species_mode,
    AddPlantStep.SPECIES_TEXT: AddPlantStates.waiting_species_text,
}

async def _next_step(state: FSMContext, step: AddPlantStep):
    data = await state.get_data()
    steps: list[str] = data.get("steps", [])
    if not steps or steps[-1] != step.name:
        steps.append(step.name)
        await state.update_data(steps=steps)

async def _prev_step(state: FSMContext) -> AddPlantStep | None:
    data = await state.get_data()
    steps: list[str] = data.get("steps", [])
    if not steps:
        return None
    steps.pop()
    await state.update_data(steps=steps)
    return AddPlantStep[steps[-1]] if steps else None

async def _current_step(state: FSMContext) -> AddPlantStep | None:
    data = await state.get_data()
    steps: list[str] = data.get("steps", [])
    return AddPlantStep[steps[-1]] if steps else None

def _slice(items: list, page: int, size: int = PAGE_SIZE):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    s, e = (page - 1) * size, (page - 1) * size + size
    return items[s:e], page, pages, total


async def _get_user(user_tg_id: int):
    async with new_uow() as uow:
        return await uow.users.get(user_tg_id)


async def _get_plants_with_filter(user_id: int, species_id: int | None):
    async with new_uow() as uow:
        items = await uow.plants.list_by_user(user_id)
        if species_id:
            items = [p for p in items if getattr(p, "species_id", None) == species_id]
        return list(items)


async def _get_species(user_id: int):
    async with new_uow() as uow:
        return list(await uow.species.list_by_user(user_id))


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
        me = await uow.users.get(user_tg_id)
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
    async with new_uow() as uow:
        try:
            plants = await uow.plants.list_by_user(user_id)
        except AttributeError:
            plants = []
    return sum(1 for p in plants if getattr(p, "species_id", None) == species_id)


async def _species_delete_if_unused(user_tg_id: int, species_id: int) -> dict:

    res = {"deleted": 0, "blocked_by_usage": 0}
    async with new_uow() as uow:
        me = await uow.users.get(user_tg_id)
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

async def show_plants_list(
    target: types.Message | types.CallbackQuery,
    page: int = 1,
    species_id: int | None = None,
    *,
    auto_answer: bool = True,
):
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
    lines = []

    if page_items:
        for p in page_items:
            sp = f" · вид #{getattr(p, 'species_id', None)}" if getattr(p, "species_id", None) else ""
            lines.append(f"• {p.name}{sp} (id:{p.id})")
    else:
        lines.append("(здесь пусто)")

    text = f"{header}\n{sub}\n\nСписок ваших растений.\n" + "\n".join(lines)

    reply_markup = kb_plants_list_page(page=page, pages=pages, species_id=species_id, prefix=PREFIX)

    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=reply_markup)
        if auto_answer:
            await target.answer()
    else:
        await message.answer(text, reply_markup=reply_markup)

@plants_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_plants_noop(cb: types.CallbackQuery):
    await cb.answer()

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:page:"))
async def on_plants_page(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    try:
        page = int(parts[2])
        species_id = int(parts[3]) or None
    except Exception:
        page, species_id = 1, None
    await show_plants_list(cb, page=page, species_id=species_id)

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:filter_species"))
async def on_filter_species(cb: types.CallbackQuery):
    species_id = int(cb.data.split(":")[2]) or None
    user = await _get_user(cb.from_user.id)
    species = await _get_species(user.id)
    text = "🧬 <b>Фильтр по видам</b>\nВыберите вид или добавьте новый."
    await cb.message.edit_text(
        text,
        reply_markup=kb_species_list(species, species_id, page=1, prefix=PREFIX),
    )
    await cb.answer()

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:species_page"))
async def on_species_page(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    page = int(parts[2])
    selected = int(parts[3]) or None
    user = await _get_user(cb.from_user.id)
    species = await _get_species(user.id)
    text = "🧬 <b>Фильтр по видам</b>\nВыберите вид или добавьте новый."
    await cb.message.edit_text(
        text,
        reply_markup=kb_species_list(species, selected, page=page, prefix=PREFIX),
    )
    await cb.answer()

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:set_species"))
async def on_set_species(cb: types.CallbackQuery):
    species_id = int(cb.data.split(":")[2]) or None
    await show_plants_list(cb, page=1, species_id=species_id)

async def render_waiting_name(msg: types.Message, state: FSMContext):
    await state.set_state(AddPlantStates.waiting_name)
    data = await state.get_data()
    preset = data.get("new_plant_name")
    text = "✍️ Введите <b>название</b> растения сообщением (свободный текст)."
    if preset:
        text += f"\n\nТекущее: <b>{preset}</b>"
    await msg.edit_text(text, reply_markup=kb_cancel_to_list(page=1, prefix=PREFIX))

async def render_species_mode(msg: types.Message, user_id: int, state: FSMContext, *, page: int = 1):
    await state.set_state(AddPlantStates.waiting_species_mode)
    user = await _get_user(user_id)
    species = await _get_species(user.id)
    await msg.edit_text(
        "🧬 Выберите <b>вид</b> из списка или введите свой.",
        reply_markup=kb_species_list(species, selected_id=None, page=page, for_add_flow=True, prefix=PREFIX),
    )

async def render_species_text(msg: types.Message, state: FSMContext):
    await state.set_state(AddPlantStates.waiting_species_text)
    data = await state.get_data()
    preset = data.get("new_species_name")
    text = "✍️ Введите название <b>вида</b> сообщением."
    if preset:
        text += f"\n\nТекущее: <b>{preset}</b>"
    await msg.edit_text(text, reply_markup=kb_cancel_to_list(page=1, prefix=PREFIX))

@plants_router.callback_query(F.data == f"{PREFIX}:add")
async def on_add_plant_start(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await _next_step(state, AddPlantStep.NAME)
    await render_waiting_name(cb.message, state)
    await cb.answer()

@plants_router.callback_query(F.data == f"{PREFIX}:species_pick_list")
async def on_add_pick_species_mode(cb: types.CallbackQuery, state: FSMContext):
    await _next_step(state, AddPlantStep.SPECIES_MODE)
    await render_species_mode(cb.message, cb.from_user.id, state, page=1)
    await cb.answer()

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:add_species_page"))
async def on_add_species_page(cb: types.CallbackQuery, state: FSMContext):
    page = int(cb.data.split(":")[2])
    await render_species_mode(cb.message, cb.from_user.id, state, page=page)
    await cb.answer()

@plants_router.callback_query(F.data == f"{PREFIX}:species_add_text")
async def on_species_add_text(cb: types.CallbackQuery, state: FSMContext):
    await _next_step(state, AddPlantStep.SPECIES_TEXT)
    await render_species_text(cb.message, state)
    await cb.answer()

@plants_router.callback_query(F.data == f"{PREFIX}:back")
async def on_back(cb: types.CallbackQuery, state: FSMContext):
    curr = await _current_step(state)
    if not curr:
        await state.clear()
        return await show_plants_list(cb, page=1, species_id=None)

    prev = await _prev_step(state)
    if not prev:
        await state.clear()
        return await show_plants_list(cb, page=1, species_id=None)

    if prev is AddPlantStep.NAME:
        await state.set_state(STEP_TO_STATE[AddPlantStep.NAME])
        await render_waiting_name(cb.message, state)
    elif prev is AddPlantStep.SPECIES_MODE:
        await state.set_state(STEP_TO_STATE[AddPlantStep.SPECIES_MODE])
        await render_species_mode(cb.message, cb.from_user.id, state, page=1)
    elif prev is AddPlantStep.SPECIES_TEXT:
        await state.set_state(STEP_TO_STATE[AddPlantStep.SPECIES_TEXT])
        await render_species_text(cb.message, state)
    else:
        await state.clear()
        return await show_plants_list(cb, page=1, species_id=None)

    await cb.answer()

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:back_to_list"))
async def on_back_to_list(cb: types.CallbackQuery, state: FSMContext):

    await state.clear()
    parts = cb.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 1
    await show_plants_list(cb, page=page, species_id=None)

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:del_menu"))
async def on_del_menu(cb: types.CallbackQuery, state: FSMContext):
    try:
        parts = cb.data.split(":")
        page = int(parts[2]) if len(parts) > 2 else 1
        species_id = int(parts[3]) or None if len(parts) > 3 else None
    except Exception:
        page, species_id = 1, None

    user = await _get_user(cb.from_user.id)
    plants = await _get_plants_with_filter(user.id, species_id)
    page_items, page, pages, _ = _slice(plants, page)

    lines = ["🗑 <b>Удаление растений</b>", "Выберите номер для удаления:"]
    if page_items:
        for idx, p in enumerate(page_items, start=1):
            sp = f" · вид #{getattr(p, 'species_id', None)}" if getattr(p, "species_id", None) else ""
            lines.append(f"{idx:>2}. {p.name}{sp} (id:{p.id})")
    else:
        lines.append("(на этой странице нет растений)")

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_delete_plants_menu(
            page_items=page_items, page=page, pages=pages, species_id=species_id, prefix=PREFIX
        ),
    )
    await cb.answer()


@plants_router.callback_query(F.data.startswith(f"{PREFIX}:del_pick"))
async def on_del_pick(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    try:
        plant_id = int(parts[2])
        page = int(parts[3])
        species_id = int(parts[4]) or None
    except Exception:
        await cb.answer("Не получилось открыть подтверждение", show_alert=True)
        return

    async with new_uow() as uow:
        plant = await uow.plants.get(plant_id)
        if not plant:
            await cb.answer("Растение не найдено", show_alert=True)
            return await show_plants_list(cb, page=page, species_id=species_id, auto_answer=False)
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

    await cb.message.edit_text(
        text,
        reply_markup=kb_confirm_delete_plant(
            plant_id=plant_id, page=page, species_id=species_id, prefix=PREFIX
        ),
    )
    await cb.answer()


@plants_router.callback_query(F.data.startswith(f"{PREFIX}:del_confirm"))
async def on_del_confirm(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    try:
        plant_id = int(parts[2])
        page = int(parts[3])
        species_id = int(parts[4]) or None
    except Exception:
        await cb.answer("Не удалось удалить", show_alert=True)
        return

    try:
        res = await _cascade_delete_plant(cb.from_user.id, plant_id)
    except PermissionError:
        await cb.answer("Недоступно", show_alert=True)
        return await show_plants_list(cb, page=page, species_id=species_id, auto_answer=False)
    except Exception:
        res = {"plant": 0, "schedules": 0, "logs": 0}

    await cb.answer(
        f"Удалено: растение {res.get('plant',0)}, расписаний {res.get('schedules',0)}, логов {res.get('logs',0)} ✅",
        show_alert=False
    )

    # Обновим меню удаления на той же странице
    user = await _get_user(cb.from_user.id)
    plants = await _get_plants_with_filter(user.id, species_id)
    page_items, page, pages, _ = _slice(plants, page)

    lines = ["🗑 <b>Удаление растений</b>", "Выберите номер для удаления:"]
    if page_items:
        for idx, p in enumerate(page_items, start=1):
            sp = f" · вид #{getattr(p, 'species_id', None)}" if getattr(p, "species_id", None) else ""
            lines.append(f"{idx:>2}. {p.name}{sp} (id:{p.id})")
    else:
        lines.append("(на этой странице нет растений)")

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_delete_plants_menu(
            page_items=page_items, page=page, pages=pages, species_id=species_id, prefix=PREFIX
        ),
    )

async def render_spdel_menu(msg: types.Message, user_id: int, *, page: int):
    user = await _get_user(user_id)
    species = await _get_species(user.id)
    page_items, page, pages, _ = _slice(species, page)

    lines = ["🗑 <b>Удаление видов</b>", "Выберите номер вида для удаления:"]
    if page_items:
        async with new_uow() as uow:
            plants = await uow.plants.list_by_user(user.id)
        usage = {}
        for p in plants:
            sid = getattr(p, "species_id", None)
            if sid is not None:
                usage[sid] = usage.get(sid, 0) + 1

        for idx, sp in enumerate(page_items, start=1):
            use_cnt = usage.get(sp.id, 0)
            lines.append(f"{idx:>2}. {sp.name} (id:{sp.id}) — привязано растений: {use_cnt}")
    else:
        lines.append("(видов на этой странице нет)")

    await msg.edit_text(
        "\n".join(lines),
        reply_markup=kb_delete_species_menu(page_items=page_items, page=page, pages=pages, prefix=PREFIX),
    )

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:spdel_menu"))
async def on_spdel_menu(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    try:
        page = int(parts[2]) if len(parts) > 2 else 1
    except Exception:
        page = 1
    await render_spdel_menu(cb.message, cb.from_user.id, page=page)
    await cb.answer()


@plants_router.callback_query(F.data.startswith(f"{PREFIX}:spdel_pick"))
async def on_spdel_pick(cb: types.CallbackQuery, state: FSMContext):
    try:
        parts = cb.data.split(":")
        species_id = int(parts[2])
        page = int(parts[3])
    except Exception:
        await cb.answer("Не получилось открыть подтверждение", show_alert=True)
        return

    user = await _get_user(cb.from_user.id)
    use_cnt = await _species_usage_count(user.id, species_id)

    async with new_uow() as uow:
        sp = await uow.species.get(species_id)
        if not sp:
            await cb.answer("Вид не найден", show_alert=True)
            await render_spdel_menu(cb.message, cb.from_user.id, page=page)
            return  # <-- добавьте это
        if getattr(sp, "user_id", None) != getattr(user, "id", None):
            await cb.answer("Недоступно", show_alert=True)
            return

    if use_cnt > 0:
        text = (
            f"⚠️ Нельзя удалить вид «{getattr(sp, 'name', '—')}».\n\n"
            f"К нему привязано растений: <b>{use_cnt}</b>.\n"
            "Сначала удалите/измените эти растения."
        )
        await cb.message.edit_text(
            text,
            reply_markup=kb_back_to_spdel_menu(page=page, prefix=PREFIX),
        )
        return await cb.answer()

    text = (
        f"⚠️ <b>Удалить вид «{getattr(sp, 'name', '—')}»?</b>\n\n"
        "Привязанных растений нет. Вид будет удалён."
    )
    await cb.message.edit_text(
        text,
        reply_markup=kb_confirm_delete_species(
            species_id=species_id, page=page, prefix=PREFIX
        ),
    )
    await cb.answer()


@plants_router.callback_query(F.data.startswith(f"{PREFIX}:spdel_confirm"))
async def on_spdel_confirm(cb: types.CallbackQuery):
    try:
        parts = cb.data.split(":")
        species_id = int(parts[2])
        page = int(parts[3])
    except Exception:
        await cb.answer("Не удалось удалить", show_alert=True)
        return

    try:
        res = await _species_delete_if_unused(cb.from_user.id, species_id)
    except PermissionError:
        await cb.answer("Недоступно", show_alert=True)
        await render_spdel_menu(cb.message, cb.from_user.id, page=page)
        return
    except Exception:
        res = {"deleted": 0, "blocked_by_usage": 0}

    if res.get("deleted"):
        await cb.answer("Вид удалён ✅", show_alert=False)
    else:
        await cb.answer("Не удалось удалить вид", show_alert=True)

    return await render_spdel_menu(cb.message, cb.from_user.id, page=page)


@plants_router.message(AddPlantStates.waiting_name)
async def input_plant_name(m: types.Message, state: FSMContext):
    name = (m.text or "").strip()
    if not name:
        return await m.answer("Название пустое. Введите ещё раз или нажмите «Отмена».")
    await state.update_data(new_plant_name=name)
    await state.set_state(AddPlantStates.waiting_species_mode)
    await _next_step(state, AddPlantStep.SPECIES_MODE)
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
    await show_plants_list(m, page=1, species_id=None, auto_answer=False)

@plants_router.callback_query(F.data.startswith(f"{PREFIX}:add_pick_species:"))
async def on_add_pick_species(cb: types.CallbackQuery, state: FSMContext):

    parts = cb.data.split(":")
    try:
        raw_id = parts[2]
        species_id = int(raw_id)
        species_id = species_id if species_id != 0 else None
    except Exception:
        await cb.answer("Не удалось выбрать вид", show_alert=True)
        return

    data = await state.get_data()
    plant_name = (data or {}).get("new_plant_name")
    if not plant_name:
        await state.clear()
        await cb.answer("Контекст утерян, начните заново", show_alert=True)
        return await show_plants_list(cb, page=1, species_id=None, auto_answer=False)

    async with new_uow() as uow:
        user = await uow.users.get(cb.from_user.id)

        if species_id is not None:
            sp = await uow.species.get(species_id)
            if not sp or getattr(sp, "user_id", None) != getattr(user, "id", None):
                await cb.answer("Недоступно или вид не найден", show_alert=True)
                return

        await uow.plants.create(
            user_id=user.id,
            name=plant_name,
            species_id=species_id,
        )

    await state.clear()
    await cb.answer("Растение создано ✅", show_alert=False)
    return await show_plants_list(cb, page=1, species_id=None, auto_answer=False)