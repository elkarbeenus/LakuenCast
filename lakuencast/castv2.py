"""Minimaler Chromecast-Client (Cast-Protokoll v2) ohne externe Bibliotheken.

Ein Chromecast spricht TLS auf Port 8009. Jede Nachricht ist ein Protobuf
"CastMessage" mit 4-Byte-Längenpräfix. Wir brauchen davon nur wenige Felder,
deshalb wird das Protobuf hier von Hand kodiert.
"""

from __future__ import annotations

import json
import logging
import queue
import select
import socket
import ssl
import struct
import threading
import time
from typing import Callable

log = logging.getLogger("lakuencast.cast")

NS_CONNECTION = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"

DEFAULT_MEDIA_RECEIVER = "CC1AD845"
SENDER_ID = "sender-lakuencast"
RECEIVER_ID = "receiver-0"


# ---------------------------------------------------------------- Protobuf

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def encode_message(source: str, dest: str, namespace: str, payload: str) -> bytes:
    def field_str(num: int, s: str) -> bytes:
        data = s.encode("utf-8")
        return _varint((num << 3) | 2) + _varint(len(data)) + data

    msg = (
        _varint((1 << 3) | 0) + _varint(0)          # protocol_version = CASTV2_1_0
        + field_str(2, source)
        + field_str(3, dest)
        + field_str(4, namespace)
        + _varint((5 << 3) | 0) + _varint(0)        # payload_type = STRING
        + field_str(6, payload)
    )
    return struct.pack(">I", len(msg)) + msg


def decode_message(msg: bytes) -> dict:
    """Gibt {source, dest, namespace, payload} zurück."""
    names = {2: "source", 3: "dest", 4: "namespace", 6: "payload"}
    out: dict = {}
    pos = 0
    while pos < len(msg):
        key, pos = _read_varint(msg, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            _, pos = _read_varint(msg, pos)
        elif wire == 2:
            length, pos = _read_varint(msg, pos)
            data = msg[pos:pos + length]
            pos += length
            if num in names:
                out[names[num]] = data.decode("utf-8", "replace")
        else:
            raise ValueError(f"Unbekannter Protobuf-Typ {wire}")
    return out


# ---------------------------------------------------------------- Verbindung

class CastDevice:
    """Eine Verbindung zu einem Chromecast. Thread-sicher."""

    def __init__(self, host: str, port: int = 8009, name: str = "",
                 on_media_status: Callable[[dict], None] | None = None,
                 on_disconnect: Callable[[], None] | None = None):
        self.host = host
        self.port = port
        self.name = name or host
        self.on_media_status = on_media_status
        self.on_disconnect = on_disconnect

        self._sock: ssl.SSLSocket | None = None
        self._lock = threading.Lock()
        # OpenSSL verträgt kein gleichzeitiges Lesen und Schreiben aus zwei Threads.
        # Deshalb schreibt nur der I/O-Thread; andere Threads legen Nachrichten in die Warteschlange.
        self._outbox: queue.Queue[bytes] = queue.Queue()
        self._wake_r, self._wake_w = socket.socketpair()
        self._request_id = 0
        self._waiters: dict[int, tuple[threading.Event, list]] = {}
        self._running = False

        self.receiver_status: dict = {}
        self.media_status: dict = {}
        self.transport_id: str | None = None
        self.session_id: str | None = None
        self._connected_transports: set[str] = set()

    # ---- Verbindung ----
    def connect(self, timeout: float = 8.0) -> None:
        ctx = ssl.create_default_context()
        # Chromecasts nutzen selbst signierte Zertifikate (so machen es alle Cast-Clients).
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        self._sock = ctx.wrap_socket(raw)
        self._sock.settimeout(None)
        self._running = True
        threading.Thread(target=self._io_loop, name="cast-io", daemon=True).start()
        threading.Thread(target=self._heartbeat, name="cast-heartbeat", daemon=True).start()
        self._send(RECEIVER_ID, NS_CONNECTION, {"type": "CONNECT"})
        self.get_receiver_status()

    def close(self) -> None:
        if not self._running:
            return
        try:
            self._send(RECEIVER_ID, NS_CONNECTION, {"type": "CLOSE"})
            time.sleep(0.1)
        except (OSError, ConnectionError):
            pass
        self._running = False
        self._wake()

    @property
    def connected(self) -> bool:
        return self._running and self._sock is not None

    # ---- Senden/Empfangen ----
    def _send(self, dest: str, namespace: str, data: dict) -> None:
        payload = json.dumps(data)
        frame = encode_message(SENDER_ID, dest, namespace, payload)
        if not self._running or not self._sock:
            raise ConnectionError("Nicht verbunden")
        self._outbox.put(frame)
        self._wake()

    def _wake(self) -> None:
        try:
            self._wake_w.send(b"x")
        except OSError:
            pass

    def _request(self, dest: str, namespace: str, data: dict, timeout: float = 10.0) -> dict | None:
        with self._lock:
            self._request_id += 1
            rid = self._request_id
        data = {**data, "requestId": rid}
        ev = threading.Event()
        box: list = []
        self._waiters[rid] = (ev, box)
        try:
            self._send(dest, namespace, data)
            if not ev.wait(timeout):
                return None
            return box[0] if box else None
        finally:
            self._waiters.pop(rid, None)

    def _io_loop(self) -> None:
        sock = self._sock
        rbuf = b""
        try:
            while self._running:
                # Ausstehende Nachrichten senden (nur dieser Thread schreibt)
                if not self._outbox.empty():
                    sock.setblocking(True)  # type: ignore[union-attr]
                    while True:
                        try:
                            frame = self._outbox.get_nowait()
                        except queue.Empty:
                            break
                        sock.sendall(frame)  # type: ignore[union-attr]
                if sock.pending() == 0:  # type: ignore[union-attr]
                    readable, _, _ = select.select([sock, self._wake_r], [], [], 1.0)
                    if self._wake_r in readable:
                        try:
                            self._wake_r.recv(4096)
                        except OSError:
                            pass
                    if sock not in readable:
                        continue
                # Nicht blockierend lesen: "lesbar" kann auch nur TLS-Verwaltung sein
                sock.setblocking(False)  # type: ignore[union-attr]
                try:
                    chunk = sock.recv(65536)  # type: ignore[union-attr]
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                    continue
                if not chunk:
                    raise ConnectionError("Verbindung geschlossen")
                rbuf += chunk
                while len(rbuf) >= 4:
                    (length,) = struct.unpack(">I", rbuf[:4])
                    if len(rbuf) < 4 + length:
                        break
                    msg = decode_message(rbuf[4:4 + length])
                    rbuf = rbuf[4 + length:]
                    self._handle(msg)
        except (OSError, ConnectionError, ValueError) as e:
            if self._running:
                log.warning("Verbindung zu %s verloren: %s", self.name, e)
        finally:
            was_running = self._running
            self._running = False
            try:
                sock.close()  # type: ignore[union-attr]
            except OSError:
                pass
            self._sock = None
            if was_running and self.on_disconnect:
                self.on_disconnect()

    def _heartbeat(self) -> None:
        while self._running:
            time.sleep(5)
            try:
                self._send(RECEIVER_ID, NS_HEARTBEAT, {"type": "PING"})
            except (OSError, ConnectionError):
                return

    def _handle(self, msg: dict) -> None:
        ns = msg.get("namespace")
        try:
            data = json.loads(msg.get("payload") or "{}")
        except json.JSONDecodeError:
            return
        typ = data.get("type")

        if ns == NS_HEARTBEAT and typ == "PING":
            self._send(msg.get("source", RECEIVER_ID), NS_HEARTBEAT, {"type": "PONG"})
            return
        if ns == NS_CONNECTION and typ == "CLOSE":
            # Die App auf dem Chromecast wurde beendet (z. B. andere Quelle gestartet)
            self._connected_transports.discard(msg.get("source", ""))
            if msg.get("source") == self.transport_id:
                self.transport_id = None
                self.session_id = None
            return
        if ns == NS_RECEIVER and typ == "RECEIVER_STATUS":
            self.receiver_status = data.get("status", {})
            app = self._media_app()
            if app:
                self.transport_id = app.get("transportId")
                self.session_id = app.get("sessionId")
            else:
                self.transport_id = None
                self.session_id = None
        if ns == NS_MEDIA and typ == "MEDIA_STATUS":
            statuses = data.get("status") or []
            if statuses:
                st = statuses[0]
                # Teil-Updates mit dem bisherigen Stand zusammenführen
                merged = {**self.media_status, **st}
                if "media" not in st and "media" in self.media_status:
                    merged["media"] = self.media_status["media"]
                self.media_status = merged
            else:
                self.media_status = {}
            if self.on_media_status:
                try:
                    self.on_media_status(self.media_status)
                except Exception:  # noqa: BLE001
                    log.exception("Fehler im Media-Status-Callback")

        rid = data.get("requestId")
        if rid and rid in self._waiters:
            ev, box = self._waiters[rid]
            box.append(data)
            ev.set()

    # ---- Receiver ----
    def _media_app(self) -> dict | None:
        for app in self.receiver_status.get("applications", []) or []:
            if app.get("appId") == DEFAULT_MEDIA_RECEIVER:
                return app
        return None

    def get_receiver_status(self) -> dict | None:
        return self._request(RECEIVER_ID, NS_RECEIVER, {"type": "GET_STATUS"})

    def set_volume(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        self._request(RECEIVER_ID, NS_RECEIVER, {"type": "SET_VOLUME", "volume": {"level": level}})

    def set_muted(self, muted: bool) -> None:
        self._request(RECEIVER_ID, NS_RECEIVER, {"type": "SET_VOLUME", "volume": {"muted": muted}})

    @property
    def volume(self) -> dict:
        return self.receiver_status.get("volume", {}) or {}

    def launch_media_app(self, timeout: float = 20.0) -> str:
        if not self._media_app():
            self._request(RECEIVER_ID, NS_RECEIVER, {"type": "LAUNCH", "appId": DEFAULT_MEDIA_RECEIVER}, timeout=timeout)
            deadline = time.time() + timeout
            while not self._media_app() and time.time() < deadline:
                time.sleep(0.3)
                self.get_receiver_status()
        app = self._media_app()
        if not app:
            raise RuntimeError("Media-Player auf dem Chromecast startet nicht")
        tid = app["transportId"]
        if tid not in self._connected_transports:
            self._send(tid, NS_CONNECTION, {"type": "CONNECT"})
            self._connected_transports.add(tid)
        self.transport_id = tid
        self.session_id = app.get("sessionId")
        return tid

    def stop_app(self) -> None:
        if self.session_id:
            self._request(RECEIVER_ID, NS_RECEIVER, {"type": "STOP", "sessionId": self.session_id})
        self.media_status = {}

    # ---- Media ----
    def load(self, url: str, content_type: str, title: str = "", live: bool = False,
             hls_segment_format: str | None = None) -> dict | None:
        tid = self.launch_media_app()
        media = {
            "contentId": url,
            "contentUrl": url,
            "streamType": "LIVE" if live else "BUFFERED",
            "contentType": content_type,
            "metadata": {"metadataType": 0, "title": title or "LakuenCast"},
        }
        if hls_segment_format:
            media["hlsSegmentFormat"] = hls_segment_format
        self.media_status = {}
        return self._request(tid, NS_MEDIA, {
            "type": "LOAD", "sessionId": self.session_id, "media": media,
            "autoplay": True, "currentTime": 0,
        }, timeout=30)

    def _media_cmd(self, typ: str, **extra) -> dict | None:
        msid = self.media_status.get("mediaSessionId")
        if not self.transport_id or msid is None:
            return None
        return self._request(self.transport_id, NS_MEDIA, {"type": typ, "mediaSessionId": msid, **extra})

    def play(self):
        return self._media_cmd("PLAY")

    def pause(self):
        return self._media_cmd("PAUSE")

    def stop_media(self):
        return self._media_cmd("STOP")

    def seek(self, seconds: float):
        return self._media_cmd("SEEK", currentTime=seconds)

    def update_media_status(self):
        if self.transport_id:
            return self._request(self.transport_id, NS_MEDIA, {"type": "GET_STATUS"})
        return None
