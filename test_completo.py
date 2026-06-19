#!/usr/bin/env python3
"""
Prueba completa del sistema de canales lógicos
"""
import os
import sys

os.environ['ADMIN_PASSWORD'] = 'admin123'
os.environ['SECRET_KEY'] = 'test'
os.environ['JWT_SECRET'] = 'test'
os.environ['DATABASE_URL'] = 'sqlite:///test_panel.db'
os.environ['URL_M3U'] = ''

from app import app, db
from channel_manager import ChannelManager

print("=" * 70)
print("PRUEBA COMPLETA: Sistema de Canales Lógicos")
print("=" * 70)

cm = ChannelManager(db, app)

# ═══════════════════════════════════════════════════
# Paso 1: Cargar proveedor 1
# ═══════════════════════════════════════════════════
print("\n[1/6] Cargando Proveedor 1...")
with app.app_context():
    with open("/tmp/test_proveedor1.m3u") as f:
        content = f.read()
    nuevos, vinculados, total = cm.load_from_proveedor("proveedor1", content)
    print(f"  ✓ Nuevos: {nuevos}, Vinculados: {vinculados}, Total: {total}")

# Verificar
with app.app_context():
    from app import CanalLogico, CanalFuente
    print(f"  Canales lógicos: {CanalLogico.query.count()}")
    print(f"  Fuentes: {CanalFuente.query.count()}")
    print(f"  Fuentes proveedor1: {CanalFuente.query.filter_by(proveedor='proveedor1').count()}")

    print("\n  Canales cargados:")
    for ch in CanalLogico.query.order_by(CanalLogico.nombre).all():
        fuente = ch.get_fuente_activa()
        print(f"    📺 {ch.display_name or ch.nombre} [{ch.grupo}] → {fuente.proveedor if fuente else 'N/A'}")

# ═══════════════════════════════════════════════════
# Paso 2: Cargar lista gratuita (backup)
# ═══════════════════════════════════════════════════
print("\n[2/6] Cargando lista gratuita (backup)...")
with app.app_context():
    # Simular una lista gratuita con algunos canales que ya existen
    free_content = """#EXTM3U
#EXTINF:-1 tvg-id="RCN.co" tvg-logo="" group-title="Colombia",RCN Televisión
https://gratis.example.com/rcn.m3u8
#EXTINF:-1 tvg-id="Caracol.co" tvg-logo="" group-title="Colombia",Caracol TV
https://gratis.example.com/caracol.m3u8
#EXTINF:-1 tvg-id="Extra.co" tvg-logo="" group-title="Variados",Canal Extra Gratuito
https://gratis.example.com/extra.m3u8
"""
    nuevos, vinculados = cm.load_from_free_lists([])  # No usar data URLs
    # Cargamos directamente como proveedor "gratuito"
    free_content = """#EXTM3U
#EXTINF:-1 tvg-id="RCN.co" tvg-logo="" group-title="Colombia",RCN Televisión
https://gratis.example.com/rcn.m3u8
#EXTINF:-1 tvg-id="Caracol.co" tvg-logo="" group-title="Colombia",Caracol TV
https://gratis.example.com/caracol.m3u8
#EXTINF:-1 tvg-id="Extra.co" tvg-logo="" group-title="Variados",Canal Extra Gratuito
https://gratis.example.com/extra.m3u8
"""
    nuevos, vinculados, total = cm.load_from_proveedor("gratuito", free_content)
    print(f"  ✓ Nuevos: {nuevos}, Vinculados: {vinculados}, Total: {total}")

with app.app_context():
    print(f"  Canales lógicos: {CanalLogico.query.count()}")
    print(f"  Fuentes gratuitas: {CanalFuente.query.filter_by(proveedor='gratuito').count()}")

    # Ver que RCN tiene 2 fuentes ahora
    rcn = CanalLogico.query.filter_by(canal_id="rcn").first()
    if rcn:
        fuentes = rcn.get_todas_fuentes_activas()
        print(f"\n  RCN tiene {len(fuentes)} fuentes activas:")
        for f in fuentes:
            print(f"    → {f.proveedor}: {f.url[:50]}...")

# ═══════════════════════════════════════════════════
# Paso 3: Cambiar de proveedor
# ═══════════════════════════════════════════════════
print("\n[3/6] Cambiando de Proveedor 1 → Proveedor 2...")
with app.app_context():
    with open("/tmp/test_proveedor2.m3u") as f:
        content = f.read()
    desactivadas, nuevos, vinculados, sin_fuente = cm.cambiar_proveedor(
        "proveedor1", "proveedor2", content
    )
    print(f"  ✓ Fuentes desactivadas: {desactivadas}")
    print(f"  ✓ Canales nuevos: {nuevos}")
    print(f"  ✓ Canales vinculados: {vinculados}")
    print(f"  ✓ Canales sin fuente premium: {sin_fuente}")

# Verificar estado después del cambio
with app.app_context():
    print(f"\n  Estado después del cambio:")
    print(f"  Canales lógicos: {CanalLogico.query.count()}")
    print(f"  Fuentes activas: {CanalFuente.query.filter_by(activo=True).count()}")
    print(f"  Fuentes proveedor1 (inactivas): {CanalFuente.query.filter_by(proveedor='proveedor1', activo=False).count()}")
    print(f"  Fuentes proveedor2: {CanalFuente.query.filter_by(proveedor='proveedor2', activo=True).count()}")

    print("\n  Canales y sus fuentes activas:")
    for ch in CanalLogico.query.order_by(CanalLogico.nombre).all():
        fuente = ch.get_fuente_activa()
        estado = fuente.estado if fuente else "sin_fuente"
        prov = fuente.proveedor if fuente else "N/A"
        print(f"    📺 {ch.display_name or ch.nombre} → {prov} [{estado}]")

# ═══════════════════════════════════════════════════
# Paso 4: Verificar canal específico
# ═══════════════════════════════════════════════════
print("\n[4/6] Verificando canal RCN...")
with app.app_context():
    rcn = CanalLogico.query.filter_by(canal_id="rcn").first()
    if rcn:
        print(f"  Canal: {rcn.nombre}")
        print(f"  Fuentes:")
        for f in rcn.fuentes.order_by(CanalFuente.prioridad).all():
            print(f"    → {f.proveedor} (prioridad {f.prioridad}, activo={f.activo}, estado={f.estado})")
            print(f"      URL: {f.url[:60]}...")

# ═══════════════════════════════════════════════════
# Paso 5: Estadísticas
# ═══════════════════════════════════════════════════
print("\n[5/6] Estadísticas del sistema...")
with app.app_context():
    stats = cm.get_stats()
    print(f"  Total canales: {stats['total_canales']}")
    print(f"  Total fuentes: {stats['total_fuentes']}")
    print(f"  Fuentes premium: {stats['fuentes_premium']}")
    print(f"  Fuentes gratis: {stats['fuentes_gratis']}")
    print(f"  Canales caídos: {stats['canales_caidos']}")
    print(f"  Proveedores: {stats['proveedores']}")

# ═══════════════════════════════════════════════════
# Paso 6: Simular caída y auto-reemplazo
# ═══════════════════════════════════════════════════
print("\n[6/6] Simulando caída de canal...")
with app.app_context():
    # Marcar la fuente de RCN del proveedor2 como caída
    rcn = CanalLogico.query.filter_by(canal_id="rcn").first()
    if rcn:
        fuente_premium = CanalFuente.query.filter_by(
            canal_logico_id=rcn.id, proveedor="proveedor2"
        ).first()
        if fuente_premium:
            fuente_premium.estado = "caido"
            db.session.commit()
            print(f"  ✓ Fuente proveedor2 de RCN marcada como caída")

        # Ejecutar auto-reemplazo
        estado, msg = cm.check_and_replace("rcn")
        print(f"  Resultado: {estado} - {msg}")

        # Verificar que ahora usa el backup gratuito
        rcn_actualizado = CanalLogico.query.filter_by(canal_id="rcn").first()
        nueva_fuente = rcn_actualizado.get_fuente_activa()
        if nueva_fuente:
            print(f"  Nueva fuente activa: {nueva_fuente.proveedor} ({nueva_fuente.url[:50]}...)")

print("\n" + "=" * 70)
print("✅ PRUEBA COMPLETADA")
print("=" * 70)
