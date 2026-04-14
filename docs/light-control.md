# Light Control

Controls individual lights via the Zigbee gateway TCP protocol.

## How It Works

```
Your device (any LAN client)
  → TCP connect to gateway:4196
    → 10-byte binary packet
      → Zigbee 802.15.4 radio
        → Light module relay → Light ON/OFF
```

## Usage

### CLI

```bash
smz on all              # Turn on all lights
smz off all             # Turn off all lights
smz on 客厅             # Turn on all lights in zone "客厅"
smz off 客厅主灯        # Turn off a specific light
smz list                # List all configured lights
```

### Python API

```python
from smart_home_zigbee import Gateway, LightController, load_config

config = load_config()
with Gateway(config.gateway.ip) as gw:
    lights = LightController(gw, config.lights)
    lights.on("客厅")
    lights.off("客厅主灯")
    lights.on()  # all
```

## Target Matching

The `target` parameter supports multiple matching modes:

| Input | Match Type | Example |
|-------|-----------|---------|
| `None` or `"all"` | All lights | `lights.on()` |
| Exact name | Single light | `lights.on("客厅主灯")` |
| Zone name | All lights in zone | `lights.on("客厅")` |
| Substring | Partial name match | `lights.on("主灯")` |

## Packet Format

10-byte binary packet:

```
[0xA9] [0x20] [0x01] [devNo] [0x00] [devCh] [cmd] [0x00] [0x00] [checksum]
  magic  magic  light   addr   chType  channel  ON/OFF delay  len    sum&0xFF
```

- `cmd`: 0x01 = ON, 0x02 = OFF
- `checksum`: Sum of all preceding bytes & 0xFF

## Configuration

Define your lights in `config.yaml`:

```yaml
lights:
  - name: "客厅主灯"
    dev_no: 0x51      # From central.db DEVICE_BEAN table
    dev_ch: 0x02
    zone: "客厅"
```

See [device-discovery.md](device-discovery.md) for how to find your device addresses.
