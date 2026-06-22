import requests, json

BASE = "http://localhost:5000"

# Login
r = requests.post(f"{BASE}/api/auth/login", json={"username":"admin","password":"admin123"})
token = r.json().get("token","")
headers = {"Authorization": f"Bearer {token}"}

# Ver providers existentes
r = requests.get(f"{BASE}/api/providers", headers=headers)
providers = r.json().get("providers", [])
print("Providers existentes:")
for p in providers:
    print(f"  ID:{p['id']} - {p['name']} - {p['url'][:50]}")

# Usar el provider existente (ID 1) y actualizarlo
provider_id = providers[0]['id']
print(f"\nUsando provider ID: {provider_id}")

# Importar M3U al provider existente
with open("/tmp/co_channels.m3u") as f:
    m3u = f.read()

r = requests.post(f"{BASE}/api/admin/import-m3u", headers=headers, json={
    "provider_id": provider_id,
    "m3u_content": m3u
})
print(f"Import result: {r.json()}")

# Ver canales
r = requests.get(f"{BASE}/api/channels?page=1&limit=20", headers=headers)
channels = r.json().get("channels", [])
total = r.json().get("total", 0)
print(f"\nTotal canales: {total}")
print("\nPrimeros 10 canales:")
for ch in channels[:10]:
    url = ch.get("url","")
    print(f"  {ch['name'][:40]:40} | {url[:60]}...")
