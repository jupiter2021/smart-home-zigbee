"""
Floor Heating Controller
=========================

Controls floor heating units: on/off and temperature setting.
Each room has an independent heating circuit sharing the same devNo but different devCh.

Floor heating shares devNo/devCh with AC units — they are distinguished by chType
(AC = 0x19, floor heating = 0xF1).
"""

import logging
from typing import Optional

from .gateway import Gateway
from .config import HeatDevice
from .protocol import (
    CMD_ON, CMD_OFF, CMD_TEMP, CMD_READ_SWITCH, CMD_READ_SET_TEMP,
    build_heat_packet, encode_temp, decode_temp,
)

log = logging.getLogger(__name__)


class FloorHeatingController:
    """
    Controls floor heating through the Zigbee gateway.

    Usage::

        heat = FloorHeatingController(gw, config.heats)
        heat.on("客厅地暖")
        heat.set_temp("客厅地暖", 24)
        heat.off("客厅地暖")

    Args:
        gateway: Connected Gateway instance
        devices: List of HeatDevice from config
    """

    def __init__(self, gateway: Gateway, devices: list[HeatDevice]):
        self.gateway = gateway
        self.devices = {d.name: d for d in devices}

    def _get(self, name: str) -> HeatDevice:
        dev = self.devices.get(name)
        if dev is None:
            raise ValueError(
                f"Unknown heat '{name}'. Available: {list(self.devices.keys())}"
            )
        return dev

    def on(self, name: str) -> bool:
        """Turn on floor heating."""
        dev = self._get(name)
        pkt = build_heat_packet(dev.dev_no, dev.dev_ch, CMD_ON)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> ON", "ok" if ok else "FAILED", name)
        return ok

    def off(self, name: str) -> bool:
        """Turn off floor heating."""
        dev = self._get(name)
        pkt = build_heat_packet(dev.dev_no, dev.dev_ch, CMD_OFF)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> OFF", "ok" if ok else "FAILED", name)
        return ok

    def set_temp(self, name: str, temp: int) -> bool:
        """
        Set target temperature.

        Args:
            name: Heat device name
            temp: Temperature in Celsius (16-32)
        """
        if not 16 <= temp <= 32:
            raise ValueError(f"Temperature must be 16-32, got {temp}")
        dev = self._get(name)
        p1, p2 = encode_temp(temp)
        pkt = build_heat_packet(dev.dev_no, dev.dev_ch, CMD_TEMP, p1, p2)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> %d°C", "ok" if ok else "FAILED", name, temp)
        return ok

    def list_devices(self) -> list[HeatDevice]:
        """Return all configured floor heating devices."""
        return list(self.devices.values())
