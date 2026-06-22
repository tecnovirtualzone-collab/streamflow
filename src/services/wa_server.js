/**
 * WhatsApp Service — Microservicio separado para StreamFlow
 * Basado en gestingastos/wa-service (much más estable)
 * 
 * Endpoints:
 *   GET  /wa/status      — Estado del WA (ready, hasQr)
 *   GET  /wa/qr          — Página HTML con QR (auto-refresh 25s)
 *   POST /wa/send        — Enviar mensaje { phone, message }
 *   POST /wa/restart     — Reiniciar conexión WA
 */

import pkg from 'whatsapp-web.js';
const { Client, LocalAuth } = pkg;
import qrcode from 'qrcode';
import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';

const PORT = process.env.WA_PORT || 3001;
const DATA_DIR = process.env.DATA_DIR || '/data';
const SESSION_DIR = path.join(DATA_DIR, 'ww-session');
const DB_PATH = path.join(DATA_DIR, 'streamflow.sqlite');

// Asegurar directorio de sesión
if (!fs.existsSync(SESSION_DIR)) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
}

// Conexión a DB (solo lectura para verificar usuarios)
let db;
try {
  db = new Database(DB_PATH, { timeout: 5000 });
  db.pragma('journal_mode = WAL');
  console.log('✅ DB conectada');
} catch (e) {
  console.error('❌ Error DB:', e.message);
}

// ── WHATSAPP ──
let waReady = false;
let waQrUrl = null;
let waClient = null;
let waPhone = '';

function cleanLockFiles() {
  const base = path.join(SESSION_DIR, '.wwebjs_auth', 'session');
  ['SingletonLock', 'SingletonCookie', 'SingletonSocket'].forEach(f => {
    [path.join(base, 'Default', f), path.join(base, f)].forEach(p => {
      try { fs.unlinkSync(p); } catch {}
    });
  });
}

async function waSend(phone, text) {
  if (!waReady || !phone) return false;
  try {
    let clean = String(phone).replace(/\D/g, '');
    if (!clean || clean.length < 10) return false;
    // Agregar código de Colombia (57) si no lo tiene
    if (clean.length === 10 && !clean.startsWith('57')) {
      clean = '57' + clean;
    }
    const chatId = clean + '@c.us';
    const isRegistered = await waClient.isRegisteredUser(chatId).catch(() => false);
    if (!isRegistered) {
      console.log('❌ No registrado en WA:', clean);
      return false;
    }
    await waClient.sendMessage(chatId, text);
    console.log('✅ WA enviado a', clean);
    return true;
  } catch (e) {
    console.error('❌ WA send:', e.message);
    return false;
  }
}

function initWhatsApp() {
  cleanLockFiles();

  waClient = new Client({
    authStrategy: new LocalAuth({
      clientId: `sf_${Date.now()}`,
      dataPath: SESSION_DIR
    }),
    puppeteer: {
      headless: true,
      executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/snap/chromium/current/usr/lib/chromium-browser/chrome',
      args: [
        '--no-sandbox', '--disable-setuid-sandbox',
        '--disable-dev-shm-usage', '--disable-gpu',
        '--no-first-run', '--no-zygote',
        '--single-process', '--disable-extensions',
        '--disable-dbus', '--disable-software-rasterizer'
      ],
    },
  });

  waClient.on('qr', async qr => {
    try {
      waQrUrl = await qrcode.toDataURL(qr);
    } catch {
      waQrUrl = qr;
    }
    waReady = false;
    console.log('📱 QR generado — visita /wa/qr');
  });

  waClient.on('ready', () => {
    waReady = true;
    waQrUrl = null;
    waPhone = waClient.info?.wid?.user || '';
    console.log(`✅ WhatsApp conectado como +${waPhone}`);
  });

  waClient.on('authenticated', () => {
    console.log('🔐 WA autenticado');
  });

  waClient.on('auth_failure', () => {
    waReady = false;
    console.log('❌ WA auth failure — reiniciando en 5s...');
    setTimeout(initWhatsApp, 5000);
  });

  waClient.on('disconnected', (reason) => {
    waReady = false;
    console.log('⚠️ WA desconectado:', reason, '- reconectando en 5s...');
    setTimeout(initWhatsApp, 5000);
  });

  // Errores no capturados de puppeteer (ignorar)
  process.on('unhandledRejection', (err) => {
    console.error('[WA] Unhandled:', err.message || err);
  });
  process.on('uncaughtException', (err) => {
    console.error('[WA] Uncaught:', err.message || err);
  });

  waClient.initialize().catch(err => {
    console.error('❌ WA init error:', err.message);
    setTimeout(initWhatsApp, 10000);
  });
}

// ── EXPRESS SERVER ──
import express from 'express';
const app = express();
app.use(express.json());

// CORS
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  if (req.method === 'OPTIONS') return res.sendStatus(200);
  next();
});

// Estado
app.get('/wa/status', (req, res) => {
  res.json({ ready: waReady, hasQr: !!waQrUrl, phone: waPhone });
});

// Enviar mensaje
app.post('/wa/send', async (req, res) => {
  const { phone, message } = req.body;
  if (!phone || !message) return res.status(400).json({ error: 'phone y message requeridos' });
  const ok = await waSend(phone, message);
  res.json({ ok, success: ok });
});

// Reiniciar WA
app.post('/wa/restart', (req, res) => {
  console.log('🔄 Reinicio WA solicitado');
  waReady = false;
  waQrUrl = null;
  if (waClient) {
    waClient.destroy().catch(() => {});
  }
  setTimeout(initWhatsApp, 1000);
  res.json({ ok: true, message: 'Reiniciando WA...' });
});

// QR page con auto-refresh
const qrCSS = `<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#080b12;color:#e2e8f0;font-family:sans-serif;display:flex;
    flex-direction:column;align-items:center;justify-content:center;
    min-height:100vh;gap:20px;padding:24px;text-align:center}
  h2{font-size:22px;font-weight:700}
  p{color:#64748b;font-size:13px;line-height:1.8;max-width:300px}
  img{border-radius:16px;width:260px;height:260px;border:4px solid #3b82f6;
    box-shadow:0 0 40px rgba(59,130,246,.3)}
  .badge{background:rgba(16,185,129,.15);color:#10b981;border-radius:99px;
    padding:8px 22px;font-size:13px;border:1px solid rgba(16,185,129,.3)}
  .steps{background:#0e1320;border:1px solid #1e2a42;border-radius:14px;
    padding:16px 20px;text-align:left;font-size:13px;color:#94a3b8;line-height:2.4;max-width:300px}
  .error{background:rgba(239,68,68,.15);color:#ef4444;border-radius:99px;
    padding:8px 22px;font-size:13px;border:1px solid rgba(239,68,68,.3)}
</style>`;

app.get('/wa/qr', (req, res) => {
  if (waReady) {
    return res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <title>StreamFlow WA ✓</title>${qrCSS}</head><body>
      <div class="badge">✅ WhatsApp Conectado</div>
      <h2>¡Todo listo! 🎉</h2>
      <p>WhatsApp está conectado como <strong>+${waPhone}</strong><br>Enviando notificaciones automáticas a tus clientes.</p>
    </body></html>`);
  }

  if (waQrUrl) {
    return res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8">
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <meta http-equiv="refresh" content="25">
      <title>Escanear QR</title>${qrCSS}</head><body>
      <h2>📱 Escanea el QR</h2>
      <img src="${waQrUrl}" alt="QR"/>
      <div class="steps">
        1. Abre WhatsApp en tu celular<br>
        2. ⋮ → <strong>Dispositivos vinculados</strong><br>
        3. Toca <strong>Vincular dispositivo</strong><br>
        4. Apunta la cámara al QR
      </div>
      <p>Se renueva cada 25 seg automáticamente.</p>
    </body></html>`);
  }

  return res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta http-equiv="refresh" content="4"><title>Iniciando...</title>${qrCSS}</head><body>
    <p>Iniciando WhatsApp... ⏳<br>Esta página se actualiza sola.</p>
  </body></html>`);
});

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok', waReady, hasQr: !!waQrUrl, port: PORT });
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 WA Service corriendo en puerto ${PORT}`);
  console.log(`📱 QR disponible en http://0.0.0.0:${PORT}/wa/qr`);
  initWhatsApp();
});

// Shutdown limpio
process.on('SIGTERM', async () => {
  console.log('🛑 WA Service shutdown...');
  if (waClient) await waClient.destroy().catch(() => {});
  process.exit(0);
});
process.on('SIGINT', async () => {
  console.log('🛑 WA Service shutdown...');
  if (waClient) await waClient.destroy().catch(() => {});
  process.exit(0);
});
