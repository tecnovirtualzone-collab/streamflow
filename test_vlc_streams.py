#!/usr/bin/env python3
"""
Test 2 (v2): Probar streams reales de iptv-org con ffprobe
Usa las URLs directamente del JSON (ignorando el campo name)
"""
import json
import subprocess
import requests
import time
import sys

print("=" * 60)
print("TEST 2: Probar streams reales con ffprobe")
print("=" * 60)

# Cargar canales
with open("/tmp/hls_channels.json") as f:
    channels = json.load(f)

# Extraer nombre del campo info (después de la última coma)
def extract_name(ch):
    info = ch.get("info", "")
    if "," in info:
        return info.rsplit(",", 1)[-1].strip()
    return "Sin nombre"

# Filtrar URLs válidas
valid = [c for c in channels if c.get("url", "").startswith("http")]
print(f"\nCanales con URL válida: {len(valid)}")

# Probar 5 canales
sample = valid[:5]
print(f"Probando {len(sample)} canales:\n")

results = []

for i, ch in enumerate(sample):
    name = extract_name(ch)
    url = ch["url"]
    print(f"[{i+1}/{len(sample)}] {name}")
    print(f"  URL: {url[:90]}...")
    
    # Test HTTP
    try:
        r = requests.head(url, timeout=8, allow_redirects=True, headers={
            "User-Agent": "VLC/3.0.18 LibVLC/3.0.18"
        })
        http_ok = r.status_code == 200
        ct = r.headers.get("Content-Type", "?")
        print(f"  HTTP {r.status_code} | {ct}")
    except Exception as e:
        print(f"  HTTP Error: {str(e)[:60]}")
        http_ok = False
    
    # Test FFprobe
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration,bit_rate:stream=codec_name,width,height",
            "-of", "json",
            "-timeout", "8000000",
            "-user_agent", "VLC/3.0.18 LibVLC/3.0.18",
            url
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if proc.returncode == 0 and proc.stdout.strip():
            info = json.loads(proc.stdout)
            streams = info.get("streams", [])
            fmt = info.get("format", {})
            br = fmt.get("bit_rate", "?")
            print(f"  ✓ FFprobe OK | {len(streams)} stream(s) | Bitrate: {br}")
            for s in streams:
                print(f"    → {s.get('codec_name','?')} {s.get('width','?')}x{s.get('height','?')}")
            probe_ok = True
        else:
            err = (proc.stderr or "sin output")[:150]
            print(f"  ✗ FFprobe falló: {err}")
            probe_ok = False
    except subprocess.TimeoutExpired:
        print(f"  ✗ FFprobe timeout")
        probe_ok = False
    except Exception as e:
        print(f"  ✗ FFprobe error: {str(e)[:60]}")
        probe_ok = False
    
    results.append({"name": name, "url": url, "http_ok": http_ok, "probe_ok": probe_ok})
    print()

# Resumen
print("=" * 60)
print("RESUMEN")
print("=" * 60)
http_ok = sum(1 for r in results if r["http_ok"])
probe_ok = sum(1 for r in results if r["probe_ok"])
print(f"  HTTP OK: {http_ok}/{len(results)}")
print(f"  FFprobe OK: {probe_ok}/{len(results)}")

if probe_ok > 0:
    print(f"\n  ✓ Streams de iptv-org SÍ funcionan con FFmpeg")
    print(f"  ✓ Se pueden usar para relay HLS")
else:
    print(f"\n  ⚠ Streams no respondieron (posible geo-bloqueo o requerir headers)")

with open("/tmp/test2_results.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\n✅ TEST 2 COMPLETADO")
