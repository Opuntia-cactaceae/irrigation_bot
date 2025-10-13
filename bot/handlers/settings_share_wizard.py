# bot/handlers/settings_share_wizard.py
from __future__ import annotations

from typing import List, Optional, Set

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.exc import IntegrityError
from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import Schedule, Plant, ActionType
import secrets
alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

settings_router = Router(name="settings_share_wizard")

PREFIX = "settings"
PAGE_SIZE = 7


class ShareWizardStates(StatesGroup):
    selecting = State()
    confirming = State()


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

def _format_schedule_when(s: Schedule) -> str:
    """
    'каждые N дн в HH:MM' для INTERVAL,
    'Пн,Ср в HH:MM' для WEEKLY,
    'в HH:MM' по умолчанию.
    Защищено от None/ошибочных значений.
    """
    t = getattr(s, "local_time", None)
    try:
        t_str = t.strftime("%H:%M") if t else "—:—"
    except Exception:
        t_str = "—:—"

    stype = getattr(getattr(s, "type", None), "value", None)
    if stype == "INTERVAL":
        days = getattr(s, "interval_days", None)
        days_str = str(days) if days is not None else "?"
        return f"каждые {days_str} дн в {t_str}"
    if stype == "WEEKLY":
        mask_raw = getattr(s, "weekly_mask", 0) or 0
        try:
            mask = int(mask_raw)
        except Exception:
            mask = 0
        return f"{_weekly_mask_to_text(mask)} в {t_str}"
    return f"в {t_str}"


def _action_emoji(action: ActionType | str) -> str:
    val = action if isinstance(action, str) else action.value
    return {"watering": "💧", "fertilizing": "💊", "repotting": "🪴", "custom": "🔖"}.get(val, "🔔")


@settings_router.callback_query(F.data == f"{PREFIX}:share_wizard:start")
async def share_wizard_start(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(ShareWizardStates.selecting)
    await state.update_data(
        selected=[],
        action_filter="all",
        allow_complete=True,
        show_history=True,
        page=1,
    )
    await _render_select(cb, state, page=1)
    await cb.answer()

async def _collect_my_schedules(user_tg_id: int, action_filter: str) -> List[dict]:
    async with new_uow() as uow:
        me = await uow.users.get(user_tg_id)
        if not me:
            return []

        plants = await uow.plants.list_by_user(me.id) or []

        items: List[dict] = []
        for p in plants:
            sch_list = await uow.schedules.list_by_plant(p.id) or []
            for s in sch_list:
                if not getattr(s, "active", True):
                    continue

                action_val = getattr(getattr(s, "action", None), "value", getattr(s, "action", None))
                if action_filter != "all" and action_val != action_filter:
                    continue

                items.append({"schedule": s, "plant": p})

        from datetime import time as _time

        def _plant_name(it) -> str:
            name = getattr(it["plant"], "name", "") or ""
            try:
                return name.lower()
            except Exception:
                return str(name)

        def _sch_time(it):
            t = getattr(it["schedule"], "local_time", None)
            return t if t is not None else _time(0, 0)

        items.sort(key=lambda it: (_plant_name(it), _sch_time(it)))
        return items

async def _render_confirm(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_ids = set(data.get("selected", []))
    if not selected_ids:
        await cb.answer("Ничего не выбрано", show_alert=True)
        return


    allow_complete = bool(data.get("allow_complete", True))
    show_history = bool(data.get("show_history", True))


    all_items = await _collect_my_schedules(cb.from_user.id, "all")
    chosen = [it for it in all_items if it["schedule"].id in selected_ids]


    total = len(chosen)
    PREVIEW_LIMIT = 15

    lines = [
        "🧾 <b>Подтверждение</b>",
        f"Выбрано расписаний: <b>{total}</b>.",
        f"Права по умолчанию: {'отмечать можно' if allow_complete else 'отмечать нельзя'}, "
        f"{'история видна' if show_history else 'история скрыта'}.",
        "",
        "Предпросмотр:",
    ]

    def fmt_item(idx: int, it: dict) -> str:
        s: Schedule = it["schedule"]
        p: Plant = it["plant"]
        when = _format_schedule_when(s)
        custom = f" — {s.custom_title}" if s.action == ActionType.CUSTOM and s.custom_title else ""
        return f"{idx}. {p.name}{custom} · {when} {_action_emoji(s.action)}"

    for i, it in enumerate(chosen[:PREVIEW_LIMIT], start=1):
        lines.append(fmt_item(i, it))
    if total > PREVIEW_LIMIT:
        lines.append(f"… и ещё {total - PREVIEW_LIMIT}")

    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(
            text=("✅ Отметка включена" if allow_complete else "🚫 Отметка выключена"),
            callback_data=f"{PREFIX}:share_wz:opt:complete:{int(not allow_complete)}:0",
        ),
        types.InlineKeyboardButton(
            text=("👁 История видна" if show_history else "🙈 История скрыта"),
            callback_data=f"{PREFIX}:share_wz:opt:history:{int(not show_history)}:0",
        ),
    )
    kb.row(types.InlineKeyboardButton(text="🔗 Создать код", callback_data=f"{PREFIX}:share_wz:create"))
    kb.row(types.InlineKeyboardButton(text="◀️ Назад к выбору", callback_data=f"{PREFIX}:share_wz:back_to_select"))
    kb.row(types.InlineKeyboardButton(text="↩️ Настройки", callback_data=f"{PREFIX}:menu"))

    await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())

async def _render_select(cb: types.CallbackQuery, state: FSMContext, *, page: Optional[int] = None):
    data = await state.get_data()
    action_filter = data.get("action_filter", "all")
    selected: Set[int] = set(data.get("selected", set()))
    page = page or int(data.get("page", 1))

    items = await _collect_my_schedules(cb.from_user.id, action_filter)
    page_items, page, pages, _ = _slice(items, page)

    lines = [
        "🫂 <b>Поделиться расписаниями</b>",
        "Выберите несколько пунктов, затем нажмите «Создать код».",
        "",
    ]
    kb = InlineKeyboardBuilder()

    def act_btn(text: str, code: str):
        mark = "✓ " if action_filter == code else ""
        kb.button(text=f"{mark}{text}", callback_data=f"{PREFIX}:share_wz:filter:{code}:1")

    act_btn("💧", "watering")
    act_btn("💊", "fertilizing")
    act_btn("🪴", "repotting")
    act_btn("🔖", "custom")
    act_btn("👀 Все", "all")
    kb.adjust(5)

    if not page_items:
        lines.append("На этой странице нет пунктов.")
    else:
        for i, it in enumerate(page_items, start=1):
            s: Schedule = it["schedule"]
            p: Plant = it["plant"]
            when = _format_schedule_when(s)
            custom = f" — {s.custom_title}" if s.action == ActionType.CUSTOM and s.custom_title else ""
            is_on = (s.id in selected)
            chk = "☑" if is_on else "☐"
            lines.append(f"{i}. {chk} {p.name}{custom} · {when} {_action_emoji(s.action)}")
            kb.row(
                types.InlineKeyboardButton(
                    text=("Снять №" if is_on else "Выбрать №") + f"{i}",
                    callback_data=f"{PREFIX}:share_wz:toggle:{s.id}:{page}"
                )
            )

        kb.row(
            types.InlineKeyboardButton(
                text="✅ Выбрать всё на странице",
                callback_data=f"{PREFIX}:share_wz:select_all:{page}",
            ),
            types.InlineKeyboardButton(
                text="❌ Снять всё на странице",
                callback_data=f"{PREFIX}:share_wz:unselect_all:{page}",
            ),
        )

    kb.row(
        types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:share_wz:page:{max(1, page-1)}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:share_wz:page:{min(pages, page+1)}"),
    )

    allow_complete = bool(data.get("allow_complete", True))
    show_history = bool(data.get("show_history", True))
    lines.append("")
    lines.append(f"Права по умолчанию: {'отмечать можно' if allow_complete else 'отмечать нельзя'}, "
                 f"{'история видна' if show_history else 'история скрыта'}.")

    kb.row(
        types.InlineKeyboardButton(
            text=("✅ Отметка включена" if allow_complete else "🚫 Отметка выключена"),
            callback_data=f"{PREFIX}:share_wz:opt:complete:{int(not allow_complete)}:{page}",
        ),
        types.InlineKeyboardButton(
            text=("👁 История видна" if show_history else "🙈 История скрыта"),
            callback_data=f"{PREFIX}:share_wz:opt:history:{int(not show_history)}:{page}",
        ),
    )


    kb.row(
        types.InlineKeyboardButton(text="🔗 Создать код", callback_data=f"{PREFIX}:share_wz:to_confirm"),
    )
    kb.row(types.InlineKeyboardButton(text="↩️ Назад", callback_data=f"{PREFIX}:menu"))

    await state.update_data(page=page)
    await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


@settings_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_noop(cb: types.CallbackQuery):
    await cb.answer()


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_wz:toggle:"))
async def on_wz_toggle(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    sid = int(parts[3]); page = int(parts[4])
    data = await state.get_data()
    selected = set(data.get("selected", []))
    if sid in selected:
        selected.remove(sid)
    else:
        selected.add(sid)
    await state.update_data(selected=sorted(selected))
    await _render_select(cb, state, page=page)
    await cb.answer()


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_wz:select_all:"))
async def on_wz_select_all(cb: types.CallbackQuery, state: FSMContext):
    page = int(cb.data.split(":")[3])
    data = await state.get_data()
    action_filter = data.get("action_filter", "all")
    items = await _collect_my_schedules(cb.from_user.id, action_filter)
    page_items, page, _, _ = _slice(items, page)
    selected = set(data.get("selected", []))
    for it in page_items:
        selected.add(it["schedule"].id)
    await state.update_data(selected=sorted(selected))
    await _render_select(cb, state, page=page)
    await cb.answer("Выбрано всё на странице")


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_wz:unselect_all:"))
async def on_wz_unselect_all(cb: types.CallbackQuery, state: FSMContext):
    page = int(cb.data.split(":")[3])
    data = await state.get_data()
    action_filter = data.get("action_filter", "all")
    items = await _collect_my_schedules(cb.from_user.id, action_filter)
    page_items, page, _, _ = _slice(items, page)
    selected = set(data.get("selected", []))
    for it in page_items:
        selected.discard(it["schedule"].id)
    await state.update_data(selected=sorted(selected))
    await _render_select(cb, state, page=page)
    await cb.answer("Снято всё на странице")


@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_wz:opt:"))
async def on_wz_opt(cb: types.CallbackQuery, state: FSMContext):
    # settings:share_wz:opt:<complete|history>:<0|1>:<page>
    parts = cb.data.split(":")
    opt = parts[3]; val = bool(int(parts[4])); page = int(parts[5])
    if opt == "complete":
        await state.update_data(allow_complete=val)
    elif opt == "history":
        await state.update_data(show_history=val)

    current = await state.get_state()
    if current == ShareWizardStates.confirming.state:
        await _render_confirm(cb, state)
    else:
        await _render_select(cb, state, page=page)
    await cb.answer()

@settings_router.callback_query(F.data == f"{PREFIX}:share_wz:to_confirm")
async def on_wz_to_confirm(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("selected", []))
    if not selected:
        await cb.answer("Ничего не выбрано", show_alert=True)
        return
    await state.set_state(ShareWizardStates.confirming)
    await _render_confirm(cb, state)
    await cb.answer()


@settings_router.callback_query(F.data == f"{PREFIX}:share_wz:back_to_select")
async def on_wz_back_to_select(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    page = int(data.get("page", 1))
    await state.set_state(ShareWizardStates.selecting)
    await _render_select(cb, state, page=page)
    await cb.answer()

def _ensure_selecting(state_name: str) -> bool:
    return state_name == ShareWizardStates.selecting.state

@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_wz:page:"))
async def on_wz_page(cb: types.CallbackQuery, state: FSMContext):
    if not _ensure_selecting(await state.get_state()):
        await cb.answer()
        return
    page = int(cb.data.split(":")[3])
    await _render_select(cb, state, page=page)
    await cb.answer()

@settings_router.callback_query(F.data.startswith(f"{PREFIX}:share_wz:filter:"))
async def on_wz_filter(cb: types.CallbackQuery, state: FSMContext):
    if not _ensure_selecting(await state.get_state()):
        await cb.answer()
        return
    parts = cb.data.split(":")
    code = parts[3]
    page = int(parts[4]) if len(parts) > 4 else 1
    await state.update_data(action_filter=code, page=page)
    await _render_select(cb, state, page=page)
    await cb.answer()

@settings_router.callback_query(F.data == f"{PREFIX}:share_wz:create")
async def on_wz_create(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("selected", []))
    if not selected:
        await cb.answer("Ничего не выбрано", show_alert=True)
        return

    allow_complete = bool(data.get("allow_complete", True))
    show_history = bool(data.get("show_history", True))

    tg_id = cb.from_user.id
    async with new_uow() as uow:

        me = await uow.users.get(tg_id)
        if not me:
            await cb.answer("Пользователь не найден.", show_alert=True)
            return
        for _ in range(5):
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            try:
                link = await uow.share_links.create(
                    owner_user_id=me.id,
                    code=code,
                    allow_complete_default=allow_complete,
                    show_history_default=show_history,
                )
                break
            except IntegrityError:
                await uow.rollback()
                continue
        else:
            await cb.answer("Не удалось сгенерировать код, попробуйте ещё раз", show_alert=True)
            return

        await uow.share_link_schedules.bulk_add(link.id, selected)


    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="⬅️ К выбору", callback_data=f"{PREFIX}:share_wizard:start"))
    kb.row(types.InlineKeyboardButton(text="↩️ Настройки", callback_data=f"{PREFIX}:menu"))

    text = (
        "✅ Код создан.\n\n"
        f"<b>Код:</b> <code>{link.code}</code>\n\n"
        f"Права: {'можно отмечать' if allow_complete else 'нельзя отмечать'}, "
        f"{'история видна' if show_history else 'история скрыта'}.\n"
        "Передай код — по нему можно подписаться на эти расписания."
    )
    await state.clear()
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer("Код готов")