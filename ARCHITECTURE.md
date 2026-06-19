# 🌊 StreamFlow v3.0 — Plan de Arquitectura
## Sistema IPTV Proxy con VLC Relay para máxima sigilosidad

---

## 📋 ADR-001: Arquitectura de Relay con VLC

### Status: Proposed

### Contexto
- StreamFlow v2.0 usa FFmpeg directamente para hacer relay HLS
- Problema: Cada canal diferente abre una conexión al proveedor IPTV
- Con 100-200 usuarios viendo canales diferentes = muchas conexiones al proveedor
- El proveedor detecta múltiples conexiones y bloquea
- Necesitamos que **el proveedor solo vea 1 conexión por canal**, sin importar cuántos usuarios

### Decisión
Usar **VLC como relay central** + **proxy HTTP local** en StreamFlow

### Consecuencias
- ✅ Proveedor solo ve 1 conexión por canal
- ✅ VLC maneja reconexión automática (más estable que FFmpeg directo)
- ✅ Buffer estable para todos los usuarios
- ✅ Fácil de debuggear (podés ver el stream en VLC)
- ⚠️ Consume más RAM (VLC usa ~50-100MB por canal)
- ⚠️ Necesitamos gestionar procesos VLC dinámicamente

---

## 🏗️ Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                        VPS (8GB RAM, 2 CPU)                     │
│                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐    │
│  │  Proveedor   │     │  VLC Relay   │     │  StreamFlow  │    │
│  │  IPTV        │────▶│  (1 conex/   │────▶│  (Proxy      │    │
│  │              │     │   canal)     │     │   Local)     │    │
│  └──────────────┘     └──────────────┘     └──────────────┘    │
│                              │                    │             │
│                              │ HTTP/HLS           │             │
│                              │ localhost:8888     │             │
│                                                ┌──┴──┐          │
│                                                │Users│          │
│                                                │100- │          │
│                                                │200  │          │
│                                                └─────┘          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📊 Capacidad del Servidor (8GB RAM, 2 CPU)

### Recursos por componente:

| Componente | RAM | CPU | Notas |
|-----------|-----|-----|-------|
| StreamFlow (Flask/Gunicorn) | ~200MB | 0.2 | 4 workers gevent |
| VLC por canal activo | ~80MB | 0.3 | Con caché de 3s |
| PostgreSQL | ~100MB | 0.1 | Pool de 20 conexiones |
| Sistema + Docker | ~500MB | 0.1 | Base |
| **Total base** | **~800MB** | **0.7** | Sin canales |
| **Por canal VLC** | **+80MB** | **+0.3** | |

### Capacidad máxima:
- **RAM disponible para VLC**: 8GB - 800MB = ~7.2GB
- **Canales simultáneos (RAM)**: 7.2GB / 80MB = **~90 canales**
- **CPU disponible**: 2 cores - 0.7 = 1.3 cores
- **Canales simultáneos (CPU)**: 1.3 / 0.3 = **~4 canales**

### ⚠️ Cuello de botella: CPU
Con 2 CPUs, solo podemos tener **4-5 canales VLC activos simultáneamente**.

### 💡 Solución: Optimización de VLC
- Usar `--no-video --aout=none` (solo audio no, necesitamos video)
- User `--live-caching=1000` (1 segundo de buffer)
- Usar `--network-caching=1000`
- Transcodificar a resolución baja: `--sout-x264-preset=ultrafast`
- **Objetivo: Reducir CPU por canal a 0.1-0.15**

### Capacidad optimizada:
- **Canales simultáneos (CPU optimizado)**: 1.3 / 0.15 = **~8 canales**
- **Usuarios por canal**: 100-200 usuarios / 8 canales = **12-25 usuarios por canal**

---

## 🔄 Flujo de conexión de un usuario

```
1. Usuario abre stream → StreamFlow
2. StreamFlow verifica auth + límites
3. StreamFlow verifica si el canal ya tiene VLC activo
   ├── Sí → Retorna URL local del VLC (http://localhost:8888/canal123.m3u8)
   └── No → Inicia VLC para ese canal → Retorna URL local
4. Usuario recibe stream desde VLC local
5. Cuando el último usuario desconecta → VLC se apaga (timeout 60s)
```

---

## 🛡️ Estrategia anti-detección

### Nivel 1: Conexiones al proveedor
- **Solo 1 conexión por canal** (VLC relay)
- **Rotación de User-Agent** entre conexiones
- **Reconexión inteligente** — Si VLC pierde conexión, espera 5-15s antes de reconectar
- **Rate limiting de reconexión** — Máximo 3 reconexiones por minuto por canal

### Nivel 2: Patrones de acceso
- **Staggered connections** — No conectar todos los canales al mismo tiempo
- **Keep-alive** — Mantener conexiones vivas con pings periódicos
- **IP fija** — El proveedor siempre ve la misma IP del VPS

### Nivel 3: Resiliencia
- **Auto-restart** — Si VLC cae, se reinicia automáticamente
- **Health checks** — Verificar que el stream esté vivo cada 30s
- **Failover** — Si un canal falla, notificar al admin

---

## 📐 Diseño técnico

### Componente: VLC Manager (Python)
```python
class VLCRelayManager:
    """
    Gestiona procesos VLC para cada canal activo.
    - Inicia VLC cuando el primer usuario pide un canal
    - Comparte el stream entre todos los usuarios de ese canal
    - Apaga VLC cuando el último usuario se va (timeout 60s)
    - Monitorea health de cada relay
    """
    
    def start_relay(self, canal_id: str, url_proveedor: str) -> str:
        """
        Inicia VLC para un canal.
        Retorna URL local: http://localhost:8888/{canal_id}.m3u8
        """
        pass
    
    def stop_relay(self, canal_id: str):
        """Apaga VLC para un canal."""
        pass
    
    def get_local_url(self, canal_id: str) -> str:
        """Retorna URL local del stream."""
        pass
```

### Comando VLC optimizado:
```bash
vlc --intf dummy \
    --no-video-title-show \
    --no-sout-audio \
    --sout-keep \
    --live-caching=1000 \
    --network-caching=1000 \
    --sout-x264-preset=ultrafast \
    --sout-x264-tune=zerolatency \
    --sout-x264-profile=baseline \
    --sout "#standard{access=http,mux=ts,dst=:8888/{canal_id}.ts}" \
    {url_proveedor} &
```

### Componente: StreamFlow Proxy
```python
@app.route('/live/<user>/<pass>/<canal>')
def stream_proxy(user, password, canal):
    # 1. Verificar auth
    # 2. Verificar que VLC esté corriendo para ese canal
    # 3. Hacer proxy del stream local de VLC
    # 4. Contar viewers
    # 5. Cuando viewer se va, decrementar contador
    pass
```

---

## 📊 Estimación de ingresos

### Modelo de negocio:
| Paquete | Precio/mes | Usuarios estimados |
|---------|-----------|-------------------|
| Básico (1 pantalla) | $3 | 40 |
| Premium (2 pantallas) | $5 | 30 |
| Familiar (3 pantallas) | $7 | 20 |
| **Total** | | **90 usuarios** |

### Ingresos mensuales estimados:
- 40 × $3 = $120
- 30 × $5 = $150
- 20 × $7 = $140
- **Total: $410/mes**

### Costos:
- VPS 8GB/2CPU: ~$20-30/mes
- Proveedor IPTV: ~$10-20/mes
- **Ganancia neta: ~$360-380/mes**

### Escalabilidad:
- Con 200 usuarios: ~$800-900/mes
- Con 2 VPS: ~$1,500-1,800/mes

---

## 🚀 Plan de implementación

### Fase 1: VLC Relay Manager (2-3 horas)
- [ ] Crear clase VLCRelayManager
- [ ] Implementar start/stop de VLC por canal
- [ ] Contador de viewers por canal
- [ ] Timeout automático (60s sin viewers)
- [ ] Health checks

### Fase 2: Integración con StreamFlow (1-2 horas)
- [ ] Modificar endpoint /live/ para usar VLC relay
- [ ] Proxy HTTP desde VLC local
- [ ] Manejo de errores y reconexión

### Fase 3: Optimización (1-2 horas)
- [ ] Ajustar parámetros de VLC para minimizar CPU
- [ ] Configurar caché óptimo
- [ ] Pruebas de carga con múltiples canales

### Fase 4: Monitoreo (1 hora)
- [ ] Endpoint /admin/vlc-status
- [ ] Logs de conexiones VLC
- [ ] Alertas de canales caídos

### Fase 5: Despliegue y pruebas (1-2 horas)
- [ ] Actualizar Dockerfile
- [ ] Desplegar en EasyPanel
- [ ] Pruebas con usuarios reales

---

## ⚠️ Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
|--------|---------|-----------|
| VLC consume mucha CPU | Alto | Optimizar parámetros, limitar canales |
| Proveedor detecta proxy | Medio | Rotación UA, reconexión inteligente |
| VLC se cae | Medio | Auto-restart, health checks |
| Memoria insuficiente | Medio | Limitar canales activos, swap |
| Latencia alta | Bajo | Caché bajo, red local |

---

**Creado por:** Luna 🌙 para Alejo
**Fecha:** 2026-06-19
**Versión:** 3.0-draft
