import concurrent.futures
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
MASTER_FILE = ROOT / "lista_maestra.m3u"
OUTPUT_M3U = ROOT / "mi_lista_personal.m3u"
OUTPUT_TXT = ROOT / "canales_disponibles.txt"
GUIDE_FILE = ROOT / "guia.xml"
LOGO_API = "https://iptv-org.github.io/api/logos.json"
# Guía nuestra. raw.githubusercontent.com la sirve como text/plain y el
# celular no la trata como XML. jsDelivr la sirve como application/xml.
EPG_URL = "https://cdn.jsdelivr.net/gh/alberto19963-rgb/mi-iptv-vip@main/guia.xml"
EPG_SOURCE = "https://iptv-epg.org/files/epg-do.xml.gz"
# id iptv-org -> id de nuestra guía / id en iptv-epg.org
DO_GUIDE = {
    "CDN.do": ("CDN", "CDN.dr"),
    "TeleAntillas.do": ("TeleAntillas", "TeleAntillas.dr"),
    "Telemicro.do": ("Telemicro", "Telemicro.dr"),
    "Telesistema11.do": ("Telesistema11", "Telesistema11.dr"),
    "Teleunion.do": ("Teleunion", "Teleunion.dr"),
}
MAX_WORKERS = 40
URL_TIMEOUT = 12
QUALITY_RE = re.compile(r"\s*\(\d{3,4}[pi]\)", re.IGNORECASE)
STATUS_TAG_RE = re.compile(r"\s*\[(?:Not 24/7|Geo-blocked|Geo blocked)\]", re.IGNORECASE)

# País principal desde tvg-id de iptv-org: Canal.xx@Feed
# Solo estos países tienen carpeta propia; el resto va a "Otros".
COUNTRY_GROUPS = {
    "do": "🇩🇴 República Dominicana",
    "pr": "🇵🇷 Puerto Rico",
    "mx": "🇲🇽 México",
    "ve": "🇻🇪 Venezuela",
    "us": "🇺🇸 Estados Unidos",
}

PRIMARY_COUNTRIES = {"do", "pr", "mx", "ve"}

THEME_GROUPS = {
    "🏆 Deportes": [
        "sports",
        "sport",
        "espn",
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "golf",
        "tennis",
        "deportes",
        "bein",
        "wwe",
        "fifa",
        "racing",
        "f1",
        "ufc",
        "boxing",
        "soccer",
        "football",
    ],
    "🎬 Películas": [
        "movies",
        "movie",
        "cine",
        "cinema",
        "film",
        "hbo",
        "starz",
        "showtime",
        "cinemax",
        "amc",
        "tcm",
        "paramount",
        "hallmark",
        "thriller",
        "epix",
        "western",
        "horror",
    ],
    "🧸 Infantiles": [
        "kids",
        "infantil",
        "cartoon",
        "disney",
        "nickelodeon",
        "discovery kids",
        "boomerang",
        "pbs kids",
        "afarin",
        "niños",
        "child",
        "atfal",
    ],
    "📰 Noticias": [
        "news",
        "noticias",
        "weather",
        "cnn",
        "msnbc",
        "breaking",
    ],
}

GROUP_ORDER = [
    "🇩🇴 República Dominicana",
    "🇵🇷 Puerto Rico",
    "🇲🇽 México",
    "🇻🇪 Venezuela",
    "🇺🇸 EE.UU. · 🏆 Deportes",
    "🇺🇸 EE.UU. · 🎬 Películas",
    "🇺🇸 EE.UU. · 🧸 Infantiles",
    "🇺🇸 EE.UU. · 📰 Noticias",
    "🇺🇸 Estados Unidos",
    "🌍 Otros · 🏆 Deportes",
    "🌍 Otros · 🎬 Películas",
    "🌍 Otros · 🧸 Infantiles",
    "🌍 Otros · 📰 Noticias",
    "🌍 Otros",
]

TVG_ID_RE = re.compile(r'tvg-id="([^"]*)"', re.IGNORECASE)
COUNTRY_CODE_RE = re.compile(r"\.([a-z]{2})(?:@|$)")
EXTINF_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
DEFAULT_USER_AGENT = "VLC/3.0.9 LibVLC/3.0.9"


def channel_name(extinf):
    return extinf.split(",")[-1].strip()


def extract_country_code(extinf):
    match = TVG_ID_RE.search(extinf)
    if not match or not match.group(1):
        return None
    code = COUNTRY_CODE_RE.search(match.group(1).lower())
    return code.group(1) if code else None


def detect_theme(extinf, url):
    text = f"{extinf} {url}".lower()
    for theme, keywords in THEME_GROUPS.items():
        if any(keyword in text for keyword in keywords):
            return theme
    return None


def assign_category(extinf, url=""):
    """Clasifica por país (tvg-id) y, en EE.UU., también por tema."""
    code = extract_country_code(extinf)

    if code in PRIMARY_COUNTRIES:
        return COUNTRY_GROUPS[code]

    if code == "us":
        theme = detect_theme(extinf, url)
        if theme:
            return f"🇺🇸 EE.UU. · {theme}"
        return "🇺🇸 Estados Unidos"

    theme = detect_theme(extinf, url)
    if theme:
        return f"🌍 Otros · {theme}"
    return "🌍 Otros"


def extinf_attr(extinf, name):
    attrs = {key.lower(): value for key, value in EXTINF_ATTR_RE.findall(extinf)}
    return attrs.get(name.lower())


def parse_channels(path):
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    channels = []
    i = 0

    while i < len(lines):
        if lines[i].startswith("#EXTINF:"):
            extinf = lines[i]
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                j += 1
            if j < len(lines):
                channels.append((extinf, lines[j]))
                i = j + 1
                continue
        i += 1

    return channels


def stream_ok(status, content_type, chunk):
    """Acepta una lista HLS o video. Rechaza páginas HTML y errores JSON."""
    if not (200 <= status < 400):
        return False
    ctype = (content_type or "").lower()
    sample = chunk.lstrip()[:24].lower()
    if "text/html" in ctype or "application/json" in ctype or "text/json" in ctype:
        return False
    if sample.startswith((b"<html", b"<!doctype", b"<", b"{", b"[")):
        return False
    return True


def check_url(channel):
    extinf, url = channel
    headers = {"User-Agent": extinf_attr(extinf, "http-user-agent") or DEFAULT_USER_AGENT}
    referrer = extinf_attr(extinf, "http-referrer") or extinf_attr(extinf, "http-referer")
    if referrer:
        headers["Referer"] = referrer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=URL_TIMEOUT) as response:
            chunk = response.read(1024)
            if stream_ok(response.status, response.headers.get("Content-Type", ""), chunk):
                return channel
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    return None


def player_name(extinf):
    """Nombre limpio para que OTTPlayer lo compare con su biblioteca de guía."""
    name = channel_name(extinf)
    previous = None
    while name != previous:
        previous = name
        name = QUALITY_RE.sub("", name)
        name = STATUS_TAG_RE.sub("", name)
    return name.strip() or channel_name(extinf)


def load_logos():
    try:
        request = urllib.request.Request(LOGO_API, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            logos = json_load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {}
    grouped = {}
    for logo in logos:
        url = logo.get("url") or ""
        channel = logo.get("channel") or ""
        if not url or not channel:
            continue
        grouped.setdefault(channel, []).append(logo)
    return grouped


def json_load(response):
    import json

    return json.load(response)


def best_logo(logos, extinf):
    channel_id = extinf_attr(extinf, "tvg-id") or ""
    base, _, feed = channel_id.partition("@")
    options = logos.get(base) or []
    if not options:
        return None
    same_feed = [logo for logo in options if feed and logo.get("feed") == feed]
    generic = [logo for logo in options if not logo.get("feed")]
    pool = same_feed or generic or options
    pool.sort(key=lambda logo: logo.get("width") or 0, reverse=True)
    return pool[0]["url"]


def set_logo(extinf, logo_url):
    if not logo_url:
        return extinf
    current = extinf_attr(extinf, "tvg-logo")
    if current:
        return extinf
    if 'tvg-logo="' in extinf:
        return re.sub(r'tvg-logo="[^"]*"', f'tvg-logo="{logo_url}"', extinf)
    return extinf.replace("tvg-id=", f'tvg-logo="{logo_url}" tvg-id=', 1)


def guide_id_for(tvg_id):
    base = (tvg_id or "").split("@")[0]
    if base in DO_GUIDE:
        return DO_GUIDE[base][0]
    for ours, _source in DO_GUIDE.values():
        if base == ours:
            return ours
    return None


def xml_escape(text):
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def xmltv_to_local(stamp):
    """Pasa YYYYMMDDHHMMSS ±HHMM a hora de República Dominicana (−0400)."""
    from datetime import datetime, timedelta, timezone

    match = re.match(r"^(\d{14})(?:\s*([+-]\d{4}))?", (stamp or "").strip())
    if not match:
        return stamp
    digits, offset = match.group(1), match.group(2) or "+0000"
    sign = 1 if offset[0] == "+" else -1
    minutes = sign * (int(offset[1:3]) * 60 + int(offset[3:5]))
    utc = datetime.strptime(digits, "%Y%m%d%H%M%S").replace(
        tzinfo=timezone(timedelta(minutes=minutes))
    ).astimezone(timezone.utc)
    local = utc.astimezone(timezone(timedelta(hours=-4)))
    return local.strftime("%Y%m%d%H%M%S -0400")


def write_guia():
    """ Reescribe iptv-epg.org en XMLTV válido, solo los canales de la lista."""
    req = urllib.request.Request(EPG_SOURCE, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read()
    if raw[:2] == b"\x1f\x8b":
        import gzip

        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", "replace")
    wanted = {source: ours for ours, source in DO_GUIDE.values()}
    names = {
        "CDN": "CDN",
        "TeleAntillas": "Tele Antillas",
        "Telemicro": "Telemicro",
        "Telesistema11": "Telesistema 11",
        "Teleunion": "Teleunion",
    }
    # El celular puede emparejar por el id, por el nombre visible o por el
    # nombre viejo "DR - …" si todavía no recargó la lista.
    aliases = {
        "CDN": ["CDN", "DR - CDN"],
        "TeleAntillas": ["TeleAntillas", "Tele Antillas", "DR - Tele Antillas"],
        "Telemicro": ["Telemicro", "DR - Telemicro"],
        "Telesistema11": ["Telesistema11", "Telesistema 11", "DR - Telesistema 11"],
        "Teleunion": ["Teleunion", "DR - Teleunion"],
    }
    programmes = []
    for start, stop, channel, body in re.findall(
        r'<programme\s+start="([^"]+)"\s+stop="([^"]+)"\s+channel="([^"]+)"\s*>(.*?)</programme>',
        text,
        re.S,
    ):
        ours = wanted.get(channel)
        if not ours:
            continue
        title = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
        desc = re.search(r"<desc[^>]*>(.*?)</desc>", body, re.S)
        title_text = re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else ""
        desc_text = re.sub(r"<[^>]+>", "", desc.group(1)).strip() if desc else ""
        if not title_text:
            continue
        local_start = xmltv_to_local(start)
        local_stop = xmltv_to_local(stop)
        for alias in aliases[ours]:
            programmes.append((local_start, local_stop, alias, title_text, desc_text))
    if not programmes:
        raise RuntimeError("La fuente de guía no trajo programas para los canales de la lista.")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<tv>",
    ]
    for ours, alias_ids in aliases.items():
        for alias in alias_ids:
            lines.append(f'  <channel id="{xml_escape(alias)}">')
            lines.append(f"    <display-name>{xml_escape(alias)}</display-name>")
            if alias != names[ours]:
                lines.append(f"    <display-name>{xml_escape(names[ours])}</display-name>")
            lines.append("  </channel>")
    for start, stop, alias, title_text, desc_text in programmes:
        lines.append(f'  <programme start="{start}" stop="{stop}" channel="{xml_escape(alias)}">')
        lines.append(f"    <title>{xml_escape(title_text)}</title>")
        if desc_text:
            lines.append(f"    <desc>{xml_escape(desc_text)}</desc>")
        lines.append("  </programme>")
    lines.append("</tv>")
    lines.append("")
    GUIDE_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Guía XMLTV: {len(programmes)} programas en {GUIDE_FILE.name}.")
    return len(programmes)


def set_tvg_id(extinf, tvg_id):
    if not tvg_id or not TVG_ID_RE.search(extinf):
        return extinf
    return TVG_ID_RE.sub(f'tvg-id="{tvg_id}"', extinf, count=1)


def set_tvg_name(extinf, name):
    if not name:
        return extinf
    if 'tvg-name="' in extinf:
        return re.sub(r'tvg-name="[^"]*"', f'tvg-name="{name}"', extinf)
    return extinf.replace("tvg-id=", f'tvg-name="{name}" tvg-id=', 1)


def set_group_title(extinf, category):
    if 'group-title="' in extinf:
        return re.sub(r'group-title="[^"]*"', f'group-title="{category}"', extinf)
    return extinf.replace(",", f' group-title="{category}",', 1)


def sort_channels(channels):
    order = {name: index for index, name in enumerate(GROUP_ORDER)}

    def sort_key(channel):
        extinf, url = channel
        group = assign_category(extinf, url)
        return (order.get(group, 100), group, channel_name(extinf).lower())

    return sorted(channels, key=sort_key)


def write_m3u(path, channels, *, player=False, logos=None):
    logos = logos or {}
    with path.open("w", encoding="utf-8") as f:
        if player:
            f.write(f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n')
        else:
            f.write("#EXTM3U\n")
        for extinf, url in channels:
            category = assign_category(extinf, url)
            extinf = set_group_title(extinf, category)
            if player:
                extinf = set_logo(extinf, best_logo(logos, extinf))
                matched = guide_id_for(extinf_attr(extinf, "tvg-id") or "")
                name = player_name(extinf)
                if matched:
                    extinf = set_tvg_id(extinf, matched)
                    extinf = set_tvg_name(extinf, name)
                extinf = extinf[: extinf.rfind(",") + 1] + name
            f.write(extinf + "\n")
            if player:
                f.write(f"#EXTGRP:{category}\n")
            f.write(url + "\n")


def write_summary(path, channels):
    with path.open("w", encoding="utf-8") as f:
        f.write(f"LISTA DE CANALES GRATIS ({len(channels)} canales)\n")
        f.write("============================================================\n\n")
        current_group = None
        for extinf, url in channels:
            group = assign_category(extinf, url)
            if group != current_group:
                f.write(f"\n## {group}\n")
                current_group = group
            code = extract_country_code(extinf) or "??"
            f.write(f"Canal: {channel_name(extinf)} [{code.upper()}]\n")


def validate_player_m3u(path):
    text = path.read_text(encoding="utf-8")
    if "guides.xml" in text:
        raise RuntimeError("La lista publicada todavía apunta a una guía inexistente.")
    if EPG_URL not in text:
        raise RuntimeError("La lista publicada no apunta a nuestra guía XMLTV.")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise RuntimeError("La lista no empieza con #EXTM3U.")
    channels = 0
    index = 1
    while index < len(lines):
        extinf = lines[index]
        if not extinf.startswith("#EXTINF:"):
            raise RuntimeError(f"Se esperaba #EXTINF y llegó: {extinf[:80]}")
        group_line = lines[index + 1] if index + 1 < len(lines) else ""
        url = lines[index + 2] if index + 2 < len(lines) else ""
        if not group_line.startswith("#EXTGRP:"):
            raise RuntimeError(f"Falta #EXTGRP en {player_name(extinf)}")
        if not url.startswith(("http://", "https://")):
            raise RuntimeError(f"URL inválida en {player_name(extinf)}")
        name = player_name(extinf)
        visible = extinf.split(",")[-1].strip()
        if visible != name or QUALITY_RE.search(visible) or STATUS_TAG_RE.search(visible):
            raise RuntimeError(f"Nombre no apto para OTTPlayer: {visible}")
        if 'group-title="' not in extinf or not (extinf_attr(extinf, "tvg-id") or ""):
            raise RuntimeError(f"Falta grupo o tvg-id en {visible}")
        if group_line.removeprefix("#EXTGRP:") != (extinf_attr(extinf, "group-title") or ""):
            raise RuntimeError(f"El grupo no coincide en {visible}")
        channels += 1
        index += 3
    if channels == 0:
        raise RuntimeError("La lista publicada quedó vacía.")
    parsed = parse_channels(path)
    if len(parsed) != channels:
        raise RuntimeError("El lector de la lista no ve los mismos canales que se escribieron.")
    print(f"Lista OTTPlayer válida: {channels} canales.")
    return channels


def write_outputs(working_channels):
    sorted_channels = sort_channels(working_channels)
    logos = load_logos()
    write_guia()
    write_m3u(OUTPUT_M3U, sorted_channels, player=True, logos=logos)
    write_summary(OUTPUT_TXT, sorted_channels)
    validate_player_m3u(OUTPUT_M3U)


def recategorize_lists():
    """Reasigna grupos por país/tema sin verificar URLs (usa la lista maestra)."""
    from collections import Counter

    channels = sort_channels(parse_channels(MASTER_FILE))
    write_m3u(MASTER_FILE, channels)
    write_guia()
    write_m3u(OUTPUT_M3U, channels, player=True, logos=load_logos())
    write_summary(OUTPUT_TXT, channels)
    validate_player_m3u(OUTPUT_M3U)

    counts = Counter(assign_category(extinf, url) for extinf, url in channels)
    print(f"Reclasificados {len(channels)} canales:")
    for group, total in sorted(
        counts.items(),
        key=lambda item: (GROUP_ORDER.index(item[0]) if item[0] in GROUP_ORDER else 999, item[0]),
    ):
        print(f"  {group}: {total}")


def main():
    print(f"Abriendo {MASTER_FILE.name}...")
    channels = parse_channels(MASTER_FILE)
    print(f"Verificando {len(channels)} canales...")

    start = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        working_channels = [channel for channel in executor.map(check_url, channels) if channel is not None]

    elapsed = time.time() - start
    print(f"Completado en {elapsed:.2f}s. Canales activos: {len(working_channels)} / {len(channels)}")

    if not working_channels:
        raise RuntimeError("No se encontró ningún canal activo; no se sobrescriben los archivos.")

    write_outputs(working_channels)
    print("Archivos actualizados correctamente.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--recategorize":
        recategorize_lists()
    else:
        main()
