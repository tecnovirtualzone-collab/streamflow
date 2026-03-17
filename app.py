from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import requests, secrets, os
from models import db, Usuario, MacRegistrada, SesionActiva

app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost/streamflow')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

URL_M3U_PROVEEDOR = os.environ.get('URL_M3U', '')

# ════════════════════════════════════════════════════════════════
#  HELPERS PROVEEDOR
# ════════════════════════════════════════════════════════════════

def get_host():
    """Siempre devuelve https:// + dominio"""
    return "https://" + request.host

def get_proveedor_info():
    """Extrae base_url, usuario y contraseña del proveedor desde URL_M3U"""
    url = URL_M3U_PROVEEDOR
    try:
        parsed = urlparse(url)
        base   = f"{parsed.scheme}://{parsed.netloc}"
        params = parse_qs(parsed.query)
        user   = params.get('username', [''])[0]
        pwd    = params.get('password', [''])[0]
        return base, user, pwd
    except Exception:
        return '', '', ''

PAQUETES = {
    'basico':   {'max_conexiones': 1},
    'premium':  {'max_conexiones': 2},
    'familiar': {'max_conexiones': 3},
}

# ════════════════════════════════════════════════════════════════
#  CONTROL DE ACCESO
# ════════════════════════════════════════════════════════════════

def limpiar_sesiones():
    limite = datetime.utcnow() - timedelta(seconds=30)
    SesionActiva.query.filter(SesionActiva.ultimo_ping < limite).delete()
    db.session.commit()

def autenticar(usuario_str, contrasena_str):
    """Devuelve (user, error_msg)"""
    user = Usuario.query.filter_by(usuario=usuario_str, activo=True).first()
    if not user:
        return None, "Usuario no encontrado"
    if not check_password_hash(user.contrasena, contrasena_str):
        return None, "Contraseña incorrecta"
    if datetime.utcnow() > user.fecha_expira:
        return None, "Cuenta expirada"
    return user, None

def verificar_acceso(usuario_str, contrasena_str, mac, ip, canal):
    limpiar_sesiones()
    user, error = autenticar(usuario_str, contrasena_str)
    if not user:
        return False, None, error
    if mac:
        macs_guardadas = [m.mac.lower() for m in user.macs]
        if not macs_guardadas:
            db.session.add(MacRegistrada(usuario_id=user.id, mac=mac.lower(), nombre="Dispositivo 1"))
            db.session.commit()
        elif mac.lower() not in macs_guardadas:
            if len(macs_guardadas) >= user.max_conexiones:
                return False, None, "Dispositivo no autorizado."
            db.session.add(MacRegistrada(usuario_id=user.id, mac=mac.lower(), nombre=f"Dispositivo {len(macs_guardadas)+1}"))
            db.session.commit()
    sesiones    = SesionActiva.query.filter_by(usuario_id=user.id).all()
    ips_activas = list({s.ip for s in sesiones})
    if ip not in ips_activas and len(ips_activas) >= user.max_conexiones:
        return False, None, f"Límite de {user.max_conexiones} conexión(es) alcanzado"
    sesion = SesionActiva(usuario_id=user.id, ip=ip, mac=mac, canal=canal)
    db.session.add(sesion)
    db.session.commit()
    return True, user, sesion.id

# ════════════════════════════════════════════════════════════════
#  XTREAM CODES API
# ════════════════════════════════════════════════════════════════

@app.route('/player_api.php')
def player_api():
    usuario    = request.args.get('username', '')
    contrasena = request.args.get('password', '')
    action     = request.args.get('action', '')
    host       = get_host()

    user, error = autenticar(usuario, contrasena)
    if not user:
        return jsonify({"user_info": {"auth": 0}}), 401

    base_prov, prov_user, prov_pass = get_proveedor_info()

    # Login — devuelve info del servidor con HTTPS
    if not action:
        return jsonify({
            "user_info": {
                "auth":                   1,
                "username":               usuario,
                "password":               contrasena,
                "status":                 "Active",
                "exp_date":               str(int(user.fecha_expira.timestamp())),
                "is_trial":               "0",
                "active_cons":            str(len(user.sesiones)),
                "created_at":             str(int(user.creado.timestamp())),
                "max_connections":        str(user.max_conexiones),
                "allowed_output_formats": ["m3u8", "ts", "rtmp"]
            },
            "server_info": {
                "url":             host,
                "port":            "443",
                "https_port":      "443",
                "server_protocol": "https",
                "rtmp_port":       "443",
                "timestamp_now":   int(datetime.utcnow().timestamp()),
                "time_now":        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "timezone":        "UTC"
            }
        })

    # Todas las demás acciones — proxy al proveedor
    params = {k: v for k, v in request.args.items() if k not in ['username', 'password']}
    params.update({'username': prov_user, 'password': prov_pass})
    try:
        resp = requests.get(f"{base_prov}/player_api.php", params=params, timeout=15)
        return Response(resp.content, content_type=resp.headers.get('Content-Type', 'application/json'))
    except Exception:
        return jsonify({'error': 'Error al conectar con el proveedor'}), 502

@app.route('/get.php')
def get_php():
    """Lista M3U completa para el cliente"""
    usuario    = request.args.get('username', '')
    contrasena = request.args.get('password', '')

    user, error = autenticar(usuario, contrasena)
    if not user:
        return "Acceso denegado", 403

    ip  = request.headers.get('X-Real-IP', request.remote_addr)
    mac = request.headers.get('X-MAC-Address', '')
    limpiar_sesiones()
    sesiones    = SesionActiva.query.filter_by(usuario_id=user.id).all()
    ips_activas = list({s.ip for s in sesiones})
    if ip not in ips_activas and len(ips_activas) >= user.max_conexiones:
        return "Límite de conexiones alcanzado", 403

    sesion = SesionActiva(usuario_id=user.id, ip=ip, mac=mac, canal='m3u')
    db.session.add(sesion)
    db.session.commit()

    base_prov, prov_user, prov_pass = get_proveedor_info()
    host = get_host()

    try:
        resp = requests.get(
            f"{base_prov}/get.php",
            params={'username': prov_user, 'password': prov_pass, 'type': 'm3u_plus', 'output': 'ts'},
            timeout=20
        )
        # Reescribir URLs de stream para que pasen por nuestro proxy
        contenido = resp.text
        contenido = contenido.replace(
            f"{base_prov}/live/{prov_user}/{prov_pass}/",
            f"{host}/live/{usuario}/{contrasena}/"
        )
        contenido = contenido.replace(
            f"{base_prov}/movie/{prov_user}/{prov_pass}/",
            f"{host}/movie/{usuario}/{contrasena}/"
        )
        contenido = contenido.replace(
            f"{base_prov}/series/{prov_user}/{prov_pass}/",
            f"{host}/series/{usuario}/{contrasena}/"
        )
        return Response(contenido, content_type='application/x-mpegURL')
    except Exception:
        return "Error al obtener lista", 502

# ════════════════════════════════════════════════════════════════
#  STREAMS — proxy asíncrono con gevent
# ════════════════════════════════════════════════════════════════

@app.route('/live/<usuario>/<contrasena>/<canal>')
@app.route('/movie/<usuario>/<contrasena>/<canal>')
@app.route('/series/<usuario>/<contrasena>/<canal>')
def stream_xtream(usuario, contrasena, canal):
    mac  = request.headers.get('X-MAC-Address', '')
    ip   = request.headers.get('X-Real-IP', request.remote_addr)
    ruta = request.path.split('/')[1]  # live, movie o series

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({'error': resultado}), 403

    base_prov, prov_user, prov_pass = get_proveedor_info()
    url_real = f"{base_prov}/{ruta}/{prov_user}/{prov_pass}/{canal}"

    try:
        resp = requests.get(
            url_real, stream=True, timeout=30,
            headers={'User-Agent': 'Mozilla/5.0 (SMART-TV)'}
        )

        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            except Exception:
                pass

        return Response(
            generate(),
            content_type=resp.headers.get('Content-Type', 'video/mp2t'),
            headers={
                'Cache-Control':    'no-cache',
                'X-Accel-Buffering':'no',
            }
        )
    except Exception:
        return jsonify({'error': 'Error al conectar con el proveedor'}), 502

@app.route('/stream')
def stream_legacy():
    """Endpoint legacy"""
    usuario    = request.args.get('user', '')
    contrasena = request.args.get('pass', '')
    canal      = request.args.get('channel', '')
    mac        = request.args.get('mac', request.headers.get('X-MAC-Address', ''))
    ip         = request.headers.get('X-Real-IP', request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({'error': resultado}), 403

    base_prov, prov_user, prov_pass = get_proveedor_info()
    url_real = f"{base_prov}/live/{prov_user}/{prov_pass}/{canal}"

    try:
        resp = requests.get(
            url_real, stream=True, timeout=30,
            headers={'User-Agent': 'Mozilla/5.0'}
        )

        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            except Exception:
                pass

        return Response(
            generate(),
            content_type=resp.headers.get('Content-Type', 'video/mp2t'),
            headers={
                'Cache-Control':    'no-cache',
                'X-Accel-Buffering':'no',
            }
        )
    except Exception:
        return jsonify({'error': 'Error al conectar'}), 502

@app.route('/ping')
def ping():
    sid = request.args.get('sid')
    if sid:
        s = SesionActiva.query.get(int(sid))
        if s:
            s.ultimo_ping = datetime.utcnow()
            db.session.commit()
    return '', 204

# ════════════════════════════════════════════════════════════════
#  PANEL ADMIN — USUARIOS
# ════════════════════════════════════════════════════════════════

@app.route('/admin/usuarios', methods=['GET'])
def listar_usuarios():
    users = Usuario.query.order_by(Usuario.creado.desc()).all()
    return jsonify([{
        'id':                 u.id,
        'usuario':            u.usuario,
        'paquete':            u.paquete,
        'activo':             u.activo,
        'max_conexiones':     u.max_conexiones,
        'expira':             u.fecha_expira.isoformat(),
        'expira_en_dias':     max(0, (u.fecha_expira - datetime.utcnow()).days),
        'conexiones_activas': len(u.sesiones),
        'macs_registradas':   len(u.macs)
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
        'usuario':        user.usuario,
        'contrasena':     pwd,
        'paquete':        paquete,
        'max_conexiones': config['max_conexiones'],
        'expira':         user.fecha_expira.isoformat()
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

@app.route('/admin/usuarios/<int:uid>/macs')
def ver_macs(uid):
    user = Usuario.query.get_or_404(uid)
    return jsonify([{
        'id':         m.id,
        'mac':        m.mac,
        'nombre':     m.nombre,
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
    return jsonify({
        'total_usuarios':     Usuario.query.count(),
        'usuarios_activos':   Usuario.query.filter_by(activo=True).count(),
        'usuarios_expirados': Usuario.query.filter(Usuario.fecha_expira < datetime.utcnow()).count(),
        'online_ahora':       SesionActiva.query.distinct(SesionActiva.usuario_id).count()
    })

# ════════════════════════════════════════════════════════════════
#  INICIO
# ════════════════════════════════════════════════════════════════

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
