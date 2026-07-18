from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import get_env_settings, host_service_url
from app.models import AppSetting, ErrorLog

DEFAULT_KEYS = {
    "telegram_bot_token": "",
    "tmdb_api_key": "",
    "jellyfin_url": "http://192.168.68.208:8096",
    "jellyfin_api_key": "",
    "radarr_url": "http://192.168.68.208:7878",
    "radarr_api_key": "",
    "radarr_quality_profile_id": "10",  # MA 1080p max
    "radarr_root_folder": "/movies",
    "sonarr_url": "http://192.168.68.208:8989",
    "sonarr_api_key": "",
    "sonarr_quality_profile_id": "7",  # MA 1080p max
    "sonarr_root_folder": "/data/Series",
    "bazarr_url": "http://192.168.68.208:6767",
    "bazarr_api_key": "",
    "retention_days": "4",
    "disk_threshold_gb": "50",
    "max_recs_per_week": "7",
    "resolution_cap": "1080p",
    "series_buffer_episodes": "3",
    "episode_grace_hours": "12",
    "rating_timeout_hours": "24",
    "bot_status": "waiting_for_token",
}


def ensure_defaults(db: Session) -> None:
    env = get_env_settings()
    seeded = {
        **DEFAULT_KEYS,
        "jellyfin_url": env.jellyfin_url,
        "radarr_url": env.radarr_url,
        "sonarr_url": env.sonarr_url,
        "bazarr_url": env.bazarr_url,
        "retention_days": str(env.retention_days),
        "disk_threshold_gb": str(env.disk_threshold_gb),
        "max_recs_per_week": str(env.max_recs_per_week),
    }
    for key, value in seeded.items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
    db.commit()


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.get(AppSetting, key)
    if row is None or row.value is None or row.value == "":
        return default if default is not None else DEFAULT_KEYS.get(key)
    return row.value


def set_setting(db: Session, key: str, value: Optional[str]) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def get_all_settings(db: Session) -> dict[str, str]:
    ensure_defaults(db)
    rows = db.query(AppSetting).all()
    return {r.key: (r.value or "") for r in rows}


def service_cfg(db: Session) -> dict[str, Any]:
    s = get_all_settings(db)
    return {
        "telegram_bot_token": s.get("telegram_bot_token", ""),
        "tmdb_api_key": s.get("tmdb_api_key", ""),
        "jellyfin_url": host_service_url(s.get("jellyfin_url", "")),
        "jellyfin_api_key": s.get("jellyfin_api_key", ""),
        "radarr_url": host_service_url(s.get("radarr_url", "")),
        "radarr_api_key": s.get("radarr_api_key", ""),
        "radarr_quality_profile_id": int(s.get("radarr_quality_profile_id") or 10),
        "radarr_root_folder": s.get("radarr_root_folder") or "/movies",
        "sonarr_url": host_service_url(s.get("sonarr_url", "")),
        "sonarr_api_key": s.get("sonarr_api_key", ""),
        "sonarr_quality_profile_id": int(s.get("sonarr_quality_profile_id") or 7),
        "sonarr_root_folder": s.get("sonarr_root_folder") or "/data/Series",
        "bazarr_url": host_service_url(s.get("bazarr_url", "")),
        "bazarr_api_key": s.get("bazarr_api_key", ""),
        "retention_days": int(s.get("retention_days") or 4),
        "disk_threshold_gb": float(s.get("disk_threshold_gb") or 50),
        "max_recs_per_week": int(s.get("max_recs_per_week") or 7),
        "resolution_cap": (s.get("resolution_cap") or "1080p").strip().lower(),
        "series_buffer_episodes": int(s.get("series_buffer_episodes") or 3),
        "episode_grace_hours": int(s.get("episode_grace_hours") or 12),
        "rating_timeout_hours": int(s.get("rating_timeout_hours") or 24),
        "bot_status": s.get("bot_status") or "waiting_for_token",
    }


def _redact_secrets(text: str) -> str:
    """Strip API keys / JWTs from error details before persisting."""
    import re

    out = text or ""
    out = re.sub(r"(api_key=)[^&\s]+", r"\1***", out, flags=re.I)
    out = re.sub(r"(Bearer\s+)eyJ[A-Za-z0-9._\-]+", r"\1***", out, flags=re.I)
    out = re.sub(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", "***JWT***", out)
    return out


def log_error(db: Session, source: str, message: str, detail: str | None = None) -> None:
    db.add(
        ErrorLog(
            source=source,
            message=message[:500],
            detail=_redact_secrets(detail or "")[:4000],
        )
    )
    db.commit()
