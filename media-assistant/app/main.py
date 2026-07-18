from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.db import SessionLocal, init_db
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.settings_store import ensure_defaults, set_setting
from app.services.telegram_bot import ensure_bot_running

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("media_assistant")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        ensure_defaults(db)
        # Prefill from env file secrets if present (deploy injects them once)
        import os

        prefill = {
            "jellyfin_api_key": os.getenv("JELLYFIN_API_KEY", ""),
            "radarr_api_key": os.getenv("RADARR_API_KEY", ""),
            "sonarr_api_key": os.getenv("SONARR_API_KEY", ""),
            "bazarr_api_key": os.getenv("BAZARR_API_KEY", ""),
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "tmdb_api_key": os.getenv("TMDB_API_KEY", ""),
        }
        from app.services.settings_store import get_setting

        for key, val in prefill.items():
            if val and not get_setting(db, key):
                set_setting(db, key, val)
        # Copy TMDb key from Jellyfin plugin config if MA has none (never logs the key)
        try:
            from app.services.tmdb_import import ensure_tmdb_key_from_jellyfin

            result = ensure_tmdb_key_from_jellyfin(db)
            if result.get("imported"):
                logger.info("TMDb API key imported from Jellyfin plugin config")
            elif not result.get("has_key"):
                logger.info("TMDb API key not set (Jellyfin plugin has no key either)")
        except Exception:
            logger.exception("TMDb import from Jellyfin skipped")
    finally:
        db.close()

    start_scheduler()
    status = await ensure_bot_running()
    logger.info("Media Assistant listo. Bot status: %s", status)
    yield
    stop_scheduler()


app = FastAPI(title="Media Assistant", lifespan=lifespan)
app.include_router(router)

try:
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
except Exception:
    pass


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "service": "media-assistant"})
