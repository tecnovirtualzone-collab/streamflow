import urllib.request, subprocess

// Test: Verificar que los canales gratis responden
console.log("=== PROBANDO CANALES GRATIS ===\n");

const channels = [
  { name: "Colombia - Canal 13", url: "" },
  { name: "Mexico - Imagen TV", url: "" },
  { name: - "Spain - TVE", url: "" },
];

// Obtener canales reales de la BD
import Database from 'better-sqlite3';
import path from 'path';
const db = new Database('/data/streamflow.sqlite');

const samples = db.prepare("SELECT id, name, stream_url, group_name FROM channels WHERE stream_url LIKE 'http%' LIMIT 20").all();

let working = 0;
let failed = 0;

for (const ch of samples) {
  try {
    const req = urllib.request.Request(ch.stream_url, {
      headers: { 'User-Agent': 'VLC/3.0.0' }
    });
    const r = urllib.request.urlopen(req, timeout=8);
    const chunk = r.read(100);
    // Check if MPEG-TS (0x47) or HLS (EXTM3U) or other video
    const isVideo = chunk.length > 10 && (
      chunk[0] === 0x47 || // MPEG-TS
      chunk.slice(0, 7).toString().includes('#EXTM3U') || // HLS
      chunk.slice(0, 4).toString().includes('ftyp') // MP4
    );
    if (isVideo) {
      console.log(`  [OK] ${ch.name.substring(0, 40)}`);
      console.log(`       URL: ${ch.stream_url.substring(0, 60)}`);
      working++;
    } else {
      console.log(`  [??] ${ch.name.substring(0, 40)} - no video header`);
      failed++;
    }
  } catch (e) {
    console.log(`  [FAIL] ${ch.name.substring(0, 40)}`);
    console.log(`         Error: ${(e.message || '').substring(0, 60)}`);
    failed++;
  }
}

console.log(`\n=== RESULTADO ===`);
console.log(`Funcionando: ${working}/${samples.length}`);
console.log(`Fallidos: ${failed}/${samples.length}`);
console.log(`Tasa de exito: ${(working/samples.length*100).toFixed(1)}%`);
