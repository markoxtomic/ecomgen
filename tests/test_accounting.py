from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ecomgen.accounting import allocate_order_discounts, item_paid_values
from ecomgen.schemas import Order, OrderItem


def _order(discount: Decimal) -> Order:
    return Order(
        id="order-1",
        customer_id="customer-1",
        market="de",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        currency="EUR",
        fx_rate_from_eur=Decimal("1.00"),
        subtotal=Decimal("0.03"),
        discount=discount,
        shipping=Decimal("0.00"),
        tax=Decimal("0.00"),
        total=Decimal("0.03") - discount,
        discount_code="SAVE33",
        is_repeat=False,
    )


def _items() -> list[OrderItem]:
    return [
        OrderItem(
            id=item_id,
            order_id="order-1",
            variant_id="variant-1",
            quantity=1,
            unit_price=Decimal("0.01"),
        )
        for item_id in ("item-c", "item-a", "item-b")
    ]


@pytest.mark.parametrize(
    ("discount", "expected"),
    [
        (
            Decimal("0.01"),
            {"item-a": Decimal("0.01"), "item-b": Decimal("0.00"), "item-c": Decimal("0.00")},
        ),
        (
            Decimal("0.02"),
            {"item-a": Decimal("0.01"), "item-b": Decimal("0.01"), "item-c": Decimal("0.00")},
        ),
    ],
)
def test_discount_allocation_and_paid_values_reconcile_exactly(
    discount: Decimal, expected: dict[str, Decimal]
) -> None:
    order = _order(discount)
    items = _items()

    allocations = allocate_order_discounts(order, items)
    paid_values = item_paid_values(order, items)

    assert allocations == expected
    assert sum(allocations.values(), Decimal("0.00")) == order.discount
    assert sum(paid_values.values(), Decimal("0.00")) == order.subtotal - order.discount
    for item in items:
        assert paid_values[item.id] == item.unit_price * item.quantity - allocations[item.id]


def test_discount_allocation_is_deterministic_under_input_ordering() -> None:
    order = _order(Decimal("0.01"))
    items = _items()

    forward = allocate_order_discounts(order, items)
    reversed_input = allocate_order_discounts(order, list(reversed(items)))

    assert forward == reversed_input
    assert forward["item-a"] == Decimal("0.01")
