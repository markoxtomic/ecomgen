"""Regression tests for configured repeat intervals and cohort retention."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from statistics import mean

import pytest

from ecomgen import pipeline
from ecomgen.config import load_preset
from ecomgen.config.models import RepeatPurchaseConfig


def _repeat_intervals(dataset) -> list[float]:
    by_customer: defaultdict[str, list[datetime]] = defaultdict(list)
    for order in dataset.orders:
        by_customer[order.customer_id].append(order.created_at)
    intervals = []
    for timestamps in by_customer.values():
        timestamps.sort()
        intervals.extend(
            (later - earlier).total_seconds() / 86400 for earlier, later in pairwise(timestamps)
        )
    return intervals


@pytest.mark.parametrize("average_days", [60, 150])
def test_mean_repeat_interval_tracks_average_days(
    monkeypatch: pytest.MonkeyPatch, average_days: int
) -> None:
    config = load_preset("garden-decor")
    config = config.model_copy(
        update={
            "repeat_purchase": RepeatPurchaseConfig(
                probability=Decimal("0.30"), average_days=average_days
            )
        }
    )
    monkeypatch.setattr(pipeline, "load_preset", lambda name: config)
    dataset = pipeline.generate_dataset(markets=("de",), customers=4000, months=36)

    intervals = _repeat_intervals(dataset)
    assert len(intervals) > 300
    assert 0.8 * average_days <= mean(intervals) <= 1.2 * average_days


def _month_one_retention(dataset, cohort_months: set[int]) -> float:
    orders_by_customer: defaultdict[str, list[datetime]] = defaultdict(list)
    for order in dataset.orders:
        orders_by_customer[order.customer_id].append(order.created_at)
    cohort = retained = 0
    for timestamps in orders_by_customer.values():
        first = min(timestamps)
        if first.month not in cohort_months:
            continue
        cohort += 1
        retained += any(
            timestamp.month == first.month + 1 and timestamp.year == first.year
            for timestamp in timestamps
        )
    return retained / cohort


def test_month_one_retention_does_not_collapse_as_the_buyer_pool_grows() -> None:
    dataset = pipeline.generate_dataset(customers=15_000, months=12)

    early = _month_one_retention(dataset, {1, 2, 3})
    late = _month_one_retention(dataset, {7, 8, 9})
    assert early > 0.01
    assert 0.6 <= late / early <= 1.6
