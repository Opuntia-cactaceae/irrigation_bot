from __future__ import annotations

import enum
from datetime import datetime, time
from typing import Optional

from sqlalchemy import (
    ForeignKey,
    BigInteger,
    String,
    Boolean,
    Integer,
    Time,
    SmallInteger,
    DateTime,
    func,
    Enum,
    UniqueConstraint,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Пользователи
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Amsterdam")

    plants: Mapped[list["Plant"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Species(Base):
    __tablename__ = "species"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_species_user_name"),
    )

    plants: Mapped[list["Plant"]] = relationship(
        back_populates="species",
        cascade="all",
    )


class Plant(Base):
    __tablename__ = "plants"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))

    species_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("species.id", ondelete="SET NULL"),
        nullable=True,
    )
    species: Mapped[Optional[Species]] = relationship(back_populates="plants")

    user: Mapped[User] = relationship(back_populates="plants")
    schedules: Mapped[list["Schedule"]] = relationship(
        back_populates="plant",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="plant",
        cascade="all, delete-orphan",
    )


# Типы расписаний
class ScheduleType:
    INTERVAL = "interval"
    WEEKLY = "weekly"


class ActionType(enum.Enum):
    WATERING = "watering"
    FERTILIZING = "fertilizing"
    REPOTTING = "repotting"

    @classmethod
    def values(cls) -> list[str]:
        return [x.value for x in cls]

    @classmethod
    def list(cls) -> list["ActionType"]:
        return list(cls)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)  # watering | fertilizing | repotting
    type: Mapped[str] = mapped_column(String(16))  # interval | weekly
    interval_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weekly_mask: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    local_time: Mapped[time] = mapped_column(Time, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    plant: Mapped["Plant"] = relationship(back_populates="schedules")

    events: Mapped[list["Event"]] = relationship(
        back_populates="schedule",
        passive_deletes=True,
        cascade="save-update, merge",
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"),
        nullable=False,
    )
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    done_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    plant: Mapped["Plant"] = relationship(back_populates="events")
    schedule: Mapped["Schedule"] = relationship(back_populates="events")

class ActionStatus(enum.Enum):
    DONE = "done"
    SKIPPED = "skipped"

class ActionSource(enum.Enum):
    SCHEDULE = "schedule"   # по напоминанию/расписанию
    MANUAL = "manual"       # вручную (досрочно, без кнопки напоминания)

class ActionLog(Base):
    """
    История действий (полив/удобрение/пересадка) — живёт отдельно от растений и расписаний.
    """
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # всегда полезно иметь владельца истории
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),  # если удаляют пользователя — чистим его историю
        index=True,
        nullable=False,
    )

    # мягкие ссылки на сущности, которые могут исчезать
    plant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plants.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    schedule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # что сделали
    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus), nullable=False)
    source: Mapped[ActionSource] = mapped_column(Enum(ActionSource), nullable=False, default=ActionSource.SCHEDULE)

    # когда отметили
    done_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # денормализация — на случай удаления/переименования растения
    plant_name_at_time: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # произвольная заметка (например, "полил 300 мл", "подсох верхний слой")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)