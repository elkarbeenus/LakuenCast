"""HTTP-Server von LakuenCast.

  /                 Oberfläche (nur vom eigenen PC)
  /api/...          Steuerung (nur vom eigenen PC, POST braucht Header X-LakuenCast)
  /live/...         Bildschirm-Livestream (für den Chromecast im Heimnetz)
  /p/<token>/...    Video-Durchreiche (für den Chromecast, nur mit gültigem Token)
"""

from __future__ import annotations

import json
import logging
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import desktop, proxy as proxymod
from .app import App

log = logging.getLogger("lakuencast.http")
WEB = Path(__file__).parent / "web"


class Handler(BaseHTTPRequestHandler):
    server_version = "LakuenCast"
    app: App  # wird gesetzt

    def log_message(self, fmt, *args):  # leiser
        log.debug("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------ Hilfen
    @property
    def local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin.startswith(("chrome-extension://", "moz-extension://")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-LakuenCast")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, data, code: int = 200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _media_headers(self):
        # Der Chromecast lädt HLS per XHR → CORS nötig
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------ Methoden
    def do_OPTIONS(self):
        self.send_response(204)
        if self.path.startswith(("/live/", "/p/")):
            self._media_headers()
        else:
            self._cors()
        self.end_headers()

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head: bool = False):
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        if path.startswith("/live/"):
            return self._live(path, head)
        if path.startswith("/file/"):
            return self._file(path, head)
        if path.startswith("/p/"):
            return self._proxy(path, head)
        if path == "/remote":
            key = urllib.parse.parse_qs(parts.query).get("k", [""])[0]
            if not self._remote_ok(key):
                return self.send_error(403, "Falscher Schlüssel")
            return self._static("remote.html", head)
        if path.startswith("/r/"):
            return self._remote_api(path, None)
        if path == "/icon.png":
            return self._static("icon.png", head)
        if not self.local:
            return self.send_error(403)
        if path == "/api/status":
            return self._json(self.app.status())
        if path in ("/", "/index.html"):
            path = "/index.html"
        return self._static(path.lstrip("/"), head)

    def _static(self, name: str, head: bool):
        f = (WEB / name).resolve()
        if WEB.resolve() not in f.parents or not f.is_file():
            return self.send_error(404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def do_POST(self):
        path0 = urllib.parse.urlsplit(self.path).path
        if path0.startswith("/r/"):
            return self._remote_api(path0, self._body())
        if not self.local:
            return self.send_error(403)
        if self.headers.get("X-LakuenCast") != "1":
            return self._json({"ok": False, "error": "Header X-LakuenCast fehlt"}, 400)
        path = urllib.parse.urlsplit(self.path).path
        data = self._body()
        app = self.app
        try:
            if path == "/api/scan":
                app.scan_async()
            elif path == "/api/select":
                app.select(str(data.get("id", "")))
            elif path == "/api/add":
                dev = app.add_manual(str(data.get("host", "")).strip())
                app.select(dev.id)
            elif path == "/api/mirror":
                app.start_mirror(data.get("quality"))
            elif path == "/api/cast":
                app.cast_url(str(data.get("url", "")), data.get("headers") or {}, str(data.get("title", "")),
                             float(data.get("startAt") or 0))
            elif path == "/api/stop":
                app.stop()
            elif path == "/api/pause":
                app.pause()
            elif path == "/api/play":
                app.play()
            elif path == "/api/seek":
                app.seek_relative(float(data.get("delta", 0)))
            elif path == "/api/seekto":
                app.seek_to(float(data.get("time", 0)))
            elif path == "/api/volume":
                app.set_volume(float(data.get("level", 0.5)))
            elif path == "/api/autostart":
                desktop.set_autostart(bool(data.get("on")))
            elif path == "/api/update/check":
                app.updater.check()
            elif path == "/api/update/install":
                app.updater.install_async(on_restart=app.stop)
            elif path == "/api/show":
                app.show_requested = True
            elif path == "/api/castfile":
                app.cast_file(str(data.get("path", "")))
            elif path == "/api/option":
                app.set_option(**data)
            elif path == "/api/remote/newkey":
                app.config.new_remote_key()
            elif path == "/api/next":
                app.request_next()
            elif path == "/api/dismiss":
                app.error = None
                app.message = None
            else:
                return self._json({"ok": False, "error": "Unbekannt"}, 404)
            return self._json({"ok": True, "status": app.status()})
        except Exception as e:  # noqa: BLE001
            log.warning("Fehler bei %s: %s", path, e)
            return self._json({"ok": False, "error": str(e), "status": app.status()}, 200)

    # ------------------------------------------------------------ Fernbedienung (Handy)
    def _remote_ok(self, key: str) -> bool:
        import hmac
        return bool(key) and hmac.compare_digest(key, str(self.app.config["remote_key"]))

    def _remote_api(self, path: str, data: dict | None):
        parts = path.split("/")  # ['', 'r', key, cmd]
        if len(parts) != 4 or not self._remote_ok(parts[2]):
            return self.send_error(403)
        app = self.app
        cmd = parts[3]
        try:
            if data is not None:
                if cmd == "pause":
                    app.pause()
                elif cmd == "play":
                    app.play()
                elif cmd == "seek":
                    app.seek_relative(float(data.get("delta", 0)))
                elif cmd == "seekto":
                    app.seek_to(float(data.get("time", 0)))
                elif cmd == "volume":
                    app.set_volume(float(data.get("level", 0.5)))
                elif cmd == "mute":
                    if app.cast:
                        app.cast.set_muted(bool(data.get("on", True)))
                elif cmd == "stop":
                    app.stop()
                elif cmd == "next":
                    app.request_next()
                elif cmd == "power":
                    app.power(str(data.get("action", "")))
                elif cmd == "mirror":
                    import threading as _t
                    _t.Thread(target=app.start_mirror, daemon=True).start()
                elif cmd != "status":
                    return self._json({"ok": False, "error": "Unbekannt"}, 404)
            st = app.status()
            vol = st.get("volume") or {}
            return self._json({"ok": True, "mode": st["mode"], "title": st["title"], "media": st["media"],
                               "volume": {"level": vol.get("level"), "muted": vol.get("muted")},
                               "device": (st.get("selected") or {}).get("name"),
                               "connected": st["connected"], "error": st["error"]})
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": str(e)})

    # ------------------------------------------------------------ Eigene Datei
    def _file(self, path: str, head: bool):
        fc = self.app.file
        name = path[len("/file/"):]
        if not fc or not name or "/" in name or ".." in name:
            return self.send_error(404)
        if fc.mode == "direct":
            return self._send_file_range(fc.path, "video/mp4", head)
        f = fc.dir / name if fc.dir else None
        if not f or not f.is_file():
            return self.send_error(404)
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._media_headers()
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _send_file_range(self, fpath: str, ctype: str, head: bool):
        import os as _os
        import re as _re
        size = _os.path.getsize(fpath)
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = _re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self._media_headers()
        self.end_headers()
        if head:
            return
        try:
            with open(fpath, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------ Livestream
    def _live(self, path: str, head: bool):
        m = self.app.mirror
        name = path[len("/live/"):]
        if not m or "/" in name or not name:
            return self.send_error(404)
        if name == "stream.mp4" and m.low_latency:
            return self._live_fmp4(m, head)
        f = m.dir / name
        if not f.is_file():
            return self.send_error(404)
        data = f.read_bytes()
        ctype = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._media_headers()
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _live_fmp4(self, m, head: bool):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._media_headers()
        self.end_headers()
        if head:
            return
        proc = m.open_stream()
        try:
            while True:
                chunk = proc.stdout.read1(1 << 16) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------ Durchreiche
    def _proxy(self, path: str, head: bool):
        parts = path.split("/", 4)  # ['', 'p', token, b64, name]
        if len(parts) < 5:
            return self.send_error(404)
        sess = self.app.proxy.sessions.get(parts[2])
        if not sess:
            return self.send_error(403)
        try:
            target = proxymod.unb64(parts[3])
        except Exception:  # noqa: BLE001
            return self.send_error(400)
        if not target.startswith(("http://", "https://")):
            return self.send_error(400)
        name = parts[4]

        resp = self.app.proxy.open(sess, target, self.headers.get("Range"))
        status = getattr(resp, "status", None) or resp.getcode()
        ctype = resp.headers.get("Content-Type", "")
        try:
            if status < 400 and (name.endswith(".m3u8") or proxymod.is_playlist(target, ctype, b"")):
                text = resp.read().decode("utf-8", "replace")
                final = resp.geturl() if hasattr(resp, "geturl") else target
                base = f"http://{self.headers.get('Host')}"
                body = proxymod.rewrite_playlist(
                    text, final, lambda u, n: proxymod.Proxy.make_url(base, sess.token, u, n)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self._media_headers()
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                return

            self.send_response(status)
            if name.endswith(".ts"):
                ctype = "video/mp2t"
            elif name.endswith(".mp4") and not ctype.startswith(("video/", "audio/")):
                ctype = "video/mp4"
            if ctype:
                self.send_header("Content-Type", ctype)
            for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if resp.headers.get(h):
                    self.send_header(h, resp.headers[h])
            self._media_headers()
            self.end_headers()
            if head:
                return
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Chromecast hat abgebrochen (z. B. beim Spulen)
        finally:
            resp.close()


def make_server(app: App, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
    srv.daemon_threads = True
    return srv


def serve_in_thread(srv: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=srv.serve_forever, name="http", daemon=True)
    t.start()
    return t
