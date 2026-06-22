import requests, json, subprocess

BASE = "http://localhost:5000"

# Login
r = requests.post(f"{BASE}/api/auth/login", json={"username":"admin","password":"admin123"})
token = r.json().get("token","")
headers = {"Authorization": f"Bearer {token}"}
print(f"Login OK")

# Descargar M3U del proveedor real
print("Descargando M3U de Red4TV...")
result = subprocess.run([
    "wget", "-q", "-O", "/tmp/red4tv_full.m3u",
    "http://red4tv.lat:8000/get.php?username=UgDwgnxyFY&password=RZK22tgr7v&type=m3u_plus&output=ts"
], capture_output=True, text=True, timeout=60)

if result.returncode != 0:
    print(f"Error descargando: {result.stderr}")
    # Intentar con curl
    result2 = subprocess.run([
        "curl", "-s", "-o", "/tmp/red4tv_full.m3u", "--connect-timeout", "30",
        "http://red4tv.lat:8000/get.php?username=UgDwgnxyFY&password=RZK22tgr7v&type=m3u_plus&output=ts"
    ], capture_output=True, text=True, timeout=60)
    print(f"Curl result: {result2.returncode}")

# Verificar archivo
import os
size = os.path.getsize("/tmp/red4tv_full.m3u") if os.path.exists("/tmp/red4tv_full.m3u") else 0
print(f"M3U tamaño: {size} bytes")

if size > 100:
    with open("/tmp/red4tv_full.m3u") as f:
        lines = f.readlines()
    print(f"M3U líneas: {len(lines)}")
    print("Primeras 5 líneas:")
    for l in lines[:5]:
        print(f"  {l.strip()}")
    
    # Contar canales
    channel_count = sum(1 for l in lines if l.startswith("#EXTINF"))
    print(f"Canales encontrados: {channel_count}")
else:
    print("ERROR: M3U vacío o muy pequeño")
    # Probar conexión directa
    import urllib.request
    try:
        req = urllib.request.urlopen("http://red4tv.lat:8000/get.php?username=UgDwgnxyFY&password=RZK22tgr7v&type=m3u_plus&output=ts", timeout=30)
        data = req.read()
        print(f"Descarga directa: {len(data)} bytes")
        with open("/tmp/red4tv_full.m3u", "wb") as f:
            f.write(data)
    except Exception as e:
        print(f"Error descarga directa: {e}")
