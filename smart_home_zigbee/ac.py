"""
Air Conditioner Controller
===========================

Controls central air conditioning units: on/off, temperature, mode, and wind speed.
Each room has an independent AC unit sharing the same devNo but different devCh.
"""

import time
import logging
from typing import Optional

from .gateway import Gateway
from .config import ACDevice
from .protocol import (
    CMD_ON, CMD_OFF, CMD_TEMP, CMD_MODE, CMD_WIND_SPEED,
    CMD_READ_FULL_STATUS, CMD_READ_ROOM_TEMP,
    AC_MODE_MAP, WIND_SPEED_MAP,
    build_ac_packet, encode_temp, decode_temp,
)

log = logging.getLogger(__name__)

DELAY_BETWEEN_COMMANDS = 0.1


class ACController:
    """
    Controls air conditioning units through the Zigbee gateway.

    Usage::

        ac = ACController(gw, config.acs)
        ac.on("客厅空调")
        ac.set_temp("客厅空调", 24)
        ac.set_mode("客厅空调", "cool")
        ac.set_speed("客厅空调", "auto")
        ac.off("客厅空调")

    Args:
        gateway: Connected Gateway instance
        devices: List of ACDevice from config
    """

    def __init__(self, gateway: Gateway, devices: list[ACDevice]):
        self.gateway = gateway
        self.devices = {d.name: d for d in devices}

    def _get(self, name: str) -> ACDevice:
        dev = self.devices.get(name)
        if dev is None:
            raise ValueError(
                f"Unknown AC '{name}'. Available: {list(self.devices.keys())}"
            )
        return dev

    def on(self, name: str) -> bool:
        """Turn on an AC unit (keeps last mode and temperature)."""
        dev = self._get(name)
        pkt = build_ac_packet(dev.dev_no, dev.dev_ch, CMD_ON)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> ON", "ok" if ok else "FAILED", name)
        return ok

    def off(self, name: str) -> bool:
        """Turn off an AC unit."""
        dev = self._get(name)
        pkt = build_ac_packet(dev.dev_no, dev.dev_ch, CMD_OFF)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> OFF", "ok" if ok else "FAILED", name)
        return ok

    def set_temp(self, name: str, temp: int) -> bool:
        """
        Set target temperature.

        Args:
            name: AC device name
            temp: Temperature in Celsius (16-32)
        """
        if not 16 <= temp <= 32:
            raise ValueError(f"Temperature must be 16-32, got {temp}")
        dev = self._get(name)
        p1, p2 = encode_temp(temp)
        pkt = build_ac_packet(dev.dev_no, dev.dev_ch, CMD_TEMP, p1, p2)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> %d°C", "ok" if ok else "FAILED", name, temp)
        return ok

    def set_mode(self, name: str, mode: str) -> bool:
        """
        Set AC mode.

        Args:
            name: AC device name
            mode: "cool", "heat", "fan", or "dehumid"
        """
        mode_val = AC_MODE_MAP.get(mode)
        if mode_val is None:
            raise ValueError(
                f"Invalid mode '{mode}'. Valid: {list(AC_MODE_MAP.keys())}"
            )
        dev = self._get(name)
        pkt = build_ac_packet(dev.dev_no, dev.dev_ch, CMD_MODE, 0x00, mode_val)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> mode %s", "ok" if ok else "FAILED", name, mode)
        return ok

    def set_speed(self, name: str, speed: str) -> bool:
        """
        Set wind speed.

        Args:
            name: AC device name
            speed: "low", "mid", "high", or "auto"
        """
        speed_val = WIND_SPEED_MAP.get(speed)
        if speed_val is None:
            raise ValueError(
                f"Invalid speed '{speed}'. Valid: {list(WIND_SPEED_MAP.keys())}"
            )
        dev = self._get(name)
        pkt = build_ac_packet(dev.dev_no, dev.dev_ch, CMD_WIND_SPEED, 0x00, speed_val)
        ok = self.gateway.send(pkt)
        log.info("  %s %s -> speed %s", "ok" if ok else "FAILED", name, speed)
        return ok

    def read_room_temp(self, name: str) -> Optional[float]:
        """Read current room temperature from AC sensor. Returns °C or None."""
        dev = self._get(name)
        pkt = build_ac_packet(dev.dev_no, dev.dev_ch, CMD_READ_ROOM_TEMP)
        resp = self.gateway.send_and_recv(pkt)
        if resp and len(resp) >= 2:
            return decode_temp(resp[-2], resp[-1])
        return None

    def list_devices(self) -> list[ACDevice]:
        """Return all configured AC devices."""
        return list(self.devices.values())
