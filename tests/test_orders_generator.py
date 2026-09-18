from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import numpy as np

from ecomgen.config import load_markets, load_preset
from ecomgen.generators import generate_orders
from ecomgen.schemas import Customer, Product, Variant


def _inputs(customer_count: int = 2_000):
    product = Product(
        id="p-all",
        title="Garden collection",
        category="planters",
        description_short="A test collection.",
        price_eur=Decimal("29.20"),
        cost_eur=Decimal("10.00"),
        markets=["de"],
    )
    expensive_product = Product(
        id="p-high",
        title="Garden furniture",
        category="garden-furniture",
        description_short="A premium test collection.",
        price_eur=Decimal("220.40"),
        cost_eur=Decimal("100.00"),
        markets=["de"],
    )
    variants = [
        Variant(
            id="v-low",
            product_id=product.id,
            sku="LOW",
            option_name="size",
            option_value="Small",
            price_eur=product.price_eur,
            inventory=10_000,
        ),
        Variant(
            id="v-high",
            product_id=expensive_product.id,
            sku="HIGH",
            option_name="material",
            option_value="Metal",
            price_eur=expensive_product.price_eur,
            inventory=10_000,
        ),
    ]
    customers = [
        Customer(
            id=f"c-{number:05d}",
            market="de",
            email=f"customer-{number}@example.test",
            first_name="Test",
            last_name="Customer",
            city="Berlin",
            created_at=datetime(2024, 12, 1, tzinfo=UTC),
            acquisition_channel="direct",
        )
        for number in range(customer_count)
    ]
    return [product, expensive_product], variants, customers


def _generated(seed: int = 42):
    products, variants, customers = _inputs()
    result = generate_orders(
        config=load_preset("garden-decor"),
        markets={"de": load_markets()["de"]},
        products=products,
        variants=variants,
        customers=customers,
        start_date=datetime(2025, 1, 1, tzinfo=UTC),
        months=12,
        rng=np.random.default_rng(seed),
        base_daily_orders=3,
    )
    return result, products, variants, customers


def test_foreign_keys_dates_markets_and_customer_lifecycle() -> None:
    result, products, variants, customers = _generated()
    customer_by_id = {customer.id: customer for customer in customers}
    variant_by_id = {variant.id: variant for variant in variants}
    product_by_id = {product.id: product for product in products}
    order_by_id = {order.id: order for order in result.orders}
    seen_customers: set[str] = set()

    assert result.orders
    assert result.order_items
    assert len(order_by_id) == len(result.orders)
    assert len({item.id for item in result.order_items}) == len(result.order_items)
    for order in result.orders:
        assert order.customer_id in customer_by_id
        assert customer_by_id[order.customer_id].created_at <= order.created_at
        assert datetime(2025, 1, 1, tzinfo=UTC) <= order.created_at
        assert order.created_at < datetime(2026, 1, 1, tzinfo=UTC)
        assert order.is_repeat == (order.customer_id in seen_customers)
        seen_customers.add(order.customer_id)
    for item in result.order_items:
        assert item.order_id in order_by_id
        assert item.variant_id in variant_by_id
        product = product_by_id[variant_by_id[item.variant_id].product_id]
        assert order_by_id[item.order_id].market in product.markets


def test_customer_created_during_window_cannot_order_early() -> None:
    products, variants, customers = _inputs(customer_count=1)
    created_at = datetime(2025, 1, 15, 12, tzinfo=UTC)
    customers[0] = customers[0].model_copy(update={"created_at": created_at})

    result = generate_orders(
        config=load_preset("garden-decor"),
        markets={"de": load_markets()["de"]},
        products=products,
        variants=variants,
        customers=customers,
        start_date=datetime(2025, 1, 1, tzinfo=UTC),
        months=1,
        rng=np.random.default_rng(10),
        base_daily_orders=20,
    )

    assert result.orders
    assert min(order.created_at for order in result.orders) >= created_at


def test_repeat_rate_tracks_preset_probability() -> None:
    result, _, _, _ = _generated()
    repeat_rate = sum(order.is_repeat for order in result.orders) / len(result.orders)

    assert Decimal("0.19") <= Decimal(str(repeat_rate)) <= Decimal("0.29")


def test_money_totals_discounts_and_psychological_prices_are_exact() -> None:
    result, _, _, _ = _generated()
    preset = load_preset("garden-decor")
    items_by_order = defaultdict(list)
    for item in result.order_items:
        items_by_order[item.order_id].append(item)

    assert any(item.unit_price >= 100 for item in result.order_items)
    for item in result.order_items:
        assert item.unit_price.as_tuple().exponent == -2
        if item.unit_price < 100:
            assert item.unit_price % 1 == Decimal("0.90")
        else:
            assert item.unit_price % 1 == 0
    for order in result.orders:
        expected_subtotal = sum(
            (item.unit_price * item.quantity for item in items_by_order[order.id]),
            Decimal("0.00"),
        )
        assert order.subtotal == expected_subtotal
        assert order.total == order.subtotal - order.discount + order.shipping + order.tax
        assert order.tax == (
            (order.subtotal - order.discount + order.shipping) * Decimal("0.19")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for amount in (
            order.subtotal,
            order.discount,
            order.shipping,
            order.tax,
            order.total,
        ):
            assert amount.as_tuple().exponent == -2
        if order.discount_code is None:
            assert order.discount == 0
        else:
            assert order.discount > 0
            assert order.subtotal * preset.discount.depth_range[0] - Decimal("0.01") <= (
                order.discount
            )
            assert order.discount <= (
                order.subtotal * preset.discount.depth_range[1] + Decimal("0.01")
            )
    discount_rate = sum(order.discount_code is not None for order in result.orders) / len(
        result.orders
    )
    assert 0.22 <= discount_rate <= 0.34


def test_inventory_never_goes_negative_and_inputs_are_not_mutated() -> None:
    result, _, variants, _ = _generated()
    sold = Counter()
    for item in result.order_items:
        sold[item.variant_id] += item.quantity

    assert all(quantity >= 0 for quantity in result.inventory.values())
    for variant in variants:
        assert result.inventory[variant.id] == variant.inventory - sold[variant.id]
        assert variant.inventory == 10_000


def test_same_seed_is_deterministic_and_different_seed_differs() -> None:
    first, _, _, _ = _generated(seed=7)
    second, _, _, _ = _generated(seed=7)
    different, _, _, _ = _generated(seed=8)

    assert first == second
    assert first != different


def test_garden_peak_month_has_more_orders_than_low_month() -> None:
    result, _, _, _ = _generated()
    monthly_counts = Counter(order.created_at.month for order in result.orders)

    assert monthly_counts[5] > monthly_counts[1]
