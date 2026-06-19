#!/usr/bin/env python3
"""
Test 1: Descargar y parsear la lista M3U de iptv-org
"""
import requests
import re
import sys
import time

M3U_URL = "https://iptv-org.github.io/iptv/index.m3u"

print("=" * 60)
print("TEST 1: Descarga y parseo de lista M3U")
print("=" * 60)

# Descargar
print(f"\n[1/4] Descargando {M3U_URL} ...")
start = time.time()
try:
    resp = requests.get(M3U_URL, timeout=60)
    elapsed = time.time() - start
    print(f"  ✓ Descargado en {elapsed:.1f}s ({len(resp.text):,} bytes)")
except Exception as e:
    print(f"  ✗ Error: {e}")
    sys.exit(1)

# Verificar formato M3U
content = resp.text
lines = content.split("\n")
print(f"\n[2/4] Verificando formato M3U...")
if lines[0].strip().startswith("#EXTM3U"):
    print(f"  ✓ Header #EXTM3U válido: {lines[0].strip()[:80]}")
else:
    print(f"  ✗ Header inválido: {lines[0][:100]}")
    sys.exit(1)

# Parsear canales
print(f"\n[3/4] Parsear canales...")
channels = []
current = {}
for line in lines:
    line = line.strip()
    if line.startswith("#EXTINF:"):
        current = {"info": line}
        # Extraer atributos
        name_match = re.search(r'tvg-name="([^"]*)"', line)
        id_match = re.search(r'tvg-id="([^"]*)"', line)
        group_match = re.search(r'group-title="([^"]*)"', line)
        logo_match = re.search(r'tvg-logo="([^"]*)"', line)
        current["name"] = name_match.group(1) if name_match else ""
        current["tvg_id"] = id_match.group(1) if id_match else ""
        current["group"] = group_match.group(1) if group_match else ""
        current["logo"] = logo_match.group(1) if logo_match else ""
    elif line.startswith("http"):
        if current:
            current["url"] = line
            channels.append(current)
            current = {}

print(f"  ✓ {len(canales)} canales encontrados" if False else f"  ✓ {len(channels)} canales encontrados")

# Analizar tipos de URL
url_types = {}
for ch in channels:
    url = ch.get("url", "")
    if ".m3u8" in url:
        url_types["HLS (.m3u8)"] = url_types.get("HLS (.m3u8)", 0) + 1
    elif ".ts" in url:
        url_types["TS (.ts)"] = url_types.get("TS (.ts)", 0) + 1
    elif "live" in url and ("username" in url or "password" in url or "get.php" in url):
        url_types["Xtream Codes"] = url_types.get("Xtream Codes", 0) + 1
    else:
        ext = url.split(".")[-1].split("?")[0] if "." in url else "otro"
        url_types[ext] = url_types.get(ext, 0) + 1

print(f"\n[4/4] Tipos de stream encontrados:")
for t, count in sorted(url_types.items(), key=lambda x: -x[1]):
    print(f"  - {t}: {count} canales")

# Mostrar algunos ejemplos
print(f"\n[5/5] Ejemplos de canales (primeros 10):")
for ch in channels[:10]:
    name = ch.get("name", "Sin nombre") or "Sin nombre"
    group = ch.get("group", "") or "Sin grupo"
    url = ch.get("url", "")
    print(f"  📺 {name}")
    print(f"     Grupo: {group}")
    print(f"     URL: {url[:80]}...")
    print()

# Guardar canales HLS para pruebas
hls_channels = [ch for ch in channels if ".m3u8" in ch.get("url", "")]
print(f"\n📊 RESUMEN:")
print(f"  Total canales: {len(channels)}")
print(f"  Canales HLS (.m3u8): {len(hls_channels)}")
print(f"  Canales con URL válida: {sum(1 for c in channels if c.get('url', '').startswith('http'))}")

# Guardar lista de canales HLS para siguientes tests
with open("/tmp/hls_channels.json", "w") as f:
    import json
    json.dump(hls_channels[:20], f, indent=2)
print(f"  ✓ Guardados 20 canales HLS en /tmp/hls_channels.json")

print("\n✅ TEST 1 COMPLETADO")
