import os

from pydantic_settings import BaseSettings


class SettingsDB(BaseSettings):
    mongodb_uri: str = os.getenv("MONGODB_URI")
    mongodb_db: str = os.getenv("MONGODB_DB")
    tls_enabled: bool = os.getenv("ENABLE_TLS", "false").lower() in ("1", "true", "yes")


class SettingsAPI(BaseSettings):
    pwd_secret_key: str | None = os.getenv("PWD_SECRET", None)
    pwd_algorithm: str = os.getenv("PWD_ALGORITHM", "HS256")
    pwd_access_token_expire_minutes: int = 60 * 24 * 100  # 100 days
    # Retention of raw usage events (TTL index on usage_events.ts).
    usage_events_ttl_days: int = int(os.getenv("USAGE_EVENTS_TTL_DAYS", "180"))


settings_db = SettingsDB()
settings_api = SettingsAPI()
