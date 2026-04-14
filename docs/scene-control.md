# Scene Control

Supports two types of scenes: hardware (gateway-stored) and software (config-defined).

## Hardware Scenes

Pre-programmed in the Zigbee gateway NVM by the installer. These scenes **cannot be modified** through any known interface -- they can only be triggered.

```bash
smz scene 回家          # Execute "come home" hardware scene
smz scene 离家          # Execute "leave home" hardware scene
```

### Why Read-Only?

The DNAKE gateway stores scene configurations in its NVM/EEPROM. Investigation shows:
- TCP 4196 protocol only supports `GET_SCENE_LIST` (read) and `SCENE_EXECUTE` (trigger)
- HTTP REST API's `addScene`/`editScene` return errors
- No management port exposed (nmap confirms only TCP 4196)
- Scene configuration requires the DNAKE installer tool (proprietary, not publicly available)

### Configuration

```yaml
scenes:
  hardware:
    - name: "回家"
      addr: 0x05
      ch: 0x00
    - name: "离家"
      addr: 0x05
      ch: 0x01
```

## Software Scenes

Defined in `config.yaml` as combinations of light names. Fully customizable.

```bash
smz scene 会客          # Turn on living room + dining room lights
smz scene 晚安          # Turn on only bedroom light strip
smz scene 会客 --off    # Turn off the same set
```

### Configuration

```yaml
scenes:
  software:
    全开: ["*"]                    # All lights
    会客: ["客厅主灯", "客厅筒灯", "餐厅主灯", "餐厅灯带", "餐厅筒灯"]
    晚安: ["主卧灯带"]             # Only bedroom light strip
```

### Python API

```python
from smart_home_zigbee import Gateway, LightController, load_config
from smart_home_zigbee.scene import SceneController

config = load_config()
with Gateway(config.gateway.ip) as gw:
    lights = LightController(gw, config.lights)
    scenes = SceneController(
        gw, config.hardware_scenes, config.software_scenes, lights
    )
    scenes.execute("回家")           # Hardware scene
    scenes.execute("会客")           # Software scene: ON
    scenes.execute("会客", on=False) # Software scene: OFF
```

## Hardware Scene Packet Format

```
[0xA9] [0x20] [0x05] [addr] [0x00] [ch] [0x0E] [0x00] [0x00] [checksum]
  magic  magic  scene  addr   chType  ch  EXECUTE delay   len    sum&0xFF
```
