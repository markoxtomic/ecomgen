"""Deterministic return generation from order items."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np

from ecomgen.accounting import item_paid_values
from ecomgen.config.models import PresetConfig
from ecomgen.schemas import Order, OrderItem, Product, Return, Variant

RETURN_DELAY_MIN_DAYS = 3
RETURN_DELAY_MAX_DAYS = 30


def _unique_by_id(records: Sequence[object], name: str) -> dict[str, object]:
    indexed = {record.id: record for record in records}
    if len(indexed) != len(records):
        raise ValueError(f"{name} ids must be unique")
    return indexed


def _weighted_reason(weights: dict[str, Decimal], rng: np.random.Generator) -> str:
    reasons = tuple(sorted(weights))
    probabilities = np.asarray([float(weights[reason]) for reason in reasons])
    probabilities /= probabilities.sum()
    return reasons[int(rng.choice(len(reasons), p=probabilities))]


def generate_returns(
    config: PresetConfig,
    products: Sequence[Product],
    variants: Sequence[Variant],
    orders: Sequence[Order],
    order_items: Sequence[OrderItem],
    rng: np.random.Generator,
    *,
    return_cutoff: datetime | None = None,
) -> list[Return]:
    """Sample at most one return per item using its product category settings.

    Refunds are the VAT-inclusive line value less its share of the order
    discount (see ``ecomgen.accounting``). When ``return_cutoff`` is supplied,
    returns after that inclusive boundary are omitted. Item ids are required to
    be unique and each item is sampled once, so an order item never receives
    more than one return and cumulative refunds can never exceed its paid value.
    All randomness comes from the caller-provided NumPy generator.
    """

    product_by_id = _unique_by_id(products, "product")
    variant_by_id = _unique_by_id(variants, "variant")
    order_by_id = _unique_by_id(orders, "order")
    _unique_by_id(order_items, "order item")

    missing_variant_products = sorted(
        {variant.product_id for variant in variants if variant.product_id not in product_by_id}
    )
    if missing_variant_products:
        raise ValueError(f"variants reference missing products: {missing_variant_products}")
    missing_orders = sorted(
        {item.order_id for item in order_items if item.order_id not in order_by_id}
    )
    if missing_orders:
        raise ValueError(f"order items reference missing orders: {missing_orders}")
    missing_variants = sorted(
        {item.variant_id for item in order_items if item.variant_id not in variant_by_id}
    )
    if missing_variants:
        raise ValueError(f"order items reference missing variants: {missing_variants}")

    unknown_categories = sorted(
        {
            product.category
            for product in product_by_id.values()
            if product.category not in config.categories
        }
    )
    if unknown_categories:
        raise ValueError(f"products use categories absent from the preset: {unknown_categories}")

    items_by_order: defaultdict[str, list[OrderItem]] = defaultdict(list)
    for item in order_items:
        items_by_order[item.order_id].append(item)
    paid_values = {
        item_id: paid_value
        for order_id, related_items in items_by_order.items()
        for item_id, paid_value in item_paid_values(order_by_id[order_id], related_items).items()
    }

    returns: list[Return] = []
    for item in order_items:
        variant = variant_by_id[item.variant_id]
        product = product_by_id[variant.product_id]
        category = config.categories[product.category]
        if rng.random() >= float(category.return_rate):
            continue

        order = order_by_id[item.order_id]
        max_delay_days = RETURN_DELAY_MAX_DAYS
        if return_cutoff is not None:
            try:
                available_days = (return_cutoff - order.created_at) // timedelta(days=1)
            except TypeError as exc:
                raise ValueError(
                    "return_cutoff timezone awareness must match order created_at"
                ) from exc
            max_delay_days = min(max_delay_days, available_days)
        if max_delay_days < RETURN_DELAY_MIN_DAYS:
            continue
        delay_days = int(rng.integers(RETURN_DELAY_MIN_DAYS, int(max_delay_days) + 1))
        refund = paid_values[item.id]
        returns.append(
            Return(
                id=f"ret-{len(returns) + 1:09d}",
                order_id=order.id,
                order_item_id=item.id,
                reason=_weighted_reason(category.return_reasons, rng),
                refund_amount=refund,
                created_at=order.created_at + timedelta(days=delay_days),
            )
        )

    return returns
