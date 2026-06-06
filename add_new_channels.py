import os
import urllib.request
import ssl

m3u_file = "/Users/albertorosario/Documents/IPTV/mi_lista_personal.m3u"

def fetch_and_append(url, limit, name_prefix=""):
    print(f"Buscando canales de {url}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            content = response.read().decode('utf-8')
            lines = content.splitlines()
            
            added = 0
            i = 0
            while i < len(lines) and added < limit:
                line = lines[i]
                if line.startswith("#EXTINF"):
                    extinf = line
                    url_line = lines[i+1] if i+1 < len(lines) else ""
                    if url_line and not url_line.startswith("#"):
                        with open(m3u_file, 'a', encoding='utf-8') as f:
                            f.write(f"{extinf}\n{url_line}\n")
                        added += 1
                        i += 2
                        continue
                i += 1
            print(f"Añadidos {added} canales.")
    except Exception as e:
        print(f"Error fetching {url}: {e}")

fetch_and_append("https://iptv-org.github.io/iptv/countries/mx.m3u", 8)
fetch_and_append("https://iptv-org.github.io/iptv/countries/ve.m3u", 8)
fetch_and_append("https://iptv-org.github.io/iptv/categories/kids.m3u", 15)

