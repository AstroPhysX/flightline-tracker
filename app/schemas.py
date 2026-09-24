from datetime import date, datetime
from pydantic import BaseModel, Field


class ManualTripCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ManualFlightCreate(BaseModel):
    trip_id: int | None = None
    flight_number: str
    flight_date: date
    origin: str = Field(min_length=3, max_length=8)
    destination: str = Field(min_length=3, max_length=8)
    deadhead: bool = False
    scheduled_departure_utc: datetime | None = None
    scheduled_arrival_utc: datetime | None = None
    trip_name: str = "Manual trip"


class LineImportRequest(BaseModel):
    bid_period: str
    line_number: int


class TrackingSettingsUpdate(BaseModel):
    provider: str = "disabled"
    api_key: str | None = None
    poll_seconds: int = Field(default=600, ge=60)
    monthly_budget_usd: float = Field(default=4.50, ge=0)
    public_delay_minutes: int = Field(default=10, ge=0, le=120)
    local_clock_name: str = Field(default="Jerome", min_length=1, max_length=40)


class FlightScheduleUpdate(BaseModel):
    flight_number: str
    flight_date: date
    origin: str = Field(min_length=3, max_length=8)
    destination: str = Field(min_length=3, max_length=8)
    deadhead: bool = False
    scheduled_departure_utc: datetime | None = None
    scheduled_arrival_utc: datetime | None = None
    note: str | None = None


class ScheduleRemoveRequest(BaseModel):
    remove_later_flights: bool = False


class ViewerHeartbeat(BaseModel):
    viewer_id: str = Field(min_length=1, max_length=160)


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    remember: bool = True
