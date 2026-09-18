"""Shared accounting formulas used by generators and validation."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from ecomgen.schemas import Order, OrderItem

CENT = Decimal("0.01")


def _cents(value: Decimal, name: str) -> int:
    if value != value.quantize(CENT):
        raise ValueError(f"{name} must be cent-exact")
    return int(value / CENT)


def allocate_order_discounts(order: Order, order_items: Sequence[OrderItem]) -> dict[str, Decimal]:
    """Allocate an order discount across its lines in whole cents.

    Each line first receives the floor of its exact pro-rata discount. Remaining
    cents go to the largest fractional remainders, with item id as the stable
    tie-break. The allocations therefore sum exactly to ``order.discount``
    regardless of input ordering.
    """

    items = list(order_items)
    if any(item.order_id != order.id for item in items):
        raise ValueError(f"all order items must belong to order {order.id}")
    if len({item.id for item in items}) != len(items):
        raise ValueError("order item ids must be unique")

    subtotal_cents = _cents(order.subtotal, "order subtotal")
    discount_cents = _cents(order.discount, "order discount")
    if not items:
        if subtotal_cents or discount_cents:
            raise ValueError("cannot allocate a nonzero order without order items")
        return {}

    line_cents = {
        item.id: _cents(item.unit_price * item.quantity, f"order item {item.id} value")
        for item in items
    }
    line_total_cents = sum(line_cents.values())
    if line_total_cents != subtotal_cents:
        raise ValueError("order item total must equal order subtotal")
    if not line_total_cents:
        if discount_cents:
            raise ValueError("cannot allocate a positive discount across zero-value lines")
        return {item.id: Decimal("0.00") for item in sorted(items, key=lambda item: item.id)}
    if discount_cents > subtotal_cents:
        raise ValueError("order discount cannot exceed the order item total")

    allocated_cents: dict[str, int] = {}
    remainders: dict[str, int] = {}
    for item_id, value_cents in line_cents.items():
        allocated_cents[item_id], remainders[item_id] = divmod(
            discount_cents * value_cents, subtotal_cents
        )

    cents_left = discount_cents - sum(allocated_cents.values())
    ranked_ids = sorted(line_cents, key=lambda item_id: (-remainders[item_id], item_id))
    for item_id in ranked_ids[:cents_left]:
        allocated_cents[item_id] += 1

    return {
        item_id: Decimal(allocated_cents[item_id]) * CENT for item_id in sorted(allocated_cents)
    }


def item_paid_values(order: Order, order_items: Sequence[OrderItem]) -> dict[str, Decimal]:
    """Return each line's VAT-inclusive paid value after allocated discount.

    Order-item prices already include VAT. Shipping is excluded, and VAT is not
    added again. A line's paid value is both its full-return refund and its
    cumulative refund cap.
    """

    items = list(order_items)
    discounts = allocate_order_discounts(order, items)
    items_by_id = {item.id: item for item in items}
    return {
        item_id: items_by_id[item_id].unit_price * items_by_id[item_id].quantity - discount
        for item_id, discount in discounts.items()
    }
