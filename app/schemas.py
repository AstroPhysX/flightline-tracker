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


class BrowserScheduleFlight(BaseModel):
    external_id: str | None = Field(default=None, max_length=160)
    kind: str = Field(default="flight", max_length=32)
    flight_number: str | None = Field(default=None, max_length=32)
    flight_date: date | None = None
    origin: str | None = Field(default=None, max_length=8)
    destination: str | None = Field(default=None, max_length=8)
    deadhead: bool = False
    scheduled_departure_utc: datetime | None = None
    scheduled_arrival_utc: datetime | None = None
    schedule_code: str | None = Field(default=None, max_length=32)


class BrowserScheduleTrip(BaseModel):
    external_id: str | None = Field(default=None, max_length=160)
    name: str | None = Field(default=None, max_length=160)
    start_date: date | None = None
    end_date: date | None = None
    flights: list[BrowserScheduleFlight] = Field(default_factory=list, max_length=200)


class BrowserScheduleSync(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=20)
    source: str = Field(default="ups-edge-extension", max_length=80)
    extension_version: str | None = Field(default=None, max_length=40)
    captured_at: datetime
    page_url: str | None = Field(default=None, max_length=1000)
    bid_period: str | None = Field(default=None, max_length=40)
    line_number: int | None = None
    trips: list[BrowserScheduleTrip] = Field(default_factory=list, max_length=100)
