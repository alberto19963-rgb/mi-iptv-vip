from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    DeletionLog,
    EpisodeFileState,
    EpisodeInterest,
    Person,
    SeriesRoll,
    WatchEvent,
)
from app.services.disk import can_fit_download
from app.services.quality import resolve_profiles
from app.services.settings_store import log_error, service_cfg
from app.services.sonarr import SonarrClient


def start_series_roll(
    db: Session,
    person: Person,
    *,
    tmdb_id: int,
    title: str,
    season_number: int = 1,
    sonarr_id: int | None = None,
) -> SeriesRoll:
    cfg = service_cfg(db)
    buffer = int(cfg.get("series_buffer_episodes") or 3)
    roll = (
        db.query(SeriesRoll)
        .filter_by(person_id=person.id, tmdb_id=tmdb_id)
        .one_or_none()
    )
    if roll is None:
        roll = SeriesRoll(
            person_id=person.id,
            tmdb_id=tmdb_id,
            title=title,
            sonarr_id=sonarr_id,
            season_number=season_number,
            last_watched_episode=0,
            buffer_ahead=buffer,
            active=True,
        )
        db.add(roll)
    else:
        roll.active = True
        roll.sonarr_id = sonarr_id or roll.sonarr_id
        roll.season_number = season_number
        roll.buffer_ahead = buffer
        roll.title = title or roll.title
    db.commit()
    db.refresh(roll)
    return roll


def _upsert_episode(
    db: Session,
    *,
    tmdb_id: int,
    sonarr_id: int,
    title: str,
    season: int,
    episode: int,
    person_id: int,
    status: str = "wanted",
    episode_file_id: int | None = None,
) -> EpisodeFileState:
    row = (
        db.query(EpisodeFileState)
        .filter_by(sonarr_id=sonarr_id, season_number=season, episode_number=episode)
        .one_or_none()
    )
    if row is None:
        row = EpisodeFileState(
            tmdb_id=tmdb_id,
            sonarr_id=sonarr_id,
            title=title,
            season_number=season,
            episode_number=episode,
            status=status,
            episode_file_id=episode_file_id,
            managed_by_us=True,
        )
        db.add(row)
        db.flush()
    else:
        if episode_file_id:
            row.episode_file_id = episode_file_id
        if status == "ready" or (status == "downloading" and row.status == "wanted"):
            row.status = status
        if status == "ready" and not row.downloaded_at:
            row.downloaded_at = datetime.utcnow()
    interest = (
        db.query(EpisodeInterest)
        .filter_by(episode_id=row.id, person_id=person_id)
        .one_or_none()
    )
    if interest is None:
        db.add(EpisodeInterest(episode_id=row.id, person_id=person_id))
    return row


def ensure_buffer_downloads(db: Session, roll: SeriesRoll) -> dict:
    """Download episodes from last_watched+1 through last_watched+buffer."""
    cfg = service_cfg(db)
    if not roll.active or roll.keep or not roll.sonarr_id:
        return {"ok": False, "reason": "inactive"}
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
    if not sonarr.configured():
        return {"ok": False, "reason": "sonarr"}

    profiles = resolve_profiles(db)
    episodes = sonarr.episodes(roll.sonarr_id)
    season_eps = sorted(
        [e for e in episodes if e.get("seasonNumber") == roll.season_number and (e.get("episodeNumber") or 0) > 0],
        key=lambda e: e.get("episodeNumber") or 0,
    )
    if not season_eps:
        return {"ok": False, "reason": "no_episodes"}

    start = max(1, int(roll.last_watched_episode) + 1)
    end = start + int(roll.buffer_ahead) - 1
    wanted_nums = {e.get("episodeNumber") for e in season_eps if start <= (e.get("episodeNumber") or 0) <= end}

    downloaded = 0
    blocked = 0
    for ep in season_eps:
        num = ep.get("episodeNumber")
        if num not in wanted_nums:
            continue
        has_file = bool(ep.get("hasFile"))
        file_id = ep.get("episodeFileId")
        status = "ready" if has_file else "wanted"
        _upsert_episode(
            db,
            tmdb_id=roll.tmdb_id,
            sonarr_id=roll.sonarr_id,
            title=f"{roll.title} S{roll.season_number:02d}E{num:02d}",
            season=roll.season_number,
            episode=num,
            person_id=roll.person_id,
            status=status,
            episode_file_id=file_id,
        )
        if has_file:
            continue
        # Disk check before searching one episode
        fit = can_fit_download(db, media_type="tv", resolution_cap=profiles["resolution_cap"], episode_count=1)
        if not fit["ok"]:
            blocked += 1
            continue
        try:
            # Monitor + search this episode
            sonarr.monitor_episodes([ep["id"]], monitored=True)
            sonarr.search_episode([ep["id"]])
            row = (
                db.query(EpisodeFileState)
                .filter_by(sonarr_id=roll.sonarr_id, season_number=roll.season_number, episode_number=num)
                .one()
            )
            row.status = "downloading"
            downloaded += 1
        except Exception as exc:
            log_error(db, "series_roll", f"No se pudo pedir ep {roll.title} E{num}", str(exc))
    db.commit()
    return {"ok": True, "searched": downloaded, "blocked_disk": blocked, "window": f"{start}-{end}"}


def sync_rolls_from_watches(db: Session) -> dict:
    """Update last_watched_episode from Jellyfin watch events and advance buffers."""
    cfg = service_cfg(db)
    grace_hours = int(cfg.get("episode_grace_hours") or 12)
    rolls = db.query(SeriesRoll).filter(SeriesRoll.active.is_(True)).all()
    updated = 0
    for roll in rolls:
        watches = (
            db.query(WatchEvent)
            .filter(
                WatchEvent.person_id == roll.person_id,
                WatchEvent.item_type == "Episode",
                WatchEvent.completed.is_(True),
                WatchEvent.season_number == roll.season_number,
            )
            .all()
        )
        # Match by series name loosely if no series_tmdb_id
        relevant = []
        for w in watches:
            if w.series_tmdb_id and w.series_tmdb_id == roll.tmdb_id:
                relevant.append(w)
            elif w.series_name and roll.title and w.series_name.lower() in roll.title.lower():
                relevant.append(w)
            elif w.series_name and roll.title and roll.title.lower() in w.series_name.lower():
                relevant.append(w)
        if not relevant and watches and roll.title:
            # fallback: any episode watch with matching season for this person while roll active
            relevant = [w for w in watches if w.episode_number]

        max_ep = max((w.episode_number or 0 for w in relevant), default=0)
        if max_ep > roll.last_watched_episode:
            roll.last_watched_episode = max_ep
            updated += 1
            # Mark episode interests watched + schedule delete
            eps = (
                db.query(EpisodeFileState)
                .filter(
                    EpisodeFileState.sonarr_id == roll.sonarr_id,
                    EpisodeFileState.season_number == roll.season_number,
                    EpisodeFileState.episode_number <= max_ep,
                    EpisodeFileState.status.in_(["ready", "downloading", "wanted", "watched"]),
                )
                .all()
            )
            now = datetime.utcnow()
            for ep in eps:
                interest = (
                    db.query(EpisodeInterest)
                    .filter_by(episode_id=ep.id, person_id=roll.person_id)
                    .one_or_none()
                )
                if interest is None:
                    interest = EpisodeInterest(episode_id=ep.id, person_id=roll.person_id)
                    db.add(interest)
                    db.flush()
                interest.watched = True
                interest.watched_at = now
                if not ep.keep and ep.status != "kept":
                    ep.status = "watched"
                    if not ep.delete_after:
                        ep.delete_after = now + timedelta(hours=grace_hours)
        ensure_buffer_downloads(db, roll)
    db.commit()
    return {"rolls": len(rolls), "advanced": updated}


def _episode_safe_to_delete(db: Session, ep: EpisodeFileState) -> bool:
    if ep.keep or ep.status == "kept":
        return False
    interests = db.query(EpisodeInterest).filter_by(episode_id=ep.id).all()
    if not interests:
        return True
    if any(i.keep for i in interests):
        return False
    # All interested people must have watched (or no longer active on that series)
    for i in interests:
        if not i.watched:
            # Still needed if person has an active roll for this series
            roll = (
                db.query(SeriesRoll)
                .filter_by(person_id=i.person_id, tmdb_id=ep.tmdb_id, active=True)
                .one_or_none()
            )
            if roll and not roll.keep:
                return False
    return True


def cleanup_watched_episodes(db: Session) -> dict:
    """Delete watched episode files (rolling window). Mid-season OK; end-of-season waits for stars."""
    from app.services.ratings import series_roll_rating_ready, prepare_series_completion_ratings

    prepare_series_completion_ratings(db)
    cfg = service_cfg(db)
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
    now = datetime.utcnow()
    candidates = (
        db.query(EpisodeFileState)
        .filter(
            EpisodeFileState.managed_by_us.is_(True),
            EpisodeFileState.status == "watched",
            EpisodeFileState.delete_after.isnot(None),
            EpisodeFileState.delete_after <= now,
            EpisodeFileState.episode_file_id.isnot(None),
        )
        .all()
    )
    deleted = 0
    for ep in candidates:
        if not _episode_safe_to_delete(db, ep):
            continue
        # If this season is marked complete and awaiting rating, hold the LAST remaining files
        # until stars/timeout — but still allow rolling deletes mid-season (season_complete False).
        rolls = (
            db.query(SeriesRoll)
            .filter(
                SeriesRoll.tmdb_id == ep.tmdb_id,
                SeriesRoll.season_number == ep.season_number,
                SeriesRoll.season_complete.is_(True),
                SeriesRoll.cleanup_after_rating.is_(True),
            )
            .all()
        )
        if rolls and not all(series_roll_rating_ready(r) for r in rolls):
            continue
        try:
            sonarr.delete_episode_file(ep.episode_file_id)
            db.add(
                DeletionLog(
                    media_type="episode",
                    tmdb_id=ep.tmdb_id,
                    title=ep.title,
                    reason="rolling cleanup episodio visto",
                    sonarr_id=ep.sonarr_id,
                )
            )
            ep.status = "deleted"
            ep.episode_file_id = None
            ep.delete_after = None
            deleted += 1
        except Exception as exc:
            log_error(db, "series_roll", f"No se pudo borrar {ep.title}", str(exc))

    # After rating ready on completed seasons: wipe any leftover ready files for that season
    for roll in db.query(SeriesRoll).filter(
        SeriesRoll.season_complete.is_(True),
        SeriesRoll.cleanup_after_rating.is_(True),
        SeriesRoll.keep.is_(False),
    ).all():
        if not series_roll_rating_ready(roll):
            continue
        leftovers = (
            db.query(EpisodeFileState)
            .filter(
                EpisodeFileState.sonarr_id == roll.sonarr_id,
                EpisodeFileState.season_number == roll.season_number,
                EpisodeFileState.episode_file_id.isnot(None),
                EpisodeFileState.status.in_(["ready", "watched"]),
                EpisodeFileState.keep.is_(False),
            )
            .all()
        )
        for ep in leftovers:
            if not _episode_safe_to_delete(db, ep):
                continue
            try:
                sonarr.delete_episode_file(ep.episode_file_id)
                db.add(
                    DeletionLog(
                        media_type="episode",
                        tmdb_id=ep.tmdb_id,
                        title=ep.title,
                        reason="cleanup post-valoración temporada/serie",
                        sonarr_id=ep.sonarr_id,
                    )
                )
                ep.status = "deleted"
                ep.episode_file_id = None
                ep.delete_after = None
                deleted += 1
            except Exception as exc:
                log_error(db, "series_roll", f"No se pudo borrar {ep.title}", str(exc))
        if roll.series_complete:
            roll.active = False
        roll.cleanup_after_rating = False

    db.commit()
    return {"deleted": deleted}


def refresh_episode_file_ids(db: Session) -> int:
    cfg = service_cfg(db)
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
    if not sonarr.configured():
        return 0
    updated = 0
    pending = (
        db.query(EpisodeFileState)
        .filter(EpisodeFileState.status.in_(["wanted", "downloading"]), EpisodeFileState.managed_by_us.is_(True))
        .all()
    )
    by_series: dict[int, list] = {}
    for ep in pending:
        by_series.setdefault(ep.sonarr_id, []).append(ep)
    for sid, eps in by_series.items():
        try:
            remote = sonarr.episodes(sid)
            index = {(e.get("seasonNumber"), e.get("episodeNumber")): e for e in remote}
            for ep in eps:
                rem = index.get((ep.season_number, ep.episode_number))
                if rem and rem.get("hasFile"):
                    ep.episode_file_id = rem.get("episodeFileId")
                    ep.status = "ready"
                    ep.downloaded_at = datetime.utcnow()
                    updated += 1
        except Exception as exc:
            log_error(db, "series_roll", f"refresh series {sid}", str(exc))
    db.commit()
    return updated
