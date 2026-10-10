"""Optional local-only integration tests against a built panel component.

Set SMZ_PANEL_ELEVATOR_TEST_BINARY to the native test executable. All HTTP and
SIP sockets in these tests are bound to loopback with synthetic identities.
"""

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from smart_home_zigbee.config import Config, ElevatorConfig
from smart_home_zigbee.elevator import ElevatorController


BINARY = os.environ.get("SMZ_PANEL_ELEVATOR_TEST_BINARY", "")
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(BINARY and Path(BINARY).is_file(), "native panel test binary not configured")
class ElevatorIntegrationTests(unittest.TestCase):
    @contextlib.contextmanager
    def component(self, sip_status=200, result_code=200):
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(0.1)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        http_port = probe.getsockname()[1]
        probe.close()
        stop = threading.Event()
        requests = []

        def reply():
            while not stop.is_set():
                try:
                    packet, source = udp.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    return
                requests.append(packet)
                header, _ = packet.decode("utf-8").split("\r\n\r\n", 1)
                headers = {}
                for line in header.split("\r\n")[1:]:
                    name, value = line.split(":", 1)
                    headers[name.lower()] = value.strip()
                if result_code is None:
                    body = b""
                else:
                    body = f"<params><resultCode>{result_code}</resultCode></params>".encode()
                response = (
                    f"SIP/2.0 {sip_status} Test\r\n"
                    f"Via: {headers['via']}\r\n"
                    f"Call-ID: {headers['call-id']}\r\n"
                    f"CSeq: {headers['cseq']}\r\n"
                    "Content-Type: text/plain\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode() + body
                udp.sendto(response, source)

        thread = threading.Thread(target=reply, daemon=True)
        thread.start()
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix="smz-elevator-loopback-") as directory:
                config_path = Path(directory) / "config.json"
                config_path.write_text(json.dumps({
                    "listen_addr": f"127.0.0.1:{http_port}",
                    "timeout_seconds": 1,
                    "cooldown_seconds": 30,
                    "sip": {
                        "source_ip": "127.0.0.1", "source_id": "1000001",
                        "target_ip": "127.0.0.1", "target_id": "1000002",
                        "port": udp.getsockname()[1], "floor": 1, "family": 1,
                    },
                }), encoding="utf-8")
                token = "integration-only-" + os.urandom(16).hex()
                environment = dict(os.environ, SMZ_ELEVATOR_TOKEN=token)
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                process = subprocess.Popen(
                    [BINARY, "-config", str(config_path)], env=environment,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    creationflags=flags,
                )
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        self.fail("loopback panel component exited before startup")
                    try:
                        with socket.create_connection(("127.0.0.1", http_port), timeout=0.1):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            self.fail("loopback panel component did not start")
                        time.sleep(0.02)
                cfg = ElevatorConfig(
                    enabled=True,
                    endpoint=f"http://127.0.0.1:{http_port}/api/elevator/down",
                    timeout=3,
                )
                with patch.dict(os.environ, {"SMZ_ELEVATOR_TOKEN": token}):
                    yield cfg, requests
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
            stop.set()
            thread.join(timeout=1)
            udp.close()

    def test_python_to_go_to_sip_acceptance_and_global_cooldown(self):
        with self.component() as (cfg, requests):
            result = ElevatorController(cfg).call_down()
            self.assertTrue(result.accepted)
            self.assertEqual((result.sip_status, result.result_code), (200, 200))
            self.assertEqual(len(requests), 1)
            self.assertIn(b"<direct>2</direct>", requests[0])
            self.assertIn(b"/elev/appoint", requests[0])
            self.assertNotIn(b"/elev/join", requests[0])
            self.assertNotIn(b"/talk/unlock", requests[0])
            second = ElevatorController(cfg).call_down()
            self.assertEqual(second.status, "cooldown")
            self.assertEqual(len(requests), 1)

    def test_sip_failure_and_missing_business_result_are_not_success(self):
        for sip_status, result_code, expected in (
            (403, 403, "rejected"),
            (200, 403, "rejected"),
            (200, None, "unknown"),
        ):
            with self.subTest(sip_status=sip_status, result_code=result_code):
                with self.component(sip_status, result_code) as (cfg, requests):
                    result = ElevatorController(cfg).call_down()
                    self.assertFalse(result.accepted)
                    self.assertEqual(result.status, expected)
                    self.assertEqual(len(requests), 1)

    def test_wrong_token_is_an_explicit_error_and_sends_no_sip(self):
        with self.component() as (cfg, requests):
            with patch.dict(os.environ, {"SMZ_ELEVATOR_TOKEN": "invalid-local-test-" + "x" * 32}):
                result = ElevatorController(cfg).call_down()
            self.assertEqual(result.status, "error")
            self.assertEqual(result.message, "unauthorized")
            self.assertFalse(result.accepted)
            self.assertEqual(len(requests), 0)

    def test_bemfa_callback_uses_same_protected_path(self):
        spec = importlib.util.spec_from_file_location(
            "bemfa_loopback_integration", ROOT / "examples" / "bemfa_bridge.py",
        )
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        with self.component() as (cfg, requests):
            config = Config(elevator=cfg)
            controller = ElevatorController(cfg)
            started = threading.Event()
            release = threading.Event()

            def call_down():
                result = controller.call_down()
                started.set()
                release.wait(2)
                return result

            dispatcher = bridge._SingleElevatorDispatcher(call_down)
            lights = Mock()
            callback = bridge.create_message_handler(
                config, lights, Mock(), controller, dispatcher,
            )

            def msg(payload, retain=False, dup=False):
                return SimpleNamespace(
                    topic=cfg.topic, payload=payload, retain=retain, dup=dup,
                )

            try:
                callback(None, None, msg(b"on", retain=True))
                callback(None, None, msg(b"off"))
                self.assertEqual(len(requests), 0)
                callback(None, None, msg(b"on"))
                self.assertTrue(started.wait(2))
                callback(None, None, msg(b"on"))
                callback(None, None, msg(b"on", dup=True))
                light_topic = next(iter(bridge.TOPIC_LIGHT_MAP))
                callback(None, None, SimpleNamespace(topic=light_topic, payload=b"on"))
                lights.on.assert_called_once_with(bridge.TOPIC_LIGHT_MAP[light_topic])
                self.assertEqual(len(requests), 1)
            finally:
                release.set()
                dispatcher.close(timeout=2)
            self.assertEqual(len(requests), 1)


if __name__ == "__main__":
    unittest.main()
