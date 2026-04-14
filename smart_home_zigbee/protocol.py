"""
DNAKE Zigbee Gateway Binary Protocol
=====================================

Implements the proprietary binary protocol used by DNAKE smart home panels
to communicate with their Zigbee gateway (model SH-ZBA-GTW).

Supports: lights, scenes, fresh air, and other extension devices.
Transport: TCP to gateway:4196, no authentication.
"""

# Protocol constants
HEADER = bytes([0xA9, 0x20])

# Device types
DEV_TYPE_LIGHT = 0x01
DEV_TYPE_DIMMER = 0x03
DEV_TYPE_SCENE = 0x05
DEV_TYPE_EXTENSION = 0x08

# Commands
CMD_ON = 0x01
CMD_OFF = 0x02
CMD_DIM = 0x07
CMD_SCENE_EXECUTE = 0x0E
CMD_WIND_SPEED = 0x12

# Channel types
CH_TYPE_DEFAULT = 0x00
CH_TYPE_FRESH_AIR = 0x59

# Wind speed levels
WIND_LOW = 0x01
WIND_MID = 0x02
WIND_HIGH = 0x03

WIND_SPEED_MAP = {
    "low": WIND_LOW,
    "mid": WIND_MID,
    "high": WIND_HIGH,
}


def calc_checksum(data: bytes) -> int:
    """Calculate checksum: sum of all bytes & 0xFF."""
    return sum(data) & 0xFF


def build_light_packet(dev_no: int, dev_ch: int, on: bool) -> bytes:
    """
    Build a 10-byte light control packet.

    Args:
        dev_no: Device number (e.g., 0x51)
        dev_ch: Device channel (e.g., 0x02)
        on: True for ON, False for OFF

    Returns:
        10-byte binary packet ready to send to gateway
    """
    cmd = CMD_ON if on else CMD_OFF
    data = bytes([0xA9, 0x20, DEV_TYPE_LIGHT, dev_no, CH_TYPE_DEFAULT, dev_ch,
                  cmd, 0x00, 0x00])
    return data + bytes([calc_checksum(data)])


def build_scene_packet(addr: int, ch: int) -> bytes:
    """
    Build a 10-byte scene execution packet.

    Args:
        addr: Scene address (e.g., 0x05)
        ch: Scene channel (e.g., 0x00 for "come home", 0x01 for "leave home")

    Returns:
        10-byte binary packet ready to send to gateway
    """
    data = bytes([0xA9, 0x20, DEV_TYPE_SCENE, addr, CH_TYPE_DEFAULT, ch,
                  CMD_SCENE_EXECUTE, 0x00, 0x00])
    return data + bytes([calc_checksum(data)])


def build_fresh_air_packet(
    dev_no: int,
    dev_ch: int,
    cmd: int,
    ch_type: int = CH_TYPE_FRESH_AIR,
    param1: int = 0x00,
    param2: int = 0x00,
) -> bytes:
    """
    Build a 12-byte fresh air control packet.

    Args:
        dev_no: Device number (e.g., 0x03)
        dev_ch: Device channel (e.g., 0x01)
        cmd: Command (CMD_ON, CMD_OFF, or CMD_WIND_SPEED)
        ch_type: Channel type (default 0x59 for fresh air)
        param1: Extra param 1 (usually 0x00)
        param2: Extra param 2 (wind speed value for CMD_WIND_SPEED)

    Returns:
        12-byte binary packet ready to send to gateway
    """
    data = bytes([0xA9, 0x20, DEV_TYPE_EXTENSION, dev_no, ch_type, dev_ch,
                  cmd, 0x00, 0x02, param1, param2])
    return data + bytes([calc_checksum(data)])


def build_heartbeat_packet() -> bytes:
    """
    Build a heartbeat packet to keep TCP connection alive.

    Uses devType=0x30, which is recognized by the gateway as a keepalive.
    """
    data = bytes([0xA9, 0x20, 0x30, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00])
    return data + bytes([calc_checksum(data)])
