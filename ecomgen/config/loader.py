"""Load and validate configuration bundled with the package."""

from importlib.resources import files
from typing import Any

import yaml
from pydantic import TypeAdapter

from .models import MarketConfig, PresetConfig

_MARKETS_ADAPTER = TypeAdapter(dict[str, MarketConfig])


def _config_root():
    return files("ecomgen.config")


def _read_yaml(resource) -> dict[str, Any]:
    data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{resource.name} must contain a YAML mapping")
    return data


def load_markets() -> dict[str, MarketConfig]:
    """Return all bundled markets, keyed by lowercase market code."""

    markets = _MARKETS_ADAPTER.validate_python(_read_yaml(_config_root() / "markets.yaml"))
    mismatches = [key for key, market in markets.items() if key != market.code]
    if mismatches:
        raise ValueError(f"market keys must match their code: {mismatches}")
    return markets


def load_market(code: str) -> MarketConfig:
    """Load one bundled market by its lowercase code."""

    markets = load_markets()
    if code not in markets:
        choices = ", ".join(sorted(markets))
        raise ValueError(f"unknown market {code!r}; available markets: {choices}")
    return markets[code]


def available_presets() -> tuple[str, ...]:
    """Return bundled preset names in stable order."""

    preset_root = _config_root() / "presets"
    return tuple(
        sorted(
            item.name.removesuffix(".yaml")
            for item in preset_root.iterdir()
            if item.name.endswith(".yaml")
        )
    )


def load_preset(name: str) -> PresetConfig:
    """Load a bundled preset by name and reject unknown names."""

    if name not in available_presets():
        choices = ", ".join(available_presets())
        raise ValueError(f"unknown preset {name!r}; available presets: {choices}")
    preset = PresetConfig.model_validate(_read_yaml(_config_root() / "presets" / f"{name}.yaml"))
    if preset.name != name:
        raise ValueError(f"preset name {preset.name!r} does not match filename {name!r}")
    return preset
