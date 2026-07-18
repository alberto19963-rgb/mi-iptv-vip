from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.services.radarr import RadarrClient
from app.services.settings_store import service_cfg


# Conservative estimates when *arr has no size yet
EST_MOVIE_GB = {"720p": 3.5, "1080p": 6.0}
EST_EPISODE_GB = {"720p": 0.7, "1080p": 1.2}


def multimedia_free_gb(db: Session) -> Optional[float]:
    """Free space on media volume via Radarr diskspace (covers /movies root on Multimedia)."""
    cfg = service_cfg(db)
    radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
    if not radarr.configured():
        return None
    try:
        spaces = radarr.disk_space()
        values = []
        for s in spaces:
            free = s.get("freeSpace")
            if free is not None:
                values.append(free / (1024**3))
        return round(min(values), 2) if values else None
    except Exception:
        return None


def estimate_size_gb(media_type: str, resolution_cap: str = "1080p", episode_count: int = 1) -> float:
    cap = resolution_cap if resolution_cap in ("720p", "1080p") else "1080p"
    if media_type == "tv":
        return round(EST_EPISODE_GB[cap] * max(1, episode_count), 2)
    return EST_MOVIE_GB[cap]


def can_fit_download(
    db: Session,
    *,
    media_type: str,
    resolution_cap: str = "1080p",
    episode_count: int = 1,
    estimated_gb: float | None = None,
) -> dict:
    """Check whether a download would leave enough free space above threshold."""
    cfg = service_cfg(db)
    threshold = float(cfg.get("disk_threshold_gb") or 50)
    free = multimedia_free_gb(db)
    need = estimated_gb if estimated_gb is not None else estimate_size_gb(media_type, resolution_cap, episode_count)
    if free is None:
        return {
            "ok": True,
            "unknown": True,
            "free_gb": None,
            "need_gb": need,
            "threshold_gb": threshold,
            "message": "No se pudo leer el espacio libre; se permite con precaución.",
        }
    remaining = free - need
    ok = remaining >= threshold
    return {
        "ok": ok,
        "unknown": False,
        "free_gb": free,
        "need_gb": need,
        "threshold_gb": threshold,
        "remaining_after_gb": round(remaining, 2),
        "message": (
            None
            if ok
            else (
                f"Disco bajo: {free:.1f} GB libres. Esta descarga ~{need:.1f} GB "
                f"dejaría {remaining:.1f} GB (mínimo {threshold:.0f} GB)."
            )
        ),
    }
