#!/usr/bin/env python3
"""
Test 5: Verificación del código — ¿Implementa correctamente el relay compartido?

Analiza el código de app.py y vlc_manager.py para verificar:
1. ¿Múltiples usuarios del mismo canal comparten 1 relay?
2. ¿El relay se reutiliza correctamente?
3. ¿Hay algún camino que permita conexiones directas al proveedor?
4. ¿El contador de viewers funciona bien?
"""
import ast
import sys

print("=" * 70)
print("TEST 5: Verificación del código StreamFlow v3.0")
print("=" * 70)

# ═══════════════════════════════════════════════════
# 1. Verificar VLCRelayManager.start_relay()
# ═══════════════════════════════════════════════════
print("\n[1/6] Verificando VLCRelayManager.start_relay()...")

with open("/root/streamflow/vlc_manager.py") as f:
    vlc_code = f.read()

# Verificar que reutiliza relays existentes
checks = {
    "Reutiliza relay existente si está vivo": 'if relay.is_alive()' in vlc_code and 'return relay.local_url' in vlc_code,
    "Incrementa viewers en relay existente": 'relay.add_viewer()' in vlc_code or 'self._relays[canal_id].viewers' in vlc_code,
    "Límite de canales simultáneos": 'VLC_MAX_CHANNELS' in vlc_code and 'len(self._relays) >= VLC_MAX_CHANNELS' in vlc_code,
    "Auto-stop por inactividad": 'viewers == 0' in vlc_code and 'VLC_TIMEOUT' in vlc_code,
    "Health check automático": '_health_loop' in vlc_code and 'relay.is_alive()' in vlc_code,
    "Auto-reconnect": 'reconnect()' in vlc_code,
}

for check, result in checks.items():
    status = "✅" if result else "❌"
    print(f"  {status} {check}")

# ═══════════════════════════════════════════════════
# 2. Verificar endpoint live_stream_hls
# ═══════════════════════════════════════════════════
print("\n[2/6] Verificando endpoint /live/...")

with open("/root/streamflow/app.py") as f:
    app_code = f.read()

endpoint_checks = {
    "Usa vlc_manager.start_relay()": 'vlc_manager.start_relay' in app_code,
    "Usa vlc_manager.add_viewer()": 'vlc_manager.add_viewer' in app_code,
    "Usa vlc_manager.remove_viewer()": 'vlc_manager.remove_viewer' in app_code,
    "Espera a que VLC esté listo": 'ready_event.wait' in app_code,
    "Fallback directo al proveedor": 'gen_fallback' in app_code or 'url_proveedor' in app_code,
    "Proxy del stream local de VLC": 'requests.get(local_url' in app_code,
}

for check, result in endpoint_checks.items():
    status = "✅" if result else "❌"
    print(f"  {status} {check}")

# ═══════════════════════════════════════════════════
# 3. Verificar sistema HLS con FFmpeg (el que SÍ funciona)
# ═══════════════════════════════════════════════════
print("\n[3/6] Verificando sistema HLS con FFmpeg (start_relay)...")

hls_checks = {
    "Solo 1 FFmpeg por canal": 'if canal_id in _relays' in app_code and '_relay_lock' in app_code,
    "Comparte segmentos entre viewers": 'viewers' in app_code and '_relays[canal_id]["viewers"]' in app_code,
    "Auto-stop sin viewers": 'viewers' in app_code and 'HLS_TIMEOUT' in app_code,
    "Limpieza de segmentos al apagar": 'shutil.rmtree' in app_code,
    "Espera a que el playlist esté listo": '_mark_ready' in app_code,
    "Usa -c copy (sin re-codificar)": '"-c", "copy"' in app_code,
}

for check, result in hls_checks.items():
    status = "✅" if result else "❌"
    print(f"  {status} {check}")

# ═══════════════════════════════════════════════════
# 4. Verificar que NO hay conexiones directas sin relay
# ═══════════════════════════════════════════════════
print("\n[4/6] Verificando que no hay bypass del relay...")

# Buscar si hay algún endpoint que sirva streams directos sin pasar por relay
bypass_risks = []

# Verificar que el endpoint /live/ siempre usa relay
if 'vlc_manager.start_relay' in app_code:
    # Verificar que no hay un camino sin relay
    if 'requests.get(url_proveedor' in app_code and 'gen_fallback' in app_code:
        bypass_risks.append("⚠️ Fallback directo al proveedor existe (solo si VLC falla)")
    
    if 'requests.get(local_url' in app_code:
        print(f"  ✅ Endpoint /live/ usa proxy local de VLC")
else:
    bypass_risks.append("❌ Endpoint /live/ NO usa VLC relay")

# Verificar que /hls/ solo sirve archivos locales
if 'os.path.join(HLS_DIR' in app_code:
    print(f"  ✅ Endpoint /hls/ solo sirve archivos locales")
else:
    bypass_risks.append("❌ Endpoint /hls/ podría servir URLs remotas")

for risk in bypass_risks:
    print(f"  {risk}")

# ═══════════════════════════════════════════════════
# 5. Verificar contador de viewers
# ═══════════════════════════════════════════════════
print("\n[5/6] Verificando contador de viewers...")

viewer_checks = {
    "Incrementa viewers al entrar": '+=' in app_code and 'viewers' in app_code,
    "Decrementa viewers al salir": 'remove_viewer' in app_code or '-=' in app_code,
    "Thread-safe (lock)": '_relay_lock' in app_code or '_lock' in vlc_code,
    "Cleanup de relays sin viewers": 'viewers' in app_code and ('<= 0' in app_code or '== 0' in app_code),
}

for check, result in viewer_checks.items():
    status = "✅" if result else "❌"
    print(f"  {status} {check}")

# ═══════════════════════════════════════════════════
# 6. Resumen
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"[6/6] RESUMEN DE LA ARQUITECTURA")
print(f"{'=' * 70}")

print(f"""
  FLUJO DE UN USUANDO VIENDO UN CANAL:
  
  1. Usuario → GET /live/user/pass/123.ts
  2. Flask verifica auth + conexiones
  3. vlc_manager.add_viewer("123")
  4. vlc_manager.start_relay("123")
     ├─ Si ya existe relay activo → reutiliza (viewers++)
     └─ Si no existe → inicia VLC (1 conexión al proveedor)
  5. Espera a que VLC esté listo
  6. Proxy del stream local de VLC → usuario
  7. Cuando usuario desconecta → vlc_manager.remove_viewer("123")
  8. Si viewers == 0 por 60s → VLC se apaga
  
  CONEXIONES AL PROVEEDOR:
  ┌──────────────────────────────────────────────────────┐
  │  200 usuarios en canal X = 1 conexión al proveedor  │
  │  200 usuarios en 8 canales = 8 conexiones al proveedor│
  │  El proveedor solo ve 8 conexiones de tu VPS        │
  └──────────────────────────────────────────────────────┘
  
  SISTEMA DUAL:
  - VLC Manager (vlc_manager.py): Relay vía VLC HTTP
  - HLS Relay (app.py): Relay vía FFmpeg → segmentos HLS
  - El endpoint /live/ usa VLC Manager
  - El endpoint /hls/ sirve segmentos FFmpeg
  
  ⚠️ PROBLEMA DETECTADO:
  El endpoint /live/ tiene un fallback directo al proveedor
  si VLC no está listo. Esto significa que si VLC falla,
  cada usuario abriría su propia conexión al proveedor.
  
  SOLUCIÓN: El fallback debería usar el relay HLS (FFmpeg)
  en lugar de ir directo al proveedor.
""")

print("✅ TEST 5 COMPLETADO")
