"""
smart-home-zigbee: Control DNAKE smart home devices via Zigbee gateway
======================================================================

A Python library for controlling DNAKE smart home devices (lights, scenes,
fresh air, air conditioning, floor heating) through the Zigbee gateway's
proprietary TCP protocol.

Quick start::

    from smart_home_zigbee import Gateway, LightController, load_config

    config = load_config("config.yaml")
    with Gateway(config.gateway.ip) as gw:
        lights = LightController(gw, config.lights)
        lights.on("客厅")
"""

__version__ = "0.2.0"

from .gateway import Gateway
from .light import LightController
from .scene import SceneController
from .fresh_air import FreshAirController
from .ac import ACController
from .heat import FloorHeatingController
from .config import load_config, Config

__all__ = [
    "Gateway",
    "LightController",
    "SceneController",
    "FreshAirController",
    "ACController",
    "FloorHeatingController",
    "load_config",
    "Config",
]
