"""M3 regression: order generation must not scan every customer for every order."""

import time
from datetime import date

from ecomgen.pipeline import generate_dataset

# 50k customers / 12 months in one market took 143-227 s with the O(orders x customers)
# candidate scan. The linear implementation needs well under 30 s; 120 s is a generous
# ceiling that still fails loudly on a regression to quadratic behaviour.
CEILING_SECONDS = 120


def test_50k_customers_single_market_completes_under_ceiling() -> None:
    started = time.perf_counter()
    dataset = generate_dataset(markets=("de",), customers=50_000, months=12)
    elapsed = time.perf_counter() - started

    assert len(dataset.customers) == 50_000
    assert dataset.orders
    assert elapsed < CEILING_SECONDS, f"generation took {elapsed:.1f}s"


def test_progress_is_reported_per_simulated_day() -> None:
    calls: list[tuple[int, int]] = []

    generate_dataset(
        markets=("de",),
        customers=50,
        months=2,
        start_date=date(2024, 1, 1),
        progress=lambda done, total: calls.append((done, total)),
    )

    # 60 simulated days in Jan+Feb 2024: expect at least one call per day.
    assert len(calls) >= 60
    totals = {total for _, total in calls}
    assert len(totals) == 1
    done_values = [done for done, _ in calls]
    assert done_values == sorted(done_values)
    assert calls[-1][0] == calls[-1][1]
