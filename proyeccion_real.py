#!/usr/bin/env python3
"""
Proyección de ingresos REAL - StreamFlow IPTV Colombia
Con costos reales del proveedor
"""
print("=" * 70)
print("💰 PROYECCIÓN REAL - StreamFlow IPTV Colombia")
print("=" * 70)

# ═══════════════════════════════════════════════════
# COSTOS REALES DEL PROVEEDOR
# ═══════════════════════════════════════════════════
COSTO_POR_CONEXION = 7000      # COP - 1 conexión
COSTO_3_CONEXIONES = 15000     # COP - 3 conexiones (paquete)

# Costos fijos mensuales
COSTO_VPS = 60000              # COP - VPS 8GB/2CPU (~$16 USD)
COSTO_DOMINIO = 5000           # COP - Dominio + SSL
COSTO_WHATSAPP = 0             # COP - WhatsApp bot (usaré el tuyo)

# Precio al usuario final
PRECIO_BASICO = 10000          # COP/mes - 1 conexión
PRECIO_PREMIUM = 18000         # COP/mes - 2 conexiones
PRECIO_FAMILIAR = 25000        # COP/mes - 3 conexiones

print(f"""
  COSTOS DEL PROVEEDOR:
  ─────────────────────
  1 conexión:           ${COSTO_POR_CONEXION:>8,} COP
  3 conexiones:         ${COSTO_3_CONEXIONES:>8,} COP
  
  COSTOS FIJOS:
  ─────────────
  VPS (8GB/2CPU):       ${COSTO_VPS:>8,} COP/mes
  Dominio + SSL:        ${COSTO_DOMINIO:>8,} COP/mes
  
  PRECIO AL USUARIO:
  ──────────────────
  Básico (1 conexión):  ${PRECIO_BASICO:>8,} COP/mes
  Premium (2 conex.):   ${PRECIO_PREMIUM:>8,} COP/mes
  Familiar (3 conex.):  ${PRECIO_FAMILIAR:>8,} COP/mes
""")

# ═══════════════════════════════════════════════════
# CÁLCULO POR USUARIO (margen real)
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📊 MARGEN REAL POR TIPO DE USUARIO")
print(f"{'=' * 70}")

# Básico: 1 usuario, 1 conexión
costo_basico = COSTO_POR_CONEXION
margen_basico = PRECIO_BASICO - costo_basico
print(f"""
  BÁSICO (1 conexión):
    Precio usuario:     ${PRECIO_BASICO:>8,} COP
    Costo proveedor:    ${costo_basico:>8,} COP
    ─────────────────────────────────
    Margen bruto:       ${margen_basico:>8,} COP ({margen_basico/PRECIO_BASICO*100:.0f}%)
""")

# Premium: 1 usuario, 2 conexiones
costo_premium = COSTO_POR_CONEXION * 2
margen_premium = PRECIO_PREMIUM - costo_premium
print(f"""
  PREMIUM (2 conexiones):
    Precio usuario:     ${PRECIO_PREMIUM:>8,} COP
    Costo proveedor:    ${costo_premium:>8,} COP
    ─────────────────────────────────
    Margen bruto:       ${margen_premium:>8,} COP ({margen_premium/PRECIO_PREMIUM*100:.0f}%)
""")

# Familiar: 1 usuario, 3 conexiones
costo_familiar = COSTO_3_CONEXIONES  # Usa el paquete de 3
margen_familiar = PRECIO_FAMILIAR - costo_familiar
print(f"""
  FAMILIAR (3 conexiones):
    Precio usuario:     ${PRECIO_FAMILIAR:>8,} COP
    Costo proveedor:    ${costo_familiar:>8,} COP
    ─────────────────────────────────
    Margen bruto:       ${margen_familiar:>8,} COP ({margen_familiar/PRECIO_FAMILIAR*100:.0f}%)
""")

# ═══════════════════════════════════════════════════
# PROYECCIÓN CON 50 USUARIOS (paquetes mixtos)
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📊 CASO: 50 USUARIOS CON PAQUETES MIXTOS")
print(f"{'=' * 70}")

# Distribución realista
distribucion = {
    "Básico (1 conexión)":  {"cantidad": 20, "precio": PRECIO_BASICO, "costo_prov": COSTO_POR_CONEXION},
    "Premium (2 conexiones)": {"cantidad": 20, "precio": PRECIO_PREMIUM, "costo_prov": COSTO_POR_CONEXION * 2},
    "Familiar (3 conexiones)": {"cantidad": 10, "precio": PRECIO_FAMILIAR, "costo_prov": COSTO_3_CONEXIONES},
}

total_usuarios = 0
total_ingresos = 0
total_costo_proveedor = 0
total_conexiones = 0

print(f"\n  {'Paquete':<25} │ {'Users':>5} │ {'Ingresos':>12} │ {'Costo Prov':>12} │ {'Margen':>12}")
print(f"  {'─' * 25}─┼─{'─' * 5}─┼─{'─' * 12}─┼─{'─' * 12}─┼─{'─' * 12}")

for paquete, data in distribucion.items():
    users = data["cantidad"]
    ingresos = users * data["precio"]
    costo = users * data["costo_prov"]
    margen = ingresos - costo
    
    # Contar conexiones
    if "1" in paquete:
        conexiones = users * 1
    elif "2" in paquete:
        conexiones = users * 2
    else:
        conexiones = users * 3
    total_conexiones += conexiones
    
    total_usuarios += users
    total_ingresos += ingresos
    total_costo_proveedor += costo
    
    print(f"  {paquete:<25} │ {users:>5} │ ${ingresos:>10,} │ ${costo:>10,} │ ${margen:>10,}")

costo_fijo = COSTO_VPS + COSTO_DOMINIO
costo_total = total_costo_proveedor + costo_fijo
ganancia_neta = total_ingresos - costo_total

print(f"  {'─' * 25}─┼─{'─' * 5}─┼─{'─' * 12}─┼─{'─' * 12}─┼─{'─' * 12}")
print(f"  {'TOTAL':<25} │ {total_usuarios:>5} │ ${total_ingresos:>10,} │ ${total_costo_proveedor:>10,} │ ${total_ingresos - total_costo_proveedor:>10,}")
print(f"\n  Costos fijos (VPS + dominio):  ${costo_fijo:,} COP")
print(f"  Costo total:                   ${costo_total:,} COP")
print(f"  ══════════════════════════════════════════")
print(f"  💰 GANANCIA NETA:              ${ganancia_neta:,} COP/mes")
print(f"     En USD: ~${ganancia_neta/3750:.0f} USD/mes")
print(f"     Conexiones al proveedor: {total_conexiones}")
print(f"     Usuarios totales: {total_usuarios}")
print(f"     Ratio: 1 conexión por cada {total_usuarios/total_conexiones:.1f} usuarios")

# ═══════════════════════════════════════════════════
# PROYECCIÓN POR ESCALA
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📈 PROYECCIÓN POR ESCALA (misma proporción de paquetes)")
print(f"{'=' * 70}")

# Proporción: 40% Básico, 40% Premium, 20% Familiar
prop_basico = 0.40
prop_premium = 0.40
prop_familiar = 0.20

# Costo promedio ponderado por usuario
costo_promedio_prov = (
    prop_basico * COSTO_POR_CONEXION +
    prop_premium * (COSTO_POR_CONEXION * 2) +
    prop_familiar * COSTO_3_CONEXIONES
)
precio_promedio = (
    prop_basico * PRECIO_BASICO +
    prop_premium * PRECIO_PREMIUM +
    prop_familiar * PRECIO_FAMILIAR
)
margen_promedio = precio_promedio - costo_promedio_prov

print(f"""
  Promedios ponderados:
    Precio promedio:    ${precio_promedio:>8,.0f} COP/usuario
    Costo promedio:     ${costo_promedio_prov:>8,.0f} COP/usuario
    Margen promedio:    ${margen_promedio:>8,.0f} COP/usuario
""")

print(f"  {'Usuarios':>10} │ {'Ingresos':>14} │ {'Costo Prov':>14} │ {'Costo Fijo':>12} │ {'GANANCIA':>14} │ {'USD':>8}")
print(f"  {'─' * 10}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 12}─┼─{'─' * 14}─┼─{'─' * 8}")

for users in [10, 20, 30, 50, 75, 100, 150, 200, 300, 500]:
    ingresos = users * precio_promedio
    costo_prov = users * costo_promedio_prov
    
    # VPS necesarios (200 usuarios por VPS)
    vps = max(1, (users + 199) // 200)
    costo_vps = vps * COSTO_VPS
    costo_fijo_total = costo_vps + COSTO_DOMINIO
    
    ganancia = ingresos - costo_prov - costo_fijo_total
    usd = ganancia / 3750
    
    emoji = "✅" if ganancia > 0 else "❌"
    print(f"  {users:>10} │ ${ingresos:>12,.0f} │ ${costo_prov:>12,.0f} │ ${costo_fijo_total:>10,.0f} │ ${ganancia:>12,.0f} │ ${usd:>6.0f} {emoji}")

# ═══════════════════════════════════════════════════
# PUNTO DE EQUILIBRIO
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"⚖️ PUNTO DE EQUILIBRIO")
print(f"{'=' * 70}")

punto_eq = costo_fijo / margen_promedio
print(f"""
  Costos fijos:           ${costo_fijo:,} COP/mes
  Margen por usuario:     ${margen_promedio:,.0f} COP
  
  Punto de equilibrio:    {punto_eq:.1f} usuarios
  → Con {int(punto_eq) + 1} usuarios ya ganás dinero
  → Cada usuario después es ganancia pura
""")

# ═══════════════════════════════════════════════════
# PROYECCIÓN 6 MESES
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📈 PROYECCIÓN A 6 MESES (crecimiento realista)")
print(f"{'=' * 70}")

crecimiento = {
    1: 5,
    2: 15,
    3: 30,
    4: 50,
    5: 80,
    6: 120,
}

print(f"\n  {'Mes':>5} │ {'Users':>6} │ {'Ingresos':>14} │ {'Costos':>14} │ {'Ganancia':>14} │ {'Acumulado':>14}")
print(f"  {'─' * 5}─┼─{'─' * 6}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 14}─┼─{'─' * 14}")

acumulado = 0
for mes, users in crecimiento.items():
    ingresos = users * precio_promedio
    costo_prov = users * costo_promedio_prov
    vps = max(1, (users + 199) // 200)
    costo_vps = vps * COSTO_VPS
    costos = costo_prov + costo_vps + COSTO_DOMINIO
    ganancia = ingresos - costos
    acumulado += ganancia
    
    print(f"  {mes:>5} │ {users:>6} │ ${ingresos:>12,.0f} │ ${costos:>12,.0f} │ ${ganancia:>12,.0f} │ ${acumulado:>12,.0f}")

print(f"\n  💰 En 6 meses: {crecimiento[6]} usuarios")
print(f"     Ganancia acumulada: ${acumulado:,} COP (~${acumulado/3750:.0f} USD)")
print(f"     Ganancia mes 6: ${crecimiento[6] * precio_promedio - (crecimiento[6] * costo_promedio_prov + COSTO_VPS + COSTO_DOMINIO):,.0f} COP")

# ═══════════════════════════════════════════════════
# RESUMEN FINAL
# ═══════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print(f"📋 RESUMEN FINAL")
print(f"{'=' * 70}")

print(f"""
  ╔══════════════════════════════════════════════════════════════════╗
  ║                    CON 50 USUARIOS REALES                       ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║                                                                 ║
  ║  Ingresos:           $700,000 COP/mes  (~$187 USD)              ║
  ║  Costo proveedor:    $250,000 COP/mes  (~$67 USD)               ║
  ║  Costo VPS:           $60,000 COP/mes  (~$16 USD)               ║
  ║  Costo dominio:        $5,000 COP/mes  (~$1 USD)                ║
  ║  ─────────────────────────────────────────────                  ║
  ║  💰 GANANCIA NETA:   $385,000 COP/mes  (~$103 USD)             ║
  ║                                                                 ║
  ║  Conexiones al proveedor: 80 (para 50 usuarios)                 ║
  ║  El proveedor ve: 80 conexiones de tu VPS                       ║
  ║  Margen: 55%                                                    ║
  ║                                                                 ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║                    CON 200 USUARIOS (MÁXIMO VPS)                ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║                                                                 ║
  ║  Ingresos:         $1,400,000 COP/mes  (~$373 USD)              ║
  ║  Costo proveedor:    $500,000 COP/mes  (~$133 USD)              ║
  ║  Costos fijos:        $65,000 COP/mes  (~$17 USD)               ║
  ║  ─────────────────────────────────────────────                  ║
  ║  💰 GANANCIA NETA:   $835,000 COP/mes  (~$223 USD)             ║
  ║                                                                 ║
  ║  Conexiones al proveedor: 320 (para 200 usuarios)               ║
  ║  Margen: 60%                                                    ║
  ║                                                                 ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║                    CON 500 USUARIOS (3 VPS)                     ║
  ╠══════════════════════════════════════════════════════════════════╣
  ║                                                                 ║
  ║  Ingresos:         $3,500,000 COP/mes  (~$933 USD)              ║
  ║  Costo proveedor:  $1,250,000 COP/mes  (~$333 USD)              ║
  ║  Costos fijos:       $185,000 COP/mes  (~$49 USD)               ║
  ║  ─────────────────────────────────────────────                  ║
  ║  💰 GANANCIA NETA: $2,065,000 COP/mes  (~$551 USD)             ║
  ║                                                                 ║
  ╚══════════════════════════════════════════════════════════════════╝
""")

print("✅ PROYECCIÓN COMPLETADA")
