#!/bin/bash
# Iniciar el servicio de WhatsApp en background
cd /app/wa-service && node server.js &
WA_PID=$!
echo "🚀 WA Service iniciado (PID: $WA_PID)"

# Iniciar gunicorn (Python)
cd /app && gunicorn --bind 0.0.0.0:5000 --worker-class gevent --workers 4 --worker-connections 1000 --timeout 300 app:app
