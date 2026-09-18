from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from ecomgen.accounting import item_paid_values
from ecomgen.pipeline import generate_dataset
from ecomgen.schemas import Dataset, Return
from ecomgen.validation import validate_dataset


def _dataset(preset: str, seed: int) -> Dataset:
    return generate_dataset(
        preset=preset,
        markets=("de", "fr"),
        customers=300,
        months=6,
        start_date=date(2024, 1, 1),
        seed=seed,
    )


@pytest.mark.parametrize("preset", ["electronics", "fashion", "garden-decor"])
@pytest.mark.parametrize("seed", [1, 42])
def test_generated_refunds_never_exceed_item_value_cumulatively(preset: str, seed: int) -> None:
    dataset = _dataset(preset, seed)
    items = {item.id: item for item in dataset.order_items}
    paid_values: dict[str, Decimal] = {}
    for order in dataset.orders:
        related_items = [item for item in dataset.order_items if item.order_id == order.id]
        paid_values.update(item_paid_values(order, related_items))
    refunded: defaultdict[str, Decimal] = defaultdict(Decimal)
    for returned in dataset.returns:
        refunded[returned.order_item_id] += returned.refund_amount

    assert dataset.returns
    assert max(Counter(r.order_item_id for r in dataset.returns).values()) == 1
    for item_id, total in refunded.items():
        assert item_id in items
        assert total <= paid_values[item_id]
    assert validate_dataset(dataset).valid


def test_returns_are_strictly_after_orders_and_within_the_extended_window() -> None:
    dataset = _dataset("fashion", 42)
    orders = {order.id: order for order in dataset.orders}
    order_window_end = datetime(2024, 7, 1, tzinfo=UTC)
    return_cutoff = order_window_end + timedelta(days=30)

    assert dataset.returns
    for returned in dataset.returns:
        assert returned.created_at > orders[returned.order_id].created_at
        assert returned.created_at <= return_cutoff


def _with_split_refund(dataset: Dataset, *shares: Decimal) -> tuple[Dataset, str, Decimal]:
    """Replace the returns table with several returns against one order item."""

    item = dataset.order_items[0]
    order = next(order for order in dataset.orders if order.id == item.order_id)
    related_items = [
        candidate for candidate in dataset.order_items if candidate.order_id == order.id
    ]
    item_value = item_paid_values(order, related_items)[item.id]
    returns = [
        Return(
            id=f"ret-crafted-{number}",
            order_id=order.id,
            order_item_id=item.id,
            reason="changed_mind",
            refund_amount=(item_value * share).quantize(Decimal("0.01")),
            created_at=order.created_at + timedelta(days=5 + number),
        )
        for number, share in enumerate(shares)
    ]
    return replace(dataset, returns=returns), item.id, item_value


def test_validation_rejects_cumulative_over_refund() -> None:
    # Each return alone is within the item value (and passed the old per-return
    # check), but together they refund 140% of it.
    dataset, item_id, item_value = _with_split_refund(
        _dataset("fashion", 42), Decimal("0.7"), Decimal("0.7")
    )
    assert all(r.refund_amount <= item_value for r in dataset.returns)
    cumulative = sum((r.refund_amount for r in dataset.returns), Decimal(0))

    report = validate_dataset(dataset)

    assert not report.valid
    expected_value = item_value.quantize(Decimal("0.01"))
    assert report.errors == [
        f"order item {item_id}: cumulative refunds {cumulative} exceed item value {expected_value}"
    ]


def test_validation_accepts_split_refunds_within_item_value() -> None:
    dataset, _, _ = _with_split_refund(_dataset("fashion", 42), Decimal("0.25"), Decimal("0.5"))

    assert validate_dataset(dataset).valid
