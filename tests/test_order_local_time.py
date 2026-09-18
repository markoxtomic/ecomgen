from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np

from ecomgen.config import load_markets, load_preset
from ecomgen.generators import generate_orders
from ecomgen.schemas import Customer, Product, Variant


def test_orders_follow_nonuniform_market_local_hour_profiles() -> None:
    market_codes = ("de", "uk")
    all_markets = load_markets()
    markets = {code: all_markets[code] for code in market_codes}
    for market in markets.values():
        assert hasattr(market, "timezone"), f"market {market.code!r} must define an IANA timezone"
        assert hasattr(market, "order_hour_weights"), (
            f"market {market.code!r} must define local order-hour weights"
        )
        assert len(market.order_hour_weights) == 24
        assert len(set(market.order_hour_weights)) > 1
        ZoneInfo(market.timezone)

    product = Product(
        id="product-all-markets",
        title="Sculpted Stone Planter",
        category="planters",
        description_short="A test product available in every selected market.",
        price_eur=Decimal("49.90"),
        cost_eur=Decimal("20.00"),
        markets=list(market_codes),
    )
    variant = Variant(
        id="variant-all-markets",
        product_id=product.id,
        sku="ALL-MARKETS",
        option_name="size",
        option_value="Medium",
        price_eur=product.price_eur,
        inventory=100_000,
    )
    customers = [
        Customer(
            id=f"customer-{market}-{number:04d}",
            market=market,
            email=f"{market}-{number}@example.test",
            first_name="Test",
            last_name="Customer",
            city="Test City",
            created_at=datetime(2024, 12, 1, tzinfo=UTC),
            acquisition_channel="direct",
        )
        for market in market_codes
        for number in range(3_000)
    ]

    result = generate_orders(
        config=load_preset("garden-decor"),
        markets=markets,
        products=[product],
        variants=[variant],
        customers=customers,
        start_date=date(2025, 1, 1),
        months=3,
        rng=np.random.default_rng(42),
        base_daily_orders=20,
    )

    for code, market in markets.items():
        local_hours = Counter(
            order.created_at.astimezone(ZoneInfo(market.timezone)).hour
            for order in result.orders
            if order.market == code
        )
        ranked_hours = sorted(
            range(24),
            key=lambda hour: (market.order_hour_weights[hour], hour),
        )
        quiet_hours = ranked_hours[:6]
        peak_hours = ranked_hours[-6:]

        assert sum(local_hours[hour] for hour in peak_hours) > sum(
            local_hours[hour] for hour in quiet_hours
        )
