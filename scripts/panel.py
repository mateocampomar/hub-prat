#!/usr/bin/env python3
"""panel.py — Panel de control PRAT.

Sirve una UI local para ordenar los clips de 2-normalizados/ y exportar
el render final (concat demuxer, spec: h264 29.97 CFR yuv420p).

Uso:  python3 scripts/panel.py   →  http://localhost:8787
"""

import base64
import hmac
import ipaddress
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PUERTO = 8787

# Escucha en todas las interfaces para que se pueda entrar directo por Tailscale
# o por la LAN, sin dar la vuelta por Cloudflare. Solo se aceptan conexiones
# desde estas redes; además todo pide contraseña.
ESCUCHA = "0.0.0.0"
REDES_OK = [ipaddress.ip_network(r) for r in (
    "127.0.0.0/8",      # local, y por acá entra el túnel de Cloudflare
    "100.64.0.0/10",    # Tailscale
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",   # LAN
)]
HOST_CLOUDFLARE = "hub-prat.skatoramps.com"
REPO = Path(__file__).resolve().parent.parent
FUENTES = REPO / "1-fuentes"
NORMALIZADOS = REPO / "2-normalizados"
RENDER = REPO / "3-render"
PRUEBAS = REPO / "4-pruebas"
ORDEN_JSON = REPO / "scripts" / "panel-orden.json"
PANEL_HTML = REPO / "scripts" / "panel" / "index.html"
CREDENCIALES = REPO / ".env"
PARCIALES = REPO / "4-pruebas" / ".subidas"

SEP_DEFAULT = 2.0
PRUEBA_SEG_POR_CLIP = 5.0

# Geometría de la TV: está rotada 90° y tiene una portilla física encima, así
# que el render lleva un recorte cuadrado del cuadro girado, corrido a la
# posición de la portilla. Deducido del render PRAT-TV-FINAL-5 por PSNR.
GEOM_DEFAULT = {"activa": True, "offx": 238, "offy": 420, "giro": 2}
EXTENSIONES = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}

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
proceso_export = {"p": None}

# Normalizaciones en curso, una entrada por archivo subido.
trabajos = {}
lock_trabajos = threading.Lock()


def cargar_credenciales():
    """Lee usuario/clave de .env. Si no existe, lo crea con una clave random.

    El panel se publica por el túnel de Cloudflare, así que sale a internet:
    sin credenciales cualquiera podría disparar renders.
    """
    if CREDENCIALES.exists():
        datos = {}
        for linea in CREDENCIALES.read_text().splitlines():
            if "=" in linea and not linea.strip().startswith("#"):
                k, _, v = linea.partition("=")
                datos[k.strip()] = v.strip()
        if datos.get("PANEL_USER") and datos.get("PANEL_PASS"):
            return datos["PANEL_USER"], datos["PANEL_PASS"]

    usuario, clave = "mateo", secrets.token_urlsafe(18)
    CREDENCIALES.write_text(
        "# Credenciales del panel. NO va al repo (está en .gitignore).\n"
        f"PANEL_USER={usuario}\nPANEL_PASS={clave}\n")
    CREDENCIALES.chmod(0o600)
    print(f"Credenciales nuevas en {CREDENCIALES}:\n  usuario: {usuario}\n  clave:   {clave}")
    return usuario, clave


USUARIO, CLAVE = cargar_credenciales()


def clip_valido(nombre):
    """Solo nombres de archivos que existan en 2-normalizados/.

    El nombre llega del cliente: sin este chequeo un '../..' armaría rutas
    fuera del directorio.
    """
    if "/" in nombre or "\\" in nombre or nombre.startswith("."):
        raise ValueError(f"Nombre inválido: {nombre}")
    ruta = (NORMALIZADOS / nombre).resolve()
    if ruta.parent != NORMALIZADOS.resolve() or not ruta.is_file():
        raise ValueError(f"No existe el clip {nombre}")
    return ruta


def leer_geom(body):
    """Toma la geometría del pedido, completando con los valores por defecto."""
    g = dict(GEOM_DEFAULT)
    g.update({k: v for k, v in (body.get("geom") or {}).items() if k in g})
    g["activa"] = bool(g["activa"])
    g["giro"] = int(g["giro"])
    g["offx"], g["offy"] = int(g["offx"]), int(g["offy"])
    if g["giro"] not in (0, 1, 2, 3):
        raise ValueError("giro tiene que ser 0, 1, 2 o 3")
    if not (0 <= g["offx"] <= SPEC_ANCHO) or not (0 <= g["offy"] <= SPEC_ANCHO):
        raise ValueError("offset fuera de rango")
    return g


def cadena_geometria(g):
    """transpose gira el cuadro; crop saca el cuadrado que se ve por la
    portilla; pad lo reubica en el lienzo de la TV. Las medidas van por
    expresión para no depender de que la fuente sea 1920x1080."""
    return (f"transpose={g['giro']},"
            f"crop=in_w:in_w:0:{g['offy']},"
            f"pad={SPEC_ANCHO}:{SPEC_ALTO}:{g['offx']}:0")


def red_permitida(ip):
    try:
        dir_ip = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(dir_ip in red for red in REDES_OK)


def vias_directas():
    """URLs por las que se llega al panel sin pasar por Cloudflare.

    Subir un archivo por el túnel lo manda al datacenter de Cloudflare y lo
    trae de vuelta; por Tailscale o la LAN va derecho a esta máquina.
    """
    vias = []
    # launchd no hereda el PATH de la shell, así que el binario se busca por
    # ruta absoluta además de por nombre.
    for binario in ("tailscale", "/usr/local/bin/tailscale",
                    "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        try:
            ip = subprocess.run([binario, "ip", "-4"], capture_output=True,
                                text=True, timeout=5).stdout.strip().splitlines()
        except Exception:
            continue
        if ip:
            vias.append({"url": f"http://{ip[0]}:{PUERTO}", "via": "Tailscale"})
            break
    return vias


def nombre_seguro(nombre):
    """Sanea el nombre que manda el navegador.

    Se queda solo con el basename y con caracteres previsibles: el nombre
    termina siendo parte de una ruta y de un comando de ffmpeg.
    """
    nombre = Path(nombre).name
    nombre = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    tallo, punto, ext = nombre.rpartition(".")
    ext = ("." + ext).lower()
    if not punto or ext not in EXTENSIONES:
        raise ValueError(f"Extensión no permitida ({ext or 'sin extensión'}). "
                         f"Aceptadas: {', '.join(sorted(EXTENSIONES))}")
    tallo = re.sub(r"[^A-Za-z0-9._-]+", "-", tallo).strip("-.") or "clip"
    return tallo[:80] + ext


def sin_pisar(directorio, nombre):
    """Agrega -2, -3… si ya existe un archivo con ese nombre."""
    destino = directorio / nombre
    if not destino.exists():
        return destino
    tallo, ext = destino.stem, destino.suffix
    for n in range(2, 1000):
        cand = directorio / f"{tallo}-{n}{ext}"
        if not cand.exists():
            return cand
    raise ValueError("Demasiados archivos con ese nombre")


def cumple_spec(p):
    """¿El clip ya está en la spec y se puede concatenar sin re-encode?"""
    codec, ancho, alto, fps, pixfmt = p["video"]
    return (codec == "h264" and ancho == SPEC_ANCHO and alto == SPEC_ALTO
            and fps == SPEC_FPS and pixfmt == SPEC_PIXFMT
            and p["audio"] is not None
            and p["audio"][0] == "aac" and str(p["audio"][1]) == "48000"
            and int(p["audio"][2]) == 2)


def cmd_normalizar(origen, salida, props):
    """ffmpeg para llevar un clip a la spec: 1080p, 29.97 CFR, yuv420p, AAC 48k.

    scale+pad mantiene el aspecto original y rellena con negro; sin eso un clip
    vertical saldría deformado. Si el clip no trae audio se le agrega una pista
    de silencio: el concat demuxer necesita que todos tengan los mismos streams.
    """
    entrada = ["-i", str(origen)]
    mapeo = []
    if props["audio"] is None:
        entrada += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        mapeo = ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    vf = (f"scale={SPEC_ANCHO}:{SPEC_ALTO}:force_original_aspect_ratio=decrease,"
          f"pad={SPEC_ANCHO}:{SPEC_ALTO}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={SPEC_FPS}")
    return ["ffmpeg", "-y", "-v", "error", *entrada, *mapeo,
            "-vf", vf,
            "-c:v", "libx264", "-crf", "18", "-preset", "slow",
            "-pix_fmt", SPEC_PIXFMT,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", str(salida)]


def anotar(nombre, **kw):
    with lock_trabajos:
        trabajos.setdefault(nombre, {}).update(kw)


def normalizar_subido(origen):
    """Lleva a 2-normalizados/ un clip recién subido a 1-fuentes/."""
    nombre = origen.name
    try:
        props = props_clip(origen)
        salida = sin_pisar(NORMALIZADOS, f"{origen.stem}-CFR.mp4")

        if cumple_spec(props):
            # Ya está en la spec: copiarlo es instantáneo y no pierde calidad.
            anotar(nombre, estado="copiando", progreso=0.0,
                   mensaje="Ya cumple la spec, copiando")
            shutil.copy2(origen, salida)
            anotar(nombre, estado="ok", progreso=1.0, salida=salida.name,
                   mensaje=f"Listo (sin re-encode) → {salida.name}")
            return

        dur = props["dur"]
        anotar(nombre, estado="normalizando", progreso=0.0,
               mensaje="Normalizando a 29.97 CFR")
        proc = subprocess.Popen(cmd_normalizar(origen, salida, props),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        for linea in proc.stdout:
            m = re.match(r"out_time_ms=(\d+)", linea)
            if m and dur > 0:
                anotar(nombre, progreso=min(1.0, int(m.group(1)) / 1e6 / dur))
        proc.wait()
        if proc.returncode == 0:
            anotar(nombre, estado="ok", progreso=1.0, salida=salida.name,
                   mensaje=f"Listo → {salida.name}")
        else:
            salida.unlink(missing_ok=True)
            anotar(nombre, estado="error",
                   mensaje=proc.stderr.read()[-600:] or "ffmpeg falló")
    except Exception as e:
        anotar(nombre, estado="error", mensaje=str(e))


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
    geom = dict(GEOM_DEFAULT)
    geom.update(guardado.get("geom", {}))
    return clips, guardado.get("sep", SEP_DEFAULT), geom


def salida_valida(nombre):
    """Ruta de un render o una prueba, validando el nombre que manda el cliente."""
    if "/" in nombre or "\\" in nombre or nombre.startswith("."):
        raise ValueError(f"Nombre inválido: {nombre}")
    for carpeta in (RENDER, PRUEBAS):
        ruta = (carpeta / nombre).resolve()
        if ruta.parent == carpeta.resolve() and ruta.is_file():
            return ruta
    raise ValueError(f"No existe el archivo {nombre}")


def listar_renders():
    """Los videos ya generados, para poder bajarlos desde el panel."""
    salidas = []
    for carpeta, tipo in ((RENDER, "render"), (PRUEBAS, "prueba")):
        for f in carpeta.glob("*.mp4"):
            if f.name.startswith("sep-"):
                continue      # los separadores de negro no son entregables
            try:
                dur = float(ffprobe(f, "-show_entries", "format=duration")
                            ["format"]["duration"])
            except Exception:
                dur = 0.0
            st = f.stat()
            salidas.append({"nombre": f.name, "tipo": tipo,
                            "dur": round(dur, 1), "bytes": st.st_size,
                            "mtime": int(st.st_mtime)})
    salidas.sort(key=lambda x: x["mtime"], reverse=True)
    return salidas


def espacio():
    """Cuánto ocupan las salidas y cuánto queda libre en el disco."""
    usa = sum(s["bytes"] for s in listar_renders())
    libre = shutil.disk_usage(RENDER).free
    return {"ocupado": usa, "libre": libre}


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


def armar_export(orden, sep, modo, geom=None):
    """Devuelve (comando ffmpeg, archivo de lista, salida, dur_total, copia)."""
    if not orden:
        raise ValueError("No hay clips seleccionados")
    geom = geom or dict(GEOM_DEFAULT)
    rutas = [clip_valido(n) for n in orden]
    props = [props_clip(r) for r in rutas]

    # ¿streams uniformes? → concat con -c copy (sin pérdida). Con geometría no
    # hay caso: hay que reencodear para poder tocar el cuadro.
    copia = (
        not geom["activa"]
        and len({p["video"] for p in props}) == 1
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

    filtro = ["-vf", cadena_geometria(geom)] if geom["activa"] else []

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
           *filtro, *codec, "-movflags", "+faststart",
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
        with lock:
            proceso_export["p"] = proc
        for linea in proc.stdout:
            m = re.match(r"out_time_ms=(\d+)", linea)
            if m and dur_total > 0:
                with lock:
                    estado["progreso"] = min(1.0, int(m.group(1)) / 1e6 / dur_total)
        proc.wait()
        err = proc.stderr.read()[-3000:]
        with lock:
            cancelado = proceso_export.pop("cancelado", False)
            proceso_export["p"] = None
            if cancelado:
                # El archivo a medio escribir no sirve para nada y ocupa GB.
                salida.unlink(missing_ok=True)
                estado.update(estado="idle", progreso=0.0, log="")
            elif proc.returncode == 0:
                estado.update(estado="ok", progreso=1.0, log=err)
            else:
                estado.update(estado="error", log=err)
    except Exception as e:
        with lock:
            estado.update(estado="error", log=str(e))


class Handler(BaseHTTPRequestHandler):
    def _red_ok(self):
        if red_permitida(self.client_address[0]):
            return True
        self.send_response(403)
        self.end_headers()
        return False

    def _autorizado(self):
        """HTTP Basic. Compara con hmac para no filtrar por tiempo de respuesta."""
        cabecera = self.headers.get("Authorization", "")
        ok = False
        if cabecera.startswith("Basic "):
            try:
                usuario, _, clave = base64.b64decode(
                    cabecera[6:]).decode("utf-8", "replace").partition(":")
                ok = (hmac.compare_digest(usuario, USUARIO)
                      and hmac.compare_digest(clave, CLAVE))
            except Exception:
                ok = False
        if not ok:
            cuerpo = b"Credenciales requeridas"
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Panel PRAT"')
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        return ok

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

    def _recibir_trozo(self, consulta):
        """Recibe un trozo del archivo y lo agrega al parcial.

        El navegador manda el archivo por partes porque Cloudflare rechaza los
        request de más de 100 MB, y acá los clips pesan cientos de MB.
        """
        nombre = nombre_seguro(consulta["nombre"][0])
        offset = int(consulta.get("offset", ["0"])[0])
        ultimo = consulta.get("final", ["0"])[0] == "1"
        PARCIALES.mkdir(parents=True, exist_ok=True)
        parcial = PARCIALES / (nombre + ".part")

        if offset == 0:
            parcial.unlink(missing_ok=True)
        elif not parcial.exists():
            raise ValueError("No hay una subida en curso para ese archivo: "
                             "volvé a empezar desde el offset 0")
        elif parcial.stat().st_size != offset:
            # El cliente y el servidor no coinciden: mejor cortar que guardar
            # un archivo corrupto.
            raise ValueError(
                f"Trozo fuera de orden (esperaba {parcial.stat().st_size}, vino {offset})")

        faltan = int(self.headers.get("Content-Length", 0))
        with open(parcial, "ab") as f:
            while faltan > 0:
                datos = self.rfile.read(min(1 << 20, faltan))
                if not datos:
                    raise ValueError("Se cortó la subida")
                f.write(datos)
                faltan -= len(datos)

        if not ultimo:
            return {"ok": True, "recibido": parcial.stat().st_size}

        destino = sin_pisar(FUENTES, nombre)
        parcial.replace(destino)
        anotar(destino.name, estado="normalizando", progreso=0.0,
               mensaje="En cola")
        threading.Thread(target=normalizar_subido, args=(destino,),
                         daemon=True).start()
        return {"ok": True, "completo": True, "nombre": destino.name}

    def _enviar_archivo(self, ruta):
        """Manda un video, soportando Range.

        Sin Range el navegador no puede reanudar una descarga cortada ni
        reproducir sin bajar el archivo entero, y acá pesan gigas. Se lee por
        bloques: cargar 2 GB en memoria voltearía el panel.
        """
        total = ruta.stat().st_size
        rango = self.headers.get("Range", "")
        ini, fin = 0, total - 1
        parcial = False
        m = re.match(r"bytes=(\d*)-(\d*)$", rango.strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                ini = int(m.group(1))
                if m.group(2):
                    fin = int(m.group(2))
            else:
                ini = max(0, total - int(m.group(2)))   # sufijo: últimos N bytes
            if ini >= total:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.end_headers()
                return
            fin = min(fin, total - 1)
            parcial = True

        largo = fin - ini + 1
        self.send_response(206 if parcial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(largo))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{ruta.name}"')
        if parcial:
            self.send_header("Content-Range", f"bytes {ini}-{fin}/{total}")
        self.end_headers()

        try:
            with open(ruta, "rb") as f:
                f.seek(ini)
                faltan = largo
                while faltan > 0:
                    datos = f.read(min(1 << 20, faltan))
                    if not datos:
                        break
                    self.wfile.write(datos)
                    faltan -= len(datos)
        except (BrokenPipeError, ConnectionResetError):
            # El navegador cortó la descarga: es normal, no hay nada que hacer.
            self.close_connection = True

    def do_GET(self):
        if not self._red_ok() or not self._autorizado():
            return
        partes = urlparse(self.path)
        if partes.path == "/api/acceso":
            # El cliente usa esto para avisar que hay una vía más rápida.
            por_cloudflare = self.headers.get("Host", "").startswith(HOST_CLOUDFLARE)
            return self._json({"por_cloudflare": por_cloudflare,
                               "directas": vias_directas()})
        if partes.path == "/api/vista":
            # Un cuadro con la geometría aplicada, para ajustar OFFX sin tener
            # que exportar media hora a ciegas.
            try:
                q = parse_qs(partes.query)
                ruta = clip_valido(q["clip"][0])
                g = leer_geom({"geom": {k: v[0] for k, v in q.items()
                                        if k in GEOM_DEFAULT}})
                seg = max(0.0, float(q.get("seg", ["10"])[0]))
                filtro = cadena_geometria(g) if g["activa"] else "null"
                # tv=1 devuelve el cuadro girado como se ve en la TV, que está
                # montada en vertical: así la previa se mira igual que la pared.
                if q.get("tv", ["0"])[0] == "1":
                    filtro += ",transpose=1"
                jpg = subprocess.run(
                    ["ffmpeg", "-v", "error", "-ss", str(seg), "-i", str(ruta),
                     "-vf", f"{filtro},scale=-1:640", "-frames:v", "1",
                     "-f", "image2", "-c:v", "mjpeg", "-"],
                    capture_output=True, timeout=60).stdout
                if not jpg:
                    raise ValueError("no se pudo generar el cuadro")
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(jpg)
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        if partes.path == "/api/renders":
            return self._json({"salidas": listar_renders(), "espacio": espacio()})
        if partes.path == "/api/miniatura":
            # Un cuadro del render: de un vistazo se distingue el que tiene la
            # geometría de la TV del que salió 16:9 plano.
            try:
                ruta = salida_valida(parse_qs(partes.query)["archivo"][0])
                jpg = subprocess.run(
                    ["ffmpeg", "-v", "error", "-ss", "40", "-i", str(ruta),
                     "-vf", "scale=200:-1", "-frames:v", "1",
                     "-f", "image2", "-c:v", "mjpeg", "-"],
                    capture_output=True, timeout=60).stdout
                if not jpg:
                    raise ValueError("no se pudo generar la miniatura")
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.end_headers()
                return self.wfile.write(jpg)
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        if partes.path == "/api/descargar":
            try:
                ruta = salida_valida(parse_qs(partes.query)["archivo"][0])
            except Exception as e:
                return self._json({"error": str(e)}, 400)
            return self._enviar_archivo(ruta)
        if partes.path == "/api/trabajos":
            with lock_trabajos:
                return self._json(trabajos)
        if partes.path == "/api/subida":
            # Cuánto se recibió ya de este archivo, para poder reanudar una
            # subida que se cortó en vez de empezarla de cero.
            try:
                nombre = nombre_seguro(parse_qs(partes.query)["nombre"][0])
                parcial = PARCIALES / (nombre + ".part")
                recibido = parcial.stat().st_size if parcial.exists() else 0
                return self._json({"recibido": recibido})
            except Exception as e:
                return self._json({"error": str(e)}, 400)
        if self.path == "/":
            cuerpo = PANEL_HTML.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/api/clips":
            clips, sep, geom = listar_clips()
            self._json({"clips": clips, "sep": sep, "geom": geom,
                        "proxima": proxima_salida().name})
        elif self.path == "/api/estado":
            with lock:
                self._json(dict(estado))
        else:
            self._json({"error": "no existe"}, 404)

    def do_POST(self):
        if not self._red_ok() or not self._autorizado():
            return
        partes = urlparse(self.path)
        try:
            # La subida manda binario crudo, no JSON: va antes de leer el body.
            if partes.path == "/api/subir":
                return self._json(self._recibir_trozo(parse_qs(partes.query)))
            if partes.path == "/api/cancelar-export":
                with lock:
                    proc = proceso_export.get("p")
                    if not proc or estado["estado"] != "corriendo":
                        return self._json({"error": "no hay export corriendo"}, 409)
                    proceso_export["cancelado"] = True
                    proc.terminate()
                return self._json({"ok": True})
            if partes.path == "/api/borrar":
                nombre = parse_qs(partes.query)["archivo"][0]
                ruta = salida_valida(nombre)
                with lock:
                    # Si un export lo está escribiendo justo ahora, borrarlo
                    # deja el ffmpeg escribiendo en un archivo fantasma.
                    if (estado["estado"] == "corriendo"
                            and estado["salida"] == ruta.name):
                        return self._json(
                            {"error": "ese archivo se está exportando ahora"}, 409)
                liberado = ruta.stat().st_size
                ruta.unlink()
                return self._json({"ok": True, "liberado": liberado,
                                   "espacio": espacio()})
            if partes.path == "/api/cancelar":
                nombre = nombre_seguro(parse_qs(partes.query)["nombre"][0])
                (PARCIALES / (nombre + ".part")).unlink(missing_ok=True)
                with lock_trabajos:
                    trabajos.pop(nombre, None)
                return self._json({"ok": True})

            body = self._leer_body()
            if self.path == "/api/orden":
                ORDEN_JSON.write_text(json.dumps(
                    {"orden": body["orden"], "sep": body.get("sep", SEP_DEFAULT),
                     "geom": leer_geom(body)},
                    indent=2))
                self._json({"ok": True})
            elif self.path == "/api/comando":
                cmd, _, salida, dur, copia = armar_export(
                    body["orden"], float(body.get("sep", SEP_DEFAULT)),
                    body["modo"], leer_geom(body))
                self._json({"comando": " ".join(cmd), "salida": salida.name,
                            "dur": round(dur, 1), "copia": copia})
            elif self.path == "/api/exportar":
                with lock:
                    if estado["estado"] == "corriendo":
                        return self._json({"error": "ya hay un export corriendo"}, 409)
                cmd, _, salida, dur, copia = armar_export(
                    body["orden"], float(body.get("sep", SEP_DEFAULT)),
                    body["modo"], leer_geom(body))
                threading.Thread(target=correr_export,
                                 args=(cmd, salida, dur, body["modo"]),
                                 daemon=True).start()
                self._json({"ok": True, "salida": salida.name, "copia": copia})
            else:
                self._json({"error": "no existe"}, 404)
        except Exception as e:
            if partes.path == "/api/subir":
                # El body quedó a medio leer: reusar la conexión leería basura
                # como si fuera el próximo request.
                self.close_connection = True
            self._json({"error": str(e)}, 500)

    def log_message(self, formato, *args):
        # Los sondeos de progreso son constantes y tapan todo lo demás.
        if "/api/estado" in args[0] or "/api/trabajos" in args[0]:
            return
        super().log_message(formato, *args)


if __name__ == "__main__":
    print(f"Panel PRAT en http://localhost:{PUERTO}")
    for v in vias_directas():
        print(f"  vía directa ({v['via']}): {v['url']}")
    ThreadingHTTPServer((ESCUCHA, PUERTO), Handler).serve_forever()
