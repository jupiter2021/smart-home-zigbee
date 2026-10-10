import importlib.util
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from smart_home_zigbee.config import Config, ElevatorConfig
from smart_home_zigbee.elevator import ElevatorController


SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "bemfa_bridge.py"
spec = importlib.util.spec_from_file_location("bemfa_bridge_example", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BemfaElevatorTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(elevator=ElevatorConfig(
            enabled=True,
            endpoint="http://127.0.0.1:9998/api/elevator/down",
        ))
        self.lights = Mock()
        self.fresh_air = Mock()
        self.elevator = Mock()
        self.elevator.call_down.return_value = SimpleNamespace(
            accepted=True, status="accepted",
        )
        self.handler = bridge.create_message_handler(
            self.config, self.lights, self.fresh_air, self.elevator,
        )

    def message(self, payload=b"on", **flags):
        return SimpleNamespace(
            topic=self.config.elevator.topic, payload=payload,
            retain=flags.get("retain", False), dup=flags.get("dup", False),
        )

    def test_on_calls_once(self):
        self.handler(None, None, self.message(b"  ON  "))
        self.elevator.call_down.assert_called_once_with()
        self.lights.on.assert_not_called()
        self.fresh_air.on.assert_not_called()

    def test_off_invalid_and_oversized_do_not_call(self):
        for payload in (b"off", b"on#100", b"", b"down", b"\xff", b"on" * 40):
            with self.subTest(payload=payload):
                self.handler(None, None, self.message(payload))
        self.elevator.call_down.assert_not_called()

    def test_retained_or_duplicate_on_is_ignored(self):
        self.handler(None, None, self.message(retain=True))
        self.handler(None, None, self.message(dup=True))
        self.elevator.call_down.assert_not_called()

    def test_production_dispatcher_ignores_off_retained_and_duplicate(self):
        callback = Mock()
        dispatcher = bridge._SingleElevatorDispatcher(callback)
        handler = bridge.create_message_handler(
            self.config, self.lights, self.fresh_air, self.elevator, dispatcher,
        )
        handler(None, None, self.message(b"off"))
        handler(None, None, self.message(retain=True))
        handler(None, None, self.message(dup=True))
        self.assertFalse(dispatcher._busy)
        callback.assert_not_called()
        self.elevator.call_down.assert_not_called()

    def test_dispatcher_close_rejects_new_submissions(self):
        callback = Mock()
        dispatcher = bridge._SingleElevatorDispatcher(callback)
        dispatcher.close()
        self.assertFalse(dispatcher.submit())
        callback.assert_not_called()

    def test_disabled_does_not_subscribe_or_call(self):
        self.config.elevator.enabled = False
        self.assertNotIn(self.config.elevator.topic, bridge.subscription_topics(self.config))
        self.handler(None, None, self.message())
        self.elevator.call_down.assert_not_called()

    def test_subscription_added_once(self):
        topics = bridge.subscription_topics(self.config)
        self.assertEqual(topics.count(self.config.elevator.topic), 1)
        self.assertEqual(len(topics), len(set(topics)))

    def test_topic_collision_is_rejected(self):
        self.config.elevator.topic = next(iter(bridge.TOPIC_LIGHT_MAP))
        with self.assertRaises(ValueError):
            bridge.subscription_topics(self.config)

    def test_existing_light_scene_and_fresh_air_paths(self):
        light_topic = next(iter(bridge.TOPIC_LIGHT_MAP))
        msg = SimpleNamespace(topic=light_topic, payload=b"on")
        self.handler(None, None, msg)
        self.lights.on.assert_called_once_with(bridge.TOPIC_LIGHT_MAP[light_topic])

        self.lights.reset_mock()
        self.config.software_scenes["all-example"] = ["*"]
        with patch.dict(bridge.TOPIC_SCENE_MAP, {"scene-example006": "all-example"}):
            msg = SimpleNamespace(topic="scene-example006", payload=b"off")
            self.handler(None, None, msg)
        self.lights.off.assert_called_once_with()

        msg = SimpleNamespace(topic="xinfenggf006", payload=b"on")
        self.handler(None, None, msg)
        self.fresh_air.on.assert_called_once_with(speed="high")
        self.elevator.call_down.assert_not_called()

    def test_failure_is_not_retried(self):
        self.elevator.call_down.side_effect = RuntimeError("simulated failure")
        self.handler(None, None, self.message())
        self.elevator.call_down.assert_called_once_with()

    def test_slow_elevator_does_not_block_device_commands(self):
        started = threading.Event()
        release = threading.Event()

        def slow_call():
            started.set()
            release.wait(2)
            return SimpleNamespace(accepted=True, status="accepted")

        dispatcher = bridge._SingleElevatorDispatcher(slow_call)
        handler = bridge.create_message_handler(
            self.config, self.lights, self.fresh_air, self.elevator, dispatcher,
        )
        handler(None, None, self.message())
        self.assertTrue(started.wait(1))

        start = time.monotonic()
        light_topic = next(iter(bridge.TOPIC_LIGHT_MAP))
        handler(
            None,
            None,
            SimpleNamespace(topic=light_topic, payload=b"on"),
        )
        self.assertLess(time.monotonic() - start, 0.5)
        self.lights.on.assert_called_once_with(bridge.TOPIC_LIGHT_MAP[light_topic])

        release.set()
        dispatcher.close(timeout=1)

    def test_busy_elevator_call_is_dropped_without_queueing(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_call():
            calls.append(None)
            started.set()
            release.wait(2)
            return SimpleNamespace(accepted=True, status="accepted")

        dispatcher = bridge._SingleElevatorDispatcher(slow_call)
        handler = bridge.create_message_handler(
            self.config, self.lights, self.fresh_air, self.elevator, dispatcher,
        )
        handler(None, None, self.message())
        self.assertTrue(started.wait(1))
        handler(None, None, self.message())
        self.assertEqual(len(calls), 1)

        release.set()
        dispatcher.close(timeout=1)
        self.assertEqual(len(calls), 1)

    def test_dispatcher_recovers_after_exception(self):
        calls = []
        second_call = threading.Event()

        def flaky_call():
            calls.append(None)
            if len(calls) == 1:
                raise RuntimeError("simulated failure")
            second_call.set()
            return SimpleNamespace(accepted=True, status="accepted")

        dispatcher = bridge._SingleElevatorDispatcher(flaky_call)
        self.assertTrue(dispatcher.submit())
        deadline = time.monotonic() + 1
        while len(calls) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(calls), 1)

        deadline = time.monotonic() + 1
        submitted = False
        while time.monotonic() < deadline:
            if dispatcher.submit():
                submitted = True
                break
            time.sleep(0.01)
        self.assertTrue(submitted)
        self.assertTrue(second_call.wait(1))
        dispatcher.close(timeout=1)
        self.assertEqual(len(calls), 2)

    def test_dispatcher_logs_cooldown_and_error_without_traceback(self):
        cooldown = lambda: SimpleNamespace(accepted=False, status="cooldown")
        dispatcher = bridge._SingleElevatorDispatcher(cooldown)
        with self.assertLogs(bridge.log, level="WARNING") as records:
            self.assertTrue(dispatcher.submit())
            dispatcher.close(timeout=1)
        self.assertTrue(any("cooldown" in record for record in records.output))

        def fail():
            raise RuntimeError("simulated failure")

        dispatcher = bridge._SingleElevatorDispatcher(fail)
        with self.assertLogs(bridge.log, level="ERROR") as records:
            self.assertTrue(dispatcher.submit())
            dispatcher.close(timeout=1)
        self.assertTrue(any("no retry" in record for record in records.output))
        self.assertFalse(any("Traceback" in record for record in records.output))

    def test_repeated_messages_share_controller_cooldown(self):
        with patch.dict("os.environ", {"SMZ_ELEVATOR_TOKEN": "x" * 32}):
            controller = ElevatorController(self.config.elevator)
            # A failed HTTP attempt must still reserve the physical-action cooldown.
            handler = bridge.create_message_handler(
                self.config, self.lights, self.fresh_air, controller,
            )
            with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError) as send:
                handler(None, None, self.message())
                handler(None, None, self.message())
            self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
