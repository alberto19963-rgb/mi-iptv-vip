from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import Person, PreferenceScore, WatchEvent

# Soft prior from "movies they keep in the library". Much weaker than
# completed watches (learn_from_watches ~0.35) or star ratings (1–5★).
# Once the user watches or rates, those stronger signals dominate.
LIBRARY_SEED_GENRE_WEIGHT = 0.08
LIBRARY_SEED_GENRE_CAP = 2.0
LIBRARY_SEED_DECADE_WEIGHT = 0.04
LIBRARY_SEED_DECADE_CAP = 1.0
LIBRARY_SEED_META_KIND = "meta"
LIBRARY_SEED_META_VALUE = "library_seed"


def _upsert_score(db: Session, person_id: int, kind: str, value: str, delta: float) -> None:
    value = (value or "").strip()
    if not value:
        return
    row = (
        db.query(PreferenceScore)
        .filter_by(person_id=person_id, kind=kind, value=value[:120])
        .one_or_none()
    )
    if row is None:
        row = PreferenceScore(person_id=person_id, kind=kind, value=value[:120], score=delta, samples=1)
        db.add(row)
    else:
        row.score = float(row.score) + delta
        row.samples = int(row.samples or 0) + 1


def apply_feedback(
    db: Session,
    person_id: int,
    *,
    liked: Optional[bool],
    genres: list[str] | None = None,
    year: Optional[int] = None,
    actors: list[str] | None = None,
    weight: float = 1.0,
) -> None:
    """liked=True → positive, False → negative, None → mild negative (not interested)."""
    if liked is True:
        delta = 2.0 * weight
    elif liked is False:
        delta = -2.0 * weight
    else:
        delta = -1.0 * weight

    for g in genres or []:
        _upsert_score(db, person_id, "genre", g, delta)
    if year:
        decade = f"{(year // 10) * 10}s"
        _upsert_score(db, person_id, "decade", decade, delta * 0.5)
    for a in (actors or [])[:5]:
        _upsert_score(db, person_id, "actor", a, delta * 0.4)
    db.commit()


def apply_star_rating(
    db: Session,
    person_id: int,
    *,
    stars: int,
    genres: list[str] | None = None,
    year: Optional[int] = None,
    actors: list[str] | None = None,
) -> None:
    """Map 1–5 stars to preference deltas (strong signal)."""
    stars = max(1, min(5, int(stars)))
    # 5→+3, 4→+2, 3→0, 2→-2, 1→-3
    mapping = {5: 3.0, 4: 2.0, 3: 0.0, 2: -2.0, 1: -3.0}
    delta = mapping[stars]
    _upsert_score(db, person_id, "rating", f"{stars}_stars", abs(delta) * 0.1)
    if delta == 0:
        db.commit()
        return
    weight = abs(delta) / 2.0  # 1.0–1.5
    liked = delta > 0
    apply_feedback(
        db,
        person_id,
        liked=liked,
        genres=genres,
        year=year,
        actors=actors,
        weight=weight,
    )


def learn_from_watches(db: Session, person: Person) -> None:
    """Boost genres from completed watches (stronger than library seed, weaker than ★)."""
    since = datetime.utcnow() - timedelta(days=180)
    watches = (
        db.query(WatchEvent)
        .filter(WatchEvent.person_id == person.id, WatchEvent.completed.is_(True), WatchEvent.last_watched_at >= since)
        .all()
    )
    for w in watches:
        genres = [g.strip() for g in (w.genres or "").split(",") if g.strip()]
        apply_feedback(db, person.id, liked=True, genres=genres, year=w.year, weight=0.35)


def preference_signal_count(db: Session, person_id: int) -> int:
    """Count real preference rows (excludes the library_seed meta marker)."""
    return (
        db.query(PreferenceScore)
        .filter(
            PreferenceScore.person_id == person_id,
            ~(
                (PreferenceScore.kind == LIBRARY_SEED_META_KIND)
                & (PreferenceScore.value == LIBRARY_SEED_META_VALUE)
            ),
        )
        .count()
    )


def has_library_seed(db: Session, person_id: int) -> bool:
    return (
        db.query(PreferenceScore)
        .filter_by(person_id=person_id, kind=LIBRARY_SEED_META_KIND, value=LIBRARY_SEED_META_VALUE)
        .first()
        is not None
    )


def seed_preferences_from_library(
    db: Session,
    person: Person,
    movies: list[dict[str, Any]],
) -> dict[str, Any]:
    """Cold-start prior from movies currently in the person's Jellyfin library.

    Library presence ≠ watched. This is only a soft "these are the kinds of
    movies they keep" signal. Prefer Played items via learn_from_watches first;
    call this when the person still has zero preference signals (or to record
    a one-shot seed marker after watches already created prefs — skipped).

    Stronger signals (watch completion, Telegram ★ ratings, like/dislike)
    override this prior by accumulating larger deltas on the same genres.
    """
    if preference_signal_count(db, person.id) > 0:
        return {"seeded": False, "reason": "already_has_signals", "movies": 0, "genres": 0, "decades": 0}
    if has_library_seed(db, person.id):
        return {"seeded": False, "reason": "already_seeded", "movies": 0, "genres": 0, "decades": 0}
    if not movies:
        return {"seeded": False, "reason": "empty_library", "movies": 0, "genres": 0, "decades": 0}

    genre_counts: Counter[str] = Counter()
    decade_counts: Counter[str] = Counter()
    for item in movies:
        for g in item.get("Genres") or []:
            name = str(g).strip()
            if name:
                genre_counts[name] += 1
        year = item.get("ProductionYear")
        if isinstance(year, int) and year > 0:
            decade_counts[f"{(year // 10) * 10}s"] += 1

    for genre, count in genre_counts.items():
        score = min(LIBRARY_SEED_GENRE_CAP, count * LIBRARY_SEED_GENRE_WEIGHT)
        _upsert_score(db, person.id, "genre", genre, score)
    for decade, count in decade_counts.items():
        score = min(LIBRARY_SEED_DECADE_CAP, count * LIBRARY_SEED_DECADE_WEIGHT)
        _upsert_score(db, person.id, "decade", decade, score)

    # Marker so we do not re-seed; score ~0 so it does not affect ranking.
    _upsert_score(db, person.id, LIBRARY_SEED_META_KIND, LIBRARY_SEED_META_VALUE, 0.001)
    db.commit()
    return {
        "seeded": True,
        "reason": "ok",
        "movies": len(movies),
        "genres": len(genre_counts),
        "decades": len(decade_counts),
        "top_genres": genre_counts.most_common(8),
    }


def top_preferences(db: Session, person_id: int, kind: str = "genre", limit: int = 5) -> list[PreferenceScore]:
    return (
        db.query(PreferenceScore)
        .filter_by(person_id=person_id, kind=kind)
        .order_by(PreferenceScore.score.desc())
        .limit(limit)
        .all()
    )


def disliked_preferences(db: Session, person_id: int, kind: str = "genre", limit: int = 5) -> list[PreferenceScore]:
    return (
        db.query(PreferenceScore)
        .filter_by(person_id=person_id, kind=kind)
        .order_by(PreferenceScore.score.asc())
        .limit(limit)
        .all()
    )


def score_candidate(db: Session, person_id: int, genres: list[str], year: Optional[int] = None) -> float:
    prefs = db.query(PreferenceScore).filter_by(person_id=person_id).all()
    by_key = {(p.kind, p.value.lower()): p.score for p in prefs}
    score = 0.0
    for g in genres:
        score += by_key.get(("genre", g.lower()), 0.0)
    if year:
        decade = f"{(year // 10) * 10}s"
        score += by_key.get(("decade", decade.lower()), 0.0) * 0.5
    return score


def compute_weekly_pace(db: Session, person: Person) -> float:
    since = datetime.utcnow() - timedelta(days=28)
    count = (
        db.query(WatchEvent)
        .filter(
            WatchEvent.person_id == person.id,
            WatchEvent.item_type == "Movie",
            WatchEvent.last_watched_at >= since,
        )
        .count()
    )
    pace = max(1.0, round(count / 4.0, 1))
    person.movies_per_week = pace
    db.commit()
    return pace


def preference_summary(db: Session, person_id: int) -> dict:
    liked = top_preferences(db, person_id, "genre", 5)
    disliked = [p for p in disliked_preferences(db, person_id, "genre", 5) if p.score < 0]
    return {
        "liked": [(p.value, p.score) for p in liked if p.score > 0],
        "disliked": [(p.value, p.score) for p in disliked],
    }
