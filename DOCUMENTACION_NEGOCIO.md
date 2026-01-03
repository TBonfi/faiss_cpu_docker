# Generador de Promociones Basado en Reglas de Asociación

## Guía para el Negocio

---

## 1. ¿Qué hace este sistema?

El sistema analiza **qué productos se compran juntos** y genera **sugerencias de promociones** para aumentar las ventas de productos cuyo volumen está cayendo.

**Ejemplo simple:**
> Si detectamos que "cuando un cliente compra Sprite 1.25L, el 98% de las veces también compra Coca-Cola 1.25L", y las ventas de Coca-Cola 1.25L bajaron este mes, el sistema sugiere promociones para incentivar la compra de más unidades de Coca-Cola cuando el cliente ya está comprando Sprite.

---

## 2. ¿Qué datos utiliza?

### Reglas de Asociación (ya generadas)
Patrones de compra conjunta con métricas como:
- **Confianza**: Probabilidad de que se compre el consecuente dado que se compró el antecedente
- **Soporte**: Frecuencia de la combinación en el total de transacciones
- **Lift**: Qué tan fuerte es la asociación (>1 significa que se potencian)

### Transaccional de Ventas
Historial de ventas con:
- Fecha (año, mes, día)
- Cliente
- Producto
- Cantidad (cajas físicas)
- Ingreso

---

## 3. Paso a Paso del Proceso

### Paso 1: Identificar productos con ventas decrecientes

El sistema compara las ventas del **último mes vs el mes anterior**.

```
Ejemplo:
- Coca-Cola 500ml vendió 1,000 cajas en Agosto
- Coca-Cola 500ml vendió 850 cajas en Septiembre
- Diferencia: -150 cajas → PRODUCTO EN DECLIVE ✓
```

**Solo se generan promociones para productos cuyas ventas están bajando.**

---

### Paso 2: Analizar compras conjuntas

Para cada regla de asociación, el sistema analiza:

| Métrica | Qué significa | Ejemplo |
|---------|---------------|---------|
| Cantidad de compras conjuntas | Cuántas veces se compraron juntos | 500 transacciones |
| Cantidad promedio del consecuente | Cuántas unidades lleva típicamente | 2.3 cajas promedio |
| Cantidad máxima del consecuente | Máximo histórico en una compra | 8 cajas |

**¿Por qué importa?**
Si históricamente nadie compró más de 3 unidades, ofrecer "Llevá 6 pagá 5" tiene poca probabilidad de éxito.

---

### Paso 3: Calcular el margen disponible para descuentos

**Regla fundamental:** El descuento solo puede salir del margen de las unidades ADICIONALES que esperamos vender.

```
Ejemplo:
- Un cliente normalmente compra 2 unidades
- Queremos que compre 3 unidades
- Margen por unidad: $500
- Margen disponible para descuento: $500 (solo la 3ra unidad)
- Si el precio por unidad es $2,000, el descuento máximo sobre 3 unidades es:
  $500 / ($2,000 × 3) = 8.3%
```

**Esto garantiza que la promoción no destruya el margen existente.**

---

### Paso 4: Generar opciones de promoción

El sistema genera **3 tipos de promociones**:

#### Tipo A: Descuento Porcentual
```
"15% OFF llevando 3+ unidades"
```
- Incentiva compra de más unidades
- Fácil de comunicar

#### Tipo B: Combo N×M
```
"Llevá 6 pagá 5"
```
- Muy atractivo para el cliente
- Funciona bien cuando hay historial de compras de esas cantidades

#### Tipo C: Segunda Unidad con Descuento
```
"2da unidad al 50%"
```
- Ideal para productos de compra unitaria
- Incentiva duplicar la compra

---

### Paso 5: Estimar el impacto

Para cada promoción, el sistema calcula:

| Métrica | Descripción |
|---------|-------------|
| **Unidades incrementales** | Cuántas unidades adicionales se venderían |
| **Ingreso incremental** | Ingreso extra menos el descuento otorgado |
| **Margen incremental** | Ganancia neta después del descuento |
| **% Crecimiento en unidades** | Aumento porcentual vs baseline |

**Supuesto conservador:** Se asume que el 30% de las transacciones conjuntas aprovecharán la promoción.

---

### Paso 6: Priorizar las opciones

Cada promoción recibe un **score de prioridad (0-100)** basado en:

| Factor | Peso | Lógica |
|--------|------|--------|
| Margen de seguridad | 30% | Preferimos no usar todo el margen disponible |
| Viabilidad histórica | 50% | La cantidad objetivo debe ser alcanzable según historial |
| Tipo de promoción | 20% | Combos > 2da unidad > Descuento % |

---

## 4. ¿Cómo leer los resultados?

El sistema genera una tabla donde **cada fila es una opción de promoción**:

### Columnas principales:

| Columna | Significado |
|---------|-------------|
| `antecedents` | Producto disparador (lo que compra el cliente) |
| `consequents` | Producto objetivo de la promoción |
| `promo_type` | Tipo: `pct_discount`, `combo_nxm`, `second_unit_discount` |
| `promo_description` | Texto legible de la promoción |
| `consequent_trend` | Cambio en ventas (negativo = bajó) |
| `promo_priority_score` | Score de 0-100 (mayor = mejor opción) |

### Columnas de impacto estimado:

| Columna | Significado |
|---------|-------------|
| `estimated_delta_units` | Unidades adicionales esperadas |
| `estimated_delta_revenue` | Ingreso adicional esperado |
| `estimated_delta_margin` | Margen adicional esperado |
| `baseline_units` | Unidades actuales (sin promoción) |
| `estimated_unit_growth_pct` | % de crecimiento en unidades |

### Columnas de control:

| Columna | Significado |
|---------|-------------|
| `max_discount_allowed` | Descuento máximo permitido por margen |
| `discount_value` | Descuento efectivo de esta opción |
| `joint_purchase_count` | Transacciones históricas conjuntas |
| `max_joint_qty_consequent` | Máximo histórico comprado del producto |

---

## 5. Ejemplo de Interpretación

```
Regla: Sprite 500ml → Coca-Cola 500ml
Promoción: "Llevá 3 pagá 2"
Score de prioridad: 78.5

Métricas:
- Tendencia consecuente: -120 unidades (bajó)
- Compras conjuntas históricas: 800 transacciones
- Cantidad promedio: 1.8 unidades
- Cantidad máxima histórica: 5 unidades

Impacto estimado:
- Unidades incrementales: +240
- Ingreso incremental: +$180,000
- Margen incremental: +$45,000
- Crecimiento: +16.7%
```

**Lectura:**
> "Para clientes que compran Sprite 500ml, ofrecemos Coca-Cola 500ml con promoción 'Llevá 3 pagá 2'. Históricamente hubo 800 compras conjuntas y el máximo comprado fue 5 unidades, así que pedir 3 es viable. Esperamos vender 240 unidades adicionales con un margen neto de $45,000."

---

## 6. Flujo de Decisión Recomendado

```
1. Filtrar por promo_priority_score > 60
   └─→ Quedarse con las mejores opciones

2. Agrupar por producto consecuente
   └─→ Ver todas las opciones para cada producto

3. Elegir 1 promoción por producto
   └─→ Considerar el tipo que mejor funcione en el canal

4. Validar el max_discount_allowed
   └─→ Asegurar que el descuento no exceda el límite

5. Revisar estimated_delta_margin > 0
   └─→ Confirmar que la promoción es rentable

6. Implementar y medir resultados reales
   └─→ Comparar vs estimaciones para calibrar el modelo
```

---

## 7. Preguntas Frecuentes

**¿Por qué algunas reglas no tienen promociones?**
- El producto consecuente no está en declive (ventas estables o creciendo)
- No hay suficiente margen para ofrecer descuentos
- No hay historial de compras conjuntas

**¿Puedo modificar los porcentajes de descuento ofrecidos?**
Sí, los valores (5%, 10%, 15%...) son configurables en el código.

**¿Qué tan confiables son las estimaciones de impacto?**
Son estimaciones conservadoras (30% de adopción). Los resultados reales pueden variar. Se recomienda medir y ajustar.

**¿Con qué frecuencia debo ejecutar el proceso?**
Recomendado: mensualmente, para detectar nuevos productos en declive.

---

## 8. Glosario

| Término | Definición |
|---------|------------|
| **Antecedente** | Producto que "dispara" la regla (lo que compra el cliente primero) |
| **Consecuente** | Producto asociado que queremos promocionar |
| **Regla de asociación** | Patrón estadístico de compra conjunta |
| **Margen incremental** | Ganancia de las unidades adicionales vendidas |
| **Lift** | Fuerza de la asociación entre productos (>1 = se potencian) |
| **Confianza** | Probabilidad de comprar B dado que se compró A |

---

*Documento generado para el equipo de negocio. Para consultas técnicas, contactar al equipo de Analytics.*
