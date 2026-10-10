"""Rollup models for minute and hourly aggregations."""

from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MinuteRollup(Base):
    """Minute-level aggregation rollup."""

    __tablename__ = "minute_rollups"
    __table_args__ = (
        UniqueConstraint("project_id", "event_name", "bucket", name="uq_minute_rollup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class HourlyRollup(Base):
    """Hourly-level compacted rollup."""

    __tablename__ = "hourly_rollups"
    __table_args__ = (
        UniqueConstraint("project_id", "event_name", "bucket", name="uq_hourly_rollup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
