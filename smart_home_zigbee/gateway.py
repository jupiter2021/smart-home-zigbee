"""
Zigbee Gateway TCP Connection Manager
======================================

Manages a persistent TCP connection to the DNAKE Zigbee gateway.
Features auto-reconnection, periodic heartbeat keepalive, and thread-safe send.
"""

import socket
import logging
import threading
from typing import Optional

from .protocol import build_heartbeat_packet

log = logging.getLogger(__name__)


class Gateway:
    """
    Persistent TCP connection to the Zigbee gateway.

    Maintains a single long-lived TCP socket, auto-reconnects on failure,
    and sends periodic heartbeats to keep the connection alive.

    Usage::

        gw = Gateway("192.168.71.12")
        gw.connect()
        gw.send(some_packet)
        gw.close()

    Or as a context manager::

        with Gateway("192.168.71.12") as gw:
            gw.send(some_packet)

    Args:
        ip: Gateway IP address
        port: Gateway TCP port (default 4196)
        timeout: TCP connection timeout in seconds (default 5)
        keepalive_interval: Seconds between heartbeat packets (default 30)
    """

    DEFAULT_PORT = 4196

    def __init__(
        self,
        ip: str,
        port: int = DEFAULT_PORT,
        timeout: int = 5,
        keepalive_interval: int = 30,
    ):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.keepalive_interval = keepalive_interval
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._keepalive_timer: Optional[threading.Timer] = None
        self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self) -> bool:
        """Establish TCP connection to gateway."""
        with self._lock:
            return self._connect_locked()

    def _connect_locked(self) -> bool:
        """Internal connect (must hold lock)."""
        self._close_locked()
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self.timeout)
            self._sock.connect((self.ip, self.port))
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self._connected = True
            log.info("Gateway connected (%s:%d)", self.ip, self.port)
            self._schedule_keepalive()
            return True
        except Exception as e:
            log.error("Gateway connect failed: %s", e)
            self._sock = None
            self._connected = False
            return False

    def _close_locked(self):
        """Internal close (must hold lock)."""
        self._cancel_keepalive()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._connected = False

    def close(self):
        """Close the connection."""
        with self._lock:
            self._close_locked()
            log.info("Gateway connection closed")

    @property
    def connected(self) -> bool:
        """Whether the gateway is currently connected."""
        return self._connected

    def send(self, packet: bytes) -> bool:
        """
        Send a binary packet, auto-reconnecting if needed.

        Args:
            packet: Raw binary packet to send

        Returns:
            True if sent successfully, False on failure
        """
        with self._lock:
            for attempt in range(2):
                if not self._connected or not self._sock:
                    if not self._connect_locked():
                        return False
                try:
                    self._sock.sendall(packet)
                    self._schedule_keepalive()
                    return True
                except Exception as e:
                    log.warning("TCP send failed (attempt %d): %s", attempt + 1, e)
                    self._close_locked()
            return False

    def _schedule_keepalive(self):
        """Schedule next heartbeat."""
        self._cancel_keepalive()
        self._keepalive_timer = threading.Timer(
            self.keepalive_interval, self._send_keepalive
        )
        self._keepalive_timer.daemon = True
        self._keepalive_timer.start()

    def _cancel_keepalive(self):
        """Cancel pending heartbeat timer."""
        if self._keepalive_timer:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None

    def _send_keepalive(self):
        """Send heartbeat to keep connection alive."""
        with self._lock:
            if not self._connected or not self._sock:
                return
            try:
                self._sock.sendall(build_heartbeat_packet())
                log.debug("Gateway keepalive sent")
                self._schedule_keepalive()
            except Exception as e:
                log.warning("Keepalive failed: %s, will reconnect on next command", e)
                self._close_locked()

    def __repr__(self):
        status = "connected" if self._connected else "disconnected"
        return f"Gateway({self.ip}:{self.port}, {status})"
