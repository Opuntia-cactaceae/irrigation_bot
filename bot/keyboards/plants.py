# bot/keyboards/plants.py
from __future__ import annotations

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

DEFAULT_PREFIX = "plants"

def _pager_buttons(prefix: str, route: str, page: int, pages: int, *extra_parts: str):
    has_prev = page > 1
    has_next = page < pages
    prev_page = page - 1 if has_prev else 1
    next_page = page + 1 if has_next else pages

    left_text = "◀️" if has_prev else "⏺"
    right_text = "▶️" if has_next else "⏺"

    suffix = (":" + ":".join(extra_parts)) if extra_parts else ""

    left_cb = f"{prefix}:{route}:{prev_page}{suffix}" if has_prev else f"{prefix}:noop"
    right_cb = f"{prefix}:{route}:{next_page}{suffix}" if has_next else f"{prefix}:noop"

    return (
        types.InlineKeyboardButton(text=left_text, callback_data=left_cb),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{prefix}:noop"),
        types.InlineKeyboardButton(text=right_text, callback_data=right_cb),
    )

def kb_back(callback_data: str, *, text: str = "◀️ Назад"):
    kb = InlineKeyboardBuilder()
    kb.button(text=text, callback_data=callback_data)
    kb.adjust(1)
    return kb.as_markup()

def add_back_row(kb: InlineKeyboardBuilder, callback_data: str, *, text: str = "◀️ Назад") -> InlineKeyboardBuilder:
    kb.row(types.InlineKeyboardButton(text=text, callback_data=callback_data))
    return kb

def kb_species_list(
    species,
    selected_id: int | None,
    *,
    page: int = 1,
    page_size: int = 10,
    for_add_flow: bool = False,
    prefix: str = DEFAULT_PREFIX,
):

    def _slice(items: list, page: int, size: int):
        total = len(items)
        pages = max(1, (total + size - 1) // size)
        page = max(1, min(page, pages))
        s, e = (page - 1) * size, (page - 1) * size + size
        return items[s:e], page, pages, total

    items, page, pages, _ = _slice(list(species), page, page_size)
    kb = InlineKeyboardBuilder()

    if for_add_flow:
        kb.button(text="(без вида)", callback_data=f"{prefix}:add_pick_species:0")
        for s in items:
            kb.button(text=s.name, callback_data=f"{prefix}:add_pick_species:{s.id}")
    else:
        mark = "✓ " if not selected_id else ""
        kb.button(text=f"{mark}Все виды", callback_data=f"{prefix}:set_species:0:{page}")
        for s in items:
            mark = "✓ " if (selected_id == s.id) else ""
            kb.button(text=f"{mark}{s.name}", callback_data=f"{prefix}:set_species:{s.id}:{page}")

    kb.adjust(2)

    if pages > 1:
        if for_add_flow:
            l, c, r = _pager_buttons(prefix, "add_species_page", page, pages)
        else:
            l, c, r = _pager_buttons(prefix, "species_page", page, pages, str(selected_id or 0))
        kb.row(l, c, r)

    if for_add_flow:
        kb.row(
            types.InlineKeyboardButton(text="✍️ Ввести свой вид", callback_data=f"{prefix}:species_add_text"),
        )
        add_back_row(kb, f"{prefix}:back_to_list:1", text="↩️ Отмена")
    else:
        kb.row(
            types.InlineKeyboardButton(text="➕ Добавить вид (текстом)", callback_data=f"{prefix}:species_add_text"),
        )
        add_back_row(kb, f"{prefix}:page:1:0", text="↩️ К списку")

    return kb.as_markup()

def kb_add_species_mode(*, prefix: str = DEFAULT_PREFIX):
    kb = InlineKeyboardBuilder()
    kb.button(text="🧬 Выбрать из списка", callback_data=f"{prefix}:species_pick_list")
    kb.button(text="✍️ Ввести свой вид", callback_data=f"{prefix}:species_add_text")
    kb.adjust(1)
    return kb.as_markup()

def kb_plants_list_page(
    *,
    page: int,
    pages: int,
    species_id: int | None,
    prefix: str = DEFAULT_PREFIX,
):
    kb = InlineKeyboardBuilder()

    l, c, r = _pager_buttons(prefix, "page", page, pages, str(species_id or 0))
    kb.row(l, c, r)

    kb.row(types.InlineKeyboardButton(text="🧬 Фильтр по виду", callback_data=f"{prefix}:filter_species:{species_id or 0}"))

    kb.row(
        types.InlineKeyboardButton(
            text="🗑 Удалить растения",
            callback_data=f"{prefix}:del_menu:{page}:{species_id or 0}",
        ),
        types.InlineKeyboardButton(
            text="🗑 Удалить вид",
            callback_data=f"{prefix}:spdel_menu:1",
        ),
    )

    kb.row(
        types.InlineKeyboardButton(text="➕ Добавить растение", callback_data=f"{prefix}:add"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )
    return kb.as_markup()

def kb_cancel_to_list(*, page: int = 1, prefix: str = DEFAULT_PREFIX):
    return kb_back(f"{prefix}:back_to_list:{page}", text="↩️ Отмена")

def kb_delete_plants_menu(
    *,
    page_items,
    page: int,
    pages: int,
    species_id: int | None,
    prefix: str = DEFAULT_PREFIX,
):
    kb = InlineKeyboardBuilder()

    for idx, p in enumerate(page_items, start=1):
        kb.button(text=str(idx), callback_data=f"{prefix}:del_pick:{p.id}:{page}:{species_id or 0}")
    if page_items:
        kb.adjust(5)

    l, c, r = _pager_buttons(prefix, "del_menu", page, pages, str(species_id or 0))
    kb.row(l, c, r)
    add_back_row(kb, f"{prefix}:page:{page}:{species_id or 0}", text="↩️ Назад")
    return kb.as_markup()

def kb_confirm_delete_plant(
    *,
    plant_id: int,
    page: int,
    species_id: int | None,
    prefix: str = DEFAULT_PREFIX,
):
    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(text="✅ Да",
                                   callback_data=f"{prefix}:del_confirm:{plant_id}:{page}:{species_id or 0}"),
    )
    add_back_row(kb, f"{prefix}:del_menu:{page}:{species_id or 0}", text="↩️ Отмена")
    return kb.as_markup()

def kb_delete_species_menu(
    *,
    page_items,
    page: int,
    pages: int,
    prefix: str = DEFAULT_PREFIX,
):
    kb = InlineKeyboardBuilder()
    for idx, sp in enumerate(page_items, start=1):
        kb.button(text=str(idx), callback_data=f"{prefix}:spdel_pick:{sp.id}:{page}")
    if page_items:
        kb.adjust(5)
    l, c, r = _pager_buttons(prefix, "spdel_menu", page, pages)
    kb.row(l, c, r)
    add_back_row(kb, f"{prefix}:page:1:0", text="↩️ Назад")
    return kb.as_markup()

def kb_back_to_spdel_menu(*, page: int, prefix: str = DEFAULT_PREFIX):
    return kb_back(f"{prefix}:spdel_menu:{page}", text="↩️ Назад")

def kb_confirm_delete_species(*, species_id: int, page: int, prefix: str = DEFAULT_PREFIX):
    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(text="✅ Да", callback_data=f"{prefix}:spdel_confirm:{species_id}:{page}"),
    )
    add_back_row(kb, f"{prefix}:spdel_menu:{page}", text="↩️ Отмена")
    return kb.as_markup()