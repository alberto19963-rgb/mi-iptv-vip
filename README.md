# Mi IPTV Gratis

Lista personal de canales IPTV gratuitos en formato M3U. El repo mantiene una lista maestra, valida enlaces activos y publica una lista final lista para usar en VLC, TiviMate, IPTV Smarters u otro reproductor compatible.

## Archivos

- `lista_maestra.m3u`: fuente principal de canales.
- `mi_lista_personal.m3u`: lista filtrada y categorizada para usar en reproductores.
- `canales_disponibles.txt`: resumen legible de canales activos.
- `update_channels.py`: verifica enlaces activos y regenera la lista final.
- `add_new_channels.py`: agrega canales desde fuentes publicas de iptv-org.
- `.github/workflows/update-iptv.yml`: actualiza la lista automaticamente una vez al dia.

## Uso

Usa la URL raw de `mi_lista_personal.m3u` en tu reproductor IPTV:

```text
https://raw.githubusercontent.com/alberto19963-rgb/mi-iptv-vip/main/mi_lista_personal.m3u
```

## Carpetas / grupos

Cada canal se clasifica con el codigo de pais del `tvg-id` de iptv-org (`Canal.do@SD`, `Canal.us@HD`, etc.):

- `🇩🇴 República Dominicana`
- `🇵🇷 Puerto Rico`
- `🇲🇽 México`
- `🇻🇪 Venezuela`
- `🇺🇸 EE.UU. · Deportes / Películas / Infantiles / Noticias`
- `🇺🇸 Estados Unidos` (resto de EE.UU.)
- `🌍 Otros` (otros paises)

Para actualizar manualmente desde tu computadora:

```bash
python3 update_channels.py
```

Solo reclasificar grupos sin verificar URLs:

```bash
python3 update_channels.py --recategorize
```
## Seguridad

No subas scripts locales con contrasenas, datos del NAS, logs o archivos temporales. El `.gitignore` ya bloquea patrones comunes como `*.exp`, `logs.zip` y `data/`.

Si algun secreto se subio o estuvo en archivos locales, cambia esa contrasena o token inmediatamente. Para acceder a servidores o NAS, usa llaves SSH en vez de guardar passwords en scripts.

## Aviso

Esta lista esta pensada para streams publicos y gratuitos. No hospeda video ni redistribuye contenido; solo referencia URLs externas. Algunos canales pueden estar caidos, bloqueados por region o cambiar sin aviso.
