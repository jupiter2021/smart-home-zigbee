"""
Fresh Air Controller
=====================

Controls the fresh air (ventilation) system: on/off and wind speed (low/mid/high).
"""

import time
import logging

from .gateway import Gateway
from .config import FreshAirConfig
from .protocol import (
    CMD_ON, CMD_OFF, CMD_WIND_SPEED,
    WIND_LOW, WIND_MID, WIND_HIGH, WIND_SPEED_MAP,
    build_fresh_air_packet,
)

log = logging.getLogger(__name__)

# Delay between ON command and speed command (hardware needs startup time)
# The hardware needs ~1s startup time before accepting speed commands.
SPEED_CHANGE_DELAY = 1.0

SPEED_NAMES = {
    WIND_LOW: "low",
    WIND_MID: "mid",
    WIND_HIGH: "high",
}


class FreshAirController:
    """
    Controls the fresh air (ventilation) system.

    Usage::

        fa = FreshAirController(gw, config.fresh_air)
        fa.on()                    # Turn on with default speed
        fa.on(speed="high")        # Turn on with high speed
        fa.off()                   # Turn off
        fa.set_speed("low")        # Change speed without toggling power

    Args:
        gateway: Connected Gateway instance
        config: FreshAirConfig from config
    """

    def __init__(self, gateway: Gateway, config: FreshAirConfig):
        self.gateway = gateway
        self.config = config

    def on(self, speed: str = None) -> bool:
        """
        Turn on fresh air system.

        Args:
            speed: Wind speed ("low", "mid", "high").
                   If None, uses default_speed from config.

        Returns:
            True if command sent successfully
        """
        fa = self.config
        packet = build_fresh_air_packet(fa.dev_no, fa.dev_ch, CMD_ON, fa.ch_type)
        ok = self.gateway.send(packet)
        status = "ok" if ok else "FAILED"
        log.info("  %s Fresh air -> ON", status)

        if ok:
            speed = speed or fa.default_speed
            if speed:
                time.sleep(SPEED_CHANGE_DELAY)
                return self.set_speed(speed)

        return ok

    def off(self) -> bool:
        """
        Turn off fresh air system.

        Returns:
            True if command sent successfully
        """
        fa = self.config
        packet = build_fresh_air_packet(fa.dev_no, fa.dev_ch, CMD_OFF, fa.ch_type)
        ok = self.gateway.send(packet)
        status = "ok" if ok else "FAILED"
        log.info("  %s Fresh air -> OFF", status)
        return ok

    def set_speed(self, speed: str) -> bool:
        """
        Set wind speed without toggling power.

        Args:
            speed: "low", "mid", or "high"

        Returns:
            True if command sent successfully

        Raises:
            ValueError: If speed is not a valid value
        """
        speed_val = WIND_SPEED_MAP.get(speed)
        if speed_val is None:
            raise ValueError(
                f"Invalid speed '{speed}'. Valid values: {list(WIND_SPEED_MAP.keys())}"
            )

        fa = self.config
        packet = build_fresh_air_packet(
            fa.dev_no, fa.dev_ch, CMD_WIND_SPEED, fa.ch_type,
            param2=speed_val,
        )
        ok = self.gateway.send(packet)
        status = "ok" if ok else "FAILED"
        log.info("  %s Fresh air -> speed %s", status, speed)
        return ok
