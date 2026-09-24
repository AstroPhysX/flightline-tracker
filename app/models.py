from __future__ import annotations

from datetime import date, datetime, timezone
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Airport(Base):
    __tablename__ = "airports"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    timezone_name: Mapped[str | None] = mapped_column(String(80), nullable=True)


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    bid_period: Mapped[str | None] = mapped_column(String(20), nullable=True)
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ups_trip_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    flights: Mapped[list[Flight]] = relationship(
        back_populates="trip", cascade="all, delete-orphan", order_by="Flight.sequence"
    )


class Flight(Base):
    __tablename__ = "flights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    flight_number: Mapped[str] = mapped_column(String(24), index=True)
    flight_date: Mapped[date] = mapped_column(Date)
    origin: Mapped[str] = mapped_column(String(8))
    destination: Mapped[str] = mapped_column(String(8))
    deadhead: Mapped[bool] = mapped_column(Boolean, default=False)

    scheduled_departure_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_arrival_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_departure_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_arrival_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # FlightAware's runway schedule is kept separately from the UPS/PDF schedule.
    # This avoids silently rewriting the awarded/current itinerary while still
    # letting the live status card mirror FlightAware's OFF/ON timing.
    provider_scheduled_departure_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_scheduled_arrival_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_departure_delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_arrival_delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_departure_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_arrival_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_rest_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(24), default="scheduled")
    aircraft_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    registration: Mapped[str | None] = mapped_column(String(24), nullable=True)
    provider_flight_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    current_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    groundspeed_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_position_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_provider_poll_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_track_poll_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reassignment_search_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_track_fetched: Mapped[bool] = mapped_column(Boolean, default=False)

    # Schedule-revision support. The existing fields above are the current/actual
    # itinerary. These awarded_* fields preserve the immutable PDF-awarded plan
    # so the UI can compare "Awarded" with "Current / actually flown".
    schedule_active: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_added: Mapped[bool] = mapped_column(Boolean, default=False)
    awarded_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    awarded_flight_number: Mapped[str | None] = mapped_column(String(24), nullable=True)
    awarded_flight_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    awarded_origin: Mapped[str | None] = mapped_column(String(8), nullable=True)
    awarded_destination: Mapped[str | None] = mapped_column(String(8), nullable=True)
    awarded_deadhead: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    awarded_scheduled_departure_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    awarded_scheduled_arrival_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schedule_change_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    trip: Mapped[Trip] = relationship(back_populates="flights")
    positions: Mapped[list[FlightPosition]] = relationship(
        back_populates="flight", cascade="all, delete-orphan", order_by="FlightPosition.captured_utc"
    )


class LogbookEntry(Base):
    __tablename__ = "logbook_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    flight_date: Mapped[date] = mapped_column(Date, index=True)
    aircraft_model: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    aircraft_ident: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    flight_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    route_raw: Mapped[str | None] = mapped_column(String(240), nullable=True)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    night_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    instrument_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    landings_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    landings_night: Mapped[int | None] = mapped_column(Integer, nullable=True)
    second_in_command_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    pilot_in_command_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    legs: Mapped[list[LogbookLeg]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="LogbookLeg.sequence"
    )
    visits: Mapped[list[LogbookAirportVisit]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", order_by="LogbookAirportVisit.sequence"
    )


class LogbookLeg(Base):
    __tablename__ = "logbook_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("logbook_entries.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(8), index=True)
    destination: Mapped[str] = mapped_column(String(8), index=True)

    entry: Mapped[LogbookEntry] = relationship(back_populates="legs")


class LogbookAirportVisit(Base):
    __tablename__ = "logbook_airport_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("logbook_entries.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    airport_code: Mapped[str] = mapped_column(String(8), index=True)

    entry: Mapped[LogbookEntry] = relationship(back_populates="visits")


class FlightPosition(Base):
    __tablename__ = "flight_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("flights.id"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    altitude_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    groundspeed_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    flight: Mapped[Flight] = relationship(back_populates="positions")
