from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.db import SessionLocal
from app.models import DownloadRequest, Person, Recommendation
from app.services.downloads import (
    DiskBlockedError,
    mark_download_watched,
    request_download,
    set_keep,
)
from app.services.preferences import apply_feedback
from app.services.settings_store import get_setting, log_error, service_cfg, set_setting
from app.services.tmdb import TMDbClient

logger = logging.getLogger(__name__)

# Intent keywords for on-demand suggestions
_MOVIE_WORDS = ("pelicula", "película", "peliculas", "películas", "movie", "movies", "film", "films")
_SERIES_WORDS = ("serie", "series", "show", "shows", "temporada", "tv")
_SUGGEST_WORDS = (
    "sugerencia", "sugerencias", "sugiere", "sugiéreme", "sugiereme",
    "recomienda", "recomiéndame", "recomiendame", "recomendacion", "recomendación",
    "recomendaciones", "suggest", "suggestion", "suggestions", "recommend",
    "recommendation", "recommendations", "dame algo", "que veo", "qué veo",
)


def detect_suggestion_intent(text: str) -> Optional[str]:
    """Return 'movie', 'tv', 'ambiguous' or None (not a suggestion request)."""
    t = (text or "").lower()
    wants = any(w in t for w in _SUGGEST_WORDS)
    has_movie = any(w in t for w in _MOVIE_WORDS)
    has_series = any(w in t for w in _SERIES_WORDS)
    if not wants and not (has_movie or has_series):
        return None
    if not wants:
        # Mentions movies/series but no suggestion verb → not a request
        return None
    if has_movie and not has_series:
        return "movie"
    if has_series and not has_movie:
        return "tv"
    if has_movie and has_series:
        return "ambiguous"
    return "ambiguous"

_app: Optional[Application] = None
_task: Optional[asyncio.Task] = None
_bot_token_running: Optional[str] = None


def generate_pairing_code() -> str:
    return secrets.token_hex(3).upper()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    text = (update.message.text or "").strip()
    parts = text.split(maxsplit=1)
    code = parts[1].strip().upper() if len(parts) > 1 else None
    chat_id = str(update.effective_chat.id)

    db = SessionLocal()
    try:
        person = db.query(Person).filter_by(telegram_chat_id=chat_id).one_or_none()
        if person:
            await update.message.reply_text(
                f"Hola {person.name} 👋\nYa estás vinculado al Media Assistant.\n"
                "Escribe *ayuda* o /help para ver todos los comandos.",
                parse_mode="Markdown",
            )
            return
        if code:
            person = db.query(Person).filter_by(pairing_code=code).one_or_none()
            if person:
                person.telegram_chat_id = chat_id
                person.pairing_code = None
                db.commit()
                await update.message.reply_text(
                    f"¡Listo! Te vinculé como *{person.name}*.\nPronto te enviaré recomendaciones.",
                    parse_mode="Markdown",
                )
                return
            await update.message.reply_text("Código inválido. Pide uno nuevo en el panel de administración.")
            return
        await update.message.reply_text(
            "Bienvenido al Media Assistant 🎬\n\n"
            "Para vincularte, abre el panel web, crea tu perfil y usa:\n"
            "`/start CODIGO`\n\n"
            "También puedes escribir tu código de emparejamiento aquí.",
            parse_mode="Markdown",
        )
    finally:
        db.close()


HELP_TEXT = (
    "🎬 *Media Assistant — ayuda*\n"
    "\n"
    "Hola. Aquí tienes *todo* lo que puedes pedirme. "
    "Puedes usar comandos o escribir en lenguaje natural.\n"
    "\n"
    "——————\n"
    "\n"
    "📘 */help* o escribe *ayuda*\n"
    "_Muestra este mensaje._\n"
    "Ejemplo: `ayuda`\n"
    "\n"
    "🎬 */peliculas*\n"
    "_Te mando sugerencias de películas según lo que sueles ver._\n"
    "Ejemplo: `/peliculas`\n"
    "También: `dame sugerencias de películas` · `sugiéreme una película` · `suggest me a movie`\n"
    "\n"
    "📺 */series*\n"
    "_Te mando sugerencias de series._\n"
    "Ejemplo: `/series`\n"
    "También: `dame sugerencias de series` · `sugiéreme una serie` · `suggest me a series`\n"
    "\n"
    "❓ *«dame sugerencias»* (sin decir películas ni series)\n"
    "_No adivino: te pregunto con botones 🎬 Películas | 📺 Series._\n"
    "Ejemplo: `dame sugerencias` · `recomiéndame algo` · `give me suggestions`\n"
    "\n"
    "📊 */status*\n"
    "_Tu ritmo de visionado y si estás activo o pausado._\n"
    "Ejemplo: `/status`\n"
    "\n"
    "🔗 */start CODIGO*\n"
    "_Vincula tu Telegram con tu perfil del panel._\n"
    "Ejemplo: `/start A1B2C3`\n"
    "\n"
    "——————\n"
    "\n"
    "*Botones en cada sugerencia*\n"
    "📥 *Descargar* — la mando a *Radarr* (pelis) o *Sonarr* (series); ellos buscan en Prowlarr/indexers (máx. 1080p). TMDb solo aporta póster/sinopsis, no descarga.\n"
    "🚫 *No me interesa* — la descarto y aprendo de eso\n"
    "👁 *Ya la vi* — te pido valoración ⭐ 1–5\n"
    "🔒 *Conservar* — no se borra sola (útil si te encantó)\n"
    "\n"
    "*Valoración*\n"
    "Después de ver una *película*, antes de borrarla te pregunto con estrellas ⭐.\n"
    "En *series* no te molesto en cada capítulo: solo al *terminar la temporada/serie*.\n"
    "Si no respondes en ~24 h, limpio igual.\n"
    "\n"
    "——————\n"
    "\n"
    "💡 *Tip:* calidad máxima *1080p* (nunca 4K). En series solo mantengo unos pocos "
    "episodios por delante y borro los ya vistos para no llenar el disco.\n"
    "\n"
    "Puedes escribirme con naturalidad; si te atoras, manda *ayuda*."
)


def is_help_request(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    # Exact or short phrases
    if t in ("ayuda", "help", "ayudame", "ayúdame", "/help", "/ayuda"):
        return True
    help_phrases = (
        "necesito ayuda",
        "quiero ayuda",
        "que puedo hacer",
        "qué puedo hacer",
        "como funciona",
        "cómo funciona",
        "comandos",
        "menu",
        "menú",
    )
    return any(p in t for p in help_phrases)


async def send_help(message) -> None:
    await message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await send_help(update.message)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return
    db = SessionLocal()
    try:
        person = db.query(Person).filter_by(telegram_chat_id=str(update.effective_chat.id)).one_or_none()
        if not person:
            await update.message.reply_text("No estás vinculado. Usa /start CODIGO")
            return
        await update.message.reply_text(
            f"👤 {person.name}\n"
            f"🎞 Ritmo: ~{person.movies_per_week} películas/semana\n"
            f"Estado: {'pausado' if person.paused else 'activo'}"
        )
    finally:
        db.close()


def _media_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎬 Películas", callback_data="sug:movie"),
                InlineKeyboardButton("📺 Series", callback_data="sug:tv"),
            ]
        ]
    )


def _md_escape(text: str) -> str:
    """Escape Telegram legacy Markdown special chars in free-form titles/overviews."""
    out = text or ""
    for ch in ("\\", "_", "*", "`", "["):
        out = out.replace(ch, f"\\{ch}")
    return out


async def _run_on_demand(bot, chat_id: str, media_type: str) -> None:
    """Ack + scan history/prefs + send several suggestions for that person."""
    from app.services.recommendations import generate_recommendations
    from app.services.watch_sync import sync_person_watches
    from app.services.jellyfin import JellyfinClient

    chat = int(chat_id)
    db = SessionLocal()
    acked = False
    try:
        person = db.query(Person).filter_by(telegram_chat_id=str(chat_id)).one_or_none()
        if not person:
            await bot.send_message(chat_id=chat, text="No estás vinculado. Usa /start CODIGO")
            return
        await bot.send_message(
            chat_id=chat,
            text="¡De una! Dame un momento mientras reviso lo que te gusta… 🍿",
        )
        acked = True
        cfg = service_cfg(db)
        if not cfg["tmdb_api_key"]:
            await bot.send_message(
                chat_id=chat,
                text="Aún falta configurar la clave de TMDb en el panel. Avísale al admin 😉",
            )
            return
        # Quick TMDb probe so JWT/v3 misconfig fails with a clear user message
        tmdb = TMDbClient(cfg["tmdb_api_key"])
        try:
            tmdb.discover_movies(page=1) if media_type == "movie" else tmdb.discover_tv(page=1)
        except Exception as tmdb_exc:
            log_error(db, "telegram", "TMDb no responde en sugerencias", str(tmdb_exc))
            await bot.send_message(
                chat_id=chat,
                text="No pude consultar TMDb ahora mismo. Revisa la clave API en el panel e inténtalo de nuevo.",
            )
            return
        # Refresh watch history/prefs before recommending
        try:
            jf = JellyfinClient(cfg["jellyfin_url"], cfg["jellyfin_api_key"])
            if jf.configured() and person.jellyfin_user_id:
                sync_person_watches(db, person, jf)
        except Exception:
            logger.exception("sync before on-demand failed")

        recs = generate_recommendations(db, person, media_type=media_type, count=3)
        if not recs:
            pending = (
                db.query(Recommendation)
                .filter_by(person_id=person.id, status="pending", media_type=media_type)
                .order_by(Recommendation.score.desc())
                .limit(3)
                .all()
            )
            recs = pending
        if not recs:
            kind = "series" if media_type == "tv" else "películas"
            await bot.send_message(
                chat_id=chat,
                text=f"No encontré {kind} nuevas para ti ahora mismo. Intenta más tarde 🙏",
            )
            return
        sent_ok = 0
        for rec in recs:
            if await send_recommendation(db, bot, person, rec):
                sent_ok += 1
            await asyncio.sleep(1)
        if sent_ok == 0:
            await bot.send_message(
                chat_id=chat,
                text="Encontré sugerencias pero no pude enviártelas por Telegram. Intenta de nuevo en un momento.",
            )
    except Exception as exc:
        log_error(db, "telegram", "Error en sugerencias bajo demanda", str(exc))
        logger.exception("on-demand recs failed")
        if acked:
            try:
                await bot.send_message(
                    chat_id=chat,
                    text="Uy, se me trabó buscando sugerencias. Intenta de nuevo en un momento 🙏",
                )
            except Exception:
                logger.exception("failed to notify user after on-demand error")
    finally:
        db.close()


async def cmd_peliculas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _run_on_demand(context.bot, str(update.effective_chat.id), "movie")


async def cmd_series(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _run_on_demand(context.bot, str(update.effective_chat.id), "tv")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pairing codes + natural-language suggestion requests."""
    if not update.message or not update.effective_chat:
        return
    raw = (update.message.text or "").strip()
    chat_id = str(update.effective_chat.id)

    # 0) Help (ayuda / help / …)
    if is_help_request(raw):
        await send_help(update.message)
        return

    # 1) Pairing code (6 hex chars)
    code = raw.upper()
    if len(code) == 6 and all(c in "0123456789ABCDEF" for c in code):
        db = SessionLocal()
        try:
            if db.query(Person).filter_by(telegram_chat_id=chat_id).one_or_none():
                return
            person = db.query(Person).filter_by(pairing_code=code).one_or_none()
            if not person:
                await update.message.reply_text("Código no encontrado.")
                return
            person.telegram_chat_id = chat_id
            person.pairing_code = None
            db.commit()
            await update.message.reply_text(f"¡Vinculado como {person.name}!")
            return
        finally:
            db.close()

    # 2) Suggestion intent
    intent = detect_suggestion_intent(raw)
    if intent == "movie":
        await _run_on_demand(context.bot, chat_id, "movie")
    elif intent == "tv":
        await _run_on_demand(context.bot, chat_id, "tv")
    elif intent == "ambiguous":
        await update.message.reply_text(
            "¿De qué quieres sugerencias: películas o series?",
            reply_markup=_media_choice_keyboard(),
        )


def _rec_keyboard(rec_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 Descargar", callback_data=f"dl:{rec_id}"),
                InlineKeyboardButton("🚫 No me interesa", callback_data=f"no:{rec_id}"),
            ],
            [
                InlineKeyboardButton("👁 Ya la vi", callback_data=f"seen:{rec_id}"),
                InlineKeyboardButton("🔒 Conservar", callback_data=f"keep:{rec_id}"),
            ],
        ]
    )


def _stars_keyboard(prompt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1★", callback_data=f"star:{prompt_id}:1"),
                InlineKeyboardButton("2★", callback_data=f"star:{prompt_id}:2"),
                InlineKeyboardButton("3★", callback_data=f"star:{prompt_id}:3"),
                InlineKeyboardButton("4★", callback_data=f"star:{prompt_id}:4"),
                InlineKeyboardButton("5★", callback_data=f"star:{prompt_id}:5"),
            ]
        ]
    )


async def send_rating_prompt(bot, person: Person, prompt) -> bool:
    """Send poster + ¿Cómo te pareció? + 1–5 stars."""
    from app.services.tmdb import TMDbClient

    if not person.telegram_chat_id:
        return False
    cfg = service_cfg(db := SessionLocal())
    try:
        tmdb = TMDbClient(cfg.get("tmdb_api_key") or "")
        poster = tmdb.poster_url(prompt.poster_path) if prompt.poster_path else None
        year = f" ({prompt.year})" if prompt.year else ""
        if prompt.context in ("season_complete", "series_complete"):
            kind = "la serie" if prompt.context == "series_complete" else "esta temporada de"
            text = (
                f"⭐ Antes de limpiar archivos…\n\n"
                f"¿Cómo te pareció {kind} *{prompt.title}*{year}?\n"
                f"_Valora del 1 al 5_"
            )
        elif prompt.context == "after_seen":
            text = f"¿Cómo te pareció *{prompt.title}*{year}?\n_Valora del 1 al 5_"
        else:
            text = (
                f"⭐ Antes de borrarla…\n\n"
                f"¿Cómo te pareció *{prompt.title}*{year}?\n"
                f"_Cuéntame con estrellas (1–5)_"
            )
        kb = _stars_keyboard(prompt.id)
        chat = int(person.telegram_chat_id)
        if poster:
            await bot.send_photo(chat_id=chat, photo=poster, caption=text, parse_mode="Markdown", reply_markup=kb)
        else:
            await bot.send_message(chat_id=chat, text=text, parse_mode="Markdown", reply_markup=kb)
        return True
    except Exception:
        logger.exception("send_rating_prompt failed")
        return False
    finally:
        db.close()


async def dispatch_pending_rating_prompts() -> int:
    from app.models import Person, RatingPrompt
    from app.services.ratings import (
        pending_prompts_to_send,
        prepare_pre_delete_ratings,
        prepare_series_completion_ratings,
    )

    app = _app
    if not app or not app.bot:
        return 0
    db = SessionLocal()
    sent = 0
    try:
        prepare_pre_delete_ratings(db)
        prepare_series_completion_ratings(db)
        # Mark which ones we already tried? Use asked_at + a simple approach:
        # send all pending that don't have a "sent" flag — we use status pending and
        # only send once per prompt by checking a detail in ErrorLog is overkill;
        # instead store nothing and rely on creating prompts only once.
        # To avoid re-sending every scheduler tick, set recommendation_id=-1 as "delivered" hack
        # Better: add delivered flag — use answered_at is null and check ErrorLog…
        # Simplest: set prompt.recommendation_id = -1 when delivered (unused for ratings).
        for prompt in pending_prompts_to_send(db):
            if prompt.recommendation_id == -1:
                continue
            person = db.get(Person, prompt.person_id)
            if not person:
                continue
            if await send_rating_prompt(app.bot, person, prompt):
                prompt.recommendation_id = -1  # delivered marker
                sent += 1
                await asyncio.sleep(0.5)
        db.commit()
    finally:
        db.close()
    return sent



async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_chat:
        return
    await query.answer()
    parts = query.data.split(":")
    action = parts[0] if parts else ""

    # Media-type choice for ambiguous suggestion requests
    if action == "sug":
        media_type = "tv" if (parts[1] if len(parts) > 1 else "") == "tv" else "movie"
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await _run_on_demand(context.bot, str(update.effective_chat.id), media_type)
        return

    # Star rating: star:{prompt_id}:{1-5}
    if action == "star" and len(parts) >= 3:
        from app.services.ratings import record_star_rating

        try:
            prompt_id = int(parts[1])
            stars = int(parts[2])
        except ValueError:
            return
        db = SessionLocal()
        try:
            person = db.query(Person).filter_by(telegram_chat_id=str(update.effective_chat.id)).one_or_none()
            if not person:
                return
            prompt = record_star_rating(db, prompt_id, person.id, stars)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            if prompt:
                faces = {1: "😕", 2: "😐", 3: "🙂", 4: "😊", 5: "🤩"}
                await query.message.reply_text(
                    f"{faces.get(stars, '⭐')} Gracias — anoté *{stars}/5* para *{prompt.title}*.",
                    parse_mode="Markdown",
                )
        except Exception as exc:
            log_error(db, "telegram", "Error en valoración", str(exc))
            logger.exception("star rating callback")
        finally:
            db.close()
        return

    try:
        rec_id = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return
    raw_id = parts[1] if len(parts) > 1 else ""


    db = SessionLocal()
    try:
        person = db.query(Person).filter_by(telegram_chat_id=str(update.effective_chat.id)).one_or_none()
        rec = db.get(Recommendation, rec_id)
        if not person or not rec or rec.person_id != person.id:
            await query.edit_message_reply_markup(reply_markup=None)
            return

        genres: list[str] = []
        actors: list[str] = []
        cfg = service_cfg(db)
        tmdb = TMDbClient(cfg["tmdb_api_key"])
        if tmdb.configured():
            try:
                detail = tmdb.movie(rec.tmdb_id) if rec.media_type == "movie" else tmdb.tv(rec.tmdb_id)
                genres = [g["name"] for g in detail.get("genres") or []]
                cast = (detail.get("credits") or {}).get("cast") or []
                actors = [c["name"] for c in cast[:5]]
            except Exception:
                pass

        if action == "dl":
            # Always Radarr (movies) / Sonarr (series) → Prowlarr/indexers. Never TMDb.
            try:
                dl = request_download(
                    db,
                    person,
                    media_type=rec.media_type,
                    tmdb_id=rec.tmdb_id,
                    title=rec.title,
                    year=rec.year,
                )
            except DiskBlockedError as blocked:
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(
                    "⚠️ No hay espacio suficiente en el disco ahora mismo.\n"
                    f"{blocked.fit.get('message') or ''}\n"
                    "Voy a intentar liberar espacio; vuelve a pedirla en un rato."
                )
                return
            rec.status = "downloaded" if dl.status != "failed" else "pending"
            rec.feedback_at = datetime.utcnow()
            db.commit()
            via = "Sonarr" if rec.media_type == "tv" else "Radarr"
            if dl.status == "failed":
                suffix = (
                    f"\n\n❌ No pude añadirla a {via}. "
                    "Puede que aún no esté en catálogo o haya un fallo de API. "
                    "Revisa el panel o inténtalo más tarde."
                )
            elif dl.status == "completed":
                suffix = f"\n\n✅ Ya estaba en disco (vía {via}). ¡A disfrutar!"
            else:
                suffix = (
                    f"\n\n✅ Pedida a *{via}* (máx. 1080p). "
                    "Está buscando en tus indexers (Prowlarr). "
                    "Te aviso cuando esté lista; si no aparece, puede que aún no haya releases."
                )
            if query.message and query.message.caption is not None:
                await query.edit_message_caption(
                    caption=(query.message.caption or "") + suffix,
                    parse_mode="Markdown",
                    reply_markup=None,
                )
            else:
                await query.edit_message_text(
                    (query.message.text or "") + suffix,
                    parse_mode="Markdown",
                    reply_markup=None,
                )
        elif action == "no":
            rec.status = "rejected"
            rec.feedback_at = datetime.utcnow()
            apply_feedback(db, person.id, liked=None, genres=genres, year=rec.year, actors=actors)
            db.commit()
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("Ok, no te la recomendaré más.")
        elif action == "seen":
            from app.services.ratings import create_rating_prompt

            rec.status = "already_seen"
            rec.feedback_at = datetime.utcnow()
            mark_download_watched(db, person.id, rec.tmdb_id, rec.media_type)
            db.commit()
            await query.edit_message_reply_markup(reply_markup=None)
            # Fast learning: stars right after "Ya la vi"
            prompt = create_rating_prompt(
                db,
                person,
                media_type=rec.media_type,
                tmdb_id=rec.tmdb_id,
                title=rec.title,
                year=rec.year,
                recommendation_id=rec.id,
                poster_path=rec.poster_path,
                context="after_seen",
            )
            ok = await send_rating_prompt(context.bot, person, prompt)
            if ok:
                prompt.recommendation_id = -1  # already delivered
                db.commit()
            else:
                await query.message.reply_text(
                    "¿Cómo te pareció? Responde cuando puedas con /help si no ves las estrellas."
                )
        elif action == "like":
            rec.status = "liked"
            apply_feedback(db, person.id, liked=True, genres=genres, year=rec.year, actors=actors)
            db.commit()
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("👍 Anotado en tu perfil.")
        elif action == "dislike":
            rec.status = "disliked"
            apply_feedback(db, person.id, liked=False, genres=genres, year=rec.year, actors=actors)
            db.commit()
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("👎 Anotado, ajustaré futuras recomendaciones.")
        elif action == "keep":
            from app.models import DownloadRequest

            dl = (
                db.query(DownloadRequest)
                .filter_by(tmdb_id=rec.tmdb_id, media_type=rec.media_type)
                .order_by(DownloadRequest.id.desc())
                .first()
            )
            if dl:
                set_keep(db, dl.id, person.id)
            rec.status = "kept"
            db.commit()
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text("🔒 Marcada para conservar (no se borrará sola).")
    except Exception as exc:
        log_error(db, "telegram", "Error en callback", str(exc))
        logger.exception("callback error")
    finally:
        db.close()


async def send_recommendation(db: Session, bot, person: Person, rec: Recommendation) -> bool:
    if not person.telegram_chat_id:
        return False
    cfg = service_cfg(db)
    tmdb = TMDbClient(cfg["tmdb_api_key"])
    poster = tmdb.poster_url(rec.poster_path)
    year = f" ({rec.year})" if rec.year else ""
    kind = "Serie" if rec.media_type == "tv" else "Película"
    title = _md_escape(rec.title or "")
    overview = _md_escape((rec.overview or "")[:600])
    caption = f"🎬 *{title}*{year}\n_{kind}_\n\n{overview}"
    chat = int(person.telegram_chat_id)
    kb = _rec_keyboard(rec.id)
    try:
        if poster:
            await bot.send_photo(
                chat_id=chat,
                photo=poster,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=kb,
            )
        else:
            await bot.send_message(
                chat_id=chat,
                text=caption,
                parse_mode="Markdown",
                reply_markup=kb,
            )
        rec.status = "sent"
        rec.sent_at = datetime.utcnow()
        db.commit()
        return True
    except Exception as exc:
        # Retry plain text if Markdown parsing fails on title/overview
        try:
            plain = f"🎬 {rec.title}{year}\n{kind}\n\n{(rec.overview or '')[:600]}"
            if poster:
                await bot.send_photo(chat_id=chat, photo=poster, caption=plain, reply_markup=kb)
            else:
                await bot.send_message(chat_id=chat, text=plain, reply_markup=kb)
            rec.status = "sent"
            rec.sent_at = datetime.utcnow()
            db.commit()
            return True
        except Exception as exc2:
            log_error(db, "telegram", f"No se pudo enviar rec a {person.name}", f"{exc} | retry: {exc2}")
            return False


async def notify_ready(bot, chat_id: str, title: str) -> None:
    try:
        await bot.send_message(chat_id=int(chat_id), text=f"Ya está lista en Jellyfin 🎬\n*{title}*", parse_mode="Markdown")
    except Exception:
        logger.exception("notify_ready failed")


def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ayuda", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("peliculas", cmd_peliculas))
    app.add_handler(CommandHandler("series", cmd_series))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


async def _run_polling(app: Application) -> None:
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    # keep alive until cancelled
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


async def ensure_bot_running() -> str:
    """Start or restart bot if token present. Returns status string."""
    global _app, _task, _bot_token_running
    db = SessionLocal()
    try:
        token = (get_setting(db, "telegram_bot_token") or "").strip()
        if not token:
            set_setting(db, "bot_status", "waiting_for_token")
            if _task and not _task.done():
                _task.cancel()
            _app = None
            _bot_token_running = None
            return "waiting_for_token"

        if _bot_token_running == token and _task and not _task.done():
            set_setting(db, "bot_status", "running")
            return "running"

        if _task and not _task.done():
            _task.cancel()
            try:
                await _task
            except Exception:
                pass

        _app = build_application(token)
        _bot_token_running = token
        _task = asyncio.create_task(_run_polling(_app))
        set_setting(db, "bot_status", "running")
        return "running"
    except Exception as exc:
        set_setting(db, "bot_status", f"error: {exc}")
        log_error(db, "telegram", "No se pudo iniciar el bot", str(exc))
        return f"error: {exc}"
    finally:
        db.close()


def get_bot_app() -> Optional[Application]:
    return _app


async def dispatch_pending_recommendations() -> int:
    app = _app
    if not app or not app.bot:
        return 0
    db = SessionLocal()
    sent = 0
    try:
        people = db.query(Person).filter(Person.active.is_(True), Person.paused.is_(False), Person.telegram_chat_id.isnot(None)).all()
        for person in people:
            pending = (
                db.query(Recommendation)
                .filter_by(person_id=person.id, status="pending")
                .order_by(Recommendation.score.desc())
                .limit(2)
                .all()
            )
            for rec in pending:
                if await send_recommendation(db, app.bot, person, rec):
                    sent += 1
                    await asyncio.sleep(1)
    finally:
        db.close()
    return sent


async def notify_completed_downloads(ready_ids) -> None:
    """Notify users that downloads are ready. `ready_ids` are DownloadRequest PKs."""
    app = _app
    if not app or not app.bot or not ready_ids:
        return
    db = SessionLocal()
    try:
        for dl_id in ready_ids:
            dl = db.get(DownloadRequest, int(dl_id))
            if not dl or dl.notified_ready:
                continue
            person = db.get(Person, dl.person_id)
            if person and person.telegram_chat_id:
                await notify_ready(app.bot, person.telegram_chat_id, dl.title)
                dl.notified_ready = True
        db.commit()
    finally:
        db.close()
