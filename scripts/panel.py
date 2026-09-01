#!/usr/bin/env python3
"""panel.py — Panel de control PRAT.

Sirve una UI local para ordenar los clips de 2-normalizados/ y exportar
el render final (concat demuxer, spec: h264 29.97 CFR yuv420p).

Uso:  python3 scripts/panel.py   →  http://localhost:8787
"""

import json
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PUERTO = 8787
REPO = Path(__file__).resolve().parent.parent
NORMALIZADOS = REPO / "2-normalizados"
RENDER = REPO / "3-render"
PRUEBAS = REPO / "4-pruebas"
ORDEN_JSON = REPO / "scripts" / "panel-orden.json"
PANEL_HTML = REPO / "scripts" / "panel" / "index.html"

SEP_DEFAULT = 2.0
PRUEBA_SEG_POR_CLIP = 5.0

# Spec de render (CLAUDE.md / docs/VIDEO.md)
SPEC_FPS = "30000/1001"
SPEC_ANCHO = 1920
SPEC_ALTO = 1080
SPEC_PIXFMT = "yuv420p"

estado = {
    "estado": "idle",       # idle | corriendo | ok | error
    "modo": None,
    "progreso": 0.0,
    "total_seg": 0.0,
    "salida": None,
    "log": "",
    "comando": None,
}
lock = threading.Lock()


def ffprobe(path, *entradas):
    cmd = ["ffprobe", "-v", "error", *entradas, "-of", "json", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def props_clip(path):
    data = ffprobe(
        path,
        "-show_entries",
        "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels",
        "-show_entries", "format=duration",
    )
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    return {
        "dur": float(data["format"]["duration"]),
        "video": (v["codec_name"], v["width"], v["height"], v["r_frame_rate"], v["pix_fmt"]),
        "audio": (a["codec_name"], a["sample_rate"], a["channels"]) if a else None,
    }


def listar_clips():
    clips = []
    for f in sorted(NORMALIZADOS.glob("*.mp4")):
        try:
            p = props_clip(f)
        except Exception:
            continue
        clips.append({
            "nombre": f.name,
            "dur": round(p["dur"], 2),
            "mb": round(f.stat().st_size / 1e6),
            "fps": p["video"][3],
            "res": f'{p["video"][1]}x{p["video"][2]}',
        })
    # aplicar orden guardado
    guardado = {}
    if ORDEN_JSON.exists():
        guardado = json.loads(ORDEN_JSON.read_text())
    orden = guardado.get("orden", [])
    clips.sort(key=lambda c: orden.index(c["nombre"]) if c["nombre"] in orden else 999)
    return clips, guardado.get("sep", SEP_DEFAULT)


def proxima_salida():
    ns = []
    for f in RENDER.glob("PRAT-TV-FINAL*.mp4"):
        m = re.match(r"PRAT-TV-FINAL-?(\d*)\.mp4$", f.name)
        if m:
            ns.append(int(m.group(1) or 1))
    return RENDER / f"PRAT-TV-FINAL-{max(ns, default=0) + 1}.mp4"


def separador(seg):
    """Genera (una vez) el clip negro+silencio que matchea la spec."""
    out = PRUEBAS / f"sep-{seg:g}s.mp4"
    if out.exists():
        return out
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={SPEC_ANCHO}x{SPEC_ALTO}:r={SPEC_FPS}:d={seg}",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seg}",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", SPEC_PIXFMT,
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def armar_export(orden, sep, modo):
    """Devuelve (comando ffmpeg, archivo de lista, salida, dur_total, copia)."""
    rutas = [NORMALIZADOS / n for n in orden]
    for r in rutas:
        if not r.exists():
            raise ValueError(f"No existe {r.name}")
    props = [props_clip(r) for r in rutas]

    # ¿streams uniformes? → concat con -c copy (sin pérdida)
    copia = (
        len({p["video"] for p in props}) == 1
        and len({p["audio"] for p in props}) == 1
        and props[0]["audio"] is not None
        and modo == "final"
    )

    sep_file = separador(sep)
    lineas = []
    dur_total = 0.0
    for i, r in enumerate(rutas):
        if i > 0:
            lineas.append(f"file '{sep_file}'")
            dur_total += sep
        lineas.append(f"file '{r}'")
        if modo == "prueba":
            lineas.append(f"outpoint {PRUEBA_SEG_POR_CLIP}")
            dur_total += min(PRUEBA_SEG_POR_CLIP, props[i]["dur"])
        else:
            dur_total += props[i]["dur"]

    lista = tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="prat-concat-", delete=False)
    lista.write("\n".join(lineas) + "\n")
    lista.close()

    if modo == "prueba":
        salida = PRUEBAS / "PANEL-PRUEBA.mp4"
        codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                 "-pix_fmt", SPEC_PIXFMT, "-c:a", "aac", "-b:a", "192k"]
    else:
        salida = proxima_salida()
        if copia:
            codec = ["-c", "copy"]
        else:
            codec = ["-c:v", "libx264", "-preset", "slow", "-crf", "18",
                     "-r", SPEC_FPS, "-pix_fmt", SPEC_PIXFMT,
                     "-c:a", "aac", "-b:a", "192k"]

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lista.name,
           *codec, "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", str(salida)]
    return cmd, lista.name, salida, dur_total, copia


def correr_export(cmd, salida, dur_total, modo):
    with lock:
        estado.update(estado="corriendo", modo=modo, progreso=0.0,
                      total_seg=dur_total, salida=salida.name, log="",
                      comando=" ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        for linea in proc.stdout:
            m = re.match(r"out_time_ms=(\d+)", linea)
            if m and dur_total > 0:
                with lock:
                    estado["progreso"] = min(1.0, int(m.group(1)) / 1e6 / dur_total)
        proc.wait()
        err = proc.stderr.read()[-3000:]
        with lock:
            if proc.returncode == 0:
                estado.update(estado="ok", progreso=1.0, log=err)
            else:
                estado.update(estado="error", log=err)
    except Exception as e:
        with lock:
            estado.update(estado="error", log=str(e))


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        cuerpo = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _leer_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/":
            cuerpo = PANEL_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/api/clips":
            clips, sep = listar_clips()
            self._json({"clips": clips, "sep": sep,
                        "proxima": proxima_salida().name})
        elif self.path == "/api/estado":
            with lock:
                self._json(dict(estado))
        else:
            self._json({"error": "no existe"}, 404)

    def do_POST(self):
        try:
            body = self._leer_body()
            if self.path == "/api/orden":
                ORDEN_JSON.write_text(json.dumps(
                    {"orden": body["orden"], "sep": body.get("sep", SEP_DEFAULT)},
                    indent=2))
                self._json({"ok": True})
            elif self.path == "/api/comando":
                cmd, _, salida, dur, copia = armar_export(
                    body["orden"], float(body.get("sep", SEP_DEFAULT)), body["modo"])
                self._json({"comando": " ".join(cmd), "salida": salida.name,
                            "dur": round(dur, 1), "copia": copia})
            elif self.path == "/api/exportar":
                with lock:
                    if estado["estado"] == "corriendo":
                        return self._json({"error": "ya hay un export corriendo"}, 409)
                cmd, _, salida, dur, copia = armar_export(
                    body["orden"], float(body.get("sep", SEP_DEFAULT)), body["modo"])
                threading.Thread(target=correr_export,
                                 args=(cmd, salida, dur, body["modo"]),
                                 daemon=True).start()
                self._json({"ok": True, "salida": salida.name, "copia": copia})
            else:
                self._json({"error": "no existe"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Panel PRAT en http://localhost:{PUERTO}")
    ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler).serve_forever()
