import requests, json, sys

BASE = "http://localhost:5000"

# Login
r = requests.post(f"{BASE}/api/auth/login", json={"username":"admin","password":"admin123"})
token = r.json().get("token","")
print(f"Login: {r.json().get('message','')}")
headers = {"Authorization": f"Bearer {token}"}

# Crear provider
r = requests.post(f"{BASE}/api/providers", headers=headers, json={
    "name": "IPTV Public CO",
    "url": "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/co.m3u",
    "max_connections": 3,
    "is_active": 1
})
print(f"Provider: {r.json()}")

# Importar M3U
with open("/tmp/co_channels.m3u") as f:
    m3u = f.read()

r = requests.post(f"{BASE}/api/admin/import-m3u", headers=headers, json={
    "provider_id": 2,
    "m3u_content": m3u
})
print(f"Import: {r.json()}")

# Ver canales importados
r = requests.get(f"{BASE}/api/channels?page=1&limit=10", headers=headers)
channels = r.json().get("channels", [])
print(f"\nCanales importados: {len(channels)}")
for ch in channels[:5]:
    print(f"  - {ch['name']} ({ch.get('group_name','')})")

# Guardar URLs para VLC
r = requests.get(f"{BASE}/api/channels?page=1&limit=20", headers=headers)
channels = r.json().get("channels", [])
with open("/tmp/stream_urls.txt", "w") as f:
    for ch in channels:
        if ch.get("url"):
            f.write(f"{ch['name']}|{ch['url']}\n")

print("\nURLs guardadas en /tmp/stream_urls.txt")
