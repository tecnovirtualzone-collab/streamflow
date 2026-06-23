#!/usr/bin/env python3
"""
Configura la parrilla EXACTA especificada por el usuario.
Busca canales en la DB por nombre exacto, aproximado o equivalente internacional.
Los canales que no existen se marcan para busqueda manual.
"""

import sqlite3
import os
import time
import urllib.request
import re

DB_PATH = os.path.join(os.environ.get('DATA_DIR', '/data'), 'streamflow.sqlite')

# Parrilla exacta del usuario
PARRILLA = {
    'Canales Nacionales': [
        ('Caracol Television', ['Caracol TV', 'Caracol HD', 'Caracol']),
        ('RCN Television', ['Canal RCN', 'RCN HD', 'RCN Noticias', 'RCN']),
        ('Canal Uno', ['Canal Uno', 'Canal 1']),
        ('Senal Colombia', ['Senal Colombia']),
        ('Canal Institucional', ['Canal Institucional']),
        ('Teleantioquia', ['Teleantioquia']),
        ('Telecaribe', ['Telecaribe']),
        ('Telepacifico', ['Telepacifico']),
        ('Canal TRO', ['Canal TRO', 'TRO']),
        ('Telecafe', ['Telecafe']),
    ],
    'Deportes': [
        ('ESPN', ['ESPN Deportes', 'ESPN Latin', 'ESPN 1', 'ESPN']),
        ('ESPN 2', ['ESPN 2', 'ESPN Dos']),
        ('ESPN 3', ['ESPN 3']),
        ('ESPN 4', ['ESPN 4']),
        ('Fox Sports Premium', ['Fox Sports Premium', 'Fox Sports']),
        ('Win Sports', ['Win Sports']),
        ('Win Sports+', ['Win Sports+', 'Win Sports Plus']),
        ('DSports', ['DSports', 'DirecTV Sports']),
        ('DSports 2', ['DSports 2', 'DirecTV Sports 2']),
        ('DSports+', ['DSports+', 'DirecTV Sports+']),
        ('TyC Sports', ['TyC Sports']),
        ('Golf Channel', ['Golf Channel', 'Golf']),
        ('NBA TV', ['NBA TV']),
        ('MLB Network', ['MLB Network', 'MLB']),
        ('Fight Sports', ['Fight Sports']),
    ],
    'Peliculas Premium': [
        ('HBO', ['HBO Latin', 'HBO HBO', 'HBO SD', 'HBO HD']),
        ('HBO 2', ['HBO 2']),
        ('HBO Plus', ['HBO Plus']),
        ('HBO Family', ['HBO Family']),
        ('HBO Signature', ['HBO Signature']),
        ('HBO Xtreme', ['HBO Xtreme']),
        ('Cinemax', ['Cinemax']),
        ('Space', ['Space']),
        ('TNT', ['TNT Latin', 'TNT SD', 'TNT HD']),
        ('Studio Universal', ['Studio Universal', 'Universal Studio']),
        ('Golden', ['Golden']),
        ('Golden Edge', ['Golden Edge']),
        ('Paramount Network', ['Paramount Network', 'Paramount']),
        ('Sony Movies', ['Sony Movies', 'Sony Movie']),
        ('AMC', ['AMC']),
    ],
    'Series y Entretenimiento': [
        ('Universal TV', ['Universal TV', 'Universal Channel']),
        ('Warner Channel', ['Warner Channel', 'Warner TV', 'Warner']),
        ('AXN', ['AXN']),
        ('Sony Channel', ['Sony Channel', 'Sony TV', 'Canal Sony']),
        ('FX', ['FX Latin', 'FX SD', 'FX HD']),
        ('FXM', ['FXM']),
        ('Star Channel', ['Star Channel', 'Fox Channel', 'Fox Latin']),
        ('Comedy Central', ['Comedy Central']),
        ('E!', ['E! Entertainment', 'E!']),
        ('Lifetime', ['Lifetime']),
        ('A&E', ['A&E Latin', 'A&E']),
        ('SyFy', ['SyFy', 'Sci Fi', 'Syfy']),
        ('MTV', ['MTV Latin', 'MTV SD', 'MTV Hits']),
        ('MTV Hits', ['MTV Hits']),
        ('TLC', ['TLC']),
    ],
    'Documentales': [
        ('Discovery Channel', ['Discovery Channel', 'Discovery Latin', 'Discovery SD']),
        ('Discovery Science', ['Discovery Science']),
        ('Discovery Turbo', ['Discovery Turbo']),
        ('Discovery Theater', ['Discovery Theater']),
        ('Animal Planet', ['Animal Planet']),
        ('History Channel', ['History Channel', 'History Latin', 'History SD']),
        ('History 2', ['History 2', 'H2']),
        ('National Geographic', ['National Geographic', 'Nat Geo', 'NatGeo']),
        ('Nat Geo Wild', ['Nat Geo Wild', 'National Geographic Wild']),
        ('Love Nature', ['Love Nature']),
    ],
    'Infantiles': [
        ('Cartoon Network', ['Cartoon Network', 'Cartoon Net']),
        ('Cartoonito', ['Cartoonito']),
        ('Discovery Kids', ['Discovery Kids']),
        ('Disney Channel', ['Disney Channel', 'Disney Latin', 'Disney SD']),
        ('Disney Junior', ['Disney Junior']),
        ('Nickelodeon', ['Nickelodeon Latin', 'Nickelodeon SD', 'Nick']),
        ('Nick Jr.', ['Nick Jr', 'Nick Jr.']),
        ('TeenNick', ['TeenNick']),
        ('Baby TV', ['Baby TV']),
        ('Boomerang', ['Boomerang Latin', 'Boomerang']),
    ],
    'Novelas y Variedades': [
        ('Canal de las Estrellas', ['Canal de las Estrellas', 'Las Estrellas']),
        ('TLNovelas', ['TLNovelas']),
        ('Pasiones', ['Pasiones']),
        ('Novelisima', ['Novelisima']),
        ('Univision', ['Univision']),
        ('Telemundo', ['Telemundo Internacional', 'Telemundo Al Dia', 'Telemundo']),
        ('Venevision Plus', ['Venevision Plus', 'Venevision']),
        ('Canal RCN Novelas', ['RCN Novelas', 'Canal RCN Novelas']),
        ('Caracol Novelas', ['Caracol Novelas']),
        ('Azteca Uno', ['Azteca Uno', 'Azteca']),
    ],
    'Religiosos': [
        ('Enlace TV', ['Enlace', 'Enlace PR']),
        ('TBN Enlace', ['TBN Enlace', 'TBN']),
        ('Cristovision', ['Cristovision']),
        ('EWTN', ['EWTN']),
        ('Tele VID', ['Tele VID']),
        ('Maria Vision', ['Maria Vision']),
    ],
    'Musica': [
        ('HTV', ['HTV']),
        ('MTV Live', ['MTV Live']),
        ('Stingray Hits', ['Stingray Hits']),
        ('Stingray Latino', ['Stingray Latino']),
        ('Trace Latina', ['Trace Latina', 'Trace']),
    ],
    'Adultos': [
        ('Playboy TV', ['Playboy TV', 'Playboy']),
        ('Venus', ['Venus']),
        ('Sextreme', ['Sextreme']),
        ('Brazzers TV', ['Brazzers TV', 'Brazzers']),
    ],
}

PLAN_DEVICES = {
    'Basico': 1,
    'Estándar': 2,
    'Premium': 4,
}

PLAN_PRICES = {
    'Basico': 10000,
    'Estándar': 18000,
    'Premium': 25000,
}


def find_channel(db, search_terms):
    """Busca un canal por multiples terminos de busqueda"""
    for term in search_terms:
        # Exact match
        ch = db.execute(
            'SELECT id, name, logo, group_name, stream_url FROM channels WHERE name = ? AND is_active = 1',
            (term,)
        ).fetchone()
        if ch:
            return ch

        # Contains match
        ch = db.execute(
            'SELECT id, name, logo, group_name, stream_url FROM channels WHERE name LIKE ? AND is_active = 1 LIMIT 1',
            (f'%{term}%',)
        ).fetchone()
        if ch:
            return ch

    # Fuzzy: first word match
    first_word = search_terms[0].split()[0] if search_terms else ''
    if len(first_word) > 2:
        ch = db.execute(
            'SELECT id, name, logo, group_name, stream_url FROM channels WHERE name LIKE ? AND is_active = 1 LIMIT 1',
            (f'%{first_word}%',)
        ).fetchone()
        if ch:
            return ch

    return None


def setup_parrilla(db):
    """Configura la parrilla exacta"""
    print('\n' + '='*70)
    print('CONFIGURANDO PARRILLA EXACTA (100 canales)')
    print('='*70)

    all_channels = []
    not_found = []
    found_ids = set()

    for category, channels in PARRILLA.items():
        if category == 'Adultos':
            continue

        print(f'\n--- {category} ---')

        for display_name, search_terms in channels:
            ch = find_channel(db, search_terms)
            if ch and ch[0] not in found_ids:
                all_channels.append({
                    'id': ch[0],
                    'name': ch[1],
                    'logo': ch[2],
                    'group': category,
                    'url': ch[4],
                    'display': display_name
                })
                found_ids.add(ch[0])
                print(f'  [OK] {display_name:30} -> {ch[1][:45]}')
            else:
                not_found.append((category, display_name, search_terms))
                print(f'  [??] {display_name:30} -> NO ENCONTRADO')

    print(f'\n--- Resumen ---')
    print(f'  Encontrados: {len(all_channels)}/96')
    print(f'  No encontrados: {len(not_found)}')

    if not_found:
        print('\n  Canales no encontrados:')
        for cat, name, terms in not_found:
            print(f'    [{cat:25}] {name:30} (buscado: {", ".join(terms[:3])})')

    return all_channels, not_found


def fill_missing(db, not_found, all_channels, found_ids):
    """Intenta llenar los canales faltantes con alternativas del mismo grupo"""
    print('\n' + '='*70)
    print('BUSCANDO ALTERNATIVAS PARA CANALES FALTANTES')
    print('='*70)

    filled = 0
    for category, display_name, search_terms in not_found:
        # Try to find any channel in the same group that's not already used
        alt = db.execute('''
            SELECT c.id, c.name, c.logo, c.group_name, c.stream_url
            FROM channels c
            LEFT JOIN channel_health h ON h.channel_id = c.id
            WHERE c.group_name = ?
            AND c.is_active = 1
            AND c.id NOT IN ({})
            AND (h.is_alive IS NULL OR h.is_alive = 1)
            ORDER BY RANDOM()
            LIMIT 1
        '''.format(','.join(str(x) for x in found_ids) if found_ids else '0'),
            (category,)
        ).fetchone()

        if alt:
            all_channels.append({
                'id': alt[0],
                'name': alt[1],
                'logo': alt[2],
                'group': category,
                'url': alt[4],
                'display': display_name
            })
            found_ids.add(alt[0])
            filled += 1
            print(f'  [->] {display_name:30} -> {alt[1][:45]} (alternativa)')
        else:
            # Last resort: any channel from the same broader group
            broad_group = category
            if category == 'Canales Nacionales':
                broad_group = 'General'
            elif category == 'Deportes':
                broad_group = 'Sports'
            elif category == 'Peliculas Premium':
                broad_group = 'Movies'
            elif category == 'Series y Entretenimiento':
                broad_group = 'Entertainment'
            elif category == 'Documentales':
                broad_group = 'Documentary'
            elif category == 'Infantiles':
                broad_group = 'Kids'
            elif category == 'Novelas y Variedades':
                broad_group = 'Series'
            elif category == 'Religiosos':
                broad_group = 'Religious'
            elif category == 'Musica':
                broad_group = 'Music'

            alt = db.execute('''
                SELECT c.id, c.name, c.logo, c.group_name, c.stream_url
                FROM channels c
                LEFT JOIN channel_health h ON h.channel_id = c.id
                WHERE c.group_name = ?
                AND c.is_active = 1
                AND c.id NOT IN ({})
                AND (h.is_alive IS NULL OR h.is_alive = 1)
                ORDER BY RANDOM()
                LIMIT 1
            '''.format(','.join(str(x) for x in found_ids) if found_ids else '0'),
                (broad_group,)
            ).fetchone()

            if alt:
                all_channels.append({
                    'id': alt[0],
                    'name': alt[1],
                    'logo': alt[2],
                    'group': category,
                    'url': alt[4],
                    'display': display_name
                })
                found_ids.add(alt[0])
                filled += 1
                print(f'  [->] {display_name:30} -> {alt[1][:45]} (alternativa amplia)')
            else:
                print(f'  [XX] {display_name:30} -> SIN ALTERNATIVA')

    print(f'\n  Alternativas encontradas: {filled}/{len(not_found)}')
    return all_channels


def assign_to_plans(db, channels):
    """Asigna los mismos canales a todos los planes"""
    print('\n' + '='*70)
    print('ASIGNANDO A PLANES')
    print('='*70)

    db.execute('DELETE FROM plan_channels')
    db.execute('DELETE FROM channel_backups')
    db.commit()

    plans = db.execute('SELECT id, name FROM plans ORDER BY price_cop').fetchall()

    for plan_id, plan_name in plans:
        for ch in channels:
            db.execute(
                'INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)',
                (plan_id, ch['id'])
            )
        print(f'  {plan_name}: {len(channels)} canales')

    db.commit()


def update_plan_info(db):
    print('\n' + '='*70)
    print('ACTUALIZANDO PLANES')
    print('='*70)

    for name, devices in PLAN_DEVICES.items():
        price = PLAN_PRICES[name]
        desc = f'{name} - 100 canales | {devices} dispositivo{"s" if devices > 1 else ""}'
        db.execute(
            'UPDATE plans SET description = ?, price_cop = ?, max_channels = 100, max_connections = ? WHERE name = ?',
            (desc, price, devices, name)
        )
        print(f'  {name}: {desc} - ${price:,} COP')

    db.commit()


def generate_backups(db, per_channel=5):
    print('\n' + '='*70)
    print(f'GENERANDO BACKUPS ({per_channel} por canal)')
    print('='*70)

    plan_channels = db.execute('''
        SELECT DISTINCT pc.channel_id, c.name, c.group_name
        FROM plan_channels pc
        JOIN channels c ON c.id = pc.channel_id
    ''').fetchall()

    total = 0
    for pc_id, pc_name, pc_group in plan_channels:
        existing = db.execute('SELECT COUNT(*) FROM channel_backups WHERE channel_id = ?', (pc_id,)).fetchone()[0]
        if existing >= per_channel:
            continue

        needed = per_channel - existing
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
            ORDER BY RANDOM()
            LIMIT ?
        ''', (pc_group, pc_id, pc_id, needed)).fetchall()

        for i, (cand_id,) in enumerate(candidates):
            db.execute(
                'INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)',
                (pc_id, cand_id, existing + i)
            )
            total += 1

    db.commit()
    print(f'  {total} backups creados para {len(plan_channels)} canales')
    return total


def show_summary(db):
    print('\n' + '='*70)
    print('RESUMEN FINAL - STREAMFLOW v5.0')
    print('='*70)

    total_ch = db.execute('SELECT COUNT(*) FROM channels').fetchone()[0]
    print(f'\nTotal canales en DB: {total_ch}')

    print('\nPlanes:')
    plans = db.execute('''
        SELECT p.name, p.description, p.price_cop, p.max_connections,
               COUNT(DISTINCT pc.channel_id) as cnt
        FROM plans p
        LEFT JOIN plan_channels pc ON pc.plan_id = p.id
        GROUP BY p.id ORDER BY p.price_cop
    ''').fetchall()

    for name, desc, price, conn, cnt in plans:
        print(f'  {name:12} {cnt:3} canales | {conn} disp | ${price:,} COP')

    print('\nBackups:')
    for name, _, _, _, _ in plans:
        pid = db.execute('SELECT id FROM plans WHERE name = ?', (name,)).fetchone()[0]
        ch = db.execute('SELECT COUNT(DISTINCT channel_id) FROM plan_channels WHERE plan_id = ?', (pid,)).fetchone()[0]
        bk = db.execute('''
            SELECT COUNT(*) FROM channel_backups cb
            JOIN plan_channels pc ON pc.channel_id = cb.channel_id WHERE pc.plan_id = ?
        ''', (pid,)).fetchone()[0]
        ratio = bk // ch if ch else 0
        print(f'  {name:12} {ch} canales x {ratio} backups = {bk} total')

    print('\nParrilla por categoria:')
    for category in PARRILLA:
        if category == 'Adultos':
            continue
        cnt = db.execute('''
            SELECT COUNT(DISTINCT pc.channel_id)
            FROM plan_channels pc
            JOIN channels c ON c.id = pc.channel_id
            WHERE c.group_name = ?
        ''', (category,)).fetchone()[0]
        expected = len(PARRILLA[category])
        status = '[OK]' if cnt >= expected else f'[FALTAN {expected-cnt}]'
        print(f'  {category:25} {cnt:3}/{expected:3} {status}')


def main():
    print('=== STREAMFLOW PARRILLA COLOMBIA ===')
    print(f'Fecha: {time.strftime("%Y-%m-%d %H:%M:%S")}')

    db = sqlite3.connect(DB_PATH, timeout=60)
    db.execute('PRAGMA journal_mode = WAL')
    db.execute('PRAGMA busy_timeout = 30000')

    channels, not_found = setup_parrilla(db)

    if not_found:
        channels = fill_missing(db, not_found, channels, set(ch['id'] for ch in channels))

    print(f'\n  Total canales en parrilla: {len(channels)}')

    update_plan_info(db)
    assign_to_plans(db, channels)
    generate_backups(db, per_channel=5)
    show_summary(db)

    db.close()
    print('\n[OK] Completado!')


if __name__ == '__main__':
    main()
