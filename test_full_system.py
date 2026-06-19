#!/usr/bin/env python3
"""Prueba completa del panel y todas las funcionalidades"""
import os, sys, json, requests

os.environ['ADMIN_PASSWORD'] = 'admin123'
os.environ['SECRET_KEY'] = 'test_secret'
os.environ['JWT_SECRET'] = 'test_jwt'
os.environ['DATABASE_URL'] = 'sqlite:///test_full.db'
os.environ['URL_M3U'] = ''

from app import app, db
from channel_manager import ChannelManager

print("=" * 70)
print("PRUEBA COMPLETA DEL SISTEMA")
print("=" * 70)

cm = ChannelManager(db, app)

# ═══════════════════════════════════════════════════
# 1. Login
# ═══════════════════════════════════════════════════
print("\n[1/8] Login...")
with app.test_client() as c:
    r = c.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    d = r.get_json()
    token = d.get('token', '')
    print(f"  Status: {r.status_code}")
    print(f"  Token: {token[:20]}..." if token else f"  ✗ Error: {d}")
    assert token, "Login falló"

headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

# ═══════════════════════════════════════════════════
# 2. Dashboard stats
# ═══════════════════════════════════════════════════
print("\n[2/8] Dashboard stats...")
with app.test_client() as c:
    r = c.get('/admin/stats', headers=headers)
    d = r.get_json()
    print(f"  Status: {r.status_code}")
    print(f"  Usuarios: {d.get('total_usuarios', 0)}")

# ═══════════════════════════════════════════════════
# 3. Crear usuario
# ═══════════════════════════════════════════════════
print("\n[3/8] Crear usuario...")
with app.test_client() as c:
    r = c.post('/admin/usuarios', headers=headers, json={
        'usuario': 'test_user',
        'paquete': 'premium',
        'dias': 30,
        'pantallas': 2,
        'notas': '+57 300 123 4567'
    })
    d = r.get_json()
    print(f"  Status: {r.status_code}")
    print(f"  Usuario: {d.get('usuario')}")
    print(f"  Password: {d.get('password')}")
    assert d.get('usuario') == 'test_user', "Creación falló"

# ═══════════════════════════════════════════════════
# 4. Listar usuarios
# ═══════════════════════════════════════════════════
print("\n[4/8] Listar usuarios...")
with app.test_client() as c:
    r = c.get('/admin/usuarios', headers=headers)
    d = r.get_json()
    print(f"  Status: {r.status_code}")
    print(f"  Total: {len(d)}")
    for u in d:
        print(f"    👤 {u['usuario']} ({u['paquete']}) - {'Activo' if u['activo'] else 'Inactivo'}")

# ═══════════════════════════════════════════════════
# 5. Cargar canales de prueba
# ═══════════════════════════════════════════════════
print("\n[5/8] Cargar canales de prueba...")
with app.app_context():
    with open("/tmp/test_proveedor1.m3u") as f:
        content = f.read()
    nuevos, vinculados, total = cm.load_from_proveedor("proveedor1", content)
    print(f"  Nuevos: {nuevos}, Vinculados: {vinculados}, Total: {total}")
    assert total == 10, f"Esperaba 10 canales, obtuve {total}"

# ═══════════════════════════════════════════════════
# 6. Listar canales
# ═══════════════════════════════════════════════════
print("\n[6/8] Listar canales...")
with app.test_client() as c:
    r = c.get('/admin/channels', headers=headers)
    d = r.get_json()
    print(f"  Status: {r.status_code}")
    print(f"  Canales: {d.get('total', 0)}")
    for ch in d.get('canales', [])[:5]:
        print(f"    📺 {ch['display_name']} [{ch['fuente_proveedor']}] - {ch['fuente_estado']}")

# ═══════════════════════════════════════════════════
# 7. Cambiar proveedor
# ═══════════════════════════════════════════════════
print("\n[7/8] Cambiar proveedor...")
with app.test_client() as c:
    with open("/tmp/test_proveedor2.m3u") as f:
        content = f.read()
    r = c.post('/admin/channels/change-provider', headers=headers, json={
        'proveedor_anterior': 'proveedor1',
        'proveedor_nuevo': 'proveedor2',
        'url_nueva': 'http://test.m3u'
    })
    # Esto va a fallar porque la URL no es real, pero probamos el flujo
    print(f"  Status: {r.status_code}")
    if r.status_code == 200:
        d = r.get_json()
        print(f"  Desactivadas: {d.get('fuentes_desactivadas')}")
        print(f"  Vinculados: {d.get('canales_vinculados')}")

# ═══════════════════════════════════════════════════
# 8. Estadísticas de canales
# ═══════════════════════════════════════════════════
print("\n[8/8] Estadísticas de canales...")
with app.test_client() as c:
    r = c.get('/admin/channels/stats', headers=headers)
    d = r.get_json()
    print(f"  Status: {r.status_code}")
    print(f"  Canales: {d.get('total_canales')}")
    print(f"  Fuentes premium: {d.get('fuentes_premium')}")
    print(f"  Proveedores: {d.get('proveedores')}")

print("\n" + "=" * 70)
print("✅ TODAS LAS PRUEBAS COMPLETADAS")
print("=" * 70)
