"""
Configuration loader for smart-home-zigbee.

Loads device settings from a YAML config file so users only need to edit
config.yaml with their own device addresses -- no code changes required.
"""

import os
import logging
import math
import re
from urllib.parse import urlsplit
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


_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ELEVATOR_PATH = "/api/elevator/down"


def _validate_elevator_endpoint(endpoint: Any, enabled: bool) -> str:
    """Validate the fixed local-server endpoint used by the elevator client."""
    if not isinstance(endpoint, str):
        raise ValueError("elevator.endpoint must be a string")
    if not enabled:
        return endpoint
    if not endpoint:
        raise ValueError(
            "elevator.endpoint is required when elevator.enabled is true"
        )
    if any(ch.isspace() or ch == "\\" for ch in endpoint):
        raise ValueError("elevator.endpoint must not contain whitespace or backslashes")

    try:
        parsed = urlsplit(endpoint)
        # Accessing port also validates malformed bracketed hosts and ports.
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("elevator.endpoint is not a valid URL") from exc

    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("elevator.endpoint must use http or https")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("elevator.endpoint must be an absolute URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("elevator.endpoint must not contain userinfo")
    # Reject query/fragment delimiters even when they are empty (``...?``).
    if "?" in endpoint or "#" in endpoint:
        raise ValueError("elevator.endpoint must not contain a query or fragment")
    if parsed.path != _ELEVATOR_PATH:
        raise ValueError(
            f"elevator.endpoint path must be {_ELEVATOR_PATH}"
        )
    return endpoint


def _strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _strict_positive_number(
    value: Any, field_name: str, *, minimum: Optional[float] = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} must be at least {minimum:g}")
    return number


def _validate_token_env(token_env: Any) -> str:
    if not isinstance(token_env, str) or not token_env:
        raise ValueError("elevator.token_env must be a non-empty environment name")
    if not _ENV_NAME_RE.fullmatch(token_env):
        raise ValueError("elevator.token_env is not a valid environment name")
    return token_env


def _validate_elevator_topic(topic: Any) -> str:
    if not isinstance(topic, str) or not topic:
        raise ValueError("elevator.topic must be a non-empty MQTT topic")
    if "+" in topic or "#" in topic or "\x00" in topic:
        raise ValueError("elevator.topic must be an MQTT literal topic")
    return topic


@dataclass
class ElevatorConfig:
    """Configuration for the optional local-server elevator adapter."""
    enabled: bool = False
    endpoint: str = ""
    token_env: str = "SMZ_ELEVATOR_TOKEN"
    timeout: float = 5.0
    cooldown: float = 30.0
    topic: str = "dianti006"

    def __post_init__(self) -> None:
        self.enabled = _strict_bool(self.enabled, "elevator.enabled")
        self.endpoint = _validate_elevator_endpoint(self.endpoint, self.enabled)
        self.token_env = _validate_token_env(self.token_env)
        self.timeout = _strict_positive_number(self.timeout, "elevator.timeout")
        self.cooldown = _strict_positive_number(
            self.cooldown, "elevator.cooldown", minimum=1.0
        )
        self.topic = _validate_elevator_topic(self.topic)


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
    elevator: ElevatorConfig = field(default_factory=ElevatorConfig)


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

    # Elevator is opt-in and intentionally contains no literal token field.
    elevator = raw.get("elevator", {})
    if "elevator" in raw:
        if not isinstance(elevator, dict):
            raise ValueError("elevator must be a mapping")
        forbidden = {"token", "password", "username", "identity"}
        configured_forbidden = sorted(forbidden.intersection(elevator))
        if configured_forbidden:
            raise ValueError(
                "elevator does not accept literal credentials: "
                + ", ".join(configured_forbidden)
            )
        defaults = config.elevator
        config.elevator = ElevatorConfig(
            enabled=(
                _strict_bool(elevator["enabled"], "elevator.enabled")
                if "enabled" in elevator
                else defaults.enabled
            ),
            endpoint=elevator.get("endpoint", defaults.endpoint),
            token_env=elevator.get("token_env", defaults.token_env),
            timeout=(
                _strict_positive_number(
                    elevator["timeout"], "elevator.timeout"
                )
                if "timeout" in elevator
                else defaults.timeout
            ),
            cooldown=(
                _strict_positive_number(
                    elevator["cooldown"], "elevator.cooldown", minimum=1.0
                )
                if "cooldown" in elevator
                else defaults.cooldown
            ),
            topic=elevator.get("topic", defaults.topic),
        )

    return config
