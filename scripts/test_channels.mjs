import Database from 'better-sqlite3';
import https from 'https';
import http from 'http';

const db = new Database('/data/streamflow.sqlite');

function testUrl(url) {
  return new Promise((resolve) => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(url, {
      headers: { 'User-Agent': 'VLC/3.0.0', 'Accept': '*/*' },
      timeout: 8000
    }, (res) => {
      // Check status
      if (res.statusCode >= 400) {
        return resolve({ ok: false, status: res.statusCode });
      }
      // Read first 100 bytes
      let got = false;
      res.on('data', (chunk) => {
        if (got) return;
        got = true;
        res.destroy();
        const isVideo = chunk.length > 10 && (
          chunk[0] === 0x47 || // MPEG-TS
          chunk.slice(0, 7).toString().includes('#EXTM3U') || // HLS
          chunk.slice(0, 4).toString().includes('ftyp') // MP4
        );
        resolve({ ok: isVideo, status: res.statusCode, size: chunk.length });
      });
      res.on('error', () => resolve({ ok: false }));
    });
    req.on('error', (e) => resolve({ ok: false, error: e.message?.substring(0, 40) }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, error: 'timeout' }); });
  });
}

async function main() {
  console.log('=== PROBANDO CANALES GRATIS ===\n');

  const samples = db.prepare("SELECT id, name, stream_url FROM channels WHERE stream_url LIKE 'http%' LIMIT 30").all();

  let working = 0;
  let failed = 0;

  for (const ch of samples) {
    const result = await testUrl(ch.stream_url);
    if (result.ok) {
      console.log('  [OK] ' + ch.name.substring(0, 45));
      working++;
    } else {
      console.log('  [FAIL] ' + ch.name.substring(0, 45) + ' - ' + (result.error || 'status ' + result.status));
      failed++;
    }
    // Small delay
    await new Promise(r => setTimeout(r, 200));
  }

  console.log('\n=== RESULTADO ===');
  console.log('Funcionando: ' + working + '/' + samples.length);
  console.log('Fallidos: ' + failed + '/' + samples.length);
  console.log('Tasa de exito: ' + (working/samples.length*100).toFixed(1) + '%');

  // Por pais
  console.log('\n=== CANALES POR PAIS ===');
  const groups = db.prepare("SELECT group_name, COUNT(*) as count FROM channels GROUP BY group_name ORDER BY count DESC LIMIT 15").all();
  for (const g of groups) {
    console.log('  ' + g.group_name + ': ' + g.count + ' ch');
  }
}

main().catch(console.error);
