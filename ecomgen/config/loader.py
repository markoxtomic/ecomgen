"""Load and validate configuration bundled with the package."""

from importlib.resources import files
from typing import Any

import yaml
from pydantic import TypeAdapter

from .models import MarketConfig, PresetConfig

_MARKETS_ADAPTER = TypeAdapter(dict[str, MarketConfig])


class ConfigError(ValueError):
    """A concise, user-facing configuration error."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that treats duplicate mapping keys as errors."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found an unhashable key ({exc})",
                key_node.start_mark,
            ) from None
        if duplicate:
            line = key_node.start_mark.line + 1
            column = key_node.start_mark.column + 1
            raise ConfigError(f"duplicate mapping key {key!r} at line {line}, column {column}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _config_root():
    return files("ecomgen.config")


def _read_yaml(resource) -> dict[str, Any]:
    try:
        data = yaml.load(
            resource.read_text(encoding="utf-8"),
            Loader=_UniqueKeySafeLoader,
        )
    except ConfigError as exc:
        raise ConfigError(f"invalid YAML in {resource.name}: {exc}") from None
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise ConfigError(f"invalid YAML in {resource.name}{location}: {problem}") from None
    if not isinstance(data, dict):
        kind = type(data).__name__
        raise ConfigError(
            f"invalid YAML in {resource.name}: top-level value must be a mapping, got {kind}"
        )
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
