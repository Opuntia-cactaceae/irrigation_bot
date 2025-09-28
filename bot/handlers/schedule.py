# bot/handlers/schedule.py
from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import Command
from datetime import time

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType  # если требуется поле action у расписания

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


@router.message(Command("set_schedule"))
async def set_schedule(m: types.Message):
    """
    Форматы:
      /set_schedule <plant_id> interval <days> HH:MM
      /set_schedule <plant_id> weekly   <Mon,Wed,...> HH:MM
    По умолчанию создаём расписание для действия WATERING.
    Если у растения уже есть расписание(я) — заменяем.
    """
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

    async with new_uow() as uow:
        plant = await uow.plants.get(plant_id)
        if not plant:
            return await m.answer("Растение не найдено.")

        # Проверим владельца
        owner = await uow.users.get_by_id(getattr(plant, "user_id", None))
        if not owner or owner.tg_user_id != m.from_user.id:
            return await m.answer("Недоступно. Это растение не принадлежит вам.")

        # Удалим старые расписания этого растения (если такая логика нужна)
        try:
            old_list = await uow.schedules.list_by_plant(plant_id)
            for s in old_list:
                await uow.schedules.delete(s.id)
        except AttributeError:
            # если нет list_by_plant/delete — попробуем get_by_plant + delete_one
            try:
                old = await uow.schedules.get_by_plant(plant_id)
                if old:
                    await uow.schedules.delete(old.id)
            except AttributeError:
                pass  # нет API — пропустим

        # Создадим новое расписание
        if kind == "interval":
            try:
                days = int(spec)
            except Exception:
                return await m.answer("Для interval укажи целое число дней, например: 3")
            created = await uow.schedules.create(
                plant_id=plant.id,
                type="interval",
                interval_days=days,
                local_time=local_t,
                active=True,
                # если в модели есть поле action — оставим полив по умолчанию
                action=getattr(ActionType, "WATERING", None),
            )
        else:
            mask = _parse_weekly_mask(spec)
            if mask == 0:
                return await m.answer("Для weekly укажи дни, например: Mon,Thu")
            created = await uow.schedules.create(
                plant_id=plant.id,
                type="weekly",
                weekly_mask=mask,
                local_time=local_t,
                active=True,
                action=getattr(ActionType, "WATERING", None),
            )

    # Спланируем следующий запуск (вне UnitOfWork)
    planned_ok = False
    # пробуем разные варианты, чтобы не зависеть от реализации планировщика
    try:
        from bot.scheduler import plan_next_for_schedule
        if created and getattr(created, "id", None) is not None:
            await plan_next_for_schedule(m.bot, created.id)
            planned_ok = True
    except Exception:
        pass
    if not planned_ok:
        try:
            from bot.scheduler import plan_next_for_plant
            await plan_next_for_plant(m.bot, plant_id)
        except Exception:
            pass  # не критично для сохранения расписания

    await m.answer("Расписание сохранено ✅")