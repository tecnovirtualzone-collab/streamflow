#!/usr/bin/env node
/**
 * StreamFlow Guardian Agent v2.0 - ADMIN COMPLETO 🛡️
 * =====================================================
 * Agente autónomo que administra StreamFlow 24/7
 * 
 * Responsabilidades:
 * 1. Monitorear servidor (health check cada 5 min)
 * 2. Verificar canales cada 2h y reemplazar caídos
 * 3. Gestionar fechas de vencimiento (alertas WhatsApp)
 * 4. Monitorear LOGS y detectar errores
 * 5. Corregir problemas automáticamente
 * 6. Verificar deploys (que todo instale correctamente)
 * 7. Reiniciar servicios si fallan
 * 8. Reporte diario por WhatsApp
 * 9. Responder comandos por WhatsApp
 */

const { Client, LocalAuth } = require('whatsapp-web.js');
const { exec, execSync } = require('child_process');
const cron = require('node-cron');
const http = require('http');
const fs = require('fs');
const path = require('path');
const qrcode = require('qrcode-terminal');

// ═══════════════════════════════════════════════════════════
// CONFIGURACIÓN
// ═══════════════════════════════════════════════════════════

const CONFIG = {
    APP_URL: process.env.APP_URL || 'http://localhost:5000',
    APP_DIR: process.env.APP_DIR || '/root/streamflow',
    ADMIN_USER: process.env.ADMIN_USER || 'admin',
    ADMIN_PASS: process.env.ADMIN_PASS || 'admin123',
    ADMIN_PHONE: process.env.ADMIN_PHONE || '573222468509',
    
    // Intervalos (minutos)
    HEALTH_CHECK_INTERVAL: 5,
    CHANNEL_CHECK_INTERVAL: 120,
    EXPIRY_CHECK_INTERVAL: 60,
    LOG_CHECK_INTERVAL: 15,
    DEPLOY_CHECK_INTERVAL: 10,
    DAILY_REPORT_HOUR: 9,
    
    // Umbrales
    MAX_RESTART_RETRIES: 3,
    LOG_ERROR_THRESHOLD: 5,  // Errores en 15 min antes de alertar
    
    // Servicios a monitorear
    SERVICES: ['streamflow', 'postgres', 'redis', 'nginx'],
    
    // Logs a monitorear
    LOG_FILES: [
        '/var/log/syslog',
        '/var/log/nginx/error.log',
        `${process.env.APP_DIR || '/root/streamflow'}/logs/app.log`,
        '/var/log/docker.log',
    ],
};

// ═══════════════════════════════════════════════════════════
// ESTADO
// ═══════════════════════════════════════════════════════════

const state = {
    serverAlive: false,
    services: {},
    channels: {},
    users: [],
    lastChecks: {
        health: null,
        channels: null,
        expiry: null,
        logs: null,
        deploy: null,
    },
    alertsSent: new Set(),
    startTime: Date.now(),
    deployStatus: 'idle', // idle, deploying, success, failed
    stats: {
        totalChecks: 0,
        channelsReplaced: 0,
        serverRestarts: 0,
        servicesRestarted: 0,
        errorsDetected: 0,
        errorsFixed: 0,
        alertsSent: 0,
    },
    logErrors: [],
    lastLogPosition: {},
};

// ═══════════════════════════════════════════════════════════
// WHATSAPP
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
        qrcode.generate(qr, { small: true });
    });

    waClient.on('ready', () => {
        console.log('✅ WhatsApp conectado');
        waReady = true;
        sendWhatsApp(CONFIG.ADMIN_PHONE, 
            '🛡️ *StreamFlow Guardian v2.0*\n\n' +
            'Agente ADMIN iniciado.\n' +
            'Monitoreo 24/7 activo:\n' +
            '• Servidor y servicios\n' +
            '• Canales y streams\n' +
            '• Logs y errores\n' +
            '• Deploys\n' +
            '• Vencimientos\n\n' +
            'Escribe /ayuda para comandos.'
        );
    });

    waClient.on('disconnected', (reason) => {
        console.log('⚠️ WhatsApp desconectado:', reason);
        waReady = false;
        setTimeout(initWhatsApp, 5000);
    });

    waClient.on('message', handleWhatsAppCommand);
    
    // Mensajes de clientes → reenviar a Luna (OWL)
    waClient.on('message', async (msg) => {
        // Solo mensajes que NO son del admin
        if (msg.from && msg.from !== `${CONFIG.ADMIN_PHONE}@c.us` && !msg.from.includes('@g.us')) {
            // Guardar en cola para que Luna lo procese
            try {
                const fs = require('fs');
                const queueFile = '/tmp/luna-whatsapp-queue.json';
                let queue = [];
                if (fs.existsSync(queueFile)) {
                    queue = JSON.parse(fs.readFileSync(queueFile, 'utf-8'));
                }
                queue.push({
                    from: msg.from,
                    body: msg.body,
                    timestamp: new Date().toISOString(),
                    phone: msg.from.replace('@c.us', ''),
                });
                fs.writeFileSync(queueFile, JSON.stringify(queue.slice(-50)));
            } catch (e) {}
        }
    });
    
    waClient.initialize();
}

async function handleWhatsAppCommand(msg) {
    if (!msg.from || !msg.from.endsWith('@c.us')) return;
    if (msg.from !== `${CONFIG.ADMIN_PHONE}@c.us`) return;
    
    const text = msg.body.toLowerCase().trim();
    
    const commands = {
        '/status': () => msg.reply(getStatusReport()),
        '/estado': () => msg.reply(getStatusReport()),
        '/canales': () => msg.reply(getChannelsReport()),
        '/channels': () => msg.reply(getChannelsReport()),
        '/logs': () => msg.reply(getLogsReport()),
        '/errores': () => msg.reply(getLogsReport()),
        '/servicios': () => msg.reply(getServicesReport()),
        '/services': () => msg.reply(getServicesReport()),
        '/deploy': () => msg.reply(getDeployReport()),
        '/check': () => { checkAllChannels(); msg.reply('🔍 Verificando canales...'); },
        '/verificar': () => { checkAllChannels(); msg.reply('🔍 Verificando canales...'); },
        '/restart': () => { restartServer(); msg.reply('🔄 Reiniciando StreamFlow...'); },
        '/reiniciar': () => { restartServer(); msg.reply('🔄 Reiniciando StreamFlow...'); },
        '/fix': () => { fixErrors(); msg.reply('🔧 Intentando corregir errores...'); },
        '/corregir': () => { fixErrors(); msg.reply('🔧 Intentando corregir errores...'); },
        '/update': () => { updateFromGit(); msg.reply('📦 Actualizando desde GitHub...'); },
        '/actualizar': () => { updateFromGit(); msg.reply('📦 Actualizando desde GitHub...'); },
        '/backup': () => { createBackup(); msg.reply('💾 Creando backup...'); },
        '/ayuda': () => msg.reply(getHelpText()),
        '/help': () => msg.reply(getHelpText()),
    };
    
    const handler = commands[text];
    if (handler) handler();
}

async function sendWhatsApp(phone, message) {
    if (!waReady || !waClient || !phone) {
        console.log(`📤 [WhatsApp no disponible] ${phone}: ${message.substring(0, 80)}`);
        return false;
    }
    try {
        const chatId = phone.includes('@c.us') ? phone : `${phone}@c.us`;
        await waClient.sendMessage(chatId, message);
        state.stats.alertsSent++;
        return true;
    } catch (err) {
        console.error('❌ Error WhatsApp:', err.message);
        return false;
    }
}

// ═══════════════════════════════════════════════════════════
// MONITOREO DEL SERVIDOR
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

async function healthCheck() {
    try {
        const resp = await httpGet(`${CONFIG.APP_URL}/health`, 5000);
        const wasAlive = state.serverAlive;
        state.serverAlive = resp.status === 200;
        state.lastChecks.health = new Date();
        state.stats.totalChecks++;
        
        if (wasAlive && !state.serverAlive) {
            // Servidor se cayó
            await sendWhatsApp(CONFIG.ADMIN_PHONE,
                `🚨 *ALERTA: StreamFlow CAÍDO*\n\n` +
                `Hora: ${new Date().toLocaleTimeString('es-CO')}\n` +
                `Intentando reinicio automático...`
            );
            await restartServer();
        }
    } catch (err) {
        if (state.serverAlive) {
            state.serverAlive = false;
            await sendWhatsApp(CONFIG.ADMIN_PHONE,
                `🚨 *ALERTA: StreamFlow no responde*\n\n` +
                `Error: ${err.message}\n` +
                `Intentando reinicio...`
            );
            await restartServer();
        }
    }
}

// ═══════════════════════════════════════════════════════════
// MONITOREO DE SERVICIOS (Docker, PostgreSQL, etc.)
// ═══════════════════════════════════════════════════════════

async function checkServices() {
    console.log('🔧 Verificando servicios...');
    
    const services = {
        'StreamFlow': 'docker ps --filter "name=streamflow" --format "{{.Status}}"',
        'PostgreSQL': 'docker ps --filter "name=streamflow-db" --format "{{.Status}}"',
        'Traefik': 'docker ps --filter "name=traefik" --format "{{.Status}}"',
        'Redis': 'docker ps --filter "name=redis" --format "{{.Status}}"',
        'Nginx': 'docker ps --filter "name=nginx" --format "{{.Status}}"',
    };
    
    let problems = [];
    
    for (const [name, cmd] of Object.entries(services)) {
        try {
            const result = execSync(cmd, { timeout: 5000 }).toString().trim();
            const isRunning = result.toLowerCase().includes('up');
            state.services[name] = isRunning ? 'running' : 'stopped';
            
            if (!isRunning) {
                problems.push({ name, status: result || 'stopped' });
            }
        } catch (err) {
            state.services[name] = 'error';
            problems.push({ name, status: 'error' });
        }
    }
    
    // Reiniciar servicios caídos
    for (const svc of problems) {
        console.log(`⚠️ Servicio caído: ${svc.name}`);
        await restartService(svc.name);
    }
    
    if (problems.length > 0) {
        await sendWhatsApp(CONFIG.ADMIN_PHONE,
            `🔧 *Servicios reiniciados*\n\n` +
            problems.map(s => `• ${s.name}: ${s.status}`).join('\n')
        );
    }
}

async function restartService(name) {
    const serviceMap = {
        'StreamFlow': 'docker restart $(docker ps -aq --filter "name=streamflow")',
        'PostgreSQL': 'docker restart $(docker ps -aq --filter "name=streamflow-db")',
        'Traefik': 'docker restart $(docker ps -aq --filter "name=traefik")',
        'Redis': 'docker restart $(docker ps -aq --filter "name=redis")',
        'Nginx': 'docker restart $(docker ps -aq --filter "name=nginx")',
    };
    
    const cmd = serviceMap[name];
    if (!cmd) return;
    
    try {
        execSync(cmd, { timeout: 30000 });
        state.stats.servicesRestarted++;
        console.log(`✅ ${name} reiniciado`);
    } catch (err) {
        console.error(`❌ Error reiniciando ${name}:`, err.message);
    }
}

// ═══════════════════════════════════════════════════════════
// MONITOREO DE LOGS Y DETECCIÓN DE ERRORES
// ═══════════════════════════════════════════════════════════

const ERROR_PATTERNS = [
    /error/i,
    /fatal/i,
    /crash/i,
    /exception/i,
    /traceback/i,
    /failed/i,
    /refused/i,
    /timeout/i,
    /out of memory/i,
    /permission denied/i,
    /segmentation fault/i,
    /cannot allocate/i,
    /connection reset/i,
    /broken pipe/i,
];

const CRITICAL_PATTERNS = [
    /fatal/i,
    /crash/i,
    /out of memory/i,
    /segmentation fault/i,
    /disk full/i,
    /permission denied/i,
];

async function checkLogs() {
    console.log('📋 Verificando logs...');
    state.lastChecks.logs = new Date();
    
    let newErrors = [];
    
    for (const logFile of CONFIG.LOG_FILES) {
        if (!fs.existsSync(logFile)) continue;
        
        try {
            const stats = fs.statSync(logFile);
            const lastSize = state.lastLogPosition[logFile] || 0;
            
            // Solo leer líneas nuevas
            if (stats.size <= lastSize) continue;
            
            const fd = fs.openSync(logFile, 'r');
            const buffer = Buffer.alloc(stats.size - lastSize);
            fs.readSync(fd, buffer, 0, buffer.length, lastSize);
            fs.closeSync(fd);
            
            const newContent = buffer.toString('utf-8');
            const lines = newContent.split('\n').filter(l => l.trim());
            
            state.lastLogPosition[logFile] = stats.size;
            
            for (const line of lines) {
                const isError = ERROR_PATTERNS.some(p => p.test(line));
                const isCritical = CRITICAL_PATTERNS.some(p => p.test(line));
                
                if (isError) {
                    newErrors.push({
                        file: path.basename(logFile),
                        line: line.substring(0, 200),
                        critical: isCritical,
                        time: new Date(),
                    });
                }
            }
        } catch (err) {
            // Ignorar errores de lectura
        }
    }
    
    // También verificar logs de Docker
    try {
        const dockerLogs = execSync(
            'docker logs --tail 50 $(docker ps -aq --filter "name=streamflow") 2>&1',
            { timeout: 10000 }
        ).toString();
        
        const lines = dockerLogs.split('\n').filter(l => l.trim());
        for (const line of lines.slice(-20)) {
            const isError = ERROR_PATTERNS.some(p => p.test(line));
            if (isError) {
                newErrors.push({
                    file: 'docker-streamflow',
                    line: line.substring(0, 200),
                    critical: CRITICAL_PATTERNS.some(p => p.test(line)),
                    time: new Date(),
                });
            }
        }
    } catch (e) {}
    
    state.stats.errorsDetected += newErrors.length;
    state.logErrors.push(...newErrors);
    
    // Mantener solo últimos 100 errores
    if (state.logErrors.length > 100) {
        state.logErrors = state.logErrors.slice(-100);
    }
    
    // Alerta si hay errores críticos
    const criticalErrors = newErrors.filter(e => e.critical);
    if (criticalErrors.length > 0) {
        let msg = `🚨 *ERRORES CRÍTICOS detectados*\n\n`;
        criticalErrors.slice(0, 5).forEach(e => {
            msg += `• [${e.file}] ${e.line.substring(0, 100)}\n`;
        });
        if (criticalErrors.length > 5) {
            msg += `\n...y ${criticalErrors.length - 5} más`;
        }
        await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
    }
    
    // Intentar corregir errores automáticamente
    if (newErrors.length > 0) {
        await autoFixErrors(newErrors);
    }
    
    if (newErrors.length > 0) {
        console.log(`📋 Logs: ${newErrors.length} errores detectados (${criticalErrors.length} críticos)`);
    }
}

async function autoFixErrors(errors) {
    console.log('🔧 Intentando correcciones automáticas...');
    
    for (const err of errors) {
        const line = err.line.toLowerCase();
        
        // Out of memory → reiniciar servicio
        if (line.includes('out of memory') || line.includes('cannot allocate')) {
            console.log('  💾 Memoria insuficiente, reiniciando...');
            await restartServer();
            state.stats.errorsFixed++;
        }
        
        // Connection refused → reiniciar servicio
        else if (line.includes('connection refused') || line.includes('refused')) {
            console.log('  🔌 Conexión rechazada, reiniciando servicios...');
            await checkServices();
            state.stats.errorsFixed++;
        }
        
        // Permission denied → intentar chmod
        else if (line.includes('permission denied')) {
            console.log('  🔒 Permiso denegado, intentando chmod...');
            try {
                execSync(`chmod -R 755 ${CONFIG.APP_DIR}`, { timeout: 10000 });
                state.stats.errorsFixed++;
            } catch (e) {}
        }
        
        // Disk full → limpiar logs y temporales
        else if (line.includes('disk full') || line.includes('no space')) {
            console.log('  💾 Disco lleno, limpiando...');
            try {
                execSync('docker system prune -f', { timeout: 60000 });
                execSync('rm -rf /tmp/*.log /tmp/*.tmp', { timeout: 10000 });
                state.stats.errorsFixed++;
            } catch (e) {}
        }
        
        // Timeout → reiniciar
        else if (line.includes('timeout') && err.critical) {
            console.log('  ⏱️ Timeout crítico, reiniciando...');
            await restartServer();
            state.stats.errorsFixed++;
        }
    }
}

// ═══════════════════════════════════════════════════════════
// VERIFICACIÓN DE DEPLOYS
// ═══════════════════════════════════════════════════════════

let lastDeployCommit = null;

async function checkDeploy() {
    console.log('📦 Verificando estado del deploy...');
    state.lastChecks.deploy = new Date();
    
    try {
        // Verificar si hay un deploy en curso (EasyPanel)
        const deployCheck = execSync(
            'docker ps --filter "label=easypanel.deploy" --format "{{.Status}}" 2>/dev/null || echo "none"',
            { timeout: 5000 }
        ).toString().trim();
        
        if (deployCheck !== 'none' && deployCheck !== '') {
            state.deployStatus = 'deploying';
            console.log('  📦 Deploy en curso...');
            
            // Monitorear hasta que termine
            await monitorDeploy();
            return;
        }
        
        // Verificar si el código cambió (nuevo commit en GitHub)
        const currentCommit = execSync(
            `cd ${CONFIG.APP_DIR} && git rev-parse HEAD 2>/dev/null || echo "unknown"`,
            { timeout: 5000 }
        ).toString().trim();
        
        if (lastDeployCommit && lastDeployCommit !== currentCommit) {
            console.log('  🔄 Nuevo commit detectado, verificando instalación...');
            await verifyInstallation();
        }
        
        lastDeployCommit = currentCommit;
        
    } catch (err) {
        console.log('  ⚠️ Error verificando deploy:', err.message);
    }
}

async function monitorDeploy() {
    console.log('  👀 Monitoreando deploy...');
    
    let attempts = 0;
    const maxAttempts = 60; // 10 minutos
    
    while (attempts < maxAttempts) {
        await sleep(10000); // 10 segundos
        attempts++;
        
        try {
            // Verificar si el deploy terminó
            const deployCheck = execSync(
                'docker ps --filter "label=easypanel.deploy" --format "{{.Status}}" 2>/dev/null || echo "done"',
                { timeout: 5000 }
            ).toString().trim();
            
            if (deployCheck === 'done' || deployCheck === '') {
                state.deployStatus = 'success';
                console.log('  ✅ Deploy completado');
                
                // Verificar que todo esté bien
                await verifyInstallation();
                return;
            }
            
            // Verificar si hay errores
            const errorCheck = execSync(
                'docker ps --filter "label=easypanel.deploy" --filter "status=exited" --format "{{.Status}}" 2>/dev/null || echo ""',
                { timeout: 5000 }
            ).toString().trim();
            
            if (errorCheck.includes('exited')) {
                state.deployStatus = 'failed';
                console.log('  ❌ Deploy fallido');
                
                await sendWhatsApp(CONFIG.ADMIN_PHONE,
                    '🚨 *DEPLOY FALLIDO*\n\n' +
                    'El deploy no se completó correctamente.\n' +
                    'Intentando rollback...'
                );
                
                await rollbackDeploy();
                return;
            }
            
        } catch (err) {
            // Ignorar errores temporales
        }
    }
    
    // Timeout del monitoreo
    state.deployStatus = 'timeout';
    await sendWhatsApp(CONFIG.ADMIN_PHONE,
        '⏱️ *DEPLOY TIMEOUT*\n\n' +
        'El deploy tardó más de 10 minutos.\n' +
        'Puede requerir intervención manual.'
    );
}

async function verifyInstallation() {
    console.log('  🔍 Verificando instalación...');
    
    const checks = [
        { name: 'Python dependencies', cmd: `cd ${CONFIG.APP_DIR} && python3 -c "import flask; import jwt; import requests; print('ok')"` },
        { name: 'Database tables', cmd: `cd ${CONFIG.APP_DIR} && python3 -c "from app import db; db.create_all(); print('ok')"` },
        { name: 'FFmpeg', cmd: 'ffmpeg -version 2>&1 | head -1' },
        { name: 'Node.js', cmd: 'node --version' },
        { name: 'NPM packages', cmd: `cd ${CONFIG.APP_DIR}/guardian-agent && npm list --depth=0 2>&1 | head -5` },
    ];
    
    let allOk = true;
    let results = [];
    
    for (const check of checks) {
        try {
            const result = execSync(check.cmd, { timeout: 15000 }).toString().trim();
            results.push(`✅ ${check.name}: ${result.substring(0, 50)}`);
        } catch (err) {
            results.push(`❌ ${check.name}: ${err.message.substring(0, 50)}`);
            allOk = false;
        }
    }
    
    // Verificar que el servidor responde
    try {
        await sleep(5000); // Esperar a que inicie
        const resp = await httpGet(`${CONFIG.APP_URL}/health`, 10000);
        if (resp.status === 200) {
            results.push('✅ Servidor HTTP: OK');
        } else {
            results.push(`❌ Servidor HTTP: status ${resp.status}`);
            allOk = false;
        }
    } catch (err) {
        results.push(`❌ Servidor HTTP: ${err.message}`);
        allOk = false;
    }
    
    state.deployStatus = allOk ? 'success' : 'failed';
    
    const msg = allOk 
        ? '✅ *Deploy verificado*\n\nTodo instalado correctamente:\n' + results.join('\n')
        : '⚠️ *Deploy con problemas*\n\n' + results.join('\n');
    
    await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
    
    if (!allOk) {
        await fixInstallationIssues(results);
    }
}

async function fixInstallationIssues(results) {
    console.log('🔧 Intentando corregir problemas de instalación...');
    
    for (const result of results) {
        if (result.includes('Python dependencies')) {
            console.log('  📦 Instalando dependencias Python...');
            try {
                execSync(`cd ${CONFIG.APP_DIR} && pip3 install -r requirements.txt 2>&1`, { timeout: 120000 });
                state.stats.errorsFixed++;
            } catch (e) {}
        }
        
        if (result.includes('NPM packages')) {
            console.log('  📦 Instalando dependencias NPM...');
            try {
                execSync(`cd ${CONFIG.APP_DIR}/guardian-agent && npm install 2>&1`, { timeout: 120000 });
                state.stats.errorsFixed++;
            } catch (e) {}
        }
        
        if (result.includes('Database')) {
            console.log('  🔄 Creando tablas de base de datos...');
            try {
                execSync(`cd ${CONFIG.APP_DIR} && python3 -c "from app import db; db.create_all()"`, { timeout: 30000 });
                state.stats.errorsFixed++;
            } catch (e) {}
        }
    }
}

async function rollbackDeploy() {
    console.log('⏪ Intentando rollback...');
    
    try {
        // Volver al commit anterior
        execSync(`cd ${CONFIG.APP_DIR} && git reset --hard HEAD~1`, { timeout: 30000 });
        
        // Reinstalar dependencias
        execSync(`cd ${CONFIG.APP_DIR} && pip3 install -r requirements.txt 2>&1`, { timeout: 120000 });
        
        // Reiniciar servidor
        await restartServer();
        
        await sendWhatsApp(CONFIG.ADMIN_PHONE,
            '⏪ *Rollback completado*\n\n' +
            'Se volvió al commit anterior.\n' +
            'El servidor fue reiniciado.'
        );
    } catch (err) {
        await sendWhatsApp(CONFIG.ADMIN_PHONE,
            '🚨 *ERROR EN ROLLBACK*\n\n' +
            'No se pudo hacer rollback automático.\n' +
            'Requiere intervención manual.'
        );
    }
}

// ═══════════════════════════════════════════════════════════
// VERIFICACIÓN DE CANALES
// ═══════════════════════════════════════════════════════════

async function checkAllChannels() {
    console.log('📺 Verificando canales...');
    state.lastChecks.channels = new Date();
    
    try {
        const resp = await httpGet(`${CONFIG.APP_URL}/admin/channels?limit=200`, 10000);
        if (resp.status !== 200) return;
        
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
                
                try {
                    await httpGet(`${CONFIG.APP_URL}/admin/channels/${chId}/check`, 15000);
                    recovered.push(chName);
                    state.stats.channelsReplaced++;
                } catch (e) {}
            }
        }
        
        if (fallen.length > 0) {
            const notRecovered = fallen.filter(f => !recovered.includes(f));
            let msg = `📺 *Canales*\n\n`;
            msg += `❌ Caídos: ${fallen.length}\n`;
            if (recovered.length > 0) msg += `✅ Recuperados: ${recovered.length}\n`;
            if (notRecovered.length > 0) {
                msg += `⚠️ Sin recuperar:\n`;
                notRecovered.forEach(c => msg += `  • ${c}\n`);
            }
            await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
        }
        
    } catch (err) {
        console.error('❌ Error verificando canales:', err.message);
    }
}

// ═══════════════════════════════════════════════════════════
// VERIFICACIÓN DE VENCIMIENTOS
// ═══════════════════════════════════════════════════════════

async function checkExpirations() {
    console.log('📅 Verificando vencimientos...');
    state.lastChecks.expiry = new Date();
    
    try {
        const resp = await httpGet(`${CONFIG.APP_URL}/admin/users?limit=500`, 10000);
        if (resp.status !== 200) return;
        
        const data = JSON.parse(resp.data);
        const users = data.usuarios || data.users || [];
        const now = new Date();
        
        const expired = [];
        const expiringSoon = [];
        
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
        
        if (expired.length > 0) {
            let msg = `🚨 *VENCIDOS*\n\n${expired.length} usuario(s):\n`;
            expired.forEach(u => msg += `  • ${u.name} (${u.phone || 'sin teléfono'})\n`);
            await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
        }
        
        if (expiringSoon.length > 0) {
            let msg = `⏰ *Próximos a vencer*\n\n`;
            expiringSoon.forEach(u => {
                msg += `  • ${u.name} — ${u.days} día(s) (${u.phone || 'sin teléfono'})\n`;
            });
            await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
            
            // Notificar a cada usuario
            for (const user of expiringSoon) {
                if (user.phone) {
                    await sendWhatsApp(user.phone,
                        `📺 *StreamFlow*\n\n` +
                        `Hola ${user.name}, tu suscripción vence en *${user.days} día(s)*.\n\n` +
                        `Para renovar, contacta al administrador.\n` +
                        `¡No te quedes sin señal! 📡`
                    );
                    await sleep(2000);
                }
            }
        }
        
    } catch (err) {
        console.error('❌ Error verificando vencimientos:', err.message);
    }
}

// ═══════════════════════════════════════════════════════════
// REPORTES
// ═══════════════════════════════════════════════════════════

async function sendDailyReport() {
    console.log('📊 Generando reporte diario...');
    
    try {
        const resp = await httpGet(`${CONFIG.APP_URL}/admin/stats`, 10000);
        const stats = resp.status === 200 ? JSON.parse(resp.data) : {};
        const uptime = Math.floor((Date.now() - state.startTime) / 1000 / 60 / 60);
        
        let msg = `📊 *Reporte Diario*\n📅 ${new Date().toLocaleDateString('es-CO')}\n\n`;
        msg += `🟢 Servidor: ${state.serverAlive ? 'ONLINE' : 'OFFLINE'}\n`;
        msg += `⏱️ Uptime agente: ${uptime}h\n`;
        msg += `👥 Usuarios: ${stats.total_usuarios || '?'}\n`;
        msg += `💰 Ingresos: $${(stats.ingresos_mes || 0).toLocaleString('es-CO')} COP\n`;
        msg += `🔄 Canales reemplazados: ${state.stats.channelsReplaced}\n`;
        msg += `🔁 Reinicios: ${state.stats.serverRestarts}\n`;
        msg += `🔧 Servicios reiniciados: ${state.stats.servicesRestarted}\n`;
        msg += `❌ Errores detectados: ${state.stats.errorsDetected}\n`;
        msg += `✅ Errores corregidos: ${state.stats.errorsFixed}\n`;
        msg += `📤 Alertas: ${state.stats.alertsSent}\n`;
        
        await sendWhatsApp(CONFIG.ADMIN_PHONE, msg);
    } catch (err) {
        console.error('❌ Error reporte:', err.message);
    }
}

function getStatusReport() {
    const uptime = Math.floor((Date.now() - state.startTime) / 1000 / 60 / 60);
    let msg = `🛡️ *Guardian v2.0 - Estado*\n\n`;
    msg += `🟢 Servidor: ${state.serverAlive ? 'ONLINE ✅' : 'OFFLINE ❌'}\n`;
    msg += `📦 Deploy: ${state.deployStatus}\n`;
    msg += `⏱️ Uptime: ${uptime}h\n`;
    msg += `🔄 Canales reemplazados: ${state.stats.channelsReplaced}\n`;
    msg += `🔁 Reinicios: ${state.stats.serverRestarts}\n`;
    msg += `🔧 Servicios reiniciados: ${state.stats.servicesRestarted}\n`;
    msg += `❌ Errores: ${state.stats.errorsDetectados}\n`;
    msg += `✅ Corregidos: ${state.stats.errorsFixed}\n`;
    msg += `📤 Alertas: ${state.stats.alertsSent}\n`;
    return msg;
}

function getChannelsReport() {
    return `📺 *Canales*\n\n` +
        `Última verificación: ${state.lastChecks.channels?.toLocaleTimeString('es-CO') || 'Nunca'}\n` +
        `Reemplazados: ${state.stats.channelsReplaced}\n\nUsa /check para verificar ahora.`;
}

function getLogsReport() {
    const recentErrors = state.logErrors.slice(-10);
    let msg = `📋 *Últimos errores*\n\n`;
    if (recentErrors.length === 0) {
        msg += '✅ Sin errores recientes';
    } else {
        recentErrors.forEach(e => {
            msg += `• [${e.file}] ${e.line.substring(0, 80)}\n`;
        });
    }
    return msg;
}

function getServicesReport() {
    let msg = `🔧 *Servicios*\n\n`;
    for (const [name, status] of Object.entries(state.services)) {
        const icon = status === 'running' ? '✅' : '❌';
        msg += `${icon} ${name}: ${status}\n`;
    }
    return msg;
}

function getDeployReport() {
    return `📦 *Deploy*\n\n` +
        `Estado: ${state.deployStatus}\n` +
        `Última verificación: ${state.lastChecks.deploy?.toLocaleTimeString('es-CO') || 'Nunca'}`;
}

function getHelpText() {
    return `🛡️ *Guardian v2.0 - Comandos*\n\n` +
        `/status - Estado general\n` +
        `/canales - Estado de canales\n` +
        `/logs - Últimos errores\n` +
        `/servicios - Estado de servicios\n` +
        `/deploy - Estado del deploy\n` +
        `/check - Verificar canales\n` +
        `/restart - Reiniciar servidor\n` +
        `/fix - Corregir errores\n` +
        `/update - Actualizar de GitHub\n` +
        `/backup - Crear backup\n` +
        `/ayuda - Este mensaje`;
}

// ═══════════════════════════════════════════════════════════
// FUNCIONES DE CONTROL
// ═══════════════════════════════════════════════════════════

async function restartServer() {
    console.log('🔄 Reiniciando StreamFlow...');
    state.stats.serverRestarts++;
    
    return new Promise((resolve) => {
        exec('docker restart $(docker ps -q --filter "name=streamflow")', (err) => {
            if (err) {
                exec('pm2 restart streamflow', (err2) => {
                    if (err2) {
                        sendWhatsApp(CONFIG.ADMIN_PHONE, '🚨 No se pudo reiniciar. Requiere intervención.');
                    }
                });
            } else {
                setTimeout(() => {
                    sendWhatsApp(CONFIG.ADMIN_PHONE, '✅ StreamFlow reiniciado automáticamente.');
                }, 10000);
            }
            resolve();
        });
    });
}

async function updateFromGit() {
    console.log('📦 Actualizando desde GitHub...');
    
    try {
        execSync(`cd ${CONFIG.APP_DIR} && git pull origin main`, { timeout: 60000 });
        execSync(`cd ${CONFIG.APP_DIR} && pip3 install -r requirements.txt 2>&1`, { timeout: 120000 });
        
        await restartServer();
        
        await sendWhatsApp(CONFIG.ADMIN_PHONE,
            '✅ *Actualización completada*\n\n' +
            'Código actualizado desde GitHub.\n' +
            'Dependencias instaladas.\n' +
            'Servidor reiniciado.'
        );
    } catch (err) {
        await sendWhatsApp(CONFIG.ADMIN_PHONE,
            '❌ *Error actualizando*\n\n' + err.message
        );
    }
}

async function createBackup() {
    console.log('💾 Creando backup...');
    
    const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const backupDir = `/root/backups/streamflow-${timestamp}`;
    
    try {
        execSync(`mkdir -p ${backupDir}`, { timeout: 5000 });
        
        // Backup de la base de datos
        execSync(`docker exec $(docker ps -q --filter "name=streamflow-db") pg_dump -U postgres streamflow > ${backupDir}/database.sql 2>&1`, { timeout: 60000 });
        
        // Backup de la configuración
        execSync(`cp -r ${CONFIG.APP_DIR}/.env ${CONFIG.APP_DIR}/config ${backupDir}/ 2>/dev/null`, { timeout: 10000 });
        
        await sendWhatsApp(CONFIG.ADMIN_PHONE,
            `💾 *Backup creado*\n\n` +
            `Archivo: ${backupDir}\n` +
            `Fecha: ${new Date().toLocaleString('es-CO')}`
        );
    } catch (err) {
        await sendWhatsApp(CONFIG.ADMIN_PHONE, `❌ Error en backup: ${err.message}`);
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ═══════════════════════════════════════════════════════════
// CRON JOBS
// ═══════════════════════════════════════════════════════════

function initCronJobs() {
    cron.schedule('*/5 * * * *', () => healthCheck());
    cron.schedule('*/10 * * * *', () => checkServices());
    cron.schedule('*/15 * * * *', () => checkLogs());
    cron.schedule('*/10 * * * *', () => checkDeploy());
    cron.schedule('0 */2 * * *', () => checkAllChannels());
    cron.schedule('0 * * * *', () => checkExpirations());
    cron.schedule('0 9 * * *', () => sendDailyReport());
    
    console.log('⏰ Cron jobs:');
    console.log('   • Health: 5 min | Servicios: 10 min | Logs: 15 min');
    console.log('   • Deploy: 10 min | Canales: 2h | Vencimientos: 1h');
    console.log('   • Reporte: 9 AM');
}

// ═══════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════

async function main() {
    console.log(`
╔══════════════════════════════════════════════════════════════╗
║          🛡️ StreamFlow Guardian v2.0 - ADMIN               ║
║          Monitoreo 24/7 | Auto-fix | WhatsApp               ║
╚══════════════════════════════════════════════════════════════╝
    `);
    
    console.log(`📋 APP_URL: ${CONFIG.APP_URL}`);
    console.log(`📋 APP_DIR: ${CONFIG.APP_DIR}`);
    console.log(`📋 ADMIN_PHONE: ${CONFIG.ADMIN_PHONE || 'NO CONFIGURADO'}`);
    console.log('');
    
    initWhatsApp();
    initCronJobs();
    
    // Primer check inmediato
    await healthCheck();
    await checkServices();
    
    console.log('\n✅ Guardian v2.0 activo. Administrando StreamFlow 24/7...\n');
}

main().catch(err => {
    console.error('❌ Error fatal:', err);
    process.exit(1);
});
