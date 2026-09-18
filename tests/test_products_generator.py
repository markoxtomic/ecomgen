from decimal import Decimal

import numpy as np

from ecomgen.config import load_markets, load_preset
from ecomgen.generators import generate_products


def _generated(seed: int = 42):
    return generate_products(load_preset("fashion"), load_markets(), np.random.default_rng(seed))


def test_ids_skus_and_foreign_keys_are_valid() -> None:
    products, variants = _generated()
    product_ids = {product.id for product in products}

    assert len(product_ids) == len(products)
    assert len({variant.id for variant in variants}) == len(variants)
    assert len({variant.sku for variant in variants}) == len(variants)
    assert variants
    assert all(variant.product_id in product_ids for variant in variants)
    assert product_ids == {variant.product_id for variant in variants}


def test_prices_costs_options_and_markets_follow_configuration() -> None:
    preset = load_preset("fashion")
    market_codes = set(load_markets())
    products, variants = _generated()
    product_by_id = {product.id: product for product in products}

    for product in products:
        category = preset.categories[product.category]
        assert category.price_range[0] <= product.price_eur <= category.price_range[1]
        assert product.price_eur * category.cost_ratio_range[0] <= product.cost_eur
        assert product.cost_eur <= product.price_eur * category.cost_ratio_range[1]
        assert product.markets
        assert set(product.markets) <= market_codes
        assert len(product.markets) == len(set(product.markets))

    for variant in variants:
        product = product_by_id[variant.product_id]
        options = preset.categories[product.category].variant_options
        assert variant.option_name in options
        assert variant.option_value in options[variant.option_name]
        assert variant.price_eur == product.price_eur


def test_generation_is_deterministic_for_mapping_or_list_markets() -> None:
    preset = load_preset("fashion")
    markets = load_markets()

    first = generate_products(preset, markets, np.random.default_rng(7))
    second = generate_products(
        preset,
        list(reversed(markets.values())),
        np.random.default_rng(7),
    )

    assert first == second


def test_inventory_is_nonnegative_and_ample() -> None:
    _, variants = _generated()
    inventories = [variant.inventory for variant in variants]

    assert all(inventory >= 0 for inventory in inventories)
    assert min(inventories) >= 25
    assert sum(inventories) >= Decimal(25) * len(inventories)
