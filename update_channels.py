import os
import urllib.request
import concurrent.futures
import time
import re

def assign_category(extinf, url):
    full_text = (extinf + " " + url).lower()
    kw_sports = ["sports", "espn", "nba", "nfl", "mlb", "nhl", "golf", "tennis", "deportes", "bein", "wwe"]
    kw_movies = ["movies", "cine", "hbo", "starz", "showtime", "cinemax", "amc", "tcm", "paramount", "fx", "hallmark", "action", "comedy", "drama", "thriller", "epix"]
    kw_news = ["news", "noticias", "weather"]
    kw_do = ["telemicro", "color vision", "cdn", "teleantillas", "telecentro", "dominicana", "canal 4", "rnntv", "microvision"]
    kw_mx = ["mexico", "méxico", "mx@", "azteca", "adn 40", "adn40", "canal 10", "mexiquense", "televisa", "foro", "tudn", "milenio", "multimedios", "guanajuato", "fresnillo", "zacatecas"]
    kw_ve = ["venezuela", "ve@", "venevision", "televen", "globovision", "vtv", "aguacate", "alcance tv", "anzoategui", "barinas", "as3 sport"]
    kw_kids = ["kids", "infantil", "cartoon", "disney", "nickelodeon", "discovery kids", "boomerang", "pbs kids", "afarin", "anim", "niños"]
    
    # Excepción para discovery en español
    if "discovery en espa" in full_text:
        return "🌎 Canales Generales"
        
    if any(kw in full_text for kw in kw_do):
        return "🇩🇴 Rep. Dom."
    elif any(kw in full_text for kw in kw_mx):
        return "🇲🇽 México"
    elif any(kw in full_text for kw in kw_ve):
        return "🇻🇪 Venezuela"
    elif any(kw in full_text for kw in kw_kids):
        return "🧸 Infantiles"
    elif any(kw in full_text for kw in kw_sports):
        return "🏆 Deportes"
    elif any(kw in full_text for kw in kw_movies):
        return "🎬 Películas"
    elif any(kw in full_text for kw in kw_news):
        return "📰 Noticias"
    else:
        return "🌎 Canales Generales"

master_file = "lista_maestra.m3u"
m3u_file = "mi_lista_personal.m3u"
txt_file = "canales_disponibles.txt"

print(f"Abriendo {master_file}...")
with open(master_file, 'r', encoding='utf-8') as f:
    lines = [line.strip() for line in f.readlines() if line.strip()]

channels = []
i = 1
while i < len(lines):
    if lines[i].startswith("#EXTINF:"):
        extinf = lines[i]
        url = lines[i+1] if (i+1 < len(lines) and not lines[i+1].startswith("#")) else None
        if url:
            channels.append((extinf, url))
            i += 2
            continue
    i += 1

def check_url(channel):
    extinf, url = channel
    req = urllib.request.Request(url, headers={'User-Agent': 'VLC/3.0.9 LibVLC/3.0.9'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                return channel
    except Exception:
        pass
    return None

working_channels = []
print(f"Verificando {len(channels)} canales...")
start = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
    results = list(executor.map(check_url, channels))

for res in results:
    if res is not None:
        working_channels.append(res)

print(f"Completado en {time.time()-start:.2f}s. Canales activos: {len(working_channels)} / {len(channels)}")

with open(m3u_file, 'w', encoding='utf-8') as f:
    f.write('#EXTM3U x-tvg-url="https://iptv-org.github.io/epg/guides.xml"\n')
    for extinf, url in working_channels:
        new_category = assign_category(extinf, url)
        # Reemplazar el group-title existente o añadirlo si no existe
        if 'group-title="' in extinf:
            extinf = re.sub(r'group-title="[^"]*"', f'group-title="{new_category}"', extinf)
        else:
            extinf = extinf.replace(',', f' group-title="{new_category}",', 1)
        f.write(extinf + "\n")
        f.write(url + "\n")

with open(txt_file, 'w', encoding='utf-8') as f:
    f.write(f"LISTA DE CANALES FINAL VIP ({len(working_channels)} canales)\n")
    f.write("============================================================\n\n")
    for extinf, url in working_channels:
        group = assign_category(extinf, url)
        name = extinf.split(',')[-1].strip()
        f.write(f"Canal: {name.ljust(40)} | Categoría: {group}\n")

print("Archivos actualizados correctamente.")
