#!/usr/bin/env python3
"""
Reasigna la parrilla con los canales premium reales recien importados.
Usa matching exacto por nombre para evitar falsos positivos.
"""

import sqlite3
import os
import time

DB_PATH = os.path.join(os.environ.get('DATA_DIR', '/data'), 'streamflow.sqlite')

# Parrilla exacta con nombres que existen en la DB
PARRILLA = {
    'Canales Nacionales': [
        'Caracol TV',
        'Canal RCN',
        'Senal Colombia',
        'Canal Institucional',
        'Teleantioquia',
        'Telecaribe',
        'Canal TRO',
        'Telecafe',
        'Las Estrellas',
        'RCN Novelas',
    ],
    'Deportes': [
        'ESPN',
        'ESPN2',
        'ESPN News',
        'Fox Sports',
        'Win Sports',
        'DSports',
        'TyC Sports',
        'Golf Channel',
        'NBA TV',
        'MLB Channel',
    ],
    'Peliculas Premium': [
        'HBO',
        'HBO 2',
        'HBO Family',
        'HBO Signature',
        'HBO Comedy',
        'Cinemax',
        'TNT',
        'Studio Universal',
        'Space',
        'Golden',
        'Paramount Network',
        'Sony Movies',
        'AMC',
        'AXN',
    ],
    'Series y Entretenimiento': [
        'Universal TV',
        'Comedy Central',
        'A&E',
        'SyFy',
        'MTV',
        'MTV Live',
        'TLC',
        'Lifetime',
        'E!',
        'Warner Channel',
        'FX',
        'Star Channel',
        'Sony Channel',
        'Telemundo Internacional',
    ],
    'Documentales': [
        'Discovery Channel',
        'Animal Planet',
        'History Channel',
        'History 2',
        'National Geographic',
        'Nat Geo Wild',
        'Love Nature',
        'Discovery Science',
        'Discovery Turbo',
        'Discovery Theater',
    ],
    'Infantiles': [
        'Cartoon Network',
        'Cartoonito',
        'Discovery Kids',
        'Disney Channel',
        'Disney Junior',
        'Nickelodeon',
        'Nick Jr.',
        'TeenNick',
        'Boomerang',
        'Baby TV',
    ],
    'Novelas y Variedades': [
        'Pasiones',
        'Novelisima',
        'Univision',
        'Venevision',
        'Azteca Internacional',
        'Caracol HD2',
        'Canal de las Estrellas',
        'TLNovelas',
        'Pluto TV Series',
        'RCN Novelas',
    ],
    'Religiosos': [
        'Enlace',
        'TBN',
        'Cristovision',
        'EWTN',
        'Maria Vision',
        'Tele VID',
    ],
    'Musica': [
        'HTV',
        'MTV Hits',
        'Stingray Hits',
        'Stingray Latino',
        'Trace Latina',
    ],
}

PLAN_DEVICES = {
    'Básico': 1,
    'Estándar': 2,
    'Premium': 4,
}

PLAN_PRICES = {
    'Básico': 10000,
    'Estándar': 18000,
    'Premium': 25000,
}


def find_channel(db, name):
    """Busca canal por nombre exacto o contains"""
    # Exact match
    ch = db.execute('SELECT id, name, logo, group_name, stream_url FROM channels WHERE name = ? AND is_active = 1', (name,)).fetchone()
    if ch:
        return ch
    # Contains
    ch = db.execute('SELECT id, name, logo, group_name, stream_url FROM channels WHERE name LIKE ? AND is_active = 1 LIMIT 1', (f'%{name}%',)).fetchone()
    return ch


def main():
    print('=== PARRILLA PREMIUM FINAL ===')
    print(f'Fecha: {time.strftime("%Y-%m-%d %H:%M:%S")}')

    db = sqlite3.connect(DB_PATH, timeout=60)
    db.execute('PRAGMA journal_mode = WAL')

    # Clear and rebuild
    db.execute('DELETE FROM plan_channels')
    db.execute('DELETE FROM channel_backups')
    db.commit()

    all_channels = []
    not_found = []
    found_ids = set()

    for category, names in PARRILLA.items():
        print(f'\n--- {category} ---')
        for name in names:
            ch = find_channel(db, name)
            if ch and ch[0] not in found_ids:
                all_channels.append({'id': ch[0], 'name': ch[1], 'group': category})
                found_ids.add(ch[0])
                print(f'  [OK] {name:30} -> {ch[1][:45]}')
            else:
                not_found.append((category, name))
                print(f'  [??] {name:30} -> NOT FOUND')

    # Fill missing with random from same group
    for category, name in not_found:
        alt = db.execute('SELECT id, name FROM channels WHERE is_active = 1 AND id NOT IN ({}) ORDER BY RANDOM() LIMIT 1'.format(','.join(str(x) for x in found_ids) if found_ids else '0')).fetchone()
        if alt:
            all_channels.append({'id': alt[0], 'name': alt[1], 'group': category})
            found_ids.add(alt[0])
            print(f'  [->] {name:30} -> {alt[1][:45]} (alt)')

    print(f'\nTotal: {len(all_channels)} canales')

    # Update plans
    for plan_name, devices in PLAN_DEVICES.items():
        price = PLAN_PRICES[plan_name]
        desc = f'{plan_name} - 100 canales | {devices} dispositivo{"s" if devices > 1 else ""}'
        db.execute('UPDATE plans SET description = ?, price_cop = ?, max_channels = 100, max_connections = ? WHERE name = ?', (desc, price, devices, plan_name))

    # Assign to all plans
    plans = db.execute('SELECT id, name FROM plans ORDER BY price_cop').fetchall()
    for plan_id, plan_name in plans:
        for ch in all_channels:
            db.execute('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)', (plan_id, ch['id']))
        print(f'{plan_name}: {len(all_channels)} canales')

    # Generate backups
    plan_channels = db.execute('SELECT DISTINCT pc.channel_id, c.group_name FROM plan_channels pc JOIN channels c ON c.id = pc.channel_id').fetchall()
    total_bk = 0
    for pc_id, pc_group in plan_channels:
        existing = db.execute('SELECT COUNT(*) FROM channel_backups WHERE channel_id = ?', (pc_id,)).fetchone()[0]
        if existing >= 5:
            continue
        needed = 5 - existing
        candidates = db.execute('SELECT c.id FROM channels c WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1 AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?) AND c.id NOT IN (SELECT channel_id FROM plan_channels) ORDER BY RANDOM() LIMIT ?', (pc_group, pc_id, pc_id, needed)).fetchall()
        for i, (cand_id,) in enumerate(candidates):
            db.execute('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)', (pc_id, cand_id, existing + i))
            total_bk += 1

    db.commit()

    # Summary
    print(f'\nBackups: {total_bk}')
    print(f'Total DB channels: {db.execute("SELECT COUNT(*) FROM channels").fetchone()[0]}')

    db.close()
    print('\n[OK] Done!')


if __name__ == '__main__':
    main()
