#!/usr/bin/env python3
"""
Massive IPTV Channel Importer + Backup Generator
Imports channels from iptv-org and generates backup channels for plans.

Usage:
    python3 import_all.py              # Import everything
    python3 import_all.py --countries  # Only country channels
    python3 import_all.py --backups    # Only generate backups
    python3 import_all.py --test       # Test M3U sources only
"""

import sqlite3
import urllib.request
import urllib.error
import re
import sys
import os
import time

DATA_DIR = os.environ.get('DATA_DIR', '/data')
DB_PATH = os.path.join(DATA_DIR, 'streamflow.sqlite')

COUNTRIES = ['co', 'mx', 'ar', 'es', 'pe', 'cl', 'ec', 've', 'cu', 'do',
             'py', 'bo', 'uy', 'hn', 'sv', 'ni', 'cr', 'pa', 'gt',
             'us', 'br', 'pt', 'it', 'fr', 'de', 'gb', 'ca']

CATEGORIES = ['news', 'sports', 'entertainment', 'movies', 'series',
              'kids', 'music', 'documentary', 'lifestyle', 'religious', 'education']

args = sys.argv[1:]
MODE_COUNTRIES = '--countries' in args
MODE_BACKUPS = '--backups' in args
MODE_TEST = '--test' in args
MODE_ALL = not MODE_COUNTRIES and not MODE_BACKUPS and not MODE_TEST


def fetch_url(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'StreamFlow-Importer/2.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_m3u(content):
    lines = content.split('\n')
    channels = []
    current = {}
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith('#EXTINF:'):
            name_m = re.search(r',(.+)$', trimmed)
            logo_m = re.search(r'tvg-logo="([^"]*)"', trimmed)
            group_m = re.search(r'group-title="([^"]*)"', trimmed)
            current = {
                'name': name_m.group(1).strip() if name_m else '',
                'logo': logo_m.group(1) if logo_m else '',
                'group': group_m.group(1) if group_m else 'General',
            }
        elif trimmed and not trimmed.startswith('#') and current.get('name'):
            channels.append({**current, 'url': trimmed})
            current = {}
    return channels


def import_channels(db):
    total_imported = 0
    total_skipped = 0

    # Get or create provider
    row = db.execute("SELECT id FROM providers WHERE name = 'IPTV Gratis (iptv.org)'").fetchone()
    if row:
        provider_id = row[0]
    else:
        cur = db.execute(
            "INSERT INTO providers (name, url, username, password) VALUES (?, ?, ?, ?)",
            ('IPTV Gratis (iptv.org)', 'https://iptv-org.github.io', '', '')
        )
        provider_id = cur.lastrowid
        print(f'  Created provider id={provider_id}')

    # Build existing sets for dedup
    existing_urls = set(r[0] for r in db.execute('SELECT stream_url FROM channels').fetchall())
    existing_names = set(r[0].lower() for r in db.execute('SELECT LOWER(name) FROM channels').fetchall())

    def insert_channel(name, logo, group, url):
        db.execute(
            'INSERT OR IGNORE INTO channels (provider_id, name, logo, group_name, stream_url) VALUES (?, ?, ?, ?, ?)',
            (provider_id, name, logo, group, url)
        )

    # Import by country
    if MODE_ALL or MODE_COUNTRIES:
        print('\n=== IMPORTANDO POR PAIS ===\n')
        for country in COUNTRIES:
            try:
                url = f'https://iptv-org.github.io/iptv/countries/{country}.m3u'
                content = fetch_url(url)
                channels = parse_m3u(content)

                imported = 0
                skipped = 0
                for ch in channels:
                    if ch['url'] in existing_urls or ch['name'].lower() in existing_names:
                        skipped += 1
                        continue
                    insert_channel(ch['name'], ch['logo'], ch['group'], ch['url'])
                    existing_urls.add(ch['url'])
                    existing_names.add(ch['name'].lower())
                    imported += 1

                total_imported += imported
                total_skipped += skipped
                print(f'  {country.upper()}: +{imported} importados, {skipped} duplicados')

                # Small delay to be polite
                time.sleep(0.3)
            except Exception as e:
                print(f'  {country.upper()}: ERROR - {e}')

    # Import by category (filter for real stream URLs)
    if MODE_ALL:
        print('\n=== IMPORTANDO POR CATEGORIA ===\n')
        for cat in CATEGORIES:
            try:
                url = f'https://iptv-org.github.io/iptv/categories/{cat}.m3u'
                content = fetch_url(url)
                channels = parse_m3u(content)

                imported = 0
                for ch in channels:
                    if ch['url'] in existing_urls or ch['name'].lower() in existing_names:
                        continue
                    # Skip non-stream URLs
                    if not re.search(r'\.(m3u8?|ts|mp4|flv|avi)(\?|$)', ch['url'], re.I):
                        if 'live' not in ch['url'].lower():
                            continue
                    insert_channel(ch['name'], ch['logo'], ch['group'], ch['url'])
                    existing_urls.add(ch['url'])
                    existing_names.add(ch['name'].lower())
                    imported += 1

                total_imported += imported
                if imported > 0:
                    print(f'  {cat}: +{imported} importados')
                time.sleep(0.3)
            except Exception as e:
                pass  # Category might not exist

    db.commit()
    return total_imported, total_skipped


def generate_backups(db):
    print('\n=== GENERANDO BACKUPS AUTOMATICOS ===\n')

    plan_channels = db.execute('''
        SELECT DISTINCT pc.channel_id, c.name, c.group_name
        FROM plan_channels pc
        JOIN channels c ON c.id = pc.channel_id
    ''').fetchall()

    if not plan_channels:
        print('  No hay canales en planes para generar backups.')
        return 0

    created = 0
    for pc_id, pc_name, pc_group in plan_channels:
        existing_count = db.execute(
            'SELECT COUNT(*) FROM channel_backups WHERE channel_id = ?', (pc_id,)
        ).fetchone()[0]
        if existing_count >= 3:
            continue

        candidates = db.execute('''
            SELECT c.id
            FROM channels c
            LEFT JOIN channel_health h ON h.channel_id = c.id
            WHERE c.group_name = ?
            AND c.id != ?
            AND c.is_active = 1
            AND (h.is_alive IS NULL OR h.is_alive = 1)
            AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?)
            ORDER BY RANDOM()
            LIMIT 5
        ''', (pc_group, pc_id, pc_id)).fetchall()

        priority = existing_count
        for (cand_id,) in candidates:
            db.execute(
                'INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)',
                (pc_id, cand_id, priority)
            )
            priority += 1
            created += 1

    db.commit()
    print(f'  {created} backups creados para {len(plan_channels)} canales en planes.')
    return created


def test_sources():
    print('=== PROBANDO FUENTES M3U ===\n')
    for country in COUNTRIES[:10]:
        try:
            url = f'https://iptv-org.github.io/iptv/countries/{country}.m3u'
            content = fetch_url(url)
            channels = parse_m3u(content)
            print(f'  {country.upper()}: {len(channels)} canales - OK')
        except Exception as e:
            print(f'  {country.upper()}: ERROR - {e}')
        time.sleep(0.2)


def main():
    print('=== IPTV MASSIVE IMPORTER v2 (Python) ===')
    print(f'Fecha: {time.strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'DB: {DB_PATH}')

    if not os.path.exists(DB_PATH):
        print(f'ERROR: DB not found at {DB_PATH}')
        sys.exit(1)

    db = sqlite3.connect(DB_PATH, timeout=60)
    db.execute('PRAGMA journal_mode = WAL')
    db.execute('PRAGMA busy_timeout = 30000')

    before = db.execute('SELECT COUNT(*) FROM channels').fetchone()[0]
    print(f'Canales actuales: {before}')

    if MODE_TEST:
        test_sources()
        db.close()
        return

    if MODE_ALL or MODE_COUNTRIES:
        imported, skipped = import_channels(db)
        print(f'\n[OK] Total importados: {imported}')
        print(f'     Total duplicados: {skipped}')

    if MODE_ALL or MODE_BACKUPS:
        generate_backups(db)

    after = db.execute('SELECT COUNT(*) FROM channels').fetchone()[0]
    plan_ch = db.execute('SELECT COUNT(DISTINCT channel_id) FROM plan_channels').fetchone()[0]
    backups = db.execute('SELECT COUNT(*) FROM channel_backups').fetchone()[0]
    groups = db.execute('SELECT COUNT(DISTINCT group_name) FROM channels').fetchone()[0]

    print('\n=== ESTADO FINAL ===')
    print(f'Total canales en DB: {after} (+{after - before})')
    print(f'Canales en planes: {plan_ch}')
    print(f'Backups configurados: {backups}')
    print(f'Grupos: {groups}')

    db.close()


if __name__ == '__main__':
    main()
