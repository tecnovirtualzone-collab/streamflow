from flask import Flask, request, Response, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import requests, secrets, os, threading, subprocess, time, tempfile, shutil
from models import db, Usuario, MacRegistrada, SesionActiva, Pago, LogAcceso

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', 'streamflow-secret-2026')

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'postgresql://postgres:postgres@localhost/streamflow')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

URL_M3U_PROVEEDOR = os.environ.get('URL_M3U', '')
WA_SERVICE_URL = os.environ.get('WA_SERVICE_URL', 'http://localhost:3001')
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASSWORD', 'admin123')

# ════════════════════════════════════════════════════════════════
#  AUTENTICACION PANEL ADMIN
# ════════════════════════════════════════════════════════════════

def admin_requerido(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged'):
            if request.is_json or request.path.startswith('/admin/'):
                return jsonify({'error': 'No autorizado'}), 401
            from flask import redirect as r
            return r('/panel/login')
        return f(*args, **kwargs)
    return decorated

@app.route('/panel/login', methods=['GET'])
def panel_login():
    from flask import send_from_directory
    return send_from_directory('panel', 'login.html')

@app.route('/panel/login', methods=['POST'])
def panel_login_post():
    from flask import redirect as r
    data = request.json or {}
    if data.get('usuario') == ADMIN_USER and data.get('password') == ADMIN_PASS:
        session['admin_logged'] = True
        return jsonify({'ok': True})
    return jsonify({'error': 'Credenciales incorrectas'}), 401

@app.route('/panel/logout')
def panel_logout():
    from flask import redirect as r
    session.clear()
    return r('/panel/login')

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

# ════════════════════════════════════════════════════════════════
#  HLS RELAY — Un proceso FFmpeg por canal, compartido entre clientes
# ════════════════════════════════════════════════════════════════

HLS_DIR     = '/tmp/hls'
HLS_TIMEOUT = 60  # segundos sin viewers para apagar el relay

os.makedirs(HLS_DIR, exist_ok=True)

# { canal_id: { 'proc': subprocess, 'viewers': int, 'last_view': timestamp, 'ready': bool } }
_relays = {}
_relay_lock = threading.Lock()

def get_hls_dir(canal_id):
    d = os.path.join(HLS_DIR, str(canal_id))
    os.makedirs(d, exist_ok=True)
    return d

def start_relay(canal_id, url_proveedor):
    """Arranca FFmpeg para un canal si no está corriendo"""
    with _relay_lock:
        if canal_id in _relays and _relays[canal_id]['proc'].poll() is None:
            _relays[canal_id]['viewers'] += 1
            _relays[canal_id]['last_view'] = time.time()
            return True

        hls_path = get_hls_dir(canal_id)
        playlist = os.path.join(hls_path, 'index.m3u8')

        # Limpiar segmentos anteriores
        for f in os.listdir(hls_path):
            try: os.remove(os.path.join(hls_path, f))
            except: pass

        cmd = [
            'ffmpeg', '-y',
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
            '-user_agent', 'Mozilla/5.0',
            '-i', url_proveedor,
            '-c', 'copy',
            '-f', 'hls',
            '-hls_time', '3',
            '-hls_list_size', '10',
            '-hls_flags', 'append_list',
            '-hls_segment_filename', os.path.join(hls_path, 'seg%05d.ts'),
            playlist
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            _relays[canal_id] = {
                'proc':      proc,
                'viewers':   1,
                'last_view': time.time(),
                'ready':     False,
                'path':      hls_path,
                'playlist':  playlist
            }
            # Esperar hasta que el playlist esté listo (máx 8 segundos)
            threading.Thread(target=_mark_ready, args=(canal_id,), daemon=True).start()
            return True
        except Exception as e:
            print(f"Error arrancando FFmpeg para canal {canal_id}: {e}")
            return False

def _mark_ready(canal_id):
    playlist = _relays[canal_id]['playlist']
    for _ in range(40):  # 8 segundos max
        if os.path.exists(playlist) and os.path.getsize(playlist) > 0:
            with _relay_lock:
                if canal_id in _relays:
                    _relays[canal_id]['ready'] = True
            return
        time.sleep(0.2)

def stop_relay(canal_id):
    with _relay_lock:
        if canal_id in _relays:
            _relays[canal_id]['viewers'] = max(0, _relays[canal_id]['viewers'] - 1)

def cleanup_relays():
    """Hilo que apaga relays sin viewers"""
    while True:
        time.sleep(10)
        with _relay_lock:
            to_remove = []
            for cid, info in _relays.items():
                if info['viewers'] <= 0 and time.time() - info['last_view'] > HLS_TIMEOUT:
                    try:
                        info['proc'].terminate()
                        shutil.rmtree(info['path'], ignore_errors=True)
                    except: pass
                    to_remove.append(cid)
            for cid in to_remove:
                del _relays[cid]
                print(f"Relay canal {cid} apagado")

# Iniciar hilo de limpieza
threading.Thread(target=cleanup_relays, daemon=True).start()

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
def live_stream_hls(usuario, contrasena, canal):
    """Canal live — usa relay HLS compartido"""
    mac = request.headers.get('X-MAC-Address', '')
    ip  = request.headers.get('X-Real-IP', request.remote_addr)

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({'error': resultado}), 403

    if user_obj:
        try:
            log = LogAcceso(usuario_id=user_obj.id, canal=canal, ip=ip)
            db.session.add(log)
            db.session.commit()
        except Exception:
            pass

    base_prov, prov_user, prov_pass = get_proveedor_info()
    # Extraer solo el id del canal (sin extensión)
    canal_id = canal.replace('.ts', '').replace('.m3u8', '')
    url_proveedor = f"{base_prov}/live/{prov_user}/{prov_pass}/{canal_id}.ts"

    # Arrancar relay si no existe
    start_relay(canal_id, url_proveedor)

    # Esperar hasta 8s a que el relay esté listo
    for _ in range(40):
        info = _relays.get(canal_id)
        if info and info.get('ready'):
            break
        time.sleep(0.2)

    info = _relays.get(canal_id)
    if not info or not info.get('ready'):
        # Fallback: proxy directo si FFmpeg no arrancó
        stop_relay(canal_id)
        try:
            resp = requests.get(url_proveedor, stream=True, timeout=15,
                                headers={'User-Agent': 'Mozilla/5.0'})
            def gen_fallback():
                try:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk: yield chunk
                except: pass
            return Response(gen_fallback(),
                            content_type=resp.headers.get('Content-Type', 'video/mp2t'),
                            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        except Exception:
            return jsonify({'error': 'Error al conectar'}), 502

    # Redirigir al endpoint del playlist HLS
    from flask import redirect as redir
    host = get_host()
    return redir(f"{host}/hls/{canal_id}/index.m3u8", code=302)

@app.route('/hls/<canal_id>/<segmento>')
def serve_hls_segment(canal_id, segmento):
    """Sirve segmentos HLS del relay"""
    seg_path = os.path.join(HLS_DIR, canal_id, segmento)

    # Esperar hasta 3 segundos si el segmento aún no existe
    for _ in range(15):
        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
            break
        time.sleep(0.2)

    if not os.path.exists(seg_path):
        return '', 404

    with open(seg_path, 'rb') as f:
        data = f.read()

    # Actualizar last_view para mantener relay activo
    with _relay_lock:
        if canal_id in _relays:
            _relays[canal_id]['last_view'] = time.time()
            _relays[canal_id]['viewers'] = max(1, _relays[canal_id].get('viewers', 1))

    return Response(data, content_type='video/mp2t',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/hls/<canal_id>/index.m3u8')
def serve_hls_playlist(canal_id):
    """Sirve el playlist HLS actualizado"""
    info = _relays.get(canal_id)
    if not info:
        return '', 404

    playlist_path = info['playlist']
    if not os.path.exists(playlist_path):
        return '', 404

    with _relay_lock:
        if canal_id in _relays:
            _relays[canal_id]['last_view'] = time.time()

    with open(playlist_path, 'r') as f:
        playlist_content = f.read()

    host = get_host()
    lines = []
    for line in playlist_content.splitlines():
        if line.endswith('.ts') and not line.startswith('#'):
            lines.append(f"{host}/hls/{canal_id}/{line}")
        else:
            lines.append(line)

    return Response('\n'.join(lines),
                    content_type='application/vnd.apple.mpegurl',
                    headers={'Cache-Control': 'no-cache'})

@app.route('/movie/<usuario>/<contrasena>/<canal>')
@app.route('/series/<usuario>/<contrasena>/<canal>')
def stream_xtream(usuario, contrasena, canal):
    """Películas y series — proxy directo (cada cliente en su propio minuto)"""
    mac  = request.headers.get('X-MAC-Address', '')
    ip   = request.headers.get('X-Real-IP', request.remote_addr)
    ruta = request.path.split('/')[1]

    ok, user_obj, resultado = verificar_acceso(usuario, contrasena, mac, ip, canal)
    if not ok:
        return jsonify({'error': resultado}), 403

    if user_obj:
        try:
            log = LogAcceso(usuario_id=user_obj.id, canal=canal, ip=ip)
            db.session.add(log)
            db.session.commit()
        except Exception:
            pass

    base_prov, prov_user, prov_pass = get_proveedor_info()
    url_real = f"{base_prov}/{ruta}/{prov_user}/{prov_pass}/{canal}"

    try:
        resp = requests.get(url_real, stream=True, timeout=30,
                            headers={'User-Agent': 'Mozilla/5.0'})
        def generate():
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk: yield chunk
            except: pass
        return Response(generate(),
                        content_type=resp.headers.get('Content-Type', 'video/mp2t'),
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    except Exception:
        return jsonify({'error': 'Error al conectar'}), 502

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
@admin_requerido
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
        'macs_registradas':   len(u.macs),
        'notas':              u.notas or ''
    } for u in users])

@app.route('/admin/usuarios', methods=['POST'])
@admin_requerido
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
        fecha_expira   = datetime.utcnow() + timedelta(days=dias),
        notas          = data.get('notas', '')
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
@admin_requerido
def eliminar_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/usuarios/<int:uid>/suspender', methods=['POST'])
@admin_requerido
def suspender_usuario(uid):
    user = Usuario.query.get_or_404(uid)
    user.activo = not user.activo
    db.session.commit()
    return jsonify({'activo': user.activo})

@app.route('/admin/usuarios/<int:uid>/renovar', methods=['POST'])
@admin_requerido
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
@admin_requerido
def ver_macs(uid):
    user = Usuario.query.get_or_404(uid)
    return jsonify([{
        'id':         m.id,
        'mac':        m.mac,
        'nombre':     m.nombre,
        'registrada': m.registrada.isoformat()
    } for m in user.macs])

@app.route('/admin/macs/<int:mid>', methods=['DELETE'])
@admin_requerido
def revocar_mac(mid):
    mac = MacRegistrada.query.get_or_404(mid)
    db.session.delete(mac)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/usuarios/<int:uid>/notas', methods=['POST'])
@admin_requerido
def actualizar_notas(uid):
    user = Usuario.query.get_or_404(uid)
    user.notas = request.json.get('notas', '')
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/usuarios/<int:uid>/reset-password', methods=['POST'])
@admin_requerido
def reset_password(uid):
    user = Usuario.query.get_or_404(uid)
    pwd  = secrets.token_urlsafe(8)
    user.contrasena = generate_password_hash(pwd)
    db.session.commit()
    return jsonify({
        'usuario':  user.usuario,
        'contrasena': pwd,
        'expira':   user.fecha_expira.isoformat()
    })

# ════════════════════════════════════════════════════════════════
#  LOGS DE ACCESO
# ════════════════════════════════════════════════════════════════

@app.route('/admin/usuarios/<int:uid>/logs')
@admin_requerido
def ver_logs(uid):
    logs = LogAcceso.query.filter_by(usuario_id=uid).order_by(LogAcceso.fecha.desc()).limit(50).all()
    return jsonify([{
        'canal': l.canal,
        'ip':    l.ip,
        'fecha': l.fecha.isoformat()
    } for l in logs])

@app.route('/admin/logs')
@admin_requerido
def todos_logs():
    logs = LogAcceso.query.order_by(LogAcceso.fecha.desc()).limit(100).all()
    return jsonify([{
        'usuario': Usuario.query.get(l.usuario_id).usuario if Usuario.query.get(l.usuario_id) else '?',
        'canal':   l.canal,
        'ip':      l.ip,
        'fecha':   l.fecha.isoformat()
    } for l in logs])

# ════════════════════════════════════════════════════════════════
#  PAGOS
# ════════════════════════════════════════════════════════════════

@app.route('/admin/usuarios/<int:uid>/pagos', methods=['GET'])
@admin_requerido
def listar_pagos(uid):
    user = Usuario.query.get_or_404(uid)
    return jsonify([{
        'id':     p.id,
        'monto':  p.monto,
        'metodo': p.metodo,
        'notas':  p.notas,
        'fecha':  p.fecha.isoformat()
    } for p in sorted(user.pagos, key=lambda x: x.fecha, reverse=True)])

@app.route('/admin/usuarios/<int:uid>/pagos', methods=['POST'])
@admin_requerido
def registrar_pago(uid):
    user = Usuario.query.get_or_404(uid)
    data = request.json
    pago = Pago(
        usuario_id = uid,
        monto      = float(data.get('monto', 0)),
        metodo     = data.get('metodo', 'Efectivo'),
        notas      = data.get('notas', '')
    )
    db.session.add(pago)
    db.session.commit()
    return jsonify({'ok': True, 'id': pago.id})

@app.route('/admin/pagos/<int:pid>', methods=['DELETE'])
@admin_requerido
def eliminar_pago(pid):
    pago = Pago.query.get_or_404(pid)
    db.session.delete(pago)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/resumen-pagos')
@admin_requerido
def resumen_pagos():
    from sqlalchemy import func
    pagos = Pago.query.all()
    total = sum(p.monto for p in pagos)
    este_mes = sum(p.monto for p in pagos if p.fecha.month == datetime.utcnow().month and p.fecha.year == datetime.utcnow().year)
    # Pagos por mes (últimos 6 meses)
    from collections import defaultdict
    por_mes = defaultdict(float)
    for p in pagos:
        key = p.fecha.strftime('%Y-%m')
        por_mes[key] += p.monto
    return jsonify({
        'total': total,
        'este_mes': este_mes,
        'por_mes': dict(sorted(por_mes.items())[-6:])
    })

# ════════════════════════════════════════════════════════════════
#  ESTADÍSTICAS
# ════════════════════════════════════════════════════════════════

@app.route('/admin/stats')
@admin_requerido
def stats():
    limpiar_sesiones()
    return jsonify({
        'total_usuarios':     Usuario.query.count(),
        'usuarios_activos':   Usuario.query.filter_by(activo=True).count(),
        'usuarios_expirados': Usuario.query.filter(Usuario.fecha_expira < datetime.utcnow()).count(),
        'online_ahora':       SesionActiva.query.distinct(SesionActiva.usuario_id).count(),
        'ingresos_mes':       sum(p.monto for p in Pago.query.all() if p.fecha.month == datetime.utcnow().month and p.fecha.year == datetime.utcnow().year)
    })

# ════════════════════════════════════════════════════════════════
#  INICIO
# ════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════
#  PANEL ADMIN — FRONTEND (auth defined above)

# ════════════════════════════════════════════════════════════════
#  WHATSAPP
# ════════════════════════════════════════════════════════════════

@app.route('/admin/wa/status')
@admin_requerido
def wa_status():
    try:
        resp = requests.get(f"{WA_SERVICE_URL}/status", timeout=5)
        return jsonify(resp.json())
    except Exception:
        return jsonify({'ready': False, 'hasQr': False, 'error': 'Servicio WA no disponible'})

@app.route('/admin/wa/send', methods=['POST'])
@admin_requerido
def wa_send():
    data = request.json
    try:
        resp = requests.post(f"{WA_SERVICE_URL}/send", json=data, timeout=10)
        return jsonify(resp.json())
    except Exception:
        return jsonify({'ok': False, 'error': 'Error al enviar'})

@app.route('/admin/wa/notify-expiring', methods=['POST'])
@admin_requerido
def wa_notify_expiring():
    dias = int(request.json.get('dias', 3))
    try:
        users = Usuario.query.filter(
            Usuario.activo == True,
            Usuario.notas != '',
            Usuario.notas != None,
            Usuario.fecha_expira > datetime.utcnow(),
            Usuario.fecha_expira <= datetime.utcnow() + timedelta(days=dias)
        ).all()
        enviados = 0
        for u in users:
            dias_rest = (u.fecha_expira - datetime.utcnow()).days + 1
            mensaje = (
                "\u26a0\ufe0f *\u00a1Tu suscripci\u00f3n StreamFlow vence pronto!*\n\n"
                + f"Hola *{u.usuario}*, tu acceso vence en *{dias_rest} d\u00eda" + ("s" if dias_rest != 1 else "") + "*.\n\n"
                + "Para renovar, cont\u00e1ctanos \U0001f447\n"
                + "_Soporte v\u00eda WhatsApp_ \U0001f4ac"
            )
            try:
                resp = requests.post(f"{WA_SERVICE_URL}/send",
                    json={'phone': u.notas, 'message': mensaje}, timeout=10)
                if resp.json().get('ok'):
                    enviados += 1
            except Exception:
                pass
        return jsonify({'ok': True, 'enviados': enviados, 'total': len(users)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ════════════════════════════════════════════════════════════════
#  PANEL ADMIN — FRONTEND
# ════════════════════════════════════════════════════════════════

@app.route('/wa/qr')
def wa_qr():
    try:
        resp = requests.get(f"{WA_SERVICE_URL}/qr", timeout=10)
        return Response(resp.content, content_type=resp.headers.get('Content-Type', 'text/html'))
    except Exception:
        return "<h2 style='font-family:sans-serif;color:#ef4444;padding:40px'>Servicio WhatsApp iniciando... espera unos segundos y recarga.</h2>", 503

@app.route('/panel/')
@app.route('/panel')
@admin_requerido
def panel():
    from flask import send_from_directory
    return send_from_directory('panel', 'index.html')

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
