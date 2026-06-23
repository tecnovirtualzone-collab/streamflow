/**
 * Complete Channel Fix — Verifica TODOS los canales en planes,
 * reemplaza muertos, y genera backups.
 * Ejecutar: node scripts/complete_fix.mjs
 */

import Database from 'better-sqlite3';
import https from 'https';
import http from 'http';
import path from 'path';
import { DATA_DIR } from '../src/config/constants.js';

const DB_PATH = path.join(DATA_DIR, 'streamflow.sqlite');
const TIMEOUT = 8000;
const CONCURRENT = 25;

function testUrl(url) {
  return new Promise((resolve) => {
    try {
      const mod = url.startsWith('https') ? https : http;
      const start = Date.now();
      const req = mod.get(url, {
        headers: { 'User-Agent': 'VLC/3.0.0', 'Accept': '*/*', 'Connection': 'close' },
        timeout: TIMEOUT
      }, (res) => {
        const elapsed = Date.now() - start;
        if (res.statusCode >= 400) { res.destroy(); return resolve({ ok: false }); }
        let got = false;
        res.on('data', (chunk) => {
          if (got) return;
          got = true;
          res.destroy();
          const isMedia = chunk[0] === 0x47 || chunk.slice(0, 7).toString().includes('#EXTM3U') ||
            chunk.slice(0, 4).toString('hex').match(/^(000000|6674779|1a45dfa3)/);
          resolve({ ok: isMedia, elapsed });
        });
        res.on('error', () => resolve({ ok: false }));
      });
      req.on('error', () => resolve({ ok: false }));
      req.on('timeout', () => { req.destroy(); resolve({ ok: false }); });
    } catch (e) { resolve({ ok: false }); }
  });
}

async function main() {
  const db = new Database(DB_PATH, { timeout: 30000 });
  db.pragma('journal_mode = WAL');

  console.log('=== COMPLETE CHANNEL FIX ===\n');

  // PASO 1: Obtener TODOS los canales en planes (únicos)
  const planChannels = db.prepare(`
    SELECT DISTINCT c.id, c.name, c.stream_url, c.group_name,
           COALESCE(h.is_alive, -1) as current_health
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    LEFT JOIN channel_health h ON h.channel_id = c.id
    ORDER BY c.name
  `).all();

  console.log(`Canales únicos en planes: ${planChannels.length}`);

  const needCheck = planChannels.filter(c => c.current_health === -1);
  const alreadyDead = planChannels.filter(c => c.current_health === 0);
  const alreadyAlive = planChannels.filter(c => c.current_health === 1);

  console.log(`  Ya vivos: ${alreadyAlive.length}`);
  console.log(`  Ya muertos: ${alreadyDead.length}`);
  console.log(`  Sin verificar: ${needCheck.length}`);

  // PASO 2: Verificar los que falta
  if (needCheck.length > 0) {
    console.log(`\nVerificando ${needCheck.length} canales...`);

    let checked = 0;
    for (let i = 0; i < needCheck.length; i += CONCURRENT) {
      const batch = needCheck.slice(i, i + CONCURRENT);
      const results = await Promise.all(batch.map(async (ch) => {
        const r = await testUrl(ch.stream_url);
        return { ch, ...r };
      }));

      for (const r of results) {
        const now = Math.floor(Date.now() / 1000);
        db.prepare(`
          INSERT INTO channel_health (channel_id, last_check, last_success, is_alive, fail_count, response_time_ms)
          VALUES (?, ?, ?, ?, 0, ?)
          ON CONFLICT(channel_id) DO UPDATE SET
            last_check = excluded.last_check,
            last_success = excluded.last_success,
            is_alive = excluded.is_alive,
            response_time_ms = excluded.response_time_ms
        `).run(r.ch.id, now, r.ok ? now : 0, r.ok ? 1 : 0, r.elapsed || 0);
      }

      checked += results.length;
      const alive = results.filter(r => r.ok).length;
      console.log(`  ${checked}/${needCheck.length} | ✓${alive} ✗${results.length - alive}`);

      await new Promise(r => setTimeout(r, 100));
    }
  }

  // PASO 3: Obtener TODOS los muertos actualizados
  const allDead = db.prepare(`
    SELECT DISTINCT c.id, c.name, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 0
    ORDER BY c.group_name, c.name
  `).all();

  console.log(`\nTotal muertos en planes: ${allDead.length}`);

  // PASO 4: Reemplazar muertos
  console.log('\n--- Reemplazando muertos ---');
  const plans = db.prepare('SELECT id FROM plans ORDER BY id').all();
  let replaced = 0, noReplace = 0;

  for (const dch of allDead) {
    // Buscar alternativa viva del mismo grupo
    const alts = db.prepare(`
      SELECT c.id, c.name, c.stream_url
      FROM channels c
      JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 1
      WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
      AND c.id NOT IN (SELECT channel_id FROM plan_channels)
      ORDER BY RANDOM() LIMIT 5
    `).all(dch.group_name, dch.id);

    let found = false;
    for (const alt of alts) {
      for (const plan of plans) {
        db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(plan.id, dch.id);
        db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(plan.id, alt.id);
      }
      console.log(`  ✓ ${dch.name.substring(0, 45).padEnd(47)} → ${alt.name.substring(0, 40)}`);
      replaced++;
      found = true;
      break;
    }

    if (!found) {
      // Cualquier canal vivo
      const anyAlive = db.prepare(`
        SELECT c.id, c.name, c.stream_url
        FROM channels c
        JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 1
        WHERE c.id != ? AND c.is_active = 1
        AND c.id NOT IN (SELECT channel_id FROM plan_channels)
        ORDER BY RANDOM() LIMIT 5
      `).all(dch.id);

      for (const alt of anyAlive) {
        for (const plan of plans) {
          db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(plan.id, dch.id);
          db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(plan.id, alt.id);
        }
        console.log(`  ✓ ${dch.name.substring(0, 45).padEnd(47)} → ${alt.name.substring(0, 40)} (genérico)`);
        replaced++;
        found = true;
        break;
      }
    }

    if (!found) {
      console.log(`  ✗ SIN REEMPLAZO: ${dch.name.substring(0, 50)}`);
      noReplace++;
    }
  }

  // PASO 5: Generar 10 backups por canal
  console.log('\n--- Generando backups ---');
  db.prepare('DELETE FROM channel_backups').run();

  const finalPCs = db.prepare(`
    SELECT DISTINCT c.id, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
  `).all();

  let bkCreated = 0;
  const insertBk = db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)');

  for (const pc of finalPCs) {
    const candidates = db.prepare(`
      SELECT c.id FROM channels c
      JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 1
      WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
      AND c.id NOT IN (SELECT channel_id FROM plan_channels)
      ORDER BY RANDOM() LIMIT 10
    `).all(pc.group_name, pc.id);

    let prio = 0;
    for (const cand of candidates) {
      insertBk.run(pc.id, cand.id, prio++);
      bkCreated++;
    }
  }
  console.log(`  ${bkCreated} backups creados`);

  // PASO 6: Verificar que los reemplazos también estén vivos
  console.log('\n--- Verificando reemplazos ---');
  const unchecked = db.prepare(`
    SELECT DISTINCT c.id, c.name, c.stream_url
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    LEFT JOIN channel_health h ON h.channel_id = c.id
    WHERE h.is_alive IS NULL OR h.is_alive = -1
  `).all();

  if (unchecked.length > 0) {
    console.log(`  Verificando ${unchecked.length} canales nuevos...`);
    for (let i = 0; i < unchecked.length; i += CONCURRENT) {
      const batch = unchecked.slice(i, i + CONCURRENT);
      const results = await Promise.all(batch.map(async (ch) => {
        const r = await testUrl(ch.stream_url);
        return { ch, ...r };
      }));

      for (const r of results) {
        const now = Math.floor(Date.now() / 1000);
        db.prepare(`
          INSERT INTO channel_health (channel_id, last_check, last_success, is_alive, fail_count, response_time_ms)
          VALUES (?, ?, ?, ?, 0, ?)
          ON CONFLICT(channel_id) DO UPDATE SET
            last_check = excluded.last_check,
            last_success = excluded.last_success,
            is_alive = excluded.is_alive,
            response_time_ms = excluded.response_time_ms
        `).run(r.ch.id, now, r.ok ? now : 0, r.ok ? 1 : 0, r.elapsed || 0);
      }
      await new Promise(r => setTimeout(r, 100));
    }
  }

  // Resumen final
  console.log('\n=== RESUMEN FINAL ===');
  for (const plan of db.prepare('SELECT id, name FROM plans ORDER BY id').all()) {
    const total = db.prepare('SELECT COUNT(DISTINCT channel_id) as c FROM plan_channels WHERE plan_id = ?').get(plan.id);
    const alive = db.prepare(`
      SELECT COUNT(DISTINCT pc.channel_id) as c
      FROM plan_channels pc
      JOIN channel_health h ON h.channel_id = pc.channel_id AND h.is_alive = 1
      WHERE pc.plan_id = ?
    `).get(plan.id);
    console.log(`  ${plan.name}: ${total.c} total | ✓${alive.c} vivos | ✗${total.c - alive.c} muertos`);
  }
  console.log(`  Backups: ${db.prepare('SELECT COUNT(*) as c FROM channel_backups').get().c}`);
  console.log(`  Reemplazados: ${replaced}`);
  console.log(`  Sin reemplazo: ${noReplace}`);

  db.close();
}

main().catch(console.error);
