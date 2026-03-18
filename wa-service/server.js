const express = require('express');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode  = require('qrcode');
const cron    = require('node-cron');
const { Pool } = require('pg');

const app  = express();
const PORT = process.env.WA_PORT || 3001;
const TZ   = process.env.TIMEZONE || 'America/Bogota';
const DB   = process.env.DATABASE_URL;
const DIAS_AVISO = parseInt(process.env.DIAS_AVISO || '3'); // días antes de vencer

app.use(express.json());

// ── BASE DE DATOS ──
const pool = new Pool({ connectionString: DB });

// ── WHATSAPP ──
let waReady = false;
let waQrUrl = null;
let waClient = null;

async function waSend(phone, text) {
  if (!waReady || !phone) return false;
  try {
    const clean = String(phone).replace(/\D/g, '');
    if (!clean || clean.length < 10) return false;
    const chatId = clean + '@c.us';
    const isRegistered = await waClient.isRegisteredUser(chatId).catch(() => false);
    if (!isRegistered) { console.log('❌ No registrado en WA:', clean); return false; }
    await waClient.sendMessage(chatId, text);
    console.log('✅ WA enviado a', clean);
    return true;
  } catch (e) {
    console.error('❌ WA send:', e.message);
    return false;
  }
}

function cleanLockFiles() {
  const fs = require('fs');
  const base = '/app/ww-session/.wwebjs_auth/session';
  ['SingletonLock','SingletonCookie','SingletonSocket'].forEach(f => {
    [base+'/Default/'+f, base+'/'+f].forEach(p => {
      try { fs.unlinkSync(p); } catch {}
    });
  });
}

function startCrons() {
  // Verificar usuarios por vencer cada día a las 9 AM
  cron.schedule('0 9 * * *', async () => {
    try {
      const { rows: users } = await pool.query(`
        SELECT u.usuario, u.notas, u.fecha_expira,
               EXTRACT(DAY FROM (u.fecha_expira - NOW())) as dias_restantes
        FROM usuarios u
        WHERE u.activo = TRUE
          AND u.notas IS NOT NULL
          AND u.notas != ''
          AND u.fecha_expira > NOW()
          AND u.fecha_expira <= NOW() + INTERVAL '${DIAS_AVISO} days'
      `);

      for (const u of users) {
        const dias = Math.ceil(parseFloat(u.dias_restantes));
        const host = process.env.APP_URL || '';
        await waSend(u.notas,
          `⚠️ *¡Tu suscripción StreamFlow vence pronto!*\n\n` +
          `Hola *${u.usuario}*, tu acceso vence en *${dias} día${dias !== 1 ? 's' : ''}*.\n\n` +
          `Para no quedarte sin servicio, renueva ahora 👇\n` +
          (host ? `📱 ${host}\n` : '') +
          `\n_Soporte vía WhatsApp_ 💬`
        );
      }
      console.log(`✅ Cron vencimiento: ${users.length} avisos enviados`);
    } catch (e) { console.error('Cron vencimiento:', e.message); }
  }, { timezone: TZ });

  // Usuarios expirados — avisarles al día siguiente
  cron.schedule('0 10 * * *', async () => {
    try {
      const { rows: users } = await pool.query(`
        SELECT u.usuario, u.notas
        FROM usuarios u
        WHERE u.activo = TRUE
          AND u.notas IS NOT NULL
          AND u.notas != ''
          AND u.fecha_expira < NOW()
          AND u.fecha_expira >= NOW() - INTERVAL '1 day'
      `);
      for (const u of users) {
        await waSend(u.notas,
          `😔 *Tu suscripción StreamFlow ha expirado*\n\n` +
          `Hola *${u.usuario}*, tu acceso venció hoy.\n\n` +
          `Para reactivar tu servicio, contáctanos 👇\n` +
          `_Soporte vía WhatsApp_ 💬`
        );
      }
      console.log(`✅ Cron expirados: ${users.length} avisos enviados`);
    } catch (e) { console.error('Cron expirados:', e.message); }
  }, { timezone: TZ });

  console.log(`⏰ Crons activos (TZ: ${TZ})`);
}

function initWhatsApp() {
  cleanLockFiles();

  waClient = new Client({
    authStrategy: new LocalAuth({ clientId: 'streamflow', dataPath: '/app/ww-session' }),
    puppeteer: {
      headless: true,
      executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
      args: [
        '--no-sandbox','--disable-setuid-sandbox',
        '--disable-dev-shm-usage','--disable-gpu',
        '--no-first-run','--no-zygote',
        '--single-process','--disable-extensions',
      ],
    },
  });

  waClient.on('qr', async qr => {
    waQrUrl = await qrcode.toDataURL(qr);
    waReady = false;
    console.log('📱 QR generado — visita /wa/qr');
  });

  waClient.on('ready', () => {
    waReady = true;
    waQrUrl = null;
    console.log('✅ WhatsApp conectado');
    startCrons();
  });

  waClient.on('auth_failure', () => {
    waReady = false;
    console.log('❌ WA auth failure — reiniciando...');
    setTimeout(initWhatsApp, 5000);
  });

  waClient.on('disconnected', () => {
    waReady = false;
    console.log('⚠️ WA desconectado — reconectando...');
    setTimeout(initWhatsApp, 5000);
  });

  waClient.initialize();
}

// ── ENDPOINTS ──

// Estado del WA
app.get('/status', (req, res) => {
  res.json({ ready: waReady, hasQr: !!waQrUrl });
});

// Enviar mensaje manual
app.post('/send', async (req, res) => {
  const { phone, message } = req.body;
  if (!phone || !message) return res.status(400).json({ error: 'phone y message requeridos' });
  const ok = await waSend(phone, message);
  res.json({ ok });
});

// QR page
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
</style>`;

app.get('/qr', (req, res) => {
  if (waReady) return res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>StreamFlow WA ✓</title>${qrCSS}</head><body>
    <div class="badge">✅ WhatsApp conectado</div>
    <h2>¡Todo listo! 🎉</h2>
    <p>WhatsApp está conectado y enviando notificaciones automáticas a tus clientes.</p>
  </body></html>`);

  if (waQrUrl) return res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8">
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

  return res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8">
    <meta http-equiv="refresh" content="4"><title>Iniciando...</title>${qrCSS}</head><body>
    <p>Iniciando WhatsApp... ⏳<br>Esta página se actualiza sola.</p>
  </body></html>`);
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`🚀 WA Service corriendo en puerto ${PORT}`);
  initWhatsApp();
});
