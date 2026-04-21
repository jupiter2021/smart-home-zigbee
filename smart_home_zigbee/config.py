"""
Configuration loader for smart-home-zigbee.

Loads device settings from a YAML config file so users only need to edit
config.yaml with their own device addresses -- no code changes required.
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


# ============================================================
# Data classes
# ============================================================

@dataclass
class GatewayConfig:
    """Zigbee gateway connection settings."""
    ip: str = "192.168.71.12"
    port: int = 4196


@dataclass
class LightDevice:
    """A single light device."""
    name: str
    dev_no: int
    dev_ch: int
    zone: str = ""


@dataclass
class HardwareScene:
    """A hardware scene stored in the gateway (read-only, execute only)."""
    name: str
    addr: int
    ch: int


@dataclass
class FreshAirConfig:
    """Fresh air system device parameters."""
    dev_type: int = 0x08
    dev_no: int = 0x03
    ch_type: int = 0x59
    dev_ch: int = 0x01
    default_speed: str = "mid"


@dataclass
class ACDevice:
    """A single air conditioner unit."""
    name: str
    dev_no: int
    dev_ch: int
    zone: str = ""


@dataclass
class HeatDevice:
    """A single floor heating circuit."""
    name: str
    dev_no: int
    dev_ch: int
    zone: str = ""


@dataclass
class BemfaConfig:
    """Bemfa MQTT bridge settings (optional)."""
    enabled: bool = False
    broker: str = "bemfa.com"
    port: int = 9501
    key: str = ""


@dataclass
class Config:
    """Top-level configuration."""
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    lights: list[LightDevice] = field(default_factory=list)
    hardware_scenes: list[HardwareScene] = field(default_factory=list)
    software_scenes: dict[str, list[str]] = field(default_factory=dict)
    fresh_air: FreshAirConfig = field(default_factory=FreshAirConfig)
    acs: list[ACDevice] = field(default_factory=list)
    heats: list[HeatDevice] = field(default_factory=list)
    bemfa: BemfaConfig = field(default_factory=BemfaConfig)


# ============================================================
# Loaders
# ============================================================

def _parse_int(value: Any) -> int:
    """Parse an integer from YAML, supporting both 0x51 and 81 formats."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        value = value.strip()
        if value.startswith(("0x", "0X")):
            return int(value, 16)
        return int(value)
    return int(value)


def load_config(path: Optional[str] = None) -> Config:
    """
    Load configuration from a YAML file.

    Search order (if path not specified):
      1. ``CONFIG_FILE`` environment variable
      2. ``./config.yaml``
      3. ``~/.smart-home-zigbee/config.yaml``

    Args:
        path: Explicit path to config file. If None, searches default locations.

    Returns:
        Parsed Config object

    Raises:
        FileNotFoundError: If no config file found
        ImportError: If PyYAML is not installed
    """
    if yaml is None:
        raise ImportError(
            "PyYAML is required for config loading. "
            "Install it with: pip install pyyaml"
        )

    config_path = _find_config(path)
    log.info("Loading config from: %s", config_path)

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return _parse_config(raw)


def _find_config(path: Optional[str] = None) -> Path:
    """Find the config file path."""
    if path:
        p = Path(path)
        if p.exists():
            return p
        raise FileNotFoundError(f"Config file not found: {path}")

    # Environment variable
    env_path = os.environ.get("CONFIG_FILE")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # Current directory
    p = Path("config.yaml")
    if p.exists():
        return p

    # Home directory
    p = Path.home() / ".smart-home-zigbee" / "config.yaml"
    if p.exists():
        return p

    raise FileNotFoundError(
        "No config.yaml found. Copy config.example.yaml to config.yaml "
        "and fill in your device information.\n"
        "Searched: ./config.yaml, ~/.smart-home-zigbee/config.yaml"
    )


def _parse_config(raw: dict) -> Config:
    """Parse raw YAML dict into Config object."""
    config = Config()

    # Gateway
    gw = raw.get("gateway", {})
    if gw:
        config.gateway = GatewayConfig(
            ip=gw.get("ip", config.gateway.ip),
            port=int(gw.get("port", config.gateway.port)),
        )

    # Lights
    for item in raw.get("lights", []):
        config.lights.append(LightDevice(
            name=item["name"],
            dev_no=_parse_int(item["dev_no"]),
            dev_ch=_parse_int(item["dev_ch"]),
            zone=item.get("zone", ""),
        ))

    # Scenes
    scenes = raw.get("scenes", {})
    for item in scenes.get("hardware", []):
        config.hardware_scenes.append(HardwareScene(
            name=item["name"],
            addr=_parse_int(item["addr"]),
            ch=_parse_int(item["ch"]),
        ))
    config.software_scenes = scenes.get("software", {})

    # Fresh air
    fa = raw.get("fresh_air", {})
    if fa:
        config.fresh_air = FreshAirConfig(
            dev_type=_parse_int(fa.get("dev_type", config.fresh_air.dev_type)),
            dev_no=_parse_int(fa.get("dev_no", config.fresh_air.dev_no)),
            ch_type=_parse_int(fa.get("ch_type", config.fresh_air.ch_type)),
            dev_ch=_parse_int(fa.get("dev_ch", config.fresh_air.dev_ch)),
            default_speed=fa.get("default_speed", config.fresh_air.default_speed),
        )

    # ACs
    for item in raw.get("acs", []):
        config.acs.append(ACDevice(
            name=item["name"],
            dev_no=_parse_int(item["dev_no"]),
            dev_ch=_parse_int(item["dev_ch"]),
            zone=item.get("zone", ""),
        ))

    # Floor heating
    for item in raw.get("heats", []):
        config.heats.append(HeatDevice(
            name=item["name"],
            dev_no=_parse_int(item["dev_no"]),
            dev_ch=_parse_int(item["dev_ch"]),
            zone=item.get("zone", ""),
        ))

    # Bemfa
    bemfa = raw.get("bemfa", {})
    if bemfa:
        key = bemfa.get("key", "") or os.environ.get("BEMFA_KEY", "")
        config.bemfa = BemfaConfig(
            enabled=bemfa.get("enabled", False),
            broker=bemfa.get("broker", config.bemfa.broker),
            port=int(bemfa.get("port", config.bemfa.port)),
            key=key,
        )

    return config
