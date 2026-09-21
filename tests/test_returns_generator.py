from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pytest

from ecomgen.accounting import item_paid_values
from ecomgen.config import load_preset
from ecomgen.generators import generate_returns
from ecomgen.schemas import Order, OrderItem, Product, Variant


def _inputs(item_count: int = 4_000):
    subtotal = Decimal("39.98") * item_count
    product = Product(
        id="product-1",
        title="Test shirt",
        category="tops",
        description_short="Test",
        price_eur=Decimal("20.00"),
        cost_eur=Decimal("8.00"),
        markets=["de"],
    )
    variant = Variant(
        id="variant-1",
        product_id=product.id,
        sku="TEST",
        option_name="size",
        option_value="M",
        price_eur=product.price_eur,
        inventory=item_count,
    )
    order = Order(
        id="order-1",
        customer_id="customer-1",
        market="de",
        created_at=datetime(2025, 1, 1, 12, tzinfo=UTC),
        currency="EUR",
        fx_rate_from_eur=Decimal("1.00"),
        subtotal=subtotal,
        discount=Decimal("0.00"),
        shipping=Decimal("0.00"),
        tax=Decimal("0.00"),
        total=subtotal,
        discount_code=None,
        is_repeat=False,
    )
    items = [
        OrderItem(
            id=f"item-{number:05d}",
            order_id=order.id,
            variant_id=variant.id,
            quantity=2,
            unit_price=Decimal("19.99"),
        )
        for number in range(item_count)
    ]
    return [product], [variant], [order], items


def test_return_rates_bounds_dates_reasons_and_foreign_keys() -> None:
    preset = load_preset("fashion")
    products, variants, orders, items = _inputs()
    generated = generate_returns(
        preset, products, variants, orders, items, np.random.default_rng(42)
    )
    order_ids = {order.id for order in orders}
    item_ids = {item.id for item in items}
    allowed_reasons = set(preset.categories["tops"].return_reasons)
    rate = len(generated) / len(items)

    assert 0.19 <= rate <= 0.25
    assert len({record.id for record in generated}) == len(generated)
    for record in generated:
        assert record.order_id in order_ids
        assert record.order_item_id in item_ids
        assert record.reason in allowed_reasons
        assert Decimal("0.00") <= record.refund_amount <= Decimal("39.98")
        assert record.refund_amount.as_tuple().exponent == -2
        delay = record.created_at - orders[0].created_at
        assert 3 <= delay.days <= 30
        assert record.created_at > orders[0].created_at


def test_return_reason_weights_are_reflected() -> None:
    preset = load_preset("fashion")
    products, variants, orders, items = _inputs(20_000)
    generated = generate_returns(
        preset, products, variants, orders, items, np.random.default_rng(9)
    )
    wrong_size_rate = sum(record.reason == "wrong_size" for record in generated) / len(generated)

    assert 0.46 <= wrong_size_rate <= 0.54


def test_returns_are_deterministic_and_validate_foreign_keys() -> None:
    preset = load_preset("fashion")
    products, variants, orders, items = _inputs(100)

    first = generate_returns(preset, products, variants, orders, items, np.random.default_rng(7))
    second = generate_returns(preset, products, variants, orders, items, np.random.default_rng(7))
    different = generate_returns(
        preset, products, variants, orders, items, np.random.default_rng(8)
    )

    assert first == second
    assert first != different
    with pytest.raises(ValueError, match="missing variants"):
        generate_returns(
            preset,
            products,
            variants,
            orders,
            [items[0].model_copy(update={"variant_id": "missing"})],
            np.random.default_rng(7),
        )


def test_full_line_refunds_use_order_level_cent_allocation() -> None:
    preset = load_preset("fashion")
    category = preset.categories["tops"].model_copy(
        update={"return_rate": Decimal("1.00"), "return_reasons": {"test": Decimal("1.00")}}
    )
    preset = preset.model_copy(update={"categories": {"tops": category}})
    products, variants, orders, items = _inputs(3)
    order = orders[0].model_copy(
        update={
            "subtotal": Decimal("0.03"),
            "discount": Decimal("0.01"),
            "total": Decimal("0.02"),
            "discount_code": "SAVE33",
        }
    )
    items = [
        item.model_copy(update={"id": item_id, "quantity": 1, "unit_price": Decimal("0.01")})
        for item, item_id in zip(items, ("item-c", "item-a", "item-b"), strict=True)
    ]

    generated = generate_returns(
        preset, products, variants, [order], items, np.random.default_rng(11)
    )
    expected = item_paid_values(order, items)

    assert len(generated) == len(items)
    assert {returned.order_item_id: returned.refund_amount for returned in generated} == expected
