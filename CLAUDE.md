# PRAT — Instalación audiovisual sincronizada

Showroom de baños, Montevideo, Uruguay. Una experiencia autónoma que se dispara
con un único botón inalámbrico: video en TV + luz LED sincronizada + ventilador
que mueve flecos de techo + sonido.

**Éxito = corre solo, sin intervención manual, en loop.**

---

## Cómo trabajar en este repo

- Respondé en **español**, directo y accionable. Sin explicaciones de más.
- **Nunca** me pidas que edite parámetros a mano: generá los scripts/archivos
  completos y listos para ejecutar.
- Todo script nuevo va en `scripts/`, con `set -euo pipefail` y variables
  declaradas arriba del archivo.
- No borres nada de `1-fuentes/`. Es material original.
- Antes de un render largo, mostrame el comando ffmpeg completo para revisarlo.

## Estructura

```
~/PRAT/
├── 1-fuentes/       # material original (no tocar)
├── 2-normalizados/  # clips normalizados a spec común
├── 3-render/        # salidas finales (PRAT-TV-FINAL-N.mp4)
├── 4-pruebas/       # tests, recortes, previews
├── scripts/         # organizar.sh, panel.py (panel de render, puerto 8787)
└── docs/            # HARDWARE.md, VIDEO.md, láminas HTML
```

---

## Máquinas y acceso

| Máquina     | Usuario         | Rol                          |
|-------------|-----------------|------------------------------|
| MacBook Air | `mateocampomar` | interfaz principal           |
| Mac mini    | `mateo`         | servidor de render (ffmpeg)  |
| Raspberry Pi 5 | `pi`         | reproducción en showroom     |

- Tailscale activo. Mac mini en **`100.64.227.78`** desde cualquier lado.
- Alias `Host macmini` en `~/.ssh/config` del MacBook Air.
- Para sesiones remotas usar **la IP de Tailscale directa**, no el alias ni
  `.local` (mDNS es link-local, no funciona fuera de la LAN).
- Homebrew + ffmpeg instalados en ambas Macs.

---

## Video — especificación de render

- **Framerate:** 29.97 CFR (`30000/1001`) — CFR obligatorio, nada de VFR.
- **Códec:** `libx264`, `-crf 18`, `-preset slow`, `-pix_fmt yuv420p`.
- **Salida actual:** `3-render/PRAT-TV-FINAL-5.mp4`
- **TV vertical**, rotada 90° horario, con portilla física superpuesta.
- **Parámetros de geometría:** `OFFX=238`, `GIRO=2`, `OFFY=420`
  Cadena exacta (deducida del FINAL-5 por PSNR, reproduce con PSNR 47):
  `transpose=2,crop=in_w:in_w:0:420,pad=1920:1080:238:0`
  El panel la aplica con vista previa; obliga a reencodear (~9 min por render).
- **Orden de render:** PRAT General → Star Guitar → PRAT Construcción →
  Estranged → Acuario
- **Separadores:** 2 segundos de negro entre bloques.
- **Techo total:** 30 minutos.

### Normalización previa (obligatoria)
Las fuentes vienen en HEVC y/o VFR. Antes de concatenar:
1. Convertir a CFR 30000/1001.
2. En clips con frames duplicados: `mpdecimate` + `minterpolate`.
3. Unificar resolución, SAR y pix_fmt.
4. Recién ahí concatenar (concat demuxer, no filter, si ya están normalizados).

---

## Hardware

**Cerebro:** Raspberry Pi 5 (8GB) con `mpv` para video + scheduler de cues en
Python. Almacenamiento NVMe vía HAT (microSD Max Endurance es aceptable dado el
bajo volumen de escritura).

**Red:** router WiFi dedicado **sin internet**, solo para los nodos.

**Nodos:**
- ESP32 con **WLED** → tira LED WS2812B.
- ESP32-C3 → control de velocidad del ventilador **Deepcool XF120** (3 pines,
  PWM por voltaje vía MOSFET a 25 kHz).
- ESP32-C3 SuperMini con batería 18650 → **botón inalámbrico por ESP-NOW**
  (~2 años de autonomía con el LED desoldado). ESP-NOW porque dispara casi
  instantáneo sin depender de la infraestructura WiFi.

**Audio:** DAC USB → parlante **Edifier R12U**, alimentado por USB desde la Pi.

**Restricción dura: cero tensión de red dentro del baño.** Todo en baja tensión.

---

## Estado actual

- [x] Arquitectura de hardware definida
- [x] Acceso remoto resuelto (Tailscale + SSH)
- [x] Material organizado en la estructura del repo
- [x] Panel de render (`scripts/panel.py`, drag & drop + export)
- [x] Normalizados los 5 de la spec. Acuario recortado a 1:00–13:55 (775 s):
      la fuente dura 3h25m y arranca con logo de marca. Los 5 dan 30:00 exactos.
- [ ] Render final `PRAT-TV-FINAL-5.mp4` y validación en la TV por USB
- [ ] Compra del kit Pi 5 en Mercado Libre UY (evita demora de importación)
- [ ] Migrar de USB a Pi + mpv
- [ ] Test de integración completo: botón → video + LED + ventilador

### Limitación conocida del loop por USB
La TV Samsung reaparece con su UI en cada vuelta del loop y puede entrar en
suspensión. **No hay workaround bueno**: la solución correcta es Pi + mpv.

---

## Compras

Primero **Mercado Libre Uruguay**. Si no está local: Amazon o AliExpress vía
servicio de forwarding. Costo de importación y tiempo de espera son
restricciones activas — siempre compará contra la opción local antes de sugerir
importar.
