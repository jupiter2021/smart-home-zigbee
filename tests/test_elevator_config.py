import math
import unittest

from smart_home_zigbee.config import ElevatorConfig, _parse_config


class ElevatorConfigTests(unittest.TestCase):
    def test_old_config_defaults_to_disabled_elevator(self):
        config = _parse_config({"gateway": {"ip": "192.0.2.1"}})
        self.assertFalse(config.elevator.enabled)
        self.assertEqual(config.elevator.endpoint, "")
        self.assertEqual(config.elevator.token_env, "SMZ_ELEVATOR_TOKEN")
        self.assertEqual(config.elevator.topic, "dianti006")

    def test_valid_mapping_is_parsed(self):
        config = _parse_config(
            {
                "elevator": {
                    "enabled": True,
                    "endpoint": "https://panel.example/api/elevator/down",
                    "token_env": "MY_TOKEN",
                    "timeout": 2.5,
                    "cooldown": 3,
                    "topic": "elevator.down",
                }
            }
        )
        self.assertEqual(
            config.elevator,
            ElevatorConfig(
                enabled=True,
                endpoint="https://panel.example/api/elevator/down",
                token_env="MY_TOKEN",
                timeout=2.5,
                cooldown=3,
                topic="elevator.down",
            ),
        )

    def test_enabled_requires_fixed_absolute_endpoint(self):
        for endpoint in (
            "",
            "/api/elevator/down",
            "panel.example/api/elevator/down",
            "ftp://panel.example/api/elevator/down",
            "http://panel.example/other",
            "http://user:pass@panel.example/api/elevator/down",
            "http://panel.example/api/elevator/down?x=1",
            "http://panel.example/api/elevator/down#fragment",
            "http://panel.example/api/elevator/ down",
            "http:// panel.example/api/elevator/down",
            "http://panel.example\\api/elevator/down",
            "http://panel.example/api/elevator/\u00a0down",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    ElevatorConfig(enabled=True, endpoint=endpoint)

    def test_disabled_endpoint_is_not_required(self):
        config = ElevatorConfig(enabled=False, endpoint="")
        self.assertEqual(config.endpoint, "")

    def test_strict_enabled_bool(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    _parse_config({"elevator": {"enabled": value}})

    def test_timeout_and_cooldown_must_be_finite_and_positive(self):
        for field in ("timeout", "cooldown"):
            for value in (
                0,
                -1,
                math.nan,
                math.inf,
                10**10000,
                "5",
                True,
            ):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        _parse_config({"elevator": {field: value}})
        with self.assertRaises(ValueError):
            _parse_config({"elevator": {"cooldown": 0.5}})

    def test_environment_name_is_validated(self):
        for value in ("", "1TOKEN", "TOKEN-NAME", "TOKEN NAME", "TOKEN\n"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    _parse_config({"elevator": {"token_env": value}})

    def test_literal_credentials_are_rejected(self):
        for key in ("token", "password", "username", "identity"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    _parse_config({"elevator": {key: "secret"}})

    def test_topic_is_nonempty_literal(self):
        for topic in ("", "+", "#", "elevator/+", "elevator/#", "bad\x00topic"):
            with self.subTest(topic=repr(topic)):
                with self.assertRaises(ValueError):
                    _parse_config({"elevator": {"topic": topic}})

    def test_elevator_must_be_mapping(self):
        with self.assertRaises(ValueError):
            _parse_config({"elevator": True})


if __name__ == "__main__":
    unittest.main()
