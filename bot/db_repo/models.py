from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey, BigInteger, String, Boolean, Integer, Time, SmallInteger, DateTime, func, Enum
import enum
from sqlalchemy import UniqueConstraint

class Base(DeclarativeBase):
    pass

# Пользователи
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Amsterdam")
    plants: Mapped[list["Plant"]] = relationship(back_populates="user", cascade="all, delete-orphan")



class Species(Base):
    __tablename__ = "species"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_species_user_name"),)

    # связи
    # (по желанию) user = relationship("User")
    plants: Mapped[list["Plant"]] = relationship(back_populates="species", cascade="all")

# В Plant:
class Plant(Base):
    __tablename__ = "plants"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))

    species_id: Mapped[int | None] = mapped_column(ForeignKey("species.id", ondelete="SET NULL"), nullable=True)
    species: Mapped[Species | None] = relationship(back_populates="plants")

    user: Mapped[User] = relationship(back_populates="plants")
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="plant", cascade="all, delete-orphan")
    events: Mapped[list["Event"]] = relationship(back_populates="plant", cascade="all, delete-orphan")

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

# Расписания (могут быть несколько на одно растение — для каждого action)
class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)  # watering | fertilizing | repotting
    type: Mapped[str] = mapped_column(String(16))  # interval | weekly
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_mask: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    local_time: Mapped[str] = mapped_column(Time, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    plant: Mapped[Plant] = relationship(back_populates="schedules")

# События (факт выполнения)
class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plants.id", ondelete="CASCADE"))
    action: Mapped[ActionType] = mapped_column(Enum(ActionType), nullable=False)
    done_at_utc: Mapped["datetime"] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(16))  # 'manual' | 'auto'

    plant: Mapped[Plant] = relationship(back_populates="events")

