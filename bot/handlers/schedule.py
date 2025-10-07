# bot/handlers/schedule.py
from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command
from datetime import time

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType, ScheduleType
from bot.scheduler import plan_next_for_schedule, scheduler as aps

router = Router(name="schedule_cmd")


def _parse_weekly_mask(spec: str) -> int:
    """
    spec: строка вида "Mon,Thu" (регистр не важен, допускаются пробелы).
    Биты: Mon=0 .. Sun=6 (совпадает с Python weekday()).
    """
    order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    names = [x.strip().lower() for x in spec.split(",") if x.strip()]
    mask = 0
    for n in names:
        if n in order:
            mask |= 1 << order.index(n)
    return mask


def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"


def _action_from_code_opt(code: str | None) -> ActionType | None:
    """
    Преобразует короткий код действия в ActionType.
    w/f/r -> WATERING/FERTILIZING/REPOTTING
    None/'all' -> None (без фильтра)
    """
    if not code or code.lower() == "all":
        return None
    code = code.lower()
    mapping = {"w": ActionType.WATERING, "f": ActionType.FERTILIZING, "r": ActionType.REPOTTING}
    return mapping.get(code)


def _fmt_schedule_row(s) -> str:
    """
    Красивое описание расписания одной строкой.
    """
    act_emoji = {ActionType.WATERING: "💧", ActionType.FERTILIZING: "💊", ActionType.REPOTTING: "🪴"}.get(
        getattr(s, "action", None), "•"
    )
    s_type = getattr(s.type, "value", s.type)
    if s_type == "interval":
        body = f"каждые {s.interval_days} дн в {s.local_time.strftime('%H:%M')}"
    else:
        week_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        mask = int(getattr(s, "weekly_mask", 0) or 0)
        days = ",".join(lbl for i, lbl in enumerate(week_labels) if mask & (1 << i)) or "—"
        body = f"{days} в {s.local_time.strftime('%H:%M')}"
    return f"#{s.id} {act_emoji} {body}"


@router.message(Command("set_schedule"))
async def set_schedule(m: types.Message):
    try:
        _, plant_id_str, kind, spec, hm = (m.text or "").split(maxsplit=4)
        h, mi = map(int, hm.split(":"))
        local_t = time(hour=h, minute=mi)
        plant_id = int(plant_id_str)
        kind = kind.lower()
        if kind not in ("interval", "weekly"):
            raise ValueError
    except Exception:
        return await m.answer(
            "Формат:\n"
            "interval: <code>/set_schedule &lt;plant_id&gt; interval &lt;days&gt; HH:MM</code>\n"
            "weekly:   <code>/set_schedule &lt;plant_id&gt; weekly &lt;Mon,Wed,...&gt; HH:MM</code>",
            parse_mode="HTML",
        )

    created_id: int | None = None

    async with new_uow() as uow:
        plant = await uow.plants.get(plant_id)
        if not plant:
            return await m.answer("Растение не найдено.")

        me = await uow.users.get(m.from_user.id)
        if getattr(plant, "user_id", None) != getattr(me, "id", None):
            return await m.answer("Недоступно. Это растение не принадлежит вам.")

        if kind == "interval":
            try:
                days = int(spec)
            except Exception:
                return await m.answer("Для interval укажи целое число дней, например: 3")

            created = await uow.schedules.create(
                plant_id=plant.id,
                type=ScheduleType.INTERVAL,
                interval_days=days,
                local_time=local_t,
                active=True,
                action=ActionType.WATERING,
            )
        else:
            mask = _parse_weekly_mask(spec)
            if mask == 0:
                return await m.answer("Для weekly укажи дни, например: Mon,Thu")

            created = await uow.schedules.create(
                plant_id=plant.id,
                type=ScheduleType.WEEKLY,
                weekly_mask=mask,
                local_time=local_t,
                active=True,
                action=ActionType.WATERING,
            )

        created_id = getattr(created, "id", None)

    if created_id is not None:
        try:
            await plan_next_for_schedule(created_id)
        except Exception:
            pass

    await m.answer("Расписание сохранено ✅")


# ----------------------------- ДОБАВЛЕНО -----------------------------

@router.message(Command("list_schedules"))
async def list_schedules(m: types.Message):
    """
    Показать расписания для растения.
    Форматы:
      /list_schedules <plant_id>
      /list_schedules <plant_id> <w|f|r|all>

    Пример:
      /list_schedules 12 w
    """
    parts = (m.text or "").split()
    if len(parts) < 2:
        return await m.answer("Использование: /list_schedules <plant_id> [w|f|r|all]")
    try:
        plant_id = int(parts[1])
    except Exception:
        return await m.answer("plant_id должен быть числом")
    act_filter = _action_from_code_opt(parts[2] if len(parts) > 2 else None)

    async with new_uow() as uow:
        plant = await uow.plants.get(plant_id)
        if not plant:
            return await m.answer("Растение не найдено.")

        me = await uow.users.get(m.from_user.id)
        if getattr(plant, "user_id", None) != getattr(me, "id", None):
            return await m.answer("Недоступно. Это растение не принадлежит вам.")

        try:
            if act_filter:
                schedules = await uow.schedules.list_by_plant_action(plant_id, act_filter)
            else:
                schedules = await uow.schedules.list_by_plant(plant_id)
        except AttributeError:
            try:
                all_s = await uow.schedules.list_by_plant(plant_id)
                schedules = [s for s in all_s if (act_filter is None or getattr(s, "action", None) == act_filter)]
            except AttributeError:
                schedules = []

    if not schedules:
        return await m.answer("Расписаний не найдено.")

    text_lines = ["📋 Список расписаний:"]
    text_lines += [f"• {_fmt_schedule_row(s)}" for s in schedules]
    text_lines.append("\nУдаление: /delete_schedule <schedule_id>")
    await m.answer("\n".join(text_lines))


@router.message(Command("delete_schedule"))
async def delete_schedule(m: types.Message):

    parts = (m.text or "").split()
    if len(parts) != 2:
        return await m.answer("Использование: /delete_schedule <schedule_id>")
    try:
        sch_id = int(parts[1])
    except Exception:
        return await m.answer("schedule_id должен быть числом")

    async with new_uow() as uow:
        sch = await uow.schedules.get(sch_id)
        if not sch:
            return await m.answer("Расписание не найдено.")
        plant = await uow.plants.get(sch.plant_id)
        me = await uow.users.get(m.from_user.id)
        if not plant or getattr(plant, "user_id", None) != getattr(me, "id", None):
            return await m.answer("Недоступно.")

        try:
            await uow.schedules.delete(sch_id)
        except AttributeError:
            try:
                await uow.schedules.update(sch_id, active=False)
            except AttributeError:
                pass

    try:
        aps.remove_job(_job_id(sch_id))
    except Exception:
        pass

    await m.answer("Удалено ✅")


@router.message(Command("delete_schedules"))
async def delete_schedules_bulk(m: types.Message):
    """
    Пакетное удаление расписаний по растению (и опционально по действию).
    Форматы:
      /delete_schedules <plant_id>
      /delete_schedules <plant_id> <w|f|r|all>
    """
    parts = (m.text or "").split()
    if len(parts) < 2:
        return await m.answer("Использование: /delete_schedules <plant_id> [w|f|r|all]")
    try:
        plant_id = int(parts[1])
    except Exception:
        return await m.answer("plant_id должен быть числом")

    act_filter = _action_from_code_opt(parts[2] if len(parts) > 2 else None)

    ids: list[int] = []
    async with new_uow() as uow:
        plant = await uow.plants.get(plant_id)
        if not plant:
            return await m.answer("Растение не найдено.")

        me = await uow.users.get(m.from_user.id)
        if getattr(plant, "user_id", None) != getattr(me, "id", None):
            return await m.answer("Недоступно.")

        try:
            if act_filter:
                items = await uow.schedules.list_by_plant_action(plant_id, act_filter)
            else:
                items = await uow.schedules.list_by_plant(plant_id)
        except AttributeError:
            try:
                all_s = await uow.schedules.list_by_plant(plant_id)
            except AttributeError:
                all_s = []
            items = [s for s in all_s if (act_filter is None or getattr(s, "action", None) == act_filter)]

        ids = [s.id for s in items]

        if not ids:
            return await m.answer("Нечего удалять.")

        for sid in ids:
            try:
                await uow.schedules.delete(sid)
            except AttributeError:
                try:
                    await uow.schedules.update(sid, active=False)
                except AttributeError:
                    pass

    for sid in ids:
        try:
            aps.remove_job(_job_id(sid))
        except Exception:
            pass

    await m.answer(f"Удалено расписаний: {len(ids)} ✅")