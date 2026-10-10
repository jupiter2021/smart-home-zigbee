import contextlib
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from smart_home_zigbee import load_config
from smart_home_zigbee.cli import main
from smart_home_zigbee.config import HardwareScene, LightDevice
from smart_home_zigbee.light import LightController
from smart_home_zigbee.scene import SceneController
from smart_home_zigbee.protocol import (
    CH_TYPE_AC,
    CH_TYPE_FRESH_AIR,
    CH_TYPE_HEAT,
    CMD_ON,
    build_ac_packet,
    build_fresh_air_packet,
    build_heat_packet,
    build_heartbeat_packet,
    build_light_packet,
    build_scene_packet,
    decode_temp,
    encode_temp,
)


ROOT = Path(__file__).resolve().parents[1]


class ExistingControlsTests(unittest.TestCase):
    def test_light_packet_wire_format_is_unchanged(self):
        self.assertEqual(
            build_light_packet(0x51, 2, True),
            bytes.fromhex("A9 20 01 51 00 02 01 00 00 1E"),
        )
        self.assertEqual(build_light_packet(0x51, 2, False)[6], 2)

    def test_scene_and_heartbeat_checksums(self):
        for packet in (build_scene_packet(5, 0), build_heartbeat_packet()):
            self.assertEqual(len(packet), 10)
            self.assertEqual(packet[:2], b"\xa9\x20")
            self.assertEqual(packet[-1], sum(packet[:-1]) & 0xFF)

    def test_extension_device_channels_are_unchanged(self):
        cases = (
            (build_ac_packet(1, 2, CMD_ON), CH_TYPE_AC),
            (build_fresh_air_packet(3, 1, CMD_ON), CH_TYPE_FRESH_AIR),
            (build_heat_packet(1, 2, CMD_ON), CH_TYPE_HEAT),
        )
        for packet, channel in cases:
            with self.subTest(channel=channel):
                self.assertEqual(len(packet), 12)
                self.assertEqual(packet[4], channel)
                self.assertEqual(packet[-1], sum(packet[:-1]) & 0xFF)

    def test_temperature_round_trip(self):
        self.assertEqual(encode_temp(24), (0, 240))
        self.assertEqual(decode_temp(*encode_temp(32)), 32.0)

    def test_light_selection_priority(self):
        devices = [
            LightDevice("room-main", 1, 1, "room"),
            LightDevice("room-strip", 1, 2, "room"),
            LightDevice("hall-main", 2, 1, "hall"),
        ]
        gateway = Mock()
        gateway.send.return_value = True
        lights = LightController(gateway, devices, cmd_delay=0)
        self.assertEqual(len(lights.match("room")), 2)
        self.assertEqual(lights.match("room-main"), devices[:1])
        self.assertEqual(len(lights.match("main")), 2)
        self.assertEqual(lights.match("missing"), [])
        self.assertEqual(len(lights.on("all")), 3)
        self.assertEqual(gateway.send.call_count, 3)

    def test_hardware_and_software_scene_paths(self):
        gateway = Mock()
        gateway.send.return_value = True
        devices = [LightDevice("example-light", 1, 1)]
        lights = LightController(gateway, devices, cmd_delay=0)
        scenes = SceneController(
            gateway, [HardwareScene("hardware", 5, 0)],
            {"software": ["*"]}, lights,
        )
        self.assertTrue(scenes.execute("hardware"))
        gateway.send.assert_called_with(build_scene_packet(5, 0))
        self.assertTrue(scenes.execute("software", on=False))
        gateway.send.assert_called_with(build_light_packet(1, 1, False))
        self.assertFalse(scenes.execute("missing"))

    def test_example_config_preserves_existing_devices(self):
        config = load_config(str(ROOT / "config.example.yaml"))
        self.assertEqual((len(config.lights), len(config.acs), len(config.heats)), (11, 4, 4))
        self.assertEqual((len(config.hardware_scenes), len(config.software_scenes)), (2, 7))
        self.assertFalse(config.elevator.enabled)

    def test_all_existing_list_commands_remain_offline(self):
        with patch("smart_home_zigbee.gateway.socket.socket") as socket_factory:
            for target in ("lights", "scenes", "acs", "heats"):
                with self.subTest(target=target), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main([
                        "-c", str(ROOT / "config.example.yaml"), "list", target,
                    ]), 0)
            socket_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
