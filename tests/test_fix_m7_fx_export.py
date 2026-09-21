"""Report issue m7 (remainder): the export must carry its own FX rates.

Money lives in each market's currency, but the conversion rates existed only in the
package's YAML, so a BI consumer could not convert CHF/GBP revenue or compare it with
the EUR `cost_eur` without reaching into the source tree. Contribution margin for
non-EUR markets was simply left NULL.
"""

from __future__ import annotations

import csv
from decimal import Decimal

from ecomgen.config import load_markets
from ecomgen.pipeline import generate_dataset

MARKETS = ("de", "uk", "ch")


def _dataset():
    return generate_dataset(markets=MARKETS, customers=600, months=3, seed=13)


def test_orders_carry_the_rate_used_to_price_them() -> None:
    configured = load_markets()
    dataset = _dataset()

    assert dataset.orders
    for order in dataset.orders:
        assert order.fx_rate_from_eur == configured[order.market].fx_rate_from_eur


def test_marketing_rows_carry_the_same_rate() -> None:
    configured = load_markets()

    for row in _dataset().marketing_spend:
        assert row.fx_rate_from_eur == configured[row.market].fx_rate_from_eur


def test_revenue_converts_to_eur_without_external_lookups(tmp_path) -> None:
    from ecomgen.exporters import export_dataset

    dataset = _dataset()
    export_dataset(dataset, tmp_path / "out", formats=("csv",), arguments={"seed": 13})
    with (tmp_path / "out" / "orders.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert {row["currency"] for row in rows} >= {"EUR", "GBP", "CHF"}
    total_eur = sum(Decimal(row["total"]) / Decimal(row["fx_rate_from_eur"]) for row in rows)
    assert total_eur > 0
    # A GBP order divided by its rate must exceed its nominal amount (GBP < EUR).
    gbp = next(row for row in rows if row["currency"] == "GBP")
    assert Decimal(gbp["total"]) / Decimal(gbp["fx_rate_from_eur"]) > Decimal(gbp["total"])
