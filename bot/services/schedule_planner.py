# bot/services/schedule_planner.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
from typing import Optional, Sequence, List

import pytz
from pytz import AmbiguousTimeError, NonExistentTimeError, UnknownTimeZoneError

from bot.db_repo.models import Schedule, ActionType, ActionSource


@dataclass
class NextRun:
    schedule_id: int
    plant_id: int
    action: ActionType
    dt_utc: datetime
    dt_local: datetime
    user_tz: str


# ---------- Timezone helpers (pytz-safe) ----------

def _safe_tz(tz_name: str):
    """Безопасно получить tz; при ошибке — UTC."""
    try:
        return pytz.timezone(tz_name or "UTC")
    except UnknownTimeZoneError:
        return pytz.timezone("UTC")


def _to_local(dt_utc: datetime, tz_name: str) -> datetime:
    """Преобразовать UTC в локальное время пользователя."""
    tz = _safe_tz(tz_name)
    return dt_utc.astimezone(tz)


def _make_local_dt(base_date: date, local_t: time, tz_name: str) -> datetime:
    """
    Построить локальный datetime (наивный date + time -> локализованный) с учетом DST.
    Обрабатывает неоднозначные и несуществующие моменты времени.
    """
    tz = _safe_tz(tz_name)
    naive = datetime.combine(base_date, local_t)
    try:
        # строгая локализация; если момент неоднозначный/несуществующий — кинет исключение
        return tz.localize(naive, is_dst=None)
    except AmbiguousTimeError:
        # выбираем «летний» вариант как правило удобнее для напоминаний
        return tz.localize(naive, is_dst=True)
    except NonExistentTimeError:
        # момент «перескочил» при весеннем переводе — сдвигаем на час вперёд
        return tz.localize(naive + timedelta(hours=1), is_dst=True)


# ---------- Core scheduling logic ----------

def calc_next_run_utc(
    sch: Schedule,
    *,
    owner_tz: str,
    last_event_utc: Optional[datetime],
    last_event_source: Optional[ActionSource],  # зарезервировано на будущее
    now_utc: Optional[datetime] = None,
) -> datetime:
    """
    Расчёт ближайшего запуска расписания в UTC.

    Правила:
    - type == INTERVAL: каждые N дней в local_time владельца.
      Если last_event отсутствует, сначала пробуем «сегодня в local_time»,
      и только если этот момент уже прошёл, берём «сегодня + step, затем +step…».
    - type == WEEKLY: по weekly_mask (бит 0=Пн … бит 6=Вс) в local_time владельца.
      Ищем ближайший день начиная с «сегодня» (включительно).
      Пустая маска -> fallback на «завтра в local_time».

    Примечания:
    - `last_event_source` оставлен в сигнатуре для совместимости/расширений.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    if getattr(sch, "local_time", None) is None:
        raise ValueError(f"Schedule {getattr(sch, 'id', None)} has no local_time")

    tz = _safe_tz(owner_tz)
    today_local = now_utc.astimezone(tz).date()

    def to_utc(d: date) -> datetime:
        return _make_local_dt(d, sch.local_time, owner_tz).astimezone(timezone.utc)

    stype = getattr(getattr(sch, "type", None), "value", None)

    if stype == "INTERVAL":
        step = max(1, int(getattr(sch, "interval_days", 1) or 1))

        # Якорь — дата последнего события в локальной зоне, иначе «сегодня».
        anchor_local_date = (
            last_event_utc.astimezone(tz).date()
            if last_event_utc is not None
            else today_local
        )

        # 1) сначала пробуем момент на якорную дату
        cand_utc = to_utc(anchor_local_date)
        if cand_utc > now_utc:
            return cand_utc

        # 2) затем движемся по шагам
        d = anchor_local_date
        while True:
            d += timedelta(days=step)
            cand_utc = to_utc(d)
            if cand_utc > now_utc:
                return cand_utc

    else:  # трактуем всё остальное как WEEKLY
        mask = int(getattr(sch, "weekly_mask", 0) or 0)  # bit 0=Mon ... 6=Sun
        # Ищем ближайший подходящий день, начиная с «сегодня»
        for delta in range(0, 14):  # за две недели обязаны найти, если маска не пустая
            d = today_local + timedelta(days=delta)
            w = d.weekday()  # Monday=0..Sunday=6
            if (mask >> w) & 1:
                cand_utc = to_utc(d)
                if cand_utc > now_utc:
                    return cand_utc
        # Пустая маска — разумный fallback: «завтра в local_time»
        return to_utc(today_local + timedelta(days=1))


# ---------- Public API ----------

async def build_next_runs_for_user(
    uow,
    *,
    user_id: int,
    limit: int = 50,
    action_filter: Optional[ActionType] = None,
) -> List[NextRun]:
    """
    Возвращает отсортированный список ближайших срабатываний по активным расписаниям пользователя.
    """
    user = await uow.users.get(user_id)
    if not user:
        return []

    user_tz = getattr(user, "tz", "UTC") or "UTC"
    now_utc = datetime.now(timezone.utc)

    # Пытаемся получить растения сразу с related (если есть оптимизированный метод)
    try:
        plants = await uow.plants.list_by_user_with_relations(user.id)
    except AttributeError:
        plants = await uow.plants.list_by_user(user.id)

    items: List[NextRun] = []

    # Нормализуем фильтр действия к его «значению»
    af_val = getattr(action_filter, "value", action_filter) if action_filter else None

    for p in (plants or []):
        # Берём только активные расписания
        schedules: Sequence[Schedule] = [
            s for s in (getattr(p, "schedules", []) or [])
            if getattr(s, "active", True)
        ]

        # Применяем фильтр действия, если задан
        if af_val is not None:
            schedules = [
                s for s in schedules
                if getattr(getattr(s, "action", None), "value", getattr(s, "action", None)) == af_val
            ]

        if not schedules:
            continue

        for sch in schedules:
            # Получаем последний «эффективный» лог (DONE/SKIP) — метод уже должен это учитывать
            try:
                last_event_utc, last_event_source = await uow.action_logs.last_effective_done(sch.id)
            except Exception:
                last_event_utc, last_event_source = None, None

            try:
                run_at_utc = calc_next_run_utc(
                    sch=sch,
                    owner_tz=user_tz,
                    last_event_utc=last_event_utc,
                    last_event_source=last_event_source,
                    now_utc=now_utc,
                )
            except Exception:
                # Один «битый» шедул не должен рушить весь список
                continue

            items.append(
                NextRun(
                    schedule_id=sch.id,
                    plant_id=p.id,
                    action=sch.action,
                    dt_utc=run_at_utc,
                    dt_local=_to_local(run_at_utc, user_tz),
                    user_tz=user_tz,
                )
            )

    items.sort(key=lambda x: x.dt_utc)
    return items[:limit]