#!/bin/bash
# ═══════════════════════════════════════════════════════════
# StreamFlow Guardian v2.0 - Script de Instalación
# Ejecutar en el VPS como root
# ═══════════════════════════════════════════════════════════

set -e

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          🛡️ StreamFlow Guardian v2.0 - Instalación          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Configuración ──
APP_DIR="/root/streamflow"
GUARDIAN_DIR="$APP_DIR/guardian-agent"
ADMIN_PHONE="573222468509"
APP_URL="http://localhost:5000"

# ── 1. Verificar Node.js ──
echo "📋 Verificando Node.js..."
if ! command -v node &> /dev/null; then
    echo "📦 Instalando Node.js 18..."
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
    apt-get install -y nodejs
fi
echo "✅ Node.js $(node --version)"

# ── 2. Verificar dependencias del sistema ──
echo "📋 Verificando dependencias..."
apt-get update -qq
apt-get install -y -qq \
    chromium-browser \
    chromium-chromedriver \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    xvfb \
    > /dev/null 2>&1 || true
echo "✅ Dependencias instaladas"

# ── 3. Instalar dependencias del Guardian ──
echo "📦 Instalando dependencias del Guardian..."
cd "$GUARDIAN_DIR"
npm install --production 2>&1 | tail -5
echo "✅ Dependencias NPM instaladas"

# ── 4. Crear archivo de configuración ──
echo "📝 Creando configuración..."
cat > "$GUARDIAN_DIR/.env" << EOF
# StreamFlow Guardian v2.0 - Configuración
APP_URL=$APP_URL
APP_DIR=$APP_DIR
ADMIN_PHONE=$ADMIN_PHONE
GUARDIAN_PORT=5001
EOF
echo "✅ Configuración creada"

# ── 5. Crear servicio systemd ──
echo "🔧 Creando servicio systemd..."
cat > /etc/systemd/system/streamflow-guardian.service << EOF
[Unit]
Description=StreamFlow Guardian Agent v2.0
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$GUARDIAN_DIR
ExecStart=/usr/bin/node server.js
Restart=always
RestartSec=10
Environment=NODE_ENV=production
EnvironmentFile=$GUARDIAN_DIR/.env

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable streamflow-guardian
echo "✅ Servicio systemd creado"

# ── 6. Iniciar Guardian ──
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  🚀 Iniciando Guardian..."
echo "══════════════════════════════════════════════════════════"
echo ""
echo "  ⚠️  IMPORTANTE: Se mostrará un QR en pantalla."
echo "  📱 Escanéalo con el WhatsApp del número +57 322 2468509"
echo ""
echo "  Esperando 30 segundos para escanear..."
echo ""

# Iniciar con Xvfb para que funcione en headless
xvfb-run --auto-servernum node server.js &
GUARDIAN_PID=$!

# Esperar a que aparezca el QR
sleep 15

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✅ Guardian iniciado (PID: $GUARDIAN_PID)"
echo ""
echo "  📱 Escanea el QR con WhatsApp +57 322 2468509"
echo "  📊 API de estado: http://localhost:5001/health"
echo "  🛡️  El agente monitoreará StreamFlow 24/7"
echo "══════════════════════════════════════════════════════════"
