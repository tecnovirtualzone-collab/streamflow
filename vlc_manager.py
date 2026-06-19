"""
StreamFlow v3.0 - VLC Relay Manager
====================================
Gestiona procesos VLC para hacer relay de canales IPTV.
- 1 conexión al proveedor por canal (sin importar cuántos usuarios)
- Auto-start cuando el primer usuario pide un canal
- Auto-stop cuando el último usuario se va
- Health checks y auto-restart
"""

import os
import re
import time
import signal
import shutil
import hashlib
import logging
import subprocess
import threading
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict

logger = logging.getLogger("streamflow.vlc")

# ════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ════════════════════════════════════════════════════════════════

VLC_HTTP_PORT = int(os.environ.get("VLC_HTTP_PORT", "8888"))
VLC_HTTP_USER = os.environ.get("VLC_HTTP_USER", "streamflow")
VLC_HTTP_PASS = os.environ.get("VLC_HTTP_PASS", "sf_vlc_2026")
VLC_CACHE_MS = int(os.environ.get("VLC_CACHE_MS", "1000"))  # 1 segundo de buffer
VLC_TIMEOUT = int(os.environ.get("VLC_TIMEOUT", "60"))  # segundos sin viewers para apagar
VLC_MAX_CHANNELS = int(os.environ.get("VLC_MAX_CHANNELS", "8"))  # máximo canales simultáneos
VLC_RECONNECT_DELAY = int(os.environ.get("VLC_RECONNECT_DELAY", "10"))  # segundos entre reconexiones
VLC_MAX_RECONNECTS = int(os.environ.get("VLC_MAX_RECONNECTS", "3"))  # max reconexiones por minuto

# User-Agents para rotar (simula diferentes dispositivos)
USER_AGENTS = [
    "VLC/3.0.18 LibVLC/3.0.18",
    "VLC/3.0.16 LibVLC/3.0.16",
    "VLC/3.0.14 LibVLC/3.0.14",
    "VLC/3.0.12 LibVLC/3.0.12",
    "VLC/3.0.11 LibVLC/3.0.11",
]


class VLCRelay:
    """Representa un relay VLC para un canal específico."""
    
    def __init__(self, canal_id: str, url_proveedor: str, user_agent: str):
        self.canal_id = canal_id
        self.url_proveedor = url_proveedor
        self.user_agent = user_agent
        self.proc: Optional[subprocess.Popen] = None
        self.viewers: int = 0
        self.last_view: float = time.time()
        self.start_time: float = time.time()
        self.ready: bool = False
        self.reconnect_count: int = 0
        self.last_reconnect: float = 0
        self.local_url: str = f"http://127.0.0.1:{VLC_HTTP_PORT}/{canal_id}.ts"
        self.ready_event = threading.Event()
        self._lock = threading.Lock()
    
    def start(self) -> bool:
        """Inicia el proceso VLC para este canal."""
        if self.proc and self.proc.poll() is None:
            logger.info(f"VLC canal {self.canal_id} ya está corriendo")
            return True
        
        # Comando VLC optimizado para mínimo CPU
        cmd = [
            "vlc",
            "--intf", "dummy",                    # Sin interfaz gráfica
            "--no-video-title-show",              # Sin overlay
            "--no-sout-audio",                    # No re-codificar audio
            "--sout-keep",                        # Mantener salida viva
            f"--live-caching={VLC_CACHE_MS}",     # Buffer de 1s
            f"--network-caching={VLC_CACHE_MS}",  # Buffer de red 1s
            "--file-caching=500",                 # Cache de archivo
            "--clock-jitter=0",                   # Sin jitter
            # Transcodificación ultra-rápida
            "--sout-x264-preset=ultrafast",
            "--sout-x264-tune=zerolatency",
            "--sout-x264-profile=baseline",
            "--sout-x264-level=3.0",
            # Salida HTTP
            "--sout", f"#standard{{access=http,mux=ts,dst=:{VLC_HTTP_PORT}/{self.canal_id}.ts}}",
            # Headers para parecer un player real
            f"--http-user={VLC_HTTP_USER}",
            f"--http-password={VLC_HTTP_PASS}",
            "--http-host=127.0.0.1",
            # User-Agent
            f"--http-user-agent={self.user_agent}",
            # URL del proveedor
            self.url_proveedor,
            "vlc://quit",  # Salir cuando termine el stream
        ]
        
        try:
            logger.info(f"Iniciando VLC para canal {self.canal_id}")
            logger.info(f"  URL proveedor: {self.url_proveedor}")
            logger.info(f"  URL local: {self.local_url}")
            
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,  # Capturar stderr para debug
                preexec_fn=os.setsid,
                close_fds=True,
            )
            
            self.start_time = time.time()
            self.ready = False
            self.ready_event.clear()
            
            # Hilo para esperar que VLC esté listo
            t = threading.Thread(target=self._wait_ready, daemon=True)
            t.start()
            
            return True
            
        except Exception as e:
            logger.error(f"Error iniciando VLC canal {self.canal_id}: {e}")
            return False
    
    def _wait_ready(self):
        """Espera a que VLC genere el primer segmento HTTP."""
        for i in range(30):  # 15 segundos max
            if self.proc and self.proc.poll() is not None:
                # VLC se cerró
                stderr = self.proc.stderr.read().decode("utf-8", errors="ignore")[:500]
                logger.error(f"VLC canal {self.canal_id} se cerró. Stderr: {stderr}")
                return
            
            # Verificar si el puerto HTTP responde
            try:
                r = requests.get(
                    f"http://127.0.0.1:{VLC_HTTP_PORT}/{self.canal_id}.ts",
                    auth=(VLC_HTTP_USER, VLC_HTTP_PASS),
                    timeout=2,
                    stream=True,
                )
                if r.status_code == 200:
                    # Leer algunos bytes para confirmar
                    chunk = r.content[:1024]
                    if len(chunk) > 0:
                        self.ready = True
                        self.ready_event.set()
                        logger.info(f"VLC canal {self.canal_id} listo en {i*0.5:.1f}s")
                        r.close()
                        return
                    r.close()
            except:
                pass
            
            time.sleep(0.5)
        
        logger.warning(f"VLC canal {self.canal_id} no se puso listo en 15s")
    
    def stop(self):
        """Detiene el proceso VLC."""
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=5)
            except:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except:
                    pass
            self.proc = None
            self.ready = False
            self.ready_event.clear()
            logger.info(f"VLC canal {self.canal_id} detenido")
    
    def add_viewer(self):
        """Incrementa el contador de viewers."""
        with self._lock:
            self.viewers += 1
            self.last_view = time.time()
    
    def remove_viewer(self):
        """Decrementa el contador de viewers."""
        with self._lock:
            self.viewers = max(0, self.viewers - 1)
            self.last_view = time.time()
    
    def is_alive(self) -> bool:
        """Verifica si el proceso VLC está vivo."""
        return self.proc is not None and self.proc.poll() is None
    
    def can_reconnect(self) -> bool:
        """Verifica si puede reconectar (rate limiting)."""
        now = time.time()
        if now - self.last_reconnect > 60:
            self.reconnect_count = 0
        return self.reconnect_count < VLC_MAX_RECONNECTS
    
    def reconnect(self) -> bool:
        """Reintenta la conexión al proveedor."""
        if not self.can_reconnect():
            logger.warning(f"VLC canal {self.canal_id}: demasiadas reconexiones")
            return False
        
        self.reconnect_count += 1
        self.last_reconnect = time.time()
        
        delay = VLC_RECONNECT_DELAY * self.reconnect_count
        logger.info(f"VLC canal {self.canal_id}: reconectando en {delay}s (intento {self.reconnect_count})")
        time.sleep(delay)
        
        return self.start()
    
    def get_stats(self) -> dict:
        """Retorna estadísticas del relay."""
        return {
            "canal_id": self.canal_id,
            "viewers": self.viewers,
            "ready": self.ready,
            "alive": self.is_alive(),
            "uptime": int(time.time() - self.start_time),
            "local_url": self.local_url,
            "reconnect_count": self.reconnect_count,
        }


class VLCRelayManager:
    """
    Gestiona todos los relays VLC del sistema.
    - Inicia VLC cuando el primer usuario pide un canal
    - Comparte el stream entre todos los usuarios de ese canal
    - Apaga VLC cuando el último usuario se va (timeout)
    - Monitorea health de cada relay
    - Limita el número de canales simultáneos
    """
    
    def __init__(self):
        self._relays: Dict[str, VLCRelay] = {}
        self._lock = threading.Lock()
        self._ua_index = 0  # Para rotar User-Agents
        
        # Iniciar hilo de limpieza
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        
        # Iniciar hilo de health checks
        self._health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self._health_thread.start()
        
        logger.info(f"VLC Relay Manager iniciado (max {VLC_MAX_CHANNELS} canales)")
    
    def _get_next_ua(self) -> str:
        """Rota entre User-Agents."""
        ua = USER_AGENTS[self._ua_index % len(USER_AGENTS)]
        self._ua_index += 1
        return ua
    
    def _get_proveedor_url(self, canal_id: str) -> str:
        """Obtiene la URL del proveedor para un canal desde la lista M3U."""
        # Esto se integra con el sistema de cache M3U de StreamFlow
        from app import get_m3u_list, get_proveedor_info
        
        m3u_content = get_m3u_list()
        if not m3u_content:
            return ""
        
        # Buscar el canal en la lista M3U
        lines = m3u_content.split("\n")
        for i, line in enumerate(lines):
            if f'tvg-name="{canal_id}"' in line or canal_id.lower() in line.lower():
                # La siguiente línea con http es la URL
                for j in range(i + 1, min(i + 3, len(lines))):
                    if lines[j].startswith("http"):
                        return lines[j].strip()
        
        # Fallback: construir URL Xtream Codes
        base, user, pwd = get_proveedor_info()
        if base:
            return f"{base}/live/{user}/{pwd}/{canal_id}.ts"
        
        return ""
    
    def start_relay(self, canal_id: str, url_proveedor: str = None) -> Optional[str]:
        """
        Inicia un relay VLC para un canal.
        Retorna la URL local del stream o None si falla.
        """
        with self._lock:
            # Verificar si ya existe
            if canal_id in self._relays:
                relay = self._relays[canal_id]
                if relay.is_alive():
                    return relay.local_url
                else:
                    # Relay muerto, eliminar
                    del self._relays[canal_id]
            
            # Verificar límite de canales
            self._cleanup_dead_relays()
            if len(self._relays) >= VLC_MAX_CHANNELS:
                # Apagar el canal más viejo sin viewers
                self._evict_oldest()
            
            # Obtener URL del proveedor si no se proporcionó
            if not url_proveedor:
                url_proveedor = self._get_proveedor_url(canal_id)
            
            if not url_proveedor:
                logger.error(f"No se encontró URL del proveedor para canal {canal_id}")
                return None
            
            # Crear e iniciar relay
            relay = VLCRelay(
                canal_id=canal_id,
                url_proveedor=url_proveedor,
                user_agent=self._get_next_ua(),
            )
            
            if relay.start():
                self._relays[canal_id] = relay
                return relay.local_url
            
            return None
    
    def stop_relay(self, canal_id: str):
        """Detiene un relay VLC."""
        with self._lock:
            if canal_id in self._relays:
                self._relays[canal_id].stop()
                del self._relays[canal_id]
    
    def get_relay(self, canal_id: str) -> Optional[VLCRelay]:
        """Obtiene un relay activo."""
        return self._relays.get(canal_id)
    
    def add_viewer(self, canal_id: str):
        """Agrega un viewer a un canal."""
        relay = self._relays.get(canal_id)
        if relay:
            relay.add_viewer()
    
    def remove_viewer(self, canal_id: str):
        """Remueve un viewer de un canal."""
        relay = self._relays.get(canal_id)
        if relay:
            relay.remove_viewer()
    
    def get_local_url(self, canal_id: str) -> Optional[str]:
        """Obtiene la URL local de un canal."""
        relay = self._relays.get(canal_id)
        if relay and relay.ready:
            return relay.local_url
        return None
    
    def get_all_stats(self) -> list:
        """Retorna estadísticas de todos los relays activos."""
        return [relay.get_stats() for relay in self._relays.values()]
    
    def _cleanup_dead_relays(self):
        """Elimina relays muertos."""
        dead = [cid for cid, r in self._relays.items() if not r.is_alive()]
        for cid in dead:
            logger.info(f"Eliminando relay muerto: {cid}")
            del self._relays[cid]
    
    def _evict_oldest(self):
        """Desaloja el canal más viejo sin viewers para hacer espacio."""
        # Primero buscar canales sin viewers
        candidates = [
            (cid, r) for cid, r in self._relays.items()
            if r.viewers == 0
        ]
        
        if candidates:
            # Apagar el más viejo sin viewers
            oldest = min(candidates, key=lambda x: x[1].start_time)
            logger.info(f"Desalojando canal {oldest[0]} (sin viewers)")
            oldest[1].stop()
            del self._relays[oldest[0]]
        else:
            # Todos tienen viewers, apagar el más viejo
            oldest = min(self._relays.items(), key=lambda x: x[1].start_time)
            logger.info(f"Desalojando canal {oldest[0]} (todos tienen viewers)")
            oldest[1].stop()
            del self._relays[oldest[0]]
    
    def _cleanup_loop(self):
        """Hilo que apaga relays sin viewers después del timeout."""
        while True:
            time.sleep(15)
            with self._lock:
                to_remove = []
                for cid, relay in self._relays.items():
                    if relay.viewers == 0 and (time.time() - relay.last_view) > VLC_TIMEOUT:
                        to_remove.append(cid)
                
                for cid in to_remove:
                    logger.info(f"Apagando relay {cid} (sin viewers por {VLC_TIMEOUT}s)")
                    self._relays[cid].stop()
                    del self._relays[cid]
    
    def _health_loop(self):
        """Hilo que verifica la salud de los relays y reinicia si es necesario."""
        while True:
            time.sleep(30)
            with self._lock:
                for cid, relay in list(self._relays.items()):
                    if not relay.is_alive() and relay.viewers > 0:
                        # Relay caído pero hay viewers, intentar reconectar
                        logger.warning(f"Relay {cid} caído con {relay.viewers} viewers, reconectando...")
                        relay.reconnect()


# Instancia global
vlc_manager = VLCRelayManager()
