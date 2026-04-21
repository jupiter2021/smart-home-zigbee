"""
DNAKE Zigbee Gateway Binary Protocol
=====================================

Implements the proprietary binary protocol used by DNAKE smart home panels
to communicate with their Zigbee gateway (model SH-ZBA-GTW).

Supports: lights, scenes, fresh air, air conditioning, and floor heating.
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
CMD_TEMP = 0x10
CMD_MODE = 0x11
CMD_WIND_SPEED = 0x12
CMD_READ_SWITCH = 0x64
CMD_READ_ROOM_TEMP = 0x65
CMD_READ_SET_TEMP = 0x66
CMD_READ_FULL_STATUS = 0x6D

# Channel types
CH_TYPE_DEFAULT = 0x00
CH_TYPE_AC = 0x19
CH_TYPE_FRESH_AIR = 0x59
CH_TYPE_HEAT = 0xF1

# Wind speed levels
WIND_LOW = 0x01
WIND_MID = 0x02
WIND_HIGH = 0x03
WIND_AUTO = 0x05

WIND_SPEED_MAP = {
    "low": WIND_LOW,
    "mid": WIND_MID,
    "high": WIND_HIGH,
    "auto": WIND_AUTO,
}

# AC modes
AC_MODE_COOL = 0x00
AC_MODE_HEAT = 0x01
AC_MODE_FAN = 0x02
AC_MODE_DEHUMID = 0x03

AC_MODE_MAP = {
    "cool": AC_MODE_COOL,
    "heat": AC_MODE_HEAT,
    "fan": AC_MODE_FAN,
    "dehumid": AC_MODE_DEHUMID,
}


def calc_checksum(data: bytes) -> int:
    """Calculate checksum: sum of all bytes & 0xFF."""
    return sum(data) & 0xFF


def _build_ext_packet(
    dev_no: int,
    dev_ch: int,
    cmd: int,
    ch_type: int,
    param1: int = 0x00,
    param2: int = 0x00,
) -> bytes:
    """Build a 12-byte extension device packet (AC, fresh air, floor heating)."""
    data = bytes([0xA9, 0x20, DEV_TYPE_EXTENSION, dev_no, ch_type, dev_ch,
                  cmd, 0x00, 0x02, param1, param2])
    return data + bytes([calc_checksum(data)])


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
    return _build_ext_packet(dev_no, dev_ch, cmd, ch_type, param1, param2)


def build_ac_packet(
    dev_no: int,
    dev_ch: int,
    cmd: int,
    param1: int = 0x00,
    param2: int = 0x00,
) -> bytes:
    """
    Build a 12-byte air conditioner control packet.

    Args:
        dev_no: Device number (e.g., 0x01)
        dev_ch: Device channel (e.g., 0x05 for living room)
        cmd: Command (CMD_ON, CMD_OFF, CMD_TEMP, CMD_MODE, CMD_WIND_SPEED, etc.)
        param1: Parameter 1 (temperature high byte, or 0x00)
        param2: Parameter 2 (temperature low byte, mode value, or speed value)

    Returns:
        12-byte binary packet ready to send to gateway
    """
    return _build_ext_packet(dev_no, dev_ch, cmd, CH_TYPE_AC, param1, param2)


def build_heat_packet(
    dev_no: int,
    dev_ch: int,
    cmd: int,
    param1: int = 0x00,
    param2: int = 0x00,
) -> bytes:
    """
    Build a 12-byte floor heating control packet.

    Args:
        dev_no: Device number (e.g., 0x01)
        dev_ch: Device channel (e.g., 0x05 for living room)
        cmd: Command (CMD_ON, CMD_OFF, CMD_TEMP, CMD_READ_SWITCH, CMD_READ_SET_TEMP)
        param1: Parameter 1 (temperature high byte, or 0x00)
        param2: Parameter 2 (temperature low byte, or 0x00)

    Returns:
        12-byte binary packet ready to send to gateway
    """
    return _build_ext_packet(dev_no, dev_ch, cmd, CH_TYPE_HEAT, param1, param2)


def encode_temp(temp_c: int) -> tuple[int, int]:
    """Encode temperature to (high_byte, low_byte). Value = temp * 10, big-endian."""
    val = temp_c * 10
    return (val >> 8) & 0xFF, val & 0xFF


def decode_temp(high: int, low: int) -> float:
    """Decode temperature from two bytes. Returns degrees Celsius."""
    val = (high << 8) | low
    return val / 10.0


def build_heartbeat_packet() -> bytes:
    """
    Build a heartbeat packet to keep TCP connection alive.

    Uses devType=0x30, which is recognized by the gateway as a keepalive.
    """
    data = bytes([0xA9, 0x20, 0x30, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00])
    return data + bytes([calc_checksum(data)])
