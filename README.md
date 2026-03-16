# StreamFlow — Guía de instalación en EasyPanel

## Paso 1 — Subir a GitHub
1. Ve a github.com/new
2. Nombre: streamflow | Privado ✓
3. Sube todos estos archivos tal como están

## Paso 2 — Crear base de datos en EasyPanel
1. Create Service → Postgres
2. Nombre: streamflow-db
3. Copia: Host, User, Password, Database

## Paso 3 — Crear la app backend
1. Create Service → App
2. Nombre: streamflow-api
3. Source: GitHub → tu repo streamflow
4. En "Environment Variables" agrega:

   DATABASE_URL = postgresql://USUARIO:PASSWORD@streamflow-db/DATABASE
   URL_M3U = http://TU-PROVEEDOR.COM/get.php?username=X&password=Y&type=m3u_plus

5. Deploy

## Paso 4 — Panel admin
1. Create Service → App (o Static)
2. Sube solo la carpeta /panel
3. Asigna un dominio

## URLs del sistema
- M3U del cliente:  https://tu-dominio.com/m3u/USUARIO?pass=CLAVE
- Stream:           https://tu-dominio.com/stream?user=X&pass=Y&channel=Z
- Panel admin:      https://tu-panel.com
- Stats:            https://tu-dominio.com/admin/stats

## Paquetes
| Paquete  | Pantallas | Días |
|----------|-----------|------|
| basico   | 1         | 30   |
| premium  | 2         | 30   |
| familiar | 3         | 30   |
