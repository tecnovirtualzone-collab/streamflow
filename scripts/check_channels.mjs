/**
 * Channel Health Checker — Verifica que los canales respondan
 * 
 * Uso:
 *   node scripts/check_channels.mjs          # Check all channels
 *   node scripts/check_channels.mjs --sample # Check only 50 random channels
 *   node scripts/check_channels.mjs --clean  # Remove dead channels
 * 
 * Cron: cada 6 horas
 */

import Database from 'better-sqlite3';
import https from 'https';
import http from 'http';
import path from 'path';
import { DATA_DIR } from '../src/config/constants.js';

const DB_PATH = path.join(DATA_DIR, 'streamflow.sqlite');
const TIMEOUT = 10000; // 10 segundos por canal
const CONCURRENT = 10; // 10 checks simultáneos

const args = process.argv.slice(2);
const MODE_SAMPLE = args.includes('--sample');
const MODE_CLEAN = args.includes('--clean');
const MODE_FULL = !MODE_SAMPLE && !MODE_CLEAN;

function testUrl(url) {
  return new Promise((resolve) => {
    try {
      const mod = url.startsWith('https') ? https : http;
      const req = mod.get(url, {
        headers: { 
          'User-Agent': 'VLC/3.0.0 LibVLC/3.0.0',
          'Accept': '*/*',
          'Connection': 'close'
        },
        timeout: TIMEOUT
      }, (res) => {
        // Accept 2xx and 3xx (redirects) as potentially working
        if (res.statusCode >= 400) {
          return resolve({ ok: false, status: res.statusCode, error: 'HTTP ' + res.statusCode });
        }
        // Read first bytes to verify it's actually media
        let got = false;
        res.on('data', (chunk) => {
          if (got) return;
          got = true;
          res.destroy();
          // Check for valid media signatures
          const hex = chunk.slice(0, 4).toString('hex');
          const isMedia = 
            chunk[0] === 0x47 || // MPEG-TS sync byte
            chunk.slice(0, 7).toString().includes('#EXTM3U') || // HLS playlist
            hex.match(/^(000000|6674779|1a45dfa3)/) || // MP4/WEBM/MKV
            chunk.slice(0, 3).toString() === 'FLV' || // FLV
            res.statusCode < 400; // If status is OK and we got data, likely works
          resolve({ ok: isMedia, status: res.statusCode });
        });
        res.on('error', () => resolve({ ok: false, error: 'response error' }));
      });
      req.on('error', (e) => resolve({ ok: false, error: e.message?.substring(0, 50) }));
      req.on('timeout', () => { req.destroy(); resolve({ ok: false, error: 'timeout' }); });
    } catch (e) {
      resolve({ ok: false, error: e.message?.substring(0, 50) });
    }
  });
}

async function checkChannels(db, channels) {
  let working = 0;
  let failed = 0;
  const deadChannels = [];

  // Process in batches
  for (let i = 0; i < channels.length; i += CONCURRENT) {
    const batch = channels.slice(i, i + CONCURRENT);
    const results = await Promise.all(batch.map(async (ch) => {
      const result = await testUrl(ch.stream_url);
      return { channel: ch, ...result };
    }));

    for (const r of results) {
      if (r.ok) {
        working++;
      } else {
        failed++;
        deadChannels.push(r.channel.id);
        if (deadChannels.length <= 20) {
          console.log('  [DEAD] ' + r.channel.name.substring(0, 40) + ' - ' + (r.error || 'status ' + r.status));
        }
      }
    }

    // Progress
    if (i % 100 === 0 && i > 0) {
      console.log('  Progreso: ' + (i + CONCURRENT) + '/' + channels.length + ' trabajando=' + working + ' muertos=' + failed);
    }

    // Small delay between batches
    await new Promise(r => setTimeout(r, 300));
  }

  return { working, failed, deadChannels };
}

async function main() {
  const db = new Database(DB_PATH, { timeout: 30000 });
  db.pragma('journal_mode = WAL');

  console.log('=== CHANNEL HEALTH CHECKER ===');
  console.log('Fecha: ' + new Date().toISOString());
  console.log('Timeout: ' + TIMEOUT + 'ms | Concurrente: ' + CONCURRENT);

  // Get channels to check
  let channels;
  if (MODE_SAMPLE) {
    channels = db.prepare("SELECT id, name, stream_url FROM channels WHERE stream_url LIKE 'http%' ORDER BY RANDOM() LIMIT 50").all();
    console.log('Modo: MUESTRA (50 canales aleatorios)');
  } else {
    channels = db.prepare("SELECT id, name, stream_url FROM channels WHERE stream_url LIKE 'http%'").all();
    console.log('Modo: COMPLETO (' + channels.length + ' canales)');
  }

  console.log('\nVerificando ' + channels.length + ' canales...\n');

  const startTime = Date.now();
  const { working, failed, deadChannels } = await checkChannels(db, channels);
  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

  console.log('\n=== RESULTADO ===');
  console.log('Tiempo: ' + elapsed + ' segundos');
  console.log('Verificados: ' + channels.length);
  console.log('Funcionando: ' + working + ' (' + (working/channels.length*100).toFixed(1) + '%)');
  console.log('Muertos: ' + failed + ' (' + (failed/channels.length*100).toFixed(1) + '%)');

  // Clean dead channels if requested
  if (MODE_CLEAN && deadChannels.length > 0) {
    console.log('\n=== LIMPIANDO ' + deadChannels.length + ' CANALES MUERTOS ===');
    
    db.pragma('foreign_keys = OFF');
    
    const deleteFromStats = db.prepare('DELETE FROM stream_stats WHERE channel_id = ?');
    const deleteFromPlanChannels = db.prepare('DELETE FROM plan_channels WHERE channel_id = ?');
    const deleteFromStreams = db.prepare('DELETE FROM current_streams WHERE channel_id = ?');
    const deleteChannel = db.prepare('DELETE FROM channels WHERE id = ?');
    
    const cleanup = db.transaction(() => {
      for (const id of deadChannels) {
        deleteFromStats.run(id);
        deleteFromPlanChannels.run(id);
        deleteFromStreams.run(id);
        deleteChannel.run(id);
      }
    });
    
    cleanup();
    db.pragma('foreign_keys = ON');
    
    const remaining = db.prepare('SELECT COUNT(*) as c FROM channels').get().c;
    console.log('Canales eliminados: ' + deadChannels.length);
    console.log('Canales restantes: ' + remaining);
  } else if (deadChannels.length > 0) {
    console.log('\n(Usa --clean para eliminar los ' + deadChannels.length + ' canales muertos)');
  }

  db.close();
}

main().catch(console.error);
