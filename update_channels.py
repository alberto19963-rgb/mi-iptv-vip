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
EPG_URL = "https://iptv-org.github.io/epg/guides.xml"
MAX_WORKERS = 40
URL_TIMEOUT = 12

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


def parse_channels(path):
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    channels = []
    i = 0

    while i < len(lines):
        if lines[i].startswith("#EXTINF:"):
            extinf = lines[i]
            url = lines[i + 1] if i + 1 < len(lines) and not lines[i + 1].startswith("#") else None
            if url:
                channels.append((extinf, url))
                i += 2
                continue
        i += 1

    return channels


def check_url(channel):
    _, url = channel
    req = urllib.request.Request(url, headers={"User-Agent": "VLC/3.0.9 LibVLC/3.0.9"})
    try:
        with urllib.request.urlopen(req, timeout=URL_TIMEOUT) as response:
            if 200 <= response.status < 400:
                return channel
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    return None


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


def write_m3u(path, channels):
    with path.open("w", encoding="utf-8") as f:
        f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
        for extinf, url in channels:
            category = assign_category(extinf, url)
            f.write(set_group_title(extinf, category) + "\n")
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


def write_outputs(working_channels):
    sorted_channels = sort_channels(working_channels)
    write_m3u(OUTPUT_M3U, sorted_channels)
    write_summary(OUTPUT_TXT, sorted_channels)


def recategorize_lists():
    """Reasigna grupos por país/tema sin verificar URLs (usa la lista maestra)."""
    from collections import Counter

    channels = sort_channels(parse_channels(MASTER_FILE))
    write_m3u(MASTER_FILE, channels)
    write_m3u(OUTPUT_M3U, channels)
    write_summary(OUTPUT_TXT, channels)

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
