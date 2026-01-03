"""
PromotionGenerator: Generador de sugerencias de promociones basado en reglas de asociación.
Versión optimizada para bajo consumo de memoria.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Set, Tuple, Optional, Iterator
import json
import gc


class PromotionGenerator:
    """
    Genera promociones basadas en reglas de asociación y datos transaccionales.
    Optimizado para bajo consumo de memoria.
    """

    def __init__(
        self,
        rules_df: pd.DataFrame,
        transactions_df: pd.DataFrame,
        margin_pct_range: Tuple[float, float] = (0.25, 0.35)
    ):
        self.margin_pct_range = margin_pct_range

        # Validar antes de procesar
        self._validate_inputs(rules_df, transactions_df)

        # Optimizar tipos de datos y preparar (in-place, sin copias)
        self.rules_df = self._optimize_rules_dtypes(rules_df)
        self.transactions_df = self._prepare_transactions(transactions_df)

        # Pre-calcular todo en batch (una sola vez)
        self._precompute_all_stats()

    def _validate_inputs(self, rules_df: pd.DataFrame, transactions_df: pd.DataFrame) -> None:
        required_rules_cols = ['material_id antecedents', 'material_id consequents']
        required_txn_cols = ['año', 'mes', 'día', 'cliente_id', 'material_id',
                            'cajas_fisicas', 'ingreso_neto']

        missing_rules = set(required_rules_cols) - set(rules_df.columns)
        if missing_rules:
            raise ValueError(f"Faltan columnas en rules_df: {missing_rules}")

        missing_txn = set(required_txn_cols) - set(transactions_df.columns)
        if missing_txn:
            raise ValueError(f"Faltan columnas en transactions_df: {missing_txn}")

    def _optimize_rules_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimiza tipos de datos de reglas."""
        df = df.copy()

        # Convertir IDs a int32
        for col in ['material_id antecedents', 'material_id consequents']:
            if col in df.columns:
                df[col] = df[col].astype(np.int32)

        # Convertir floats a float32
        float_cols = df.select_dtypes(include=['float64']).columns
        for col in float_cols:
            df[col] = df[col].astype(np.float32)

        return df

    def _prepare_transactions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Prepara transacciones con tipos optimizados, sin copias innecesarias."""
        # Seleccionar solo columnas necesarias
        cols_needed = ['año', 'mes', 'día', 'cliente_id', 'material_id',
                       'cajas_fisicas', 'ingreso_neto']
        df = df[cols_needed].copy()

        # Optimizar tipos
        df['año'] = df['año'].astype(np.int16)
        df['mes'] = df['mes'].astype(np.int8)
        df['día'] = df['día'].astype(np.int8)
        df['cliente_id'] = df['cliente_id'].astype(np.int32)
        df['material_id'] = df['material_id'].astype(np.int32)
        df['cajas_fisicas'] = df['cajas_fisicas'].astype(np.float32)
        df['ingreso_neto'] = df['ingreso_neto'].astype(np.float32)

        # Transaction ID como entero compuesto (más eficiente que string)
        df['transaction_id'] = (
            df['cliente_id'].astype(np.int64) * 1000000 +
            df['año'].astype(np.int64) * 10000 +
            df['mes'].astype(np.int64) * 100 +
            df['día'].astype(np.int64)
        )

        # Precio y margen unitario
        mask = df['cajas_fisicas'] > 0
        df['precio_unitario'] = np.where(
            mask,
            df['ingreso_neto'] / df['cajas_fisicas'],
            0
        ).astype(np.float32)

        # Margen simulado
        np.random.seed(42)
        margin_pct = np.random.uniform(
            self.margin_pct_range[0],
            self.margin_pct_range[1],
            len(df)
        ).astype(np.float32)
        df['margen_unitario'] = np.where(
            mask,
            (df['ingreso_neto'] * margin_pct) / df['cajas_fisicas'],
            0
        ).astype(np.float32)

        gc.collect()
        return df

    def _precompute_all_stats(self) -> None:
        """Pre-calcula todas las estadísticas en batch para evitar recálculos."""
        txn = self.transactions_df

        # 1. Identificar meses
        month_groups = txn.groupby(['año', 'mes']).size().reset_index()[['año', 'mes']]
        month_groups['period'] = month_groups['año'] * 12 + month_groups['mes']
        month_groups = month_groups.sort_values('period', ascending=False)

        self._last_month = month_groups.iloc[0] if len(month_groups) > 0 else None
        self._prev_month = month_groups.iloc[1] if len(month_groups) > 1 else None

        # 2. Ventas por producto por mes (solo últimos 2 meses)
        if self._last_month is not None and self._prev_month is not None:
            mask_last = (txn['año'] == self._last_month['año']) & (txn['mes'] == self._last_month['mes'])
            mask_prev = (txn['año'] == self._prev_month['año']) & (txn['mes'] == self._prev_month['mes'])

            ventas_last = txn.loc[mask_last].groupby('material_id')['cajas_fisicas'].sum()
            ventas_prev = txn.loc[mask_prev].groupby('material_id')['cajas_fisicas'].sum()

            # Productos decrecientes
            diff = ventas_last.subtract(ventas_prev, fill_value=0)
            self._declining_products = set(diff[diff < 0].index.tolist())

            # Tendencia por producto
            self._product_trend = diff.to_dict()

            del ventas_last, ventas_prev, diff
        else:
            self._declining_products = set()
            self._product_trend = {}

        # 3. Stats por producto (una sola agregación)
        self._product_stats = txn.groupby('material_id').agg({
            'cajas_fisicas': 'sum',
            'precio_unitario': 'mean',
            'margen_unitario': 'mean'
        }).rename(columns={
            'cajas_fisicas': 'total_cajas',
            'precio_unitario': 'precio_unitario_promedio',
            'margen_unitario': 'margen_unitario_promedio'
        })

        # 4. Pre-calcular compras conjuntas para TODOS los pares en las reglas
        self._precompute_joint_purchases()

        # 5. Distribución de cantidades por producto (solo para productos en reglas)
        self._precompute_quantity_distributions()

        gc.collect()

    def _precompute_joint_purchases(self) -> None:
        """Pre-calcula estadísticas de compras conjuntas para todos los pares de reglas."""
        txn = self.transactions_df
        rules = self.rules_df

        # Obtener pares únicos
        pairs = rules[['material_id antecedents', 'material_id consequents']].drop_duplicates()

        # Crear lookup de transaction_id -> productos comprados
        txn_products = txn.groupby('transaction_id').agg({
            'material_id': list,
            'cajas_fisicas': list
        }).reset_index()

        # Convertir a diccionario para lookup rápido
        txn_dict = {}
        for _, row in txn_products.iterrows():
            txn_dict[row['transaction_id']] = dict(zip(row['material_id'], row['cajas_fisicas']))

        del txn_products
        gc.collect()

        # Calcular stats para cada par
        self._joint_stats = {}

        for _, pair in pairs.iterrows():
            ant_id = pair['material_id antecedents']
            cons_id = pair['material_id consequents']

            counts = []
            qty_ant = []
            qty_cons = []

            for txn_id, products in txn_dict.items():
                if ant_id in products and cons_id in products:
                    counts.append(1)
                    qty_ant.append(products[ant_id])
                    qty_cons.append(products[cons_id])

            if counts:
                self._joint_stats[(ant_id, cons_id)] = {
                    'count': len(counts),
                    'avg_qty_antecedent': np.mean(qty_ant),
                    'avg_qty_consequent': np.mean(qty_cons),
                    'max_qty_consequent': max(qty_cons),
                    'total_qty_consequent': sum(qty_cons),
                    'median_qty_consequent': np.median(qty_cons)
                }
            else:
                self._joint_stats[(ant_id, cons_id)] = {
                    'count': 0, 'avg_qty_antecedent': 0, 'avg_qty_consequent': 0,
                    'max_qty_consequent': 0, 'total_qty_consequent': 0, 'median_qty_consequent': 0
                }

        del txn_dict
        gc.collect()

    def _precompute_quantity_distributions(self) -> None:
        """Pre-calcula distribución de cantidades para productos en reglas."""
        txn = self.transactions_df

        # Solo productos que son consecuentes
        cons_products = set(self.rules_df['material_id consequents'].unique())

        # Filtrar transacciones relevantes
        txn_filtered = txn[txn['material_id'].isin(cons_products) & (txn['cajas_fisicas'] > 0)]

        self._qty_distributions = {}
        for material_id, group in txn_filtered.groupby('material_id'):
            quantities = group['cajas_fisicas'].values
            self._qty_distributions[material_id] = {
                'max': float(quantities.max()),
                'mean': float(quantities.mean()),
                'median': float(np.median(quantities)),
                'count': len(quantities)
            }

        del txn_filtered
        gc.collect()

    def _generate_promo_options(
        self,
        consequent_id: int,
        joint_stats: Dict,
        precio_unitario: float,
        margen_unitario: float
    ) -> Iterator[Dict]:
        """Genera opciones de promoción como generador (no almacena en memoria)."""

        if precio_unitario <= 0 or margen_unitario <= 0:
            return

        avg_qty = max(1, joint_stats.get('avg_qty_consequent', 1))
        max_qty = max(1, joint_stats.get('max_qty_consequent', 1))
        median_qty = max(1, joint_stats.get('median_qty_consequent', 1))
        base_qty = max(1, int(median_qty))

        # OPCIÓN 1: Descuento % directo
        target_qty = base_qty + 1
        margen_incremental = margen_unitario * (target_qty - base_qty)
        total_price = precio_unitario * target_qty
        max_discount_pct = (margen_incremental / total_price) * 100 if total_price > 0 else 0

        for discount_pct in [5, 10, 15, 20, 25, 30]:
            if discount_pct <= max_discount_pct:
                yield {
                    'promo_type': 'pct_discount',
                    'promo_params': json.dumps({'discount_pct': discount_pct, 'min_qty': int(target_qty)}),
                    'promo_description': f'{discount_pct}% OFF llevando {int(target_qty)}+ unidades',
                    'target_qty': target_qty,
                    'base_qty': base_qty,
                    'discount_value': float(discount_pct),
                    'max_discount_allowed': round(max_discount_pct, 2),
                    'priority_score': self._calc_priority(discount_pct, max_discount_pct, target_qty, max_qty, 'pct_discount')
                }

        # OPCIÓN 2: Combo NxM
        for n in range(2, min(int(max_qty) + 2, 8)):
            m = n - 1
            margen_inc_combo = margen_unitario * (n - m)
            descuento_total = precio_unitario

            if margen_inc_combo >= descuento_total * 0.8:
                viabilidad = 1.0 if n <= max_qty + 1 else 0.5
                max_disc = (margen_inc_combo / (precio_unitario * n)) * 100

                yield {
                    'promo_type': 'combo_nxm',
                    'promo_params': json.dumps({'n': n, 'm': m}),
                    'promo_description': f'Llevá {n} pagá {m}',
                    'target_qty': float(n),
                    'base_qty': float(m),
                    'discount_value': round((1/n) * 100, 1),
                    'max_discount_allowed': round(max_disc, 2),
                    'priority_score': self._calc_priority(1/n * 100, max_disc, n, max_qty, 'combo_nxm', viabilidad)
                }

        # OPCIÓN 3: Segunda unidad con descuento
        margen_segunda = margen_unitario
        max_desc_segunda = (margen_segunda / precio_unitario) * 100 if precio_unitario > 0 else 0

        for desc_segunda in [20, 30, 40, 50, 60, 70]:
            if desc_segunda <= max_desc_segunda:
                yield {
                    'promo_type': 'second_unit_discount',
                    'promo_params': json.dumps({'second_unit_discount_pct': desc_segunda}),
                    'promo_description': f'2da unidad al {100 - desc_segunda}% (descuento {desc_segunda}%)',
                    'target_qty': 2.0,
                    'base_qty': 1.0,
                    'discount_value': desc_segunda / 2,
                    'max_discount_allowed': round(max_desc_segunda, 2),
                    'priority_score': self._calc_priority(desc_segunda, max_desc_segunda, 2, max_qty, 'second_unit_discount')
                }

    def _calc_priority(self, discount_used, max_discount, target_qty, max_hist_qty, promo_type, viab_override=None):
        """Calcula score de prioridad."""
        margin_safety = 1 - (discount_used / max_discount) if max_discount > 0 else 0

        if viab_override is not None:
            viabilidad = viab_override
        elif target_qty <= max_hist_qty:
            viabilidad = 1.0
        elif target_qty <= max_hist_qty + 1:
            viabilidad = 0.7
        else:
            viabilidad = 0.4

        type_weights = {'combo_nxm': 1.0, 'second_unit_discount': 0.9, 'pct_discount': 0.8}
        type_weight = type_weights.get(promo_type, 0.5)

        return round((margin_safety * 0.3 + viabilidad * 0.5 + type_weight * 0.2) * 100, 2)

    def _estimate_impact(self, promo: Dict, joint_stats: Dict, precio_unitario: float, margen_unitario: float) -> Dict:
        """Estima impacto de una promoción."""
        joint_count = joint_stats.get('count', 0)
        avg_qty = joint_stats.get('avg_qty_consequent', 1)

        base_qty = promo['base_qty']
        target_qty = promo['target_qty']
        discount_value = promo['discount_value']
        promo_type = promo['promo_type']

        adoption_rate = 0.30
        transactions_impacted = joint_count * adoption_rate
        incremental_units = target_qty - base_qty
        delta_units = transactions_impacted * incremental_units

        if promo_type == 'pct_discount':
            ingreso_bruto = delta_units * precio_unitario
            descuento_total = transactions_impacted * target_qty * precio_unitario * (discount_value / 100)
            delta_revenue = ingreso_bruto - descuento_total
        elif promo_type == 'combo_nxm':
            ingreso_bruto = delta_units * precio_unitario
            descuento_total = transactions_impacted * precio_unitario
            delta_revenue = ingreso_bruto - descuento_total
        else:
            ingreso_bruto = delta_units * precio_unitario
            descuento_total = transactions_impacted * precio_unitario * (discount_value / 100)
            delta_revenue = ingreso_bruto - descuento_total

        delta_margin = (delta_units * margen_unitario) - descuento_total

        baseline_units = joint_count * avg_qty
        baseline_revenue = baseline_units * precio_unitario
        baseline_margin = baseline_units * margen_unitario

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

    def generate(self, batch_size: int = 100) -> pd.DataFrame:
        """
        Genera DataFrame enriquecido con opciones de promoción.
        Procesa en batches para controlar memoria.
        """
        results = []
        rules_filtered = self.rules_df[
            self.rules_df['material_id consequents'].isin(self._declining_products)
        ]

        if len(rules_filtered) == 0:
            return self._empty_result()

        # Procesar en batches
        for batch_start in range(0, len(rules_filtered), batch_size):
            batch = rules_filtered.iloc[batch_start:batch_start + batch_size]

            for idx, rule in batch.iterrows():
                ant_id = rule['material_id antecedents']
                cons_id = rule['material_id consequents']

                # Obtener stats pre-calculadas
                joint_stats = self._joint_stats.get((ant_id, cons_id), {})
                if joint_stats.get('count', 0) == 0:
                    continue

                cons_stats = self._product_stats.loc[cons_id] if cons_id in self._product_stats.index else None
                if cons_stats is None:
                    continue

                precio = cons_stats['precio_unitario_promedio']
                margen = cons_stats['margen_unitario_promedio']
                trend = self._product_trend.get(cons_id, 0)

                # Generar opciones (generador, no lista)
                promo_idx = 0
                for promo in self._generate_promo_options(cons_id, joint_stats, precio, margen):
                    promo_idx += 1
                    impact = self._estimate_impact(promo, joint_stats, precio, margen)

                    result_row = {
                        **rule.to_dict(),
                        'promo_option_idx': promo_idx,
                        'promo_type': promo['promo_type'],
                        'promo_params': promo['promo_params'],
                        'promo_description': promo['promo_description'],
                        'consequent_trend': trend,
                        'joint_purchase_count': joint_stats['count'],
                        'avg_joint_qty_consequent': round(joint_stats['avg_qty_consequent'], 2),
                        'max_joint_qty_consequent': joint_stats['max_qty_consequent'],
                        'target_qty': promo['target_qty'],
                        'base_qty': promo['base_qty'],
                        'discount_value': promo['discount_value'],
                        'max_discount_allowed': promo['max_discount_allowed'],
                        'promo_priority_score': promo['priority_score'],
                        **impact
                    }
                    results.append(result_row)

            # Limpiar memoria entre batches
            if batch_start % (batch_size * 5) == 0:
                gc.collect()

        if not results:
            return self._empty_result()

        result_df = pd.DataFrame(results)
        result_df = result_df.sort_values(
            ['material_id antecedents', 'material_id consequents', 'promo_priority_score'],
            ascending=[True, True, False]
        ).reset_index(drop=True)

        # Optimizar tipos del resultado
        for col in result_df.select_dtypes(include=['float64']).columns:
            result_df[col] = result_df[col].astype(np.float32)

        gc.collect()
        return result_df

    def _empty_result(self) -> pd.DataFrame:
        """Retorna DataFrame vacío con columnas esperadas."""
        cols = list(self.rules_df.columns) + [
            'promo_option_idx', 'promo_type', 'promo_params', 'promo_description',
            'consequent_trend', 'joint_purchase_count', 'avg_joint_qty_consequent',
            'max_joint_qty_consequent', 'target_qty', 'base_qty', 'discount_value',
            'max_discount_allowed', 'promo_priority_score', 'estimated_delta_units',
            'estimated_delta_revenue', 'estimated_delta_margin', 'baseline_units',
            'baseline_revenue', 'baseline_margin', 'estimated_unit_growth_pct',
            'estimated_revenue_growth_pct', 'transactions_impacted'
        ]
        return pd.DataFrame(columns=cols)

    def get_summary_stats(self) -> Dict:
        """Retorna estadísticas de resumen."""
        return {
            'total_rules': len(self.rules_df),
            'total_products': self.transactions_df['material_id'].nunique(),
            'declining_products_count': len(self._declining_products),
            'rules_with_declining_consequent': len(
                self.rules_df[self.rules_df['material_id consequents'].isin(self._declining_products)]
            )
        }

    def clear_cache(self) -> None:
        """Libera memoria de caches internos."""
        self._joint_stats = {}
        self._qty_distributions = {}
        self._product_trend = {}
        gc.collect()
