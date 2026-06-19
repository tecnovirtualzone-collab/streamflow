#!/usr/bin/env python3
"""
Proyección final - StreamFlow IPTV Colombia
Planes por dispositivos y cantidad de canales
"""
print("=" * 70)
print("💰 PROYECCIÓN FINAL - StreamFlow IPTV Colombia")
print("=" * 70)

# ═══════════════════════════════════════════════════
# ESTRUCTURA DE PLANES
# ═══════════════════════════════════════════════════
planes = {
    "Básico": {
        "dispositivos": 1,
        "canales": 40,
        "precio": 10000,
        "costo_prov": 7000,  # 1 conexión
        "descripcion": "TV Colombia + Noticias + Kids",
    },
    "Estándar": {
        "dispositivos": 2,
        "canales": 70,
        "precio": 18000,
        "costo_prov": 14000,  # 2 conexiones
        "descripcion": "Básico + Deportes + Novelas",
    },
    "Premium": {
        "dispositivos": 3,
        "canales": 100,
        "precio": 25000,
        "costo_prov": 15000,  # 3 conexiones (paquete)
        "descripcion": "Todo + Cine + PPV + Exclusivos",
    },
}

COSTO_VPS = 60000       # COP/mes
COSTO_DOMINIO = 5000    # COP/mes
COSTO_FIJO = COSTO_VPS + COSTO_DOMINIO

print(f"""
  ╔══════════════════════════════════════════════════════════════════╗
  ║                    PLANES STREAMFLOW IPTV                       ║
  ╠══════════════════════════════════════════════════════════════════╣""")

for nombre, p in planes.items():
    margen = p["precio"] - p["costo_prov"]
    margen_pct = margen / p["precio"] * 100
    print(f"""  ║                                                                  ║
  ║  📦 {nombre.upper():<10} ({p['canales']} canales, {p['dispositivos']} dispositivo{'s' if p['dispositivos'] > 1 else ''}){' ' * (35 - len(f"{p['canales']} canales, {p['dispositivos']} dispositivos"))}║
  ║     {p['descripcion']:<50}        ║
  ║     Precio: ${p['precio']:>8,} COP/mes{' ' * 33}║
  ║     Costo:  ${p['costo_prov']:>8,} COP/mes{' ' * 33}║
  ║     Margen: ${margen:>8,} COP/mes ({margen_pct:.0f}%){' ' * 25}║""")

print(f"""  ║                                                                  ║
  ╚══════════════════════════════════════════════════════════════════╝
""")

# ═══════════════════════════════════════════════════
# MARGEN POR PLAN
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📊 ANÁLISIS POR PLAN")
print(f"{'=' * 70}")

print(f"\n  {'Plan':<12} │ {'Precio':>10} │ {'Costo':>10} │ {'Margen':>10} │ {'Margen %':>10} │ {'Por disp.':>10}")
print(f"  {'─' * 12}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 10}")

for nombre, p in planes.items():
    margen = p["precio"] - p["costo_prov"]
    margen_pct = margen / p["precio"] * 100
    por_disp = p["precio"] / p["dispositivos"]
    print(f"  {nombre:<12} │ ${p['precio']:>8,} │ ${p['costo_prov']:>8,} │ ${margen:>8,} │ {margen_pct:>8.0f}% │ ${por_disp:>8,.0f}")

# ═══════════════════════════════════════════════════
# PROYECCIÓN 50 USUARIOS
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📊 CASO: 50 USUARIOS")
print(f"{'=' * 70}")

distribucion_50 = {
    "Básico": 15,
    "Estándar": 20,
    "Premium": 15,
}

total_users = 0
total_ingresos = 0
total_costo_prov = 0
total_conexiones = 0
total_dispositivos = 0

print(f"\n  {'Plan':<12} │ {'Users':>5} │ {'Ingresos':>12} │ {'Costo Prov':>12} │ {'Margen':>12} │ {'Conex.':>8}")
print(f"  {'─' * 12}─┼─{'─' * 5}─┼─{'─' * 12}─┼─{'─' * 12}─┼─{'─' * 12}─┼─{'─' * 8}")

for nombre, users in distribucion_50.items():
    p = planes[nombre]
    ingresos = users * p["precio"]
    costo = users * p["costo_prov"]
    margen = ingresos - costo
    conexiones = users * p["dispositivos"]
    dispositivos = users * p["dispositivos"]
    
    total_users += users
    total_ingresos += ingresos
    total_costo_prov += costo
    total_conexiones += conexiones
    total_dispositivos += dispositivos
    
    print(f"  {nombre:<12} │ {users:>5} │ ${ingresos:>10,} │ ${costo:>10,} │ ${margen:>10,} │ {conexiones:>6}")

costo_total = total_costo_prov + COSTO_FIJO
ganancia = total_ingresos - costo_total

print(f"  {'─' * 12}─┼─{'─' * 5}─┼─{'─' * 12}─┼─{'─' * 12}─┼─{'─' * 12}─┼─{'─' * 8}")
print(f"  {'TOTAL':<12} │ {total_users:>5} │ ${total_ingresos:>10,} │ ${total_costo_prov:>10,} │ ${total_ingresos - total_costo_prov:>10,} │ {total_conexiones:>6}")
print(f"\n  Costos fijos: ${COSTO_FIJO:,} COP")
print(f"  Costo total:  ${costo_total:,} COP")
print(f"  ══════════════════════════════════════")
print(f"  💰 GANANCIA:  ${ganancia:,} COP/mes (~${ganancia/3750:.0f} USD)")
print(f"  Dispositivos: {total_dispositivos} (para {total_users} cuentas)")
print(f"  Conexiones al proveedor: {total_conexiones}")

# ═══════════════════════════════════════════════════
# PROYECCIÓN POR ESCALA
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📈 PROYECCIÓN POR ESCALA")
print(f"{'=' * 70}")

# Proporciones: 30% Básico, 40% Estándar, 30% Premium
prop = {"Básico": 0.30, "Estándar": 0.40, "Premium": 0.30}

precio_prom = sum(prop[n] * p["precio"] for n, p in planes.items())
costo_prom = sum(prop[n] * p["costo_prov"] for n, p in planes.items())
margen_prom = precio_prom - costo_prom
conn_prom = sum(prop[n] * p["dispositivos"] for n, p in planes.items())

print(f"""
  Promedios ponderados:
    Precio promedio:    ${precio_prom:>8,.0f} COP/cuenta
    Costo promedio:     ${costo_prom:>8,.0f} COP/cuenta
    Margen promedio:    ${margen_prom:>8,.0f} COP/cuenta
    Conexiones/cuenta:  {conn_prom:>8.1f}
""")

print(f"  {'Cuentas':>8} │ {'Ingresos':>14} │ {'Costo Prov':>14} │ {'Costo Fijo':>12} │ {'GANANCIA':>14} │ {'USD':>8} │ {'Conex.':>8}")
print(f"  {'─' * 8}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 12}─┼─{'─' * 14}─┼─{'─' * 8}─┼─{'─' * 8}")

for cuentas in [10, 20, 30, 50, 75, 100, 150, 200, 300, 500]:
    ingresos = cuentas * precio_prom
    costo_prov = cuentas * costo_prom
    conexiones = int(cuentas * conn_prom)
    
    # VPS necesarios (200 usuarios por VPS)
    vps = max(1, (cuentas + 199) // 200)
    costo_vps = vps * COSTO_VPS
    costo_fijo = costo_vps + COSTO_DOMINIO
    
    ganancia = ingresos - costo_prov - costo_fijo
    usd = ganancia / 3750
    
    emoji = "✅" if ganancia > 0 else "❌"
    print(f"  {cuentas:>8} │ ${ingresos:>12,.0f} │ ${costo_prov:>12,.0f} │ ${costo_fijo:>10,.0f} │ ${ganancia:>12,.0f} │ ${usd:>6.0f} │ {conexiones:>6} {emoji}")

# ═══════════════════════════════════════════════════
# PUNTO DE EQUILIBRIO
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"⚖️ PUNTO DE EQUILIBRIO")
print(f"{'=' * 70}")

punto_eq = COSTO_FIJO / margen_prom
print(f"""
  Costos fijos:     ${COSTO_FIJO:,} COP/mes
  Margen por cuenta: ${margen_prom:,.0f} COP
  
  Punto de equilibrio: {punto_eq:.1f} cuentas
  → Con {int(punto_eq) + 1} cuentas ya ganás dinero
  → Cada cuenta nueva después es ganancia pura
""")

# ═══════════════════════════════════════════════════
# PROYECCIÓN 6 MESES
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📈 PROYECCIÓN A 6 MESES")
print(f"{'=' * 70}")

crecimiento = {1: 5, 2: 15, 3: 30, 4: 50, 5: 80, 6: 120}

print(f"\n  {'Mes':>5} │ {'Cuentas':>8} │ {'Ingresos':>14} │ {'Costos':>14} │ {'Ganancia':>14} │ {'Acumulado':>14}")
print(f"  {'─' * 5}─┼─{'─' * 8}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 14}")

acumulado = 0
for mes, cuentas in crecimiento.items():
    ingresos = cuentas * precio_prom
    costo_prov = cuentas * costo_prom
    vps = max(1, (cuentas + 199) // 200)
    costos = costo_prov + vps * COSTO_VPS + COSTO_DOMINIO
    ganancia = ingresos - costos
    acumulado += ganancia
    print(f"  {mes:>5} │ {cuentas:>8} │ ${ingresos:>12,.0f} │ ${costos:>12,.0f} │ ${ganancia:>12,.0f} │ ${acumulado:>12,.0f}")

# ═══════════════════════════════════════════════════
# RESUMEN FINAL
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📋 RESUMEN FINAL")
print(f"{'=' * 70}")

# Calcular para 50 y 200
ing_50 = 50 * precio_prom
costo_50 = 50 * costo_prom + COSTO_FIJO
gan_50 = ing_50 - costo_50

ing_200 = 200 * precio_prom
costo_200 = 200 * costo_prom + COSTO_FIJO
gan_200 = ing_200 - costo_200

ing_500 = 500 * precio_prom
costo_500 = 500 * costo_prom + (3 * COSTO_VPS) + COSTO_DOMINIO
gan_500 = ing_500 - costo_500

print(f"""
  ╔══════════════════════════════════════════════════════════════════╗
  ║                    CON 50 CUENTAS                               ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  Ingresos:           ${ing_50:>12,.0f} COP  (~${ing_50/3750:.0f} USD)          ║
  ║  Costo proveedor:    ${50*costo_prom:>12,.0f} COP  (~${50*costo_prom/3750:.0f} USD)          ║
  ║  Costos fijos:       ${COSTO_FIJO:>12,.0f} COP  (~${COSTO_FIJO/3750:.0f} USD)          ║
  ║  ─────────────────────────────────────────────                  ║
  ║  💰 GANANCIA NETA:   ${gan_50:>12,.0f} COP  (~${gan_50/3750:.0f} USD)          ║
  ║  Conexiones proveedor: ~{int(50*conn_prom):>3}                                    ║
  ║  Margen: {gan_50/ing_50*100:.0f}%                                                    ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║                    CON 200 CUENTAS (1 VPS)                      ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  Ingresos:           ${ing_200:>12,.0f} COP  (~${ing_200/3750:.0f} USD)          ║
  ║  Costo proveedor:    ${200*costo_prom:>12,.0f} COP  (~${200*costo_prom/3750:.0f} USD)          ║
  ║  Costos fijos:       ${COSTO_FIJO:>12,.0f} COP  (~${COSTO_FIJO/3750:.0f} USD)          ║
  ║  ─────────────────────────────────────────────                  ║
  ║  💰 GANANCIA NETA:   ${gan_200:>12,.0f} COP  (~${gan_200/3750:.0f} USD)          ║
  ║  Conexiones proveedor: ~{int(200*conn_prom):>3}                                    ║
  ║  Margen: {gan_200/ing_200*100:.0f}%                                                    ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║                    CON 500 CUENTAS (3 VPS)                      ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║  Ingresos:           ${ing_500:>12,.0f} COP  (~${ing_500/3750:.0f} USD)          ║
  ║  Costo proveedor:    ${500*costo_prom:>12,.0f} COP  (~${500*costo_prom/3750:.0f} USD)          ║
  ║  Costos fijos:       ${(3*COSTO_VPS+COSTO_DOMINIO):>12,.0f} COP  (~${(3*COSTO_VPS+COSTO_DOMINIO)/3750:.0f} USD)          ║
  ║  ─────────────────────────────────────────────                  ║
  ║  💰 GANANCIA NETA:   ${gan_500:>12,.0f} COP  (~${gan_500/3750:.0f} USD)          ║
  ║  Conexiones proveedor: ~{int(500*conn_prom):>3}                                    ║
  ║  Margen: {gan_500/ing_500*100:.0f}%                                                    ║
  ╚══════════════════════════════════════════════════════════════════╝

  💡 DATOS CLAVE:
  ─────────────
  • Punto de equilibrio: {int(punto_eq)+1} cuentas
  • Cada cuenta nueva después es ganancia pura
  • El proveedor solo ve las conexiones de tu VPS
  • 1 VPS aguanta 200 cuentas sin problema
  • Margen mejora con más usuarios (los costos fijos se diluyen)
""")

print("✅ PROYECCIÓN COMPLETADA")
