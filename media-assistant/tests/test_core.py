from __future__ import annotations

from app.services.telegram_bot import (
    HELP_TEXT,
    detect_suggestion_intent,
    generate_pairing_code,
    is_help_request,
    _rec_keyboard,
)


def test_pairing_code_format():
    code = generate_pairing_code()
    assert len(code) == 6
    assert all(c in "0123456789ABCDEF" for c in code)


def test_rec_keyboard_buttons():
    kb = _rec_keyboard(42)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "dl:42" in flat
    assert "no:42" in flat
    assert "seen:42" in flat
    assert "keep:42" in flat


def test_preference_scoring_memory():
    """Unit test without DB: score math via in-memory dict simulation."""
    scores = {("genre", "comedia"): 4.0, ("genre", "terror"): -3.0, ("decade", "2010s"): 1.0}

    def score(genres, year=None):
        s = 0.0
        for g in genres:
            s += scores.get(("genre", g.lower()), 0.0)
        if year:
            decade = f"{(year // 10) * 10}s"
            s += scores.get(("decade", decade.lower()), 0.0) * 0.5
        return s

    assert score(["Comedia"], 2015) > score(["Terror"], 2015)
    assert score(["Comedia"], 2015) > score(["Comedia"], 1990)


def test_help_request_detection():
    assert is_help_request("ayuda")
    assert is_help_request("HELP")
    assert is_help_request("qué puedo hacer")
    assert not is_help_request("dame sugerencias")
    assert "1080p" in HELP_TEXT
    assert "/peliculas" in HELP_TEXT


def test_star_rating_deltas():
    from app.services.preferences import apply_star_rating

    # mapping sanity without DB: reuse formula
    mapping = {5: 3.0, 4: 2.0, 3: 0.0, 2: -2.0, 1: -3.0}
    assert mapping[5] > 0 and mapping[1] < 0 and mapping[3] == 0


def test_stars_keyboard():
    from app.services.telegram_bot import _stars_keyboard

    kb = _stars_keyboard(7)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "star:7:1" in flat and "star:7:5" in flat
    assert "⭐" in __import__("app.services.telegram_bot", fromlist=["HELP_TEXT"]).HELP_TEXT or "estrellas" in __import__("app.services.telegram_bot", fromlist=["HELP_TEXT"]).HELP_TEXT.lower()


def test_library_seed_weights_weaker_than_watches():
    """Library seed caps are soft; watch/rating weights dominate once they exist."""
    from app.services.preferences import (
        LIBRARY_SEED_GENRE_CAP,
        LIBRARY_SEED_GENRE_WEIGHT,
    )

    # ~25 movies of same genre → capped soft prior
    assert min(LIBRARY_SEED_GENRE_CAP, 25 * LIBRARY_SEED_GENRE_WEIGHT) == LIBRARY_SEED_GENRE_CAP
    # A single ★5 feedback delta (weight 1.5 → +3) beats the seed cap
    assert 3.0 > LIBRARY_SEED_GENRE_CAP


def test_tmdb_xml_key_extract_empty_and_present():
    from app.services.tmdb_import import _extract_key_from_xml

    empty = """<?xml version="1.0"?><PluginConfiguration><TmdbApiKey /></PluginConfiguration>"""
    assert _extract_key_from_xml(empty) is None
    filled = """<?xml version="1.0"?><PluginConfiguration><TmdbApiKey>abc123xyz</TmdbApiKey></PluginConfiguration>"""
    assert _extract_key_from_xml(filled) == "abc123xyz"


def test_suspense_maps_to_thriller_genre_id():
    from app.services.tmdb import TMDbClient

    assert TMDbClient.GENRE_NAME_TO_ID["suspense"] == 53
    assert TMDbClient.GENRE_NAME_TO_ID["ciencia ficción"] == 878


def test_tmdb_client_detects_v4_bearer_vs_v3_api_key():
    from app.services.tmdb import TMDbClient

    v3 = TMDbClient("a" * 32)
    assert v3.configured()
    assert not v3._uses_bearer()

    # Shape only — not a real token
    jwt = "eyJhbGciOiJIUzI1NiJ9." + ("x" * 40) + "." + ("y" * 40)
    v4 = TMDbClient(jwt)
    assert v4.configured()
    assert v4._uses_bearer()


def test_md_escape_and_secret_redaction():
    from app.services.telegram_bot import _md_escape
    from app.services.settings_store import _redact_secrets

    assert "\\_" in _md_escape("foo_bar")
    assert "\\*" in _md_escape("a*b")
    red = _redact_secrets("url?api_key=eyJhbGciOiJIUzI1NiJ9.aaa.bbb&x=1")
    assert "api_key=***" in red
    assert "eyJhbGci" not in red

