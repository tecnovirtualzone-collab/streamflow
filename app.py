"""
StreamFlow v3.0 - Backend con VLC Relay
========================================
Mejoras v3.0:
- VLC Relay Manager: 1 conexión al proveedor por canal (indetectable)
- Proxy HTTP local desde VLC a usuarios
- Auto-start/stop de VLC por canal
- Health checks y auto-reconnect
- Optimizado para 100-200 usuarios en VPS 8GB/2CPU

Mejoras v2.0:
- Seguridad: JWT auth, rate limiting, secrets management
- Cache de M3U
- Panel admin mejorado
"""

import os
import sys
import secrets
import logging
import threading
import time
import shutil
import subprocess
import hashlib
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from functools import wraps

from flask import Flask, request, Response, jsonify, session, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
import requests
import jwt

# VLC Relay Manager
from vlc_manager import vlc_manager

# ════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════

app = Flask(__name__)

# CORS restrictivo
CORS(app, resources={
    r"/api/*": {"origins": os.environ.get("ALLOWED_ORIGINS", "*").split(",")},
    r"/m3u/*": {"origins": "*"},
    r"/stream/*": {"origins": "*"},
    r"/live/*": {"origins": "*"},
    r"/hls/*": {"origins": "*"},
    r"/movie/*": {"origins": "*"},
    r"/series/*": {"origins": "*"},
})

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per minute", "50 per second"],
    storage_uri="memory://",
)

# Secrets desde variables de entorno
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))

VLC_HTTP_PORT = int(os.environ.get("VLC_HTTP_PORT", "8888"))
VLC_HTTP_USER = os.environ.get("VLC_HTTP_USER", "streamflow")
VLC_HTTP_PASS = os.environ.get("VLC_HTTP_PASS", "sf_vlc_2026")
VLC_TIMEOUT = int(os.environ.get("VLC_TIMEOUT", "60"))
VLC_MAX_CHANNELS = int(os.environ.get("VLC_MAX_CHANNELS", "8"))

# Admin credentials from env
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS_HASH = os.environ.get("ADMIN_PASS_HASH", "")

# Base de datos
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost/streamflow"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Proveedor IPTV
URL_M3U_PROVEEDOR = os.environ.get("URL_M3U", "")
WA_SERVICE_URL = os.environ.get("WA_SERVICE_URL", "http://localhost:3001")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("streamflow")
hls_log = logging.getLogger("hls")

# ════════════════════════════════════════════════════════════════
# BASE DE DATOS
# ════════════════════════════════════════════════════════════════

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy(app)


class Usuario(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(50), unique=True, nullable=False)
    contrasena = db.Column(db.String(200), nullable=False)
    paquete = db.Column(db.String(20), default="basico")
    max_conexiones = db.Column(db.Integer, default=1)
    fecha_expira = db.Column(db.DateTime, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    notas = db.Column(db.String(300), default="")
    macs = db.relationship("MacRegistrada", backref="usuario", lazy=True, cascade="all, delete-orphan")
    sesiones = db.relationship("SesionActiva", backref="usuario", lazy=True, cascade="all, delete-orphan")
    pagos = db.relationship("Pago", backref="usuario", lazy=True, cascade="all, delete-orphan")
    logs = db.relationship("LogAcceso", backref="usuario", lazy=True, cascade="all, delete-orphan")


class MacRegistrada(db.Model):
    __tablename__ = "macs"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    mac = db.Column(db.String(17), nullable=False)
    nombre = db.Column(db.String(50), default="Dispositivo")
    registrada = db.Column(db.DateTime, default=datetime.utcnow)


class Paquete(db.Model):
    __tablename__ = "paquetes"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    descripcion = db.Column(db.String(200), default="")
    categorias = db.Column(db.Text, default="")
    creado = db.Column(db.DateTime, default=datetime.utcnow)


class LogAcceso(db.Model):
    __tablename__ = "logs_acceso"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    canal = db.Column(db.String(200))
    ip = db.Column(db.String(45))
    fecha = db.Column(db.DateTime, default=datetime.utcnow)


class Pago(db.Model):
    __tablename__ = "pagos"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    metodo = db.Column(db.String(50), default="Efectivo")
    notas = db.Column(db.String(200), default="")
    fecha = db.Column(db.DateTime, default=datetime.utcnow)


class SesionActiva(db.Model):
    __tablename__ = "sesiones"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    ip = db.Column(db.String(45), nullable=False)
    mac = db.Column(db.String(17))
    canal = db.Column(db.String(200))
    ultimo_ping = db.Column(db.DateTime, default=datetime.utcnow)


# ════════════════════════════════════════════════════════════════
# SISTEMA ANTI-BLOQUEO: RELAY HLS COMPARTIDO
# ════════════════════════════════════════════════════════════════
#
# Clave del sistema: UNA sola conexión al proveedor por canal,
# sin importar cuántos usuarios estén viendo.
#
# Arquitectura:
#   Usuario 1 ──┐
#   Usuario 2 ──┼──→ Relay HLS (1 conexión al proveedor) → Segmentos compartidos
#   Usuario N ──┘
#
# El relay se mantiene activo mientras haya viewers.
# Se apaga automáticamente después de HLS_TIMEOUT sin viewers.

HLS_DIR = "/tmp/hls"
HLS_TIMEOUT = 120  # segundos sin viewers para apagar el relay
HLS_SEGMENT_TIME = 2  # segundos por segmento
HLS_LIST_SIZE = 20  # segmentos en el playlist

os.makedirs(HLS_DIR, exist_ok=True)

# Estado de relays: { canal_id: { proc, viewers, last_view, ready, path, playlist, url } }
_relays = {}
_relay_lock = threading.Lock()


def get_hls_dir(canal_id: str) -> str:
    d = os.path.join(HLS_DIR, str(canal_id))
    os.makedirs(d, exist_ok=True)
    return d


def get_proveedor_url(canal_id: str) -> str:
    """Construye la URL del proveedor para un canal."""
    base, user, pwd = get_proveedor_info()
    return f"{base}/live/{user}/{pwd}/{canal_id}.ts"


def start_relay(canal_id: str) -> bool:
    """
    Arranca un relay FFmpeg para un canal SOLO si no existe ya.
    Múltiples usuarios comparten el mismo relay = 1 conexión al proveedor.
    """
    with _relay_lock:
        if canal_id in _relays:
            if _relays[canal_id]["proc"].poll() is None:
                _relays[canal_id]["viewers"] += 1
                _relays[canal_id]["last_view"] = time.time()
                logger.info(f"Relay canal {canal_id}: +1 viewer (total: {_relays[canal_id]['viewers']})")
                return True
            else:
                # Proceso muerto, limpiar
                del _relays[canal_id]

        url_proveedor = get_proveedor_url(canal_id)
        hls_path = get_hls_dir(canal_id)
        playlist = os.path.join(hls_path, "index.m3u8")

        # Limpiar segmentos anteriores
        for f in os.listdir(hls_path):
            try:
                os.remove(os.path.join(hls_path, f))
            except:
                pass

        cmd = [
            "ffmpeg", "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-user_agent", "VLC/3.0.18 LibVLC/3.0.18",
            "-headers", "Referer: https://player.club/\r\n",
            "-i", url_proveedor,
            "-c", "copy",
            "-f", "hls",
            "-hls_time", str(HLS_SEGMENT_TIME),
            "-hls_list_size", str(HLS_LIST_SIZE),
            "-hls_flags", "append_list+delete_segments",
            "-hls_segment_filename", os.path.join(hls_path, "seg%05d.ts"),
            playlist,
        ]

        try:
            hls_log.info(f"Arrancando relay canal {canal_id} → {url_proveedor}")
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
                close_fds=True,
            )
            _relays[canal_id] = {
                "proc": proc,
                "viewers": 1,
                "last_view": time.time(),
                "ready": False,
                "path": hls_path,
                "playlist": playlist,
                "url": url_proveedor,
            }
            # Esperar a que el playlist esté listo
            t = threading.Thread(target=_mark_ready, args=(canal_id,), daemon=True)
            t.start()
            return True
        except Exception as e:
            logger.error(f"Error arrancando relay canal {canal_id}: {e}")
            return False


def _mark_ready(canal_id: str):
    """Espera a que el relay genere el primer segmento."""
    info = _relays.get(canal_id)
    if not info:
        return
    playlist = info["playlist"]
    for i in range(40):  # 8 segundos max
        if os.path.exists(playlist) and os.path.getsize(playlist) > 0:
            with _relay_lock:
                if canal_id in _relays:
                    _relays[canal_id]["ready"] = True
            hls_log.info(f"Canal {canal_id} listo en {i * 0.2:.1f}s")
            return
        time.sleep(0.2)
    hls_log.error(f"Canal {canal_id} NO se puso listo en 8s")


def stop_relay_viewer(canal_id: str):
    """Decrementa el contador de viewers. No mata el relay inmediatamente."""
    with _relay_lock:
        if canal_id in _relays:
            _relays[canal_id]["viewers"] = max(0, _relays[canal_id]["viewers"] - 1)


def cleanup_relays():
    """Hilo que apaga relays sin viewers después del timeout."""
    while True:
        time.sleep(15)
        with _relay_lock:
            to_remove = []
            for cid, info in _relays.items():
                if info["viewers"] <= 0 and time.time() - info["last_view"] > HLS_TIMEOUT:
                    try:
                        info["proc"].terminate()
                        info["proc"].wait(timeout=5)
                    except:
                        try:
                            info["proc"].kill()
                        except:
                            pass
                    shutil.rmtree(info["path"], ignore_errors=True)
                    to_remove.append(cid)
                    logger.info(f"Relay canal {cid} apagado (sin viewers)")
            for cid in to_remove:
                del _relays[cid]


def init_relay_cleanup():
    t = threading.Thread(target=cleanup_relays, daemon=True)
    t.start()


# ════════════════════════════════════════════════════════════════
# CACHE DE M3U
# ════════════════════════════════════════════════════════════════

_m3u_cache = {
    "content": "",
    "timestamp": 0,
    "ttl": 300,  # 5 minutos de cache
}


def get_m3u_list(force_refresh=False) -> str:
    """Obtiene la lista M3U del proveedor con cache."""
    now = time.time()
    if not force_refresh and _m3u_cache["content"] and (now - _m3u_cache["timestamp"]) < _m3u_cache["ttl"]:
        return _m3u_cache["content"]

    base, user, pwd = get_proveedor_info()
    if not base:
        return ""

    try:
        resp = requests.get(
            f"{base}/get.php",
            params={"username": user, "password": pwd, "type": "m3u_plus", "output": "ts"},
            timeout=30,
        )
        resp.raise_for_status()
        _m3u_cache["content"] = resp.text
        _m3u_cache["timestamp"] = now
        logger.info(f"M3U actualizada: {len(resp.text)} bytes")
        return resp.text
    except Exception as e:
        logger.error(f"Error obteniendo M3U: {e}")
        return _m3u_cache["content"]  # Retornar cache aunque esté vieja


# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def get_host():
    proto = request.headers.get("X-Forwarded-Proto", "https")
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}"


def get_proveedor_info():
    url = URL_M3U_PROVEEDOR
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        params = parse_qs(parsed.query)
        user = params.get("username", [""])[0]
        pwd = params.get("password", [""])[0]
        return base, user, pwd
    except:
        return "", "", ""


def generate_jwt(payload: dict) -> str:
    payload["exp"] = datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    payload["iat"] = datetime.utcnow()
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verify_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except:
        return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Verificar JWT en header o sesión
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if token:
            payload = verify_jwt(token)
            if payload and payload.get("admin"):
                return f(*args, **kwargs)
        # Fallback a sesión
        if session.get("admin_logged"):
            return f(*args, **kwargs)
        return jsonify({"error": "No autorizado"}), 401
    return decorated


def limpiar_sesiones():
    limite = datetime.utcnow() - timedelta(seconds=30)
    SesionActiva.query.filter(SesionActiva.ultimo_ping < limite).delete()
    db.session.commit()


def autenticar(usuario_str: str, contrasena_str: str):
    user = Usuario.query.filter_by(usuario=usuario_str, activo=True).first()
    if not user:
        return None, "Usuario no encontrado"
    if not check_password_hash(user.contrasena, contrasena_str):
        return None, "Contraseña incorrecta"
    if datetime.utcnow() > user.fecha_expira:
        return None, "Cuenta expirada"
    return user, None


def verificar_acceso(usuario_str, contrasena_str, mac, ip, canal):
    limpiar_sesiones()
    user, error = autenticar(usuario_str, contrasena_str)
    if not user:
        return False, None, error

    if mac:
        macs_guardadas = [m.mac.lower() for m in user.macs]
        if not macs_guardadas:
            db.session.add(MacRegistrada(usuario_id=user.id, mac=mac.lower(), nombre="Dispositivo 1"))
            db.session.commit()
        elif mac.lower() not in macs_guardadas:
            if len(macs_guardadas) >= user.max_conexiones:
                return False, None, "Dispositivo no autorizado."
            db.session.add(MacRegistrada(usuario_id=user.id, mac=mac.lower(), nombre=f"Dispositivo {len(macs_guardadas)+1}"))
            db.session.commit()

    sesiones = SesionActiva.query.filter_by(usuario_id=user.id).all()
    ips_activas = list({s.ip for s in sesiones})
    if ip not in ips_activas and len(ips_activas) >= user.max_conexiones:
        return False, None, f"Límite de {user.max_conexiones} conexión(es) alcanzado"

    sesion = SesionActiva(usuario_id=user.id, ip=ip, mac=mac, canal=canal)
    db.session.add(sesion)
    db.session.commit()
    return True, user, sesion.id


# ════════════════════════════════════════════════════════════════
# PAQUETES POR DEFECTO
# ════════════════════════════════════════════════════════════════

PAQUETES_DEFAULT = {
    "basico": {
        "descripcion": "TV Colombia + Noticias + Kids + Entretenimiento",
        "categorias_nombres": [
            "COLOMBIA", "NOTICIAS", "KIDS", "INFANTIL",
            "ENTRETENIMIENTO", "RELIGION", "MUSICA",
            "CANALES 24/7", "CULTURA", "CANALES 24/7 INFANTIL",
            "REALITYS", "TELEMUNDO", "UNIVISION", "VIX"
        ],
    },
    "premium": {
        "descripcion": "Basico + Deportes + Latinoamerica + Cine",
        "categorias_nombres": [
            "COLOMBIA", "NOTICIAS", "KIDS", "INFANTIL",
            "ENTRETENIMIENTO", "RELIGION", "MUSICA",
            "CANALES 24/7", "CULTURA", "CANALES 24/7 INFANTIL",
            "REALITYS", "TELEMUNDO", "UNIVISION", "VIX",
            "DEPORTES", "FORMULA 1", "LIGAS", "FUTBOL",
            "ARGENTINA", "CHILE", "MEXICO", "VENEZUELA",
            "PERU", "ECUADOR", "BOLIVIA", "BRASIL",
            "URUGUAY", "PARAGUAY", "COSTA RICA", "PANAMA",
            "GUATEMALA", "HONDURAS", "EL SALVADOR", "NICARAGUA",
            "CUBA", "PUERTO RICO", "REPUBLICA DOMINICANA",
            "CINE", "PLUTO", "TUBI", "ITALIA", "ESPANA"
        ],
    },
    "familiar": {
        "descripcion": "Premium + USA + Eventos + Exclusivos",
        "categorias_nombres": [
            "COLOMBIA", "NOTICIAS", "KIDS", "INFANTIL",
            "ENTRETENIMIENTO", "RELIGION", "MUSICA",
            "CANALES 24/7", "CULTURA", "CANALES 24/7 INFANTIL",
            "REALITYS", "TELEMUNDO", "UNIVISION", "VIX",
            "DEPORTES", "FORMULA 1", "LIGAS", "FUTBOL",
            "ARGENTINA", "CHILE", "MEXICO", "VENEZUELA",
            "PERU", "ECUADOR", "BOLIVIA", "BRASIL",
            "URUGUAY", "PARAGUAY", "COSTA RICA", "PANAMA",
            "GUATEMALA", "HONDURAS", "EL SALVADOR", "NICARAGUA",
            "CUBA", "PUERTO RICO", "REPUBLICA DOMINICANA",
            "CINE", "PLUTO", "TUBI", "ITALIA", "ESPANA",
            "USA", "NBA", "NFL", "MLB", "NHL", "NCAA",
            "PPV", "WWE", "UFC", "EVENTOS",
            "EXCLUSIVOS", "CASA DE LOS FAMOSOS", "GRAN HERMANO",
            "ADULTO", "ARABIA", "KODIMAX", "CANALES IZZI",
            "CANADA", "ALERT"
        ],
    },
}


# ════════════════════════════════════════════════════════════════
# RUTAS DE AUTENTICACIÓN
# ════════════════════════════════════════════════════════════════

@app.route("/panel/login", methods=["GET"])
def panel_login():
    return send_from_directory("panel", "login.html")


@app.route("/api/auth/login", methods=["POST"])
@limiter.limit("10 per minute")
def api_login():
    data = request.json or {}
    username = data.get("username", "")
    password = data.get("password", "")

    # Verificar contra hash de env o contra usuario en BD
    if ADMIN_PASS_HASH:
        if username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password):
            token = generate_jwt({"admin": True, "user": username})
            return jsonify({"token": token, "user": username})
    else:
        # Fallback: password plano en env (solo para desarrollo)
        admin_pass_env = os.environ.get("ADMIN_PASSWORD", "admin123")
        if username == ADMIN_USER and password == admin_pass_env:
            token = generate_jwt({"admin": True, "user": username})
            session["admin_logged"] = True
            return jsonify({"token": token, "user": username})

    return jsonify({"error": "Credenciales incorrectas"}), 401


@app.route("/api/auth/verify", methods=["GET"])
def verify_token():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    payload = verify_jwt(token)
    if payload:
        return jsonify({"valid": True, "user": payload.get("user")})
    return jsonify({"valid": False}), 401


# ════════════════════════════════════════════════════════════════
# XTREAM CODES API (compatible con apps IPTV)
# ════════════════════════════════════════════════════════════════

@app.route("/player_api.php")
def player_api():
    usuario = request.args.get("username", "")
    contrasena = request.args.get("password", "")
    action = request.args.get("action", "")
    host = get_host()

    user, error = autenticar(usuario, contrasena)
    if not user:
        return jsonify({"user_info": {"auth": 0}}), 401

    base_prov, prov_user, prov_pass = get_proveedor_info()

    if not action:
        return jsonify({
            "user_info": {
                "auth": 1,
                "username": usuario,
                "password": contrasena,
                "status": "Active",
                "exp_date": str(int(user.fecha_expira.timestamp())),
                "is_trial": "0",
                "active_cons": str(len(user.sesiones)),
                "created_at": str(int(user.creado.timestamp())),
                "max_connections": str(user.max_conexiones),
                "allowed_output_formats": ["m3u8", "ts", "rtmp"],
            },
            "server_info": {
                "url": host,
                "port": "443",
                "https_port": "443",
                "server_protocol": "https",
                "rtmp_port": "443",
                "timestamp_now": int(datetime.utcnow().timestamp()),
                "time_now": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "UTC",
            },
        })

    # Proxy al proveedor para otras acciones
    params = {k: v for k, v in request.args.items() if k not in ["username", "password"]}
    params.update({"username": prov_user, "password": prov_pass})
    try:
        resp = requests.get(f"{base_prov}/player_api.php", params=params, timeout=15)
        return Response(resp.content, content_type=resp.headers.get("Content-Type", "application/json"))
    except:
        return jsonify({"error": "Error al conectar con el proveedor"}), 502


@app.route("/get.php")
def get_php():
    """Lista M3U con reescritura de URLs para pasar por nuestro proxy."""
    usuario = request.args.get("username", "")
    contrasena = request.args.get("password", "")

    user, error = autenticar(usuario, contrasena)
    if not user:
        return "Acceso denegado", 403

    ip = request.headers.get("X-Real-IP", request.remote_addr)
    mac = request.headers.get("X-MAC-Address", "")
    limpiar_sesiones()
    sesiones = SesionActiva.query.filter_by(usuario_id=user.id).all()
    ips_activas = list({s.ip for s in sesiones})
    if ip not in ips_activas and len(ips_activas) >= user.max_conexiones:
        return "Límite de conexiones alcanzado", 403

    sesion = SesionActiva(usuario_id=user.id, ip=ip, mac=mac, canal="m3u")
    db.session.add(sesion)
    db.session.commit()

    base_prov, prov_user, prov_pass = get_proveedor_info()
    host = get_host()

    contenido = get_m3u_list()
    if not contenido:
        return "Error al obtener lista", 502

    # Reescribir URLs para que pasen por nuestro proxy
    contenido = contenido.replace(
        f"{base_prov}/live/{prov_user}/{prov_pass}/",
        f"{host}/live/{usuario}/{contrasena}/"
    )
    contenido = contenido.replace(
        f"{base_prov}/movie/{prov_user}/{prov_pass}/",
        f"{host}/movie/{usuario}/{contrasena}/"
    )
    contenido = contenido.replace(
        f"{base_prov}/series/{prov_user}/{prov_pass}/",
        f"{host}/series/{usuario}/{contrasena}/"
    )

    return Response(contenido, content_type="application/x-mpegURL")


# ════════════════════════════════════════════════════════════════
# STREAMING — HLS RELAY (ANTI-BLOQUEO)
# ════════════════════════════════════════════════════════════════

@app.route("/live/<usuario>/<contrasena>/<canal>")
def live_stream_hls(usuario, contrasena, canal):
    """
    Endpoint principal de streaming live v3.0.
    Usa VLC Relay: 1 conexión al proveedor = N usuarios (indetectable).
    """
    mac = request.headers.get("X-MAC-Address", "")
    ip = request.headers.get("X-Real-IP", request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({"error": resultado}), 403

    if user_obj:
        try:
            log = LogAcceso(usuario_id=user_obj.id, canal=canal, ip=ip)
            db.session.add(log)
            db.session.commit()
        except:
            pass

    canal_id = canal.replace(".ts", "").replace(".m3u8", "")

    # Agregar viewer al canal
    vlc_manager.add_viewer(canal_id)
    
    # Iniciar relay VLC (o reutilizar uno existente)
    local_url = vlc_manager.start_relay(canal_id)
    
    if not local_url:
        vlc_manager.remove_viewer(canal_id)
        return jsonify({"error": "Error al iniciar stream"}), 502
    
    # Esperar a que VLC esté listo
    relay = vlc_manager.get_relay(canal_id)
    if relay:
        relay.ready_event.wait(timeout=15)
    
    if not relay or not relay.ready:
        # Fallback: proxy directo al proveedor
        logger.warning(f"VLC no listo para {canal_id}, usando fallback directo")
        base, user, pwd = get_proveedor_info()
        url_proveedor = f"{base}/live/{user}/{pwd}/{canal_id}.ts"
        try:
            resp = requests.get(url_proveedor, stream=True, timeout=15,
                                headers={"User-Agent": "VLC/3.0.18"})
            def gen_fallback():
                try:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                except:
                    pass
                finally:
                    vlc_manager.remove_viewer(canal_id)
            return Response(gen_fallback(),
                            content_type="video/mp2t",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        except:
            vlc_manager.remove_viewer(canal_id)
            return jsonify({"error": "Error al conectar"}), 502
    
    # Proxy del stream local de VLC a todos los usuarios
    def generate():
        try:
            resp = requests.get(local_url, stream=True, timeout=30,
                                auth=(VLC_HTTP_USER, VLC_HTTP_PASS) if 'VLC_HTTP_USER' in dir() else None)
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk
        except Exception as e:
            logger.error(f"Error en proxy VLC canal {canal_id}: {e}")
        finally:
            vlc_manager.remove_viewer(canal_id)
    
    return Response(
        generate(),
        content_type="video/mp2t",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/hls/<canal_id>/<segmento>")
def serve_hls_segment(canal_id, segmento):
    """Sirve segmentos HLS individuales (para players que lo necesiten)."""
    if request.method == "OPTIONS":
        r = Response()
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    seg_path = os.path.join(HLS_DIR, canal_id, segmento)

    for _ in range(15):
        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
            break
        time.sleep(0.2)

    if not os.path.exists(seg_path):
        return "", 404

    with open(seg_path, "rb") as f:
        data = f.read()

    with _relay_lock:
        if canal_id in _relays:
            _relays[canal_id]["last_view"] = time.time()
            _relays[canal_id]["viewers"] = max(1, _relays[canal_id].get("viewers", 1))

    return Response(data, content_type="video/mp2t", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })


@app.route("/hls/<canal_id>/index.m3u8")
def serve_hls_playlist(canal_id):
    """Sirve el playlist HLS actualizado."""
    info = _relays.get(canal_id)
    if not info:
        return "", 404

    playlist_path = info["playlist"]
    if not os.path.exists(playlist_path):
        return "", 404

    with _relay_lock:
        if canal_id in _relays:
            _relays[canal_id]["last_view"] = time.time()

    with open(playlist_path, "r") as f:
        playlist_content = f.read()

    host = get_host()
    lines = []
    for line in playlist_content.splitlines():
        if line.endswith(".ts") and not line.startswith("#"):
            lines.append(f"{host}/hls/{canal_id}/{line}")
        else:
            lines.append(line)

    return Response("\n".join(lines), content_type="application/vnd.apple.mpegurl", headers={
        "Cache-Control": "no-cache",
        "Access-Control-Allow-Origin": "*",
    })


# ════════════════════════════════════════════════════════════════
# MOVIES Y SERIES — Proxy directo
# ════════════════════════════════════════════════════════════════

@app.route("/movie/<usuario>/<contrasena>/<canal>")
@app.route("/series/<usuario>/<contrasena>/<canal>")
def stream_xtream(usuario, contrasena, canal):
    mac = request.headers.get("X-MAC-Address", "")
    ip = request.headers.get("X-Real-IP", request.remote_addr)
    ruta = request.path.split("/")[1]

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({"error": resultado}), 403

    if user_obj:
        try:
            log = LogAcceso(usuario_id=user_obj.id, canal=canal, ip=ip)
            db.session.add(log)
            db.session.commit()
        except:
            pass

    base_prov, prov_user, prov_pass = get_proveedor_info()
    url_real = f"{base_prov}/{ruta}/{prov_user}/{prov_pass}/{canal}"

    try:
        resp = requests.get(url_real, stream=True, timeout=30,
                            headers={"User-Agent": "VLC/3.0.18"})
        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            except:
                pass
        return Response(generate(),
                        content_type=resp.headers.get("Content-Type", "video/mp2t"),
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    except:
        return jsonify({"error": "Error al conectar"}), 502


@app.route("/stream")
def stream_legacy():
    """Endpoint legacy para compatibilidad."""
    usuario = request.args.get("user", "")
    contrasena = request.args.get("pass", "")
    canal = request.args.get("channel", "")
    mac = request.args.get("mac", request.headers.get("X-MAC-Address", ""))
    ip = request.headers.get("X-Real-IP", request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({"error": resultado}), 403

    canal_id = canal.replace(".ts", "").replace(".m3u8", "")
    if not start_relay(canal_id):
        return jsonify({"error": "Error al iniciar stream"}), 502

    for _ in range(40):
        info = _relays.get(canal_id)
        if info and info.get("ready"):
            break
        time.sleep(0.2)

    info = _relays.get(canal_id)
    if not info or not info.get("ready"):
        stop_relay_viewer(canal_id)
        base_prov, prov_user, prov_pass = get_proveedor_info()
        url_real = f"{base_prov}/live/{prov_user}/{prov_pass}/{canal_id}"
        try:
            resp = requests.get(url_real, stream=True, timeout=30,
                                headers={"User-Agent": "VLC/3.0.18"})
            def generate():
                try:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                except:
                    pass
            return Response(generate(), content_type="video/mp2t",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        except:
            return jsonify({"error": "Error al conectar"}), 502

    def generate_ts():
        seg_index = 0
        consecutive_errors = 0
        try:
            while consecutive_errors < 10:
                with _relay_lock:
                    if canal_id in _relays:
                        _relays[canal_id]["last_view"] = time.time()
                    else:
                        break
                seg_path = os.path.join(HLS_DIR, canal_id, f"seg{seg_index:05d}.ts")
                found = False
                for _ in range(20):
                    if os.path.exists(seg_path) and os.path.getsize(seg_path) > 10000:
                        found = True
                        break
                    time.sleep(0.2)
                if not found:
                    consecutive_errors += 1
                    time.sleep(0.5)
                    continue
                consecutive_errors = 0
                try:
                    with open(seg_path, "rb") as f:
                        while True:
                            chunk = f.read(65536)
                            if not chunk:
                                break
                            yield chunk
                except:
                    break
                seg_index += 1
        finally:
            stop_relay_viewer(canal_id)

    return Response(generate_ts(), content_type="video/mp2t",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/ping")
def ping():
    sid = request.args.get("sid")
    if sid:
        s = SesionActiva.query.get(int(sid))
        if s:
            s.ultimo_ping = datetime.utcnow()
            db.session.commit()
    return "", 204


# ════════════════════════════════════════════════════════════════
# PANEL ADMIN — API
# ════════════════════════════════════════════════════════════════

@app.route("/admin/usuarios", methods=["GET"])
@admin_required
def listar_usuarios():
    users = Usuario.query.order_by(Usuario.creado.desc()).all()
    return jsonify([{
        "id": u.id,
        "usuario": u.usuario,
        "paquete": u.paquete,
        "activo": u.activo,
        "max_conexiones": u.max_conexiones,
        "expira": u.fecha_expira.isoformat(),
        "expira_en_dias": max(0, (u.fecha_expira - datetime.utcnow()).days),
        "conexiones_activas": len(u.sesiones),
        "macs_registradas": len(u.macs),
        "notas": u.notas or "",
    } for u in users])


@app.route("/admin/usuarios", methods=["POST"])
@admin_required
def crear_usuario():
    data = request.json
    paquete = data.get("paquete", "basico")
    pantallas = int(data.get("pantallas", 1))
    dias = int(data.get("dias", 30))
    pwd = secrets.token_urlsafe(8)

    if Usuario.query.filter_by(usuario=data["usuario"]).first():
        return jsonify({"error": "El usuario ya existe"}), 400

    user = Usuario(
        usuario=data["usuario"],
        contrasena=generate_password_hash(pwd),
        paquete=paquete,
        max_conexiones=pantallas,
        fecha_expira=datetime.utcnow() + timedelta(days=dias),
        notas=data.get("notas", ""),
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({
        "usuario": user.usuario,
        "contrasena": pwd,
        "paquete": paquete,
        "max_conexiones": pantallas,
        "expira": user.fecha_expira.isoformat(),
    })


@app.route("/admin/usuarios/<int:uid>", methods=["DELETE"])
@admin_required
def eliminar_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/usuarios/<int:uid>/suspender", methods=["POST"])
@admin_required
def suspender_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    user.activo = not user.activo
    db.session.commit()
    return jsonify({"activo": user.activo})


@app.route("/admin/usuarios/<int:uid>/renovar", methods=["POST"])
@admin_required
def renovar_usuario(uid):
    data = request.json
    dias = int(data.get("dias", 30))
    user = Usuario.query.get_or_404(uid)
    if user.fecha_expira < datetime.utcnow():
        user.fecha_expira = datetime.utcnow() + timedelta(days=dias)
    else:
        user.fecha_expira = user.fecha_expira + timedelta(days=dias)
    user.activo = True
    db.session.commit()
    return jsonify({"expira": user.fecha_expira.isoformat()})


@app.route("/admin/usuarios/<int:uid>/macs")
@admin_required
def ver_macs(uid):
    user = Usuario.query.get_or_404(uid)
    return jsonify([{
        "id": m.id,
        "mac": m.mac,
        "nombre": m.nombre,
        "registrada": m.registrada.isoformat(),
    } for m in user.macs])


@app.route("/admin/macs/<int:mid>", methods=["DELETE"])
@admin_required
def revocar_mac(mid):
    mac = MacRegistrada.query.get_or_404(mid)
    db.session.delete(mac)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/usuarios/<int:uid>/notas", methods=["POST"])
@admin_required
def actualizar_notas(uid):
    user = Usuario.query.get_or_404(uid)
    user.notas = request.json.get("notas", "")
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/usuarios/<int:uid>/reset-password", methods=["POST"])
@admin_required
def reset_password(uid):
    user = Usuario.query.get_or_404(uid)
    pwd = secrets.token_urlsafe(8)
    user.contrasena = generate_password_hash(pwd)
    db.session.commit()
    return jsonify({"usuario": user.usuario, "contrasena": pwd, "expira": user.fecha_expira.isoformat()})


@app.route("/admin/usuarios/<int:uid>/logs")
@admin_required
def ver_logs(uid):
    logs = LogAcceso.query.filter_by(usuario_id=uid).order_by(LogAcceso.fecha.desc()).limit(50).all()
    return jsonify([{
        "canal": l.canal,
        "ip": l.ip,
        "fecha": l.fecha.isoformat(),
    } for l in logs])


@app.route("/admin/logs")
@admin_required
def todos_logs():
    logs = LogAcceso.query.order_by(LogAcceso.fecha.desc()).limit(100).all()
    return jsonify([{
        "usuario": Usuario.query.get(l.usuario_id).usuario if Usuario.query.get(l.usuario_id) else "?",
        "canal": l.canal,
        "ip": l.ip,
        "fecha": l.fecha.isoformat(),
    } for l in logs])


@app.route("/admin/usuarios/<int:uid>/pagos", methods=["GET"])
@admin_required
def listar_pagos(uid):
    user = Usuario.query.get_or_404(uid)
    return jsonify([{
        "id": p.id,
        "monto": p.monto,
        "metodo": p.metodo,
        "notas": p.notas,
        "fecha": p.fecha.isoformat(),
    } for p in sorted(user.pagos, key=lambda x: x.fecha, reverse=True)])


@app.route("/admin/usuarios/<int:uid>/pagos", methods=["POST"])
@admin_required
def registrar_pago(uid):
    user = Usuario.query.get_or_404(uid)
    data = request.json
    pago = Pago(
        usuario_id=uid,
        monto=float(data.get("monto", 0)),
        metodo=data.get("metodo", "Efectivo"),
        notas=data.get("notas", ""),
    )
    db.session.add(pago)
    db.session.commit()
    return jsonify({"ok": True, "id": pago.id})


@app.route("/admin/pagos/<int:pid>", methods=["DELETE"])
@admin_required
def eliminar_pago(pid):
    pago = Pago.query.get_or_404(pid)
    db.session.delete(pago)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/resumen-pagos")
@admin_required
def resumen_pagos():
    from sqlalchemy import func
    from collections import defaultdict
    pagos = Pago.query.all()
    total = sum(p.monto for p in pagos)
    este_mes = sum(p.monto for p in pagos if p.fecha.month == datetime.utcnow().month and p.fecha.year == datetime.utcnow().year)
    por_mes = defaultdict(float)
    for p in pagos:
        key = p.fecha.strftime("%Y-%m")
        por_mes[key] += p.monto
    return jsonify({
        "total": total,
        "este_mes": este_mes,
        "por_mes": dict(sorted(por_mes.items())[-6:]),
    })


@app.route("/admin/paquetes", methods=["GET"])
@admin_required
def listar_paquetes():
    paquetes = Paquete.query.all()
    return jsonify([{
        "id": p.id,
        "nombre": p.nombre,
        "descripcion": p.descripcion,
        "categorias": p.categorias,
    } for p in paquetes])


@app.route("/admin/paquetes", methods=["POST"])
@admin_required
def crear_paquete():
    data = request.json
    if Paquete.query.filter_by(nombre=data["nombre"]).first():
        return jsonify({"error": "Ya existe un paquete con ese nombre"}), 400
    p = Paquete(
        nombre=data["nombre"],
        descripcion=data.get("descripcion", ""),
        categorias=data.get("categorias", ""),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "id": p.id})


@app.route("/admin/paquetes/<int:pid>", methods=["PUT"])
@admin_required
def actualizar_paquete(pid):
    p = Paquete.query.get_or_404(pid)
    data = request.json
    if "descripcion" in data:
        p.descripcion = data["descripcion"]
    if "categorias" in data:
        p.categorias = data["categorias"]
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/paquetes/<int:pid>", methods=["DELETE"])
@admin_required
def eliminar_paquete(pid):
    p = Paquete.query.get_or_404(pid)
    db.session.delete(p)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/categorias-proveedor")
@admin_required
def categorias_proveedor():
    base_prov, prov_user, prov_pass = get_proveedor_info()
    try:
        resp = requests.get(f"{base_prov}/player_api.php",
            params={"username": prov_user, "password": prov_pass, "action": "get_live_categories"},
            timeout=15)
        return Response(resp.content, content_type="application/json")
    except:
        return jsonify([])


@app.route("/admin/stats")
@admin_required
def stats():
    limpiar_sesiones()
    return jsonify({
        "total_usuarios": Usuario.query.count(),
        "usuarios_activos": Usuario.query.filter_by(activo=True).count(),
        "usuarios_expirados": Usuario.query.filter(Usuario.fecha_expira < datetime.utcnow()).count(),
        "online_ahora": SesionActiva.query.distinct(SesionActiva.usuario_id).count(),
        "ingresos_mes": sum(p.monto for p in Pago.query.all() if p.fecha.month == datetime.utcnow().month and p.fecha.year == datetime.utcnow().year),
        "relays_activos": len(_relays),
    })


@app.route("/admin/relays")
@admin_required
def ver_relays():
    """Ver estado de los relays activos (debug)."""
    with _relay_lock:
        return jsonify({
            cid: {
                "viewers": info["viewers"],
                "ready": info["ready"],
                "uptime": int(time.time() - info.get("start_time", time.time())),
                "url": info["url"],
            }
            for cid, info in _relays.items()
        })


# ════════════════════════════════════════════════════════════════
# WHATSAPP
# ════════════════════════════════════════════════════════════════

@app.route("/admin/wa/status")
@admin_required
def wa_status():
    try:
        resp = requests.get(f"{WA_SERVICE_URL}/status", timeout=5)
        return jsonify(resp.json())
    except:
        return jsonify({"ready": False, "hasQr": False, "error": "Servicio WA no disponible"})


@app.route("/admin/wa/send", methods=["POST"])
@admin_required
def wa_send():
    data = request.json
    try:
        resp = requests.post(f"{WA_SERVICE_URL}/send", json=data, timeout=10)
        return jsonify(resp.json())
    except:
        return jsonify({"ok": False, "error": "Error al enviar"})


@app.route("/admin/wa/notify-expiring", methods=["POST"])
@admin_required
def wa_notify_expiring():
    dias = int(request.json.get("dias", 3))
    try:
        users = Usuario.query.filter(
            Usuario.activo == True,
            Usuario.notas != "",
            Usuario.notas != None,
            Usuario.fecha_expira > datetime.utcnow(),
            Usuario.fecha_expira <= datetime.utcnow() + timedelta(days=dias),
        ).all()
        enviados = 0
        for u in users:
            dias_rest = (u.fecha_expira - datetime.utcnow()).days + 1
            mensaje = (
                "⚠️ *¡Tu suscripción StreamFlow vence pronto!*\n\n"
                + f"Hola *{u.usuario}*, tu acceso vence en *{dias_rest} día" + ("s" if dias_rest != 1 else "") + "*.\n\n"
                + "Para renovar, contáctanos 👇\n"
                + "_Soporte vía WhatsApp_ 💬"
            )
            try:
                resp = requests.post(f"{WA_SERVICE_URL}/send",
                    json={"phone": u.notas, "message": mensaje}, timeout=10)
                if resp.json().get("ok"):
                    enviados += 1
            except:
                pass
        return jsonify({"ok": True, "enviados": enviados, "total": len(users)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ════════════════════════════════════════════════════════════════
# FRONTEND
# ════════════════════════════════════════════════════════════════

@app.route("/tv/")
@app.route("/tv")
def tv_app():
    return send_from_directory("tv", "index.html")


@app.route("/tv/<path:filename>")
def tv_static(filename):
    return send_from_directory("tv", filename)


@app.route("/wa/qr")
def wa_qr():
    try:
        resp = requests.get(f"{WA_SERVICE_URL}/qr", timeout=10)
        return Response(resp.content, content_type=resp.headers.get("Content-Type", "text/html"))
    except:
        return "<h2 style='font-family:sans-serif;color:#ef4444;padding:40px'>Servicio WhatsApp iniciando... espera unos segundos y recarga.</h2>", 503


@app.route("/panel/")
@app.route("/panel")
@admin_required
def panel():
    return send_from_directory("panel", "index.html")


@app.route("/panel/<path:filename>")
@admin_required
def panel_static(filename):
    return send_from_directory("panel", filename)


# ════════════════════════════════════════════════════════════════
# INICIALIZACIÓN
# ════════════════════════════════════════════════════════════════

with app.app_context():
    db.create_all()
    if Paquete.query.count() == 0:
        for nombre, config in PAQUETES_DEFAULT.items():
            db.session.add(Paquete(
                nombre=nombre,
                descripcion=config["descripcion"],
                categorias=",".join(config["categorias_nombres"]),
            ))
        db.session.commit()
        logger.info("Paquetes por defecto creados")
    init_relay_cleanup()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
