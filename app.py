"""
StreamFlow v4.0 - Backend con Smart Relay (1 cuenta → usuarios ilimitados)
==========================================================================
Mejoras v4.0:
- Smart Relay: 3 conexiones dinámicas del proveedor rotan por demanda
- Los canales más vistos siempre tienen conexión
- Rotación automática cuando cambia la demanda
- Buffer HLS para que el usuario no note los cambios
- 1 cuenta del proveedor = usuarios ilimitados (con delay aceptable)
- Películas y series con mismo sistema de relay

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
from collections import defaultdict

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

# Base de datos - 100% desde variable de entorno, sin defaults hardcodeados
_db_url = os.environ.get("DATABASE_URL", "")
if not _db_url:
    pg_user = os.environ.get("POSTGRES_USER", "postgres")
    pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
    pg_host = os.environ.get("POSTGRES_HOST", "db")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_db = os.environ.get("POSTGRES_DB", "streamflow")
    _db_url = f"postgresql://{pg_user}:***@{pg_host}:{pg_port}/{pg_db}"

# SQLAlchemy 2.x requiere postgresql:// no postgres://
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Proveedor IPTV (cuentas premium)
URL_M3U_PROVEEDOR = os.environ.get("URL_M3U", "")
WA_SERVICE_URL = os.environ.get("WA_SERVICE_URL", "http://localhost:3001")

# Listas gratuitas para canales de relleno
# Formato: lista de URLs M3U públicas
FREE_M3U_LISTS = os.environ.get("FREE_M3U_LISTS", "").split(",") if os.environ.get("FREE_M3U_LISTS") else [
    "https://iptv-org.github.io/iptv/index.m3u8",
]

# Canales premium (los que vienen del proveedor de pago)
# Se configuran automáticamente desde la lista M3U del proveedor
PREMIUM_CHANNELS_FILE = os.environ.get("PREMIUM_CHANNELS_FILE", "/tmp/premium_channels.json")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("streamflow")
hls_log = logging.getLogger("hls")

# ════════════════════════════════════════════════════════════════
# SMART RELAY MANAGER — 3 conexiones → usuarios ilimitados
# ════════════════════════════════════════════════════════════════
#
# Estrategia: Las 3 conexiones del proveedor se asignan dinámicamente
# a los canales más vistos. Cuando cambia la demanda, rota.
#
# Ejemplo:
#   45 usuarios ven RCN     → Conexión 1 (fija por demanda alta)
#   30 usuarios ven Caracol → Conexión 2 (fija por demanda alta)
#   15 usuarios ven ESPN    → Conexión 3 (dinámica)
#   8 usuarios ven Fox      → Espera (cuando ESPN baja, toma la conexión)
#
# El proveedor siempre ve solo 3 conexiones de tu VPS.
# Los usuarios ven TODOS los canales con un delay de 30-90 segundos.

SMART_MAX_CONNECTIONS = int(os.environ.get("SMART_MAX_CONNECTIONS", "3"))  # conexiones del proveedor
SMART_ROTATION_INTERVAL = int(os.environ.get("SMART_ROTATION_INTERVAL", "30"))  # segundos entre rotaciones
SMART_BUFFER_SECONDS = int(os.environ.get("SMART_BUFFER_SECONDS", "10"))  # buffer HLS para el usuario

# Estado de canales: { canal_id: viewers_count }
_channel_viewers = defaultdict(int)
_channel_viewers_lock = threading.Lock()

# Relays activos: { canal_id: { proc, path, playlist, url, started_at } }
_active_relays = {}
_relays_lock = threading.Lock()

# Cola de canales esperando conexión
_pending_channels = []


def _get_canal_url(canal_id):
    """Obtiene la URL del proveedor para un canal."""
    base, user, pwd = get_proveedor_info()
    if not base:
        return None
    return f"{base}/live/{user}/{pwd}/{canal_id}.ts"


def _start_canal_relay(canal_id):
    """Inicia un relay FFmpeg para un canal específico."""
    url = _get_canal_url(canal_id)
    if not url:
        return False

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
        "-i", url,
        "-c", "copy",
        "-f", "hls",
        "-hls_time", str(HLS_SEGMENT_TIME),
        "-hls_list_size", str(HLS_LIST_SIZE),
        "-hls_flags", "append_list+delete_segments",
        "-hls_segment_filename", os.path.join(hls_path, "seg%05d.ts"),
        playlist,
    ]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        with _relays_lock:
            _active_relays[canal_id] = {
                "proc": proc,
                "path": hls_path,
                "playlist": playlist,
                "url": url,
                "started_at": time.time(),
            }
        logger.info(f"Smart Relay: canal {canal_id} iniciado (PID {proc.pid})")
        return True
    except Exception as e:
        logger.error(f"Smart Relay: error iniciando canal {canal_id}: {e}")
        return False


def _stop_canal_relay(canal_id):
    """Detiene un relay de canal."""
    with _relays_lock:
        relay = _active_relays.get(canal_id)
        if relay:
            try:
                relay["proc"].terminate()
                relay["proc"].wait(timeout=5)
            except:
                try:
                    relay["proc"].kill()
                except:
                    pass
            del _active_relays[canal_id]
            logger.info(f"Smart Relay: canal {canal_id} detenido")


def _smart_rotation_loop():
    """
    Hilo principal de rotación inteligente.
    Cada SMART_ROTATION_INTERVAL segundos:
    1. Cuenta viewers por canal
    2. Asigna las 3 conexiones a los más vistos
    3. Detiene relays que ya no tienen viewers
    4. Inicia relays para canales con viewers sin conexión
    """
    while True:
        time.sleep(SMART_ROTATION_INTERVAL)

        try:
            with _channel_viewers_lock:
                # Ordenar canales por viewers (más vistos primero)
                canales_ordenados = sorted(
                    _channel_viewers.items(),
                    key=lambda x: x[1],
                    reverse=True
                )

            # Canales que deberían tener conexión (los N más vistos)
            canales_activos = set()
            for i, (canal_id, viewers) in enumerate(canales_ordenados):
                if i < SMART_MAX_CONNECTIONS and viewers > 0:
                    canales_activos.add(canal_id)

            with _relays_lock:
                # Detener relays que ya no están en top
                for canal_id in list(_active_relays.keys()):
                    if canal_id not in canales_activos:
                        _stop_canal_relay(canal_id)

                # Iniciar relays para canales nuevos
                for canal_id in canales_activos:
                    if canal_id not in _active_relays:
                        _start_canal_relay(canal_id)

            # Log de estado
            activos = list(canales_activos)[:SMART_MAX_CONNECTIONS]
            logger.info(f"Smart Relay: {len(activos)} canales activos: {activos}")

        except Exception as e:
            logger.error(f"Smart Relay: error en rotación: {e}")


def smart_add_viewer(canal_id):
    """Registra un viewer viendo un canal."""
    with _channel_viewers_lock:
        _channel_viewers[canal_id] += 1


def smart_remove_viewer(canal_id):
    """Remueve un viewer de un canal."""
    with _channel_viewers_lock:
        _channel_viewers[canal_id] = max(0, _channel_viewers[canal_id] - 1)


def smart_get_relay(canal_id):
    """Obtiene el relay activo para un canal."""
    with _relays_lock:
        return _active_relays.get(canal_id)


def smart_get_stats():
    """Estadísticas del Smart Relay."""
    with _relays_lock:
        activos = list(_active_relays.keys())
    with _channel_viewers_lock:
        viewers = dict(_channel_viewers)
    return {
        "max_connections": SMART_MAX_CONNECTIONS,
        "active_relays": len(activos),
        "active_channels": activos,
        "channel_viewers": viewers,
        "rotation_interval": SMART_ROTATION_INTERVAL,
    }


# Iniciar hilo de rotación
_smart_rotation_thread = threading.Thread(target=_smart_rotation_loop, daemon=True)
_smart_rotation_thread.start()
logger.info(f"Smart Relay iniciado: {SMART_MAX_CONNECTIONS} conexiones, rotación cada {SMART_ROTATION_INTERVAL}s")


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


# ════════════════════════════════════════════════════════════════
# MODELOS DE CANALES LÓGICOS Y FUENTES
# ════════════════════════════════════════════════════════════════
# Un "canal lógico" es el canal que ve el usuario (ej: "RCN").
# Una "fuente" es la URL real de ese canal en un proveedor.
#
# Cuando el admin cambia de proveedor:
#   1. Las fuentes del proveedor anterior se marcan inactivas
#   2. Se cargan los canales del nuevo proveedor
#   3. Se vinculan automáticamente por nombre a los canales lógicos
#   4. Si un canal no existe en el nuevo proveedor, usa backup gratuito

class CanalLogico(db.Model):
    """Canal que ve el usuario. Independiente del proveedor."""
    __tablename__ = "canales_logicos"
    id = db.Column(db.Integer, primary_key=True)
    canal_id = db.Column(db.String(100), unique=True, nullable=False)  # ej: "rcn", "caracol"
    nombre = db.Column(db.String(200), nullable=False)  # ej: "RCN Televisión"
    display_name = db.Column(db.String(200), default="")  # ej: "RCN"
    grupo = db.Column(db.String(100), default="")  # ej: "Colombia", "Deportes"
    logo = db.Column(db.String(500), default="")
    pais = db.Column(db.String(50), default="")
    prioridad = db.Column(db.Integer, default=100)  # menor = más arriba en la lista
    activo = db.Column(db.Boolean, default=True)
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relación con fuentes
    fuentes = db.relationship("CanalFuente", backref="canal_logico", lazy="dynamic",
                               cascade="all, delete-orphan")

    def get_fuente_activa(self):
        """Retorna la mejor fuente activa para este canal."""
        return self.fuentes.filter_by(activo=True).order_by(CanalFuente.prioridad).first()

    def get_todas_fuentes_activas(self):
        """Retorna todas las fuentes activas ordenadas por prioridad."""
        return self.fuentes.filter_by(activo=True).order_by(CanalFuente.prioridad).all()

    def to_dict(self):
        fuente = self.get_fuente_activa()
        return {
            "id": self.id,
            "canal_id": self.canal_id,
            "nombre": self.nombre,
            "display_name": self.display_name or self.nombre,
            "grupo": self.grupo,
            "logo": self.logo,
            "pais": self.pais,
            "prioridad": self.prioridad,
            "activo": self.activo,
            "fuente_activa": fuente.to_dict() if fuente else None,
            "fuentes_count": self.fuentes.filter_by(activo=True).count(),
        }


class CanalFuente(db.Model):
    """URL real de un canal en un proveedor específico."""
    __tablename__ = "canales_fuentes"
    id = db.Column(db.Integer, primary_key=True)
    canal_logico_id = db.Column(db.Integer, db.ForeignKey("canales_logicos.id"), nullable=False)
    proveedor = db.Column(db.String(100), nullable=False)  # "proveedor1", "gratuito", etc.
    url = db.Column(db.Text, nullable=False)  # URL real del stream
    nombre_fuente = db.Column(db.String(200), default="")  # Nombre en el proveedor
    prioridad = db.Column(db.Integer, default=1)  # 1=premium, 2=backup, 3=gratuito
    activo = db.Column(db.Boolean, default=True)
    estado = db.Column(db.String(20), default="ok")  # ok, caido, reemplazado
    ultimo_check = db.Column(db.DateTime)
    ultimo_error = db.Column(db.String(500), default="")
    vez_usado = db.Column(db.Integer, default=0)
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    actualizado = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "proveedor": self.proveedor,
            "url": self.url if self.activo else None,
            "nombre_fuente": self.nombre_fuente,
            "prioridad": self.prioridad,
            "activo": self.activo,
            "estado": self.estado,
            "ultimo_check": self.ultimo_check.isoformat() if self.ultimo_check else None,
            "ultimo_error": self.ultimo_error,
        }


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
    Endpoint de streaming live v4.0 con Smart Relay.
    3 conexiones del proveedor rotan por demanda → usuarios ilimitados.
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

    # Registrar viewer en el Smart Relay
    smart_add_viewer(canal_id)

    try:
        # Esperar a que el Smart Relay asigne una conexión a este canal
        # (máximo SMART_ROTATION_INTERVAL segundos de espera)
        espera_max = SMART_ROTATION_INTERVAL + 10
        relay = None
        for _ in range(espera_max * 2):
            relay = smart_get_relay(canal_id)
            if relay and os.path.exists(relay["playlist"]) and os.path.getsize(relay["playlist"]) > 0:
                break
            time.sleep(0.5)

        if not relay:
            return jsonify({"error": "Canal no disponible temporalmente. Intente en unos segundos."}), 503

        # Servir segmentos HLS del relay
        def generate():
            try:
                while True:
                    r = smart_get_relay(canal_id)
                    if not r:
                        break
                    playlist_path = r["playlist"]
                    if os.path.exists(playlist_path):
                        with open(playlist_path) as f:
                            content = f.read()
                        segs = [l.strip() for l in content.split("\n") if l.strip().endswith(".ts")]
                        for seg_name in segs:
                            seg_path = os.path.join(r["path"], seg_name)
                            if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                                with open(seg_path, "rb") as sf:
                                    yield sf.read()
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Error streaming canal {canal_id}: {e}")
            finally:
                smart_remove_viewer(canal_id)

        return Response(generate(),
                        content_type="video/mp2t",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    except Exception as e:
        smart_remove_viewer(canal_id)
        return jsonify({"error": "Error en el servidor de streaming"}), 500


@app.route("/api/smart-relay/stats")
@admin_required
def api_smart_relay_stats():
    """Estadísticas del Smart Relay."""
    return jsonify(smart_get_stats())


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


@app.route("/health")
def health_check():
    """Health check para Guardian Agent. No requiere auth."""
    try:
        CanalLogico.query.count()
        return jsonify({"status": "ok", "timestamp": datetime.utcnow().isoformat()}), 200
    except:
        return jsonify({"status": "error"}), 503


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

# ── Conexión a BD con retry (espera a que la red overlay esté lista) ──
_db_connected = False
_db_retries = 0
_max_db_retries = 30  # 30 intentos = ~60 segundos
while not _db_connected and _db_retries < _max_db_retries:
    try:
        with app.app_context():
            db.create_all()
        _db_connected = True
        logger.info(f"BD conectada después de {_db_retries + 1} intento(s)")
    except Exception as e:
        _db_retries += 1
        logger.warning(f"BD no disponible (intento {_db_retries}/{_max_db_retries}): {e}")
        time.sleep(2)

if not _db_connected:
    logger.error("No se pudo conectar a la BD después de 30 intentos. Abortando.")
    sys.exit(1)

with app.app_context():
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

    # Inicializar Channel Manager
    from channel_manager import init_channel_manager
    init_channel_manager(db, app)
    logger.info("Channel Manager iniciado")


# ════════════════════════════════════════════════════════════════
# SERVIDOR HTTP (debe ir al final, después de definir todos los routes)
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    _port = int(os.environ.get("PORT", "5000"))
    _debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    logger.info(f"StreamFlow iniciando en puerto {_port} (debug={_debug})")
    app.run(host="0.0.0.0", port=_port, debug=_debug, threaded=True)


# ════════════════════════════════════════════════════════════════
# APIs DE GESTIÓN DE CANALES (Panel Admin)
# ════════════════════════════════════════════════════════════════

def _get_channel_manager():
    """Obtiene la instancia del channel manager."""
    from channel_manager import channel_manager
    return channel_manager


# ── Carga de canales ──

@app.route("/admin/channels/load-provider", methods=["POST"])
@admin_required
def admin_load_provider():
    """
    Carga canales desde un proveedor (URL M3U).
    Body: { url: "http://proveedor.com/get.php?...", nombre: "Proveedor1" }
    """
    data = request.json or {}
    url = data.get("url", "").strip()
    nombre = data.get("nombre", "proveedor").strip()

    if not url:
        return jsonify({"error": "URL requerida"}), 400

    try:
        resp = requests.get(url, timeout=60)
        if resp.status_code != 200:
            return jsonify({"error": f"HTTP {resp.status_code}"}), 502

        cm = _get_channel_manager()
        nuevos, vinculados, total = cm.load_from_proveedor(nombre, resp.text)

        return jsonify({
            "ok": True,
            "proveedor": nombre,
            "nuevos": nuevos,
            "vinculados": vinculados,
            "total": total,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/channels/change-provider", methods=["POST"])
@admin_required
def admin_change_provider():
    """
    Cambia de proveedor. Desactiva el anterior y carga el nuevo.
    Body: {
        proveedor_anterior: "Proveedor1",
        proveedor_nuevo: "Proveedor2",
        url_nueva: "http://nuevo.com/get.php?..."
    }
    """
    data = request.json or {}
    prov_anterior = data.get("proveedor_anterior", "").strip()
    prov_nuevo = data.get("proveedor_nuevo", "").strip()
    url_nueva = data.get("url_nueva", "").strip()

    if not prov_nuevo or not url_nueva:
        return jsonify({"error": "proveedor_nuevo y url_nueva requeridos"}), 400

    try:
        resp = requests.get(url_nueva, timeout=60)
        if resp.status_code != 200:
            return jsonify({"error": f"HTTP {resp.status_code}"}), 502

        cm = _get_channel_manager()
        desactivadas, nuevos, vinculados, sin_fuente = cm.cambiar_proveedor(
            prov_anterior, prov_nuevo, resp.text
        )

        return jsonify({
            "ok": True,
            "proveedor_anterior": prov_anterior,
            "proveedor_nuevo": prov_nuevo,
            "fuentes_desactivadas": desactivadas,
            "canales_nuevos": nuevos,
            "canales_vinculados": vinculados,
            "canales_sin_fuente": sin_fuente,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin/channels/load-free", methods=["POST"])
@admin_required
def admin_load_free():
    """
    Carga canales desde listas gratuitas como backup.
    Body: { urls: ["https://iptv-org.github.io/iptv/index.m3u8", ...] }
    """
    data = request.json or {}
    urls = data.get("urls", [])

    if not urls:
        return jsonify({"error": "URLs requeridas"}), 400

    try:
        cm = _get_channel_manager()
        nuevos, vinculados = cm.load_from_free_lists(urls)
        return jsonify({"ok": True, "nuevos": nuevos, "vinculados": vinculados})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Listado y gestión ──

@app.route("/admin/channels", methods=["GET"])
@admin_required
def admin_list_channels():
    """
    Lista todos los canales lógicos con su estado.
    Query params:
        search: buscar por nombre
        group: filtrar por grupo
        estado: filtrar por estado (ok, caido, sin_fuente)
        page, limit: paginación
    """
    from app import CanalLogico

    search = request.args.get("search", "").lower()
    group = request.args.get("group", "")
    estado = request.args.get("estado", "")
    page = int(request.args.get("page", 1))
    limit = min(int(request.args.get("limit", 50)), 200)

    query = CanalLogico.query.filter_by(activo=True)

    if search:
        query = query.filter(
            db.or_(
                CanalLogico.nombre.ilike(f"%{search}%"),
                CanalLogico.canal_id.ilike(f"%{search}%"),
            )
        )
    if group:
        query = query.filter(CanalLogico.grupo.ilike(f"%{group}%"))

    total = query.count()
    canales = query.order_by(CanalLogico.prioridad, CanalLogico.nombre).offset(
        (page - 1) * limit
    ).limit(limit).all()

    result = []
    for ch in canales:
        d = ch.to_dict()
        # Agregar estado de la fuente activa
        fuente = ch.get_fuente_activa()
        d["fuente_estado"] = fuente.estado if fuente else "sin_fuente"
        d["fuente_proveedor"] = fuente.proveedor if fuente else None
        d["fuente_url"] = fuente.url if fuente and fuente.activo else None
        result.append(d)

    # Filtrar por estado si se pide
    if estado:
        result = [r for r in result if r.get("fuente_estado") == estado]

    # Grupos disponibles
    grupos = db.session.query(CanalLogico.grupo).filter(
        CanalLogico.activo == True,
        CanalLogico.grupo != ""
    ).distinct().all()
    grupos = sorted([g[0] for g in grupos if g[0]])

    return jsonify({
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
        "groups": grupos,
        "canales": result,
    })


@app.route("/admin/channels/<canal_id>/check", methods=["POST"])
@admin_required
def admin_check_channel(canal_id):
    """Verifica si un canal funciona y lo reemplaza si está caído."""
    cm = _get_channel_manager()
    estado, msg = cm.check_and_replace(canal_id)
    return jsonify({"ok": True, "estado": estado, "mensaje": msg})


@app.route("/admin/channels/check-all", methods=["POST"])
@admin_required
def admin_check_all_channels():
    """Verifica todos los canales y reemplaza los caídos."""
    cm = _get_channel_manager()
    results = cm.auto_replace_all()
    return jsonify({"ok": True, "results": results})


@app.route("/admin/channels/<canal_id>/sources", methods=["GET"])
@admin_required
def admin_channel_sources(canal_id):
    """Obtiene todas las fuentes de un canal."""
    from app import CanalLogico

    canal = CanalLogico.query.filter_by(canal_id=canal_id).first()
    if not canal:
        return jsonify({"error": "Canal no encontrado"}), 404

    fuentes = canal.fuentes.order_by(CanalFuente.prioridad).all()
    return jsonify({
        "canal": canal.to_dict(),
        "fuentes": [f.to_dict() for f in fuentes],
    })


@app.route("/admin/channels/<canal_id>/sources", methods=["POST"])
@admin_required
def admin_add_source(canal_id):
    """Agrega una fuente manual a un canal."""
    from app import CanalLogico, CanalFuente

    data = request.json or {}
    url = data.get("url", "").strip()
    proveedor = data.get("proveedor", "manual").strip()
    prioridad = data.get("prioridad", 2)

    if not url:
        return jsonify({"error": "URL requerida"}), 400

    canal = CanalLogico.query.filter_by(canal_id=canal_id).first()
    if not canal:
        return jsonify({"error": "Canal no encontrado"}), 404

    fuente = CanalFuente(
        canal_logico_id=canal.id,
        proveedor=proveedor,
        url=url,
        prioridad=prioridad,
        activo=True,
        estado="ok",
    )
    db.session.add(fuente)
    db.session.commit()

    return jsonify({"ok": True, "fuente": fuente.to_dict()})


@app.route("/admin/channels/<canal_id>/sources/<int:fuente_id>", methods=["DELETE"])
@admin_required
def admin_remove_source(canal_id, fuente_id):
    """Desactiva una fuente."""
    from app import CanalFuente

    fuente = CanalFuente.query.get_or_404(fuente_id)
    fuente.activo = False
    fuente.estado = "desactivado"
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/admin/channels/stats", methods=["GET"])
@admin_required
def admin_channels_stats():
    """Estadísticas del sistema de canales."""
    cm = _get_channel_manager()
    return jsonify(cm.get_stats())


# ── Listas por plan ──

@app.route("/admin/plans/<plan>/channels", methods=["GET"])
@admin_required
def admin_plan_channels(plan):
    """Obtiene los canales asignados a un plan."""
    from app import CanalLogico, Paquete

    paquete = Paquete.query.filter_by(nombre=plan).first()
    if not paquete:
        return jsonify({"error": "Plan no encontrado"}), 404

    # Obtener canales del plan por categorías
    categorias = [c.strip().upper() for c in paquete.categorias.split(",") if c.strip()]

    canales = CanalLogico.query.filter_by(activo=True).order_by(
        CanalLogico.prioridad, CanalLogico.nombre
    ).all()

    result = []
    for canal in canales:
        canal_grupo = (canal.grupo or "").upper()
        if any(cat in canal_grupo or canal_grupo in cat for cat in categorias):
            d = canal.to_dict()
            d["in_plan"] = True
            result.append(d)

    return jsonify({
        "plan": plan,
        "canales": result,
        "total": len(result),
    })


@app.route("/admin/plans/<plan>/channels", methods=["POST"])
@admin_required
def admin_save_plan_channels(plan):
    """
    Guarda la lista de canales de un plan.
    Body: { canales: [ { canal_id, nombre, orden }, ... ] }
    """
    data = request.json or {}
    canales = data.get("canales", [])

    # Actualizar las categorías del paquete basado en los canales seleccionados
    from app import CanalLogico, Paquete

    paquete = Paquete.query.filter_by(nombre=plan).first()
    if not paquete:
        return jsonify({"error": "Plan no encontrado"}), 404

    # Guardar la lista como metadatos del paquete
    paquete.categorias = json.dumps(canales)
    db.session.commit()

    return jsonify({"ok": True, "total": len(canales)})


# ── Generador de M3U por plan ──

@app.route("/m3u/<plan>")
def m3u_by_plan(plan):
    """Genera lista M3U personalizada para un plan."""
    usuario = request.args.get("username", "")
    contrasena = request.args.get("password", "")

    user, error = autenticar(usuario, contrasena)
    if not user:
        return "Acceso denegado", 403

    if user.paquete != plan:
        return "Plan no autorizado", 403

    from app import CanalLogico, Paquete

    paquete = Paquete.query.filter_by(nombre=plan).first()
    if not paquete:
        return "#EXTM3U\n# Plan no encontrado", 200

    categorias = [c.strip().upper() for c in paquete.categorias.split(",") if c.strip()]
    canales = CanalLogico.query.filter_by(activo=True).order_by(
        CanalLogico.prioridad, CanalLogico.nombre
    ).all()

    m3u_lines = ["#EXTM3U"]
    host = request.host_url.rstrip("/")

    for canal in canales:
        canal_grupo = (canal.grupo or "").upper()
        if not any(cat in canal_grupo or canal_grupo in cat for cat in categorias):
            continue

        fuente = canal.get_fuente_activa()
        if not fuente:
            continue

        name = canal.display_name or canal.nombre
        logo = canal.logo or ""
        tvg_id = canal.canal_id
        group = canal.grupo or plan

        extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name}'
        m3u_lines.append(extinf)
        m3u_lines.append(f"{host}/live/{usuario}/{contrasena}/{canal.canal_id}")

    return Response("\n".join(m3u_lines), content_type="application/x-mpegURL")
    """Agrega un canal a la lista de un plan."""
    data = request.json or {}
    channel_id = data.get("channel_id")
    channel_name = data.get("channel_name", "")
    is_premium = data.get("is_premium", False)

    if not channel_id:
        return jsonify({"error": "channel_id requerido"}), 400

    with _custom_lists_lock:
        if plan not in _custom_lists:
            _custom_lists[plan] = []

        # Verificar si ya existe
        existing = [ch for ch in _custom_lists[plan] if ch.get("channel_id") == channel_id]
        if existing:
            return jsonify({"error": "El canal ya está en la lista"}), 409

        _custom_lists[plan].append({
            "channel_id": channel_id,
            "name": channel_name,
            "is_premium": is_premium,
            "order": len(_custom_lists[plan]) + 1,
            "added_at": datetime.utcnow().isoformat(),
        })

    return jsonify({"ok": True})


@app.route("/admin/lists/<plan>/remove", methods=["POST"])
@admin_required
def admin_remove_channel_from_plan(plan):
    """Remueve un canal de la lista de un plan."""
    data = request.json or {}
    channel_id = data.get("channel_id")

    if not channel_id:
        return jsonify({"error": "channel_id requerido"}), 400

    with _custom_lists_lock:
        if plan in _custom_lists:
            _custom_lists[plan] = [
                ch for ch in _custom_lists[plan]
                if ch.get("channel_id") != channel_id
            ]

    return jsonify({"ok": True})


@app.route("/admin/lists/<plan>/reorder", methods=["POST"])
@admin_required
def admin_reorder_list(plan):
    """Reordena los canales de un plan."""
    data = request.json or {}
    ordered_ids = data.get("channel_ids", [])

    with _custom_lists_lock:
        if plan not in _custom_lists:
            _custom_lists[plan] = []

        # Reordenar según la lista de IDs
        ordered = []
        for i, ch_id in enumerate(ordered_ids):
            for ch in _custom_lists[plan]:
                if ch.get("channel_id") == ch_id:
                    ch["order"] = i + 1
                    ordered.append(ch)
                    break
        _custom_lists[plan] = ordered

    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════
# MOVIES Y SERIES API (VOD - Video On Demand)
# ════════════════════════════════════════════════════════════════

# Cache para películas y series
_vod_cache = {
    "movies": {"data": [], "timestamp": 0, "ttl": 600},      # 10 min cache
    "series": {"data": [], "timestamp": 0, "ttl": 600},
    "movie_cats": {"data": [], "timestamp": 0, "ttl": 3600},  # 1 hora cache
    "series_cats": {"data": [], "timestamp": 0, "ttl": 3600},
}


def _fetch_vod_list(tipo="movie"):
    """Obtiene lista de películas/series del proveedor con cache."""
    cache = _vod_cache[tipo]
    now = time.time()
    if cache["data"] and (now - cache["timestamp"]) < cache["ttl"]:
        return cache["data"]

    base_prov, prov_user, prov_pass = get_proveedor_info()
    if not base_prov:
        return []

    try:
        action = f"get_{tipo}_categories"
        resp = requests.get(
            f"{base_prov}/player_api.php",
            params={"username": prov_user, "password": prov_pass, "action": action},
            timeout=15,
        )
        data = resp.json()
        if isinstance(data, list):
            cache["data"] = data
            cache["timestamp"] = now
            return data
    except Exception as e:
        logger.error(f"Error obteniendo {tipo} list: {e}")
    return cache["data"]


def _fetch_vod_info(tipo, vod_id):
    """Obtiene info detallada de una película/serie."""
    base_prov, prov_user, prov_pass = get_proveedor_info()
    if not base_prov:
        return None
    try:
        resp = requests.get(
            f"{base_prov}/player_api.php",
            params={
                "username": prov_user,
                "password": prov_pass,
                "action": f"get_{tipo}_info",
                f"{tipo}_id": vod_id,
            },
            timeout=15,
        )
        return resp.json()
    except:
        return None


@app.route("/api/movies/categories", methods=["GET"])
@admin_required
def api_movie_categories():
    """Lista categorías de películas."""
    cats = _fetch_vod_list("movie")
    return jsonify(cats)


@app.route("/api/movies", methods=["GET"])
@admin_required
def api_movies_list():
    """Lista películas con filtro por categoría."""
    categoria = request.args.get("category", "")
    pagina = int(request.args.get("page", 1))
    limite = int(request.args.get("limit", 50))

    base_prov, prov_user, prov_pass = get_proveedor_info()
    if not base_prov:
        return jsonify({"error": "Proveedor no configurado"}), 502

    try:
        params = {"username": prov_user, "password": prov_pass, "action": "get_movies"}
        if categoria:
            params["category_id"] = categoria
        resp = requests.get(f"{base_prov}/player_api.php", params=params, timeout=15)
        movies = resp.json() if isinstance(resp.json(), list) else []

        # Paginación
        inicio = (pagina - 1) * limite
        fin = inicio + limite
        paginado = movies[inicio:fin]

        return jsonify({
            "total": len(movies),
            "page": pagina,
            "limit": limite,
            "movies": paginado,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/movies/<int:movie_id>", methods=["GET"])
@admin_required
def api_movie_detail(movie_id):
    """Detalle de una película."""
    info = _fetch_vod_info("movie", movie_id)
    if info:
        return jsonify(info)
    return jsonify({"error": "No encontrada"}), 404


@app.route("/api/series/categories", methods=["GET"])
@admin_required
def api_series_categories():
    """Lista categorías de series."""
    cats = _fetch_vod_list("series")
    return jsonify(cats)


@app.route("/api/series", methods=["GET"])
@admin_required
def api_series_list():
    """Lista series con filtro por categoría."""
    categoria = request.args.get("category", "")
    pagina = int(request.args.get("page", 1))
    limite = int(request.args.get("limit", 50))

    base_prov, prov_user, prov_pass = get_proveedor_info()
    if not base_prov:
        return jsonify({"error": "Proveedor no configurado"}), 502

    try:
        params = {"username": prov_user, "password": prov_pass, "action": "get_series"}
        if categoria:
            params["category_id"] = categoria
        resp = requests.get(f"{base_prov}/player_api.php", params=params, timeout=15)
        series = resp.json() if isinstance(resp.json(), list) else []

        inicio = (pagina - 1) * limite
        fin = inicio + limite
        paginado = series[inicio:fin]

        return jsonify({
            "total": len(series),
            "page": pagina,
            "limit": limite,
            "series": paginado,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/series/<int:series_id>", methods=["GET"])
@admin_required
def api_series_detail(series_id):
    """Detalle de una serie con sus temporadas y episodios."""
    info = _fetch_vod_info("series", series_id)
    if info:
        return jsonify(info)
    return jsonify({"error": "No encontrada"}), 404


@app.route("/api/vod/search", methods=["GET"])
@admin_required
def api_vod_search():
    """Busca películas y series por nombre."""
    query = request.args.get("q", "").lower()
    if not query or len(query) < 2:
        return jsonify({"movies": [], "series": []})

    base_prov, prov_user, prov_pass = get_proveedor_info()
    if not base_prov:
        return jsonify({"movies": [], "series": []})

    results = {"movies": [], "series": []}

    try:
        # Buscar películas
        resp = requests.get(
            f"{base_prov}/player_api.php",
            params={"username": prov_user, "password": prov_pass, "action": "get_movies"},
            timeout=15,
        )
        movies = resp.json() if isinstance(resp.json(), list) else []
        results["movies"] = [
            m for m in movies
            if query in m.get("name", "").lower()
        ][:20]

        # Buscar series
        resp = requests.get(
            f"{base_prov}/player_api.php",
            params={"username": prov_user, "password": prov_pass, "action": "get_series"},
            timeout=15,
        )
        series = resp.json() if isinstance(resp.json(), list) else []
        results["series"] = [
            s for s in series
            if query in s.get("name", "").lower()
        ][:20]
    except:
        pass

    return jsonify(results)


# ════════════════════════════════════════════════════════════════
# VOD STREAMING — Películas y Series con relay
# ════════════════════════════════════════════════════════════════

# Cache de relays VOD: { movie_id: { proc, viewers, last_view, ready, path, playlist, url } }
_vod_relays = {}
_vod_relay_lock = threading.Lock()


@app.route("/movie/<usuario>/<contrasena>/<vod_id>")
def movie_stream(usuario, contrasena, vod_id):
    """
    Streaming de películas con relay compartido.
    Múltiples usuarios viendo la misma película = 1 conexión al proveedor.
    """
    mac = request.headers.get("X-MAC-Address", "")
    ip = request.headers.get("X-Real-IP", request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, f"movie_{vod_id}")
    if not ok:
        return jsonify({"error": resultado}), 403

    if user_obj:
        try:
            log = LogAcceso(usuario_id=user_obj.id, canal=f"movie_{vod_id}", ip=ip)
            db.session.add(log)
            db.session.commit()
        except:
            pass

    base_prov, prov_user, prov_pass = get_proveedor_info()
    if not base_prov:
        return jsonify({"error": "Proveedor no configurado"}), 502

    # URL de la película en el proveedor
    url_proveedor = f"{base_prov}/movie/{prov_user}/{prov_pass}/{vod_id}"

    # Verificar si ya hay un relay activo para esta película
    with _vod_relay_lock:
        if vod_id in _vod_relays:
            relay = _vod_relays[vod_id]
            if relay["proc"] and relay["proc"].poll() is None:
                relay["viewers"] += 1
                relay["last_view"] = time.time()
                logger.info(f"Película {vod_id}: +1 viewer (total: {relay['viewers']})")
            else:
                del _vod_relays[vod_id]
                relay = None
        else:
            relay = None

        if not relay:
            # Iniciar nuevo relay FFmpeg para esta película
            vod_hls_dir = os.path.join(HLS_DIR, "movies", str(vod_id))
            os.makedirs(vod_hls_dir, exist_ok=True)
            playlist = os.path.join(vod_hls_dir, "index.m3u8")

            # Limpiar segmentos anteriores
            for f in os.listdir(vod_hls_dir):
                try:
                    os.remove(os.path.join(vod_hls_dir, f))
                except:
                    pass

            cmd = [
                "ffmpeg", "-y",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-user_agent", "VLC/3.0.18 LibVLC/3.0.18",
                "-i", url_proveedor,
                "-c", "copy",
                "-f", "hls",
                "-hls_time", "6",
                "-hls_list_size", "0",  # Mantener todos los segmentos (película completa)
                "-hls_flags", "append_list+delete_segments",
                "-hls_segment_filename", os.path.join(vod_hls_dir, "seg%05d.ts"),
                playlist,
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid,
                )
                _vod_relays[vod_id] = {
                    "proc": proc,
                    "viewers": 1,
                    "last_view": time.time(),
                    "ready": False,
                    "path": vod_hls_dir,
                    "playlist": playlist,
                    "url": url_proveedor,
                }
                logger.info(f"Relay película {vod_id} iniciado (PID {proc.pid})")
            except Exception as e:
                logger.error(f"Error iniciando relay película {vod_id}: {e}")
                return jsonify({"error": "Error al iniciar stream"}), 502

    # Esperar a que el playlist esté listo
    for _ in range(30):
        info = _vod_relays.get(vod_id)
        if info and os.path.exists(info["playlist"]) and os.path.getsize(info["playlist"]) > 0:
            break
        time.sleep(0.5)

    # Servir segmentos HLS
    def generate():
        try:
            while True:
                with _vod_relay_lock:
                    info = _vod_relays.get(vod_id)
                    if not info or (info["proc"] and info["proc"].poll() is not None):
                        break
                    playlist_path = info["playlist"]

                if os.path.exists(playlist_path):
                    with open(playlist_path) as f:
                        content = f.read()
                    segs = [l.strip() for l in content.split("\n") if l.strip().endswith(".ts")]
                    for seg_name in segs:
                        seg_path = os.path.join(info["path"], seg_name)
                        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                            with open(seg_path, "rb") as sf:
                                yield sf.read()
                time.sleep(1)
        except Exception as e:
            logger.error(f"Error streaming película {vod_id}: {e}")
        finally:
            with _vod_relay_lock:
                if vod_id in _vod_relays:
                    _vod_relays[vod_id]["viewers"] = max(0, _vod_relays[vod_id]["viewers"] - 1)

    return Response(generate(),
                    content_type="video/mp2t",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/series/<usuario>/<contrasena>/<episode_id>")
def series_stream(usuario, contrasena, episode_id):
    """
    Streaming de episodios de series con relay compartido.
    Misma lógica que películas.
    """
    mac = request.headers.get("X-MAC-Address", "")
    ip = request.headers.get("X-Real-IP", request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, f"series_{episode_id}")
    if not ok:
        return jsonify({"error": resultado}), 403

    if user_obj:
        try:
            log = LogAcceso(usuario_id=user_obj.id, canal=f"series_{episode_id}", ip=ip)
            db.session.add(log)
            db.session.commit()
        except:
            pass

    base_prov, prov_user, prov_pass = get_proveedor_info()
    if not base_prov:
        return jsonify({"error": "Proveedor no configurado"}), 502

    url_proveedor = f"{base_prov}/series/{prov_user}/{prov_pass}/{episode_id}"

    # Reutilizar la misma lógica de relay que películas
    with _vod_relay_lock:
        if episode_id in _vod_relays:
            relay = _vod_relays[episode_id]
            if relay["proc"] and relay["proc"].poll() is None:
                relay["viewers"] += 1
                relay["last_view"] = time.time()
            else:
                del _vod_relays[episode_id]
                relay = None
        else:
            relay = None

        if not relay:
            vod_hls_dir = os.path.join(HLS_DIR, "series", str(episode_id))
            os.makedirs(vod_hls_dir, exist_ok=True)
            playlist = os.path.join(vod_hls_dir, "index.m3u8")

            for f in os.listdir(vod_hls_dir):
                try:
                    os.remove(os.path.join(vod_hls_dir, f))
                except:
                    pass

            cmd = [
                "ffmpeg", "-y",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-user_agent", "VLC/3.0.18 LibVLC/3.0.18",
                "-i", url_proveedor,
                "-c", "copy",
                "-f", "hls",
                "-hls_time", "6",
                "-hls_list_size", "0",
                "-hls_flags", "append_list+delete_segments",
                "-hls_segment_filename", os.path.join(vod_hls_dir, "seg%05d.ts"),
                playlist,
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid,
                )
                _vod_relays[episode_id] = {
                    "proc": proc,
                    "viewers": 1,
                    "last_view": time.time(),
                    "ready": False,
                    "path": vod_hls_dir,
                    "playlist": playlist,
                    "url": url_proveedor,
                }
            except Exception as e:
                logger.error(f"Error iniciando relay serie {episode_id}: {e}")
                return jsonify({"error": "Error al iniciar stream"}), 502

    for _ in range(30):
        info = _vod_relays.get(episode_id)
        if info and os.path.exists(info["playlist"]) and os.path.getsize(info["playlist"]) > 0:
            break
        time.sleep(0.5)

    def generate():
        try:
            while True:
                with _vod_relay_lock:
                    info = _vod_relays.get(episode_id)
                    if not info or (info["proc"] and info["proc"].poll() is not None):
                        break
                    playlist_path = info["playlist"]

                if os.path.exists(playlist_path):
                    with open(playlist_path) as f:
                        content = f.read()
                    segs = [l.strip() for l in content.split("\n") if l.strip().endswith(".ts")]
                    for seg_name in segs:
                        seg_path = os.path.join(info["path"], seg_name)
                        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                            with open(seg_path, "rb") as sf:
                                yield sf.read()
                time.sleep(1)
        except Exception as e:
            logger.error(f"Error streaming serie {episode_id}: {e}")
        finally:
            with _vod_relay_lock:
                if episode_id in _vod_relays:
                    _vod_relays[episode_id]["viewers"] = max(0, _vod_relays[episode_id]["viewers"] - 1)

    return Response(generate(),
                    content_type="video/mp2t",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


