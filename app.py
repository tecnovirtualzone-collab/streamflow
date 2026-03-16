from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import requests, secrets, os
from models import db, Usuario, MacRegistrada, SesionActiva

app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost/streamflow')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# ── Pon aquí la URL M3U de tu proveedor mayorista ────────────────
URL_M3U_PROVEEDOR = os.environ.get('URL_M3U', 'https://raw.githubusercontent.com/iptv-org/iptv/master/streams/co.m3u')

PAQUETES = {
    'basico':   {'max_conexiones': 1},
    'premium':  {'max_conexiones': 2},
    'familiar': {'max_conexiones': 3},
}

# ════════════════════════════════════════════════════════════════
#  CONTROL DE ACCESO
# ════════════════════════════════════════════════════════════════

def limpiar_sesiones():
    """Elimina sesiones sin ping en los últimos 30 segundos"""
    limite = datetime.utcnow() - timedelta(seconds=30)
    SesionActiva.query.filter(SesionActiva.ultimo_ping < limite).delete()
    db.session.commit()

def verificar_acceso(usuario_str, contrasena_str, mac, ip, canal):
    limpiar_sesiones()

    # 1. Credenciales
    user = Usuario.query.filter_by(usuario=usuario_str, activo=True).first()
    if not user:
        return False, None, "Usuario no encontrado"
    if not check_password_hash(user.contrasena, contrasena_str):
        return False, None, "Contraseña incorrecta"

    # 2. Expiración
    if datetime.utcnow() > user.fecha_expira:
        return False, None, "Cuenta expirada"

    # 3. Control de MAC
    if mac:
        macs_guardadas = [m.mac.lower() for m in user.macs]
        if not macs_guardadas:
            db.session.add(MacRegistrada(usuario_id=user.id, mac=mac.lower(), nombre="Dispositivo 1"))
            db.session.commit()
        elif mac.lower() not in macs_guardadas:
            if len(macs_guardadas) >= user.max_conexiones:
                return False, None, "Dispositivo no autorizado. Contacta al administrador."
            db.session.add(MacRegistrada(usuario_id=user.id, mac=mac.lower(), nombre=f"Dispositivo {len(macs_guardadas)+1}"))
            db.session.commit()

    # 4. Conexiones simultáneas por IP
    sesiones = SesionActiva.query.filter_by(usuario_id=user.id).all()
    ips_activas = list({s.ip for s in sesiones})
    if ip not in ips_activas and len(ips_activas) >= user.max_conexiones:
        return False, None, f"Límite de {user.max_conexiones} conexión(es) simultánea(s) alcanzado"

    sesion = SesionActiva(usuario_id=user.id, ip=ip, mac=mac, canal=canal)
    db.session.add(sesion)
    db.session.commit()
    return True, user, sesion.id

# ════════════════════════════════════════════════════════════════
#  ENDPOINTS DE STREAMING
# ════════════════════════════════════════════════════════════════

@app.route('/stream')
def stream():
    usuario    = request.args.get('user', '')
    contrasena = request.args.get('pass', '')
    canal      = request.args.get('channel', '')
    mac        = request.args.get('mac', request.headers.get('X-MAC-Address', ''))
    ip         = request.headers.get('X-Real-IP', request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({'error': resultado}), 403

    try:
        resp = requests.get(URL_M3U_PROVEEDOR, stream=True, timeout=10)
        return Response(
            resp.iter_content(chunk_size=4096),
            content_type=resp.headers.get('Content-Type', 'video/mp2t')
        )
    except Exception as e:
        return jsonify({'error': 'Error al conectar con el proveedor'}), 502

@app.route('/ping')
def ping():
    """El cliente hace ping cada 10s para mantener la sesión activa"""
    sid = request.args.get('sid')
    if sid:
        s = SesionActiva.query.get(int(sid))
        if s:
            s.ultimo_ping = datetime.utcnow()
            db.session.commit()
    return '', 204

@app.route('/m3u/<usuario>')
def generar_m3u(usuario):
    """Genera la lista M3U personalizada para el cliente"""
    contrasena = request.args.get('pass', '')
    user = Usuario.query.filter_by(usuario=usuario, activo=True).first()
    if not user or not check_password_hash(user.contrasena, contrasena):
        return "Acceso denegado", 403
    if datetime.utcnow() > user.fecha_expira:
        return "Cuenta expirada", 403

    # Descarga la M3U del proveedor y la re-sirve con tus URLs
    try:
        resp = requests.get(URL_M3U_PROVEEDOR, timeout=15)
        lineas = resp.text.splitlines()
        nueva_m3u = []
        host = request.host_url.rstrip('/')
        i = 0
        while i < len(lineas):
            linea = lineas[i]
            if linea.startswith('#EXTINF'):
                nueva_m3u.append(linea)
                if i + 1 < len(lineas):
                    url_original = lineas[i + 1]
                    canal_id = abs(hash(url_original)) % 999999
                    nueva_url = f"{host}/stream?user={usuario}&pass={contrasena}&channel={canal_id}"
                    nueva_m3u.append(nueva_url)
                    i += 2
                    continue
            else:
                nueva_m3u.append(linea)
            i += 1

        return Response('\n'.join(nueva_m3u), content_type='application/x-mpegURL')
    except Exception:
        return "Error al obtener lista", 502

# ════════════════════════════════════════════════════════════════
#  PANEL ADMIN — USUARIOS
# ════════════════════════════════════════════════════════════════

@app.route('/admin/usuarios', methods=['GET'])
def listar_usuarios():
    users = Usuario.query.order_by(Usuario.creado.desc()).all()
    return jsonify([{
        'id': u.id,
        'usuario': u.usuario,
        'paquete': u.paquete,
        'activo': u.activo,
        'max_conexiones': u.max_conexiones,
        'expira': u.fecha_expira.isoformat(),
        'expira_en_dias': max(0, (u.fecha_expira - datetime.utcnow()).days),
        'conexiones_activas': len(u.sesiones),
        'macs_registradas': len(u.macs)
    } for u in users])

@app.route('/admin/usuarios', methods=['POST'])
def crear_usuario():
    data    = request.json
    paquete = data.get('paquete', 'basico')
    dias    = int(data.get('dias', 30))
    config  = PAQUETES.get(paquete, PAQUETES['basico'])
    pwd     = secrets.token_urlsafe(8)

    if Usuario.query.filter_by(usuario=data['usuario']).first():
        return jsonify({'error': 'El usuario ya existe'}), 400

    user = Usuario(
        usuario        = data['usuario'],
        contrasena     = generate_password_hash(pwd),
        paquete        = paquete,
        max_conexiones = config['max_conexiones'],
        fecha_expira   = datetime.utcnow() + timedelta(days=dias)
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({
        'usuario':    user.usuario,
        'contrasena': pwd,
        'paquete':    paquete,
        'max_conexiones': config['max_conexiones'],
        'expira':     user.fecha_expira.isoformat()
    })

@app.route('/admin/usuarios/<int:uid>', methods=['DELETE'])
def eliminar_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/usuarios/<int:uid>/suspender', methods=['POST'])
def suspender_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    user.activo = not user.activo
    db.session.commit()
    return jsonify({'activo': user.activo})

@app.route('/admin/usuarios/<int:uid>/renovar', methods=['POST'])
def renovar_usuario(uid):
    data = request.json
    dias = int(data.get('dias', 30))
    user = Usuario.query.get_or_404(uid)
    if user.fecha_expira < datetime.utcnow():
        user.fecha_expira = datetime.utcnow() + timedelta(days=dias)
    else:
        user.fecha_expira = user.fecha_expira + timedelta(days=dias)
    user.activo = True
    db.session.commit()
    return jsonify({'expira': user.fecha_expira.isoformat()})

# ════════════════════════════════════════════════════════════════
#  PANEL ADMIN — MACs
# ════════════════════════════════════════════════════════════════

@app.route('/admin/usuarios/<int:uid>/macs')
def ver_macs(uid):
    user = Usuario.query.get_or_404(uid)
    return jsonify([{
        'id': m.id,
        'mac': m.mac,
        'nombre': m.nombre,
        'registrada': m.registrada.isoformat()
    } for m in user.macs])

@app.route('/admin/macs/<int:mid>', methods=['DELETE'])
def revocar_mac(mid):
    mac = MacRegistrada.query.get_or_404(mid)
    db.session.delete(mac)
    db.session.commit()
    return jsonify({'ok': True})

# ════════════════════════════════════════════════════════════════
#  ESTADÍSTICAS
# ════════════════════════════════════════════════════════════════

@app.route('/admin/stats')
def stats():
    limpiar_sesiones()
    total     = Usuario.query.count()
    activos   = Usuario.query.filter_by(activo=True).count()
    expirados = Usuario.query.filter(Usuario.fecha_expira < datetime.utcnow()).count()
    online    = SesionActiva.query.distinct(SesionActiva.usuario_id).count()
    return jsonify({
        'total_usuarios':    total,
        'usuarios_activos':  activos,
        'usuarios_expirados': expirados,
        'online_ahora':      online
    })

# ════════════════════════════════════════════════════════════════
#  XTREAM CODES API — Compatible con IPTV Smarters Pro
# ════════════════════════════════════════════════════════════════

def generar_m3u_contenido(usuario, contrasena, host):
    resp = requests.get(URL_M3U_PROVEEDOR, timeout=15)
    lineas = resp.text.splitlines()
    nueva_m3u = []
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        if linea.startswith('#EXTINF'):
            nueva_m3u.append(linea)
            if i + 1 < len(lineas):
                url_original = lineas[i + 1]
                canal_id = abs(hash(url_original)) % 999999
                nueva_url = f"{host}/stream?user={usuario}&pass={contrasena}&channel={canal_id}"
                nueva_m3u.append(nueva_url)
                i += 2
                continue
        else:
            nueva_m3u.append(linea)
        i += 1
    return '\n'.join(nueva_m3u)

@app.route('/player_api.php')
def player_api():
    usuario    = request.args.get('username', '')
    contrasena = request.args.get('password', '')

    user = Usuario.query.filter_by(usuario=usuario, activo=True).first()
    if not user or not check_password_hash(user.contrasena, contrasena):
        return jsonify({"user_info": {"auth": 0}}), 401
    if datetime.utcnow() > user.fecha_expira:
        return jsonify({"user_info": {"auth": 0}}), 401

    host = request.host_url.rstrip('/')
    return jsonify({
        "user_info": {
            "auth": 1,
            "username": usuario,
            "password": contrasena,
            "status": "Active",
            "exp_date": str(int(user.fecha_expira.timestamp())),
            "is_trial": "0",
            "active_cons": str(len(user.sesiones)),
            "created_at": str(int(user.creado.timestamp())),
            "max_connections": str(user.max_conexiones),
            "allowed_output_formats": ["m3u8", "ts"]
        },
        "server_info": {
            "url": host,
            "port": "443",
            "https_port": "443",
            "server_protocol": "https",
            "rtmp_port": "443",
            "timestamp_now": int(datetime.utcnow().timestamp()),
            "time_now": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        }
    })

@app.route('/get.php')
def get_php():
    usuario    = request.args.get('username', '')
    contrasena = request.args.get('password', '')

    user = Usuario.query.filter_by(usuario=usuario, activo=True).first()
    if not user or not check_password_hash(user.contrasena, contrasena):
        return "Acceso denegado", 403
    if datetime.utcnow() > user.fecha_expira:
        return "Cuenta expirada", 403

    ip  = request.headers.get('X-Real-IP', request.remote_addr)
    mac = request.headers.get('X-MAC-Address', '')
    limpiar_sesiones()
    sesiones   = SesionActiva.query.filter_by(usuario_id=user.id).all()
    ips_activas = list({s.ip for s in sesiones})
    if ip not in ips_activas and len(ips_activas) >= user.max_conexiones:
        return "Límite de conexiones alcanzado", 403

    sesion = SesionActiva(usuario_id=user.id, ip=ip, mac=mac, canal='m3u')
    db.session.add(sesion)
    db.session.commit()

    try:
        host    = request.host_url.rstrip('/')
        content = generar_m3u_contenido(usuario, contrasena, host)
        return Response(content, content_type='application/x-mpegURL')
    except Exception:
        return "Error al obtener lista", 502

# ════════════════════════════════════════════════════════════════
#  INICIO
# ════════════════════════════════════════════════════════════════

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
