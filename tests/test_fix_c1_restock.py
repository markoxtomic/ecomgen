"""C1 regression: stock is replenished and suppressed demand is never silent."""

import warnings

import pytest
from pydantic import ValidationError

from ecomgen import pipeline
from ecomgen.config import load_preset
from ecomgen.config.models import InventoryConfig
from ecomgen.errors import GenerationWarning, StockoutError
from ecomgen.generators.orders import check_stockouts


def test_large_default_run_has_orders_in_every_month() -> None:
    # Before the fix every variant sold out by mid-2024 and 28 of 36 months were empty.
    with warnings.catch_warnings():
        warnings.simplefilter("error", GenerationWarning)
        dataset = pipeline.generate_dataset(customers=50_000, months=36)

    months = {order.created_at.strftime("%Y-%m") for order in dataset.orders}
    assert len(months) == 36
    by_month: dict[str, int] = {}
    for order in dataset.orders:
        key = order.created_at.strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0) + 1
    # The last year still sells: no gradual dead tail either.
    assert min(count for month, count in by_month.items() if month >= "2026-01") > 300


def _with_inventory(monkeypatch: pytest.MonkeyPatch, **policy: int) -> None:
    config = load_preset("garden-decor").model_copy(update={"inventory": InventoryConfig(**policy)})
    monkeypatch.setattr(pipeline, "load_preset", lambda name: config)


def test_starved_restock_warns_at_soft_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    # Single-unit purchase orders with a 90-day lead time drop about 14% of orders.
    _with_inventory(
        monkeypatch, reorder_point=0, restock_quantity=1, lead_time_days=90, cover_days=0
    )

    with pytest.warns(GenerationWarning, match=r"stock-outs dropped .* intended orders"):
        dataset = pipeline.generate_dataset(customers=20_000, months=12)

    assert dataset.orders


def test_starved_restock_raises_at_hard_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    # A one-year lead time means stock is effectively never replenished.
    _with_inventory(
        monkeypatch, reorder_point=0, restock_quantity=1, lead_time_days=365, cover_days=0
    )

    with pytest.raises(StockoutError, match=r"above the 25% limit"):
        pipeline.generate_dataset(customers=20_000, months=12)


def test_stockout_thresholds() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        check_stockouts(0, 0)
        check_stockouts(1000, 50)  # exactly 5% is tolerated
    with pytest.warns(GenerationWarning):
        check_stockouts(1000, 51)
    with pytest.warns(GenerationWarning):
        check_stockouts(1000, 250)  # exactly 25% still only warns
    with pytest.raises(StockoutError):
        check_stockouts(1000, 251)


def test_inventory_policy_defaults_and_strict_validation() -> None:
    preset = load_preset("garden-decor")
    assert preset.inventory.restock_quantity >= 1
    assert InventoryConfig() == InventoryConfig.model_validate({})

    for bad in (
        {"restock_quantity": 0},
        {"lead_time_days": 0},
        {"reorder_point": -1},
        {"cover_days": -1},
        {"restock_every": 7},
    ):
        with pytest.raises(ValidationError):
            InventoryConfig.model_validate(bad)
