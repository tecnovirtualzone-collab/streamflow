"""
StreamFlow v4.0 - Channel Manager
===================================
Sistema de gestión de canales lógicos y fuentes.

Concepto:
- Canal Lógico: lo que ve el usuario (ej: "RCN")
- Canal Fuente: la URL real en un proveedor (ej: http://prov.com/live/123.ts)

Cuando el admin cambia de proveedor:
1. Fuentes del proveedor anterior → inactivas
2. Carga canales del nuevo proveedor
3. Vincula automáticamente por nombre a canales lógicos existentes
4. Canales sin fuente premium → usan backup gratuito

Auto-reemplazo cuando un canal cae:
1. Detecta caída (timeout, error)
2. Busca otra fuente activa para el mismo canal lógico
3. Si no hay, busca por nombre similar en fuentes gratuitas
4. Reemplaza automáticamente, el usuario no nota
"""

import os
import re
import time
import json
import logging
import hashlib
import threading
import subprocess
import requests
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger("streamflow.channels")


# ════════════════════════════════════════════════════════════════
# FUNCIONES DE MATCHING
# ════════════════════════════════════════════════════════════════

def _normalize_name(name):
    """Normaliza un nombre de canal para comparación."""
    if not name:
        return ""
    # Minúsculas, sin espacios extra, sin caracteres especiales
    name = name.lower().strip()
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name)
    return name


def _name_similarity(name1, name2):
    """Calcula similitud entre dos nombres (0.0 a 1.0)."""
    n1 = _normalize_name(name1)
    n2 = _normalize_name(name2)

    if n1 == n2:
        return 1.0
    if n1 in n2 or n2 in n1:
        return 0.9

    # Palabras en común
    words1 = set(n1.split())
    words2 = set(n2.split())
    if not words1 or not words2:
        return 0.0

    common = words1 & words2
    total = words1 | words2
    return len(common) / len(total)


def _find_best_match(nombre, candidatos, threshold=0.7):
    """Busca el mejor match para un nombre entre una lista de candidatos."""
    best_match = None
    best_score = 0

    for candidato in candidatos:
        score = _name_similarity(nombre, candidato.get("nombre", "") or candidato.get("display_name", ""))
        if score > best_score and score >= threshold:
            best_score = score
            best_match = candidato

    return best_match, best_score


# ════════════════════════════════════════════════════════════════
# CARGA DE CANALES DESDE M3U
# ════════════════════════════════════════════════════════════════

def parse_m3u(content):
    """Parsea contenido M3U y retorna lista de canales."""
    channels = []
    current = {}
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#EXTINF:"):
            current = {"info": line}
            name_match = re.search(r'tvg-name="([^"]*)"', line)
            id_match = re.search(r'tvg-id="([^"]*)"', line)
            group_match = re.search(r'group-title="([^"]*)"', line)
            logo_match = re.search(r'tvg-logo="([^"]*)"', line)
            current["name"] = name_match.group(1) if name_match else ""
            current["tvg_id"] = id_match.group(1) if id_match else ""
            current["group"] = group_match.group(1) if group_match else ""
            current["logo"] = logo_match.group(1) if logo_match else ""
            if "," in line:
                current["display_name"] = line.rsplit(",", 1)[-1].strip()
            else:
                current["display_name"] = current["name"]
        elif line.startswith("http") and current:
            current["url"] = line
            channels.append(current)
            current = {}
    return channels


def generate_channel_id(name, group=""):
    """Genera un ID único para un canal lógico."""
    normalized = _normalize_name(name)
    # Quitar palabras comunes que no ayudan a identificar
    skip_words = {"tv", "televisión", "television", "canal", "channel", "hd", "sd", "4k", "fhd"}
    words = [w for w in normalized.split() if w not in skip_words and len(w) > 1]
    base = "_".join(words[:4])  # máximo 4 palabras
    if not base:
        base = hashlib.md5(name.encode()).hexdigest()[:8]
    return base


# ════════════════════════════════════════════════════════════════
# GESTIÓN DE PROVEEDORES
# ════════════════════════════════════════════════════════════════

class ChannelManager:
    """
    Gestiona canales lógicos y sus fuentes.
    Se encarga de:
    - Cargar canales desde proveedores
    - Cambiar de proveedor (desactiva anterior, carga nuevo)
    - Auto-reemplazar canales caídos
    - Resolver la mejor fuente para un canal
    """

    def __init__(self, db, app):
        self.db = db
        self.app = app
        self._loading = False
        self._load_lock = threading.Lock()

    # ── Carga desde proveedor ──

    def load_from_proveedor(self, proveedor_nombre, m3u_content, vincular_automaticamente=True):
        """
        Carga canales desde un proveedor (M3U).
        Si vincular_automaticamente=True, intenta vincular con canales lógicos existentes.
        Retorna: (canales_nuevos, canales_vinculados, total)
        """
        from app import CanalLogico, CanalFuente

        channels = parse_m3u(m3u_content)
        nuevos = 0
        vinculados = 0

        with self.app.app_context():
            # Obtener todos los canales lógicos existentes
            canales_existentes = CanalLogico.query.all()
            existentes_por_id = {c.canal_id: c for c in canales_existentes}
            existentes_por_nombre = {_normalize_name(c.nombre): c for c in canales_existentes if c.nombre}

            for ch in channels:
                nombre = ch.get("name", "") or ch.get("display_name", "")
                url = ch.get("url", "")
                grupo = ch.get("group", "")
                logo = ch.get("logo", "")

                if not nombre or not url:
                    continue

                canal_id = generate_channel_id(nombre, grupo)
                nombre_norm = _normalize_name(nombre)

                # Buscar si ya existe un canal lógico para este
                canal_logico = None

                if canal_id in existentes_por_id:
                    canal_logico = existentes_por_id[canal_id]
                else:
                    # Buscar por nombre similar
                    for norm_name, cl in existentes_por_nombre.items():
                        if _name_similarity(nombre, cl.nombre) >= 0.8:
                            canal_logico = cl
                            break

                if canal_logico:
                    # Verificar si ya tiene una fuente de este proveedor
                    fuente_existente = CanalFuente.query.filter_by(
                        canal_logico_id=canal_logico.id,
                        proveedor=proveedor_nombre
                    ).first()

                    if fuente_existente:
                        # Actualizar URL
                        fuente_existente.url = url
                        fuente_existente.activo = True
                        fuente_existente.estado = "ok"
                        fuente_existente.actualizado = datetime.utcnow()
                    else:
                        # Crear nueva fuente
                        fuente = CanalFuente(
                            canal_logico_id=canal_logico.id,
                            proveedor=proveedor_nombre,
                            url=url,
                            nombre_fuente=nombre,
                            prioridad=1,  # premium
                            activo=True,
                            estado="ok",
                        )
                        self.db.session.add(fuente)

                    # Actualizar datos del canal lógico si están vacíos
                    if not canal_logico.logo and logo:
                        canal_logico.logo = logo
                    if not canal_logico.grupo and grupo:
                        canal_logico.grupo = grupo

                    vinculados += 1
                else:
                    # Crear nuevo canal lógico
                    canal_logico = CanalLogico(
                        canal_id=canal_id,
                        nombre=nombre,
                        display_name=ch.get("display_name", nombre),
                        grupo=grupo,
                        logo=logo,
                        activo=True,
                    )
                    self.db.session.add(canal_logico)
                    self.db.session.flush()  # para obtener el ID

                    # Crear fuente
                    fuente = CanalFuente(
                        canal_logico_id=canal_logico.id,
                        proveedor=proveedor_nombre,
                        url=url,
                        nombre_fuente=nombre,
                        prioridad=1,
                        activo=True,
                        estado="ok",
                    )
                    self.db.session.add(fuente)

                    # Agregar a los existentes para no duplicar
                    existentes_por_id[canal_id] = canal_logico
                    existentes_por_nombre[nombre_norm] = canal_logico

                    nuevos += 1

            self.db.session.commit()

        logger.info(f"Proveedor '{proveedor_nombre}': {nuevos} nuevos, {vinculados} vinculados, {len(channels)} total")
        return nuevos, vinculados, len(channels)

    # ── Cambio de proveedor ──

    def cambiar_proveedor(self, proveedor_anterior, proveedor_nuevo, m3u_content):
        """
        Cambia de proveedor:
        1. Desactiva TODAS las fuentes del proveedor anterior
        2. Carga los canales del nuevo proveedor
        3. Vincula automáticamente con canales lógicos existentes
        4. Los canales que no se pudieron vincular quedan sin fuente premium

        Retorna: (fuentes_desactivadas, canales_nuevos, canales_vinculados, canales_sin_fuente)
        """
        from app import CanalLogico, CanalFuente

        with self._load_lock:
            if self._loading:
                return 0, 0, 0, 0
            self._loading = True

        try:
            with self.app.app_context():
                # 1. Desactivar fuentes del proveedor anterior
                fuentes_anteriores = CanalFuente.query.filter_by(
                    proveedor=proveedor_anterior, activo=True
                ).all()
                desactivadas = 0
                for f in fuentes_anteriores:
                    f.activo = False
                    f.estado = "desactivado"
                    f.actualizado = datetime.utcnow()
                    desactivadas += 1

                self.db.session.commit()
                logger.info(f"Proveedor anterior '{proveedor_anterior}': {desactivadas} fuentes desactivadas")

                # 2. Cargar canales del nuevo proveedor
                nuevos, vinculados, total = self.load_from_proveedor(
                    proveedor_nuevo, m3u_content, vincular_automaticamente=True
                )

                # 3. Contar canales sin fuente premium activa
                canales_sin_fuente = CanalLogico.query.filter(
                    CanalLogico.activo == True,
                    ~CanalLogico.fuentes.any(
                        CanalFuente.activo == True,
                        CanalFuente.proveedor != "gratuito"
                    )
                ).count()

                logger.info(f"Cambio de proveedor completado: {desactivadas} desactivadas, {nuevos} nuevos, {vinculados} vinculados, {canales_sin_fuente} sin fuente premium")

                return desactivadas, nuevos, vinculados, canales_sin_fuente

        finally:
            self._loading = False

    # ── Carga desde listas gratuitas ──

    def load_from_free_lists(self, urls):
        """
        Carga canales desde listas M3U gratuitas.
        Los marca como proveedor="gratuito" y prioridad=3 (backup).
        """
        from app import CanalLogico, CanalFuente

        total_nuevos = 0
        total_vinculados = 0

        for url in urls:
            url = url.strip()
            if not url:
                continue

            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code != 200:
                    continue

                nuevos, vinculados, _ = self.load_from_proveedor("gratuito", resp.text)
                total_nuevos += nuevos
                total_vinculados += vinculados

                # Marcar todas las fuentes gratuitas como prioridad 3 (backup)
                with self.app.app_context():
                    fuentes_gratis = CanalFuente.query.filter_by(
                        proveedor="gratuito", prioridad=1
                    ).all()
                    for f in fuentes_gratis:
                        f.prioridad = 3
                    self.db.session.commit()

            except Exception as e:
                logger.error(f"Error cargando lista gratuita {url[:50]}: {e}")

        logger.info(f"Listas gratuitas: {total_nuevos} nuevos, {total_vinculados} vinculados")
        return total_nuevos, total_vinculados

    # ── Resolución de canales ──

    def resolve_channel(self, canal_id):
        """
        Resuelve la mejor fuente activa para un canal.
        Retorna: { canal_logico, fuente, url } o None
        """
        from app import CanalLogico, CanalFuente

        with self.app.app_context():
            canal = CanalLogico.query.filter_by(canal_id=canal_id, activo=True).first()
            if not canal:
                return None

            fuente = canal.get_fuente_activa()
            if not fuente:
                return None

            return {
                "canal_logico": canal,
                "fuente": fuente,
                "url": fuente.url,
            }

    def resolve_all_channels(self):
        """Retorna todos los canales lógicos con su fuente activa."""
        from app import CanalLogico

        with self.app.app_context():
            canales = CanalLogico.query.filter_by(activo=True).order_by(CanalLogico.prioridad).all()
            result = []
            for canal in canales:
                fuente = canal.get_fuente_activa()
                result.append({
                    "canal": canal.to_dict(),
                    "fuente_url": fuente.url if fuente else None,
                    "fuente_proveedor": fuente.proveedor if fuente else None,
                    "fuente_estado": fuente.estado if fuente else "sin_fuente",
                })
            return result

    # ── Auto-reemplazo ──

    def check_and_replace(self, canal_id):
        """
        Verifica si un canal está funcionando.
        Si está caído, busca un reemplazo automáticamente.
        Retorna: (estado, mensaje)
        """
        from app import CanalLogico, CanalFuente

        with self.app.app_context():
            canal = CanalLogico.query.filter_by(canal_id=canal_id).first()
            if not canal:
                return "error", "Canal no encontrado"

            fuente = canal.get_fuente_activa()
            if not fuente:
                # Buscar cualquier fuente disponible (incluyendo gratuita)
                todas = canal.get_todas_fuentes_activas()
                if todas:
                    fuente = todas[0]
                    fuente.prioridad = 1  # promover a principal
                    self.db.session.commit()
                    return "reemplazado", f"Usando fuente de respaldo: {fuente.proveedor}"
                return "sin_fuente", "No hay fuentes disponibles"

            # Verificar si la URL responde
            try:
                r = requests.head(fuente.url, timeout=5, allow_redirects=True,
                                  headers={"User-Agent": "VLC/3.0.18"})
                if r.status_code == 200:
                    fuente.estado = "ok"
                    fuente.ultimo_check = datetime.utcnow()
                    fuente.ultimo_error = ""
                    self.db.session.commit()
                    return "ok", "Funcionando"
                else:
                    raise Exception(f"HTTP {r.status_code}")
            except Exception as e:
                # Marcar como caído
                fuente.estado = "caido"
                fuente.ultimo_check = datetime.utcnow()
                fuente.ultimo_error = str(e)[:500]
                self.db.session.commit()

                # Buscar reemplazo
                otras_fuentes = CanalFuente.query.filter(
                    CanalFuente.canal_logico_id == canal.id,
                    CanalFuente.id != fuente.id,
                    CanalFuente.activo == True
                ).order_by(CanalFuente.prioridad).all()

                for reemplazo in otras_fuentes:
                    try:
                        r = requests.head(reemplazo.url, timeout=5, allow_redirects=True,
                                          headers={"User-Agent": "VLC/3.0.18"})
                        if r.status_code == 200:
                            # Encontró reemplazo
                            reemplazo.prioridad = 1
                            reemplazo.estado = "ok"
                            reemplazo.ultimo_check = datetime.utcnow()
                            self.db.session.commit()

                            logger.info(f"Canal {canal_id}: reemplazado de {fuente.proveedor} a {reemplazo.proveedor}")
                            return "reemplazado", f"Reemplazado: {fuente.proveedor} → {reemplazo.proveedor}"
                    except:
                        continue

                return "caido", f"Caído, sin reemplazo disponible"

    def auto_replace_all(self):
        """
        Verifica todos los canales y reemplaza los caídos.
        Se ejecuta periódicamente.
        """
        from app import CanalLogico

        with self.app.app_context():
            canales = CanalLogico.query.filter_by(activo=True).all()
            results = {"ok": 0, "reemplazado": 0, "caido": 0, "sin_fuente": 0}

            for canal in canales:
                estado, msg = self.check_and_replace(canal.canal_id)
                results[estado] = results.get(estado, 0) + 1

            logger.info(f"Auto-reemplazo: {results}")
            return results

    # ── Listas por plan ──

    def get_channels_for_plan(self, plan_nombre):
        """Retorna los canales asignados a un plan."""
        from app import CanalLogico, Paquete

        with self.app.app_context():
            paquete = Paquete.query.filter_by(nombre=plan_nombre).first()
            if not paquete or not paquete.categorias:
                return []

            categorias = [c.strip().upper() for c in paquete.categorias.split(",") if c.strip()]

            canales = CanalLogico.query.filter(
                CanalLogico.activo == True,
            ).order_by(CanalLogico.prioridad, CanalLogico.nombre).all()

            # Filtrar por categorías del plan
            result = []
            for canal in canales:
                canal_grupo = (canal.grupo or "").upper()
                if any(cat in canal_grupo or canal_grupo in cat for cat in categorias):
                    result.append(canal.to_dict())

            return result

    def get_stats(self):
        """Estadísticas del sistema de canales."""
        from app import CanalLogico, CanalFuente

        with self.app.app_context():
            total_canales = CanalLogico.query.filter_by(activo=True).count()
            total_fuentes = CanalFuente.query.filter_by(activo=True).count()
            fuentes_premium = CanalFuente.query.filter(
                CanalFuente.activo == True,
                CanalFuente.proveedor != "gratuito"
            ).count()
            fuentes_gratis = CanalFuente.query.filter_by(
                proveedor="gratuito", activo=True
            ).count()
            canales_caidos = CanalFuente.query.filter_by(estado="caido", activo=True).count()
            proveedores = db.session.query(
                CanalFuente.proveedor, db.func.count(CanalFuente.id)
            ).filter_by(activo=True).group_by(CanalFuente.proveedor).all()

            return {
                "total_canales": total_canales,
                "total_fuentes": total_fuentes,
                "fuentes_premium": fuentes_premium,
                "fuentes_gratis": fuentes_gratis,
                "canales_caidos": canales_caidos,
                "proveedores": {p: c for p, c in proveedores},
            }


# Instancia global (se inicializa en app.py)
channel_manager = None


def init_channel_manager(db, app):
    """Inicializa el Channel Manager."""
    global channel_manager
    channel_manager = ChannelManager(db, app)
    return channel_manager
