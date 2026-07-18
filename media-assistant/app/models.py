from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    telegram_chat_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    pairing_code: Mapped[Optional[str]] = mapped_column(String(16), unique=True, nullable=True)
    jellyfin_user_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    jellyfin_username: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    movies_per_week: Mapped[float] = mapped_column(Float, default=1.0)
    recs_cap: Mapped[int] = mapped_column(Integer, default=7)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    watches = relationship("WatchEvent", back_populates="person", cascade="all, delete-orphan")
    preferences = relationship("PreferenceScore", back_populates="person", cascade="all, delete-orphan")
    recommendations = relationship("Recommendation", back_populates="person", cascade="all, delete-orphan")
    downloads = relationship("DownloadRequest", back_populates="person", cascade="all, delete-orphan")


class WatchEvent(Base):
    __tablename__ = "watch_events"
    __table_args__ = (UniqueConstraint("person_id", "jellyfin_item_id", name="uq_person_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    jellyfin_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), default="Movie")  # Movie | Episode
    title: Mapped[str] = mapped_column(String(300), default="")
    series_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    series_tmdb_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    season_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tmdb_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    genres: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # comma-separated
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    play_count: Mapped[int] = mapped_column(Integer, default=1)
    last_watched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    person = relationship("Person", back_populates="watches")


class PreferenceScore(Base):
    __tablename__ = "preference_scores"
    __table_args__ = (UniqueConstraint("person_id", "kind", "value", name="uq_pref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # genre | decade | actor | rating
    value: Mapped[str] = mapped_column(String(120))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    person = relationship("Person", back_populates="preferences")


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("person_id", "media_type", "tmdb_id", name="uq_rec"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    media_type: Mapped[str] = mapped_column(String(16), default="movie")  # movie | tv
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    overview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    poster_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending | sent | downloaded | rejected | already_seen | liked | disliked | kept
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    feedback_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    person = relationship("Person", back_populates="recommendations")


class DownloadRequest(Base):
    __tablename__ = "download_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    media_type: Mapped[str] = mapped_column(String(16), default="movie")
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    radarr_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sonarr_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    season_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="requested")
    # requested | downloading | completed | failed | deleted | kept | blocked_disk
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    managed_by_us: Mapped[bool] = mapped_column(Boolean, default=True)
    estimated_size_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_profile_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolution_cap: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    watched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    delete_after: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notified_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    person = relationship("Person", back_populates="downloads")
    interests = relationship("DownloadInterest", back_populates="download", cascade="all, delete-orphan")


class SeriesRoll(Base):
    """Per-person rolling window for a series (few episodes ahead, delete watched)."""

    __tablename__ = "series_rolls"
    __table_args__ = (UniqueConstraint("person_id", "tmdb_id", name="uq_series_roll"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    sonarr_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    season_number: Mapped[int] = mapped_column(Integer, default=1)
    last_watched_episode: Mapped[int] = mapped_column(Integer, default=0)
    buffer_ahead: Mapped[int] = mapped_column(Integer, default=3)
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # End-of-season / end-of-series star rating (not per-episode)
    season_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    series_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    rating_prompt_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    star_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating_asked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rating_timeout_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    cleanup_after_rating: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class EpisodeFileState(Base):
    """Tracks managed episode files for rolling cleanup (multi-user safe)."""

    __tablename__ = "episode_file_states"
    __table_args__ = (
        UniqueConstraint("sonarr_id", "season_number", "episode_number", name="uq_ep_file"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sonarr_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    episode_file_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="wanted")
    # wanted | downloading | ready | watched | deleted | kept
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    managed_by_us: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_after: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    interests = relationship("EpisodeInterest", back_populates="episode", cascade="all, delete-orphan")


class EpisodeInterest(Base):
    __tablename__ = "episode_interests"
    __table_args__ = (UniqueConstraint("episode_id", "person_id", name="uq_ep_interest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episode_file_states.id", ondelete="CASCADE"))
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    watched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    episode = relationship("EpisodeFileState", back_populates="interests")


class DownloadInterest(Base):
    """Tracks which people care about a shared download (multi-user safety)."""

    __tablename__ = "download_interests"
    __table_args__ = (UniqueConstraint("download_id", "person_id", name="uq_interest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    download_id: Mapped[int] = mapped_column(ForeignKey("download_requests.id", ondelete="CASCADE"))
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    declined: Mapped[bool] = mapped_column(Boolean, default=False)
    keep: Mapped[bool] = mapped_column(Boolean, default=False)
    watched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Star rating before deletion (1–5); asked when retention is due
    star_rating: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rating_asked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rating_received_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rating_timeout_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    download = relationship("DownloadRequest", back_populates="interests")


class RatingPrompt(Base):
    """Pending pre-delete (or post-seen) star rating prompts sent via Telegram."""

    __tablename__ = "rating_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    download_id: Mapped[Optional[int]] = mapped_column(ForeignKey("download_requests.id", ondelete="SET NULL"), nullable=True)
    series_roll_id: Mapped[Optional[int]] = mapped_column(ForeignKey("series_rolls.id", ondelete="SET NULL"), nullable=True)
    recommendation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), default="movie")
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    poster_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    context: Mapped[str] = mapped_column(String(32), default="pre_delete")
    # pre_delete | after_seen | season_complete | series_complete
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | rated | timed_out | skipped
    stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    asked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    timeout_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DeletionLog(Base):
    __tablename__ = "deletion_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_type: Mapped[str] = mapped_column(String(16))
    tmdb_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    reason: Mapped[str] = mapped_column(String(200), default="")
    radarr_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sonarr_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ErrorLog(Base):
    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), default="app")
    message: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
