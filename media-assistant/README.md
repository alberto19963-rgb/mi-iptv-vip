# Media Assistant — asistente multi-usuario de películas/series para RosarioNAS

## Qué hace

- Panel web (HTMX + Tailwind) en el puerto **8510**
- Bot de Telegram con recomendaciones personalizadas
- Seguimiento de historial Jellyfin y aprendizaje de gustos (géneros, décadas)
- **Cold-start:** si una persona aún no tiene señales, se siembran preferencias suaves desde las películas que ya tiene en la biblioteca Jellyfin (presencia ≠ visto). Al ver o valorar ★, esas señales más fuertes prevalecen.
- Las películas ya en la biblioteca no se vuelven a recomendar
- Descarga vía Radarr/Sonarr (prioridad perfil ESP - HD)
- Retención: borra vía API de *arr tras N días (nunca borra archivos a mano)
- PostgreSQL dedicado, bajo consumo de RAM

## URLs

- Panel: http://192.168.68.208:8510
- Health: http://192.168.68.208:8510/health

## Requisitos previos

1. Crear un bot con [@BotFather](https://t.me/BotFather) y copiar el token
2. Crear una API key gratuita en [TMDb](https://www.themoviedb.org/settings/api) (cuenta gratis → Settings → API → Request an API Key → Developer). **TMDb es solo metadatos** (póster, año, sinopsis, ID). Las descargas van siempre por **Radarr/Sonarr → Prowlarr/indexers**. Pega la clave en **Ajustes** del Media Assistant. Si Jellyfin ya tiene clave en su plugin TMDb, el arranque intenta copiarla automáticamente.
3. Las claves de Jellyfin/Radarr/Sonarr/Bazarr se pueden rellenar en el panel (el despliegue suele prefijarlas)

## Valoraciones ⭐

- **Películas:** antes de borrar (tras retención) el bot pregunta 1–5 estrellas a quien la vio en Jellyfin.
- **Series:** no pregunta por episodio; solo al **terminar la temporada/serie**. Timeout ~24 h.
- También puedes valorar al pulsar «Ya la vi» en una sugerencia.

## Despliegue en el NAS

```bash
# En el Mac, desde esta carpeta:
rsync -av --exclude postgres-data --exclude .venv ./ RosarioNAS:/volume1/docker/media-assistant/
ssh RosarioNAS 'cd /volume1/docker/media-assistant && echo "…" | sudo -S docker compose up -d --build'
```

Datos de Postgres: `/volume1/docker/media-assistant/postgres-data/`

## Primer uso

1. Abre el panel → **Ajustes** → pega token de Telegram y clave TMDb → Guardar
2. **Personas** → crea perfiles y enlázalos a usuarios Jellyfin (al enlazar se sincroniza y se siembra el cold-start desde la biblioteca)
3. Si no tienes Chat ID: usa el código → en Telegram `/start CODIGO`
4. Pulsa **Sincronizar Jellyfin** en el panel (Played → aprendizaje fuerte; si no hay señales, géneros de la biblioteca → prior suave)

## Mac

Hay un acceso directo en el Escritorio: `Media Assistant.app` (abre el panel en el navegador).

## Memoria

- `media-assistant-app`: límite 384 MB
- `media-assistant-db`: límite 256 MB

## Desarrollo local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# necesita Postgres o cambia DATABASE_URL
uvicorn app.main:app --reload --port 8510
pytest -q
```

## Seguridad

- No subas el archivo `.env` al repo público
- Las API keys viven en Postgres del NAS y/o `.env` local
