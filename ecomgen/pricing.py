"""Gross list prices shared by order generation and the Shopify export.

Catalog prices are gross (VAT-inclusive). A market's shelf price is the catalog
price converted with that market's FX rate and rounded the way shops price: a
`.90` ending below 100, a whole unit at or above it. Exports that quote a price
to a customer use these helpers, so a store imported from `products_shopify.csv`
charges exactly what the generated orders charge.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ecomgen.config.models import MarketConfig

_CENT = Decimal("0.01")
_WHOLE = Decimal(1)


def round_list_price(amount: Decimal) -> Decimal:
    """Round a gross amount to the nearest shelf price."""

    if amount < Decimal(100):
        rounded = (amount + Decimal("0.10")).quantize(_WHOLE, rounding=ROUND_HALF_UP) - Decimal(
            "0.10"
        )
        return max(Decimal("0.90"), rounded).quantize(_CENT)
    return amount.quantize(_WHOLE, rounding=ROUND_HALF_UP).quantize(_CENT)


def local_list_price(price_eur: Decimal, market: MarketConfig) -> Decimal:
    """Return one market's gross shelf price for a EUR catalog price."""

    return round_list_price(price_eur * market.fx_rate_from_eur)
