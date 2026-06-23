/**
 * Channel Health Checker v2 — Verifica canales, guarda health en DB,
 * y auto-reemplaza canales muertos en planes con backups.
 *
 * Uso:
 *   node scripts/check_channels.mjs          # Check all channels
 *   node scripts/check_channels.mjs --sample # Check only 50 random channels
 *   node scripts/check_channels.mjs --plan   # Check only channels in plans
 *   node scripts/check_channels.mjs --replace # Replace dead plan channels with backups
 *
 * Cron: cada 6 horas
 */

import Database from 'better-sqlite3';
import https from 'https';
import http from 'http';
import path from 'path';
import { DATA_DIR } from '../src/config/constants.js';

const DB_PATH = path.join(DATA_DIR, 'streamflow.sqlite');
const TIMEOUT = 10000;
const CONCURRENT = 15;

const args = process.argv.slice(2);
const MODE_SAMPLE = args.includes('--sample');
const MODE_PLAN = args.includes('--plan');
const MODE_REPLACE = args.includes('--replace');

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
          return resolve({ ok: false, status: res.statusCode, elapsed, error: 'HTTP ' + res.statusCode });
        }
        let got = false;
        res.on('data', (chunk) => {
          if (got) return;
          got = true;
          res.destroy();
          const hex = chunk.slice(0, 4).toString('hex');
          const isMedia =
            chunk[0] === 0x47 ||
            chunk.slice(0, 7).toString().includes('#EXTM3U') ||
            hex.match(/^(000000|6674779|1a45dfa3)/) ||
            chunk.slice(0, 3).toString() === 'FLV' ||
            res.statusCode < 400;
          resolve({ ok: isMedia, status: res.statusCode, elapsed });
        });
        res.on('error', () => resolve({ ok: false, elapsed: Date.now() - start, error: 'response error' }));
      });
      req.on('error', (e) => resolve({ ok: false, elapsed: Date.now() - start, error: e.message?.substring(0, 50) }));
      req.on('timeout', () => { req.destroy(); resolve({ ok: false, elapsed: TIMEOUT, error: 'timeout' }); });
    } catch (e) {
      resolve({ ok: false, elapsed: 0, error: e.message?.substring(0, 50) });
    }
  });
}

async function checkChannels(db, channels) {
  let working = 0, failed = 0;
  const setHealth = db.prepare(`
    INSERT INTO channel_health (channel_id, last_check, last_success, is_alive, fail_count, response_time_ms)
    VALUES (?, ?, ?, ?, 1, ?)
    ON CONFLICT(channel_id) DO UPDATE SET
      last_check = excluded.last_check,
      last_success = CASE WHEN excluded.is_alive = 1 THEN excluded.last_success ELSE channel_health.last_success END,
      is_alive = excluded.is_alive,
      fail_count = CASE WHEN excluded.is_alive = 0 THEN channel_health.fail_count + 1 ELSE 0 END,
      response_time_ms = excluded.response_time_ms
  `);

  const txn = db.transaction((results) => {
    const now = Math.floor(Date.now() / 1000);
    for (const r of results) {
      setHealth.run(
        r.channel.id,
        now,
        r.ok ? now : 0,
        r.ok ? 1 : 0,
        r.elapsed || 0
      );
    }
  });

  for (let i = 0; i < channels.length; i += CONCURRENT) {
    const batch = channels.slice(i, i + CONCURRENT);
    const results = await Promise.all(batch.map(async (ch) => {
      const result = await testUrl(ch.stream_url);
      return { channel: ch, ...result };
    }));

    txn(results);

    for (const r of results) {
      if (r.ok) working++;
      else {
        failed++;
        if (failed <= 30) {
          console.log(`  [DEAD] ${r.channel.name.substring(0, 45).padEnd(45)} ${(r.error || 'status ' + r.status).substring(0, 30)}`);
        }
      }
    }

    if (i % 200 === 0 && i > 0) {
      console.log(`  Progreso: ${Math.min(i + CONCURRENT, channels.length)}/${channels.length} | ✓${working} ✗${failed}`);
    }

    await new Promise(r => setTimeout(r, 200));
  }

  return { working, failed };
}

function replaceDeadPlanChannels(db) {
  console.log('\n=== AUTO-REEMPLAZO DE CANALES MUERTOS EN PLANES ===\n');

  // Get dead channels that are in plans and have backups
  const deadWithBackups = db.prepare(`
    SELECT DISTINCT pc.channel_id as dead_id, c.name as dead_name, c.group_name,
           cb.backup_channel_id, bc.name as backup_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
    JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 0
    JOIN channel_backups cb ON cb.channel_id = c.id
    JOIN channels bc ON bc.id = cb.backup_channel_id
    LEFT JOIN channel_health bh ON bh.channel_id = cb.backup_channel_id
    WHERE (bh.is_alive = 1 OR bh.is_alive IS NULL)
    AND bc.is_active = 1
    ORDER BY pc.channel_id, cb.priority
  `).all();

  if (deadWithBackups.length === 0) {
    console.log('  No hay canales muertos con backups disponibles.');
    return { replaced: 0 };
  }

  let replaced = 0;
  const processed = new Set();

  for (const row of deadWithBackups) {
    if (processed.has(row.dead_id)) continue;

    // Check if backup is already in the same plan
    const alreadyInPlan = db.prepare(
      'SELECT COUNT(*) as c FROM plan_channels WHERE plan_id = (SELECT plan_id FROM plan_channels WHERE channel_id = ? LIMIT 1) AND channel_id = ?'
    ).get(row.dead_id, row.backup_channel_id);

    if (alreadyInPlan.c > 0) {
      processed.add(row.dead_id);
      continue;
    }

    // Replace: remove dead channel from plan, add backup
    const planId = db.prepare('SELECT plan_id FROM plan_channels WHERE channel_id = ? LIMIT 1').get(row.dead_id)?.plan_id;
    if (!planId) continue;

    db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(planId, row.dead_id);
    db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(planId, row.backup_channel_id);

    console.log(`  ✓ Reemplazado: "${row.dead_name}" → "${row.backup_name}"`);
    processed.add(row.dead_id);
    replaced++;
  }

  return { replaced };
}

async function main() {
  const db = new Database(DB_PATH, { timeout: 30000 });
  db.pragma('journal_mode = WAL');

  console.log('=== CHANNEL HEALTH CHECKER v2 ===');
  console.log('Fecha: ' + new Date().toISOString());

  // Auto-generate backups if table is empty
  const backupCount = db.prepare('SELECT COUNT(*) as c FROM channel_backups').get().c;
  if (backupCount === 0) {
    console.log('\nGenerando backups automáticos...');
    const planChannels = db.prepare('SELECT DISTINCT pc.channel_id, c.group_name FROM plan_channels pc JOIN channels c ON c.id = pc.channel_id').all();
    let created = 0;
    const insert = db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)');

    for (const pc of planChannels) {
      const existing = db.prepare('SELECT COUNT(*) as c FROM channel_backups WHERE channel_id = ?').get(pc.channel_id);
      if (existing.c >= 3) continue;

      const candidates = db.prepare(`
        SELECT c.id FROM channels c
        LEFT JOIN channel_health h ON h.channel_id = c.id
        WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
        AND (h.is_alive IS NULL OR h.is_alive = 1)
        AND c.id NOT IN (SELECT backup_channel_id FROM channel_backups WHERE channel_id = ?)
        ORDER BY RANDOM() LIMIT 3
      `).all(pc.group_name, pc.channel_id, pc.channel_id);

      let prio = existing.c || 0;
      for (const cand of candidates) {
        insert.run(pc.channel_id, cand.id, prio++);
        created++;
      }
    }
    console.log(`  ${created} backups creados automáticamente.`);
  }

  if (MODE_REPLACE) {
    const { replaced } = replaceDeadPlanChannels(db);
    console.log(`\nTotal reemplazados: ${replaced}`);
    db.close();
    return;
  }

  // Get channels to check
  let channels;
  if (MODE_SAMPLE) {
    channels = db.prepare("SELECT id, name, stream_url FROM channels WHERE stream_url LIKE 'http%' ORDER BY RANDOM() LIMIT 50").all();
    console.log('Modo: MUESTRA (50 aleatorios)');
  } else if (MODE_PLAN) {
    channels = db.prepare(`
      SELECT DISTINCT c.id, c.name, c.stream_url
      FROM plan_channels pc
      JOIN channels c ON c.id = pc.channel_id
      WHERE c.stream_url LIKE 'http%'
    `).all();
    console.log(`Modo: PLANES (${channels.length} canales en planes)`);
  } else {
    channels = db.prepare("SELECT id, name, stream_url FROM channels WHERE stream_url LIKE 'http%'").all();
    console.log(`Modo: COMPLETO (${channels.length} canales)`);
  }

  console.log(`\nVerificando ${channels.length} canales...\n`);

  const startTime = Date.now();
  const { working, failed } = await checkChannels(db, channels);
  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

  console.log('\n=== RESULTADO ===');
  console.log(`Tiempo: ${elapsed}s`);
  console.log(`Verificados: ${channels.length}`);
  console.log(`Funcionando: ${working} (${(working/channels.length*100).toFixed(1)}%)`);
  console.log(`Muertos: ${failed} (${(failed/channels.length*100).toFixed(1)}%)`);

  // Auto-replace dead plan channels
  if (failed > 0 && !MODE_SAMPLE) {
    const { replaced } = replaceDeadPlanChannels(db);
    if (replaced > 0) {
      console.log(`\n✓ ${replaced} canales muertos reemplazados automáticamente en planes.`);
    }
  }

  // Summary
  const totalAlive = db.prepare('SELECT COUNT(*) as c FROM channel_health WHERE is_alive = 1').get().c;
  const totalDead = db.prepare('SELECT COUNT(*) as c FROM channel_health WHERE is_alive = 0').get().c;
  const planChannelsCount = db.prepare('SELECT COUNT(DISTINCT channel_id) as c FROM plan_channels').get().c;
  const backupCountNow = db.prepare('SELECT COUNT(*) as c FROM channel_backups').get().c;

  console.log('\n=== ESTADO GENERAL ===');
  console.log(`Canales saludables: ${totalAlive}`);
  console.log(`Canales muertos: ${totalDead}`);
  console.log(`Canales en planes: ${planChannelsCount}`);
  console.log(`Backups configurados: ${backupCountNow}`);

  db.close();
}

main().catch(console.error);
