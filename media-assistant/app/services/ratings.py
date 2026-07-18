from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import DownloadInterest, DownloadRequest, Person, RatingPrompt, SeriesRoll, WatchEvent
from app.services.preferences import apply_star_rating
from app.services.settings_store import service_cfg
from app.services.sonarr import SonarrClient
from app.services.tmdb import TMDbClient

RATING_TIMEOUT_HOURS = 24


def _poster_for(db: Session, media_type: str, tmdb_id: int) -> Optional[str]:
    cfg = service_cfg(db)
    tmdb = TMDbClient(cfg.get("tmdb_api_key") or "")
    if not tmdb.configured():
        return None
    try:
        detail = tmdb.movie(tmdb_id) if media_type == "movie" else tmdb.tv(tmdb_id)
        return detail.get("poster_path")
    except Exception:
        return None


def create_rating_prompt(
    db: Session,
    person: Person,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    year: int | None = None,
    download_id: int | None = None,
    series_roll_id: int | None = None,
    recommendation_id: int | None = None,
    context: str = "pre_delete",
    poster_path: str | None = None,
) -> RatingPrompt:
    timeout_h = int(service_cfg(db).get("rating_timeout_hours") or RATING_TIMEOUT_HOURS)
    now = datetime.utcnow()
    existing = None
    if download_id:
        existing = (
            db.query(RatingPrompt)
            .filter_by(person_id=person.id, download_id=download_id, status="pending")
            .one_or_none()
        )
    elif series_roll_id:
        existing = (
            db.query(RatingPrompt)
            .filter_by(person_id=person.id, series_roll_id=series_roll_id, status="pending")
            .one_or_none()
        )
    if existing:
        return existing

    poster = poster_path or _poster_for(db, media_type, tmdb_id)
    prompt = RatingPrompt(
        person_id=person.id,
        download_id=download_id,
        series_roll_id=series_roll_id,
        recommendation_id=recommendation_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        title=title,
        year=year,
        poster_path=poster,
        context=context,
        status="pending",
        asked_at=now,
        timeout_at=now + timedelta(hours=timeout_h),
    )
    db.add(prompt)
    db.flush()

    if download_id:
        interest = (
            db.query(DownloadInterest)
            .filter_by(download_id=download_id, person_id=person.id)
            .one_or_none()
        )
        if interest:
            interest.rating_asked_at = now
            interest.rating_timeout_at = prompt.timeout_at
    if series_roll_id:
        roll = db.get(SeriesRoll, series_roll_id)
        if roll:
            roll.rating_prompt_id = prompt.id
            roll.rating_asked_at = now
            roll.rating_timeout_at = prompt.timeout_at
            roll.cleanup_after_rating = True
    db.commit()
    db.refresh(prompt)
    return prompt


def record_star_rating(db: Session, prompt_id: int, person_id: int, stars: int) -> RatingPrompt | None:
    stars = max(1, min(5, int(stars)))
    prompt = db.get(RatingPrompt, prompt_id)
    if not prompt or prompt.person_id != person_id:
        return None
    if prompt.status != "pending":
        return prompt

    prompt.stars = stars
    prompt.status = "rated"
    prompt.answered_at = datetime.utcnow()

    if prompt.download_id:
        interest = (
            db.query(DownloadInterest)
            .filter_by(download_id=prompt.download_id, person_id=person_id)
            .one_or_none()
        )
        if interest:
            interest.star_rating = stars
            interest.rating_received_at = prompt.answered_at

    if prompt.series_roll_id:
        roll = db.get(SeriesRoll, prompt.series_roll_id)
        if roll:
            roll.star_rating = stars

    genres: list[str] = []
    actors: list[str] = []
    cfg = service_cfg(db)
    tmdb = TMDbClient(cfg.get("tmdb_api_key") or "")
    if tmdb.configured():
        try:
            detail = tmdb.movie(prompt.tmdb_id) if prompt.media_type == "movie" else tmdb.tv(prompt.tmdb_id)
            genres = [g["name"] for g in detail.get("genres") or []]
            cast = (detail.get("credits") or {}).get("cast") or []
            actors = [c["name"] for c in cast[:5]]
        except Exception:
            pass

    apply_star_rating(
        db,
        person_id,
        stars=stars,
        genres=genres,
        year=prompt.year,
        actors=actors,
    )
    db.commit()
    db.refresh(prompt)
    return prompt


def interest_rating_done(interest: DownloadInterest) -> bool:
    if interest.declined:
        return True
    if not interest.watched:
        return True
    if interest.star_rating is not None:
        return True
    if interest.rating_timeout_at and datetime.utcnow() >= interest.rating_timeout_at:
        return True
    if interest.rating_asked_at is None:
        return False
    return False


def all_watchers_rating_ready(db: Session, dl: DownloadRequest) -> bool:
    interests = db.query(DownloadInterest).filter_by(download_id=dl.id).all()
    if not interests:
        return True
    for i in interests:
        if i.watched and not interest_rating_done(i):
            return False
    return True


def series_roll_rating_ready(roll: SeriesRoll) -> bool:
    if not roll.cleanup_after_rating:
        return True
    if roll.star_rating is not None:
        return True
    if roll.rating_timeout_at and datetime.utcnow() >= roll.rating_timeout_at:
        return True
    if roll.rating_asked_at is None:
        return False
    return False


def expire_timed_out_ratings(db: Session) -> int:
    now = datetime.utcnow()
    n = 0
    pending = (
        db.query(RatingPrompt)
        .filter(RatingPrompt.status == "pending", RatingPrompt.timeout_at <= now)
        .all()
    )
    for p in pending:
        p.status = "timed_out"
        p.answered_at = now
        if p.download_id:
            interest = (
                db.query(DownloadInterest)
                .filter_by(download_id=p.download_id, person_id=p.person_id)
                .one_or_none()
            )
            if interest and not interest.rating_timeout_at:
                interest.rating_timeout_at = now
        if p.series_roll_id:
            roll = db.get(SeriesRoll, p.series_roll_id)
            if roll and not roll.rating_timeout_at:
                roll.rating_timeout_at = now
        n += 1
    if n:
        db.commit()
    return n


def prepare_pre_delete_ratings(db: Session) -> list[RatingPrompt]:
    """Movies approaching deletion — ask watchers for stars first."""
    now = datetime.utcnow()
    expire_timed_out_ratings(db)
    candidates = (
        db.query(DownloadRequest)
        .filter(
            DownloadRequest.managed_by_us.is_(True),
            DownloadRequest.status == "completed",
            DownloadRequest.delete_after.isnot(None),
            DownloadRequest.delete_after <= now,
            DownloadRequest.keep.is_(False),
            DownloadRequest.media_type == "movie",
        )
        .all()
    )
    new_prompts: list[RatingPrompt] = []
    for dl in candidates:
        interests = db.query(DownloadInterest).filter_by(download_id=dl.id).all()
        watchers = [i for i in interests if i.watched and not i.keep]
        targets = watchers
        if not targets and dl.watched_at:
            person = db.get(Person, dl.person_id)
            if person:
                # create synthetic via person only
                already = (
                    db.query(RatingPrompt)
                    .filter(
                        RatingPrompt.person_id == person.id,
                        RatingPrompt.download_id == dl.id,
                        RatingPrompt.status.in_(["pending", "rated", "timed_out", "skipped"]),
                    )
                    .first()
                )
                if not already and person.telegram_chat_id:
                    new_prompts.append(
                        create_rating_prompt(
                            db,
                            person,
                            media_type="movie",
                            tmdb_id=dl.tmdb_id,
                            title=dl.title,
                            year=dl.year,
                            download_id=dl.id,
                            context="pre_delete",
                        )
                    )
            continue

        for interest in targets:
            if interest.star_rating is not None:
                continue
            if interest.rating_asked_at and interest.rating_timeout_at and now < interest.rating_timeout_at:
                continue
            if interest.rating_timeout_at and now >= interest.rating_timeout_at:
                continue
            person = db.get(Person, interest.person_id)
            if not person or not person.telegram_chat_id:
                interest.rating_asked_at = now
                interest.rating_timeout_at = now
                continue
            new_prompts.append(
                create_rating_prompt(
                    db,
                    person,
                    media_type="movie",
                    tmdb_id=dl.tmdb_id,
                    title=dl.title,
                    year=dl.year,
                    download_id=dl.id,
                    context="pre_delete",
                )
            )
    db.commit()
    return new_prompts


def detect_season_complete(db: Session, roll: SeriesRoll) -> bool:
    """True when person watched all aired episodes of the roll's season."""
    if not roll.sonarr_id:
        return False
    cfg = service_cfg(db)
    sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
    if not sonarr.configured():
        return False
    try:
        episodes = sonarr.episodes(roll.sonarr_id)
    except Exception:
        return False

    season_eps = [
        e
        for e in episodes
        if e.get("seasonNumber") == roll.season_number and (e.get("episodeNumber") or 0) > 0
    ]
    now = datetime.utcnow()
    available = []
    for e in season_eps:
        air = e.get("airDateUtc") or e.get("airDate")
        has_aired = True
        if air:
            try:
                ad = datetime.fromisoformat(str(air).replace("Z", "+00:00")).replace(tzinfo=None)
                has_aired = ad <= now
            except Exception:
                has_aired = True
        if has_aired or e.get("hasFile"):
            available.append(e)
    if not available:
        return False

    watched_nums: set[int] = set()
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
    for w in watches:
        if not w.episode_number:
            continue
        if w.series_tmdb_id and w.series_tmdb_id == roll.tmdb_id:
            watched_nums.add(int(w.episode_number))
        elif w.series_name and roll.title and (
            w.series_name.lower() in roll.title.lower() or roll.title.lower() in w.series_name.lower()
        ):
            watched_nums.add(int(w.episode_number))
    if roll.last_watched_episode:
        for n in range(1, int(roll.last_watched_episode) + 1):
            watched_nums.add(n)

    needed = {int(e["episodeNumber"]) for e in available if e.get("episodeNumber")}
    return bool(needed) and needed.issubset(watched_nums)


def prepare_series_completion_ratings(db: Session) -> list[RatingPrompt]:
    """Ask stars once when season (or whole series) is finished — never mid-season."""
    expire_timed_out_ratings(db)
    new_prompts: list[RatingPrompt] = []
    rolls = db.query(SeriesRoll).filter(SeriesRoll.active.is_(True), SeriesRoll.keep.is_(False)).all()
    for roll in rolls:
        if roll.star_rating is not None:
            continue
        if roll.rating_asked_at and roll.rating_timeout_at and datetime.utcnow() < roll.rating_timeout_at:
            continue
        if roll.rating_timeout_at and datetime.utcnow() >= roll.rating_timeout_at:
            continue
        if not detect_season_complete(db, roll):
            continue

        roll.season_complete = True
        context = "season_complete"
        try:
            cfg = service_cfg(db)
            sonarr = SonarrClient(cfg["sonarr_url"], cfg["sonarr_api_key"])
            if roll.sonarr_id and sonarr.configured():
                series = None
                for s in sonarr.get_series_list():
                    if s.get("id") == roll.sonarr_id:
                        series = s
                        break
                seasons = [x for x in (series or {}).get("seasons") or [] if (x.get("seasonNumber") or 0) > 0]
                if len(seasons) <= 1:
                    roll.series_complete = True
                    context = "series_complete"
        except Exception:
            pass

        person = db.get(Person, roll.person_id)
        if not person or not person.telegram_chat_id:
            now = datetime.utcnow()
            roll.rating_asked_at = now
            roll.rating_timeout_at = now
            roll.cleanup_after_rating = True
            continue

        label = roll.title if context == "series_complete" else f"{roll.title} (T{roll.season_number})"
        new_prompts.append(
            create_rating_prompt(
                db,
                person,
                media_type="tv",
                tmdb_id=roll.tmdb_id,
                title=label,
                series_roll_id=roll.id,
                context=context,
            )
        )
    db.commit()
    return new_prompts


def pending_prompts_to_send(db: Session) -> list[RatingPrompt]:
    return (
        db.query(RatingPrompt)
        .filter(RatingPrompt.status == "pending")
        .order_by(RatingPrompt.id.asc())
        .limit(30)
        .all()
    )
