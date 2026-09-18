import pytest
from pydantic import ValidationError

from ecomgen.config import available_presets, load_market, load_markets, load_preset
from ecomgen.config.models import PresetConfig


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
