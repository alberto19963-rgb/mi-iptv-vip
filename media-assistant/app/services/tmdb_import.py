"""Import TMDb API key from Jellyfin plugin config when Media Assistant has none.

Jellyfin stores it in Jellyfin.Plugin.Tmdb.xml (often empty — Jellyfin may use a
built-in default for metadata). We never log or return the raw key to the UI.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.services.settings_store import get_setting, set_setting

# Common host/container mounts for Jellyfin linuxserver config
_CANDIDATE_PATHS = (
    Path(os.getenv("JELLYFIN_CONFIG_PATH", "")),
    Path("/jellyfin-config/configurations/Jellyfin.Plugin.Tmdb.xml"),
    Path("/jellyfin-config/data/plugins/configurations/Jellyfin.Plugin.Tmdb.xml"),
    Path("/config/configurations/Jellyfin.Plugin.Tmdb.xml"),
)


def _extract_key_from_xml(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        root = ET.fromstring(text)
        node = root.find("TmdbApiKey")
        if node is not None and (node.text or "").strip():
            return node.text.strip()
    except ET.ParseError:
        pass
    m = re.search(r"<TmdbApiKey>([^<]+)</TmdbApiKey>", text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return None


def read_tmdb_key_from_jellyfin_files() -> Optional[str]:
    for path in _CANDIDATE_PATHS:
        if not path or not str(path):
            continue
        targets = [path] if path.suffix.lower() == ".xml" else [
            path / "configurations" / "Jellyfin.Plugin.Tmdb.xml",
            path / "data" / "plugins" / "configurations" / "Jellyfin.Plugin.Tmdb.xml",
        ]
        for target in targets:
            try:
                if target.is_file():
                    key = _extract_key_from_xml(target.read_text(encoding="utf-8", errors="ignore"))
                    if key:
                        return key
            except OSError:
                continue
    return None


def ensure_tmdb_key_from_jellyfin(db: Session) -> dict:
    """If Media Assistant has no TMDb key, copy from Jellyfin plugin XML when present."""
    existing = (get_setting(db, "tmdb_api_key") or "").strip()
    if existing:
        return {"imported": False, "reason": "already_set", "has_key": True}
    key = read_tmdb_key_from_jellyfin_files()
    if not key:
        return {"imported": False, "reason": "jellyfin_empty_or_missing", "has_key": False}
    set_setting(db, "tmdb_api_key", key)
    return {"imported": True, "reason": "ok", "has_key": True, "key_len": len(key)}
