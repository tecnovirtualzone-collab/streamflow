/**
 * Fix Dead Channels — Limpia nombres corruptos, verifica backups vivos,
 * y reemplaza canales muertos en planes.
 *
 * Uso: node scripts/fix_dead_channels.mjs
 */

import Database from 'better-sqlite3';
import https from 'https';
import http from 'http';
import path from 'path';
import { DATA_DIR } from '../src/config/constants.js';

const DB_PATH = path.join(DATA_DIR, 'streamflow.sqlite');
const TIMEOUT = 8000;

function testUrl(url) {
  return new Promise((resolve) => {
    try {
      const mod = url.startsWith('https') ? https : http;
      const start = Date.now();
      const req = mod.get(url, {
        headers: {
          'User-Agent': 'VLC/3.0.0 LibVLC/3.0.0',
          'Accept': '*/*',
          'Connection': 'close'
        },
        timeout: TIMEOUT
      }, (res) => {
        const elapsed = Date.now() - start;
        if (res.statusCode >= 400) {
          res.destroy();
          return resolve({ ok: false, status: res.statusCode });
        }
        let got = false;
        res.on('data', (chunk) => {
          if (got) return;
          got = true;
          res.destroy();
          const isMedia =
            chunk[0] === 0x47 ||
            chunk.slice(0, 7).toString().includes('#EXTM3U') ||
            chunk.slice(0, 4).toString('hex').match(/^(000000|6674779|1a45dfa3)/) ||
            chunk.slice(0, 3).toString() === 'FLV';
          resolve({ ok: isMedia, status: res.statusCode, elapsed });
        });
        res.on('error', () => resolve({ ok: false }));
      });
      req.on('error', () => resolve({ ok: false }));
      req.on('timeout', () => { req.destroy(); resolve({ ok: false }); });
    } catch (e) {
      resolve({ ok: false });
    }
  });
}

async function main() {
  const db = new Database(DB_PATH, { timeout: 30000 });
  db.pragma('journal_mode = WAL');

  console.log('=== FIX DEAD CHANNELS ===\n');

  // PASO 1: Limpiar nombres corruptos (que contienen "Gecko", "Chrome", "Safari")
  console.log('--- PASO 1: Limpiando nombres corruptos ---');
  const corruptChannels = db.prepare(`
    SELECT id, name, stream_url FROM channels
    WHERE name LIKE '%Gecko%' OR name LIKE '%Chrome%' OR name LIKE '%Safari%' OR name LIKE '%CrKey%'
  `).all();

  console.log(`  Encontrados ${corruptChannels.length} canales con nombres corruptos`);

  // Intentar extraer nombre real del stream URL o marcar para desactivación
  let cleaned = 0;
  for (const ch of corruptChannels) {
    // Marcar como inactivo para que no se use
    db.prepare('UPDATE channels SET is_active = 0 WHERE id = ?').run(ch.id);
    // Quitar de planes
    db.prepare('DELETE FROM plan_channels WHERE channel_id = ?').run(ch.id);
    cleaned++;
  }
  console.log(`  ${cleaned} canales corruptos desactivados y removidos de planes`);

  // PASO 2: Verificar backups vivos para canales en planes
  console.log('\n--- PASO 2: Verificando backups vivos ---');

  const planChannels = db.prepare(`
    SELECT DISTINCT c.id, c.name, c.stream_url, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    WHERE c.is_active = 1
  `).all();

  console.log(`  ${planChannels.length} canales activos en planes`);

  // Verificar cuáles están vivos
  let alive = 0, dead = 0;
  const deadChannels = [];

  for (let i = 0; i < planChannels.length; i += 10) {
    const batch = planChannels.slice(i, i + 10);
    const results = await Promise.all(batch.map(async (ch) => {
      const r = await testUrl(ch.stream_url);
      return { ch, ...r };
    }));

    for (const r of results) {
      // Actualizar health
      const now = Math.floor(Date.now() / 1000);
      db.prepare(`
        INSERT INTO channel_health (channel_id, last_check, last_success, is_alive, fail_count, response_time_ms)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
          last_check = excluded.last_check,
          last_success = CASE WHEN excluded.is_alive = 1 THEN excluded.last_success ELSE channel_health.last_success END,
          is_alive = excluded.is_alive,
          fail_count = CASE WHEN excluded.is_alive = 0 THEN channel_health.fail_count + 1 ELSE 0 END,
          response_time_ms = excluded.response_time_ms
      `).run(r.ch.id, now, r.ok ? now : 0, r.ok ? 1 : 0, r.elapsed || 0);

      if (r.ok) {
        alive++;
      } else {
        dead++;
        deadChannels.push(r.ch);
        console.log(`  [DEAD] ${r.ch.name.substring(0, 50)}`);
      }
    }

    await new Promise(r => setTimeout(r, 200));
  }

  console.log(`\n  Vivos: ${alive} | Muertos: ${dead}`);

  // PASO 3: Reemplazar canales muertos con backups vivos
  console.log('\n--- PASO 3: Reemplazando canales muertos ---');

  let replaced = 0;
  let noBackup = 0;

  for (const deadCh of deadChannels) {
    // Buscar backups que estén vivos
    const backups = db.prepare(`
      SELECT cb.backup_channel_id, bc.name, bc.stream_url
      FROM channel_backups cb
      JOIN channels bc ON bc.id = cb.backup_channel_id
      WHERE cb.channel_id = ? AND bc.is_active = 1
      ORDER BY cb.priority
    `).all(deadCh.id);

    if (backups.length === 0) {
      // Buscar cualquier canal vivo del mismo grupo
      const alternatives = db.prepare(`
        SELECT c.id, c.name, c.stream_url
        FROM channels c
        LEFT JOIN channel_health h ON h.channel_id = c.id
        WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
        AND (h.is_alive = 1 OR h.is_alive IS NULL)
        AND c.id NOT IN (SELECT channel_id FROM plan_channels)
        ORDER BY RANDOM() LIMIT 3
      `).all(deadCh.group_name, deadCh.id);

      if (alternatives.length === 0) {
        console.log(`  [SIN BACKUP] ${deadCh.name.substring(0, 50)}`);
        noBackup++;
        continue;
      }

      // Probar alternativas
      let found = false;
      for (const alt of alternatives) {
        const r = await testUrl(alt.stream_url);
        if (r.ok) {
          const planId = db.prepare('SELECT plan_id FROM plan_channels WHERE channel_id = ? LIMIT 1').get(deadCh.id)?.plan_id;
          if (planId) {
            db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(planId, deadCh.id);
            db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(planId, alt.id);
            // Crear backup entry
            db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, 0)').run(deadCh.id, alt.id);
            console.log(`  ✓ Reemplazado: "${deadCh.name.substring(0, 40)}" → "${alt.name.substring(0, 40)}"`);
            replaced++;
            found = true;
          }
          break;
        }
      }
      if (!found) {
        console.log(`  [SIN BACKUP VIVO] ${deadCh.name.substring(0, 50)}`);
        noBackup++;
      }
    } else {
      // Probar backups en orden
      let found = false;
      for (const bk of backups) {
        const r = await testUrl(bk.stream_url);
        if (r.ok) {
          const planId = db.prepare('SELECT plan_id FROM plan_channels WHERE channel_id = ? LIMIT 1').get(deadCh.id)?.plan_id;
          if (planId) {
            db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(planId, deadCh.id);
            db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(planId, bk.backup_channel_id);
            console.log(`  ✓ Reemplazado: "${deadCh.name.substring(0, 40)}" → "${bk.name.substring(0, 40)}"`);
            replaced++;
            found = true;
          }
          break;
        }
      }
      if (!found) {
        console.log(`  [TODOS BACKUPS CAÍDOS] ${deadCh.name.substring(0, 50)}`);
        noBackup++;
      }
    }
  }

  // PASO 4: Generar más backups para canales sin suficientes
  console.log('\n--- PASO 4: Generando backups adicionales ---');

  const planChCount = db.prepare('SELECT COUNT(DISTINCT channel_id) as c FROM plan_channels').get().c;
  const allPlanChannels = db.prepare(`
    SELECT DISTINCT c.id, c.name, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    WHERE c.is_active = 1
  `).all();

  let newBackups = 0;
  for (const pc of allPlanChannels) {
    const bkCount = db.prepare('SELECT COUNT(*) as c FROM channel_backups WHERE channel_id = ?').get(pc.id);
    if (bkCount.c >= 10) continue;

    const needed = 10 - bkCount.c;
    const candidates = db.prepare(`
      SELECT c.id FROM channels c
      LEFT JOIN channel_health h ON h.channel_id = c.id
      WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
      AND (h.is_alive = 1 OR h.is_alive IS NULL)
      AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?)
      AND c.id NOT IN (SELECT channel_id FROM plan_channels)
      ORDER BY RANDOM() LIMIT ?
    `).all(pc.group_name, pc.id, pc.id, needed);

    const insert = db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)');
    let prio = bkCount.c;
    for (const cand of candidates) {
      insert.run(pc.id, cand.id, prio++);
      newBackups++;
    }
  }
  console.log(`  ${newBackups} nuevos backups creados`);

  // Resumen final
  const finalPlanCh = db.prepare('SELECT COUNT(DISTINCT channel_id) as c FROM plan_channels').get().c;
  const finalBackups = db.prepare('SELECT COUNT(*) as c FROM channel_backups').get().c;
  const finalAlive = db.prepare('SELECT COUNT(*) as c FROM channel_health WHERE is_alive = 1').get().c;
  const finalDead = db.prepare('SELECT COUNT(*) as c FROM channel_health WHERE is_alive = 0').get().c;

  console.log('\n=== RESUMEN FINAL ===');
  console.log(`Canales en planes: ${finalPlanCh}`);
  console.log(`Backups totales: ${finalBackups}`);
  console.log(`Canales vivos (health): ${finalAlive}`);
  console.log(`Canales muertos (health): ${finalDead}`);
  console.log(`Reemplazados: ${replaced}`);
  console.log(`Sin backup disponible: ${noBackup}`);

  db.close();
}

main().catch(console.error);
