#!/usr/bin/env python3
import urllib.request, json, time, concurrent.futures, re, base64

BASE = "http://localhost:5003"
with open("/tmp/v5tok_b64.txt") as fh:
    TOKEN=base64.b64decode(fh.read().strip()).decode()

def api(path, data=None, method="GET"):
    url = BASE + path
    body = json.dumps(data).encode() if data else None
    r = urllib.request.Request(url, data=body, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", "Bearer " + TOKEN)
    return json.loads(urllib.request.urlopen(r).read())

print("Token loaded: %s..." % TOKEN[:20])

# 1. Crear proveedor
print("\n=== CREANDO PROVEEDOR ===")
p = api("/api/providers", {"name":"Red4TV Premium","url":"http://red4tv.lat:80","username":"UgDwgnxyFY","password":"RZK22tgr7v","max_connections":3}, "POST")
print("Provider: id=%s name=%s" % (p.get("id"), p.get("name")))

# 2. Parsear e importar canales en lotes de 500
print("\n=== IMPORTANDO CANALES (lotes 500) ===")
with open("/tmp/red4tv.m3u", "r") as fh:
    lines = fh.readlines()

channels = []
current = {}
for line in lines:
    line = line.strip()
    if line.startswith("#EXTINF:"):
        name_match = re.search(r",(.+)$", line)
        group_match = re.search(r'group-title="([^"]*)"', line)
        logo_match = re.search(r'tvg-logo="([^"]*)"', line)
        current = {
            "name": name_match.group(1).strip() if name_match else "Unknown",
            "group": group_match.group(1) if group_match else "",
            "logo": logo_match.group(1) if logo_match else ""
        }
    elif line and not line.startswith("#") and current:
        current["url"] = line
        channels.append(current)
        current = {}

print("Total parseados: %d" % len(channels))

batch_size = 500
total = 0
t0 = time.time()

for i in range(0, len(channels), batch_size):
    batch = channels[i:i+batch_size]
    m3u_lines = ["#EXTM3U"]
    for ch in batch:
        m3u_lines.append('#EXTINF:-1 tvg-logo="%s" group-title="%s",%s' % (ch["logo"], ch["group"], ch["name"]))
        m3u_lines.append(ch["url"])
    m3u_content = "\n".join(m3u_lines)

    try:
        result = api("/api/admin/import-m3u", {"provider_id": 1, "m3u_content": m3u_content}, "POST")
        total += result.get("imported", 0)
        if i % 5000 == 0:
            print("  %d/%d: +%d = %d" % (i, len(channels), result.get("imported", 0), total))
    except Exception as e:
        print("  %d: ERROR - %s" % (i, e))

print("\nImportados: %d canales en %.1fs" % (total, time.time()-t0))

# 3. Verificar
chans = api("/api/channels")
print("Total en BD: %d" % chans["count"])

# 4. Estres: 500 usuarios
print("\n=== 500 USUARIOS ===")
def create_user(i):
    try:
        api("/api/admin/users", {"username": "user_%04d" % i, "password": "pass%d" % i, "plan": ["basico","estandar","premium"][i%3], "expires_days":30}, "POST")
        return True
    except:
        return False

t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=30) as ex:
    futs = [ex.submit(create_user, i) for i in range(1, 501)]
    ok = sum(1 for f in concurrent.futures.as_completed(futs) if f.result())
print("Creados: %d/500 en %.1fs (%.0f/s)" % (ok, time.time()-t0, ok/(time.time()-t0)))

# 5. Login masivo
print("\n=== 1000 LOGINS ===")
def login(i):
    try:
        u = "user_%04d" % (i % 500)
        p = "pass%d" % (i % 500)
        r = urllib.request.Request(BASE + "/api/auth/login",
            json.dumps({"username":u,"password":p}).encode(),
            headers={"Content-Type":"application/json"}, method="POST")
        resp = urllib.request.urlopen(r, timeout=10)
        return json.loads(resp.read()).get("token","") != ""
    except:
        return False

t0 = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=100) as ex:
    futs = [ex.submit(login, i) for i in range(1000)]
    ok = sum(1 for f in concurrent.futures.as_completed(futs) if f.result())
print("Exitosos: %d/1000 en %.1fs (%.0f/s)" % (ok, time.time()-t0, ok/(time.time()-t0)))

# 6. Dashboard
dash = api("/api/admin/dashboard")
print("\nDashboard: %s" % dash["stats"])
print("\nOK")
