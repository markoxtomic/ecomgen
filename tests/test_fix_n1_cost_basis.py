"""Report v2 issue N1: cost ratios must apply to the net price, not the gross one.

`price_eur` is a gross, VAT-inclusive list price. Applying `cost_ratio_range` to it
made the realised cost ratio against net revenue 1.19x the configured band, so every
margin in the dataset was systematically too thin.
"""

from decimal import Decimal

import numpy as np

from ecomgen.config import load_markets, load_preset
from ecomgen.generators.products import CATALOG_VAT_RATE, generate_products


def _catalog(preset_name: str):
    config = load_preset(preset_name)
    markets = load_markets()
    products, _ = generate_products(config, {"de": markets["de"]}, np.random.default_rng(42))
    return config, products


def test_cost_ratio_applies_to_the_net_catalog_price() -> None:
    for preset_name in ("garden-decor", "fashion", "electronics"):
        config, products = _catalog(preset_name)
        for product in products:
            low, high = config.categories[product.category].cost_ratio_range
            net_price = product.price_eur / (1 + CATALOG_VAT_RATE)
            ratio = product.cost_eur / net_price
            assert low - Decimal("0.02") <= ratio <= high + Decimal("0.02"), (
                f"{preset_name}/{product.id}: cost ratio {ratio:.3f} outside "
                f"configured [{low}, {high}] against the net price"
            )


def test_gross_margin_on_net_revenue_matches_the_configured_band() -> None:
    config, products = _catalog("garden-decor")
    for category, settings in config.categories.items():
        in_category = [p for p in products if p.category == category]
        net_revenue = sum(p.price_eur / (1 + CATALOG_VAT_RATE) for p in in_category)
        cogs = sum(p.cost_eur for p in in_category)
        realised = cogs / net_revenue
        low, high = settings.cost_ratio_range
        assert low <= realised <= high, (
            f"{category}: realised COGS share of net revenue {realised:.3f} outside [{low}, {high}]"
        )
