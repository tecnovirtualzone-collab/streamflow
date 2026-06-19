#!/usr/bin/env python3
"""
Prueba 1: Cargar canales desde lista gratuita (iptv-org)
"""
import os
import sys
import requests

os.environ['ADMIN_PASSWORD'] = 'admin123'
os.environ['SECRET_KEY'] = 'test'
os.environ['JWT_SECRET'] = 'test'
os.environ['DATABASE_URL'] = 'sqlite:///test_panel.db'
os.environ['URL_M3U'] = ''

from app import app, db
from channel_manager import ChannelManager

print("=" * 60)
print("PRUEBA 1: Carga de canales desde lista gratuita")
print("=" * 60)

cm = ChannelManager(db, app)

# Descargar lista gratuita
print("\n[1/3] Descargando lista iptv-org...")
url = "https://iptv-org.github.io/iptv/index.m3u8"
try:
    resp = requests.get(url, timeout=60)
    print(f"  ✓ Descargado: {len(resp.text):,} bytes")
except Exception as e:
    print(f"  ✗ Error: {e}")
    sys.exit(1)

# Cargar canales gratuitos
print("\n[2/3] Cargando canales gratuitos...")
with app.app_context():
    nuevos, vinculados = cm.load_from_free_lists([url])
    print(f"  ✓ Nuevos: {nuevos}")
    print(f"  ✓ Vinculados: {vinculados}")

# Verificar
print("\n[3/3] Verificando canales cargados...")
with app.app_context():
    from app import CanalLogico, CanalFuente
    total_canales = CanalLogico.query.count()
    total_fuentes = CanalFuente.query.count()
    fuentes_gratis = CanalFuente.query.filter_by(proveedor="gratuito").count()
    
    print(f"  Canales lógicos: {total_canales}")
    print(f"  Fuentes totales: {total_fuentes}")
    print(f"  Fuentes gratuitas: {fuentes_gratis}")
    
    # Mostrar algunos ejemplos
    canales = CanalLogico.query.filter_by(activo=True).limit(5).all()
    print(f"\n  Ejemplos de canales cargados:")
    for ch in canales:
        fuente = ch.get_fuente_activa()
        print(f"    📺 {ch.display_name or ch.nombre}")
        print(f"       Grupo: {ch.grupo}")
        print(f"       Fuente: {fuente.proveedor if fuente else 'N/A'}")
        print(f"       URL: {(fuente.url if fuente else '')[:60]}...")
        print()

print("✅ PRUEBA 1 COMPLETADA")
