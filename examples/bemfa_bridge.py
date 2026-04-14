#!/usr/bin/env python3
"""
DNAKE Smart Home <-> Bemfa (巴法云) MQTT Bridge
================================================

Bridges Bemfa cloud MQTT to DNAKE Zigbee gateway, enabling Xiaomi XiaoAi
(小爱同学) voice control for lights and fresh air.

Architecture:
  "小爱同学，开客厅灯"
    -> Mi Home -> Bemfa MQTT broker
      -> this script (subscribes to topics)
        -> TCP binary -> Zigbee gateway
          -> Zigbee radio -> light ON

Setup:
  1. pip install smart-home-zigbee[mqtt]
  2. Register at https://cloud.bemfa.com, get your private key
  3. Create topics in Bemfa console (see TOPIC_LIGHT_MAP below)
  4. Mi Home App: 我的 -> 其他平台设备 -> 添加 -> 巴法 -> bind
  5. Copy config.example.yaml -> config.yaml, fill in your devices + Bemfa key
  6. Run: python bemfa_bridge.py

Topic naming convention:
  - Topics ending in "002" = lights in Bemfa (shows as 灯 in Mi Home)
  - Topics ending in "006" = switches in Bemfa (shows as 开关 in Mi Home)
"""

import os
import sys
import time
import signal
import logging

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt is required. Install with: pip install smart-home-zigbee[mqtt]")
    sys.exit(1)

from smart_home_zigbee import Gateway, LightController, load_config
from smart_home_zigbee.fresh_air import FreshAirController
from smart_home_zigbee.protocol import WIND_SPEED_MAP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bemfa_bridge")

# ============================================================
# Bemfa Topic Mapping
#
# You need to create these topics in Bemfa console:
#   https://cloud.bemfa.com/mdevice.php
#
# Customize these maps to match your topics and light names.
# The light names must match names in your config.yaml.
# ============================================================

# Individual light topics (002 suffix = lamp type in Mi Home)
TOPIC_LIGHT_MAP = {
    "xuanguigdd002":  "玄关柜灯带",
    "xuanguantd002":  "玄关筒灯",
    "cantingdd002":   "餐厅灯带",
    "cantingzd002":   "餐厅主灯",
    "ketingzd002":    "客厅主灯",
    "cantingtd002":   "餐厅筒灯",
    "ketingtd002":    "客厅筒灯",
    "guodaod002":     "过道灯",
    "zhuwodd002":     "主卧灯带",
    "zhuwoymd002":    "主卧衣帽",
    "zhuwozd002":     "主卧主灯",
}

# Scene topics (006 suffix = switch type in Mi Home)
# Values are software scene names from config.yaml
TOPIC_SCENE_MAP = {
    "quankai006":     "全开",
    "ketingcj006":    "客厅",
    "cantingcj006":   "餐厅",
    "zhuwocj006":     "主卧",
    "huike006":       "会客",
    "yingbin006":     "迎宾",
    "wanan006":       "晚安",
}

# Fresh air topics
# Format: topic -> (name, wind_speed_or_None)
TOPIC_FRESH_MAP = {
    "xinfeng006":     (None,),       # default speed
    "xinfengdf006":   ("low",),
    "xinfengzf006":   ("mid",),
    "xinfenggf006":   ("high",),
}


def parse_bemfa_command(payload: str, is_light: bool = True):
    """Parse Bemfa command payload. Returns True (on), False (off), or None."""
    payload = payload.strip().lower()
    if is_light:
        if payload.startswith("on"):
            return True
        if payload == "off":
            return False
    else:
        if payload == "on":
            return True
        if payload == "off":
            return False
    return None


def main():
    config = load_config()

    if not config.bemfa.enabled:
        log.warning("Bemfa is not enabled in config.yaml. Set bemfa.enabled: true")

    bemfa_key = config.bemfa.key or os.environ.get("BEMFA_KEY", "")
    if not bemfa_key:
        log.error("No Bemfa key configured!")
        log.error("Set bemfa.key in config.yaml or BEMFA_KEY environment variable")
        sys.exit(1)

    # Connect to gateway
    gw = Gateway(config.gateway.ip, config.gateway.port)
    if gw.connect():
        log.info("Gateway connected: %s:%d", config.gateway.ip, config.gateway.port)
    else:
        log.warning("Gateway not reachable, will auto-connect on first command")

    # Initialize controllers
    lights = LightController(gw, config.lights)
    fresh_air = FreshAirController(gw, config.fresh_air)

    # MQTT callbacks
    def on_connect(client, userdata, flags, reason_code, properties=None):
        rc = reason_code if isinstance(reason_code, int) else reason_code.value
        if rc != 0:
            log.error("MQTT connect failed, rc=%s", rc)
            return

        log.info("Connected to Bemfa MQTT broker")
        all_topics = list(TOPIC_LIGHT_MAP) + list(TOPIC_SCENE_MAP) + list(TOPIC_FRESH_MAP)
        for topic in all_topics:
            client.subscribe(topic, qos=0)
            time.sleep(0.2)
        log.info("Subscribed to %d topics", len(all_topics))

    def on_message(client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode("utf-8", errors="ignore").strip()
        log.info("MQTT: %s -> '%s'", topic, payload)

        # Light control
        if topic in TOPIC_LIGHT_MAP:
            light_name = TOPIC_LIGHT_MAP[topic]
            on = parse_bemfa_command(payload, is_light=True)
            if on is not None:
                lights.on(light_name) if on else lights.off(light_name)
            return

        # Scene control
        if topic in TOPIC_SCENE_MAP:
            scene_name = TOPIC_SCENE_MAP[topic]
            on = parse_bemfa_command(payload, is_light=False)
            if on is not None:
                # Software scenes: match lights by scene name from config
                scene_lights = config.software_scenes.get(scene_name, [])
                if scene_lights == ["*"]:
                    lights.on() if on else lights.off()
                else:
                    for name in scene_lights:
                        lights.on(name) if on else lights.off(name)
            return

        # Fresh air control
        if topic in TOPIC_FRESH_MAP:
            (speed,) = TOPIC_FRESH_MAP[topic]
            on = parse_bemfa_command(payload, is_light=False)
            if on is not None:
                if on:
                    fresh_air.on(speed=speed)
                else:
                    fresh_air.off()
            return

    # Connect to Bemfa
    client = mqtt.Client(
        client_id=bemfa_key,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=5)

    log.info("Connecting to Bemfa MQTT (%s:%d)...", config.bemfa.broker, config.bemfa.port)
    client.connect(config.bemfa.broker, config.bemfa.port, keepalive=60)

    # Graceful shutdown
    def shutdown(sig, frame):
        log.info("Shutting down...")
        gw.close()
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    client.loop_forever()


if __name__ == "__main__":
    main()
