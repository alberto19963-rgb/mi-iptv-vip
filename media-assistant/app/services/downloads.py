from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import DownloadInterest, DownloadRequest, Person, Recommendation
from app.services.disk import can_fit_download, estimate_size_gb
from app.services.quality import resolve_profiles
from app.services.radarr import RadarrClient
from app.services.series_rolling import ensure_buffer_downloads, start_series_roll
from app.services.settings_store import log_error, service_cfg
from app.services.sonarr import SonarrClient
from app.services.retention import emergency_disk_cleanup


class DiskBlockedError(Exception):
    def __init__(self, message: str, fit: dict):
        super().__init__(message)
        self.fit = fit


def request_download(
    db: Session,
    person: Person,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    year: int | None = None,
    season_number: int = 1,
    force: bool = False,
) -> DownloadRequest:
    cfg = service_cfg(db)
    profiles = resolve_profiles(db)
    buffer = int(cfg.get("series_buffer_episodes") or 3)
    est = estimate_size_gb(
        media_type,
        profiles["resolution_cap"],
        episode_count=buffer if media_type == "tv" else 1,
    )

    fit = can_fit_download(
        db,
        media_type=media_type,
        resolution_cap=profiles["resolution_cap"],
        episode_count=buffer if media_type == "tv" else 1,
        estimated_gb=est,
    )
    if not fit["ok"] and not force:
        # Try emergency cleanup once, then re-check
        emergency_disk_cleanup(db)
        fit = can_fit_download(
            db,
            media_type=media_type,
            resolution_cap=profiles["resolution_cap"],
            episode_count=buffer if media_type == "tv" else 1,
            estimated_gb=est,
        )
        if not fit["ok"]:
            dl = DownloadRequest(
                person_id=person.id,
                media_type=media_type,
                tmdb_id=tmdb_id,
                title=title,
                year=year,
                season_number=season_number if media_type == "tv" else None,
                status="blocked_disk",
                managed_by_us=True,
                estimated_size_gb=est,
                resolution_cap=profiles["resolution_cap"],
                quality_profile_id=(
                    profiles["sonarr_quality_profile_id"]
                    if media_type == "tv"
                    else profiles["radarr_quality_profile_id"]
                ),
            )
            db.add(dl)
            db.commit()
            raise DiskBlockedError(fit.get("message") or "Disco insuficiente", fit)

    existing = (
        db.query(DownloadRequest)
        .filter(
            DownloadRequest.tmdb_id == tmdb_id,
            DownloadRequest.media_type == media_type,
            DownloadRequest.status.in_(["requested", "downloading", "completed", "kept"]),
        )
        .first()
    )
    if existing:
        interest = (
            db.query(DownloadInterest)
            .filter_by(download_id=existing.id, person_id=person.id)
            .one_or_none()
        )
        if interest is None:
            db.add(DownloadInterest(download_id=existing.id, person_id=person.id))
            db.commit()
        if media_type == "tv" and existing.sonarr_id:
            roll = start_series_roll(
                db,
                person,
                tmdb_id=tmdb_id,
                title=title,
                season_number=season_number,
                sonarr_id=existing.sonarr_id,
            )
            ensure_buffer_downloads(db, roll)
        return existing

    dl = DownloadRequest(
        person_id=person.id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        title=title,
        year=year,
        season_number=season_number if media_type == "tv" else None,
        status="requested",
        managed_by_us=True,
        estimated_size_gb=est,
        resolution_cap=profiles["resolution_cap"],
        quality_profile_id=(
            profiles["sonarr_quality_profile_id"]
            if media_type == "tv"
            else profiles["radarr_quality_profile_id"]
        ),
    )
    db.add(dl)
    db.flush()
    db.add(DownloadInterest(download_id=dl.id, person_id=person.id))

    try:
        if media_type == "tv":
            sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
            # Add series without grabbing whole season; rolling buffer searches first N
            result = sonarr.add_series(
                tmdb_id,
                profiles["sonarr_quality_profile_id"],
                cfg["sonarr_root_folder"],
                season_number=season_number,
                search=False,
            )
            dl.sonarr_id = result.get("id")
            dl.status = "downloading"
            roll = start_series_roll(
                db,
                person,
                tmdb_id=tmdb_id,
                title=title,
                season_number=season_number,
                sonarr_id=dl.sonarr_id,
            )
            ensure_buffer_downloads(db, roll)
        else:
            radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
            result = radarr.add_movie(
                tmdb_id,
                profiles["radarr_quality_profile_id"],
                cfg["radarr_root_folder"],
            )
            dl.radarr_id = result.get("id")
            if result.get("hasFile"):
                dl.status = "completed"
                dl.downloaded_at = datetime.utcnow()
            else:
                dl.status = "downloading"
    except Exception as exc:
        dl.status = "failed"
        log_error(db, "download", f"Fallo al pedir {title}", str(exc))

    rec = (
        db.query(Recommendation)
        .filter_by(person_id=person.id, media_type=media_type, tmdb_id=tmdb_id)
        .one_or_none()
    )
    if rec:
        rec.status = "downloaded"
        rec.feedback_at = datetime.utcnow()

    db.commit()
    db.refresh(dl)
    return dl


def poll_download_status(db: Session) -> dict:
    cfg = service_cfg(db)
    radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
    # Return IDs only — session closes after poll; ORM instances would be detached.
    ready_ids: list[int] = []

    pending = (
        db.query(DownloadRequest)
        .filter(DownloadRequest.status.in_(["requested", "downloading"]), DownloadRequest.managed_by_us.is_(True))
        .all()
    )
    for dl in pending:
        try:
            if dl.media_type == "movie" and dl.radarr_id and radarr.configured():
                movie = radarr.get_movie(dl.radarr_id)
                if movie.get("hasFile"):
                    dl.status = "completed"
                    dl.downloaded_at = datetime.utcnow()
                    if not dl.notified_ready:
                        ready_ids.append(int(dl.id))
            elif dl.media_type == "tv" and dl.sonarr_id and sonarr.configured():
                # Series "ready" when first buffer episode is on disk
                episodes = sonarr.episodes(dl.sonarr_id)
                season = dl.season_number or 1
                buffer = int(cfg.get("series_buffer_episodes") or 3)
                first = [
                    e
                    for e in episodes
                    if e.get("seasonNumber") == season and 1 <= (e.get("episodeNumber") or 0) <= buffer
                ]
                if first and any(e.get("hasFile") for e in first):
                    if dl.status != "completed":
                        dl.status = "completed"
                        dl.downloaded_at = datetime.utcnow()
                        if not dl.notified_ready:
                            ready_ids.append(int(dl.id))
        except Exception as exc:
            log_error(db, "download_poll", f"Error poll {dl.title}", str(exc))
    db.commit()
    return {"ready": ready_ids}


def mark_download_watched(db: Session, person_id: int, tmdb_id: int, media_type: str = "movie") -> None:
    cfg = service_cfg(db)
    retention = cfg["retention_days"]
    dls = (
        db.query(DownloadRequest)
        .filter(
            DownloadRequest.tmdb_id == tmdb_id,
            DownloadRequest.media_type == media_type,
            DownloadRequest.status.in_(["completed", "downloading", "requested", "kept"]),
        )
        .all()
    )
    now = datetime.utcnow()
    for dl in dls:
        interest = (
            db.query(DownloadInterest)
            .filter_by(download_id=dl.id, person_id=person_id)
            .one_or_none()
        )
        if interest:
            interest.watched = True
            interest.watched_at = now
        if dl.person_id == person_id:
            dl.watched_at = now
            if not dl.keep and dl.status != "kept" and media_type == "movie":
                dl.delete_after = now + timedelta(days=retention)
    db.commit()


def set_keep(db: Session, download_id: int, person_id: int | None = None) -> None:
    dl = db.get(DownloadRequest, download_id)
    if not dl:
        return
    dl.keep = True
    dl.status = "kept"
    dl.delete_after = None
    if person_id:
        interest = (
            db.query(DownloadInterest)
            .filter_by(download_id=dl.id, person_id=person_id)
            .one_or_none()
        )
        if interest:
            interest.keep = True
        if dl.media_type == "tv":
            from app.models import SeriesRoll

            roll = (
                db.query(SeriesRoll)
                .filter_by(person_id=person_id, tmdb_id=dl.tmdb_id)
                .one_or_none()
            )
            if roll:
                roll.keep = True
    db.commit()
