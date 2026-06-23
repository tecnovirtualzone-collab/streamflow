#!/usr/bin/env python3
"""
Reestructura los planes de StreamFlow:
- 100 canales colombianos en TODOS los planes
- Diferencia entre planes = numero de dispositivos
- Backups automaticos para cada canal
"""

import sqlite3
import os
import urllib.request
import re
import time

DB_PATH = os.path.join(os.environ.get('DATA_DIR', '/data'), 'streamflow.sqlite')

COLOMBIA_GROUPS = ['General', 'Entertainment', 'News', 'Sports', 'Movies', 'Series',
                   'Kids', 'Music', 'Religious', 'Documentary', 'Education', 'Lifestyle',
                   'Culture', 'Comedy', 'Family', 'Animation', 'Classic']

PLAN_DEVICE_LIMITS = {
    'Basico': 1,
    'Estandar': 2,
    'Premium': 4
}


def fetch_m3u(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'StreamFlow/2.0'})
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
            country_m = re.search(r'tvg-country="([^"]*)"', trimmed)
            current = {
                'name': name_m.group(1).strip() if name_m else '',
                'logo': logo_m.group(1) if logo_m else '',
                'group': group_m.group(1) if group_m else 'General',
                'country': country_m.group(1) if country_m else '',
            }
        elif trimmed and not trimmed.startswith('#') and current.get('name'):
            channels.append({**current, 'url': trimmed})
            current = {}
    return channels


def import_more_colombian_channels(db):
    """Importa canales colombianos adicionales de iptv-org"""
    print('\n=== IMPORTANDO CANALES COLOMBIANOS ADICIONALES ===\n')

    # Get or create provider
    row = db.execute("SELECT id FROM providers WHERE name = 'IPTV Gratis (iptv.org)'").fetchone()
    provider_id = row[0] if row else db.execute(
        "INSERT INTO providers (name, url, username, password) VALUES (?, ?, ?, ?)",
        ('IPTV Gratis (iptv.org)', 'https://iptv-org.github.io', '', '')
    ).lastrowid

    existing_urls = set(r[0] for r in db.execute('SELECT stream_url FROM channels').fetchall())
    existing_names = set(r[0].lower() for r in db.execute('SELECT LOWER(name) FROM channels').fetchall())

    total_imported = 0

    # Import from iptv-org countries that have Colombian content
    for country in ['co', 'co']:  # Colombia
        try:
            url = f'https://iptv-org.github.io/iptv/countries/{country}.m3u'
            content = fetch_m3u(url)
            channels = parse_m3u(content)

            imported = 0
            for ch in channels:
                if ch['url'] in existing_urls or ch['name'].lower() in existing_names:
                    continue
                db.execute(
                    'INSERT OR IGNORE INTO channels (provider_id, name, logo, group_name, stream_url) VALUES (?, ?, ?, ?, ?)',
                    (provider_id, ch['name'], ch['logo'], ch['group'], ch['url'])
                )
                existing_urls.add(ch['url'])
                existing_names.add(ch['name'].lower())
                imported += 1

            total_imported += imported
            print(f'  {country.upper()}: +{imported} importados')
        except Exception as e:
            print(f'  {country.upper()}: ERROR - {e}')

    # Also import from categories that likely have Colombian content
    for cat in ['news', 'sports', 'entertainment', 'movies', 'series', 'kids', 'music', 'religious']:
        try:
            url = f'https://iptv-org.github.io/iptv/categories/{cat}.m3u'
            content = fetch_m3u(url)
            channels = parse_m3u(content)

            imported = 0
            for ch in channels:
                # Only take channels that are likely Colombian (CO country tag or .co domain)
                is_colombian = (
                    ch.get('country', '').upper() == 'CO' or
                    '.co/' in ch['url'].lower() or
                    'colombia' in ch['name'].lower() or
                    'bogota' in ch['name'].lower() or
                    'medellin' in ch['name'].lower() or
                    'cali' in ch['name'].lower() or
                    'caracol' in ch['name'].lower() or
                    'rcn' in ch['name'].lower() or
                    'canal 13' in ch['name'].lower() or
                    'senal colombia' in ch['name'].lower() or
                    'telecaribe' in ch['name'].lower() or
                    'telepacifico' in ch['name'].lower() or
                    'teleantioquia' in ch['name'].lower() or
                    'telecafe' in ch['name'].lower() or
                    'teleislas' in ch['name'].lower() or
                    'canal capital' in ch['name'].lower() or
                    'canal uno' in ch['name'].lower() or
                    'red+' in ch['name'].lower() or
                    'win sports' in ch['name'].lower() or
                    'win+' in ch['name'].lower()
                )
                if not is_colombian:
                    continue
                if ch['url'] in existing_urls or ch['name'].lower() in existing_names:
                    continue
                db.execute(
                    'INSERT OR IGNORE INTO channels (provider_id, name, logo, group_name, stream_url) VALUES (?, ?, ?, ?, ?)',
                    (provider_id, ch['name'], ch['logo'], ch['group'], ch['url'])
                )
                existing_urls.add(ch['url'])
                existing_names.add(ch['name'].lower())
                imported += 1

            if imported > 0:
                total_imported += imported
                print(f'  {cat}: +{imported} colombianos importados')
        except Exception as e:
            pass

    db.commit()
    print(f'\n  Total importados: {total_imported}')
    return total_imported


def get_colombian_channels(db, limit=150):
    """Obtiene canales colombianos de la DB"""
    # First: channels from Colombia group or with CO country tag
    channels = db.execute('''
        SELECT DISTINCT c.id, c.name, c.logo, c.group_name, c.stream_url
        FROM channels c
        WHERE c.is_active = 1
        AND (
            c.group_name IN ('General', 'Entertainment', 'News', 'Sports', 'Movies', 'Series',
                           'Kids', 'Music', 'Religious', 'Documentary', 'Education', 'Lifestyle',
                           'Culture', 'Comedy', 'Family', 'Animation', 'Classic')
        )
        AND (
            c.name LIKE '%(CO)%' OR
            c.name LIKE '%Colombia%' OR
            c.name LIKE '%Bogota%' OR
            c.name LIKE '%Medellin%' OR
            c.name LIKE '%Cali%' OR
            c.name LIKE '%Caracol%' OR
            c.name LIKE '%RCN%' OR
            c.name LIKE '%Canal 13%' OR
            c.name LIKE '%Senal Colombia%' OR
            c.name LIKE '%Telecaribe%' OR
            c.name LIKE '%Telepacifico%' OR
            c.name LIKE '%Teleantioquia%' OR
            c.name LIKE '%Telecafe%' OR
            c.name LIKE '%Canal Capital%' OR
            c.name LIKE '%Canal Uno%' OR
            c.name LIKE '%Blu Radio%' OR
            c.name LIKE '%W Radio%' OR
            c.name LIKE '%La FM%' OR
            c.name LIKE '%Caracol Radio%' OR
            c.name LIKE '%RCN Radio%' OR
            c.name LIKE '%Win Sports%' OR
            c.name LIKE '%Win+%' OR
            c.name LIKE '%Red+%' OR
            c.name LIKE '%Amaga%' OR
            c.name LIKE '%ATN %' OR
            c.name LIKE '%Aupur%' OR
            c.name LIKE '%Avivamiento%' OR
            c.name LIKE '%Bendicion Channel%' OR
            c.name LIKE '%Buenisima%' OR
            c.name LIKE '%BUM %' OR
            c.stream_url LIKE '%.co/%' OR
            c.stream_url LIKE '%colombia%' OR
            c.stream_url LIKE '%bogota%' OR
            c.stream_url LIKE '%medellin%' OR
            c.stream_url LIKE '%cali%'
        )
        ORDER BY
            CASE c.group_name
                WHEN 'General' THEN 1
                WHEN 'Entertainment' THEN 2
                WHEN 'News' THEN 3
                WHEN 'Sports' THEN 4
                WHEN 'Movies' THEN 5
                WHEN 'Series' THEN 6
                WHEN 'Kids' THEN 7
                WHEN 'Music' THEN 8
                WHEN 'Religious' THEN 9
                ELSE 10
            END,
            c.name
        LIMIT ?
    ''', (limit,)).fetchall()

    return channels


def get_all_co_tagged_channels(db, limit=150):
    """Obtiene todos los canales con tvg-country CO o nombres colombianos"""
    channels = db.execute('''
        SELECT DISTINCT c.id, c.name, c.logo, c.group_name, c.stream_url
        FROM channels c
        WHERE c.is_active = 1
        AND c.name REGEXP '(CO\)|Colombia|Bogota|Medellin|Cali|Caracol|RCN|Canal 13|Senal Colombia|Telecaribe|Telepacifico|Teleantioquia|Telecafe|Canal Capital|Canal Uno|Blu Radio|W Radio|La FM|Caracol Radio|RCN Radio|Win Sports'
        ORDER BY c.group_name, c.name
        LIMIT ?
    ''', (limit,)).fetchall()
    return channels


def populate_plans_with_colombian_channels(db):
    """Puebla todos los planes con los mismos 100 canales colombianos"""
    print('\n=== POBLANDO PLANES CON 100 CANALES COLOMBIANOS ===\n')

    # Limpiar asignaciones anteriores
    db.execute('DELETE FROM plan_channels')
    db.execute('DELETE FROM channel_backups')
    db.commit()

    # Obtener canales colombianos
    channels = get_colombian_channels(db, 150)

    if len(channels) < 100:
        print(f'  [WARN] Solo {len(channels)} canales colombianos encontrados, necesitamos 100')
        print('  Buscando mas canales con criterio ampliado...')

        # Ampliar busqueda: todos los canales de paises hispanos
        extra = db.execute('''
            SELECT DISTINCT c.id, c.name, c.logo, c.group_name, c.stream_url
            FROM channels c
            WHERE c.is_active = 1
            AND c.id NOT IN (SELECT id FROM channels WHERE name LIKE '%(CO)%' OR name LIKE '%Colombia%')
            AND c.group_name IN ('General', 'Entertainment', 'News', 'Sports', 'Movies', 'Series',
                               'Kids', 'Music', 'Religious', 'Documentary', 'Education', 'Lifestyle')
            ORDER BY RANDOM()
            LIMIT ?
        ''', (150 - len(channels),)).fetchall()

        channels = list(channels) + list(extra)
        print(f'  Total ampliado: {len(channels)} canales')

    # Tomar exactamente 100
    selected = channels[:100]

    print(f'\n  Canales seleccionados: {len(selected)}')
    print('\n  Distribucion por grupo:')
    group_counts = {}
    for ch in selected:
        g = ch[3]  # group_name
        group_counts[g] = group_counts.get(g, 0) + 1
    for g, cnt in sorted(group_counts.items(), key=lambda x: -x[1]):
        print(f'    {g:20} {cnt}')

    # Asignar los mismos 100 canales a TODOS los planes
    plans = db.execute('SELECT id, name, max_channels FROM plans ORDER BY price_cop').fetchall()

    for plan_id, plan_name, max_ch in plans:
        for ch in selected:
            db.execute(
                'INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)',
                (plan_id, ch[0])
            )
        print(f'\n  {plan_name}: {len(selected)} canales asignados')

    db.commit()
    return len(selected)


def update_plan_descriptions(db):
    """Actualiza las descripciones de los planes para reflejar dispositivos"""
    print('\n=== ACTUALIZANDO DESCRIPCIONES DE PLANES ===\n')

    updates = [
        ('Basico', 'Plan Basico - 100 canales colombianos | 1 dispositivo', 10000, 100, 1),
        ('Estandar', 'Plan Estandar - 100 canales colombianos | 2 dispositivos', 18000, 100, 2),
        ('Premium', 'Plan Premium - 100 canales colombianos | 4 dispositivos', 25000, 100, 4),
    ]

    for name, desc, price, max_ch, max_conn in updates:
        db.execute(
            'UPDATE plans SET description = ?, price_cop = ?, max_channels = ?, max_connections = ? WHERE name = ?',
            (desc, price, max_ch, max_conn, name)
        )
        print(f'  {name}: {desc}')

    db.commit()


def generate_backups(db, backups_per_channel=5):
    """Genera backups para todos los canales en planes"""
    print(f'\n=== GENERANDO BACKUPS ({backups_per_channel} por canal) ===\n')

    plan_channels = db.execute('''
        SELECT DISTINCT pc.channel_id, c.name, c.group_name
        FROM plan_channels pc
        JOIN channels c ON c.id = pc.channel_id
    ''').fetchall()

    total_created = 0

    for pc_id, pc_name, pc_group in plan_channels:
        existing = db.execute(
            'SELECT COUNT(*) FROM channel_backups WHERE channel_id = ?', (pc_id,)
        ).fetchone()[0]
        if existing >= backups_per_channel:
            continue

        needed = backups_per_channel - existing

        # Buscar backups del mismo grupo, preferiblemente colombianos
        candidates = db.execute('''
            SELECT c.id
            FROM channels c
            LEFT JOIN channel_health h ON h.channel_id = c.id
            WHERE c.group_name = ?
            AND c.id != ?
            AND c.is_active = 1
            AND (h.is_alive IS NULL OR h.is_alive = 1)
            AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?)
            AND c.id NOT IN (SELECT channel_id FROM plan_channels)
            ORDER BY
                CASE
                    WHEN c.name LIKE '%(CO)%' THEN 0
                    WHEN c.name LIKE '%Colombia%' THEN 1
                    ELSE 2
                END,
                RANDOM()
            LIMIT ?
        ''', (pc_group, pc_id, pc_id, needed)).fetchall()

        for i, (cand_id,) in enumerate(candidates):
            db.execute(
                'INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)',
                (pc_id, cand_id, existing + i)
            )
            total_created += 1

    db.commit()
    print(f'  {total_created} backups creados para {len(plan_channels)} canales')
    return total_created


def show_summary(db):
    print('\n' + '='*60)
    print('RESUMEN FINAL - STREAMFLOW v5.0 (COLOMBIA)')
    print('='*60)

    total_ch = db.execute('SELECT COUNT(*) FROM channels').fetchone()[0]
    co_ch = db.execute('''
        SELECT COUNT(*) FROM channels WHERE is_active = 1 AND (
            name LIKE '%(CO)%' OR name LIKE '%Colombia%' OR name LIKE '%Bogota%' OR
            name LIKE '%Medellin%' OR name LIKE '%Cali%' OR name LIKE '%Caracol%' OR
            name LIKE '%RCN%' OR name LIKE '%Senal Colombia%' OR name LIKE '%Telecaribe%' OR
            name LIKE '%Telepacifico%' OR name LIKE '%Teleantioquia%' OR name LIKE '%Canal Capital%' OR
            name LIKE '%Canal Uno%' OR name LIKE '%Blu Radio%' OR name LIKE '%Win Sports%' OR
            stream_url LIKE '%.co/%'
        )
    ''').fetchone()[0]

    print(f'\nBase de datos:')
    print(f'  Total canales: {total_ch}')
    print(f'  Canales colombianos: {co_ch}')

    print(f'\nPlanes (mismos 100 canales, diferencia en dispositivos):')
    plans = db.execute('''
        SELECT p.name, p.description, p.price_cop, p.max_channels, p.max_connections,
               COUNT(DISTINCT pc.channel_id) as ch_count
        FROM plans p
        LEFT JOIN plan_channels pc ON pc.plan_id = p.id
        GROUP BY p.id
        ORDER BY p.price_cop
    ''').fetchall()

    for name, desc, price, max_ch, max_conn, cnt in plans:
        price_str = f'${price:,} COP'
        print(f'  {name:12} {cnt:3} canales | {max_conn} dispositivo(s) | {price_str}')
        print(f'    {desc}')

    print(f'\nBackups:')
    for name, _, _, _, _, _ in plans:
        plan_id = db.execute('SELECT id FROM plans WHERE name = ?', (name,)).fetchone()[0]
        ch_in_plan = db.execute(
            'SELECT COUNT(DISTINCT channel_id) FROM plan_channels WHERE plan_id = ?', (plan_id,)
        ).fetchone()[0]
        backups = db.execute('''
            SELECT COUNT(*) FROM channel_backups cb
            JOIN plan_channels pc ON pc.channel_id = cb.channel_id
            WHERE pc.plan_id = ?
        ''', (plan_id,)).fetchone()[0]
        ratio = backups / ch_in_plan if ch_in_plan > 0 else 0
        print(f'  {name:12} {ch_in_plan} canales x {ratio:.1f} backups = {backups} total')

    # Verificar canales colombianos en planes
    co_in_plans = db.execute('''
        SELECT COUNT(DISTINCT pc.channel_id)
        FROM plan_channels pc
        JOIN channels c ON c.id = pc.channel_id
        WHERE c.name LIKE '%(CO)%' OR c.name LIKE '%Colombia%' OR c.name LIKE '%Caracol%'
           OR c.name LIKE '%RCN%' OR c.name LIKE '%Senal Colombia%' OR c.name LIKE '%Telecaribe%'
    ''').fetchone()[0]
    print(f'\n  Canales colombianos en planes: {co_in_plans}')


def main():
    print('=== STREAMFLOW COLOMBIA RESTRUCTURE ===')
    print(f'Fecha: {time.strftime("%Y-%m-%d %H:%M:%S")}')

    db = sqlite3.connect(DB_PATH, timeout=60)
    db.execute('PRAGMA journal_mode = WAL')
    db.execute('PRAGMA busy_timeout = 30000')
    # Enable REGEXP
    db.create_function('REGEXP', 2, lambda pattern, text: 1 if re.search(pattern, text, re.I) else 0)

    import_more_colombian_channels(db)
    update_plan_descriptions(db)
    n = populate_plans_with_colombian_channels(db)
    generate_backups(db, backups_per_channel=5)
    show_summary(db)

    db.close()
    print('\n[OK] Proceso completado!')


if __name__ == '__main__':
    main()
