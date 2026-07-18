from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import DeletionLog, DownloadInterest, DownloadRequest
from app.services.radarr import RadarrClient
from app.services.ratings import all_watchers_rating_ready, prepare_pre_delete_ratings
from app.services.settings_store import log_error, service_cfg
from app.services.sonarr import SonarrClient


def _all_interests_resolved(db: Session, dl: DownloadRequest) -> bool:
    interests = db.query(DownloadInterest).filter_by(download_id=dl.id).all()
    if not interests:
        return bool(dl.watched_at) or dl.status == "kept"
    for i in interests:
        if i.keep:
            return False
        if not (i.watched or i.declined):
            return False
    return True


def _anyone_keep(db: Session, dl: DownloadRequest) -> bool:
    if dl.keep:
        return True
    return db.query(DownloadInterest).filter_by(download_id=dl.id, keep=True).count() > 0


def delete_via_arr(db: Session, dl: DownloadRequest, reason: str) -> bool:
    cfg = service_cfg(db)
    try:
        if dl.media_type == "movie" and dl.radarr_id:
            RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"]).delete_movie(dl.radarr_id, delete_files=True)
        elif dl.media_type == "tv" and dl.sonarr_id:
            sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
            episodes = sonarr.episodes(dl.sonarr_id)
            season = dl.season_number or 1
            for ep in episodes:
                if ep.get("seasonNumber") == season and ep.get("episodeFileId"):
                    sonarr.delete_episode_file(ep["episodeFileId"])
        else:
            return False
        db.add(
            DeletionLog(
                media_type=dl.media_type,
                tmdb_id=dl.tmdb_id,
                title=dl.title,
                reason=reason,
                radarr_id=dl.radarr_id,
                sonarr_id=dl.sonarr_id,
            )
        )
        dl.status = "deleted"
        dl.delete_after = None
        db.commit()
        return True
    except Exception as exc:
        log_error(db, "retention", f"No se pudo borrar {dl.title}", str(exc))
        return False


def run_retention(db: Session) -> dict:
    """
    Retention with pre-delete star ratings:
    1) Ask watchers for 1–5 stars when delete_after is due
    2) Only delete when multi-user safety + ratings done (or 24h timeout)
    """
    now = datetime.utcnow()
    prompts = prepare_pre_delete_ratings(db)
    deleted = 0
    waiting_ratings = 0
    candidates = (
        db.query(DownloadRequest)
        .filter(
            DownloadRequest.managed_by_us.is_(True),
            DownloadRequest.status.in_(["completed"]),
            DownloadRequest.delete_after.isnot(None),
            DownloadRequest.delete_after <= now,
        )
        .all()
    )
    for dl in candidates:
        if _anyone_keep(db, dl):
            dl.status = "kept"
            dl.delete_after = None
            continue
        if not _all_interests_resolved(db, dl):
            continue
        if not all_watchers_rating_ready(db, dl):
            waiting_ratings += 1
            continue
        if delete_via_arr(db, dl, reason=f"retención tras ver + valoración ({dl.delete_after})"):
            deleted += 1
    db.commit()
    return {"deleted": deleted, "rating_prompts": len(prompts), "waiting_ratings": waiting_ratings}


def emergency_disk_cleanup(db: Session) -> dict:
    cfg = service_cfg(db)
    radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
    if not radarr.configured():
        return {"ok": False, "error": "Radarr no configurado"}
    spaces = radarr.disk_space()
    free_gb = None
    for s in spaces:
        free = s.get("freeSpace")
        if free is not None:
            gb = free / (1024**3)
            free_gb = gb if free_gb is None else min(free_gb, gb)
    if free_gb is None:
        return {"ok": False, "error": "Sin datos de disco"}
    if free_gb >= cfg["disk_threshold_gb"]:
        return {"ok": True, "free_gb": round(free_gb, 1), "deleted": 0}

    candidates = (
        db.query(DownloadRequest)
        .filter(
            DownloadRequest.managed_by_us.is_(True),
            DownloadRequest.status == "completed",
            DownloadRequest.watched_at.isnot(None),
        )
        .order_by(DownloadRequest.watched_at.asc())
        .all()
    )
    deleted = 0
    for dl in candidates:
        if _anyone_keep(db, dl):
            continue
        if not _all_interests_resolved(db, dl):
            continue
        # Emergency: skip waiting for ratings
        if delete_via_arr(db, dl, reason=f"limpieza emergencia disco ({free_gb:.1f} GB libres)"):
            deleted += 1
            if deleted >= 3:
                break
    return {"ok": True, "free_gb": round(free_gb, 1), "deleted": deleted}


def disk_free_gb(db: Session) -> float | None:
    cfg = service_cfg(db)
    radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
    if not radarr.configured():
        return None
    try:
        spaces = radarr.disk_space()
        values = [s.get("freeSpace", 0) / (1024**3) for s in spaces if s.get("freeSpace") is not None]
        return round(min(values), 1) if values else None
    except Exception:
        return None
