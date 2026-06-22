import requests, json, re, sqlite3, os

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

# Parsear canales del M3U
pattern = r'#EXTINF:.*?\s*,\s*(.*?)\n(.*?)\n'
matches = re.findall(pattern, content, re.DOTALL)
print(f"Canales encontrados en M3U: {len(matches)}")

# Conectar a SQLite directamente
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Obtener provider_id
c.execute("SELECT id FROM providers LIMIT 1")
provider_id = c.fetchone()[0]
print(f"Provider ID: {provider_id}")

# Importar en lotes de 500
batch_size = 500
total_imported = 0
errors = 0

for i in range(0, len(matches), batch_size):
    batch = matches[i:i+batch_size]
    for name, url in batch:
        name = name.strip()
        url = url.strip()
        if not name or not url:
            continue
        try:
            c.execute(
                "INSERT OR IGNORE INTO channels (name, url, provider_id, group_name) VALUES (?, ?, ?, ?)",
                (name, url, provider_id, "Red4TV")
            )
            total_imported += 1
        except Exception as e:
            errors += 1
    
    conn.commit()
    if (i // batch_size) % 10 == 0:
        print(f"  Progreso: {i}/{len(matches)} procesados, {total_imported} importados")

conn.close()
print(f"\n✅ Importación completada!")
print(f"   Total importados: {total_imported}")
print(f"   Errores: {errors}")
print(f"   Lotes: {len(matches) // batch_size + 1}")
