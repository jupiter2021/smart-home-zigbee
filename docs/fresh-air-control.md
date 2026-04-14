# Fresh Air Control

Controls the whole-house fresh air (ventilation) system: power on/off and wind speed.

## Device Info

| Parameter | Value |
|-----------|-------|
| Device type | 0x08 (Extension) |
| Channel type | 0x59 |
| Control scope | Whole house (single unit, ducted to all rooms) |
| Supported | On/Off, 3-speed wind (low/mid/high) |
| Not supported | Mode switching (protocol defined but hardware ignores) |

## Usage

### CLI

```bash
smz fresh-air on                   # Turn on (default mid speed)
smz fresh-air on --speed high      # Turn on with high speed
smz fresh-air on --speed low       # Turn on with low speed
smz fresh-air speed --speed high   # Change speed without toggling power
smz fresh-air off                  # Turn off
```

### Python API

```python
from smart_home_zigbee import Gateway, load_config
from smart_home_zigbee.fresh_air import FreshAirController

config = load_config()
with Gateway(config.gateway.ip) as gw:
    fa = FreshAirController(gw, config.fresh_air)
    fa.on()                # Default speed from config
    fa.on(speed="high")   # Specific speed
    fa.set_speed("low")   # Change speed only
    fa.off()
```

## Packet Format

12-byte extended packet (2 extra parameter bytes):

```
[0xA9] [0x20] [0x08] [devNo] [0x59] [devCh] [cmd] [0x00] [0x02] [p1] [p2] [checksum]
  magic  magic  ext    addr   chType   ch     cmd   delay   len   param param sum&0xFF
```

## Commands

| Operation | cmd  | param1 | param2 | Description |
|-----------|------|--------|--------|-------------|
| Turn ON   | 0x01 | 0x00   | 0x00   | Power on |
| Turn OFF  | 0x02 | 0x00   | 0x00   | Power off |
| Low speed | 0x12 | 0x00   | 0x01   | Set wind to low |
| Mid speed | 0x12 | 0x00   | 0x02   | Set wind to mid |
| High speed| 0x12 | 0x00   | 0x03   | Set wind to high |

> **Note**: After turning ON, wait ~1 second before sending a speed command.
> The hardware needs time to initialize. The library handles this automatically.

## Configuration

```yaml
fresh_air:
  dev_type: 0x08
  dev_no: 0x03
  ch_type: 0x59
  dev_ch: 0x01
  default_speed: "mid"    # "low" / "mid" / "high"
```

## Raw Commands

```bash
# Turn ON
printf '\xa9\x20\x08\x03\x59\x01\x01\x00\x02\x00\x00\x31' | nc -w 2 192.168.71.12 4196

# Turn OFF
printf '\xa9\x20\x08\x03\x59\x01\x02\x00\x02\x00\x00\x32' | nc -w 2 192.168.71.12 4196

# High speed
printf '\xa9\x20\x08\x03\x59\x01\x12\x00\x02\x00\x03\x45' | nc -w 2 192.168.71.12 4196
```
