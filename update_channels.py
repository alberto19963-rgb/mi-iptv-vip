import os
import urllib.request
import concurrent.futures
import time
import re

m3u_file = "mi_lista_personal.m3u"
txt_file = "canales_disponibles.txt"

print(f"Abriendo {m3u_file}...")
with open(m3u_file, 'r', encoding='utf-8') as f:
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
        f.write(extinf + "\n")
        f.write(url + "\n")

with open(txt_file, 'w', encoding='utf-8') as f:
    f.write(f"LISTA DE CANALES FINAL VIP ({len(working_channels)} canales)\n")
    f.write("============================================================\n\n")
    for extinf, url in working_channels:
        group_match = re.search(r'group-title="([^"]+)"', extinf)
        group = group_match.group(1) if group_match else "Desconocido"
        name = extinf.split(',')[-1].strip()
        f.write(f"Canal: {name.ljust(40)} | Categoría: {group}\n")

print("Archivos actualizados correctamente.")
