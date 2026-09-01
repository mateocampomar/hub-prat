# Panel de render — acceso y despliegue

El panel corre en la **Mac mini** y se accede desde cualquier lado en
**https://hub-prat.skatoramps.com** (HTTP Basic: usuario y clave en `.env`).

## Piezas

| Pieza | Dónde |
|---|---|
| Servidor | `scripts/panel.py`, escucha en `127.0.0.1:8787` |
| Credenciales | `.env` en la raíz del repo (fuera de git, permisos 600) |
| Arranque automático | `~/Library/LaunchAgents/com.hub-prat.panel.plist` |
| Salida a internet | túnel `youtube-stocks`, regla `hub-prat` en `~/.cloudflared/config.yml` |
| Logs | `logs/panel.out.log` y `logs/panel.err.log` |

El panel **nunca** se expone directo: escucha solo en loopback y a internet lo
saca el túnel, que abre una conexión saliente (no hay puertos abiertos en el
router).

## Subir videos

Se arrastran al panel (o se eligen con el botón). El flujo es automático:

1. El archivo se sube **en trozos de 8 MB**. Cloudflare rechaza cualquier
   request de más de 100 MB, así que un clip de 1 GB no entra de una: el
   navegador lo parte y el servidor lo va agregando a un parcial en
   `4-pruebas/.subidas/`. Los trozos son chicos a propósito: dan progreso fino
   y un reintento cuesta poco.
2. Cuando llega el último trozo, el archivo pasa a `1-fuentes/`.
3. Arranca sola la normalización a `2-normalizados/<nombre>-CFR.mp4`, con
   progreso en la misma fila.
4. Al terminar, el clip aparece en la lista y ya se puede ordenar y exportar.

Si el clip **ya cumple la spec** (h264 1080p, 29.97 CFR, yuv420p, AAC 48 kHz
estéreo) se copia sin re-encode. Si no, se reencodea: se escala a 1920×1080
manteniendo el aspecto y rellenando con negro, y si no trae audio se le agrega
una pista de silencio — el concat demuxer necesita que todos los clips tengan
los mismos streams.

Extensiones aceptadas: `.mp4 .mov .mkv .m4v .avi .webm`. El nombre se sanea
(se queda solo el basename, sin acentos ni caracteres raros) y nunca pisa un
archivo existente: agrega `-2`, `-3`, etc.

Si un trozo falla, se reintenta hasta 4 veces, y antes de cada reintento el
cliente le pregunta al servidor cuánto recibió realmente
(`GET /api/subida?nombre=…`) para retomar desde ahí. Por eso una subida que se
cortó —conexión caída, pestaña cerrada— se puede retomar: al volver a elegir el
mismo archivo sigue desde donde iba en lugar de empezar de cero.

## Operación

```bash
# reiniciar el panel
launchctl kickstart -k gui/$(id -u)/com.hub-prat.panel

# ver el log
tail -f ~/hub-prat/logs/panel.out.log

# ver la clave actual
cat ~/hub-prat/.env
```

Para cambiar la contraseña: editar `PANEL_PASS` en `.env` y reiniciar el panel.
Si se borra `.env`, el panel genera una clave nueva al arrancar y la escribe en
el log.

## Notas

- El LaunchAgent fija `PATH` con `/opt/homebrew/bin`: launchd arranca con un
  PATH mínimo y sin eso el panel no encuentra `ffmpeg`/`ffprobe`, y lista los
  clips vacíos.
- La regla del túnel va **antes** del comodín `*.skatoramps.com`, que apunta a
  la app de landings (puerto 8002). Si queda después, el dominio responde
  "No hay ningún proyecto en esta dirección".
- Los nombres de clip que llegan del cliente se validan contra el contenido real
  de `2-normalizados/` antes de armar cualquier ruta.
