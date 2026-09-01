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
