/**
 * Final Fix — Asigna los mismos canales a los 3 planes,
 * verifica, reemplaza muertos y genera backups.
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

// Queries para buscar cada canal de la parrilla
const QUERIES = [
  // Nacionales (10)
  { name: 'Canal RCN', q: "name LIKE '%Canal RCN%' AND group_name = 'General'" },
  { name: 'Caracol TV', q: "name LIKE '%Caracol TV%' AND group_name = 'General'" },
  { name: 'Canal Institucional', q: "name LIKE '%Canal Institucional%' AND group_name = 'General'" },
  { name: 'Senal Colombia', q: "name LIKE '%Senal Colombia%' AND group_name = 'General'" },
  { name: 'Canal Trece', q: "name LIKE '%Canal Trece%'" },
  { name: 'Teleantioquia', q: "name LIKE '%Teleantioquia%'" },
  { name: 'Telecaribe', q: "name LIKE '%Telecaribe%'" },
  { name: 'Telecafe', q: "name LIKE '%Telecafe%'" },
  { name: 'Canal Uno', q: "name LIKE '%Canal Uno%'" },
  { name: 'Telepacifico', q: "name LIKE '%Telepacifico%'" },
  // Deportes (15)
  { name: 'ESPN', q: "(name LIKE 'ESPN' OR name LIKE 'ESPN %') AND group_name = 'TV'" },
  { name: 'ESPN2', q: "name LIKE 'ESPN2%'" },
  { name: 'ESPN Deportes', q: "name LIKE '%ESPN Deportes%'" },
  { name: 'ESPN News', q: "name LIKE '%ESPN News%'" },
  { name: 'FOX Sports', q: "(name LIKE 'FOX Sports' OR name LIKE 'Fox Sports%') AND group_name = 'Sports'" },
  { name: 'Fox Deportes', q: "name LIKE '%Fox Deportes%'" },
  { name: 'Win Sports', q: "name LIKE '%Win Sports%' AND group_name = 'Sports'" },
  { name: 'TyC Sports', q: "name LIKE '%TyC Sports%'" },
  { name: 'DSports', q: "name LIKE '%DSports%'" },
  { name: 'MLB Channel', q: "name LIKE '%MLB Channel%'" },
  { name: 'Golf Channel', q: "name LIKE '%Golf Channel%'" },
  { name: 'Fox Sports 2', q: "name LIKE '%Fox Sports 2%'" },
  { name: 'Win+ Futbol', q: "name LIKE '%Win+ Futbol%'" },
  { name: 'ESPN SD', q: "name LIKE 'ESPN SD%'" },
  { name: 'Fox Sports 3', q: "name LIKE '%Fox Sports 3%'" },
  // Películas (15)
  { name: 'HBO', q: "name LIKE 'HBO' AND group_name = 'TV'" },
  { name: 'HBO 2', q: "name LIKE 'HBO 2%' AND group_name = 'TV'" },
  { name: 'HBO Family', q: "name LIKE '%HBO Family%'" },
  { name: 'HBO Signature', q: "name LIKE '%HBO Signature%'" },
  { name: 'Cinemax', q: "name LIKE 'Cinemax%' AND group_name = 'TV'" },
  { name: 'AXN Latin America', q: "name LIKE '%AXN Latin America%'" },
  { name: 'Sony Movies', q: "name LIKE '%Sony Movies%'" },
  { name: 'Studio Universal', q: "name LIKE '%Studio Universal%'" },
  { name: 'Universal TV', q: "name LIKE '%Universal TV%'" },
  { name: 'AMC', q: "name LIKE 'AMC%' AND group_name = 'Movies'" },
  { name: 'Charge!', q: "name LIKE 'Charge!%'" },
  { name: 'SYFY', q: "name LIKE 'SYFY%' AND group_name = 'Movies;Series'" },
  { name: 'Paramount Network', q: "name LIKE '%Paramount Network%'" },
  { name: 'Paramount Movie Channel', q: "name LIKE '%Paramount Movie Channel%'" },
  { name: 'HBO Boxing', q: "name LIKE '%HBO Boxing%'" },
  // Series (15)
  { name: 'TNT', q: "name LIKE 'TNT' AND group_name = 'TV'" },
  { name: 'Star Channel', q: "name LIKE '%Star Channel%'" },
  { name: 'FX Latin America', q: "name LIKE '%FX Latin America%'" },
  { name: 'A&E Latin America', q: "name LIKE '%A&E Latin America%'" },
  { name: 'Telemundo Internacional', q: "name LIKE '%Telemundo Internacional%'" },
  { name: 'Las Estrellas', q: "name LIKE '%Las Estrellas%'" },
  { name: 'Pasiones', q: "name LIKE '%Pasiones%'" },
  { name: 'RCN Novelas', q: "name LIKE '%RCN Novelas%'" },
  { name: 'Novelisima', q: "name LIKE '%Novelisima%'" },
  { name: 'Tlnovelas', q: "name LIKE '%Tlnovelas%'" },
  { name: 'Pluto TV Series', q: "name LIKE '%Pluto TV Series%'" },
  { name: 'Venevision', q: "name LIKE '%Venevision%'" },
  { name: 'Azteca Internacional', q: "name LIKE '%Azteca Internacional%'" },
  { name: 'RCN HD2', q: "name LIKE '%RCN HD2%'" },
  { name: 'Telemundo', q: "name LIKE 'Telemundo' AND group_name = 'TV'" },
  // Documentales (10)
  { name: 'Discovery Channel', q: "name LIKE '%Discovery Channel%' AND group_name = 'TV'" },
  { name: 'History 2', q: "name LIKE '%History 2%'" },
  { name: 'National Geographic', q: "name LIKE 'National Geographic%' AND group_name = 'TV'" },
  { name: 'Nat Geo Wild', q: "name LIKE '%Nat Geo Wild%'" },
  { name: 'Discovery Science', q: "name LIKE '%Discovery Science%'" },
  { name: 'Discovery Turbo', q: "name LIKE '%Discovery Turbo%' AND group_name = 'TV'" },
  { name: 'Animal Planet', q: "name LIKE 'Animal Planet%' AND group_name = 'TV'" },
  { name: 'TLC', q: "name LIKE 'TLC' AND group_name = 'TV'" },
  { name: 'Love Nature', q: "name LIKE '%Love Nature%'" },
  { name: 'Travel Channel', q: "name LIKE '%Travel Channel%'" },
  // Infantiles (10)
  { name: 'Cartoon Network', q: "name LIKE 'Cartoon Network' AND group_name = 'TV'" },
  { name: 'Cartoon Network HD', q: "name LIKE '%Cartoon Network HD%'" },
  { name: 'Boomerang', q: "name LIKE 'Boomerang' AND group_name = 'TV'" },
  { name: 'Disney Channel', q: "name LIKE '%Disney Channel%' AND group_name = 'Kids'" },
  { name: 'Disney Junior', q: "name LIKE '%Disney Junior%' AND group_name = 'Kids'" },
  { name: 'Nickelodeon', q: "name LIKE '%Nickelodeon%' AND group_name = 'Kids'" },
  { name: 'Nick Jr', q: "name LIKE '%Nick Jr%' AND group_name = 'Kids'" },
  { name: 'TeenNick', q: "name LIKE '%TeenNick%' AND group_name = 'Kids'" },
  { name: 'Polsat JimJam', q: "name LIKE '%Polsat JimJam%'" },
  { name: 'Nick Jr. Latin America', q: "name LIKE '%Nick Jr. Latin America%'" },
  // Religiosos (6)
  { name: 'Enlace', q: "name LIKE 'Enlace%' AND group_name = 'Religious'" },
  { name: 'EWTN', q: "name LIKE 'EWTN%' AND group_name = 'Religious'" },
  { name: 'Cristovision', q: "name LIKE '%Cristovision%'" },
  { name: 'Alkarma TV', q: "name LIKE '%Alkarma TV%'" },
  { name: 'CSat TV', q: "name LIKE '%CSat TV%'" },
  { name: 'Maria Vision', q: "name LIKE '%Maria Vision%'" },
  // Música (5)
  { name: 'MTV Live', q: "name LIKE '%MTV Live%'" },
  { name: 'Trace Latina', q: "name LIKE '%Trace Latina%'" },
  { name: 'MTV Biggest Pop', q: "name LIKE '%MTV Biggest Pop%'" },
  { name: 'Trace Brazuca', q: "name LIKE '%Trace Brazuca%'" },
  { name: 'Rabeh Saqer', q: "name LIKE '%Rabeh Saqer%'" },
  // Adultos (4)
  { name: 'Playboy TV', q: "name LIKE '%Playboy TV%'" },
  { name: 'Venus', q: "name LIKE '%Venus%' AND (group_name = 'XXX' OR group_name = 'Adult')" },
  { name: 'Hot', q: "name LIKE '%Hot%' AND (group_name = 'XXX' OR group_name = 'Adult')" },
  { name: 'Redlight', q: "name LIKE '%Redlight%' AND (group_name = 'XXX' OR group_name = 'Adult')" },
];

async function main() {
  const db = new Database(DB_PATH, { timeout: 30000 });
  db.pragma('journal_mode = WAL');

  console.log('=== FINAL FIX ===\n');

  // PASO 1: Limpiar planes y backups
  db.prepare('DELETE FROM plan_channels').run();
  db.prepare('DELETE FROM channel_backups').run();
  console.log('Planes y backups limpiados');

  // PASO 2: Buscan canales y asignar a TODOS los planes
  console.log('\n--- Asignando canales (misma parrilla para todos) ---');
  const plans = db.prepare('SELECT id, name FROM plans ORDER BY id').all();
  const selectedChannels = []; // [{id, name, url, group}]
  const notFound = [];

  for (const q of QUERIES) {
    const ch = db.prepare(`
      SELECT c.id, c.name, c.stream_url, c.group_name
      FROM channels c
      LEFT JOIN channel_health h ON h.channel_id = c.id
      WHERE c.is_active = 1 AND (${q.q})
      ORDER BY
        CASE WHEN h.is_alive = 1 THEN 0
             WHEN h.is_alive IS NULL THEN 1
             ELSE 2 END,
        c.name
      LIMIT 1
    `).get();

    if (ch) {
      selectedChannels.push(ch);
    } else {
      notFound.push(q.name);
    }
  }

  console.log(`  Encontrados: ${selectedChannels.length}/${QUERIES.length}`);
  if (notFound.length > 0) {
    console.log(`  No encontrados: ${notFound.join(', ')}`);
  }

  // Asignar los mismos canales a los 3 planes
  for (const plan of plans) {
    for (const ch of selectedChannels) {
      db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(plan.id, ch.id);
    }
    console.log(`  ${plan.name}: ${selectedChannels.length} canales`);
  }

  // PASO 3: Verificar canales en planes
  console.log('\n--- Verificando canales ---');
  const planChs = selectedChannels;

  let alive = 0, dead = 0;
  const deadList = [];

  for (let i = 0; i < planChs.length; i += 20) {
    const batch = planChs.slice(i, i + 20);
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

      if (r.ok) alive++;
      else { dead++; deadList.push(r.ch); }
    }
    await new Promise(r => setTimeout(r, 100));
  }

  console.log(`  Vivos: ${alive} | Muertos: ${dead}`);

  // PASO 4: Reemplazar muertos
  console.log('\n--- Reemplazando muertos ---');
  let replaced = 0;

  for (const dch of deadList) {
    const alts = db.prepare(`
      SELECT c.id, c.name, c.stream_url
      FROM channels c
      LEFT JOIN channel_health h ON h.channel_id = c.id
      WHERE c.group_name = ? AND c.id != ? AND c.is_active = 1
      AND (h.is_alive = 1 OR h.is_alive IS NULL)
      AND c.id NOT IN (SELECT channel_id FROM plan_channels)
      ORDER BY
        CASE WHEN h.is_alive = 1 THEN 0 ELSE 1 END,
        RANDOM()
      LIMIT 8
    `).all(dch.group_name, dch.id);

    let found = false;
    for (const alt of alts) {
      let altHealth = db.prepare('SELECT is_alive FROM channel_health WHERE channel_id = ?').get(alt.id);
      if (!altHealth) {
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
        altHealth = { is_alive: r.ok ? 1 : 0 };
      }

      if (altHealth.is_alive === 1) {
        for (const plan of plans) {
          db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(plan.id, dch.id);
          db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(plan.id, alt.id);
        }
        console.log(`  ✓ ${dch.name.substring(0, 45).padEnd(47)} → ${alt.name.substring(0, 40)}`);
        found = true;
        replaced++;
        break;
      }
    }

    if (!found) {
      // Cualquier canal vivo
      const anyAlive = db.prepare(`
        SELECT c.id, c.name, c.stream_url
        FROM channels c
        JOIN channel_health h ON h.channel_id = c.id AND h.is_alive = 1
        WHERE c.id != ? AND c.is_active = 1
        AND c.id NOT IN (SELECT channel_id FROM plan_channels)
        ORDER BY RANDOM() LIMIT 3
      `).all(dch.id);

      for (const alt of anyAlive) {
        for (const plan of plans) {
          db.prepare('DELETE FROM plan_channels WHERE plan_id = ? AND channel_id = ?').run(plan.id, dch.id);
          db.prepare('INSERT OR IGNORE INTO plan_channels (plan_id, channel_id) VALUES (?, ?)').run(plan.id, alt.id);
        }
        console.log(`  ✓ ${dch.name.substring(0, 45).padEnd(47)} → ${alt.name.substring(0, 40)} (genérico)`);
        found = true;
        replaced++;
        break;
      }
    }

    if (!found) {
      console.log(`  ✗ SIN REEMPLAZO: ${dch.name.substring(0, 50)}`);
    }
  }

  // PASO 5: Generar backups
  console.log('\n--- Generando backups ---');
  const finalPCs = db.prepare(`
    SELECT DISTINCT c.id, c.group_name
    FROM plan_channels pc
    JOIN channels c ON c.id = pc.channel_id
  `).all();

  let bkCreated = 0;
  const insertBk = db.prepare('INSERT OR IGNORE INTO channel_backups (channel_id, backup_channel_id, priority) VALUES (?, ?, ?)');

  for (const pc of finalPCs) {
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

  // Resumen
  console.log('\n=== RESUMEN ===');
  for (const plan of plans) {
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

  db.close();
}

main().catch(console.error);
