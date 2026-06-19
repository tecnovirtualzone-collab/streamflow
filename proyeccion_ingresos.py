#!/usr/bin/env python3
"""
Proyección de ingresos - StreamFlow IPTV Colombia
"""
import json

print("=" * 70)
print("💰 PROYECCIÓN DE INGRESOS - StreamFlow IPTV Colombia")
print("=" * 70)

# ═══════════════════════════════════════════════════
# PARÁMETROS BASE
# ═══════════════════════════════════════════════════
PRECIO_MENSUAL = 10000  # COP
COSTO_VPS = 8 * 7500  # $8 USD ≈ $30,000 COP (tipo de cambio ~3,750)
COSTO_PROVEEDOR = 50000  # COP/mes (estimado proveedor IPTV)
COSTO_DOMINIO = 5000  # COP/mes
COSTO_TOTAL_FIJO = COSTO_VPS + COSTO_PROVEEDOR + COSTO_DOMINIO

print(f"""
  PARÁMETROS:
  ───────────
  Precio suscripción:    ${PRECIO_MENSUAL:>10,.0f} COP/mes
  Costo VPS (8GB/2CPU):  ${COSTO_VPS:>10,.0f} COP/mes
  Costo proveedor IPTV:  ${COSTO_PROVEEDOR:>10,.0f} COP/mes
  Costo dominio + SSL:   ${COSTO_DOMINIO:>10,.0f} COP/mes
  ─────────────────────────────────────────
  Costo fijo total:      ${COSTO_TOTAL_FIJO:>10,.0f} COP/mes
""")

# ═══════════════════════════════════════════════════
# ESCALAS DE USUARIOS
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"PROYECCIÓN POR CANTIDAD DE USUARIOS")
print(f"{'=' * 70}")

usuarios_list = [10, 25, 50, 75, 100, 150, 200, 300, 500]

print(f"\n  {'Usuarios':>10} │ {'Ingresos':>14} │ {'Costos':>14} │ {'Ganancia':>14} │ {'Margen':>8}")
print(f"  {'─' * 10}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 8}")

for users in usuarios_list:
    ingresos = users * PRECIO_MENSUAL
    costos = COSTO_TOTAL_FIJO
    ganancia = ingresos - costos
    margen = (ganancia / ingresos * 100) if ingresos > 0 else 0
    
    # Determinar si necesita más VPS
    vps_necesarios = max(1, (users + 199) // 200)  # 200 usuarios por VPS
    costo_vps_total = vps_necesarios * COSTO_VPS
    costos_reales = COSTO_PROVEEDOR + COSTO_DOMINIO + costo_vps_total
    ganancia_real = ingresos - costos_reales
    
    emoji = "✅" if ganancia_real > 0 else "❌"
    print(f"  {users:>10} │ ${ingresos:>12,.0f} │ ${costos_reales:>12,.0f} │ ${ganancia_real:>12,.0f} │ {margen:>6.0f}% {emoji}")

# ═══════════════════════════════════════════════════
# CASO ESPECÍFICO: 50 USUARIOS
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📊 CASO ESPECÍFICO: 50 USUARIOS")
print(f"{'=' * 70}")

users_50 = 50
ingresos_50 = users_50 * PRECIO_MENSUAL
ganancia_50 = ingresos_50 - COSTO_TOTAL_FIJO

print(f"""
  INGRESOS:
    50 usuarios × $10,000 COP = ${ingresos_50:,} COP/mes
    En USD: ~${ingresos_50/3750:.0f} USD/mes

  COSTOS:
    VPS (8GB/2CPU):     ${COSTO_VPS:,} COP
    Proveedor IPTV:     ${COSTO_PROVEEDOR:,} COP
    Dominio + SSL:      ${COSTO_DOMINIO:,} COP
    ─────────────────────────────────
    Total costos:       ${COSTO_TOTAL_FIJO:,} COP/mes

  GANANCIA NETA:
    ${ganancia_50:,} COP/mes
    En USD: ~${ganancia_50/3750:.0f} USD/mes

  ─────────────────────────────────
  💰 Con 50 usuarios ganás ${ganancia_50:,} COP/mes (~${ganancia_50/3750:.0f} USD)
""")

# ═══════════════════════════════════════════════════
# PUNTO DE EQUILIBRIO
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"⚖️ PUNTO DE EQUILIBRIO")
print(f"{'=' * 70}")

punto_equilibrio = COSTO_TOTAL_FIJO / PRECIO_MENSUAL
print(f"""
  Costos fijos: ${COSTO_TOTAL_FIJO:,} COP/mes
  Precio: ${PRECIO_MENSUAL:,} COP/usuario/mes

  Punto de equilibrio: {punto_equilibrio:.1f} usuarios
  → Con {int(punto_equilibrio) + 1} usuarios ya estás ganando dinero
  → Cada usuario después de eso es ganancia pura
""")

# ═══════════════════════════════════════════════════
# PROYECCIÓN A 6 MESES
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📈 PROYECCIÓN A 6 MESES (crecimiento realista)")
print(f"{'=' * 70}")

# Crecimiento realista mes a mes
crecimiento = {
    1: 5,    # Mes 1: 5 usuarios (amigos/familia)
    2: 15,   # Mes 2: 15 usuarios
    3: 30,   # Mes 3: 30 usuarios
    4: 50,   # Mes 4: 50 usuarios
    5: 80,   # Mes 5: 80 usuarios
    6: 120,  # Mes 6: 120 usuarios
}

print(f"\n  {'Mes':>5} │ {'Usuarios':>10} │ {'Ingresos':>14} │ {'Costos':>14} │ {'Ganancia':>14} │ {'Acumulado':>14}")
print(f"  {'─' * 5}─┼─{'─' * 10}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 14}")

acumulado = 0
for mes, users in crecimiento.items():
    ingresos = users * PRECIO_MENSUAL
    vps = max(1, (users + 199) // 200)
    costos = COSTO_PROVEEDOR + COSTO_DOMINIO + (vps * COSTO_VPS)
    ganancia = ingresos - costos
    acumulado += ganancia
    
    print(f"  {mes:>5} │ {users:>10} │ ${ingresos:>12,.0f} │ ${costos:>12,.0f} │ ${ganancia:>12,.0f} │ ${acumulado:>12,.0f}")

print(f"\n  💰 En 6 meses: {crecimiento[6]} usuarios, ${acumulado:,} COP acumulados (~${acumulado/3750:.0f} USD)")

# ═══════════════════════════════════════════════════
# COMPARACIÓN DE PRECIOS
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"💡 COMPARACIÓN: ¿Cuánto cobra la competencia?")
print(f"{'=' * 70}")

competencia = [
    ("StreamFlow (tu precio)", 10000),
    ("IPTV barato (mercado gris)", 15000),
    ("IPTV medio", 25000),
    ("IPTV premium", 35000),
    ("Tigo TV (legal)", 45000),
    ("Claro TV (legal)", 55000),
    ("DirecTV (legal)", 80000),
]

print(f"\n  {'Servicio':<30} │ {'Precio/mes':>12} │ {'vs tu precio':>14}")
print(f"  {'─' * 30}─┼─{'─' * 12}─┼─{'─' * 14}")

for nombre, precio in competencia:
    diff = ((precio - PRECIO_MENSUAL) / PRECIO_MENSUAL) * 100
    if diff > 0:
        comparacion = f"{diff:.0f}% más caro"
    elif diff < 0:
        comparacion = f"{-diff:.0f}% más barato"
    else:
        comparacion = "Tu precio"
    print(f"  {nombre:<30} │ ${precio:>10,.0f} │ {comparacion:>14}")

# ═══════════════════════════════════════════════════
# ESTRATEGIA DE PRECIOS
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"🎯 ESTRATEGIA DE PRECIOS SUGERIDA")
print(f"{'=' * 70}")

print(f"""
  PAQUETE BÁSICO - $10,000 COP/mes
  ─────────────────────────────────
  • TV Colombia (RCN, Caracol, Canal 13, etc.)
  • Noticias (CNN, DW, RCN Noticias)
  • Kids (Disney, Cartoon, Nickelodeon)
  • 1 conexión simultánea
  
  PAQUETE PREMIUM - $18,000 COP/mes
  ─────────────────────────────────
  • Todo lo del Básico
  • Deportes (Win Sports, ESPN, Fox Sports)
  • Cine (HBO, Cinemax, Star Channel)
  • 2 conexiones simultáneas
  
  PAQUETE FAMILIAR - $25,000 COP/mes
  ─────────────────────────────────
  • Todo lo del Premium
  • Eventos PPV (peleas, fútbol premium)
  • 4 conexiones simultáneas
  • Soporte prioritario
  
  CON PAQUETES MIXTOS:
  ────────────────────
  Si tenés 50 usuarios:
    20 Básico  × $10,000 = $200,000
    20 Premium × $18,000 = $360,000
    10 Familiar× $25,000 = $250,000
    ─────────────────────────────────
    Total: $810,000 COP/mes (~$216 USD)
    Costos: ~$85,000 COP
    GANANCIA: ~$725,000 COP (~$193 USD/mes)
""")

# ═══════════════════════════════════════════════════
# RESUMEN FINAL
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📋 RESUMEN FINAL")
print(f"{'=' * 70}")

print(f"""
  CON 50 USUARIOS A $10,000 COP:
  ──────────────────────────────
  Ingresos:    $500,000 COP/mes (~$133 USD)
  Costos:      ~$85,000 COP/mes (~$23 USD)
  Ganancia:    ~$415,000 COP/mes (~$111 USD)
  
  CON 50 USUARIOS EN PAQUETES MIXTOS:
  ──────────────────────────────────
  Ingresos:    ~$810,000 COP/mes (~$216 USD)
  Costos:      ~$85,000 COP/mes (~$23 USD)
  Ganancia:    ~$725,000 COP/mes (~$193 USD)
  
  CON 200 USUARIOS (CAPACIDAD MÁXIMA VPS):
  ────────────────────────────────────────
  Ingresos:    ~$3,200,000 COP/mes (~$853 USD)
  Costos:      ~$115,000 COP/mes (~$31 USD)
  Ganancia:    ~$3,085,000 COP/mes (~$823 USD)
  
  ────────────────────────────────────────
  💡 Cada usuario nuevo después del #9 es ganancia pura
  💡 El costo marginal por usuario adicional es $0
  💡 El límite es la capacidad del VPS (200 usuarios)
""")

print("✅ PROYECCIÓN COMPLETADA")
