"""M2 regression: spend drives acquisition instead of being back-computed from orders."""

from collections import Counter, defaultdict
from decimal import Decimal
from functools import cache

import numpy as np
import pytest

from ecomgen import pipeline
from ecomgen.config import load_markets, load_preset
from ecomgen.config.models import RepeatPurchaseConfig
from ecomgen.schemas import Dataset, MarketingSpend

PAID = ("email", "google", "meta")
UNPAID = ("direct", "organic")


@cache
def _dataset(preset: str = "garden-decor", markets: tuple[str, ...] = ("de", "at", "fr")):
    return pipeline.generate_dataset(preset=preset, markets=markets, customers=5000, months=12)


def _channel_economics(dataset: Dataset) -> dict[str, dict[str, Decimal]]:
    """Revenue, costs and spend (EUR) of the customers each channel acquired."""

    fx = {code: market.fx_rate_from_eur for code, market in load_markets().items()}
    channel_of = {customer.id: customer.acquisition_channel for customer in dataset.customers}
    order_by_id = {order.id: order for order in dataset.orders}
    product_of_variant = {variant.id: variant.product_id for variant in dataset.variants}
    cost_of_product = {product.id: product.cost_eur for product in dataset.products}
    item_by_id = {item.id: item for item in dataset.order_items}
    result: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))

    for order in dataset.orders:
        channel = channel_of[order.customer_id]
        result[channel]["revenue"] += (order.total - order.tax) / fx[order.market]
    for item in dataset.order_items:
        order = order_by_id[item.order_id]
        cost = cost_of_product[product_of_variant[item.variant_id]] * item.quantity
        result[channel_of[order.customer_id]]["cogs"] += cost
    for returned in dataset.returns:
        order = order_by_id[item_by_id[returned.order_item_id].order_id]
        refund = returned.refund_amount / fx[order.market]
        result[channel_of[order.customer_id]]["refunds"] += refund
    for row in dataset.marketing_spend:
        result[row.channel]["spend"] += row.spend / fx[row.market]
    for values in result.values():
        values["contribution"] = (
            values["revenue"] - values["cogs"] - values["refunds"] - values["spend"]
        )
    return result


def test_marketing_schema_has_currency_and_new_customers_only() -> None:
    assert list(MarketingSpend.model_fields) == [
        "date",
        "market",
        "channel",
        "currency",
        "spend",
        "impressions",
        "clicks",
        "new_customers",
    ]


def test_new_customers_reconcile_with_customers_and_funnel_holds() -> None:
    dataset = _dataset("garden-decor", ("de", "ch", "uk"))
    markets = load_markets()
    acquired = Counter(
        (customer.created_at.date(), customer.market, customer.acquisition_channel)
        for customer in dataset.customers
    )
    rows = {(row.date, row.market, row.channel): row for row in dataset.marketing_spend}

    assert len(rows) == len(dataset.marketing_spend)
    assert set(acquired) <= set(rows)
    assert sum(row.new_customers for row in rows.values()) == len(dataset.customers) == 5000
    for key, row in rows.items():
        assert row.new_customers == acquired[key]
        assert row.currency == markets[row.market].currency
        if row.channel in PAID:
            assert row.impressions >= row.clicks >= row.new_customers
        else:
            assert (row.spend, row.impressions, row.clicks) == (0, 0, 0)
    # Paid campaigns run every day instead of going dark whenever no order happens.
    paid_rows = [row for row in rows.values() if row.channel in PAID]
    assert sum(row.spend > 0 for row in paid_rows) / len(paid_rows) > 0.95


def test_roas_varies_meaningfully_across_paid_channels() -> None:
    economics = _channel_economics(_dataset())
    roas = {
        channel: economics[channel]["revenue"] / economics[channel]["spend"] for channel in PAID
    }

    assert max(roas.values()) / min(roas.values()) > 3
    assert min(roas.values()) < 2  # below break-even for a ~45% COGS catalogue


@pytest.mark.parametrize("preset", ["electronics", "fashion", "garden-decor"])
def test_each_bundled_preset_has_an_unprofitable_paid_channel(preset: str) -> None:
    economics = _channel_economics(_dataset(preset))
    contributions = {channel: economics[channel]["contribution"] for channel in PAID}

    assert min(contributions.values()) < 0, contributions
    assert max(contributions.values()) > 0, contributions


def test_spend_is_not_a_function_of_order_or_customer_counts() -> None:
    dataset = _dataset()
    orders_per_day = Counter(order.created_at.date() for order in dataset.orders)
    for channel in ("meta", "google"):
        rows = [row for row in dataset.marketing_spend if row.channel == channel]
        spend = np.array([float(row.spend) for row in rows])
        acquired = np.array([row.new_customers for row in rows])
        orders = np.array([orders_per_day[row.date] for row in rows])
        assert np.corrcoef(spend, acquired)[0, 1] < 0.9
        assert np.corrcoef(spend, orders)[0, 1] < 0.9
        # Rows with the same number of new customers carry different spend.
        spend_by_count: defaultdict[int, set[Decimal]] = defaultdict(set)
        for row in rows:
            spend_by_count[row.new_customers].add(row.spend)
        assert any(len(values) > 1 for values in spend_by_count.values())


def test_repeat_orders_carry_no_acquisition_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    base = load_preset("garden-decor")
    variants = {}
    for probability in ("0.05", "0.45"):
        config = base.model_copy(
            update={
                "repeat_purchase": RepeatPurchaseConfig(
                    probability=Decimal(probability), average_days=105
                )
            }
        )
        monkeypatch.setattr(pipeline, "load_preset", lambda name, config=config: config)
        variants[probability] = pipeline.generate_dataset(
            markets=("de",), customers=2000, months=12
        )

    low, high = variants["0.05"], variants["0.45"]
    assert sum(o.is_repeat for o in high.orders) > 3 * sum(o.is_repeat for o in low.orders)
    # Spend depends only on acquisition, so many more repeat orders cost nothing extra.
    assert low.marketing_spend == high.marketing_spend
