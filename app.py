from flask import Flask, request, Response, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import requests, secrets, os, re
from models import db, Usuario, MacRegistrada, SesionActiva

app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost/streamflow')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

URL_M3U_PROVEEDOR = os.environ.get('URL_M3U', 'https://raw.githubusercontent.com/iptv-org/iptv/master/streams/co.m3u')

PAQUETES = {
    'basico':   {'max_conexiones': 1},
    'premium':  {'max_conexiones': 2},
    'familiar': {'max_conexiones': 3},
}

# ════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════

def get_host():
    """Siempre devuelve https:// + dominio"""
    return "https://" + request.host

# Cache simple en memoria para la M3U del proveedor (5 minutos)
_m3u_cache = {'data': None, 'ts': None}

def obtener_canales():
    """
    Descarga la M3U del proveedor y devuelve lista de dicts:
    {id, nombre, grupo, logo, url_original}
    """
    ahora = datetime.utcnow()
    if _m3u_cache['data'] and _m3u_cache['ts'] and (ahora - _m3u_cache['ts']).seconds < 300:
        return _m3u_cache['data']

    try:
        resp = requests.get(URL_M3U_PROVEEDOR, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        resp.raise_for_status()
        lineas = resp.text.splitlines()
    except Exception:
        return _m3u_cache['data'] or []

    canales  = []
    canal_id = 1
    i = 0
    while i < len(lineas):
        linea = lineas[i].strip()
        if linea.startswith('#EXTINF'):
            nombre_match = re.search(r',(.+)$', linea)
            grupo_match  = re.search(r'group-title="([^"]*)"', linea)
            logo_match   = re.search(r'tvg-logo="([^"]*)"', linea)
            nombre = nombre_match.group(1).strip() if nombre_match else f"Canal {canal_id}"
            grupo  = grupo_match.group(1).strip()  if grupo_match  else "General"
            logo   = logo_match.group(1).strip()   if logo_match   else ""

            if i + 1 < len(lineas):
                url_original = lineas[i + 1].strip()
                if url_original and not url_original.startswith('#'):
                    canales.append({
                        'id':           canal_id,
                        'nombre':       nombre,
                        'grupo':        grupo,
                        'logo':         logo,
                        'url_original': url_original,
                    })
                    canal_id += 1
                    i += 2
                    continue
        i += 1

    _m3u_cache['data'] = canales
    _m3u_cache['ts']   = ahora
    return canales

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
#  STREAMING
# ════════════════════════════════════════════════════════════════

@app.route('/live/<usuario>/<contrasena>/<int:canal_id>.ts')
@app.route('/live/<usuario>/<contrasena>/<int:canal_id>.m3u8')
def live_stream(usuario, contrasena, canal_id):
    """Endpoint de stream con formato Xtream Codes"""
    ip  = request.headers.get('X-Real-IP', request.remote_addr)
    mac = request.headers.get('X-MAC-Address', '')

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, str(canal_id))
    if not ok:
        return jsonify({'error': resultado}), 403

    canales = obtener_canales()
    canal   = next((c for c in canales if c['id'] == canal_id), None)
    if not canal:
        return jsonify({'error': 'Canal no encontrado'}), 404

    try:
        resp = requests.get(canal['url_original'], stream=True, timeout=10,
                            headers={'User-Agent': 'Mozilla/5.0'})
        return Response(
            resp.iter_content(chunk_size=4096),
            content_type=resp.headers.get('Content-Type', 'video/mp2t')
        )
    except Exception:
        return jsonify({'error': 'Error al conectar con el proveedor'}), 502

@app.route('/stream')
def stream_legacy():
    """Endpoint legacy"""
    usuario    = request.args.get('user', '')
    contrasena = request.args.get('pass', '')
    canal_id   = request.args.get('channel', '0')
    mac        = request.args.get('mac', request.headers.get('X-MAC-Address', ''))
    ip         = request.headers.get('X-Real-IP', request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal_id)
    if not ok:
        return jsonify({'error': resultado}), 403

    canales = obtener_canales()
    try:
        cid   = int(canal_id)
        canal = next((c for c in canales if c['id'] == cid), None)
        url   = canal['url_original'] if canal else URL_M3U_PROVEEDOR
    except Exception:
        url = URL_M3U_PROVEEDOR

    try:
        resp = requests.get(url, stream=True, timeout=10,
                            headers={'User-Agent': 'Mozilla/5.0'})
        return Response(
            resp.iter_content(chunk_size=4096),
            content_type=resp.headers.get('Content-Type', 'video/mp2t')
        )
    except Exception:
        return jsonify({'error': 'Error al conectar con el proveedor'}), 502

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
#  M3U CLÁSICA
# ════════════════════════════════════════════════════════════════

@app.route('/m3u/<usuario>')
def generar_m3u(usuario):
    contrasena = request.args.get('pass', '')
    user = Usuario.query.filter_by(usuario=usuario, activo=True).first()
    if not user or not check_password_hash(user.contrasena, contrasena):
        return "Acceso denegado", 403
    if datetime.utcnow() > user.fecha_expira:
        return "Cuenta expirada", 403

    host    = get_host()
    canales = obtener_canales()
    lineas  = ['#EXTM3U']
    for c in canales:
        lineas.append(f'#EXTINF:-1 tvg-logo="{c["logo"]}" group-title="{c["grupo"]}",{c["nombre"]}')
        lineas.append(f'{host}/live/{usuario}/{contrasena}/{c["id"]}.ts')

    return Response('\n'.join(lineas), content_type='application/x-mpegURL')

# ════════════════════════════════════════════════════════════════
#  XTREAM CODES API — Compatible con IPTV Smarters Pro
# ════════════════════════════════════════════════════════════════

def info_servidor(host):
    return {
        "url":             host,
        "port":            "443",
        "https_port":      "443",
        "server_protocol": "https",
        "rtmp_port":       "443",
        "timestamp_now":   int(datetime.utcnow().timestamp()),
        "time_now":        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "timezone":        "UTC"
    }

def info_usuario(user, usuario, contrasena):
    return {
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
    }

@app.route('/player_api.php')
def player_api():
    usuario    = request.args.get('username', '')
    contrasena = request.args.get('password', '')
    action     = request.args.get('action', '')
    host       = get_host()

    user, error = autenticar(usuario, contrasena)
    if not user:
        return jsonify({"user_info": {"auth": 0}}), 401

    # Login (sin action)
    if not action:
        return jsonify({
            "user_info":   info_usuario(user, usuario, contrasena),
            "server_info": info_servidor(host)
        })

    # Categorías de canales en vivo
    if action == 'get_live_categories':
        canales = obtener_canales()
        grupos  = {}
        for c in canales:
            g = c['grupo']
            if g not in grupos:
                grupos[g] = len(grupos) + 1
        cats = [{"category_id": str(v), "category_name": k, "parent_id": 0}
                for k, v in grupos.items()]
        return jsonify(cats)

    # Lista de canales en vivo
    if action == 'get_live_streams':
        category_id = request.args.get('category_id', '')
        canales     = obtener_canales()
        grupos      = {}
        for c in canales:
            g = c['grupo']
            if g not in grupos:
                grupos[g] = len(grupos) + 1

        resultado = []
        for c in canales:
            cat_id = str(grupos.get(c['grupo'], 1))
            if category_id and cat_id != category_id:
                continue
            resultado.append({
                "num":                   c['id'],
                "name":                  c['nombre'],
                "stream_type":           "live",
                "stream_id":             c['id'],
                "stream_icon":           c['logo'],
                "epg_channel_id":        "",
                "added":                 "0",
                "category_id":           cat_id,
                "custom_sid":            "",
                "tv_archive":            0,
                "direct_source":         "",
                "tv_archive_duration":   0
            })
        return jsonify(resultado)

    # VOD y Series (vacío — solo live por ahora)
    if action in ('get_vod_categories', 'get_vod_streams', 'get_series_categories', 'get_series'):
        return jsonify([])

    # EPG
    if action in ('get_short_epg', 'get_simple_data_table'):
        return jsonify({"epg_listings": []})

    return jsonify([])

@app.route('/get.php')
def get_php():
    """Descarga M3U completa — compatible con Smarters y otros players"""
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

    host    = get_host()
    canales = obtener_canales()
    lineas  = ['#EXTM3U']
    for c in canales:
        lineas.append(f'#EXTINF:-1 tvg-logo="{c["logo"]}" group-title="{c["grupo"]}",{c["nombre"]}')
        lineas.append(f'{host}/live/{usuario}/{contrasena}/{c["id"]}.ts')

    return Response('\n'.join(lineas), content_type='application/x-mpegURL')

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

# ════════════════════════════════════════════════════════════════
#  PANEL ADMIN — MACs
# ════════════════════════════════════════════════════════════════

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
    total     = Usuario.query.count()
    activos   = Usuario.query.filter_by(activo=True).count()
    expirados = Usuario.query.filter(Usuario.fecha_expira < datetime.utcnow()).count()
    online    = SesionActiva.query.distinct(SesionActiva.usuario_id).count()
    return jsonify({
        'total_usuarios':     total,
        'usuarios_activos':   activos,
        'usuarios_expirados': expirados,
        'online_ahora':       online
    })

# ════════════════════════════════════════════════════════════════
#  INICIO
# ════════════════════════════════════════════════════════════════

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
