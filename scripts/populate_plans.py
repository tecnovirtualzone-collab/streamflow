#!/usr/bin/env python3
"""
Puebla los planes de StreamFlow basandose en el catalogo de DIRECTV Colombia.
Distribuye canales por categoria en 3 planes: Basico, Estandar, Premium.

Plan Basico (40 canales) - equivalente a DIRECTV Familia+:
  - General, Noticias, Deportes, Entretenimiento, Infantil

Plan Estandar (70 canales) - equivalente a DIRECTV Plata HD:
  - Todo lo de Basico + Peliculas, Series, Musica, Documental

Plan Premium (100 canales) - equivalente a DIRECTV Oro HD:
  - Todo lo de Estandar + Religioso, Educacion, Lifestyle, Cultura, Comedia

Cada canal en plan tiene 3 canales de respaldo automaticos del mismo grupo.
"""

import sqlite3
import os
import sys
import time

DB_PATH = os.path.join(os.environ.get('DATA_DIR', '/data'), 'streamflow.sqlite')

# Mapeo de grupos de iptv.org a categorias estilo DIRECTV
PLAN_STRUCTURE = {
    'Básico': {
        'max_channels': 40,
        'groups': {
            'General': 12,
            'News': 8,
            'Sports': 7,
            'Entertainment': 7,
            'Kids': 6,
        }
    },
    'Estándar': {
        'max_channels': 70,
        'groups': {
            'General': 15,
            'News': 10,
            'Sports': 10,
            'Entertainment': 10,
            'Kids': 5,
            'Movies': 8,
            'Series': 7,
            'Music': 5,
        }
    },
    'Premium': {
        'max_channels': 100,
        'groups': {
            'General': 18,
            'News': 12,
            'Sports': 12,
            'Entertainment': 12,
            'Kids': 6,
            'Movies': 10,
            'Series': 8,
            'Music': 6,
            'Documentary': 4,
            'Religious': 4,
            'Education': 3,
            'Lifestyle': 3,
            'Culture': 2,
        }
    }
}


def normalize_group(group_name):
    """Normaliza nombres de grupo para matchear mejor"""
    g = group_name.strip()
    # Mapear grupos compuestos al principal
    if 'News' in g: return 'News'
    if 'Sports' in g: return 'Sports'
    if 'Kids' in g or 'Animation' in g: return 'Kids'
    if 'Movie' in g: return 'Movies'
    if 'Series' in g: return 'Series'
    if 'Music' in g: return 'Music'
    if 'Documentary' in g: return 'Documentary'
    if 'Religious' in g: return 'Religious'
    if 'Education' in g: return 'Education'
    if 'Lifestyle' in g: return 'Lifestyle'
    if 'Entertainment' in g: return 'Entertainment'
    if 'Culture' in g: return 'Culture'
    if 'Comedy' in g: return 'Entertainment'
    if 'General' in g: return 'General'
    if 'Business' in g: return 'News'
    if 'Travel' in g: return 'Lifestyle'
    if 'Cooking' in g: return 'Lifestyle'
    if 'Outdoor' in g: return 'Sports'
    if 'Family' in g: return 'Kids'
    if 'Classic' in g: return 'Entertainment'
    if 'Shop' in g: return 'Lifestyle'
    if 'Auto' in g: return 'Lifestyle'
    if 'Science' in g: return 'Education'
    if 'Weather' in g: return 'News'
    if 'Legislative' in g: return 'News'
    if 'Public' in g: return 'General'
    if 'Relax' in g: return 'Lifestyle'
    return g


def populate_all_plans(db):
    """Puebla todos los planes con canales segun la estructura"""

    print('\n' + '='*60)
    print('POBLANDO PLANES BASADOS EN DIRECTV COLOMBIA')
    print('='*60)

    # Primero limpiar asignaciones anteriores
    db.execute('DELETE FROM plan_channels')
    db.execute('DELETE FROM channel_backups')
    db.commit()
    print('\n[OK] Asignaciones anteriores limpiadas')

    # Construir mapa de grupos normalizados
    all_groups = db.execute(
        'SELECT DISTINCT group_name FROM channels WHERE is_active = 1'
    ).fetchall()

    group_map = {}  # normalized -> [original names]
    for (gname,) in all_groups:
        norm = normalize_group(gname)
        if norm not in group_map:
            group_map[norm] = []
        group_map[norm].append(gname)

    print(f'\nGrupos disponibles: {len(group_map)} categorias')
    for norm, originals in sorted(group_map.items()):
        total = db.execute(
            'SELECT COUNT(*) FROM channels WHERE is_active = 1 AND group_name IN ({})'.format(
                ','.join(['?' for _ in originals])
            ), originals
        ).fetchone()[0]
        if total > 0:
            print(f'  {norm:15} {total:4} canales')

    total_assigned = 0

    for plan_name, config in PLAN_STRUCTURE.items():
        plan = db.execute('SELECT id, max_channels FROM plans WHERE name = ?', (plan_name,)).fetchone()
        if not plan:
            print(f'\n[WARN] Plan {plan_name} no encontrado')
            continue

        plan_id, max_ch = plan
        target = min(config['max_channels'], max_ch)

        print(f'\n--- {plan_name} (objetivo: {target} canales) ---')

        plan_channels = []

        for group_name, count in config['groups'].items():
            originals = group_map.get(group_name, [group_name])

            # Obtener canales de este grupo que no esten ya asignados a este plan
            placeholders = ','.join(['?' for _ in originals])

            # Excluir canales ya asignados a planes anteriores (para no repetir entre planes)
            # Basico tiene los mas populares, Estandar agrega mas, Premium tiene todo
            exclude_ids = [ch[0] for ch in plan_channels]

            if exclude_ids:
                exclude_placeholders = ','.join(['?' for _ in exclude_ids])
                query = f'''
                    SELECT c.id, c.name, c.group_name
                    FROM channels c
                    LEFT JOIN channel_health h ON h.channel_id = c.id
                    WHERE c.is_active = 1
                    AND c.group_name IN ({placeholders})
                    AND (h.is_alive IS NULL OR h.is_alive = 1)
                    AND c.id NOT IN ({exclude_placeholders})
                    ORDER BY c.name
                    LIMIT ?
                '''
                params = originals + exclude_ids + [count]
            else:
                query = f'''
                    SELECT c.id, c.name, c.group_name
                    FROM channels c
                    LEFT JOIN channel_health h ON h.channel_id = c.id
                    WHERE c.is_active = 1
                    AND c.group_name IN ({placeholders})
                    AND (h.is_alive IS NULL OR h.is_alive = 1)
                    ORDER BY c.name
                    LIMIT ?
                '''
                params = originals + [count]

            channels = db.execute(query, params).fetchall()

            for ch_id, ch_name, ch_group in channels:
                db.execute(
                    'INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)',
                    (plan_id, ch_id)
                )
                plan_channels.append((ch_id, ch_name, ch_group))

            got = len(channels)
            status = '[OK]' if got >= count else f'[WARN: solo {got}]'
            print(f'  {group_name:15} {got:3}/{count:3} {status}')

        assigned = len(plan_channels)
        total_assigned += assigned
        print(f'  {"TOTAL":15} {assigned} canales asignados')

    db.commit()
    print(f'\n[OK] Total canales asignados: {total_assigned}')
    return total_assigned


def generate_all_backups(db, backups_per_channel=3):
    """Genera backups para todos los canales en planes"""

    print('\n' + '='*60)
    print(f'GENERANDO BACKUPS ({backups_per_channel} por canal)')
    print('='*60)

    plan_channels = db.execute('''
        SELECT DISTINCT pc.channel_id, c.name, c.group_name, p.name as plan_name
        FROM plan_channels pc
        JOIN channels c ON c.id = pc.channel_id
        JOIN plans p ON p.id = pc.plan_id
        ORDER BY p.price_cop, c.group_name, c.name
    ''').fetchall()

    total_created = 0
    by_plan = {}

    for pc_id, pc_name, pc_group, plan_name in plan_channels:
        if plan_name not in by_plan:
            by_plan[plan_name] = {'channels': 0, 'backups': 0}
        by_plan[plan_name]['channels'] += 1

        # Buscar candidatos del mismo grupo
        candidates = db.execute('''
            SELECT c.id
            FROM channels c
            LEFT JOIN channel_health h ON h.channel_id = c.id
            WHERE c.group_name = ?
            AND c.id != ?
            AND c.is_active = 1
            AND (h.is_alive IS NULL OR h.is_alive = 1)
            AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?)
            AND c.id NOT IN (SELECT channel_id FROM plan_channels WHERE channel_id = c.id)
            ORDER BY RANDOM()
            LIMIT ?
        ''', (pc_group, pc_id, pc_id, backups_per_channel)).fetchall()

        for i, (cand_id,) in enumerate(candidates):
            db.execute(
                'INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)',
                (pc_id, cand_id, i)
            )
            total_created += 1
            by_plan[plan_name]['backups'] += 1

    db.commit()

    print('\nBackups por plan:')
    for plan_name, stats in by_plan.items():
        print(f'  {plan_name:12} {stats["channels"]:3} canales, {stats["backups"]:3} backups')

    print(f'\n[OK] Total backups creados: {total_created}')
    return total_created


def show_final_summary(db):
    """Muestra resumen final"""

    print('\n' + '='*60)
    print('RESUMEN FINAL - STREAMFLOW v5.0')
    print('='*60)

    total_ch = db.execute('SELECT COUNT(*) FROM channels').fetchone()[0]
    active_ch = db.execute('SELECT COUNT(*) FROM channels WHERE is_active = 1').fetchone()[0]
    groups = db.execute('SELECT COUNT(DISTINCT group_name) FROM channels').fetchone()[0]

    print(f'\nBase de datos:')
    print(f'  Total canales: {total_ch}')
    print(f'  Canales activos: {active_ch}')
    print(f'  Grupos/categorias: {groups}')

    print(f'\nPlanes (estilo DIRECTV Colombia):')
    plans = db.execute('''
        SELECT p.name, p.max_channels, p.price_cop,
               COUNT(DISTINCT pc.channel_id) as ch_count
        FROM plans p
        LEFT JOIN plan_channels pc ON pc.plan_id = p.id
        GROUP BY p.id
        ORDER BY p.price_cop
    ''').fetchall()

    for name, max_ch, price, cnt in plans:
        pct = cnt / max_ch * 100 if max_ch > 0 else 0
        bar = '█' * int(pct / 5) + '░' * (20 - int(pct / 5))
        price_str = f'${price:,} COP' if price > 0 else 'Gratis'
        print(f'  {name:12} [{bar}] {cnt:3}/{max_ch:3} ({pct:5.1f}%)  {price_str}')

    print(f'\nBackups:')
    for name, _, _, _ in plans:
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
        print(f'  {name:12} {ch_in_plan:3} canales x {ratio:.1f} backups = {backups} total')

    # Verificacion de canales muertos con reemplazo
    dead_with_backup = db.execute('''
        SELECT COUNT(DISTINCT pc.channel_id)
        FROM plan_channels pc
        JOIN channel_health h ON h.channel_id = pc.channel_id AND h.is_alive = 0
        JOIN channel_backups cb ON cb.channel_id = pc.channel_id
    ''').fetchone()[0]

    print(f'\n  Canales muertos con backup listo: {dead_with_backup}')
    print(f'\n  Estado: LISTO PARA PRODUCCION')


def main():
    print('=== STREAMFLOW PLAN POPULATOR (DIRECTV-based) ===')
    print(f'Fecha: {time.strftime("%Y-%m-%d %H:%M:%S")}')

    db = sqlite3.connect(DB_PATH, timeout=60)
    db.execute('PRAGMA journal_mode = WAL')
    db.execute('PRAGMA busy_timeout = 30000')

    populate_all_plans(db)
    generate_all_backups(db, backups_per_channel=3)
    show_final_summary(db)

    db.close()
    print('\n[OK] Proceso completado!')


if __name__ == '__main__':
    main()
