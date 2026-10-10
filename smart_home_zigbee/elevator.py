"""Small client for the local-server elevator down API.

The client intentionally exposes only one action: a configured down call. It
does not speak SIP and does not provide arbitrary floors, elevator selection,
access control, or retries.
"""

from __future__ import annotations

import json
import http.client
import math
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from .config import (
    ElevatorConfig,
    _strict_bool,
    _validate_elevator_endpoint,
    _validate_token_env,
)


_MAX_RESPONSE_BYTES = 8 * 1024
_STATUSES = {"accepted", "rejected", "unknown", "cooldown", "duplicate", "error"}
_HTTP_FOR_STATUS = {
    "accepted": {200},
    "rejected": {422},
    "unknown": {504},
    "cooldown": {429},
    "duplicate": {409},
    "error": {400, 401, 500},
}


@dataclass
class ElevatorResult:
    """Outcome returned by :meth:`ElevatorController.call_down`."""

    status: str
    message: str
    request_id: str
    sip_status: Optional[int] = None
    result_code: Optional[int] = None
    retry_after_seconds: Optional[float] = None

    @property
    def accepted(self) -> bool:
        """Whether the response matched SIP 2xx and business result 200."""
        return self.status == "accepted"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects instead of allowing urllib to replay the bearer token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "redirects are not allowed", headers, fp
        )


class ElevatorController:
    """Call the configured local elevator server without touching the gateway."""

    def __init__(self, config: ElevatorConfig):
        if not isinstance(config, ElevatorConfig):
            raise TypeError("config must be an ElevatorConfig")
        self.config = config
        self._lock = threading.Lock()
        self._cooldown_until = 0.0

    def call_down(self) -> ElevatorResult:
        """Request one configured down call.

        A cooldown is held after every outbound attempt, including an unknown
        result. Validation failures happen before the cooldown is consumed.
        """
        request_id = uuid.uuid4().hex
        config = self.config

        try:
            enabled = _strict_bool(config.enabled, "elevator.enabled")
        except ValueError as exc:
            return ElevatorResult("error", str(exc), request_id)
        if not enabled:
            return ElevatorResult(
                status="error",
                message="Elevator control is disabled",
                request_id=request_id,
            )

        try:
            endpoint = _validate_elevator_endpoint(config.endpoint, True)
            token_env = _validate_token_env(config.token_env)
            timeout = _positive_finite(config.timeout, "elevator.timeout")
            cooldown = _positive_finite(config.cooldown, "elevator.cooldown")
            if cooldown < 1.0:
                raise ValueError("elevator.cooldown must be at least 1")
        except ValueError as exc:
            return ElevatorResult("error", str(exc), request_id)

        token = os.environ.get(token_env)
        if not _valid_token(token):
            return ElevatorResult(
                status="error",
                message=f"Environment variable {token_env} must contain a valid token",
                request_id=request_id,
            )

        with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                return ElevatorResult(
                    status="cooldown",
                    message="Elevator request is cooling down",
                    request_id=request_id,
                    retry_after_seconds=max(0.0, self._cooldown_until - now),
                )

            # Reserve the window before opening the socket. This also covers
            # transport failures and malformed responses.
            self._cooldown_until = now + cooldown
            return self._send(endpoint, token, timeout, request_id)

    def _send(
        self, endpoint: str, token: str, timeout: float, request_id: str
    ) -> ElevatorResult:
        body = json.dumps(
            {"request_id": request_id}, separators=(",", ":")
        ).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        response = None
        try:
            # Bypass environment proxies: the bearer token is only for the
            # explicitly configured local-server endpoint.
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), _NoRedirectHandler()
            )
            response = opener.open(request, timeout=timeout)
            http_status = _response_status(response)
            response_body = _read_response(response)
            return _decode_result(request_id, http_status, response_body)
        except urllib.error.HTTPError as exc:
            response = exc
            response_body = _read_response(exc)
            return _decode_result(request_id, exc.code, response_body)
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            OSError,
            TimeoutError,
            ValueError,
        ):
            return ElevatorResult(
                status="unknown",
                message="Elevator server response is unknown",
                request_id=request_id,
            )
        finally:
            if response is not None:
                _close_response(response)


def _positive_finite(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return result


def _valid_token(value: Any) -> bool:
    """Keep the Authorization header printable and free of line injection."""
    if not isinstance(value, str) or not 32 <= len(value) <= 4096:
        return False
    return all(0x21 <= ord(char) <= 0x7E for char in value)


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()
    if isinstance(status, bool) or not isinstance(status, int):
        raise ValueError("invalid HTTP status")
    return status


def _read_response(response: Any) -> Optional[bytes]:
    try:
        # Read one extra byte so an oversized response cannot be accepted
        # after silently truncating valid-looking JSON.
        data = response.read(_MAX_RESPONSE_BYTES + 1)
    except Exception:
        return b""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data)
        if len(data) > _MAX_RESPONSE_BYTES:
            return None
        return data
    return b""


def _close_response(response: Any) -> None:
    try:
        response.close()
    except Exception:
        pass


def _decode_result(
    request_id: str, http_status: int, body: Optional[bytes]
) -> ElevatorResult:
    if body is None:
        return ElevatorResult(
            status="unknown",
            message="Elevator server response is unknown",
            request_id=request_id,
        )
    try:
        payload = json.loads(
            body.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return ElevatorResult(
            status="unknown",
            message="Elevator server response is unknown",
            request_id=request_id,
        )

    if not isinstance(payload, dict):
        return ElevatorResult(
            status="unknown",
            message="Elevator server response does not match the request",
            request_id=request_id,
        )

    status = payload.get("status")
    response_id = payload.get("request_id")
    # The panel cannot echo a request ID when authentication or request parsing
    # fails before dispatch. Only the panel's explicit pre-dispatch errors may
    # use this exception; accepted and all other outcomes still require a match.
    if (
        http_status in {400, 401}
        and status == "error"
        and ("request_id" not in payload or response_id == "")
    ):
        message = payload.get("message", "")
        if not isinstance(message, str):
            return ElevatorResult(
                status="unknown",
                message="Elevator server response is unknown",
                request_id=request_id,
            )
        sip_valid, sip_status = _optional_int(payload, "sip_status")
        result_valid, result_code = _optional_int(payload, "result_code")
        retry_valid, retry_after = _optional_nonnegative_float(
            payload, "retry_after_seconds"
        )
        if not (sip_valid and result_valid and retry_valid):
            return ElevatorResult(
                status="unknown",
                message="Elevator server response is unknown",
                request_id=request_id,
            )
        return ElevatorResult(
            status="error",
            message=message or _default_message("error"),
            request_id=request_id,
            sip_status=sip_status,
            result_code=result_code,
            retry_after_seconds=retry_after,
        )

    if response_id != request_id:
        return ElevatorResult(
            status="unknown",
            message="Elevator server response does not match the request",
            request_id=request_id,
        )

    if not isinstance(status, str) or status not in _STATUSES:
        return ElevatorResult(
            status="error",
            message="Elevator server returned an invalid status",
            request_id=request_id,
        )

    message = payload.get("message", "")
    if not isinstance(message, str):
        return ElevatorResult(
            status="unknown",
            message="Elevator server response is unknown",
            request_id=request_id,
        )

    sip_valid, sip_status = _optional_int(payload, "sip_status")
    result_valid, result_code = _optional_int(payload, "result_code")
    retry_valid, retry_after = _optional_nonnegative_float(
        payload, "retry_after_seconds"
    )
    if not (sip_valid and result_valid and retry_valid):
        return ElevatorResult(
            status="unknown",
            message="Elevator server response is unknown",
            request_id=request_id,
        )

    if http_status not in _HTTP_FOR_STATUS[status]:
        return ElevatorResult(
            status="error",
            message="Elevator server returned an inconsistent HTTP status",
            request_id=request_id,
            sip_status=sip_status,
            result_code=result_code,
            retry_after_seconds=retry_after,
        )

    if status == "accepted":
        if (
            sip_status is None
            or not 200 <= sip_status <= 299
            or result_code != 200
        ):
            return ElevatorResult(
                status="error",
                message="Elevator server returned an invalid accepted result",
                request_id=request_id,
                sip_status=sip_status,
                result_code=result_code,
                retry_after_seconds=retry_after,
            )

    return ElevatorResult(
        status=status,
        message=message or _default_message(status),
        request_id=request_id,
        sip_status=sip_status,
        result_code=result_code,
        retry_after_seconds=retry_after,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject duplicate response fields rather than silently choosing one."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _optional_int(payload: dict[str, Any], key: str) -> tuple[bool, Optional[int]]:
    if key not in payload or payload[key] is None:
        return True, None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        return False, None
    return True, value


def _optional_nonnegative_float(
    payload: dict[str, Any], key: str
) -> tuple[bool, Optional[float]]:
    if key not in payload or payload[key] is None:
        return True, None
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return False, None
    return True, result


def _default_message(status: str) -> str:
    return {
        "accepted": "Elevator request accepted",
        "rejected": "Elevator request rejected",
        "unknown": "Elevator server response is unknown",
        "cooldown": "Elevator request is cooling down",
        "duplicate": "Elevator request is a duplicate",
        "error": "Elevator request failed",
    }[status]


__all__ = ["ElevatorController", "ElevatorResult"]
