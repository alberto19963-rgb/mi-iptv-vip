from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Person, Recommendation, WatchEvent
from app.services.jellyfin import JellyfinClient
from app.services.preferences import (
    preference_signal_count,
    score_candidate,
    top_preferences,
    disliked_preferences,
)
from app.services.radarr import RadarrClient
from app.services.settings_store import log_error, service_cfg
from app.services.sonarr import SonarrClient
from app.services.tmdb import TMDbClient
from app.services.watch_sync import seed_library_prefs_for_person


def _library_tmdb_ids(jf: JellyfinClient) -> set[int]:
    ids: set[int] = set()
    try:
        for item in jf.get_library_movies() + jf.get_library_series():
            providers = item.get("ProviderIds") or {}
            raw = providers.get("Tmdb") or providers.get("tmdb")
            if raw and str(raw).isdigit():
                ids.add(int(raw))
    except Exception:
        pass
    return ids


def _blocked_tmdb_ids(db: Session, person_id: int) -> set[int]:
    blocked: set[int] = set()
    for w in db.query(WatchEvent).filter(WatchEvent.person_id == person_id, WatchEvent.tmdb_id.isnot(None)).all():
        blocked.add(int(w.tmdb_id))
    for r in (
        db.query(Recommendation)
        .filter(
            Recommendation.person_id == person_id,
            Recommendation.status.in_(["rejected", "already_seen", "downloaded", "liked", "disliked", "sent", "kept"]),
        )
        .all()
    ):
        blocked.add(int(r.tmdb_id))
    return blocked


def desired_recs_this_week(person: Person, max_cap: int) -> int:
    pace = float(person.movies_per_week or 1.0)
    # Adaptive: if they ignore, movies_per_week drops via fewer watches
    n = int(round(pace))
    return max(1, min(n, person.recs_cap or max_cap, max_cap))


def sent_this_week(db: Session, person_id: int) -> int:
    since = datetime.utcnow() - timedelta(days=7)
    return (
        db.query(Recommendation)
        .filter(Recommendation.person_id == person_id, Recommendation.sent_at >= since)
        .count()
    )


def _arr_resolvable(media_type: str, tmdb_id: int, radarr: RadarrClient, sonarr: SonarrClient) -> bool:
    """True if Radarr/Sonarr can resolve this TMDb id (same path used on Descargar).

    TMDb is metadata only; downloads always go through *arr → Prowlarr/indexers.
    Prefer titles the *arr stack can look up so Descargar is more likely to work.
    """
    try:
        if media_type == "tv":
            if not sonarr.configured():
                return True  # don't block suggestions if Sonarr offline
            return sonarr.lookup_tmdb(tmdb_id) is not None
        if not radarr.configured():
            return True
        return radarr.lookup_tmdb(tmdb_id) is not None
    except Exception:
        return True  # fail open: still suggest; download path will report errors


def generate_recommendations(db: Session, person: Person, *, media_type: str = "movie", count: int = 3) -> list[Recommendation]:
    cfg = service_cfg(db)
    tmdb = TMDbClient(cfg["tmdb_api_key"])
    if not tmdb.configured():
        return []

    jf = JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"])
    # Cold-start: seed soft prefs from library before discover if still empty
    if jf.configured() and preference_signal_count(db, person.id) == 0:
        try:
            seed_library_prefs_for_person(db, person, jf)
        except Exception:
            pass

    library_ids = _library_tmdb_ids(jf)  # already owned → don't recommend again
    blocked = _blocked_tmdb_ids(db, person.id) | library_ids

    radarr = RadarrClient(cfg["radarr_url"], cfg["radarr_api_key"])
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])

    liked = [p.value for p in top_preferences(db, person.id, "genre", 5) if p.score > 0]
    disliked = [p.value for p in disliked_preferences(db, person.id, "genre", 5) if p.score < 0]

    with_genres = None
    if liked:
        ids = []
        for name in liked[:3]:
            gid = tmdb.GENRE_NAME_TO_ID.get(name.lower())
            if gid:
                ids.append(str(gid))
        if ids:
            with_genres = "|".join(ids)  # OR

    without_genres = None
    if disliked:
        ids = []
        for name in disliked[:3]:
            gid = tmdb.GENRE_NAME_TO_ID.get(name.lower())
            if gid:
                ids.append(str(gid))
        if ids:
            without_genres = ",".join(ids)

    candidates: list[dict] = []
    for page in range(1, 5):
        if media_type == "tv":
            batch = tmdb.discover_tv(with_genres=with_genres, without_genres=without_genres, page=page)
        else:
            batch = tmdb.discover_movies(with_genres=with_genres, without_genres=without_genres, page=page)
        candidates.extend(batch)

    scored: list[tuple[float, dict]] = []
    for c in candidates:
        tid = c.get("id")
        if not tid or tid in blocked:
            continue
        genre_ids = c.get("genre_ids") or []
        id_to_name = {}
        for name, gid in tmdb.GENRE_NAME_TO_ID.items():
            id_to_name.setdefault(gid, name)
        genres = [id_to_name.get(g, str(g)) for g in genre_ids]
        year_raw = (c.get("release_date") or c.get("first_air_date") or "")[:4]
        year = int(year_raw) if year_raw.isdigit() else None
        s = score_candidate(db, person.id, genres, year) + float(c.get("vote_average") or 0) * 0.1
        scored.append((s, c))

    scored.sort(key=lambda x: x[0], reverse=True)
    created: list[Recommendation] = []
    # Check more candidates than needed so we can skip ones *arr cannot resolve
    for s, c in scored:
        if len(created) >= count:
            break
        tid = int(c["id"])
        if tid in blocked:
            continue
        if not _arr_resolvable(media_type, tid, radarr, sonarr):
            continue
        existing = (
            db.query(Recommendation)
            .filter_by(person_id=person.id, media_type=media_type, tmdb_id=tid)
            .one_or_none()
        )
        if existing:
            continue
        title = c.get("title") or c.get("name") or ""
        year_raw = (c.get("release_date") or c.get("first_air_date") or "")[:4]
        year = int(year_raw) if year_raw.isdigit() else None
        rec = Recommendation(
            person_id=person.id,
            media_type=media_type,
            tmdb_id=tid,
            title=title,
            year=year,
            overview=c.get("overview") or "",
            poster_path=c.get("poster_path"),
            score=s,
            status="pending",
        )
        db.add(rec)
        created.append(rec)
        blocked.add(tid)
    db.commit()
    return created


def prepare_weekly_recommendations(db: Session) -> dict:
    cfg = service_cfg(db)
    if not cfg["tmdb_api_key"]:
        return {"ok": False, "error": "TMDb no configurado"}
    people = db.query(Person).filter(Person.active.is_(True), Person.paused.is_(False)).all()
    total = 0
    for person in people:
        if not person.telegram_chat_id:
            continue
        try:
            want = desired_recs_this_week(person, cfg["max_recs_per_week"])
            already = sent_this_week(db, person.id)
            pending = (
                db.query(Recommendation)
                .filter_by(person_id=person.id, status="pending")
                .count()
            )
            need = max(0, want - already - pending)
            if need > 0:
                movies = generate_recommendations(db, person, media_type="movie", count=need)
                # occasionally include a series if pace >= 2
                if person.movies_per_week >= 2 and need >= 2:
                    generate_recommendations(db, person, media_type="tv", count=1)
                total += len(movies)
        except Exception as exc:
            log_error(db, "recommendations", f"Error recs {person.name}", str(exc))
    return {"ok": True, "created": total}
