#!/usr/bin/env python3
"""
Buscar canales específicos en la lista M3U de iptv-org:
- Canales de Colombia (RCN, Caracol, etc.)
- Deportes (Win Sports, ESPN, Fox Sports, etc.)
- Otros canales relevantes para IPTV Colombia
"""
import requests
import re
import json
import time

M3U_URL = "https://iptv-org.github.io/iptv/index.m3u"

print("=" * 70)
print("BUSCADOR DE CANALES - Lista M3U iptv-org")
print("=" * 70)

# Descargar lista
print(f"\nDescargando lista M3U...")
start = time.time()
resp = requests.get(M3U_URL, timeout=60)
content = resp.text
lines = content.split("\n")
print(f"✓ Descargado en {time.time()-start:.1f}s ({len(content):,} bytes)")

# Parsear todos los canales
channels = []
current = {}
for line in lines:
    line = line.strip()
    if line.startswith("#EXTINF:"):
        current = {"info": line}
        name_match = re.search(r'tvg-name="([^"]*)"', line)
        id_match = re.search(r'tvg-id="([^"]*)"', line)
        group_match = re.search(r'group-title="([^"]*)"', line)
        logo_match = re.search(r'tvg-logo="([^"]*)"', line)
        # Nombre después de la última coma
        if "," in line:
            current["name"] = line.rsplit(",", 1)[-1].strip()
        else:
            current["name"] = ""
        current["tvg_id"] = id_match.group(1) if id_match else ""
        current["group"] = group_match.group(1) if group_match else ""
        current["logo"] = logo_match.group(1) if logo_match else ""
    elif line.startswith("http"):
        if current:
            current["url"] = line
            channels.append(current)
            current = {}

print(f"✓ {len(channels)} canales parseados\n")

# ═══════════════════════════════════════════════════
# BÚSQUEDAS
# ═══════════════════════════════════════════════════

def search_channels(keywords, exclude=None, max_results=20):
    """Busca canales por keywords en nombre, grupo o tvg-id"""
    results = []
    exclude = exclude or []
    for ch in channels:
        text = f"{ch.get('name','')} {ch.get('group','')} {ch.get('tvg_id','')}".lower()
        if any(kw.lower() in text for kw in keywords):
            if not any(ex.lower() in text for ex in exclude):
                results.append(ch)
                if len(results) >= max_results:
                    break
    return results

def print_results(title, results):
    print(f"\n{'─' * 70}")
    print(f"📺 {title} ({len(results)} encontrados)")
    print(f"{'─' * 70}")
    for i, ch in enumerate(results, 1):
        name = ch.get("name", "Sin nombre") or "Sin nombre"
        group = ch.get("group", "") or "Sin grupo"
        url = ch.get("url", "")
        # Determinar tipo de stream
        if ".m3u8" in url:
            stream_type = "HLS"
        elif ".ts" in url:
            stream_type = "TS"
        else:
            stream_type = "Otro"
        print(f"  {i:>3}. {name}")
        print(f"       Grupo: {group}")
        print(f"       Tipo:  {stream_type}")
        print(f"       URL:   {url[:90]}...")
        print()

# 1. Canales de Colombia
colombia = search_channels(
    ["colombia", "caracol", "rcn", "rcn nuestra tele", "señal colombia",
     "canal 13", "canal uno", "teleantioquia", "telepacífico", "telecaribe",
     "telecafe", "teleislas", "red+"],
    exclude=["colombia_antigua"]
)
print_results("CANALES DE COLOMBIA", colombia)

# 2. Deportes Colombia
deportes_col = search_channels(
    ["win sports", "win+", "win sports+", "directv sports", "dsports",
     "espn colombia", "fox sports colombia", "tnt sports colombia",
     "caracol deportes", "rcn deportes"]
)
print_results("DEPORTES COLOMBIA", deportes_col)

# 3. Deportes generales
deportes = search_channels(
    ["espn", "fox sports", "tnt sports", "directv", "dsports",
     "tyc sports", "gol tv", "bein", "barça tv", "real madrid tv",
     "manchester tv", "nbc sports", "sky sports", "sport tv",
     "espn deportes", "fox deportes", "univision deportes",
     "futbol", "football", "soccer", "sports"],
    exclude=["espn colombia", "fox sports colombia"]  # Ya listados arriba
)
print_results("DEPORTES GENERALES", deportes)

# 4. Noticias
noticias = search_channels(
    ["cnn", "dw", "france24", "rt", "teleSUR", "noticias",
     "caracol noticias", "rcn noticias", "red+ noticias",
     "blu radio", "la fm", "w radio"]
)
print_results("NOTICIAS", noticias)

# 5. Entretenimiento / Novelas
entretenimiento = search_channels(
    ["telemundo", "univision", "televisa", "caracol", "rcn",
     "novelas", "telenovelas", "entretenimiento", "farandula",
     "vix", "pluto", "tubi"]
)
print_results("ENTRETENIMIENTO / NOVELAS", entretenimiento)

# 6. Cine
cine = search_channels(
    ["hbo", "cinemax", "star channel", "fx", "sony", "warner",
     "universal", "paramount", "cine", "movies", "películas",
     "film", "cinema"]
)
print_results("CINE", cine)

# 7. Kids
kids = search_channels(
    ["disney", "cartoon", "nickelodeon", "nick jr", "paw patrol",
     "kids", "infantil", "children", "baby", "boomerang", "tooncast"]
)
print_results("KIDS / INFANTIL", kids)

# 8. Música
musica = search_channels(
    ["mtv", "vh1", "music", "música", "radio", "la mega",
     "los 40", "reggaeton", "tropical"]
)
print_results("MÚSICA", musica)

# ═══════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"RESUMEN DE BÚSQUEDA")
print(f"{'=' * 70}")

all_found = {
    "Colombia": colombia,
    "Deportes Colombia": deportes_col,
    "Deportes Generales": deportes,
    "Noticias": noticias,
    "Entretenimiento": entretenimiento,
    "Cine": cine,
    "Kids": kids,
    "Música": musica,
}

total_found = 0
for cat, items in all_found.items():
    count = len(items)
    total_found += count
    print(f"  {cat:<25} {count:>4} canales")

print(f"\n  {'TOTAL':<25} {total_found:>4} canales")

# Guardar todos los resultados
all_channels = []
seen_urls = set()
for cat, items in all_found.items():
    for ch in items:
        if ch["url"] not in seen_urls:
            ch["categoria_busqueda"] = cat
            all_channels.append(ch)
            seen_urls.add(ch["url"])

with open("/tmp/canales_encontrados.json", "w") as f:
    json.dump(all_channels, f, indent=2, ensure_ascii=False)

print(f"\n  ✓ {len(all_channels)} canales únicos guardados en /tmp/canales_encontrados.json")

# ═══════════════════════════════════════════════════
# VERIFICAR STREAMS FUNCIONANTE
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"VERIFICANDO STREAMS FUNCIONALES (muestra de 5)")
print(f"{'=' * 70}")

import subprocess

sample = all_channels[:5]
for i, ch in enumerate(sample):
    name = ch.get("name", "?")
    url = ch.get("url", "")
    print(f"\n  [{i+1}] {name}")
    print(f"      URL: {url[:80]}...")
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
               "-of", "json", "-timeout", "8000000", url]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if proc.returncode == 0 and proc.stdout.strip():
            info = json.loads(proc.stdout)
            streams = info.get("streams", [])
            print(f"      ✓ FUNCIONA - {len(streams)} stream(s)")
            for s in streams:
                print(f"        → {s.get('codec_name','?')} {s.get('width','?')}x{s.get('height','?')}")
        else:
            print(f"      ✗ No responde")
    except:
        print(f"      ✗ Timeout/Error")

print(f"\n✅ BÚSQUEDA COMPLETADA")
