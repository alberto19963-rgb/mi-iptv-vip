from __future__ import annotations

from sqlalchemy.orm import Session

from app.services.radarr import RadarrClient
from app.services.settings_store import service_cfg
from app.services.sonarr import SonarrClient

# Profiles created on NAS (no 4K / no Remux)
PROFILE_MAP = {
    "1080p": {"radarr": 10, "sonarr": 7, "fallback_radarr": 6, "fallback_sonarr": 6},
    "720p": {"radarr": 11, "sonarr": 8, "fallback_radarr": 3, "fallback_sonarr": 3},
}


def normalize_resolution_cap(value: str | None) -> str:
    v = (value or "1080p").strip().lower()
    if v in ("720", "720p"):
        return "720p"
    return "1080p"


def quality_profile_ids(db: Session, resolution_cap: str | None = None) -> dict[str, int]:
    cfg = service_cfg(db)
    cap = normalize_resolution_cap(resolution_cap or cfg.get("resolution_cap"))
    mapping = PROFILE_MAP[cap]
    radarr_id = int(cfg.get("radarr_quality_profile_id") or mapping["radarr"])
    sonarr_id = int(cfg.get("sonarr_quality_profile_id") or mapping["sonarr"])
    # If panel still has old ESP/Any profile and cap is 1080p, force MA profiles
    if cap == "1080p":
        radarr_id = mapping["radarr"]
        sonarr_id = mapping["sonarr"]
    elif cap == "720p":
        radarr_id = mapping["radarr"]
        sonarr_id = mapping["sonarr"]
    return {"cap": cap, "radarr": radarr_id, "sonarr": sonarr_id}  # type: ignore[return-value]


def resolve_profiles(db: Session) -> dict:
    """Return profile ids, verifying they exist; fall back to HD 720/1080 if missing."""
    cfg = service_cfg(db)
    ids = quality_profile_ids(db)
    cap = ids["cap"] if isinstance(ids.get("cap"), str) else normalize_resolution_cap(cfg.get("resolution_cap"))
    mapping = PROFILE_MAP[cap]
    radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
    radarr_id = mapping["radarr"]
    sonarr_id = mapping["sonarr"]
    try:
        r_ids = {p["id"] for p in radarr.quality_profiles()}
        if radarr_id not in r_ids:
            radarr_id = mapping["fallback_radarr"]
    except Exception:
        radarr_id = mapping["fallback_radarr"]
    try:
        s_ids = {p["id"] for p in sonarr.quality_profiles()}
        if sonarr_id not in s_ids:
            sonarr_id = mapping["fallback_sonarr"]
    except Exception:
        sonarr_id = mapping["fallback_sonarr"]
    return {"resolution_cap": cap, "radarr_quality_profile_id": radarr_id, "sonarr_quality_profile_id": sonarr_id}
