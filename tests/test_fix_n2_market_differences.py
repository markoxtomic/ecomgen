"""Report issues N2 and m3 (remainder): markets must behave differently.

Every market shared one CAC draw per channel (meta cost EUR 126.6131 in AT and
126.6124 in FR), one return rate and one repeat probability, so "which market is
most efficient" had no answer beyond noise. Markets now carry their own
multipliers.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from ecomgen.config import load_markets
from ecomgen.pipeline import generate_dataset

MARKETS = ("de", "ch", "uk")


def _dataset():
    return generate_dataset(markets=MARKETS, customers=6000, months=6, seed=19)


def test_market_config_carries_behaviour_multipliers() -> None:
    markets = load_markets()

    for code in ("de", "ch", "uk"):
        market = markets[code]
        assert market.cac_multiplier > 0
        assert market.return_rate_multiplier > 0
        assert market.repeat_multiplier > 0
    assert len({markets[code].cac_multiplier for code in markets}) > 1


def test_realised_cac_differs_between_markets() -> None:
    dataset = _dataset()
    spend: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    acquired: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in dataset.marketing_spend:
        spend[(row.market, row.channel)] += row.spend / row.fx_rate_from_eur
        acquired[(row.market, row.channel)] += row.new_customers

    cac = {
        key: spend[key] / acquired[key] for key in spend if key[1] == "meta" and acquired[key] > 50
    }
    assert len(cac) >= 2
    spread = max(cac.values()) / min(cac.values())
    assert spread > Decimal("1.05"), f"meta CAC is effectively identical across markets: {cac}"


def test_return_and_repeat_rates_follow_their_market_multipliers() -> None:
    dataset = _dataset()
    markets = load_markets()
    order_market = {order.id: order.market for order in dataset.orders}
    items = defaultdict(int)
    returned = defaultdict(int)
    for item in dataset.order_items:
        items[order_market[item.order_id]] += 1
    for record in dataset.returns:
        returned[order_market[record.order_id]] += 1

    rates = {market: returned[market] / items[market] for market in MARKETS}
    highest = max(MARKETS, key=lambda code: markets[code].return_rate_multiplier)
    lowest = min(MARKETS, key=lambda code: markets[code].return_rate_multiplier)
    assert rates[highest] > rates[lowest], rates

    repeat = defaultdict(list)
    for order in dataset.orders:
        repeat[order.market].append(order.is_repeat)
    shares = {market: sum(flags) / len(flags) for market, flags in repeat.items()}
    high = max(MARKETS, key=lambda code: markets[code].repeat_multiplier)
    low = min(MARKETS, key=lambda code: markets[code].repeat_multiplier)
    assert shares[high] > shares[low], shares
