from __future__ import annotations

import datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (UniqueConstraint("origin", "destination", name="uq_route"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin: Mapped[str] = mapped_column(String(3), nullable=False)
    destination: Mapped[str] = mapped_column(String(3), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_scanned_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Route {self.origin}->{self.destination} enabled={self.enabled}>"


class ScanResult(Base):
    __tablename__ = "scan_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    origin: Mapped[str] = mapped_column(String(3), nullable=False)
    destination: Mapped[str] = mapped_column(String(3), nullable=False)
    origin_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    departure_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    departure_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    arrival_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    connection_info: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_nonstop: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    gowild_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    price_text: Mapped[str | None] = mapped_column(String(50), nullable=True)
    checked_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    raw_status: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    scan_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "origin": self.origin,
            "destination": self.destination,
            "origin_lat": self.origin_lat,
            "origin_lon": self.origin_lon,
            "dest_lat": self.dest_lat,
            "dest_lon": self.dest_lon,
            "departure_date": str(self.departure_date) if self.departure_date else None,
            "departure_time": self.departure_time,
            "arrival_time": self.arrival_time,
            "connection_info": self.connection_info,
            "is_nonstop": self.is_nonstop,
            "gowild_available": self.gowild_available,
            "price_text": self.price_text,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "raw_status": self.raw_status,
            "scan_duration_ms": self.scan_duration_ms,
            "error": self.error,
        }


class AppSettings(Base):
    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("key", name="uq_setting_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
