# StreamFlow - IPTV Relay Platform

Plataforma IPTV con Smart Relay: 1 cuenta del proveedor → usuarios ilimitados.

## Instalación en EasyPanel

### 1. Crear proyecto
- Ve a tu EasyPanel
- Click en **"New Project"**
- Nombre: `streamflow` (o el que prefieras)

### 2. Crear base de datos PostgreSQL
- Dentro del proyecto, click **"New Service" → "Database" → "PostgreSQL"**
- Configura:
  - **Database Name:** `streamflow`
  - **Username:** `postgres`
  - **Password:** (elige una segura)
- Click **"Deploy"**

### 3. Crear la aplicación
- Click **"New Service" → "App"**
- **Source:** GitHub
- **Repository:** `https://github.com/tu-usuario/streamflow` (o tu fork)
- **Branch:** `main`
- **Build Method:** Dockerfile

### 4. Configurar variables de entorno
En la configuración de la app, agrega estas variables:

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `DATABASE_URL` | Conexión a PostgreSQL | `postgresql://postgres:password@db-host:5432/streamflow` |
| `SECRET_KEY` | Clave secreta (random) | `abc123...` |
| `JWT_SECRET` | Clave JWT (random) | `xyz789...` |
| `ADMIN_USER` | Usuario admin | `admin` |
| `ADMIN_PASSWORD` | Contraseña admin | `tu-password-segura` |
| `URL_M3U` | URL del proveedor IPTV | `http://proveedor.com/get.php?username=USER&password=PASS&type=m3u_plus&output=ts` |
| `ALLOWED_ORIGINS` | Tu dominio | `https://tudominio.easypanel.host` |
| `VLC_HTTP_PASS` | Contraseña VLC | `tu-password-vlc` |

### 5. Configurar dominio
- App → **Domain** → Agrega tu dominio
- Click **"SSL"** para habilitar HTTPS

### 6. Acceder
- Panel admin: `https://tu-dominio.com/panel`
- Usuario: `admin` / Contraseña: la que configuraste

## Características

- **Smart Relay:** 3 conexiones del proveedor → usuarios ilimitados
- **VLC Relay:** 1 conexión por canal (indetectable)
- **JWT Auth:** Seguridad con tokens
- **Panel Admin:** Gestión de usuarios, canales, pagos
- **API REST:** Integración con otras plataformas

## Planes de usuario

| Plan | Canales | Precio sugerido |
|------|---------|-----------------|
| Básico | 40 | $10,000 COP/mes |
| Estándar | 70 | $18,000 COP/mes |
| Premium | 100+ | $25,000 COP/mes

## Requisitos del servidor

- **RAM:** 8GB mínimo
- **CPU:** 2 cores mínimo
- **Almacenamiento:** 20GB mínimo
- **Proveedor IPTV:** Cuenta con al menos 3 conexiones simultáneas
