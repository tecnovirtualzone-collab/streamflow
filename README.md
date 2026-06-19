# 🌊 StreamFlow v3.0 — IPTV Proxy con VLC Relay

**Sistema IPTV proxy de alta sigilosidad con relay VLC para retransmisión de canales.**

---

## ✨ Características v3.0

- 🔒 **JWT Authentication** — Tokens seguros
- 🛡️ **Rate Limiting** — Protección contra fuerza bruta
- 📺 **VLC Relay Manager** — 1 conexión al proveedor por canal (indetectable)
- 🔄 **Auto-start/stop** de VLC por canal según demanda
- 💊 **Health checks** y auto-reconnect
- 📊 **Panel admin** con JWT
- 💰 **Sistema de pagos** integrado
- 💬 **WhatsApp** notificaciones automáticas
- 📦 **Paquetes** de contenido configurables
- 📋 **Logs de acceso** detallados

---

## 🏗️ Arquitectura

```
Proveedor IPTV → VLC (1 conexión/canal) → StreamFlow Proxy → Usuarios (100-200)
```

**Clave del sistema:** VLC abre UNA SOLA conexión al proveedor por canal, sin importar cuántos usuarios estén viendo. El proveedor NUNCA detecta múltiples conexiones.

---

## 📋 Requisitos

- Docker
- 8GB RAM, 2CPU (mínimo para 100-200 usuarios)
- Proveedor IPTV compatible Xtream Codes o M3U

---

## 🚀 Despliegue en EasyPanel

### 1. Base de datos
Create Service → Postgres → Nombre: `streamflow-db`

### 2. App principal
Create Service → App → Source: GitHub → tu repo streamflow

**Variables de entorno:**
```
DATABASE_URL=postgresql://USUARIO:PASSWORD@streamflow-db/DATABASE
SECRET_KEY=(generar: python3 -c "import secrets; print(secrets.token_hex(32))")
JWT_SECRET=(generar: python3 -c "import secrets; print(secrets.token_hex(32))")
ADMIN_USER=admin
ADMIN_PASSWORD=tu_password_seguro
URL_M3U=http://tu-proveedor.com/get.php?username=X&password=Y&type=m3u_plus
VLC_HTTP_PORT=8888
VLC_HTTP_USER=streamflow
VLC_HTTP_PASS=tu_vlc_password
VLC_TIMEOUT=60
VLC_MAX_CHANNELS=8
ALLOWED_ORIGINS=https://tu-dominio.com
```

### 3. Deploy

---

## 📊 Capacidad del servidor

| Recurso | Uso base | Por canal VLC |
|---------|----------|---------------|
| RAM | ~800MB | +80MB |
| CPU | 0.7 cores | +0.15 cores |

**Capacidad máxima:** ~8 canales simultáneos, 100-200 usuarios totales

---

## 🔗 URLs del sistema

- **M3U del cliente:** `https://tu-dominio.com/m3u/USERNAME?pass=PASSWORD`
- **Stream:** `https://tu-dominio.com/live/USER/PASS/CANAL`
- **Panel admin:** `https://tu-dominio.com/panel/`
- **Stats:** `https://tu-dominio.com/admin/stats`
- **VLC Relays:** `https://tu-dominio.com/admin/relays`

---

## 📱 Apps IPTV compatibles

- IPTV Smarters Pro
- TiviMate
- VLC
- Kodi (PVR IPTV Simple Client)
- Cualquier app que soporte M3U + Xtream Codes API

---

## 💰 Modelo de negocio sugerido

| Paquete | Precio/mes | Pantallas |
|---------|-----------|-----------|
| Básico | $3 | 1 |
| Premium | $5 | 2 |
| Familiar | $7 | 3 |

**Ingreso estimado con 100 usuarios:** ~$400-500/mes

---

## 📜 Licencia

MIT
