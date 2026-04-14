"""
Light Controller
=================

Control individual lights or groups of lights by name / zone / "all".
"""

import time
import logging
from typing import Optional

from .gateway import Gateway
from .config import LightDevice
from .protocol import build_light_packet

log = logging.getLogger(__name__)

# Delay between commands when controlling multiple lights (seconds)
DEFAULT_MULTI_CMD_DELAY = 0.15


class LightController:
    """
    Controls lights through the Zigbee gateway.

    Usage::

        from smart_home_zigbee import Gateway, LightController, load_config

        config = load_config()
        gw = Gateway(config.gateway.ip, config.gateway.port)
        gw.connect()

        lights = LightController(gw, config.lights)
        lights.on("客厅")       # Turn on all lights in zone "客厅"
        lights.off("客厅主灯")  # Turn off a specific light
        lights.on()             # Turn on all lights

    Args:
        gateway: Connected Gateway instance
        devices: List of LightDevice from config
        cmd_delay: Delay between commands for multi-light operations (seconds)
    """

    def __init__(
        self,
        gateway: Gateway,
        devices: list[LightDevice],
        cmd_delay: float = DEFAULT_MULTI_CMD_DELAY,
    ):
        self.gateway = gateway
        self.devices = devices
        self.cmd_delay = cmd_delay

    def on(self, target: Optional[str] = None) -> list[tuple[str, bool]]:
        """
        Turn on lights.

        Args:
            target: Light name, zone name, or None for all lights.
                    Supports partial name matching.

        Returns:
            List of (light_name, success) tuples
        """
        return self._control(target, on=True)

    def off(self, target: Optional[str] = None) -> list[tuple[str, bool]]:
        """
        Turn off lights.

        Args:
            target: Light name, zone name, or None for all lights.

        Returns:
            List of (light_name, success) tuples
        """
        return self._control(target, on=False)

    def match(self, target: Optional[str] = None) -> list[LightDevice]:
        """
        Find lights matching a target string.

        Matching priority:
          1. None or "all" -> all lights
          2. Exact name match
          3. Zone match
          4. Substring match

        Args:
            target: Light name, zone name, "all", or None

        Returns:
            List of matching LightDevice objects
        """
        if target is None or target == "all":
            return list(self.devices)

        # Exact name match
        exact = [d for d in self.devices if d.name == target]
        if exact:
            return exact

        # Zone match
        zone = [d for d in self.devices if d.zone == target]
        if zone:
            return zone

        # Substring match
        partial = [d for d in self.devices if target in d.name]
        if partial:
            return partial

        return []

    def list_devices(self) -> list[LightDevice]:
        """Return all configured light devices."""
        return list(self.devices)

    def _control(self, target: Optional[str], on: bool) -> list[tuple[str, bool]]:
        """Internal: control matched lights."""
        matched = self.match(target)
        if not matched:
            log.warning("No lights matched: %s", target)
            return []

        action = "ON" if on else "OFF"
        results = []

        for device in matched:
            packet = build_light_packet(device.dev_no, device.dev_ch, on)
            ok = self.gateway.send(packet)
            status = "ok" if ok else "FAILED"
            log.info("  %s %s (0x%02X:0x%02X) -> %s",
                     status, device.name, device.dev_no, device.dev_ch, action)
            results.append((device.name, ok))

            if len(matched) > 1:
                time.sleep(self.cmd_delay)

        return results
