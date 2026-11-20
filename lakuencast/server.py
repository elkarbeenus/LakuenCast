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
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/live/"):
            return self._live(path, head)
        if path.startswith("/p/"):
            return self._proxy(path, head)
        if not self.local:
            return self.send_error(403)
        if path == "/api/status":
            return self._json(self.app.status())
        if path in ("/", "/index.html"):
            path = "/index.html"
        f = (WEB / path.lstrip("/")).resolve()
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
                app.cast_url(str(data.get("url", "")), data.get("headers") or {}, str(data.get("title", "")))
            elif path == "/api/stop":
                app.stop()
            elif path == "/api/pause":
                app.pause()
            elif path == "/api/play":
                app.play()
            elif path == "/api/seek":
                app.seek_relative(float(data.get("delta", 0)))
            elif path == "/api/volume":
                app.set_volume(float(data.get("level", 0.5)))
            elif path == "/api/autostart":
                desktop.set_autostart(bool(data.get("on")))
            elif path == "/api/update/check":
                app.updater.check()
            elif path == "/api/update/install":
                app.updater.install_async(on_restart=app.stop)
            elif path == "/api/dismiss":
                app.error = None
                app.message = None
            else:
                return self._json({"ok": False, "error": "Unbekannt"}, 404)
            return self._json({"ok": True, "status": app.status()})
        except Exception as e:  # noqa: BLE001
            log.warning("Fehler bei %s: %s", path, e)
            return self._json({"ok": False, "error": str(e), "status": app.status()}, 200)

    # ------------------------------------------------------------ Livestream
    def _live(self, path: str, head: bool):
        m = self.app.mirror
        name = path[len("/live/"):]
        if not m or "/" in name or not name:
            return self.send_error(404)
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
