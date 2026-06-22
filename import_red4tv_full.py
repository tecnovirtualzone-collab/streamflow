import sqlite3, re

DB_PATH = "/tmp/streamflow_copy.sqlite"
M3U_PATH = "/tmp/red4tv_p80.m3u"

# Copiar DB del container
import subprocess
result = subprocess.run(
    ["docker", "cp", "streamflow:/data/streamflow.sqlite", DB_PATH],
    capture_output=True, text=True
)
print(f"DB copiada: {result.returncode}")

# Leer M3U
with open(M3U_PATH, "r", errors="ignore") as f:
    content = f.read()

# Parsear canales
pattern = r'#EXTINF:.*?\s*,\s*(.*?)\n(.*?)\n'
matches = re.findall(pattern, content, re.DOTALL)
print(f"Canales en M3U: {len(matches)}")

# Conectar a la DB
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Ver estructura
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print(f"Tablas: {tables}")

# Ver provider
c.execute("SELECT id, name FROM providers")
providers = c.fetchall()
print(f"Providers: {providers}")

provider_id = providers[0][0] if providers else 1

# Ver estructura de channels
c.execute("PRAGMA table_info(channels)")
cols = c.fetchall()
print(f"Columnas channels: {[col[1] for col in cols]}")

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
    if (i // batch_size) % 20 == 0:
        print(f"  Progreso: {i}/{len(matches)} - importados: {total_imported}")

# Verificar
c.execute("SELECT COUNT(*) FROM channels")
total = c.fetchone()[0]
print(f"\n✅ Importación completada!")
print(f"   Importados: {total_imported}")
print(f"   Errores: {errors}")
print(f"   Total en DB: {total}")

# Mostrar algunos canales
c.execute("SELECT name, url FROM channels WHERE url != '' LIMIT 10")
print("\nPrimeros 10 canales:")
for row in c.fetchall():
    print(f"  {row[0][:45]:45} | {row[1][:60]}")

conn.close()

# Copiar DB de vuelta al container
print("\nCopiando DB de vuelta al container...")
result2 = subprocess.run(
    ["docker", "cp", DB_PATH, "streamflow:/data/streamflow.sqlite"],
    capture_output=True, text=True
)
print(f"Resultado: {result2.returncode}")
