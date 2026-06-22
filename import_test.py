import requests, json, re, sqlite3

BASE = "http://localhost:5000"
DB_PATH = "/root/streamflow/data/streamflow.db"

# Login
r = requests.post(f"{BASE}/api/auth/login", json={"username":"admin","password":"admin123"})
token = r.json().get("token","")
headers = {"Authorization": f"Bearer {token}"}
print("Login OK")

# Leer M3U
with open("/tmp/red4tv_p80.m3u", "r", errors="ignore") as f:
    content = f.read()

# Parsear canales
pattern = r'#EXTINF:.*?\s*,\s*(.*?)\n(.*?)\n'
matches = re.findall(pattern, content, re.DOTALL)
print(f"Total canales en M3U: {len(matches)}")

# Buscar canales colombianos
co_channels = []
for name, url in matches:
    name = name.strip()
    url = url.strip()
    if any(kw in name.lower() for kw in ['caracol', 'rcn', 'canal 1', 'citytv', 'señal', 'colombia', 'win', 'espn', 'fox', 'discovery', 'history', 'national', 'cartoon', 'nick', 'disney', 'hbo', 'cinemax', 'star', 'universal', 'sony', 'warner', 'paramount', 'mtv', 'vh1', 'e!']):
        co_channels.append((name, url))

print(f"\nCanales colombianos/relevantes encontrados: {len(co_channels)}")
for name, url in co_channels[:20]:
    print(f"  {name[:50]:50} | {url[:60]}...")

# Importar solo estos canales de prueba
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("SELECT id FROM providers LIMIT 1")
provider_id = c.fetchone()[0]

imported = 0
for name, url in co_channels:
    try:
        c.execute(
            "INSERT OR IGNORE INTO channels (name, url, provider_id, group_name) VALUES (?, ?, ?, ?)",
            (name, url, provider_id, "Red4TV")
        )
        imported += 1
    except:
        pass

conn.commit()

# Verificar
c.execute("SELECT COUNT(*) FROM channels")
total = c.fetchone()[0]
print(f"\n✅ Canales importados: {imported}")
print(f"📊 Total en DB: {total}")

# Mostrar algunos con URL
c.execute("SELECT name, url, group_name FROM channels WHERE url != '' LIMIT 10")
print("\nCanales con URL para VLC:")
for row in c.fetchall():
    print(f"  {row[0][:40]:40} | {row[1][:60]}")

conn.close()
