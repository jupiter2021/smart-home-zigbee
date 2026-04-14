#!/usr/bin/env python3
"""
Basic Control Example
=====================

Demonstrates the simplest way to control lights, scenes, and fresh air.

Usage:
    1. Copy config.example.yaml to config.yaml
    2. Edit config.yaml with your device addresses
    3. Run: python basic_control.py
"""

from smart_home_zigbee import Gateway, LightController, load_config
from smart_home_zigbee.scene import SceneController
from smart_home_zigbee.fresh_air import FreshAirController

# Load configuration
config = load_config()

# Connect to gateway
with Gateway(config.gateway.ip, config.gateway.port) as gw:

    # --- Light Control ---
    lights = LightController(gw, config.lights)

    # Turn on all lights
    lights.on()

    # Turn on a specific light
    lights.on("客厅主灯")

    # Turn on all lights in a zone
    lights.on("客厅")

    # Turn off everything
    lights.off()

    # List all configured lights
    for device in lights.list_devices():
        print(f"{device.name} (zone: {device.zone})")

    # --- Scene Control ---
    scenes = SceneController(
        gw, config.hardware_scenes, config.software_scenes, lights
    )

    # Execute a hardware scene
    scenes.execute("回家")

    # Execute a software scene (turns on specified lights)
    scenes.execute("会客")

    # Turn off a software scene
    scenes.execute("会客", on=False)

    # --- Fresh Air Control ---
    fa = FreshAirController(gw, config.fresh_air)

    # Turn on with default speed
    fa.on()

    # Turn on with specific speed
    fa.on(speed="high")

    # Change speed
    fa.set_speed("low")

    # Turn off
    fa.off()
