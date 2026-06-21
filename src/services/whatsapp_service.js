/**
 * WhatsApp Web Service for StreamFlow
 * Uses whatsapp-web.js to generate QR and handle messages
 * 
 * Usage: node whatsapp_service.js [session_id]
 */

import { Client, LocalAuth } from 'whatsapp-web.js';
import QRCode from 'qrcode';
import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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
  const sets = Object.keys(fields).map(k => `${k} = ?`).join(', ');
  const values = Object.values(fields);
  db.prepare(`UPDATE whatsapp_sessions SET ${sets} WHERE session_id = ?`).run(...values, sessionId);
};

const client = new Client({
  authStrategy: new LocalAuth({
    clientId: sessionId,
    dataPath: SESSION_DIR
  }),
  puppeteer: {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--no-first-run',
      '--no-zygote',
      '--single-process'
    ]
  }
});

client.on('qr', async (qr) => {
  console.log('[WA] QR Code received');
  
  // Generate QR as data URL for web display
  try {
    const qrDataUrl = await QRCode.toDataURL(qr);
    updateSession({ 
      status: 'waiting_scan',
      qr_code: qrDataUrl,
      last_seen: Math.floor(Date.now() / 1000)
    });
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

client.initialize().catch(err => {
  console.error('[WA] Init error:', err);
  updateSession({ status: 'error' });
  process.exit(1);
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
