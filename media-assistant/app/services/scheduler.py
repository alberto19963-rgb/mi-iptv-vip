from __future__ import annotations

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.db import SessionLocal
from app.services.downloads import poll_download_status
from app.services.recommendations import prepare_weekly_recommendations
from app.services.retention import emergency_disk_cleanup, run_retention
from app.services.series_rolling import (
    cleanup_watched_episodes,
    refresh_episode_file_ids,
    sync_rolls_from_watches,
)
from app.services.settings_store import log_error
from app.services.telegram_bot import (
    dispatch_pending_rating_prompts,
    dispatch_pending_recommendations,
    ensure_bot_running,
    notify_completed_downloads,
)
from app.services.watch_sync import sync_all_watches

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def _run_sync(fn):
    db = SessionLocal()
    try:
        return fn(db)
    except Exception as exc:
        try:
            log_error(db, "scheduler", fn.__name__, str(exc))
        except Exception:
            pass
        logger.exception("job %s failed", fn.__name__)
        return None
    finally:
        db.close()


async def job_watch_sync() -> None:
    await asyncio.to_thread(_run_sync, sync_all_watches)


async def job_recommendations() -> None:
    await asyncio.to_thread(_run_sync, prepare_weekly_recommendations)
    await dispatch_pending_recommendations()


async def job_downloads() -> None:
    def _poll(db):
        return poll_download_status(db)

    result = await asyncio.to_thread(_run_sync, _poll)
    if result and result.get("ready"):
        await notify_completed_downloads(result["ready"])


async def job_retention() -> None:
    await asyncio.to_thread(_run_sync, run_retention)
    await asyncio.to_thread(_run_sync, emergency_disk_cleanup)
    await dispatch_pending_rating_prompts()


async def job_series_rolling() -> None:
    """Advance rolling windows: buffer next episodes, delete watched ones, season ratings."""
    await asyncio.to_thread(_run_sync, refresh_episode_file_ids)
    await asyncio.to_thread(_run_sync, sync_rolls_from_watches)
    await asyncio.to_thread(_run_sync, cleanup_watched_episodes)
    await dispatch_pending_rating_prompts()



async def job_bot_health() -> None:
    await ensure_bot_running()


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler
    sched = AsyncIOScheduler(timezone="America/Puerto_Rico")
    sched.add_job(job_watch_sync, IntervalTrigger(minutes=30), id="watch_sync", replace_existing=True, max_instances=1)
    sched.add_job(job_recommendations, IntervalTrigger(hours=6), id="recommendations", replace_existing=True, max_instances=1)
    sched.add_job(job_downloads, IntervalTrigger(minutes=5), id="downloads", replace_existing=True, max_instances=1)
    sched.add_job(job_retention, IntervalTrigger(hours=2), id="retention", replace_existing=True, max_instances=1)
    sched.add_job(job_series_rolling, IntervalTrigger(minutes=20), id="series_rolling", replace_existing=True, max_instances=1)
    sched.add_job(job_bot_health, IntervalTrigger(minutes=2), id="bot_health", replace_existing=True, max_instances=1)
    sched.start()
    _scheduler = sched
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
