"""Ein gespielter Chromecast für Tests: spricht das Cast-Protokoll über TLS
und lädt die Video-URL, die er per LOAD bekommt, wie ein echter Chromecast."""

from __future__ import annotations

import json
import queue
import select
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from lakuencast.castv2 import (DEFAULT_MEDIA_RECEIVER, NS_HEARTBEAT, NS_MEDIA, NS_RECEIVER,
                               decode_message, encode_message)


def make_cert(tmp: Path) -> tuple[str, str]:
    key, crt = tmp / "k.pem", tmp / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(crt), "-days", "1", "-subj", "/CN=fakecast"], check=True, capture_output=True)
    return str(crt), str(key)


class FakeChromecast:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        crt, key = make_cert(self.tmp)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(crt, key)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.app_running = False
        self.loaded: list[dict] = []
        self.fetched: list[tuple[str, int, bytes]] = []   # (url, status, erste Bytes)
        self.volume = 0.5
        self.conn = None
        self.media_session = 0
        self.lock = threading.Lock()
        self.errors: list[str] = []
        self.outboxes: dict = {}
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            raw, _ = self.sock.accept()
            try:
                self.conn = self.ctx.wrap_socket(raw, server_side=True)
            except (ssl.SSLError, OSError) as e:
                self.errors.append(f"handshake: {e!r}")
                continue
            threading.Thread(target=self._serve, args=(self.conn,), daemon=True).start()

    def _send(self, conn, src, dst, ns, data):
        # Nur der Serve-Thread schreibt (OpenSSL ist nicht thread-sicher)
        self.outboxes.setdefault(id(conn), queue.Queue()).put(encode_message(src, dst, ns, json.dumps(data)))

    def _status(self):
        apps = [{"appId": DEFAULT_MEDIA_RECEIVER, "transportId": "web-1", "sessionId": "sess-1"}] if self.app_running else []
        return {"applications": apps, "volume": {"level": self.volume, "muted": False}}

    def _serve(self, conn):
        def recv(n):
            b = b""
            while len(b) < n:
                c = conn.recv(n - len(b))
                if not c:
                    raise ConnectionError
                b += c
            return b
        try:
            while True:
                box = self.outboxes.setdefault(id(conn), queue.Queue())
                while not box.empty():
                    conn.sendall(box.get())
                if conn.pending() == 0:
                    r, _, _ = select.select([conn], [], [], 0.05)
                    if not r:
                        continue
                (ln,) = struct.unpack(">I", recv(4))
                m = decode_message(recv(ln))
                d = json.loads(m["payload"])
                ns, typ, rid = m["namespace"], d.get("type"), d.get("requestId")
                src = m["source"]
                if ns == NS_HEARTBEAT and typ == "PING":
                    self._send(conn, "receiver-0", src, NS_HEARTBEAT, {"type": "PONG"})
                elif ns == NS_RECEIVER:
                    if typ == "LAUNCH":
                        self.app_running = True
                    elif typ == "STOP":
                        self.app_running = False
                    elif typ == "SET_VOLUME":
                        self.volume = d["volume"].get("level", self.volume)
                    self._send(conn, "receiver-0", src, NS_RECEIVER,
                               {"type": "RECEIVER_STATUS", "requestId": rid, "status": self._status()})
                elif ns == NS_MEDIA and typ == "LOAD":
                    self.loaded.append(d["media"])
                    self.media_session += 1
                    threading.Thread(target=self._play, args=(d["media"],), daemon=True).start()
                    self._send(conn, "web-1", src, NS_MEDIA, {"type": "MEDIA_STATUS", "requestId": rid, "status": [
                        {"mediaSessionId": self.media_session, "playerState": "BUFFERING", "media": {**d["media"], "duration": 1200}}]})
                elif ns == NS_MEDIA:
                    self._send(conn, "web-1", src, NS_MEDIA, {"type": "MEDIA_STATUS", "requestId": rid, "status": [
                        {"mediaSessionId": self.media_session, "playerState": "PAUSED" if typ == "PAUSE" else "PLAYING"}]})
        except (ConnectionError, OSError, ssl.SSLError) as e:
            self.errors.append(f"serve: {e!r}")

    def _get(self, url):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                body = r.read()
                self.fetched.append((url, r.status, body[:200]))
                return body
        except urllib.error.HTTPError as e:
            self.fetched.append((url, e.code, b""))
            return b""

    def _play(self, media):
        """Wie der echte Chromecast: Playlist und erstes Stück laden."""
        url = media["contentId"]
        body = self._get(url)
        if body.startswith(b"#EXTM3U"):
            lines = [ln for ln in body.decode().splitlines() if ln and not ln.startswith("#")]
            if lines:
                sub_url = urllib.parse.urljoin(url, lines[0])
                if lines[0].endswith(".ts"):
                    self._get(sub_url)
                    return
                sub = self._get(sub_url)
                if sub.startswith(b"#EXTM3U"):
                    seg = [ln for ln in sub.decode().splitlines() if ln and not ln.startswith("#")]
                    if seg:
                        self._get(urllib.parse.urljoin(sub_url, seg[0]))

    def finish(self):
        """Folge zu Ende melden."""
        self._send(self.conn, "web-1", "*", NS_MEDIA, {"type": "MEDIA_STATUS", "requestId": 0, "status": [
            {"mediaSessionId": self.media_session, "playerState": "IDLE", "idleReason": "FINISHED"}]})
