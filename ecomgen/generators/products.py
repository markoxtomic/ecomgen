"""Deterministic product and variant catalog generation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np

from ecomgen.config.models import CategoryConfig, MarketConfig, PresetConfig
from ecomgen.schemas import Product, Variant

PRODUCTS_PER_CATEGORY = 12
_CENT = Decimal("0.01")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    """Return a compact ASCII identifier component."""

    slug = _NON_ALNUM.sub("-", value.casefold()).strip("-")
    return slug or "item"


def _market_values(
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig],
) -> tuple[MarketConfig, ...]:
    if isinstance(markets, Mapping):
        mismatches = [key for key, market in markets.items() if key.casefold() != market.code]
        if mismatches:
            raise ValueError(f"market keys must match market codes: {mismatches}")
        values = tuple(markets.values())
    else:
        values = tuple(markets)

    if not values:
        raise ValueError("at least one market is required")

    ordered = tuple(sorted(values, key=lambda market: market.code))
    codes = [market.code for market in ordered]
    if len(codes) != len(set(codes)):
        raise ValueError("market codes must be unique")
    return ordered


def _random_decimal(
    low: Decimal,
    high: Decimal,
    rng: np.random.Generator,
) -> Decimal:
    low_cents = int((low / _CENT).to_integral_value(rounding=ROUND_CEILING))
    high_cents = int((high / _CENT).to_integral_value(rounding=ROUND_FLOOR))
    if low_cents > high_cents:
        raise ValueError("range does not contain a whole cent value")
    return Decimal(int(rng.integers(low_cents, high_cents + 1))) * _CENT


def _cost_for_price(
    price: Decimal,
    category: CategoryConfig,
    rng: np.random.Generator,
) -> Decimal:
    low_ratio, high_ratio = category.cost_ratio_range
    low_cents = int((price * low_ratio / _CENT).to_integral_value(rounding=ROUND_CEILING))
    high_cents = int((price * high_ratio / _CENT).to_integral_value(rounding=ROUND_FLOOR))
    if low_cents > high_cents:
        # This is only possible for unusually tiny configured prices.
        return (price * low_ratio).quantize(_CENT)
    return Decimal(int(rng.integers(low_cents, high_cents + 1))) * _CENT


def generate_products(
    config: PresetConfig,
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig],
    rng: np.random.Generator,
) -> tuple[list[Product], list[Variant]]:
    """Generate a product catalog using only the supplied random generator.

    Twelve products are produced for each category. Each product uses one of
    the category's configured option dimensions and has a variant for every
    configured value in that dimension.
    """

    market_values = _market_values(markets)
    market_codes = [market.code for market in market_values]
    products: list[Product] = []
    variants: list[Variant] = []
    preset_slug = _slug(config.name)
    used_titles: set[str] = set()

    for category_name, category in sorted(config.categories.items()):
        category_slug = _slug(category_name)
        option_names = tuple(sorted(category.variant_options))
        words = category.title_words
        title_parts = [
            (adjective, material, noun)
            for adjective in words.adjectives
            for material in words.materials
            for noun in words.nouns
        ]
        title_order = rng.permutation(len(title_parts))
        category_titles: list[tuple[str, str, str]] = []
        for title_index in title_order:
            parts = title_parts[int(title_index)]
            normalized = " ".join(parts).casefold()
            if normalized in used_titles:
                continue
            used_titles.add(normalized)
            category_titles.append(parts)
            if len(category_titles) == PRODUCTS_PER_CATEGORY:
                break
        if len(category_titles) < PRODUCTS_PER_CATEGORY:
            raise ValueError(
                f"category {category_name!r} needs at least {PRODUCTS_PER_CATEGORY} "
                "globally unique title combinations"
            )

        for number in range(1, PRODUCTS_PER_CATEGORY + 1):
            product_id = f"prod-{preset_slug}-{category_slug}-{number:03d}"
            adjective, material, noun = category_titles[number - 1]
            title = f"{adjective} {material} {noun}"
            price = _random_decimal(*category.price_range, rng)

            products.append(
                Product(
                    id=product_id,
                    title=title,
                    category=category_name,
                    description_short=(
                        f"A {adjective.casefold()} {material.casefold()} "
                        f"{noun.casefold()} designed for reliable everyday use."
                    ),
                    price_eur=price,
                    cost_eur=_cost_for_price(price, category, rng),
                    markets=market_codes.copy(),
                )
            )

            option_name = option_names[(number - 1) % len(option_names)]
            for value_number, option_value in enumerate(
                category.variant_options[option_name], start=1
            ):
                variant_id = f"var-{preset_slug}-{category_slug}-{number:03d}-{value_number:02d}"
                sku = (
                    f"{preset_slug}-{category_slug}-{number:03d}-"
                    f"{_slug(option_name)}-{_slug(option_value)}"
                ).upper()
                inventory = int(rng.integers(25, 251))
                variants.append(
                    Variant(
                        id=variant_id,
                        product_id=product_id,
                        sku=sku,
                        option_name=option_name,
                        option_value=option_value,
                        price_eur=price,
                        inventory=inventory,
                    )
                )

    return products, variants
