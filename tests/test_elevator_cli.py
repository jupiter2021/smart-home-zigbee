import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from smart_home_zigbee.cli import main
from smart_home_zigbee.config import Config, ElevatorConfig
from smart_home_zigbee.elevator import ElevatorResult


class ElevatorCliTests(unittest.TestCase):
    def test_elevator_down_does_not_construct_gateway(self):
        config = Config(
            elevator=ElevatorConfig(
                enabled=True,
                endpoint="http://127.0.0.1:1/api/elevator/down",
            )
        )
        result = ElevatorResult(
            status="accepted",
            message="accepted",
            request_id="request",
            sip_status=200,
            result_code=200,
        )
        controller = Mock()
        controller.return_value.call_down.return_value = result
        output = io.StringIO()
        with patch("smart_home_zigbee.cli.load_config", return_value=config), \
                patch("smart_home_zigbee.cli.Gateway") as gateway, \
                patch("smart_home_zigbee.cli.ElevatorController", controller), \
                contextlib.redirect_stdout(output):
            code = main(["elevator", "down"])
        self.assertEqual(code, 0)
        gateway.assert_not_called()
        controller.assert_called_once_with(config.elevator)
        controller.return_value.call_down.assert_called_once_with()
        self.assertIn("arrival is not confirmed", output.getvalue())

    def test_disabled_elevator_does_not_go_to_gateway_or_network(self):
        config = Config()
        output = io.StringIO()
        with patch("smart_home_zigbee.cli.load_config", return_value=config), \
                patch("smart_home_zigbee.cli.Gateway") as gateway, \
                patch("smart_home_zigbee.elevator.urllib.request.build_opener") as opener, \
                contextlib.redirect_stdout(output):
            code = main(["elevator", "down"])
        self.assertEqual(code, 1)
        gateway.assert_not_called()
        opener.assert_not_called()
        self.assertIn("disabled", output.getvalue())

    def test_wrong_action_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            main(["elevator", "up"])
        self.assertEqual(raised.exception.code, 2)

    def test_client_error_is_friendly_and_nonzero(self):
        config = Config(
            elevator=ElevatorConfig(
                enabled=True,
                endpoint="http://127.0.0.1:1/api/elevator/down",
            )
        )
        result = ElevatorResult(
            status="unknown",
            message="Elevator server response is unknown",
            request_id="request",
        )
        controller = Mock()
        controller.return_value.call_down.return_value = result
        output = io.StringIO()
        with patch("smart_home_zigbee.cli.load_config", return_value=config), \
                patch("smart_home_zigbee.cli.Gateway") as gateway, \
                patch("smart_home_zigbee.cli.ElevatorController", controller), \
                contextlib.redirect_stdout(output):
            code = main(["elevator", "down"])
        self.assertEqual(code, 1)
        gateway.assert_not_called()
        self.assertIn("unknown", output.getvalue())

    def test_config_value_error_is_friendly(self):
        output = io.StringIO()
        with patch(
            "smart_home_zigbee.cli.load_config",
            side_effect=ValueError("bad elevator configuration"),
        ), patch("smart_home_zigbee.cli.Gateway") as gateway, contextlib.redirect_stdout(output):
            code = main(["elevator", "down"])
        self.assertEqual(code, 1)
        gateway.assert_not_called()
        self.assertIn("bad elevator configuration", output.getvalue())


if __name__ == "__main__":
    unittest.main()
