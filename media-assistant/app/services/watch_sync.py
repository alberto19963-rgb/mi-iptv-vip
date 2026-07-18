from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import Person, WatchEvent
from app.services.jellyfin import JellyfinClient
from app.services.preferences import (
    compute_weekly_pace,
    learn_from_watches,
    seed_preferences_from_library,
)
from app.services.settings_store import log_error, service_cfg


def _parse_jelly_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def seed_library_prefs_for_person(db: Session, person: Person, jf: JellyfinClient) -> dict[str, Any]:
    """Soft prior from library movies when the person still has no preference signals.

    Played watches are learned first (stronger). Library presence is weaker and
    only fills a cold start. Items already in the library are also excluded from
    recommendations via _library_tmdb_ids (owned / don't recommend again).
    """
    if not person.jellyfin_user_id:
        movies = jf.get_library_movies()
    else:
        movies = jf.get_user_library_movies(person.jellyfin_user_id)
        if not movies:
            # Shared Movies library fallback
            movies = jf.get_library_movies()
    return seed_preferences_from_library(db, person, movies)


def sync_person_watches(db: Session, person: Person, jf: JellyfinClient) -> int:
    if not person.jellyfin_user_id:
        return 0
    items = jf.get_user_items(person.jellyfin_user_id)
    updated = 0
    for item in items:
        userdata = item.get("UserData") or {}
        if not userdata.get("Played") and not userdata.get("PlayCount"):
            continue
        item_id = item.get("Id")
        if not item_id:
            continue
        providers = item.get("ProviderIds") or {}
        tmdb_raw = providers.get("Tmdb") or providers.get("tmdb")
        tmdb_id = int(tmdb_raw) if tmdb_raw and str(tmdb_raw).isdigit() else None
        genres = ",".join(item.get("Genres") or [])
        row = (
            db.query(WatchEvent)
            .filter_by(person_id=person.id, jellyfin_item_id=item_id)
            .one_or_none()
        )
        last = _parse_jelly_date(userdata.get("LastPlayedDate"))
        payload = {
            "item_type": item.get("Type") or "Movie",
            "title": item.get("Name") or "",
            "series_name": item.get("SeriesName"),
            "season_number": item.get("ParentIndexNumber"),
            "episode_number": item.get("IndexNumber"),
            "tmdb_id": tmdb_id,
            "year": item.get("ProductionYear"),
            "genres": genres,
            "completed": bool(userdata.get("Played")),
            "play_count": int(userdata.get("PlayCount") or 1),
            "last_watched_at": last,
        }
        if row is None:
            db.add(WatchEvent(person_id=person.id, jellyfin_item_id=item_id, **payload))
        else:
            for k, v in payload.items():
                setattr(row, k, v)
        updated += 1
    db.commit()
    compute_weekly_pace(db, person)
    # Stronger signal first (Played). Then soft library seed if still cold-start.
    learn_from_watches(db, person)
    try:
        seed_library_prefs_for_person(db, person, jf)
    except Exception as exc:
        log_error(db, "watch_sync", f"Library seed failed for {person.name}", str(exc))
    _mark_watched_movie_downloads(db, person)
    return updated


def _mark_watched_movie_downloads(db: Session, person: Person) -> None:
    """Drive movie retention from Jellyfin watched state."""
    from app.services.downloads import mark_download_watched

    events = (
        db.query(WatchEvent)
        .filter(
            WatchEvent.person_id == person.id,
            WatchEvent.item_type == "Movie",
            WatchEvent.completed.is_(True),
            WatchEvent.tmdb_id.isnot(None),
        )
        .all()
    )
    for w in events:
        try:
            mark_download_watched(db, person.id, int(w.tmdb_id), "movie")
        except Exception:
            pass


def sync_all_watches(db: Session) -> dict:
    cfg = service_cfg(db)
    jf = JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"])
    if not jf.configured():
        return {"ok": False, "error": "Jellyfin no configurado"}
    people = db.query(Person).filter(Person.active.is_(True), Person.jellyfin_user_id.isnot(None)).all()
    total = 0
    for person in people:
        try:
            total += sync_person_watches(db, person, jf)
        except Exception as exc:
            log_error(db, "watch_sync", f"Error sync {person.name}", str(exc))
    return {"ok": True, "events": total, "people": len(people)}
