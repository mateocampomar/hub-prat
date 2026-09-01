#!/usr/bin/env bash
# organizar.sh — mueve el material de ~/Downloads a la estructura del repo.
# Idempotente: si el archivo ya no está en Downloads, lo saltea.
# No pisa nada: mv -n (no overwrite).
set -euo pipefail

ORIGEN="$HOME/Downloads"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FUENTES="$REPO/1-fuentes"
NORMALIZADOS="$REPO/2-normalizados"
RENDER="$REPO/3-render"
PRUEBAS="$REPO/4-pruebas"

mover() {
  local src="$1" dst="$2"
  if [[ -f "$src" ]]; then
    mv -n "$src" "$dst"
    echo "OK  $(basename "$src") -> ${dst#$REPO/}"
  else
    echo "--  $(basename "$src") no está en Downloads (ya movido?)"
  fi
}

# Fuentes (material original, renombrado a nombres limpios)
mover "$ORIGEN/Prat-VideoConstruccion.MOV" "$FUENTES/Construccion.MOV"
mover "$ORIGEN/The Chemical Brothers - Star Guitar (Official Music Video) - ChemicalBrothersVEVO (1080p, h264).mp4" "$FUENTES/StarGuitar.mp4"
mover "$ORIGEN/YTDown.com_YouTube_Guns-N-Roses-Estranged_Media_dpmAY059TTY_001_1080p.mp4" "$FUENTES/Estranged.mp4"
mover "$ORIGEN/YTDown.com_YouTube_Ocean-Aquarium-8K-Beautiful-Marine-Life-_Media_gV7ZwSg-yuY_001_1080p 2.mp4" "$FUENTES/Acuario.mp4"

# Normalizados (ya a 29.97 CFR, h264, 48kHz)
mover "$ORIGEN/Prat-VideoGral-CFR.mp4" "$NORMALIZADOS/General-CFR.mp4"
mover "$ORIGEN/Prat-VideoConstruccion-CFR.mp4" "$NORMALIZADOS/Construccion-CFR.mp4"

# Renders finales
mover "$ORIGEN/PRAT-TV-FINAL.mp4" "$RENDER/PRAT-TV-FINAL.mp4"
mover "$ORIGEN/PRAT-TV-FINAL-4.mp4" "$RENDER/PRAT-TV-FINAL-4.mp4"
mover "$ORIGEN/PRAT-TV-FINAL-5.mp4" "$RENDER/PRAT-TV-FINAL-5.mp4"

# Pruebas
mover "$ORIGEN/PRAT-PRUEBA-3videos.mp4" "$PRUEBAS/PRAT-PRUEBA-3videos.mp4"

echo
echo "Listo. Estado de los directorios:"
ls -lh "$FUENTES" "$NORMALIZADOS" "$RENDER" "$PRUEBAS" | grep -v '^total\|\.gitkeep' || true
