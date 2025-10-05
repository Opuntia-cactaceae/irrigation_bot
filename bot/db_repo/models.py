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
class ScheduleType(enum.Enum):
    INTERVAL = "interval"
    WEEKLY = "weekly"


class ActionType(enum.Enum):
    WATERING = "watering"
    FERTILIZING = "fertilizing"
    REPOTTING = "repotting"
    CUSTOM = "custom"

    @classmethod
    def values(cls) -> list[str]:
        return [x.value for x in cls]

    @classmethod
    def list(cls) -> list["ActionType"]:
        return list(cls)

    def emoji(self) -> str:
        if self is ActionType.WATERING:
            return "💧"
        if self is ActionType.FERTILIZING:
            return "💊"
        if self is ActionType.REPOTTING:
            return "🪴"
        if self is ActionType.CUSTOM:
            return "🪴"
        return "•"

    def title_ru(self) -> str:
        if self is ActionType.WATERING:
            return "Полив"
        if self is ActionType.FERTILIZING:
            return "Подкормка"
        if self is ActionType.REPOTTING:
            return "Пересадка"
        if self is ActionType.CUSTOM:
            return "Действие"
        return "Действие"

    def emoji(self) -> str:
        return self._EMOJI[self.value]

    def title_ru(self) -> str:
        return self._TITLE_RU[self.value]

    @classmethod
    def from_any(cls, x: str | "ActionType" | None) -> "ActionType | None":
        if x is None:
            return None
        if isinstance(x, cls):
            return x
        if isinstance(x, str):
            x_lower = x.lower()
            for m in cls:
                if m.value == x_lower:
                    return m
            by_name: dict[str, ActionType] = {m.name: m for m in cls}
            return by_name.get(x.upper())
        return None


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    type: Mapped[ScheduleType] = mapped_column(
        Enum(ScheduleType, name="scheduletype", native_enum=True, validate_strings=True),
        nullable=False,
    )
    interval_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weekly_mask: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    local_time: Mapped[time] = mapped_column(Time, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    custom_title: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    custom_note_template: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

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
    SCHEDULE = "schedule"
    MANUAL = "manual"

class ActionLog(Base):
    """
    История действий (полив/удобрение/пересадка) — живёт отдельно от растений и расписаний.
    """
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)


    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

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

    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus), nullable=False)
    source: Mapped[ActionSource] = mapped_column(Enum(ActionSource), nullable=False, default=ActionSource.SCHEDULE)

    done_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    plant_name_at_time: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)



class ScheduleShare(Base):
    __tablename__ = "schedule_shares"

    id: Mapped[int] = mapped_column(primary_key=True)

    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True, nullable=False
    )

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    note: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    allow_complete_by_subscribers: Mapped[bool] = mapped_column(Boolean, default=True)

    schedule: Mapped["Schedule"] = relationship()
    owner: Mapped["User"] = relationship()


class ScheduleSubscription(Base):
    __tablename__ = "schedule_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)

    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True, nullable=False
    )
    subscriber_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    can_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)

    accepted_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("schedule_id", "subscriber_user_id", name="uq_schedule_subscriber"),
    )

    schedule: Mapped["Schedule"] = relationship()
    subscriber: Mapped["User"] = relationship()