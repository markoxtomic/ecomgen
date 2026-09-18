from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np

from ecomgen.config import load_markets, load_preset
from ecomgen.generators import generate_marketing
from ecomgen.schemas import Customer, Order


def _customer(number: int, channel: str) -> Customer:
    return Customer(
        id=f"customer-{number}",
        market="de",
        email=f"customer-{number}@example.test",
        first_name="Test",
        last_name="Customer",
        city="Berlin",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        acquisition_channel=channel,
    )


def _order(number: int, customer: Customer, day: int) -> Order:
    return Order(
        id=f"order-{number}",
        customer_id=customer.id,
        market=customer.market,
        created_at=datetime(2025, 1, day, 12, tzinfo=UTC),
        currency="EUR",
        subtotal=Decimal("50.00"),
        discount=Decimal("0.00"),
        shipping=Decimal("5.00"),
        tax=Decimal("10.45"),
        total=Decimal("65.45"),
        discount_code=None,
        is_repeat=False,
    )


def _inputs():
    channels = ["meta", "google", "organic", "email", "direct"]
    customers = [_customer(number, channel) for number, channel in enumerate(channels)]
    orders = []
    number = 0
    for customer in customers:
        for day, count in ((2, 1), (3, 10)):
            for _ in range(count):
                number += 1
                orders.append(_order(number, customer, day))
    return customers, orders


def test_daily_records_attribution_spend_correlation_and_funnel_plausibility() -> None:
    preset = load_preset("fashion")
    market = load_markets()["de"]
    customers, orders = _inputs()
    records = generate_marketing(
        preset,
        {"de": market},
        customers,
        orders,
        date(2025, 1, 1),
        1,
        np.random.default_rng(42),
    )
    by_key = {(record.date, record.market, record.channel): record for record in records}

    assert len(records) == 31 * 5
    for day_offset in range(31):
        day = date(2025, 1, 1) + timedelta(days=day_offset)
        assert {key[2] for key in by_key if key[:2] == (day, "de")} == {
            "meta",
            "google",
            "organic",
            "email",
            "direct",
        }

    for channel in ("meta", "google", "email", "organic", "direct"):
        assert by_key[(date(2025, 1, 2), "de", channel)].attributed_orders == 1
        assert by_key[(date(2025, 1, 3), "de", channel)].attributed_orders == 10

    for channel in ("meta", "google", "email"):
        low = by_key[(date(2025, 1, 2), "de", channel)]
        high = by_key[(date(2025, 1, 3), "de", channel)]
        assert low.spend > 0
        assert high.spend > low.spend
        cac_low, cac_high = preset.channel_mix[channel].cac_range
        assert cac_low <= low.spend / low.attributed_orders <= cac_high
        assert cac_low <= high.spend / high.attributed_orders <= cac_high

    for channel in ("organic", "direct"):
        assert by_key[(date(2025, 1, 2), "de", channel)].spend == 0
        assert by_key[(date(2025, 1, 3), "de", channel)].spend == 0

    for record in records:
        assert record.impressions >= record.clicks >= record.attributed_orders
        assert record.spend.as_tuple().exponent == -2
        if record.attributed_orders == 0:
            assert (record.impressions, record.clicks, record.spend) == (0, 0, 0)


def test_marketing_is_deterministic_and_attribution_sums_to_orders() -> None:
    preset = load_preset("fashion")
    markets = {"de": load_markets()["de"]}
    customers, orders = _inputs()
    arguments = (preset, markets, customers, orders, date(2025, 1, 1), 1)

    first = generate_marketing(*arguments, np.random.default_rng(7))
    second = generate_marketing(*arguments, np.random.default_rng(7))
    different = generate_marketing(*arguments, np.random.default_rng(8))

    assert first == second
    assert first != different
    assert sum(record.attributed_orders for record in first) == len(orders)
    paid_attributed = sum(
        record.attributed_orders
        for record in first
        if record.channel in {"meta", "google", "email"}
    )
    expected_paid = sum(
        customer.acquisition_channel in {"meta", "google", "email"}
        for customer in customers
        for _ in range(11)
    )
    assert paid_attributed == expected_paid


def test_marketing_honors_selected_markets_and_partial_day_window() -> None:
    preset = load_preset("fashion")
    markets = load_markets()
    selected_customer = _customer(1, "google")
    ignored_customer = _customer(2, "meta").model_copy(update={"market": "at"})
    selected_order = _order(1, selected_customer, 1).model_copy(
        update={"created_at": datetime(2025, 2, 15, 10, tzinfo=UTC)}
    )
    ignored_order = _order(2, ignored_customer, 1).model_copy(
        update={
            "market": "at",
            "created_at": datetime(2025, 1, 20, 10, tzinfo=UTC),
        }
    )

    records = generate_marketing(
        preset,
        {"de": markets["de"]},
        [selected_customer, ignored_customer],
        [selected_order, ignored_order],
        datetime(2025, 1, 15, 12, tzinfo=UTC),
        1,
        np.random.default_rng(3),
    )

    assert len(records) == 32 * 5
    assert {record.market for record in records} == {"de"}
    assert sum(record.attributed_orders for record in records) == 1
    assert (
        next(
            record
            for record in records
            if record.date == date(2025, 2, 15) and record.channel == "google"
        ).attributed_orders
        == 1
    )
