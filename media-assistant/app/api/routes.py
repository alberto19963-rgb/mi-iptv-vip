from __future__ import annotations

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import DeletionLog, DownloadRequest, ErrorLog, Person, Recommendation
from app.services.jellyfin import JellyfinClient
from app.services.preferences import preference_summary
from app.services.radarr import RadarrClient
from app.services.retention import disk_free_gb
from app.services.settings_store import get_all_settings, service_cfg, set_setting
from app.services.telegram_bot import ensure_bot_running, generate_pairing_code
from app.services.watch_sync import sync_all_watches

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _people_cards(db: Session) -> list[dict]:
    cards = []
    for p in db.query(Person).order_by(Person.name).all():
        prefs = preference_summary(db, p.id)
        pending = db.query(Recommendation).filter_by(person_id=p.id, status="pending").count()
        sent = db.query(Recommendation).filter_by(person_id=p.id, status="sent").count()
        dls = db.query(DownloadRequest).filter_by(person_id=p.id).order_by(DownloadRequest.id.desc()).limit(5).all()
        cards.append(
            {
                "person": p,
                "prefs": prefs,
                "pending": pending,
                "sent": sent,
                "downloads": dls,
            }
        )
    return cards


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    settings = get_all_settings(db)
    free = disk_free_gb(db)
    scheduled = (
        db.query(DownloadRequest)
        .filter(DownloadRequest.delete_after.isnot(None), DownloadRequest.status == "completed")
        .order_by(DownloadRequest.delete_after)
        .limit(20)
        .all()
    )
    errors = db.query(ErrorLog).order_by(ErrorLog.id.desc()).limit(15).all()
    deletions = db.query(DeletionLog).order_by(DeletionLog.id.desc()).limit(10).all()
    downloads = db.query(DownloadRequest).order_by(DownloadRequest.id.desc()).limit(20).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "cards": _people_cards(db),
            "settings": settings,
            "free_gb": free,
            "scheduled": scheduled,
            "errors": errors,
            "deletions": deletions,
            "downloads": downloads,
            "bot_status": settings.get("bot_status", "waiting_for_token"),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db), saved: Optional[str] = None):
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": get_all_settings(db), "saved": saved == "1"},
    )


@router.post("/settings")
async def save_settings(
    request: Request,
    db: Session = Depends(get_db),
    telegram_bot_token: str = Form(""),
    tmdb_api_key: str = Form(""),
    jellyfin_url: str = Form(""),
    jellyfin_api_key: str = Form(""),
    radarr_url: str = Form(""),
    radarr_api_key: str = Form(""),
    radarr_quality_profile_id: str = Form("9"),
    radarr_root_folder: str = Form("/movies"),
    sonarr_url: str = Form(""),
    sonarr_api_key: str = Form(""),
    sonarr_quality_profile_id: str = Form("6"),
    sonarr_root_folder: str = Form("/data/Series"),
    bazarr_url: str = Form(""),
    bazarr_api_key: str = Form(""),
    retention_days: str = Form("4"),
    disk_threshold_gb: str = Form("50"),
    max_recs_per_week: str = Form("7"),
    resolution_cap: str = Form("1080p"),
    series_buffer_episodes: str = Form("3"),
    rating_timeout_hours: str = Form("24"),
):
    values = {
        "telegram_bot_token": telegram_bot_token.strip(),
        "tmdb_api_key": tmdb_api_key.strip(),
        "jellyfin_url": jellyfin_url.strip(),
        "jellyfin_api_key": jellyfin_api_key.strip(),
        "radarr_url": radarr_url.strip(),
        "radarr_api_key": radarr_api_key.strip(),
        "radarr_quality_profile_id": radarr_quality_profile_id.strip(),
        "radarr_root_folder": radarr_root_folder.strip(),
        "sonarr_url": sonarr_url.strip(),
        "sonarr_api_key": sonarr_api_key.strip(),
        "sonarr_quality_profile_id": sonarr_quality_profile_id.strip(),
        "sonarr_root_folder": sonarr_root_folder.strip(),
        "bazarr_url": bazarr_url.strip(),
        "bazarr_api_key": bazarr_api_key.strip(),
        "retention_days": retention_days.strip() or "4",
        "disk_threshold_gb": disk_threshold_gb.strip() or "50",
        "max_recs_per_week": max_recs_per_week.strip() or "7",
        "resolution_cap": (resolution_cap.strip() or "1080p").lower(),
        "series_buffer_episodes": series_buffer_episodes.strip() or "3",
        "rating_timeout_hours": rating_timeout_hours.strip() or "24",
    }
    for k, v in values.items():
        set_setting(db, k, v)
    await ensure_bot_running()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.get("/people", response_class=HTMLResponse)
def people_list(request: Request, db: Session = Depends(get_db)):
    cfg = service_cfg(db)
    jf_users = []
    try:
        jf = JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"])
        jf_users = jf.get_users()
    except Exception:
        jf_users = []
    return templates.TemplateResponse(
        "people.html",
        {
            "request": request,
            "people": db.query(Person).order_by(Person.name).all(),
            "jf_users": jf_users,
        },
    )


@router.post("/people")
def create_person(
    db: Session = Depends(get_db),
    name: str = Form(...),
    jellyfin_user_id: str = Form(""),
    telegram_chat_id: str = Form(""),
):
    jf_name = None
    if jellyfin_user_id:
        cfg = service_cfg(db)
        try:
            for u in JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"]).get_users():
                if u.get("Id") == jellyfin_user_id:
                    jf_name = u.get("Name")
                    break
        except Exception:
            pass
    person = Person(
        name=name.strip(),
        jellyfin_user_id=jellyfin_user_id or None,
        jellyfin_username=jf_name,
        telegram_chat_id=telegram_chat_id.strip() or None,
        pairing_code=None if telegram_chat_id.strip() else generate_pairing_code(),
        active=True,
    )
    db.add(person)
    db.commit()
    if person.jellyfin_user_id:
        try:
            from app.services.watch_sync import sync_person_watches

            cfg = service_cfg(db)
            jf = JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"])
            if jf.configured():
                sync_person_watches(db, person, jf)
        except Exception:
            pass
    return RedirectResponse("/people", status_code=303)


@router.post("/people/{person_id}/pairing")
def refresh_pairing(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person:
        person.pairing_code = generate_pairing_code()
        db.commit()
    return RedirectResponse("/people", status_code=303)


@router.post("/people/{person_id}/pause")
def toggle_pause(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person:
        person.paused = not person.paused
        db.commit()
    return RedirectResponse("/people", status_code=303)


@router.post("/people/{person_id}/delete")
def delete_person(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person:
        db.delete(person)
        db.commit()
    return RedirectResponse("/people", status_code=303)


@router.post("/people/{person_id}/update")
def update_person(
    person_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    jellyfin_user_id: str = Form(""),
    telegram_chat_id: str = Form(""),
    recs_cap: int = Form(7),
):
    person = db.get(Person, person_id)
    if person:
        person.name = name.strip()
        person.jellyfin_user_id = jellyfin_user_id or None
        person.telegram_chat_id = telegram_chat_id.strip() or None
        person.recs_cap = recs_cap
        if jellyfin_user_id:
            cfg = service_cfg(db)
            try:
                for u in JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"]).get_users():
                    if u.get("Id") == jellyfin_user_id:
                        person.jellyfin_username = u.get("Name")
                        break
            except Exception:
                pass
        db.commit()
        if person.jellyfin_user_id:
            try:
                from app.services.watch_sync import sync_person_watches

                cfg = service_cfg(db)
                jf = JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"])
                if jf.configured():
                    sync_person_watches(db, person, jf)
            except Exception:
                pass
    return RedirectResponse("/people", status_code=303)


@router.post("/actions/sync-watches")
def action_sync(db: Session = Depends(get_db)):
    sync_all_watches(db)
    return RedirectResponse("/", status_code=303)


@router.post("/downloads/{download_id}/keep")
def keep_download(download_id: int, db: Session = Depends(get_db)):
    from app.services.downloads import set_keep

    set_keep(db, download_id)
    return RedirectResponse("/", status_code=303)


@router.get("/api/jellyfin-users")
def api_jf_users(db: Session = Depends(get_db)):
    cfg = service_cfg(db)
    try:
        users = JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"]).get_users()
        return [{"id": u.get("Id"), "name": u.get("Name")} for u in users]
    except Exception as exc:
        return {"error": str(exc)}
