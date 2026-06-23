import Database from 'better-sqlite3';
import https from 'https';
import http from 'http';
import path from 'path';
import { DATA_DIR } from '../src/config/constants.js';

const db = new Database(path.join(DATA_DIR, 'streamflow.sqlite'), { timeout: 10000 });
db.pragma('journal_mode = WAL');

const BASE = 'https://iptv-org.github.io/iptv/countries/';

function fetch(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    mod.get(url, { headers: { 'User-Agent': 'Mozilla/5.0', 'Accept': '*/*' } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return fetch(res.headers.location).then(resolve).catch(reject);
      }
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
    }).on('error', reject);
  });
}

async function importCountry(code, name) {
  try {
    const url = BASE + code + '.m3u';
    const m3u = await fetch(url);
    if (!m3u || m3u.length < 100 || m3u.startsWith('<')) {
      console.log('  [!] ' + name + ' (' + code + '): sin contenido valido');
      return 0;
    }

    const lines = m3u.split('\n');
    let imported = 0;
    let channelName = '';
    let logo = '';
    let groupName = '';

    const insertChannel = db.prepare(
      'INSERT OR IGNORE INTO channels (provider_id, name, logo, group_name, stream_url) VALUES (2, ?, ?, ?, ?)'
    );

    const importMany = db.transaction(() => {
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (line.startsWith('#EXTINF:')) {
          const nameMatch = line.match(/,(.+)$/);
          channelName = nameMatch ? nameMatch[1].trim() : 'Channel ' + (imported + 1);
          const logoMatch = line.match(/tvg-logo="([^"]+)"/);
          logo = logoMatch ? logoMatch[1] : '';
          const groupMatch = line.match(/group-title="([^"]+)"/);
          groupName = groupMatch ? groupMatch[1] : name;
        } else if (line && !line.startsWith('#') && channelName && line.startsWith('http')) {
          insertChannel.run(channelName, logo, groupName, line);
          imported++;
          channelName = '';
        }
      }
    });

    importMany();
    console.log('  [OK] ' + name + ' (' + code + '): ' + imported + ' canales');
    return imported;
  } catch (e) {
    console.log('  [ERR] ' + name + ' (' + code + '): ' + (e.message || '').substring(0, 60));
    return 0;
  }
}

async function main() {
  console.log('=== IMPORTANDO CANALES GRATIS DE IPTV.ORG ===');

  const countries = [
    ['co', 'Colombia'],
    ['mx', 'Mexico'],
    ['ar', 'Argentina'],
    ['es', 'Espana'],
    ['pe', 'Peru'],
    ['cl', 'Chile'],
    ['ec', 'Ecuador'],
    ['ve', 'Venezuela'],
    ['us', 'USA'],
    ['br', 'Brasil'],
  ];

  let total = 0;
  for (const [code, name] of countries) {
    total += await importCountry(code, name);
    await new Promise(r => setTimeout(r, 300));
  }

  const totalCh = db.prepare('SELECT COUNT(*) as c FROM channels').get().c;
  const groups = db.prepare('SELECT group_name, COUNT(*) as count FROM channels GROUP BY group_name ORDER BY count DESC LIMIT 10').all();

  console.log('\n=== RESUMEN ===');
  console.log('Total importados: ' + total);
  console.log('Total en BD: ' + totalCh);
  console.log('\nTop 10 grupos:');
  for (const g of groups) {
    console.log('  ' + g.group_name + ': ' + g.count + ' ch');
  }

  // Muestra primeros 5
  console.log('\nMuestra:');
  const sample = db.prepare('SELECT id, name, group_name FROM channels LIMIT 5').all();
  for (const s of sample) {
    console.log('  [' + s.id + '] ' + s.name + ' (' + s.group_name + ')');
  }
}

main().catch(console.error);
