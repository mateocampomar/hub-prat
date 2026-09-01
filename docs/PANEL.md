# Panel de render — acceso y despliegue

El panel corre en la **Mac mini**. Hay dos vías de acceso, y para subir videos
la diferencia importa:

| Vía | URL | Cuándo |
|---|---|---|
| **Directa (Tailscale)** | `http://100.64.227.78:8787` | **Para subir o bajar videos.** El archivo va derecho a la Mac mini. |
| Cloudflare | `https://hub-prat.skatoramps.com` | Desde cualquier lado, sin Tailscale. Cómoda para ordenar y exportar. |

Las dos piden la misma contraseña (HTTP Basic, credenciales en `.env`).

## Por qué la vía directa para los archivos

Por Cloudflare cada byte sube al datacenter más cercano (São Paulo) y baja de
vuelta por el túnel hasta esta misma Mac mini. Si el navegador está en la LAN,
el archivo hace un viaje internacional para recorrer tres metros. Medido con un
trozo de 8 MB: **1,24 MB/s por Cloudflare contra 4,00 MB/s por Tailscale**, y la
diferencia crece desde otra máquina de la red, donde Tailscale conecta punto a
punto.

El panel lo detecta solo: si entrás por el dominio y hay una vía directa
disponible, muestra un aviso arriba con el link.

Por seguridad el panel escucha en todas las interfaces pero **solo acepta
conexiones desde loopback, Tailscale (100.64.0.0/10) y redes LAN privadas** —
una IP pública recibe 403 antes de que se le pida la contraseña. El tráfico de
Tailscale va cifrado por WireGuard, así que la contraseña no viaja en claro
pese a ser HTTP.

## Piezas

| Pieza | Dónde |
|---|---|
| Servidor | `scripts/panel.py`, escucha en `0.0.0.0:8787` (filtrado por red de origen) |
| Credenciales | `.env` en la raíz del repo (fuera de git, permisos 600) |
| Arranque automático | `~/Library/LaunchAgents/com.hub-prat.panel.plist` |
| Salida a internet | túnel `youtube-stocks`, regla `hub-prat` en `~/.cloudflared/config.yml` |
| Logs | `logs/panel.out.log` y `logs/panel.err.log` |

A internet solo lo saca el túnel, que abre una conexión saliente: no hay
puertos abiertos en el router. Escucha en todas las interfaces para permitir la
vía directa por Tailscale y LAN, pero rechaza con 403 cualquier origen fuera de
esas redes.

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

Cada archivo tiene un botón **cancelar** mientras sube: corta el trozo en vuelo
y borra en el servidor lo que se había subido.

Si un trozo falla, se reintenta hasta 4 veces, y antes de cada reintento el
cliente le pregunta al servidor cuánto recibió realmente
(`GET /api/subida?nombre=…`) para retomar desde ahí. Por eso una subida que se
cortó —conexión caída, pestaña cerrada— se puede retomar: al volver a elegir el
mismo archivo sigue desde donde iba en lugar de empezar de cero.

## Geometría de la TV

La TV está rotada 90° y tiene una portilla física encima, así que el render no
es un 16:9 plano: lleva un recorte cuadrado del cuadro girado, corrido a la
posición de la portilla. La cadena es

```
transpose=2,crop=in_w:in_w:0:420,pad=1920:1080:238:0
```

y sale de estos controles del panel:

| Control | Default | Qué hace |
|---|---|---|
| Giro | `2` (90° antihorario) | `transpose`; compensa la rotación física de la TV |
| OFFY | `420` | dónde cae el recorte cuadrado sobre el cuadro girado (420 = centrado) |
| OFFX | `238` | dónde se apoya ese cuadrado en el lienzo de la TV, o sea la portilla |

Estos valores no son inventados: se dedujeron del render `PRAT-TV-FINAL-5.mp4`
por comparación PSNR contra los clips fuente, y la cadena lo reproduce con
PSNR 47 (reconstrucción exacta).

La **vista previa** del panel muestra un cuadro con la geometría aplicada, así
que OFFX se ajusta mirando, sin exportar media hora a ciegas.

Dos consecuencias de tenerla activada:

- **Obliga a reencodear**: se pierde el concat con `-c copy`. Un render de 30
  minutos tarda unos 9 minutos en la Mac mini (medido, `preset slow` `crf 18`).
- **Tapa la marca de agua del Acuario**: el `Ocean Wonders 8K` de la esquina
  queda fuera del recorte cuadrado. Sin geometría, se ve.

Con la geometría desactivada el export vuelve a ser concat sin re-encode
(segundos), pero el resultado es 16:9 plano y **no es el formato que va a la
TV** — sirve para revisar contenido y orden.

## Videos generados

El bloque **Videos generados** lista lo que hay en `3-render/` y `4-pruebas/`,
más nuevo primero, con una miniatura de cada uno: de un vistazo se distingue el
render que tiene la geometría de la TV (cuadrado con negro a los lados) del que
salió 16:9 plano.

El botón **Descargar** sirve el archivo con `Content-Disposition: attachment` y
soporte de `Range`, así que una descarga cortada se puede reanudar y el archivo
se transmite por bloques — cargar 2 GB en memoria voltearía el panel.

Para bajar conviene la vía directa: son gigas, y por Cloudflare dan la vuelta
por el datacenter.

## Diseño

El panel ocupa la pantalla completa y **no scrollea**: tres columnas —orden de
clips, geometría y subida, videos generados— que llegan hasta abajo. Lo que
scrollea es cada columna por dentro cuando su lista no entra, así que los
botones de exportar y el total quedan siempre a la vista, arriba.

Por debajo de 1000 px de ancho las columnas se apilan y ahí sí scrollea la
página, que es inevitable.

Dos detalles del layout que conviene no romper:

- El `body` es flex, no grid con `grid-template-rows`. Con filas posicionales,
  al ocultarse el aviso de la vía directa (`display:none`) deja de generar fila
  y el `1fr` se lo lleva el footer: `main` queda con su altura natural y las
  columnas no llegan al piso.
- `min-height: 0` en `main` y en las cajas es lo que permite el scroll interno.
  Sin eso, un hijo de flex/grid nunca se encoge por debajo de su contenido y el
  scroll aparece en la página entera.

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
