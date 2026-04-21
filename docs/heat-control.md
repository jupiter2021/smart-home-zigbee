# Floor Heating Control

Controls floor heating circuits: on/off and temperature setting.

## Device Info

| Parameter | Value |
|-----------|-------|
| Device type | 0x08 (Extension) |
| Channel type | 0xF1 |
| Control scope | Per room (each room has an independent circuit) |
| Supported | On/Off, Temperature (16-32°C) |
| Not supported | Mode, Wind speed (floor heating is simpler than AC) |

## Usage

### CLI

```bash
smz heat on 客厅地暖              # Turn on
smz heat off 客厅地暖             # Turn off
smz heat temp 客厅地暖 24         # Set temperature to 24°C
smz list heats                    # List all floor heating devices
```

### Python API

```python
from smart_home_zigbee import Gateway, FloorHeatingController, load_config

config = load_config()
with Gateway(config.gateway.ip) as gw:
    heat = FloorHeatingController(gw, config.heats)
    heat.on("客厅地暖")              # Turn on
    heat.set_temp("客厅地暖", 24)    # Set temperature
    heat.off("客厅地暖")             # Turn off
```

## Packet Format

12-byte extended packet (same structure as AC, different chType):

```
[0xA9] [0x20] [0x08] [devNo] [0xF1] [devCh] [cmd] [0x00] [0x02] [p1] [p2] [checksum]
  magic  magic  ext    addr   chType   ch     cmd   delay   len   param param sum&0xFF
```

## Commands

| Operation | cmd  | param1 | param2 | Description |
|-----------|------|--------|--------|-------------|
| Turn ON   | 0x01 | 0x00   | 0x00   | Power on |
| Turn OFF  | 0x02 | 0x00   | 0x00   | Power off |
| Set Temp  | 0x10 | hi     | lo     | Temperature = (hi<<8 \| lo) / 10 |
| Read Switch | 0x64 | 0x00 | 0x00   | Read on/off status |
| Read Temp | 0x66 | 0x00   | 0x00   | Read set temperature (2 bytes) |

> **Note**: Floor heating uses cmd=0x66 to read temperature, while AC uses cmd=0x65.
> This difference was confirmed from the panel's decompiled source code (`HeatVM.java`).

## AC vs Floor Heating

| | AC (chType=0x19) | Floor Heating (chType=0xF1) |
|---|---|---|
| On/Off | cmd=0x01/0x02 | cmd=0x01/0x02 (same) |
| Temperature | cmd=0x10 | cmd=0x10 (same) |
| Mode | cmd=0x11 (cool/heat/fan/dehumid) | N/A |
| Wind speed | cmd=0x12 (low/mid/high/auto) | N/A |
| Read status | cmd=0x6D (5-byte full status) | cmd=0x64 (switch only) |
| Read temp | cmd=0x65 (room temperature) | cmd=0x66 (set temperature) |

## Configuration

```yaml
heats:
  - name: "客厅地暖"
    dev_no: 0x01
    dev_ch: 0x05
    zone: "客厅"
  - name: "主卧地暖"
    dev_no: 0x01
    dev_ch: 0x03
    zone: "主卧"
```

> Floor heating shares devNo/devCh with AC units — they are distinguished
> by chType (AC = 0x19, floor heating = 0xF1). The library handles this automatically.

## Raw Commands

```bash
# Turn ON (living room: devNo=0x01, devCh=0x05)
printf '\xa9\x20\x08\x01\xf1\x05\x01\x00\x02\x00\x00\xcb' | nc -w 2 192.168.71.12 4196

# Set 24°C (0x00F0 = 240 = 24.0°C)
printf '\xa9\x20\x08\x01\xf1\x05\x10\x00\x02\x00\xf0\xbb' | nc -w 2 192.168.71.12 4196

# Read switch status
printf '\xa9\x20\x08\x01\xf1\x05\x64\x00\x02\x00\x00\x2e' | nc -w 2 192.168.71.12 4196

# Read set temperature
printf '\xa9\x20\x08\x01\xf1\x05\x66\x00\x02\x00\x00\x30' | nc -w 2 192.168.71.12 4196
```
