from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ecomgen.config import available_presets, load_market, load_markets, load_preset
from ecomgen.config.models import PresetConfig
from ecomgen.generators.orders import _spike_multiplier


def test_all_markets_load_with_expected_codes() -> None:
    markets = load_markets()

    assert set(markets) == {"de", "at", "ch", "fr", "be", "es", "it", "nl", "uk"}
    assert all(key == market.code for key, market in markets.items())


def test_all_complete_presets_load() -> None:
    assert available_presets() == ("electronics", "fashion", "garden-decor")

    for name in available_presets():
        preset = load_preset(name)
        assert preset.name == name
        assert len(preset.seasonality) == 12
        assert set(preset.channel_mix) == {"meta", "google", "organic", "email", "direct"}
        assert preset.categories
        assert preset.special_spikes


def test_unknown_preset_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown preset"):
        load_preset("not-a-preset")


def test_unknown_market_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown market"):
        load_market("xx")


@pytest.mark.parametrize("length", [11, 13])
def test_malformed_seasonality_is_rejected(length: int) -> None:
    raw = load_preset("garden-decor").model_dump(mode="python")
    raw["seasonality"] = [1] * length

    with pytest.raises(ValidationError, match="seasonality"):
        PresetConfig.model_validate(raw)


def _preset_with_spike(start: str, end: str) -> dict:
    raw = load_preset("garden-decor").model_dump(mode="python")
    raw["special_spikes"] = [{"name": "promo", "start": start, "end": end, "multiplier": 1.5}]
    return raw


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize("bad", ["02-31", "13-01", "00-10", "04-31", "02-30", "2-15", "0101"])
def test_impossible_spike_dates_are_rejected(field: str, bad: str) -> None:
    dates = {"start": "01-01", "end": "01-02", field: bad}

    with pytest.raises(ValidationError) as caught:
        PresetConfig.model_validate(_preset_with_spike(dates["start"], dates["end"]))

    message = str(caught.value)
    assert "preset 'garden-decor'" in message
    assert f"special_spikes.0.{field}" in message
    assert repr(bad) in message


@pytest.mark.parametrize(
    ("start", "end"),
    [("02-29", "02-29"), ("02-28", "03-01"), ("01-01", "12-31"), ("12-20", "01-05")],
)
def test_valid_spike_dates_including_leap_day_and_year_wrap_are_accepted(
    start: str, end: str
) -> None:
    preset = PresetConfig.model_validate(_preset_with_spike(start, end))

    assert (preset.special_spikes[0].start, preset.special_spikes[0].end) == (start, end)


def test_leap_day_spike_is_skipped_in_non_leap_years() -> None:
    preset = PresetConfig.model_validate(_preset_with_spike("02-29", "02-29"))

    assert _spike_multiplier(preset, date(2024, 2, 29)) == Decimal("1.5")
    for day in (date(2025, 2, 28), date(2025, 3, 1)):
        assert _spike_multiplier(preset, day) == 1
