from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_data_dir: Path = Path("/data")
    tracking_provider: str = "mock"
    poll_seconds: int = 120
    dallas_timezone: str = "America/Chicago"
    admin_session_minutes: int = 30
    admin_cookie_secure: str = "auto"
    # Separate bearer token reserved for the future UPS schedule browser extension.
    # Leave blank to keep the integration endpoint disabled.
    schedule_sync_token: str = ""

    @property
    def database_url(self) -> str:
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.app_data_dir / 'tracker.db'}"

    @property
    def bid_packages_dir(self) -> Path:
        path = self.app_data_dir / "bid_packages"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
