/**
 * Rebuild Plans v5 — Script directo y simple.
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

  console.log('=== REBUILD PLANS v5 ===\n');

  // PASO 1: Obtener canales en planes que ya tienen health check
  const planChannels = db.prepare(`
    SELECT DISTINCT c.id, c.name, c.stream_url, c.group_name, COALESCE(h.is_alive, -1) as alive
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    LEFT JOIN channel_health h ON h.channel_id = c.id
    ORDER BY c.name
  `).all();

  console.log(`Canales actualmente en planes: ${planChannels.length}`);

  const alive = planChannels.filter(c => c.alive === 1);
  const dead = planChannels.filter(c => c.alive === 0);
  const unchecked = planChannels.filter(c => c.alive === -1);

  console.log(`  Vivos: ${alive.length}`);
  console.log(`  Muertos: ${dead.length}`);
  console.log(`  Sin verificar: ${unchecked.length}`);

  // PASO 2: Verificar los no verificados
  if (unchecked.length > 0) {
    console.log(`\nVerificando ${unchecked.length} canales...`);

    for (let i = 0; i < unchecked.length; i += 20) {
      const batch = unchecked.slice(i, i + 20);
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

    // Recargar
    const stillDead = db.prepare(`
      SELECT DISTINCT c.id, c.name, c.group_name
      FROM plan_channels pc
      JOIN channels c ON c.id = pc.channel_id
      JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 0
    `).all();

    console.log(`  Verificación completa. Muertos: ${stillDead.length}`);
  }

  // PASO 3: Reemplazar muertos con backups o alternativas
  console.log('\n--- Reemplazando canales muertos ---');

  const currentDead = db.prepare(`
    SELECT DISTINCT c.id, c.name, c.stream_url, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 0
  `).all();

  let replaced = 0;

  for (const dch of currentDead) {
    // 1. Intentar con backups existentes
    const backups = db.prepare(`
      SELECT cb.backup_channel_id, bc.name, bc.stream_url, bc.group_name
      FROM channel_backups cb
      JOIN channels bc ON bc.id = cb.backup_channel_id
      LEFT JOIN channel_health h ON h.channel_id = cb.backup_channel_id
      WHERE cb.channel_id = ? AND bc.is_active = 1
      AND (h.is_alive = 1 OR h.is_alive IS NULL)
      ORDER BY cb.priority
    `).all(dch.id);

    let found = false;

    for (const bk of backups) {
      // Verificar si el backup está vivo
      let bkAlive = db.prepare('SELECT is_alive FROM channel_health WHERE channel_id = ?').get(bk.backup_channel_id);
      if (!bkAlive) {
        const r = await testUrl(bk.stream_url);
        const now = Math.floor(Date.now() / 1000);
        db.prepare(`
          INSERT INTO channel_health (channel_id, last_check, last_success, is_alive, fail_count, response_time_ms)
          VALUES (?, ?, ?, ?, 0, ?)
          ON CONFLICT(channel_id) DO UPDATE SET
            last_check = excluded.last_check,
            last_success = excluded.last_success,
            is_alive = excluded.is_alive,
            response_time_ms = excluded.response_time_ms
        `).run(bk.backup_channel_id, now, r.ok ? now : 0, r.ok ? 1 : 0, r.elapsed || 0);
        bkAlive = { is_alive: r.ok ? 1 : 0 };
      }

      if (bkAlive.is_alive === 1) {
        const planIds = db.prepare('SELECT plan_id FROM plan_channels WHERE channel_id = ?').all(dch.id);
        for (const p of planIds) {
          db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(p.plan_id, dch.id);
          db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(p.plan_id, bk.backup_channel_id);
        }
        console.log(`  ✓ BACKUP: ${dch.name.substring(0, 40).padEnd(42)} → ${bk.name.substring(0, 40)}`);
        found = true;
        replaced++;
        break;
      }
    }

    if (found) continue;

    // 2. Buscar alternativa del mismo grupo
    const alts = db.prepare(`
      SELECT c.id, c.name, c.stream_url
      FROM channels c
      LEFT JOIN channel_health h ON h.channel_id = c.id
      WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
      AND (h.is_alive = 1 OR h.is_alive IS NULL)
      AND c.id NOT IN (SELECT channel_id FROM plan_channels)
      ORDER BY RANDOM() LIMIT 5
    `).all(dch.group_name, dch.id);

    let altFound = false;
    for (const alt of alts) {
      let altAlive = db.prepare('SELECT is_alive FROM channel_health WHERE channel_id = ?').get(alt.id);
      if (!altAlive) {
        const r = await testUrl(alt.stream_url);
        const now = Math.floor(Date.now() / 1000);
        db.prepare(`
          INSERT INTO channel_health (channel_id, last_check, last_success, is_alive, fail_count, response_time_ms)
          VALUES (?, ?, ?, ?, 0, ?)
          ON CONFLICT(channel_id) DO UPDATE SET
            last_check = excluded.last_check,
            last_success = excluded.last_success,
            is_alive = excluded.is_alive,
            response_time_ms = excluded.response_time_ms
        `).run(alt.id, now, r.ok ? now : 0, r.ok ? 1 : 0, r.elapsed || 0);
        if (r.ok) {
          const planIds = db.prepare('SELECT plan_id FROM plan_channels WHERE channel_id = ?').all(dch.id);
          for (const p of planIds) {
            db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(p.plan_id, dch.id);
            db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(p.plan_id, alt.id);
          }
          console.log(`  ✓ ALT: ${dch.name.substring(0, 40).padEnd(42)} → ${alt.name.substring(0, 40)}`);
          altFound = true;
          replaced++;
          break;
        }
      } else if (altAlive.is_alive === 1) {
        const planIds = db.prepare('SELECT plan_id FROM plan_channels WHERE channel_id = ?').all(dch.id);
        for (const p of planIds) {
          db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(p.plan_id, dch.id);
          db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(p.plan_id, alt.id);
        }
        console.log(`  ✓ ALT: ${dch.name.substring(0, 40).padEnd(42)} → ${alt.name.substring(0, 40)}`);
        altFound = true;
        replaced++;
        break;
      }
    }

    if (!altFound) {
      console.log(`  ✗ SIN REEMPLAZO: ${dch.name.substring(0, 50)}`);
    }
  }

  // PASO 4: Generar backups para todos los canales en planes
  console.log('\n--- Generando backups ---');
  const finalPlanChannels = db.prepare(`
    SELECT DISTINCT c.id, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
  `).all();

  let bkCreated = 0;
  const insertBk = db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)');

  for (const pc of finalPlanChannels) {
    const existing = db.prepare('SELECT COUNT(*) as c FROM channel_backups WHERE channel_id = ?').get(pc.id);
    if (existing.c >= 10) continue;

    const needed = 10 - existing.c;
    const candidates = db.prepare(`
      SELECT c.id FROM channels c
      JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 1
      WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
      AND c.id NOT IN (SELECT channel_id FROM plan_channels)
      AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?)
      ORDER BY RANDOM() LIMIT ?
    `).all(pc.group_name, pc.id, pc.id, needed);

    let prio = existing.c;
    for (const cand of candidates) {
      insertBk.run(pc.id, cand.id, prio++);
      bkCreated++;
    }
  }
  console.log(`  ${bkCreated} backups creados`);

  // Resumen final
  console.log('\n=== RESUMEN FINAL ===');
  const plans = db.prepare('SELECT id, name FROM plans ORDER BY id').all();
  for (const plan of plans) {
    const total = db.prepare('SELECT COUNT(DISTINCT channel_id) as c FROM plan_channels WHERE plan_id = ?').get(plan.id);
    const alive = db.prepare(`
      SELECT COUNT(DISTINCT pc.channel_id) as c
      FROM plan_channels pc
      JOIN channel_health h ON h.channel_id = pc.channel_id AND h.is_alive = 1
      WHERE pc.plan_id = ?
    `).get(plan.id);
    const dead = total.c - alive.c;
    console.log(`  ${plan.name}: ${total.c} total | ✓${alive.c} vivos | ✗${dead} muertos`);
  }
  console.log(`  Backups: ${db.prepare('SELECT COUNT(*) as c FROM channel_backups').get().c}`);
  console.log(`  Reemplazados: ${replaced}`);

  db.close();
}

main().catch(console.error);
