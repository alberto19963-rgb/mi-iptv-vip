from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://media_assistant:ma_change_me_local@db:5432/media_assistant"
    secret_key: str = "dev-secret-change-me"
    app_host: str = "0.0.0.0"
    app_port: int = 8510
    tz: str = "America/Puerto_Rico"

    # Defaults (overridden by DB settings when present)
    jellyfin_url: str = "http://192.168.68.208:8096"
    radarr_url: str = "http://192.168.68.208:7878"
    sonarr_url: str = "http://192.168.68.208:8989"
    bazarr_url: str = "http://192.168.68.208:6767"
    retention_days: int = 4
    disk_threshold_gb: int = 50
    max_recs_per_week: int = 7


@lru_cache
def get_env_settings() -> Settings:
    return Settings()


def host_service_url(url: str) -> str:
    """Rewrite loopback to host gateway inside Docker; keep LAN IP as-is (Synology)."""
    if not url:
        return url
    if os.path.exists("/.dockerenv"):
        return url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
    return url
