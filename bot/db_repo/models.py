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


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Amsterdam")
    tg_username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    plants: Mapped[list["Plant"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Species(Base):
    __tablename__ = "species"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
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
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
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


# Типы расписаний
class ScheduleType(enum.Enum):
    INTERVAL = "INTERVAL"
    WEEKLY = "WEEKLY"


class ActionType(enum.Enum):
    WATERING = "WATERING"
    FERTILIZING = "FERTILIZING"
    REPOTTING = "REPOTTING"
    CUSTOM = "CUSTOM"

    @classmethod
    def values(cls) -> list[str]:
        return [x.value for x in cls]

    @classmethod
    def list(cls) -> list["ActionType"]:
        return list(cls)

    def code(self) -> str:
        return self.name.lower()

    def emoji(self) -> str:
        if self is ActionType.WATERING:
            return "💧"
        if self is ActionType.FERTILIZING:
            return "💊"
        if self is ActionType.REPOTTING:
            return "🪴"
        if self is ActionType.CUSTOM:
            return "🔖"
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


    @classmethod
    def from_any(cls, x: str | "ActionType" | None) -> "ActionType | None":
        if x is None:
            return None
        if isinstance(x, cls):
            return x
        if isinstance(x, str):
            x_lower = x.lower()
            for m in cls:
                if m.value.lower() == x_lower:
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


class ActionStatus(enum.Enum):
    DONE = "DONE"
    SKIPPED = "SKIPPED"


class ActionSource(enum.Enum):
    SCHEDULE = "SCHEDULE"
    MANUAL = "MANUAL"
    SHARED = "SHARED"


class ShareMemberStatus(enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"
    BLOCKED = "BLOCKED"

class ShareLink(Base):
    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(primary_key=True)

    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    owner: Mapped["User"] = relationship()

    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    allow_complete_default: Mapped[bool] = mapped_column(Boolean, default=True)
    show_history_default: Mapped[bool] = mapped_column(Boolean, default=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uses_count: Mapped[int] = mapped_column(Integer, default=0)

    schedules: Mapped[list["ShareLinkSchedule"]] = relationship(
        back_populates="share", cascade="all, delete-orphan"
    )
    members: Mapped[list["ShareMember"]] = relationship(
        back_populates="share", cascade="all, delete-orphan"
    )


class ShareLinkSchedule(Base):
    __tablename__ = "share_link_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)

    share_id: Mapped[int] = mapped_column(ForeignKey("share_links.id", ondelete="CASCADE"), index=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), index=True)

    __table_args__ = (UniqueConstraint("share_id", "schedule_id", name="uq_share_schedule"),)

    share: Mapped["ShareLink"] = relationship(back_populates="schedules")
    schedule: Mapped["Schedule"] = relationship()


class ShareMember(Base):
    __tablename__ = "share_members"

    id: Mapped[int] = mapped_column(primary_key=True)

    share_id: Mapped[int] = mapped_column(ForeignKey("share_links.id", ondelete="CASCADE"), index=True)
    share: Mapped["ShareLink"] = relationship(back_populates="members")

    subscriber_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subscriber: Mapped["User"] = relationship()

    status: Mapped[ShareMemberStatus] = mapped_column(
        Enum(ShareMemberStatus, name="sharememberstatus"), default=ShareMemberStatus.ACTIVE, nullable=False
    )

    can_complete_override: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    show_history_override: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    muted: Mapped[bool] = mapped_column(Boolean, default=False)

    joined_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    removed_at_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("share_id", "subscriber_user_id", name="uq_share_member"),)


class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))

    plant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plants.id", ondelete="SET NULL"), index=True, nullable=True
    )
    schedule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), index=True, nullable=True
    )

    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    status: Mapped[ActionStatus] = mapped_column(Enum(ActionStatus), nullable=False)
    source: Mapped[ActionSource] = mapped_column(Enum(ActionSource), nullable=False, default=ActionSource.SCHEDULE)

    share_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("share_links.id", ondelete="SET NULL"), index=True, nullable=True
    )
    share_member_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("share_members.id", ondelete="SET NULL"), index=True, nullable=True
    )

    done_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    plant_name_at_time: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    share: Mapped[Optional["ShareLink"]] = relationship()
    share_member: Mapped[Optional["ShareMember"]] = relationship()

class ActionPending(Base):
    __tablename__ = "action_pendings"

    id: Mapped[int] = mapped_column(primary_key=True)

    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("schedules.id", ondelete="CASCADE"), index=True
    )
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    planned_run_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    resolved_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_by_log_id: Mapped[int | None] = mapped_column(ForeignKey("action_logs.id", ondelete="SET NULL"), nullable=True)
    resolved_status: Mapped[ActionStatus | None] = mapped_column(Enum(ActionStatus), nullable=True)
    resolved_source: Mapped[ActionSource | None] = mapped_column(Enum(ActionSource), nullable=True)

    __table_args__ = (
        UniqueConstraint("schedule_id", "planned_run_at_utc", name="uq_pending_sched_run_at"),
    )

class ActionPendingMessage(Base):
    __tablename__ = "action_pending_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    pending_id: Mapped[int] = mapped_column(
        ForeignKey("action_pendings.id", ondelete="CASCADE"), index=True
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)

    share_id: Mapped[int | None] = mapped_column(ForeignKey("share_links.id", ondelete="SET NULL"), index=True)
    share_member_id: Mapped[int | None] = mapped_column(ForeignKey("share_members.id", ondelete="SET NULL"), index=True)