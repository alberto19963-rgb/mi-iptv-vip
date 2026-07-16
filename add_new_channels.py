from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
M3U_FILE = ROOT / "mi_lista_personal.m3u"
SOURCES = [
    ("https://iptv-org.github.io/iptv/countries/mx.m3u", 8),
    ("https://iptv-org.github.io/iptv/countries/ve.m3u", 8),
    ("https://iptv-org.github.io/iptv/categories/kids.m3u", 15),
]


def fetch_and_append(url, limit):
    print(f"Buscando canales de {url}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            content = response.read().decode("utf-8")
            lines = content.splitlines()

            added = 0
            i = 0
            while i < len(lines) and added < limit:
                line = lines[i]
                if line.startswith("#EXTINF"):
                    url_line = lines[i + 1] if i + 1 < len(lines) else ""
                    if url_line and not url_line.startswith("#"):
                        with M3U_FILE.open("a", encoding="utf-8") as f:
                            f.write(f"{line}\n{url_line}\n")
                        added += 1
                        i += 2
                        continue
                i += 1
            print(f"Añadidos {added} canales.")
    except Exception as e:
        print(f"Error fetching {url}: {e}")


def main():
    if not M3U_FILE.exists():
        M3U_FILE.write_text('#EXTM3U x-tvg-url="https://iptv-org.github.io/epg/guides.xml"\n', encoding="utf-8")

    for source_url, limit in SOURCES:
        fetch_and_append(source_url, limit)


if __name__ == "__main__":
    main()

