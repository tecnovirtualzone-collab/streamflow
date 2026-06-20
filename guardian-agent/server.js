#!/usr/bin/env node
/**
 * StreamFlow Guardian Agent 🛡️
 * ==============================
 * Agente autónomo que gestiona StreamFlow 24/7
 * 
 * Responsabilidades:
 * 1. Monitorear que el servidor esté corriendo
 * 2. Verificar canales cada 2 horas
 * 3. Detectar y reemplazar canales caídos
 * 4. Gestionar fechas de vencimiento de usuarios
 * 5. Enviar alertas por WhatsApp
 * 6. Generar reportes diarios
 * 7. Reiniciar servicios si fallan
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const { exec } = require('child_process');
const cron = require('node-cron');
const http = require('http');

// ═══════════════════════════════════════════════════════════
// CONFIGURACIÓN
// ═══════════════════════════════════════════════════════════

const CONFIG = {
    // Servidor StreamFlow
    APP_URL: process.env.APP_URL || 'http://localhost:5000',
    ADMIN_USER: process.env.ADMIN_USER || 'admin',
    ADMIN_PASS: process.env.ADMIN_PASS || 'admin123',
    
    // WhatsApp
    ADMIN_PHONE: process.env.ADMIN_PHONE || '573001234567', // Tu número para alertas
    
    // Intervalos (en minutos)
    HEALTH_CHECK_INTERVAL: 5,      // Verificar servidor cada 5 min
    CHANNEL_CHECK_INTERVAL: 120,    // Verificar canales cada 2 horas
    EXPIRY_CHECK_INTERVAL: 60,     // Verificar vencimientos cada 1 hora
    DAILY_REPORT_HOUR: 9,           // Reporte diario a las 9 AM
    
    // Umbrales
    MAX_CHANNEL_RETRIES: 3,         // Intentos antes de marcar canal como caído
    SERVER_RESTART_RETRIES: 3,      // Reintentos de reinicio
};

// ═══════════════════════════════════════════════════════════
// ESTADO DEL AGENTE
// ═══════════════════════════════════════════════════════════

const state = {
    serverAlive: false,
    channels: {},
    users: [],
    lastHealthCheck: null,
    lastChannelCheck: null,
    alertsSent: new Set(),
    startTime: Date.now(),
    stats: {
        totalChecks: 0,
        channelsReplaced: 0,
        serverRestarts: 0,
        alertsSent: 0,
    }
};

// ═══════════════════════════════════════════════════════════
// WHATSAPP CLIENT
// ═══════════════════════════════════════════════════════════

let waClient = null;
let waReady = false;

function initWhatsApp() {
    console.log('📱 Inicializando WhatsApp...');
    
    waClient = new Client({
        authStrategy: new LocalAuth({ clientId: 'streamflow-guardian' }),
        puppeteer: {
            headless: true,
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        }
    });

    waClient.on('qr', (qr) => {
        console.log('📱 Escanea este QR con WhatsApp:');
        console.log(qr);
    });

    waClient.on('ready', () => {
        console.log('✅ WhatsApp conectado y listo');
        waReady = true;
        sendWhatsApp(CONFIG.ADMIN_PHONE, '🛡️ *StreamFlow Guardian*\n\nAgente iniciado y monitoreando el sistema 24/7.');
    });

    waClient.on('disconnected', (reason) => {
        console.log('⚠️ WhatsApp desconectado:', reason);
        waReady = false;
        setTimeout(initWhatsApp, 5000);
    });

    waClient.on('message', async (msg) => {
        // Responder comandos del admin
        if (msg.from === `${CONFIG.ADMIN_PHONE}@c.us`) {
            const text = msg.body.toLowerCase().trim();
            
            if (text === '/status' || text === '/estado') {
                const status = getStatusReport();
                msg.reply(status);
            } else if (text === '/canales' || text === '/channels') {
                const channels = getChannelsReport();
                msg.reply(channels);
            } else if (text === '/restart' || text === '/reiniciar') {
                msg.reply('🔄 Reiniciando StreamFlow...');
                restartServer();
            } else if (text === '/check' || text === '/verificar') {
                msg.reply('🔍 Verificando canales...');
                checkAllChannels();
            } else if (text === '/help' || text === '/ayuda') {
                msg.reply(getHelpText());
            }
        }
    });

    waClient.initialize();
}

async function sendWhatsApp(phone, message) {
    if (!waReady || !waClient) {
        console.log('⚠️ WhatsApp no disponible. Mensaje:', message);
        return false;
    }
    
    try {
        const chatId = `${phone}@c.us`;
        await waClient.sendMessage(chatId, message);
        state.stats.alertsSent++;
        console.log(`📤 WhatsApp enviado a ${phone}: ${message.substring(0, 50)}...`);
        return true;
    } catch (err) {
        console.error('❌ Error enviando WhatsApp:', err.message);
        return false;
    }
}

// ═══════════════════════════════════════════════════════════
// FUNCIONES DE MONITOREO
// ═══════════════════════════════════════════════════════════

function httpGet(url, timeout = 5000) {
    return new Promise((resolve, reject) => {
        const req = http.get(url, { timeout }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve({ status: res.statusCode, data }));
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    });
}

// ── 1. Health Check del Servidor ──

async function healthCheck() {
    try {
        const resp = await httpGet(`${CONFIG.APP_URL}/health`, 5000);
        state.serverAlive = resp.status === 200;
        state.lastHealthCheck = new Date();
        state.stats.totalChecks++;
        
        if (!state.serverAlive) {
            console.log(`⚠️ Servidor respondió status ${resp.status}`);
        }
    } catch (err) {
        console.log(`❌ Servidor no responde: ${err.message}`);
        
        if (state.serverAlive) {
            // El servidor estaba vivo y ahora no responde
            state.serverAlive = false;
            await sendWhatsApp(CONFIG.ADMIN_PHONE, 
                `🚨 *ALERTA: StreamFlow caído*\n\n` +
                `El servidor no responde desde ${new Date().toLocaleTimeString('es-CO')}\n` +
                `Intentando reiniciar automáticamente...`
            );
            await restartServer();
        }
    }
}

// ── 2. Verificar Canales ──

async function checkAllChannels() {
    console.log('🔍 Verificando canales...');
    
    try {
        // Obtener lista de canales del API
        const resp = await httpGet(`${CONFIG.APP_URL}/admin/channels?limit=200`, 10000);
        if (resp.status !== 200) {
            console.log('❌ No se pudo obtener lista de canales');
            return;
        }
        
        const data = JSON.parse(resp.data);
        const channels = data.canales || [];
        let fallen = [];
        let recovered = [];
        
        for (const ch of channels) {
            const chId = ch.canal_id;
            const chName = ch.nombre;
            const fuenteEstado = ch.fuente_estado;
            
            if (fuenteEstado === 'caido' || fuenteEstado === 'sin_fuente') {
                fallen.push(chName);
                
                // Intentar reemplazar
                try {
                    const replaceResp = await httpGet(
                        `${CONFIG.APP_URL}/admin/channels/${chId}/check`, 15000
                    );
                    if (replaceResp.status === 200) {
                        const result = JSON.parse(replaceResp.data);
                        if (result.estado === 'ok') {
                            recovered.push(chName);
                            state.stats.channelsReplaced++;
                        }
                    }
                } catch (e) {
                    console.log(`  ❌ Error reemplazando ${chName}: ${e.message}`);
                }
            }
        }
        
        state.lastChannelCheck = new Date();
        
        // Enviar alerta si hay canales caídos
        if (fallen.length > 0) {
            let msg = `📺 *Reporte de Canales*\n\n`;
            msg += `❌ Caídos: ${fallen.length}\n`;
            msg += fallen.map(c => `  • ${c}`).join('\n');
            
            if (recovered.length > 0) {
                msg += `\n\n✅ Recuperados: ${recovered.length}\n`;
                msg += recovered.map(c => `  • ${c}`).join('\n');
            }
            
            const notRecovered = fallen.filter(f => !recovered.includes(f));
            if (notRecovered.length > 0) {
                msg += `\n\n⚠️ Sin recuperar: ${notRecovered.length}\n`;
                msg += notRecovered.map(c => `  • ${c}`).join('\n');
            }
            
            await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
        }
        
        console.log(`✅ Verificación completada: ${channels.length} canales, ${fallen.length} caídos, ${recovered.length} recuperados`);
        
    } catch (err) {
        console.error('❌ Error verificando canales:', err.message);
    }
}

// ── 3. Verificar Vencimientos ──

async function checkExpirations() {
    console.log('📅 Verificando vencimientos...');
    
    try {
        const resp = await httpGet(`${CONFIG.APP_URL}/admin/users?limit=500`, 10000);
        if (resp.status !== 200) return;
        
        const data = JSON.parse(resp.data);
        const users = data.usuarios || data.users || [];
        const now = new Date();
        
        const expired = [];
        const expiringSoon = []; // Vencen en 3 días o menos
        
        for (const user of users) {
            if (!user.fecha_vencimiento && !user.expiry_date) continue;
            
            const expiry = new Date(user.fecha_vencimiento || user.expiry_date);
            const daysLeft = Math.ceil((expiry - now) / (1000 * 60 * 60 * 24));
            
            if (daysLeft <= 0) {
                expired.push({ name: user.nombre || user.name, phone: user.telefono || user.phone, days: daysLeft });
            } else if (daysLeft <= 3) {
                expiringSoon.push({ name: user.nombre || user.name, phone: user.telefono || user.phone, days: daysLeft });
            }
        }
        
        // Alerta de vencidos
        if (expired.length > 0) {
            let msg = `🚨 *USUARIOS VENCIDOS*\n\n`;
            msg += `${expired.length} usuario(s) vencido(s):\n`;
            expired.forEach(u => {
                msg += `  • ${u.name} (${u.phone || 'sin teléfono'})\n`;
            });
            msg += `\n⚠️ Debes renovar o suspender estos usuarios.`;
            
            await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
        }
        
        // Alerta de próximos a vencer
        if (expiringSoon.length > 0) {
            let msg = `⏰ *Próximos a vencer*\n\n`;
            msg += `${expiringSoon.length} usuario(s) vencen pronto:\n`;
            expiringSoon.forEach(u => {
                msg += `  • ${u.name} — ${u.days} día(s) (${u.phone || 'sin teléfono'})\n`;
            });
            
            await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
            
            // Enviar mensaje a cada usuario que vence pronto
            for (const user of expiringSoon) {
                if (user.phone) {
                    await sendWhatsApp(user.phone,
                        `📺 *StreamFlow*\n\n` +
                        `Hola ${user.name}, tu suscripción vence en *${user.days} día(s)*.\n\n` +
                        `Para renovar, contacta al administrador.\n` +
                        `¡No te quedes sin señal! 📡`
                    );
                    await sleep(2000); // Evitar spam
                }
            }
        }
        
        console.log(`✅ Vencimientos: ${expired.length} vencidos, ${expiringSoon.length} próximos`);
        
    } catch (err) {
        console.error('❌ Error verificando vencimientos:', err.message);
    }
}

// ── 4. Reporte Diario ──

async function sendDailyReport() {
    console.log('📊 Generando reporte diario...');
    
    try {
        const resp = await httpGet(`${CONFIG.APP_URL}/admin/stats`, 10000);
        if (resp.status !== 200) return;
        
        const stats = JSON.parse(resp.data);
        const uptime = Math.floor((Date.now() - state.startTime) / 1000 / 60 / 60);
        
        let msg = `📊 *Reporte Diario StreamFlow*\n`;
        msg += `📅 ${new Date().toLocaleDateString('es-CO')}\n\n`;
        
        msg += `🟢 Servidor: ${state.serverAlive ? 'ONLINE' : 'OFFLINE'}\n`;
        msg += `⏱️ Uptime del agente: ${uptime}h\n`;
        msg += `📺 Canales: ${stats.total_canales || '?'} total, ${stats.canales_activos || '?'} activos\n`;
        msg += `👥 Usuarios: ${stats.total_usuarios || '?'}\n`;
        msg += `💰 Ingresos del mes: $${(stats.ingresos_mes || 0).toLocaleString('es-CO')} COP\n`;
        msg += `🔄 Canales reemplazados: ${state.stats.channelsReplaced}\n`;
        msg += `🔁 Reinicios del servidor: ${state.stats.serverRestarts}\n`;
        msg += `📤 Alertas enviadas: ${state.stats.alertsSent}\n`;
        
        await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
        
    } catch (err) {
        console.error('❌ Error generando reporte:', err.message);
    }
}

// ═══════════════════════════════════════════════════════════
// FUNCIONES DE CONTROL
// ═══════════════════════════════════════════════════════════

async function restartServer() {
    console.log('🔄 Reiniciando StreamFlow...');
    state.stats.serverRestarts++;
    
    return new Promise((resolve) => {
        exec('docker restart $(docker ps -q --filter "name=streamflow")', (err, stdout, stderr) => {
            if (err) {
                console.error('❌ Error reiniciando:', err.message);
                // Intentar con pm2
                exec('pm2 restart streamflow', (err2) => {
                    if (err2) {
                        console.error('❌ Error con pm2:', err2.message);
                        sendWhatsApp(CONFIG.ADMIN_PHONE, 
                            '🚨 *ERROR CRÍTICO*\n\nNo se pudo reiniciar StreamFlow.\nRequiere intervención manual.'
                        );
                    }
                    resolve();
                });
            } else {
                console.log('✅ Servidor reiniciado');
                setTimeout(() => {
                    sendWhatsApp(CONFIG.ADMIN_PHONE, '✅ *StreamFlow reiniciado*\n\nEl servidor fue reiniciado automáticamente.');
                }, 10000);
                resolve();
            }
        });
    });
}

// ═══════════════════════════════════════════════════════════
// REPORTES POR WHATSAPP
// ═══════════════════════════════════════════════════════════

function getStatusReport() {
    const uptime = Math.floor((Date.now() - state.startTime) / 1000 / 60 / 60);
    return `🛡️ *StreamFlow Guardian - Estado*\n\n` +
        `🟢 Servidor: ${state.serverAlive ? 'ONLINE ✅' : 'OFFLINE ❌'}\n` +
        `⏱️ Uptime agente: ${uptime}h\n` +
        `📺 Última verificación canales: ${state.lastChannelCheck?.toLocaleTimeString('es-CO') || 'Nunca'}\n` +
        `🔄 Canales reemplazados: ${state.stats.channelsReplaced}\n` +
        `🔁 Reinicios: ${state.stats.serverRestarts}\n` +
        `📤 Alertas: ${state.stats.alertsSent}\n` +
        `🔍 Checks totales: ${state.stats.totalChecks}`;
}

function getChannelsReport() {
    return `📺 *Estado de Canales*\n\n` +
        `Última verificación: ${state.lastChannelCheck?.toLocaleTimeString('es-CO') || 'Nunca'}\n` +
        `Canales reemplazados hoy: ${state.stats.channelsReplaced}\n\n` +
        `Usa /check para verificar ahora.`;
}

function getHelpText() {
    return `🛡️ *StreamFlow Guardian - Comandos*\n\n` +
        `/status - Estado del sistema\n` +
        `/canales - Estado de canales\n` +
        `/check - Verificar canales ahora\n` +
        `/reiniciar - Reiniciar servidor\n` +
        `/ayuda - Este mensaje\n\n` +
        `El agente monitorea automáticamente:\n` +
        `• Servidor cada 5 min\n` +
        `• Canales cada 2 horas\n` +
        `• Vencimientos cada 1 hora\n` +
        `• Reporte diario a las 9 AM`;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ═══════════════════════════════════════════════════════════
// CRON JOBS
// ═══════════════════════════════════════════════════════════

function initCronJobs() {
    // Health check cada 5 minutos
    cron.schedule('*/5 * * * *', () => {
        healthCheck();
    });
    
    // Verificar canales cada 2 horas
    cron.schedule('0 */2 * * *', () => {
        checkAllChannels();
    });
    
    // Verificar vencimientos cada hora
    cron.schedule('0 * * * *', () => {
        checkExpirations();
    });
    
    // Reporte diario a las 9 AM
    cron.schedule('0 9 * * *', () => {
        sendDailyReport();
    });
    
    console.log('⏰ Cron jobs configurados:');
    console.log('   • Health check: cada 5 min');
    console.log('   • Canales: cada 2 horas');
    console.log('   • Vencimientos: cada 1 hora');
    console.log('   • Reporte diario: 9:00 AM');
}

// ═══════════════════════════════════════════════════════════
// API HTTP (para integración con StreamFlow)
// ═══════════════════════════════════════════════════════════

function initAPI() {
    const server = http.createServer((req, res) => {
        res.setHeader('Content-Type', 'application/json');
        
        if (req.url === '/health') {
            res.end(JSON.stringify({
                status: 'ok',
                agent: 'streamflow-guardian',
                uptime: Math.floor((Date.now() - state.startTime) / 1000),
                whatsapp: waReady,
                serverAlive: state.serverAlive,
                stats: state.stats,
            }));
        } else if (req.url === '/status') {
            res.end(JSON.stringify(state));
        } else {
            res.end(JSON.stringify({ error: 'not found' }));
        }
    });
    
    const PORT = process.env.GUARDIAN_PORT || 5001;
    server.listen(PORT, () => {
        console.log(`🌐 API del agente en puerto ${PORT}`);
    });
}

// ═══════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════

async function main() {
    console.log(`
╔══════════════════════════════════════════════════════════════╗
║          🛡️ StreamFlow Guardian Agent v1.0                  ║
║          Monitoreo 24/7 - WhatsApp Alerts                   ║
╚══════════════════════════════════════════════════════════════╝
    `);
    
    console.log('📋 Configuración:');
    console.log(`   APP_URL: ${CONFIG.APP_URL}`);
    console.log(`   ADMIN_PHONE: ${CONFIG.ADMIN_PHONE}`);
    console.log(`   HEALTH_CHECK: cada ${CONFIG.HEALTH_CHECK_INTERVAL} min`);
    console.log(`   CHANNEL_CHECK: cada ${CONFIG.CHANNEL_CHECK_INTERVAL} min`);
    console.log(`   EXPIRY_CHECK: cada ${CONFIG.EXPIRY_CHECK_INTERVAL} min`);
    console.log('');
    
    // Inicializar componentes
    initWhatsApp();
    initCronJobs();
    initAPI();
    
    // Primer health check inmediato
    await healthCheck();
    
    console.log('\n✅ Agente iniciado. Monitoreando StreamFlow 24/7...\n');
}

main().catch(err => {
    console.error('❌ Error fatal:', err);
    process.exit(1);
});
