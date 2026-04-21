# Air Conditioner Control

Controls central air conditioning units: on/off, temperature, mode, and wind speed.

## Device Info

| Parameter | Value |
|-----------|-------|
| Device type | 0x08 (Extension) |
| Channel type | 0x19 |
| Control scope | Per room (each room has an independent unit) |
| Supported | On/Off, Temperature (16-32°C), Mode (cool/heat/fan/dehumid), Wind speed (low/mid/high/auto) |
| Not supported | Auto mode (DNAKE gateway ignores it) |

## Usage

### CLI

```bash
smz ac on 客厅空调                # Turn on (keeps last mode & temperature)
smz ac off 客厅空调               # Turn off
smz ac temp 客厅空调 24           # Set temperature to 24°C
smz ac mode 客厅空调 --mode-name cool   # Set mode to cooling
smz ac speed 客厅空调 --speed-name auto # Set wind speed to auto
smz list acs                      # List all AC devices
```

### Python API

```python
from smart_home_zigbee import Gateway, ACController, load_config

config = load_config()
with Gateway(config.gateway.ip) as gw:
    ac = ACController(gw, config.acs)
    ac.on("客厅空调")                # Turn on
    ac.set_temp("客厅空调", 24)      # Set temperature
    ac.set_mode("客厅空调", "cool")  # Cooling mode
    ac.set_speed("客厅空调", "auto") # Auto wind speed
    ac.off("客厅空调")               # Turn off

    # Read room temperature from AC sensor
    temp = ac.read_room_temp("客厅空调")
    print(f"Room temperature: {temp}°C")
```

## Packet Format

12-byte extended packet (same structure as fresh air, different chType):

```
[0xA9] [0x20] [0x08] [devNo] [0x19] [devCh] [cmd] [0x00] [0x02] [p1] [p2] [checksum]
  magic  magic  ext    addr   chType   ch     cmd   delay   len   param param sum&0xFF
```

## Commands

| Operation | cmd  | param1 | param2 | Description |
|-----------|------|--------|--------|-------------|
| Turn ON   | 0x01 | 0x00   | 0x00   | Power on (keeps last mode) |
| Turn OFF  | 0x02 | 0x00   | 0x00   | Power off |
| Set Temp  | 0x10 | hi     | lo     | Temperature = (hi<<8 \| lo) / 10 |
| Set Mode  | 0x11 | 0x00   | mode   | 0=cool, 1=heat, 2=fan, 3=dehumid |
| Set Speed | 0x12 | 0x00   | speed  | 1=low, 2=mid, 3=high, 5=auto |
| Read Status | 0x6D | 0x00 | 0x00   | Returns 5 bytes: switch, wind, mode, temp_hi, temp_lo |
| Read Room Temp | 0x65 | 0x00 | 0x00 | Returns 2 bytes: temp = (hi<<8 \| lo) / 10 |

## Temperature Encoding

Temperature is encoded as `degrees × 10`, split into two big-endian bytes:

| Temperature | param1 | param2 |
|-------------|--------|--------|
| 16°C        | 0x00   | 0xA0   |
| 24°C        | 0x00   | 0xF0   |
| 26°C        | 0x01   | 0x04   |
| 32°C        | 0x01   | 0x40   |

## Configuration

```yaml
acs:
  - name: "客厅空调"
    dev_no: 0x01
    dev_ch: 0x05
    zone: "客厅"
  - name: "主卧空调"
    dev_no: 0x01
    dev_ch: 0x03
    zone: "主卧"
```

> AC and floor heating share the same devNo/devCh — they are distinguished
> by chType (AC = 0x19, floor heating = 0xF1). The library handles this automatically.

## Raw Commands

```bash
# Turn ON (living room AC: devNo=0x01, devCh=0x05)
printf '\xa9\x20\x08\x01\x19\x05\x01\x00\x02\x00\x00\xf3' | nc -w 2 192.168.71.12 4196

# Set 24°C
printf '\xa9\x20\x08\x01\x19\x05\x10\x00\x02\x00\xf0\x22' | nc -w 2 192.168.71.12 4196

# Set mode: cooling (0x00)
printf '\xa9\x20\x08\x01\x19\x05\x11\x00\x02\x00\x00\x03' | nc -w 2 192.168.71.12 4196
```
