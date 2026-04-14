"""
smart-home-zigbee: Control DNAKE smart home devices via Zigbee gateway
======================================================================

A Python library for controlling DNAKE smart home devices (lights, scenes,
fresh air) through the Zigbee gateway's proprietary TCP protocol.

Quick start::

    from smart_home_zigbee import Gateway, LightController, load_config

    config = load_config("config.yaml")
    with Gateway(config.gateway.ip) as gw:
        lights = LightController(gw, config.lights)
        lights.on("客厅")
"""

__version__ = "0.1.0"

from .gateway import Gateway
from .light import LightController
from .scene import SceneController
from .fresh_air import FreshAirController
from .config import load_config, Config

__all__ = [
    "Gateway",
    "LightController",
    "SceneController",
    "FreshAirController",
    "load_config",
    "Config",
]
