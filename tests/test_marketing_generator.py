from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np

from ecomgen.config import load_markets, load_preset
from ecomgen.generators import generate_marketing

CHANNELS = {"meta", "google", "organic", "email", "direct"}


def test_daily_records_spend_acquisition_and_funnel_plausibility() -> None:
    preset = load_preset("fashion")
    market = load_markets()["de"]
    plan = generate_marketing(
        preset,
        {"de": market},
        600,
        date(2025, 1, 1),
        1,
        np.random.default_rng(42),
    )
    records = plan.records
    by_key = {(record.date, record.market, record.channel): record for record in records}

    assert len(records) == 31 * 5
    for day_offset in range(31):
        day = date(2025, 1, 1) + timedelta(days=day_offset)
        assert {key[2] for key in by_key if key[:2] == (day, "de")} == CHANNELS

    assert sum(record.new_customers for record in records) == 600
    for channel in ("meta", "google", "email"):
        rows = [record for record in records if record.channel == channel]
        assert all(record.spend > 0 for record in rows)
        # Spend buys customers at roughly the configured CAC over the month.
        cac_low, cac_high = preset.channel_mix[channel].cac_range
        cac = sum(record.spend for record in rows) / sum(record.new_customers for record in rows)
        assert cac_low * Decimal("0.6") <= cac <= cac_high * Decimal("1.6")

    for channel in ("organic", "direct"):
        rows = [record for record in records if record.channel == channel]
        assert all(record.spend == 0 for record in rows)
        assert sum(record.new_customers for record in rows) > 0

    for record in records:
        if record.channel in {"meta", "google", "email"}:
            assert record.impressions >= record.clicks >= record.new_customers
        assert record.spend.as_tuple().exponent == -2
        assert record.currency == "EUR"
        if record.channel in {"organic", "direct"}:
            assert (record.impressions, record.clicks, record.spend) == (0, 0, 0)

    assert sum(plan.acquisitions.values()) == 600
    for (market_code, day, channel), count in plan.acquisitions.items():
        assert by_key[(day, market_code, channel)].new_customers == count


def test_marketing_is_deterministic_and_acquisitions_sum_to_customers() -> None:
    preset = load_preset("fashion")
    markets = {"de": load_markets()["de"], "uk": load_markets()["uk"]}
    arguments = (preset, markets, 777, date(2025, 1, 1), 2)

    first = generate_marketing(*arguments, np.random.default_rng(7))
    second = generate_marketing(*arguments, np.random.default_rng(7))
    different = generate_marketing(*arguments, np.random.default_rng(8))

    assert first == second
    assert first != different
    assert sum(record.new_customers for record in first.records) == 777
    assert sum(first.acquisitions.values()) == 777
    assert {record.currency for record in first.records if record.market == "uk"} == {"GBP"}


def test_marketing_honors_selected_markets_and_partial_day_window() -> None:
    preset = load_preset("fashion")
    markets = load_markets()

    plan = generate_marketing(
        preset,
        {"de": markets["de"]},
        40,
        datetime(2025, 1, 15, 12, tzinfo=UTC),
        1,
        np.random.default_rng(3),
    )

    assert len(plan.records) == 32 * 5
    assert {record.market for record in plan.records} == {"de"}
    assert min(record.date for record in plan.records) == date(2025, 1, 15)
    assert max(record.date for record in plan.records) == date(2025, 2, 15)
    assert sum(record.new_customers for record in plan.records) == 40
