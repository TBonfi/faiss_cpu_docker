"""
PromotionGenerator: Generador de sugerencias de promociones basado en reglas de asociación.

Genera opciones de descuento para productos consecuentes cuyo volumen está decreciendo,
respetando el margen incremental como límite máximo de descuento.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Set, Tuple, Optional
import json


class PromotionGenerator:
    """
    Genera promociones basadas en reglas de asociación y datos transaccionales.

    Tipos de promociones generadas:
    - pct_discount: Descuento porcentual directo
    - combo_nxm: Combo N unidades por precio de M (ej: 6x5)
    - second_unit_discount: Segunda unidad con descuento

    Attributes:
        rules_df: DataFrame con reglas de asociación de mlxtend
        transactions_df: DataFrame transaccional con ventas
    """

    def __init__(
        self,
        rules_df: pd.DataFrame,
        transactions_df: pd.DataFrame,
        margin_pct_range: Tuple[float, float] = (0.25, 0.35)
    ):
        """
        Inicializa el generador de promociones.

        Args:
            rules_df: DataFrame con reglas de asociación (debe contener
                      'material_id antecedents' y 'material_id consequents')
            transactions_df: DataFrame transaccional con columnas:
                            año, mes, día, cliente_id, material_id, cajas_fisicas, ingreso_neto
            margin_pct_range: Rango para simular margen como % del ingreso (min, max)
        """
        self.rules_df = rules_df.copy()
        self.transactions_df = transactions_df.copy()
        self.margin_pct_range = margin_pct_range

        self._validate_inputs()
        self._prepare_data()

        # Cache para cálculos
        self._declining_products: Optional[Set] = None
        self._quantity_distributions: Dict = {}
        self._product_stats: Optional[pd.DataFrame] = None

    def _validate_inputs(self) -> None:
        """Valida que los DataFrames tengan las columnas requeridas."""
        required_rules_cols = ['material_id antecedents', 'material_id consequents']
        required_txn_cols = ['año', 'mes', 'día', 'cliente_id', 'material_id',
                            'cajas_fisicas', 'ingreso_neto']

        missing_rules = set(required_rules_cols) - set(self.rules_df.columns)
        if missing_rules:
            raise ValueError(f"Faltan columnas en rules_df: {missing_rules}")

        missing_txn = set(required_txn_cols) - set(self.transactions_df.columns)
        if missing_txn:
            raise ValueError(f"Faltan columnas en transactions_df: {missing_txn}")

    def _prepare_data(self) -> None:
        """Prepara los datos: crea transaction_id, precio unitario y margen."""
        txn = self.transactions_df

        # Crear transaction_id
        txn['transaction_id'] = (
            txn['cliente_id'].astype(str) + '_' +
            txn['año'].astype(str) + '_' +
            txn['mes'].astype(str) + '_' +
            txn['día'].astype(str)
        )

        # Calcular precio unitario (evitar división por cero)
        txn['precio_unitario'] = np.where(
            txn['cajas_fisicas'] > 0,
            txn['ingreso_neto'] / txn['cajas_fisicas'],
            0
        )

        # Simular margen como porcentaje del ingreso
        np.random.seed(42)
        margin_pct = np.random.uniform(
            self.margin_pct_range[0],
            self.margin_pct_range[1],
            len(txn)
        )
        txn['margen'] = txn['ingreso_neto'] * margin_pct

        # Margen unitario
        txn['margen_unitario'] = np.where(
            txn['cajas_fisicas'] > 0,
            txn['margen'] / txn['cajas_fisicas'],
            0
        )

        self.transactions_df = txn

    def _compute_product_stats(self) -> pd.DataFrame:
        """Calcula estadísticas agregadas por producto."""
        if self._product_stats is not None:
            return self._product_stats

        txn = self.transactions_df

        stats = txn.groupby('material_id').agg({
            'cajas_fisicas': ['sum', 'mean', 'std'],
            'ingreso_neto': 'sum',
            'margen': 'sum',
            'precio_unitario': 'mean',
            'margen_unitario': 'mean',
            'transaction_id': 'nunique'
        }).reset_index()

        stats.columns = [
            'material_id', 'total_cajas', 'avg_cajas_por_linea', 'std_cajas',
            'total_ingreso', 'total_margen', 'precio_unitario_promedio',
            'margen_unitario_promedio', 'num_transacciones'
        ]

        self._product_stats = stats
        return stats

    def _detect_declining_products(self) -> Set:
        """
        Detecta productos cuyo volumen decreció entre el penúltimo y último mes.

        Returns:
            Set de material_id con volumen decreciente
        """
        if self._declining_products is not None:
            return self._declining_products

        txn = self.transactions_df

        # Identificar los dos últimos meses
        months = txn.groupby(['año', 'mes']).size().reset_index()[['año', 'mes']]
        months['period'] = months['año'] * 12 + months['mes']
        months = months.sort_values('period', ascending=False)

        if len(months) < 2:
            self._declining_products = set()
            return self._declining_products

        last_month = months.iloc[0]
        prev_month = months.iloc[1]

        # Ventas del último mes
        ventas_ultimo = txn[
            (txn['año'] == last_month['año']) & (txn['mes'] == last_month['mes'])
        ].groupby('material_id')['cajas_fisicas'].sum()

        # Ventas del mes anterior
        ventas_anterior = txn[
            (txn['año'] == prev_month['año']) & (txn['mes'] == prev_month['mes'])
        ].groupby('material_id')['cajas_fisicas'].sum()

        # Calcular diferencia
        diff = ventas_ultimo.subtract(ventas_anterior, fill_value=0)

        # Productos que bajaron al menos 1 unidad
        declining = diff[diff < 0].index.tolist()

        self._declining_products = set(declining)
        return self._declining_products

    def _get_quantity_distribution(self, material_id: int) -> Dict:
        """
        Obtiene la distribución de cantidades por transacción para un producto.

        Args:
            material_id: ID del producto

        Returns:
            Dict con estadísticas: min, max, mean, median, percentiles
        """
        if material_id in self._quantity_distributions:
            return self._quantity_distributions[material_id]

        txn = self.transactions_df
        quantities = txn[txn['material_id'] == material_id]['cajas_fisicas']
        quantities = quantities[quantities > 0]

        if len(quantities) == 0:
            result = {
                'min': 0, 'max': 0, 'mean': 0, 'median': 0,
                'p75': 0, 'p90': 0, 'count': 0
            }
        else:
            result = {
                'min': quantities.min(),
                'max': quantities.max(),
                'mean': quantities.mean(),
                'median': quantities.median(),
                'p75': quantities.quantile(0.75),
                'p90': quantities.quantile(0.90),
                'count': len(quantities)
            }

        self._quantity_distributions[material_id] = result
        return result

    def _get_joint_purchases(self, antecedent_id: int, consequent_id: int) -> Dict:
        """
        Calcula estadísticas de compras conjuntas de antecedente y consecuente.

        Args:
            antecedent_id: material_id del antecedente
            consequent_id: material_id del consecuente

        Returns:
            Dict con: count, avg_qty_antecedent, avg_qty_consequent,
                     max_qty_consequent, total_qty_consequent
        """
        txn = self.transactions_df

        # Transacciones que contienen cada producto
        txn_ant = txn[txn['material_id'] == antecedent_id][['transaction_id', 'cajas_fisicas']]
        txn_ant = txn_ant.rename(columns={'cajas_fisicas': 'qty_antecedent'})

        txn_cons = txn[txn['material_id'] == consequent_id][['transaction_id', 'cajas_fisicas']]
        txn_cons = txn_cons.rename(columns={'cajas_fisicas': 'qty_consequent'})

        # Join para encontrar compras conjuntas
        joint = txn_ant.merge(txn_cons, on='transaction_id', how='inner')

        if len(joint) == 0:
            return {
                'count': 0,
                'avg_qty_antecedent': 0,
                'avg_qty_consequent': 0,
                'max_qty_consequent': 0,
                'total_qty_consequent': 0,
                'median_qty_consequent': 0
            }

        return {
            'count': len(joint),
            'avg_qty_antecedent': joint['qty_antecedent'].mean(),
            'avg_qty_consequent': joint['qty_consequent'].mean(),
            'max_qty_consequent': joint['qty_consequent'].max(),
            'total_qty_consequent': joint['qty_consequent'].sum(),
            'median_qty_consequent': joint['qty_consequent'].median()
        }

    def _calculate_incremental_margin(
        self,
        material_id: int,
        base_qty: float,
        target_qty: float
    ) -> Tuple[float, float]:
        """
        Calcula el margen incremental disponible para descuento.

        El descuento máximo permitido es el margen de las unidades incrementales.

        Args:
            material_id: ID del producto
            base_qty: Cantidad base esperada (sin promoción)
            target_qty: Cantidad objetivo (con promoción)

        Returns:
            Tuple de (margen_incremental_absoluto, margen_unitario_promedio)
        """
        stats = self._compute_product_stats()
        product_stats = stats[stats['material_id'] == material_id]

        if len(product_stats) == 0:
            return 0.0, 0.0

        margen_unitario = product_stats['margen_unitario_promedio'].values[0]
        incremental_units = target_qty - base_qty
        margen_incremental = margen_unitario * incremental_units

        return margen_incremental, margen_unitario

    def _generate_discount_options(
        self,
        consequent_id: int,
        joint_stats: Dict,
        product_stats: pd.Series
    ) -> List[Dict]:
        """
        Genera opciones de promoción para un producto consecuente.

        Args:
            consequent_id: material_id del consecuente
            joint_stats: Estadísticas de compras conjuntas
            product_stats: Estadísticas del producto

        Returns:
            Lista de diccionarios con opciones de promoción
        """
        options = []

        qty_dist = self._get_quantity_distribution(consequent_id)
        avg_qty = max(1, joint_stats.get('avg_qty_consequent', 1))
        max_qty = max(1, joint_stats.get('max_qty_consequent', 1))
        median_qty = max(1, joint_stats.get('median_qty_consequent', 1))

        precio_unitario = product_stats.get('precio_unitario_promedio', 0)
        margen_unitario = product_stats.get('margen_unitario_promedio', 0)

        if precio_unitario <= 0 or margen_unitario <= 0:
            return options

        # Base quantity: usamos la mediana de compras conjuntas
        base_qty = max(1, int(median_qty))

        # =====================
        # OPCIÓN 1: Descuento % directo (para compra de base_qty + 1)
        # =====================
        target_qty = base_qty + 1
        margen_incremental, _ = self._calculate_incremental_margin(
            consequent_id, base_qty, target_qty
        )

        # El descuento máximo es el margen de la unidad incremental
        # aplicado sobre el total de unidades target
        total_price = precio_unitario * target_qty
        max_discount_pct = (margen_incremental / total_price) * 100 if total_price > 0 else 0

        # Generar opciones de descuento en incrementos de 5%
        for discount_pct in [5, 10, 15, 20, 25, 30]:
            if discount_pct <= max_discount_pct:
                options.append({
                    'promo_type': 'pct_discount',
                    'promo_params': json.dumps({'discount_pct': discount_pct, 'min_qty': target_qty}),
                    'promo_description': f'{discount_pct}% OFF llevando {target_qty}+ unidades',
                    'target_qty': target_qty,
                    'base_qty': base_qty,
                    'discount_value': discount_pct,
                    'max_discount_allowed': round(max_discount_pct, 2),
                    'priority_score': self._calculate_priority(
                        discount_pct, max_discount_pct, target_qty, max_qty, 'pct_discount'
                    )
                })

        # =====================
        # OPCIÓN 2: Combo NxM (N unidades por precio de M)
        # =====================
        # Generar combos basados en cantidades históricas
        for n in range(2, min(int(max_qty) + 2, 8)):  # Hasta max_qty + 1, máximo 7
            m = n - 1  # Paga M, lleva N

            # Calcular si el margen lo permite
            # Descuento = precio de 1 unidad, distribuido en N unidades
            margen_incremental_combo, _ = self._calculate_incremental_margin(
                consequent_id, m, n
            )
            descuento_total = precio_unitario  # Se "regala" una unidad

            if margen_incremental_combo >= descuento_total * 0.8:  # Toleramos hasta 80% del margen
                # Viabilidad: si históricamente compraron cerca de N
                viabilidad = 1.0 if n <= max_qty + 1 else 0.5

                options.append({
                    'promo_type': 'combo_nxm',
                    'promo_params': json.dumps({'n': n, 'm': m}),
                    'promo_description': f'Llevá {n} pagá {m}',
                    'target_qty': n,
                    'base_qty': m,
                    'discount_value': round((1/n) * 100, 1),  # % equivalente
                    'max_discount_allowed': round((margen_incremental_combo / (precio_unitario * n)) * 100, 2),
                    'priority_score': self._calculate_priority(
                        1/n * 100, (margen_incremental_combo / (precio_unitario * n)) * 100,
                        n, max_qty, 'combo_nxm', viabilidad
                    )
                })

        # =====================
        # OPCIÓN 3: Segunda unidad con descuento
        # =====================
        # El margen de la 2da unidad es el límite del descuento
        margen_segunda, _ = self._calculate_incremental_margin(consequent_id, 1, 2)
        max_descuento_segunda = (margen_segunda / precio_unitario) * 100 if precio_unitario > 0 else 0

        for descuento_segunda in [20, 30, 40, 50, 60, 70]:
            if descuento_segunda <= max_descuento_segunda:
                options.append({
                    'promo_type': 'second_unit_discount',
                    'promo_params': json.dumps({'second_unit_discount_pct': descuento_segunda}),
                    'promo_description': f'2da unidad al {100 - descuento_segunda}% (descuento {descuento_segunda}%)',
                    'target_qty': 2,
                    'base_qty': 1,
                    'discount_value': descuento_segunda / 2,  # Descuento efectivo sobre 2 unidades
                    'max_discount_allowed': round(max_descuento_segunda, 2),
                    'priority_score': self._calculate_priority(
                        descuento_segunda, max_descuento_segunda, 2, max_qty, 'second_unit_discount'
                    )
                })

        return options

    def _calculate_priority(
        self,
        discount_used: float,
        max_discount: float,
        target_qty: float,
        max_historical_qty: float,
        promo_type: str,
        viabilidad_override: float = None
    ) -> float:
        """
        Calcula un score de prioridad para una promoción.

        Factores considerados:
        - Qué tan cerca está el descuento del máximo permitido (menos es mejor)
        - Viabilidad histórica (target_qty vs max_historical_qty)
        - Tipo de promoción (preferencia por combos que son más atractivos)

        Returns:
            Score entre 0 y 100 (mayor es mejor)
        """
        # Factor 1: Margen de seguridad (preferimos no usar todo el margen)
        if max_discount > 0:
            margin_safety = 1 - (discount_used / max_discount)
        else:
            margin_safety = 0

        # Factor 2: Viabilidad histórica
        if viabilidad_override is not None:
            viabilidad = viabilidad_override
        else:
            if target_qty <= max_historical_qty:
                viabilidad = 1.0
            elif target_qty <= max_historical_qty + 1:
                viabilidad = 0.7
            else:
                viabilidad = 0.4

        # Factor 3: Preferencia por tipo de promoción
        type_weights = {
            'combo_nxm': 1.0,  # Los combos son muy atractivos
            'second_unit_discount': 0.9,  # 2da unidad también
            'pct_discount': 0.8  # Descuento directo menos atractivo
        }
        type_weight = type_weights.get(promo_type, 0.5)

        # Score final ponderado
        score = (margin_safety * 0.3 + viabilidad * 0.5 + type_weight * 0.2) * 100

        return round(score, 2)

    def _estimate_impact(
        self,
        rule_row: pd.Series,
        promo_option: Dict,
        joint_stats: Dict,
        product_stats: pd.Series
    ) -> Dict:
        """
        Estima el impacto de una promoción en unidades, ingreso y margen.

        La estimación se basa en:
        - Cantidad histórica de compras conjuntas
        - Incremento esperado por la promoción
        - Descuento aplicado

        Args:
            rule_row: Fila de la regla de asociación
            promo_option: Opción de promoción generada
            joint_stats: Estadísticas de compras conjuntas
            product_stats: Estadísticas del producto

        Returns:
            Dict con estimaciones de impacto
        """
        joint_count = joint_stats.get('count', 0)
        avg_qty = joint_stats.get('avg_qty_consequent', 1)

        base_qty = promo_option['base_qty']
        target_qty = promo_option['target_qty']

        precio_unitario = product_stats.get('precio_unitario_promedio', 0)
        margen_unitario = product_stats.get('margen_unitario_promedio', 0)

        # Estimación conservadora: 30% de las transacciones conjuntas aprovecharán la promo
        adoption_rate = 0.30
        transactions_impacted = joint_count * adoption_rate

        # Unidades incrementales por transacción
        incremental_units_per_txn = target_qty - base_qty

        # Total unidades incrementales
        delta_units = transactions_impacted * incremental_units_per_txn

        # Ingreso incremental (menos el descuento)
        promo_type = promo_option['promo_type']
        discount_value = promo_option['discount_value']

        if promo_type == 'pct_discount':
            # Ingreso por unidades nuevas menos descuento sobre todas
            ingreso_bruto = delta_units * precio_unitario
            descuento_total = transactions_impacted * target_qty * precio_unitario * (discount_value / 100)
            delta_revenue = ingreso_bruto - descuento_total

        elif promo_type == 'combo_nxm':
            # Se regala 1 unidad por combo
            ingreso_bruto = delta_units * precio_unitario
            unidades_regaladas = transactions_impacted * 1  # 1 unidad gratis por combo
            descuento_total = unidades_regaladas * precio_unitario
            delta_revenue = ingreso_bruto - descuento_total

        else:  # second_unit_discount
            # Descuento solo en la 2da unidad
            ingreso_bruto = delta_units * precio_unitario
            descuento_total = transactions_impacted * precio_unitario * (discount_value / 100)
            delta_revenue = ingreso_bruto - descuento_total

        # Margen incremental
        # Margen de unidades nuevas menos el costo del descuento
        delta_margin = (delta_units * margen_unitario) - descuento_total

        # Baseline para comparación
        baseline_units = joint_count * avg_qty
        baseline_revenue = joint_count * avg_qty * precio_unitario
        baseline_margin = joint_count * avg_qty * margen_unitario

        return {
            'estimated_delta_units': round(delta_units, 0),
            'estimated_delta_revenue': round(delta_revenue, 2),
            'estimated_delta_margin': round(delta_margin, 2),
            'baseline_units': round(baseline_units, 0),
            'baseline_revenue': round(baseline_revenue, 2),
            'baseline_margin': round(baseline_margin, 2),
            'estimated_unit_growth_pct': round((delta_units / baseline_units * 100) if baseline_units > 0 else 0, 2),
            'estimated_revenue_growth_pct': round((delta_revenue / baseline_revenue * 100) if baseline_revenue > 0 else 0, 2),
            'transactions_impacted': round(transactions_impacted, 0)
        }

    def generate(self) -> pd.DataFrame:
        """
        Genera el DataFrame de reglas enriquecido con opciones de promoción.

        Returns:
            DataFrame con todas las reglas expandidas por opción de promoción,
            incluyendo estimaciones de impacto y scores de prioridad.
        """
        declining_products = self._detect_declining_products()
        product_stats = self._compute_product_stats()

        results = []

        for idx, rule in self.rules_df.iterrows():
            antecedent_id = rule['material_id antecedents']
            consequent_id = rule['material_id consequents']

            # Filtrar: solo reglas donde el consecuente está decreciendo
            if consequent_id not in declining_products:
                continue

            # Obtener estadísticas
            joint_stats = self._get_joint_purchases(antecedent_id, consequent_id)

            cons_stats = product_stats[product_stats['material_id'] == consequent_id]
            if len(cons_stats) == 0:
                continue
            cons_stats = cons_stats.iloc[0]

            # Calcular tendencia del consecuente
            txn = self.transactions_df
            months = txn.groupby(['año', 'mes']).size().reset_index()[['año', 'mes']]
            months['period'] = months['año'] * 12 + months['mes']
            months = months.sort_values('period', ascending=False)

            if len(months) >= 2:
                last_month = months.iloc[0]
                prev_month = months.iloc[1]

                ventas_ultimo = txn[
                    (txn['año'] == last_month['año']) &
                    (txn['mes'] == last_month['mes']) &
                    (txn['material_id'] == consequent_id)
                ]['cajas_fisicas'].sum()

                ventas_anterior = txn[
                    (txn['año'] == prev_month['año']) &
                    (txn['mes'] == prev_month['mes']) &
                    (txn['material_id'] == consequent_id)
                ]['cajas_fisicas'].sum()

                consequent_trend = ventas_ultimo - ventas_anterior
            else:
                consequent_trend = 0

            # Generar opciones de promoción
            promo_options = self._generate_discount_options(
                consequent_id, joint_stats, cons_stats
            )

            if not promo_options:
                continue

            # Crear filas para cada opción (producto cartesiano)
            for promo_idx, promo in enumerate(promo_options, 1):
                # Estimar impacto
                impact = self._estimate_impact(rule, promo, joint_stats, cons_stats)

                # Construir fila resultado
                result_row = rule.to_dict()
                result_row.update({
                    'promo_option_idx': promo_idx,
                    'promo_type': promo['promo_type'],
                    'promo_params': promo['promo_params'],
                    'promo_description': promo['promo_description'],
                    'consequent_trend': consequent_trend,
                    'joint_purchase_count': joint_stats['count'],
                    'avg_joint_qty_consequent': round(joint_stats['avg_qty_consequent'], 2),
                    'max_joint_qty_consequent': joint_stats['max_qty_consequent'],
                    'target_qty': promo['target_qty'],
                    'base_qty': promo['base_qty'],
                    'discount_value': promo['discount_value'],
                    'max_discount_allowed': promo['max_discount_allowed'],
                    'promo_priority_score': promo['priority_score'],
                })
                result_row.update(impact)

                results.append(result_row)

        if not results:
            # Retornar DataFrame vacío con las columnas esperadas
            return pd.DataFrame(columns=list(self.rules_df.columns) + [
                'promo_option_idx', 'promo_type', 'promo_params', 'promo_description',
                'consequent_trend', 'joint_purchase_count', 'avg_joint_qty_consequent',
                'max_joint_qty_consequent', 'target_qty', 'base_qty', 'discount_value',
                'max_discount_allowed', 'promo_priority_score', 'estimated_delta_units',
                'estimated_delta_revenue', 'estimated_delta_margin', 'baseline_units',
                'baseline_revenue', 'baseline_margin', 'estimated_unit_growth_pct',
                'estimated_revenue_growth_pct', 'transactions_impacted'
            ])

        result_df = pd.DataFrame(results)

        # Ordenar por regla y prioridad
        result_df = result_df.sort_values(
            ['material_id antecedents', 'material_id consequents', 'promo_priority_score'],
            ascending=[True, True, False]
        )

        return result_df.reset_index(drop=True)

    def get_summary_stats(self) -> Dict:
        """
        Retorna estadísticas de resumen sobre los productos y promociones.

        Returns:
            Dict con estadísticas de resumen
        """
        declining = self._detect_declining_products()

        return {
            'total_rules': len(self.rules_df),
            'total_products': self.transactions_df['material_id'].nunique(),
            'declining_products_count': len(declining),
            'rules_with_declining_consequent': len(
                self.rules_df[self.rules_df['material_id consequents'].isin(declining)]
            ),
            'date_range': {
                'min': f"{self.transactions_df['año'].min()}-{self.transactions_df['mes'].min():02d}",
                'max': f"{self.transactions_df['año'].max()}-{self.transactions_df['mes'].max():02d}"
            }
        }


# Ejemplo de uso
if __name__ == "__main__":
    # Crear datos de ejemplo para testing

    # Reglas de ejemplo
    rules_data = {
        'material_id antecedents': [102003, 101816],
        'material_id consequents': [100403, 100116],
        'antecedents': ['102003 | SPRITE 1.25L', '101816 | CC SIN AZÚCAR 500ML'],
        'consequents': ['100403 | COCA COLA RED 1,25', '100116 | COCA-COLA 500ML'],
        'support': [0.32, 0.36],
        'confidence': [0.98, 0.98],
        'lift': [1.37, 1.14],
    }
    rules_df = pd.DataFrame(rules_data)

    # Transaccional de ejemplo
    np.random.seed(42)
    n_rows = 1000

    txn_data = {
        'año': [2025] * n_rows,
        'mes': np.random.choice([8, 9], n_rows),
        'día': np.random.randint(1, 28, n_rows),
        'cliente_id': np.random.randint(500000000, 500100000, n_rows),
        'material_id': np.random.choice([100403, 100116, 102003, 101816], n_rows),
        'cajas_fisicas': np.random.randint(1, 10, n_rows).astype(float),
        'ingreso_neto': np.random.uniform(5000, 50000, n_rows),
    }
    transactions_df = pd.DataFrame(txn_data)

    # Generar promociones
    generator = PromotionGenerator(rules_df, transactions_df)
    print("Estadísticas de resumen:")
    print(generator.get_summary_stats())

    promotions_df = generator.generate()
    print(f"\nPromociones generadas: {len(promotions_df)}")
    if len(promotions_df) > 0:
        print(promotions_df[['antecedents', 'consequents', 'promo_type',
                            'promo_description', 'promo_priority_score']].head(10))
