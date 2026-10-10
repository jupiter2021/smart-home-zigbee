import json
import http.client
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from smart_home_zigbee.config import ElevatorConfig
from smart_home_zigbee.elevator import ElevatorController, ElevatorResult


TOKEN = "t" * 32
ENDPOINT = "http://127.0.0.1:1/api/elevator/down"


class FakeResponse:
    def __init__(self, status=200, body=b"", close=None):
        self.status = status
        self._body = body
        self.closed = False
        self._close_callback = close

    def read(self, limit=-1):
        if limit >= 0:
            return self._body[:limit]
        return self._body

    def close(self):
        self.closed = True
        if self._close_callback:
            self._close_callback()


class FakeOpener:
    def __init__(self, response=None, error=None, callback=None):
        self.response = response
        self.error = error
        self.callback = callback
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.callback:
            self.callback(request, timeout)
        if self.error:
            raise self.error
        return self.response


def response_payload(request_id, status="accepted", **extra):
    body = {
        "request_id": request_id,
        "status": status,
        "message": status,
    }
    if status == "accepted":
        body.update(sip_status=200, result_code=200)
    body.update(extra)
    return body


class ElevatorClientTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"SMZ_ELEVATOR_TOKEN": TOKEN}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = ElevatorConfig(
            enabled=True, endpoint=ENDPOINT, cooldown=1, timeout=2
        )

    def controller(self, response_body=None, status=200, error=None, callback=None):
        opener = FakeOpener(
            response=FakeResponse(status, response_body or b""),
            error=error,
            callback=callback,
        )
        patcher = patch(
            "smart_home_zigbee.elevator.urllib.request.build_opener",
            return_value=opener,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return ElevatorController(self.config), opener

    def accepted_client(self):
        # The fake response needs the generated request ID, so make a response
        # after the request reaches the fake opener.
        def callback(request, _timeout):
            request_id = json.loads(request.data.decode())["request_id"]
            opener.response._body = json.dumps(
                response_payload(request_id)
            ).encode()

        opener = FakeOpener(response=FakeResponse())
        patcher = patch(
            "smart_home_zigbee.elevator.urllib.request.build_opener",
            return_value=opener,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        opener.callback = lambda request, timeout: (
            setattr(
                opener.response,
                "_body",
                json.dumps(
                    response_payload(json.loads(request.data.decode())["request_id"])
                ).encode(),
            )
        )
        return ElevatorController(self.config), opener

    def test_accepted_auth_and_request_body(self):
        client, opener = self.accepted_client()
        result = client.call_down()

        self.assertTrue(result.accepted)
        self.assertEqual(result.status, "accepted")
        self.assertEqual(result.sip_status, 200)
        self.assertEqual(result.result_code, 200)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(json.loads(request.data.decode()), {"request_id": result.request_id})
        self.assertEqual(timeout, 2.0)

    def test_token_is_required_and_invalid_tokens_do_not_send(self):
        for token in (None, "short", "x" * 4097, "x" * 31, "x" * 31 + "\n"):
            with self.subTest(token=repr(token)):
                if token is None:
                    os.environ.pop("SMZ_ELEVATOR_TOKEN", None)
                else:
                    os.environ["SMZ_ELEVATOR_TOKEN"] = token
                with patch("smart_home_zigbee.elevator.urllib.request.build_opener") as build:
                    result = ElevatorController(self.config).call_down()
                self.assertEqual(result.status, "error")
                build.assert_not_called()
        os.environ["SMZ_ELEVATOR_TOKEN"] = TOKEN

    def test_status_mapping_and_http_consistency(self):
        cases = (
            ("rejected", 422),
            ("unknown", 504),
            ("cooldown", 429),
            ("duplicate", 409),
            ("error", 500),
        )
        for status, http_status in cases:
            with self.subTest(status=status):
                request_id = "request"
                # Replace the UUID to make the response match deterministically.
                payload = response_payload(request_id, status)
                client, opener = self.controller(
                    json.dumps(payload).encode(), status=status and http_status
                )
                with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
                    uuid4.return_value.hex = request_id
                    result = client.call_down()
                self.assertEqual(result.status, status)
                self.assertFalse(result.accepted)

    def test_auth_error_and_wrong_http_status_are_not_accepted(self):
        request_id = "auth-request"
        payload = response_payload(request_id, "error")
        client, _ = self.controller(json.dumps(payload).encode(), status=401)
        with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = request_id
            result = client.call_down()
        self.assertEqual(result.status, "error")
        self.assertFalse(result.accepted)

        client, _ = self.controller(
            json.dumps(response_payload(request_id)).encode(), status=422
        )
        with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = request_id
            result = client.call_down()
        self.assertEqual(result.status, "error")

    def test_pre_dispatch_errors_without_id_are_explicit_errors(self):
        request_id = "pre-dispatch-request"
        cases = (
            (
                401,
                {"request_id": "", "status": "error", "message": "unauthorized"},
            ),
            (
                400,
                {"status": "error", "message": "invalid request"},
            ),
        )
        for http_status, payload in cases:
            with self.subTest(http_status=http_status, payload=payload):
                client, _ = self.controller(
                    json.dumps(payload).encode(), status=http_status
                )
                with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
                    uuid4.return_value.hex = request_id
                    result = client.call_down()
                self.assertEqual(result.status, "error")
                self.assertEqual(result.message, payload["message"])
                self.assertEqual(result.request_id, request_id)
                self.assertFalse(result.accepted)

    def test_pre_dispatch_exception_does_not_skip_id_or_status_validation(self):
        request_id = "pre-dispatch-request"
        cases = (
            (
                401,
                {"request_id": "other", "status": "error", "message": "unauthorized"},
            ),
            (
                400,
                {"request_id": "other", "status": "error", "message": "invalid request"},
            ),
            (500, {"request_id": "", "status": "error", "message": "server error"}),
            (400, {"request_id": "", "status": "not-a-status", "message": "invalid request"}),
            (200, {"status": "accepted", "sip_status": 200, "result_code": 200}),
            (
                200,
                {"request_id": "", "status": "accepted", "sip_status": 200, "result_code": 200},
            ),
        )
        for http_status, payload in cases:
            with self.subTest(http_status=http_status, payload=payload):
                client, _ = self.controller(
                    json.dumps(payload).encode(), status=http_status
                )
                with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
                    uuid4.return_value.hex = request_id
                    result = client.call_down()
                self.assertEqual(result.status, "unknown")
                self.assertFalse(result.accepted)

    def test_mismatch_missing_accepted_codes_and_bad_json_are_unknown_or_error(self):
        request_id = "request"
        cases = (
            {"request_id": "other", "status": "accepted", "sip_status": 200, "result_code": 200},
            {"request_id": request_id, "status": "accepted", "result_code": 200},
            {"request_id": request_id, "status": "accepted", "sip_status": 200},
            {"request_id": request_id, "status": "accepted", "sip_status": 500, "result_code": 200},
            {"request_id": request_id, "status": "accepted", "sip_status": 200, "result_code": 500},
            {"request_id": request_id, "status": "not-a-status"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                client, _ = self.controller(json.dumps(payload).encode(), status=200)
                with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
                    uuid4.return_value.hex = request_id
                    result = client.call_down()
                self.assertIn(result.status, {"error", "unknown"})
                self.assertFalse(result.accepted)

        duplicate = (
            '{"request_id":"request","request_id":"request",'
            '"status":"accepted","sip_status":200,"result_code":200}'
        ).encode()
        client, _ = self.controller(duplicate, status=200)
        with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = request_id
            result = client.call_down()
        self.assertEqual(result.status, "unknown")

    def test_oversize_body_is_unknown(self):
        body = b"{" + b"x" * 8192 + b"}"
        client, _ = self.controller(body, status=200)
        result = client.call_down()
        self.assertEqual(result.status, "unknown")

    def test_redirect_is_rejected_and_bearer_is_not_replayed(self):
        request_id = "redirect-request"
        fp = FakeResponse(close=lambda: None)
        redirect = urllib.error.HTTPError(
            ENDPOINT, 302, "redirect", {"Location": "http://127.0.0.1:2/"}, fp
        )
        client, opener = self.controller(error=redirect)
        with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = request_id
            result = client.call_down()
        self.assertEqual(result.status, "unknown")
        self.assertEqual(opener.calls[0][0].get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertTrue(fp.closed)

    def test_http_error_response_is_closed(self):
        request_id = "error-request"
        fp = FakeResponse()
        error = urllib.error.HTTPError(
            ENDPOINT, 500, "server", {}, fp
        )
        close = Mock(wraps=error.close)
        error.close = close
        client, _ = self.controller(error=error)
        with patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = request_id
            result = client.call_down()
        self.assertEqual(result.status, "unknown")
        close.assert_called_once_with()
        self.assertTrue(fp.closed)

    def test_proxy_environment_is_disabled(self):
        request_id = "proxy-request"
        opener = FakeOpener(response=FakeResponse())
        opener.callback = lambda request, timeout: setattr(
            opener.response,
            "_body",
            json.dumps(response_payload(request_id)).encode(),
        )
        captured = {}

        def build(*handlers):
            captured["handlers"] = handlers
            return opener

        with patch(
            "smart_home_zigbee.elevator.urllib.request.build_opener", side_effect=build
        ), patch("smart_home_zigbee.elevator.uuid.uuid4") as uuid4:
            uuid4.return_value.hex = request_id
            result = ElevatorController(self.config).call_down()
        self.assertTrue(result.accepted)
        self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) for h in captured["handlers"]))
        proxy = next(h for h in captured["handlers"] if isinstance(h, urllib.request.ProxyHandler))
        self.assertEqual(proxy.proxies, {})

    def test_network_exception_consumes_cooldown(self):
        opener = FakeOpener(error=urllib.error.URLError("offline"))
        with patch(
            "smart_home_zigbee.elevator.urllib.request.build_opener", return_value=opener
        ):
            client = ElevatorController(self.config)
            first = client.call_down()
            second = client.call_down()
        self.assertEqual(first.status, "unknown")
        self.assertEqual(second.status, "cooldown")
        self.assertEqual(len(opener.calls), 1)

    def test_http_exception_returns_unknown_and_consumes_cooldown(self):
        opener = FakeOpener(error=http.client.BadStatusLine("malformed response"))
        with patch(
            "smart_home_zigbee.elevator.urllib.request.build_opener", return_value=opener
        ):
            client = ElevatorController(self.config)
            first = client.call_down()
            second = client.call_down()
        self.assertEqual(first.status, "unknown")
        self.assertEqual(second.status, "cooldown")
        self.assertEqual(len(opener.calls), 1)

    def test_cooldown_is_thread_safe(self):
        entered = threading.Event()
        release = threading.Event()
        opener = FakeOpener(response=FakeResponse())

        def callback(_request, _timeout):
            entered.set()
            self.assertTrue(release.wait(2))

        opener.callback = callback
        with patch(
            "smart_home_zigbee.elevator.urllib.request.build_opener", return_value=opener
        ):
            client = ElevatorController(self.config)
            results = []
            first = threading.Thread(target=lambda: results.append(client.call_down()))
            second = threading.Thread(target=lambda: results.append(client.call_down()))
            first.start()
            self.assertTrue(entered.wait(2))
            second.start()
            time.sleep(0.05)
            release.set()
            first.join(2)
            second.join(2)
        self.assertEqual(len(results), 2)
        self.assertEqual({result.status for result in results}, {"unknown", "cooldown"})
        self.assertEqual(len(opener.calls), 1)


if __name__ == "__main__":
    unittest.main()
