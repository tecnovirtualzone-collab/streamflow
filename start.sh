#!/bin/bash
# Iniciar el servicio de WhatsApp en background (si existe)
if [ -d /app/wa-service ]; then
    cd /app/wa-service && node server.js &
    WA_PID=$!
    echo "🚀 WA Service iniciado (PID: $WA_PID)"
fi

# Iniciar la app Python
cd /app && python3 app.py
