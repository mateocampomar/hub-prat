# Pipeline de video

## 1. Organizar
`scripts/organizar.sh` — ordena y renombra lo que entra en `1-fuentes/`.

## 2. Inspeccionar
```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt,nb_frames \
  -of default=noprint_wrappers=1 archivo.mp4
```
Si `r_frame_rate != avg_frame_rate` → el clip es VFR y hay que normalizarlo sí o sí.

## 3. Normalizar
`scripts/normalizar.sh` — lleva todo a 29.97 CFR, misma resolución, yuv420p.
Clips con frames duplicados: `mpdecimate` + `minterpolate`.

## 4. Render final
`scripts/render-tv-final-5.sh` — concatena en orden con 2s de negro entre bloques.

Orden: PRAT General → Star Guitar → PRAT Construcción → Estranged → Acuario

## 5. Validar
- Duración total ≤ 30 min
- `ffprobe` confirma CFR 30000/1001
- Reproducir en la TV rotada y chequear la portilla (OFFX=238, GIRO=2)
