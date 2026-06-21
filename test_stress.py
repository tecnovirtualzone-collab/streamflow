#!/usr/bin/env python3
import urllib.request, json, time, concurrent.futures, re

BASE = "http://localhost:5003"
TOKEN=open('/tmp/v5tok.txt').read().strip()
print(f"Token: {TOKEN[:30]}...")

# 1. Parsear M3U
print("\n=== PARSEANDO M3U ===")
with open('/tmp/red4tv.m3u', 'r') as f:
    content = f.read()

lines = content.split('\n')
print(f"Total lineas: {len(lines)}")

channels = []
current_name = ""
current_group = ""
current_logo = ""

for line in lines:
    line = line.strip()
    if line.startswith('#EXTINF:'):
        name_match = re.search(r',(.+)$', line)
        group_match = re.search(r'group-title="([^"]*)"', line)
        logo_match = re.search(r'tvg-logo="([^"]*)"', line)
        current_name = name_match.group(1).strip() if name_match else "Unknown"
        current_group = group_match.group(1) if group_match else ""
        current_logo = logo_match.group(1) if logo_match else ""
    elif line and not line.startswith('#') and current_name:
        channels.append({'name': current_name, 'group': current_group, 'logo': current_logo, 'url': line})
        current_name = ""

print(f"Canales parseados: {len(channels)}")

# 2. Importar en lotes de 500
batch_size = 500
batches = [channels[i:i+batch_size] for i in range(0, len(channels), batch_size)]
print(f"Lotes: {len(batches)}")

total_imported = 0
start = time.time()

for i, batch in enumerate(batches):
    m3u_lines = ['#EXTM3U']
    for ch in batch:
        m3u_lines.append(f'#EXTINF:-1 tvg-logo="{ch["logo"]}" group-title="{ch["group"]}",{ch["name"]}')
        m3u_lines.append(ch['url'])
    m3u_content = '\n'.join(m3u_lines)

    try:
        payload = json.dumps({'provider_id': 1, 'm3u_content': m3u_content}).encode()
        req = urllib.request.Request(f'{BASE}/api/admin/import-m3u',
            data=payload,
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {TOKEN}'},
            method='POST')
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        total_imported += result.get('imported', 0)
        print(f"  Lote {i+1}/{len(batches)}: +{result.get('imported', 0)} = {total_imported}")
    except Exception as e:
        print(f"  Lote {i+1}: ERROR - {e}")

elapsed = time.time() - start
print(f"\nImportados: {total_imported} canales en {elapsed:.1f}s")

# 3. Verificar
req = urllib.request.Request(f'{BASE}/api/channels', headers={'Authorization': f'Bearer {TOKEN}'})
resp = urllib.request.urlopen(req)
chans = json.loads(resp.read())
print(f"Total en BD: {chans['count']}")

# 4. Crear 200 usuarios
print("\n=== 200 USUARIOS ===")
def create_user(i):
    try:
        payload = json.dumps({'username': f'user_{i:04d}', 'password': f'pass{i}', 'plan': ['basico','estandar','premium'][i%3], 'expires_days':30}).encode()
        req = urllib.request.Request(f'{BASE}/api/admin/users', data=payload, headers={'Content-Type':'application/json','Authorization':f'Bearer {TOKEN}'}, method='POST')
        urllib.request.urlopen(req, timeout=10)
        return True
    except:
        return False

start = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
    futs = [ex.submit(create_user, i) for i in range(1, 201)]
    ok = sum(1 for f in concurrent.futures.as_completed(futs) if f.result())
print(f"Creados: {ok}/200 en {time.time()-start:.1f}s")

# 5. Login masivo
print("\n=== 500 LOGINS ===")
def login(i):
    try:
        u = f'user_{i%200:04d}'
        p = f'pass{i%200}'
        payload = json.dumps({'username':u,'password':p}).encode()
        req = urllib.request.Request(f'{BASE}/api/auth/login', data=payload, headers={'Content-Type':'application/json'}, method='POST')
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read()).get('token','') != ''
    except:
        return False

start = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
    futs = [ex.submit(login, i) for i in range(500)]
    ok = sum(1 for f in concurrent.futures.as_completed(futs) if f.result())
print(f"Exitosos: {ok}/500 en {time.time()-start:.1f}s ({ok/(time.time()-start):.0f} req/s)")

# 6. Dashboard
req = urllib.request.Request(f'{BASE}/api/admin/dashboard', headers={'Authorization':f'Bearer {TOKEN}'})
resp = urllib.request.urlopen(req)
dash = json.loads(resp.read())
print(f"\nDashboard: {dash['stats']}")
print("\nOK")
