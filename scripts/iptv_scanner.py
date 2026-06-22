#!/usr/bin/env python3
"""
IPTV Channel Scanner - Free Channel Aggregator (Optimizado)
Descarga listas M3U públicas, verifica canales en paralelo, importa los que funcionan.
"""

import sqlite3
import subprocess
import sys
import os
import re
import json
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

DB_PATH = os.environ.get("DATA_DIR", "/data") + "/streamflow.sqlite"
SCAN_TIMEOUT = int(os.environ.get("SCAN_TIMEOUT", "6"))
MAX_CHANNELS = int(os.environ.get("SCAN_MAX_CHANNELS", "200"))
MAX_WORKERS = int(os.environ.get("SCAN_WORKERS", "10"))  # Verificación paralela
PROVIDER_NAME = "Free IPTV"

M3U_SOURCES = [
    {"name": "iptv-org-co", "url": "https://iptv-org.github.io/iptv/countries/co.m3u"},
    {"name": "iptv-org-co-raw", "url": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/co.m3u"},
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
log_lock = threading.Lock()


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_lock:
        print(f"[{ts}] {msg}", flush=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_provider(conn):
    row = conn.execute("SELECT id FROM providers WHERE name = ?", (PROVIDER_NAME,)).fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO providers (name, url, username, password, max_connections, is_active) "
        "VALUES (?, '', '', '', 999, 1)", (PROVIDER_NAME,),
    )
    conn.commit()
    return cur.lastrowid


def download_m3u(url, dest):
    try:
        result = subprocess.run(
            ["wget", "-q", "--timeout=20", "-O", dest, url],
            capture_output=True, text=True, timeout=40,
        )
        return result.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest) > 100
    except:
        return False


def parse_m3u(filepath):
    channels = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except:
        return channels

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF:"):
            if i + 1 < len(lines):
                url = lines[i + 1].strip()
                if url.startswith("http"):
                    name_m = re.search(r',([^,]+)$', line)
                    name = name_m.group(1).strip() if name_m else "Unknown"
                    logo_m = re.search(r'tvg-logo="([^"]*)"', line)
                    logo = logo_m.group(1) if logo_m else ""
                    group_m = re.search(r'group-title="([^"]*)"', line)
                    group = group_m.group(1) if group_m else "Libre"
                    channels.append({"name": name, "logo": logo, "group": group, "stream_url": url})
                i += 1
        i += 1
    return channels


def check_channel(ch, timeout=SCAN_TIMEOUT):
    """Verifica un canal. Retorna (channel_dict, ok)."""
    url = ch["stream_url"]
    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null",
             "-w", "%{http_code}|%{size_download}",
             "--max-time", str(timeout), "-L", "-k",
             "-A", UA, "-H", "Accept: */*", url],
            capture_output=True, text=True, timeout=timeout + 3,
        )
        parts = result.stdout.strip().split("|")
        if len(parts) == 2:
            code = int(parts[0]) if parts[0].isdigit() else 0
            size = int(parts[1]) if parts[1].isdigit() else 0
            ok = 200 <= code < 400 and size > 50
            return ch, ok, code, size
    except:
        pass
    return ch, False, 0, 0


def run_scan():
    log("=" * 60)
    log("ESCANEO DE CANALES LIBRES (paralelo)")
    log("=" * 60)

    conn = get_db()
    provider_id = ensure_provider(conn)

    tmp_dir = Path("/tmp/iptv_scan")
    tmp_dir.mkdir(exist_ok=True)

    all_channels = []
    for source in M3U_SOURCES:
        dest = tmp_dir / f"{source['name']}.m3u"
        if download_m3u(source["url"], str(dest)):
            chs = parse_m3u(str(dest))
            log(f"✅ {source['name']}: {len(chs)} canales")
            all_channels.extend(chs)
        else:
            log(f"❌ {source['name']}: error descargando")

    # Dedup
    seen = set()
    unique = []
    for ch in all_channels:
        if ch["stream_url"] not in seen:
            seen.add(ch["stream_url"])
            unique.append(ch)

    to_check = unique[:MAX_CHANNELS]
    log(f"Verificando {len(to_check)} canales ({MAX_WORKERS} paralelos, timeout {SCAN_TIMEOUT}s)...")

    working = []
    failed = 0
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_channel, ch): ch for ch in to_check}
        for future in as_completed(futures):
            done += 1
            ch, ok, code, size = future.result()
            if ok:
                working.append(ch)
                log(f"  ✅ [{done}/{len(to_check)}] {ch['name'][:40]} ({code},{size}b)")
            else:
                failed += 1
                if done % 20 == 0:
                    log(f"  ... {done} verificados, {len(working)} OK, {failed} fail")

    log(f"\nResultado: {len(working)} OK, {failed} fail de {len(to_check)}")

    # Importar
    imported = updated = 0
    for ch in working:
        existing = conn.execute("SELECT id FROM channels WHERE stream_url = ?", (ch["stream_url"],)).fetchone()
        if existing:
            conn.execute("UPDATE channels SET is_active = 1 WHERE id = ?", (existing[0],))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO channels (provider_id, name, logo, group_name, stream_url, is_active) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (provider_id, ch["name"], ch["logo"], ch["group"], ch["stream_url"]),
            )
            imported += 1
    conn.commit()

    # Desactivar viejos
    active_urls = {ch["stream_url"] for ch in working}
    if active_urls:
        placeholders = ",".join("?" * len(active_urls))
        deact = conn.execute(
            f"UPDATE channels SET is_active = 0 WHERE provider_id = ? AND is_active = 1 "
            f"AND stream_url NOT IN ({placeholders})",
            [provider_id] + list(active_urls),
        ).rowcount
        if deact:
            log(f"Desactivados: {deact} canales viejos")

    stats = conn.execute(
        "SELECT COUNT(*), SUM(is_active) FROM channels WHERE provider_id = ?",
        (provider_id,),
    ).fetchone()

    log(f"\n📊 {PROVIDER_NAME}: {stats[1] or 0}/{stats[0]} canales activos")
    conn.close()
    log("✅ COMPLETADO")
    return len(working), failed, imported


if __name__ == "__main__":
    try:
        w, f, i = run_scan()
        sys.exit(0 if w > 0 else 1)
    except Exception as e:
        log(f"❌ Error: {e}")
        import traceback; traceback.print_exc()
        sys.exit(1)
