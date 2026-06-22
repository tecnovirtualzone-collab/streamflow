/**
 * WhatsApp Web Service for StreamFlow
 * Uses whatsapp-web.js to generate QR and handle messages
 * 
 * Usage: node whatsapp_service.js [session_id]
 */

import pkg from 'whatsapp-web.js';
const { Client, LocalAuth } = pkg;
import QRCode from 'qrcode';
import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';

const DATA_DIR = process.env.DATA_DIR || '/data';
const SESSION_DIR = path.join(DATA_DIR, 'whatsapp_sessions');

if (!fs.existsSync(SESSION_DIR)) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
}

const db = new Database(path.join(DATA_DIR, 'streamflow.sqlite'), { timeout: 5000 });
db.pragma('journal_mode = WAL');

const sessionId = process.argv[2] || `sf_${Date.now()}`;

console.log(`[WA] Starting WhatsApp session: ${sessionId}`);

const updateSession = (fields) => {
  try {
    const sets = Object.keys(fields).map(k => `${k} = ?`).join(', ');
    const values = Object.values(fields);
    db.prepare(`UPDATE whatsapp_sessions SET ${sets} WHERE session_id = ?`).run(...values, sessionId);
  } catch (err) {
    console.error('[WA] DB update error:', err.message);
  }
};

// Create session record
try {
  db.prepare('INSERT OR REPLACE INTO whatsapp_sessions (session_id, status) VALUES (?, ?)').run(sessionId, 'initializing');
} catch (err) {
  console.error('[WA] Session creation error:', err.message);
}

const client = new Client({
  authStrategy: new LocalAuth({
    clientId: sessionId,
    dataPath: SESSION_DIR
  }),
  puppeteer: {
    headless: true,
    executablePath: '/snap/chromium/current/usr/lib/chromium-browser/chrome',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--no-first-run',
      '--no-zygote',
      '--single-process',
      '--disable-dbus',
      '--disable-software-rasterizer'
    ]
  }
});

client.on('qr', async (qr) => {
  console.log('[WA] QR Code received');
  
  try {
    const qrDataUrl = await QRCode.toDataURL(qr);
    updateSession({ 
      status: 'waiting_scan',
      qr_code: qrDataUrl,
      last_seen: Math.floor(Date.now() / 1000)
    });
    console.log('[WA] QR saved to DB');
  } catch (err) {
    console.error('[WA] QR generation error:', err);
    updateSession({ 
      status: 'waiting_scan',
      qr_code: qr,
      last_seen: Math.floor(Date.now() / 1000)
    });
  }
});

client.on('ready', () => {
  const phone = client.info.wid.user;
  console.log(`[WA] Connected as +${phone}`);
  updateSession({
    status: 'connected',
    phone: `+${phone}`,
    connected_at: Math.floor(Date.now() / 1000),
    last_seen: Math.floor(Date.now() / 1000),
    qr_code: ''
  });
  db.prepare("UPDATE settings SET value = ? WHERE key = 'whatsapp_enabled'").run('1');
  db.prepare("UPDATE settings SET value = ? WHERE key = 'whatsapp_session_id'").run(sessionId);
});

client.on('authenticated', () => {
  console.log('[WA] Authenticated');
  updateSession({ status: 'authenticated' });
});

client.on('auth_failure', (msg) => {
  console.error('[WA] Auth failure:', msg);
  updateSession({ status: 'auth_failure' });
});

client.on('disconnected', (reason) => {
  console.log('[WA] Disconnected:', reason);
  updateSession({ status: 'disconnected' });
  db.prepare("UPDATE settings SET value = ? WHERE key = 'whatsapp_enabled'").run('0');
});

client.on('message', async (msg) => {
  try {
    const contact = await msg.getContact();
    const phone = contact.number;
    
    db.prepare(
      'INSERT INTO whatsapp_messages (session_id, direction, from_number, message, message_type, status) VALUES (?, ?, ?, ?, ?, ?)'
    ).run(sessionId, 'incoming', phone, msg.body, msg.type, 'received');
    
    console.log(`[WA] Message from +${phone}: ${msg.body}`);
    
    // Auto-reply
    const body = (msg.body || '').toLowerCase();
    if (body.includes('hola') || body.includes('info') || body.includes('ayuda')) {
      client.sendMessage(msg.from, '¡Hola! 👋 Bienvenido a StreamFlow TV.\n\n📺 *Nuestros Planes:*\n✅ Básico - $10,000 COP/mes (40 canales)\n✅ Estándar - $18,000 COP/mes (70 canales)\n✅ Premium - $25,000 COP/mes (100+ canales)\n\n📞 +57 322 2468509');
    } else if (body.includes('planes') || body.includes('precio') || body.includes('costo')) {
      client.sendMessage(msg.from, '📺 *Planes StreamFlow TV*\n\n🥉 *Básico* - $10,000 COP/mes\n   📡 40 canales | 1 conexión\n\n🥈 *Estándar* - $18,000 COP/mes\n   📡 70 canales | 2 conexiones\n\n🥇 *Premium* - $25,000 COP/mes\n   📡 100+ canales | 3 conexiones\n\n💬 Escribe "suscribir" para más info.');
    } else if (body.includes('suscribir') || body.includes('pagar') || body.includes('pago')) {
      client.sendMessage(msg.from, '💳 *Para suscribirte:*\n\n1. Elige tu plan\n2. Realiza el pago por Nequi/Daviplata: 322 2468509\n3. Envía comprobante por este chat\n4. Te activamos en menos de 1 hora\n\n¿Qué plan te interesa?');
    }
  } catch (err) {
    console.error('[WA] Message handling error:', err);
  }
});

client.on('message_create', async (msg) => {
  if (msg.fromMe) {
    db.prepare(
      'INSERT INTO whatsapp_messages (session_id, direction, to_number, message, message_type, status) VALUES (?, ?, ?, ?, ?, ?)'
    ).run(sessionId, 'outgoing', msg.to, msg.body, msg.type, 'sent');
  }
});

// Catch unhandled errors from puppeteer/whatsapp-web
process.on('unhandledRejection', (err) => {
  console.error('[WA] Unhandled rejection:', err.message || err);
  // Don't exit - keep trying
});

process.on('uncaughtException', (err) => {
  console.error('[WA] Uncaught exception:', err.message || err);
  // Don't exit - keep trying
});

client.initialize().catch(err => {
  console.error('[WA] Init error:', err.message || err);
  updateSession({ status: 'error' });
  // Retry after 30 seconds
  setTimeout(() => {
    console.log('[WA] Retrying initialization...');
    client.initialize().catch(e => console.error('[WA] Retry failed:', e.message));
  }, 30000);
});

process.on('SIGTERM', async () => {
  console.log('[WA] Shutting down...');
  await client.destroy();
  process.exit(0);
});

process.on('SIGINT', async () => {
  console.log('[WA] Shutting down...');
  await client.destroy();
  process.exit(0);
});
