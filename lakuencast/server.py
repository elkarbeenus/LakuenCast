"""HTTP-Server von LakuenCast.

  /                 Oberfläche (nur vom eigenen PC)
  /api/...          Steuerung (nur vom eigenen PC, POST braucht Header X-LakuenCast)
  /live/...         Bildschirm-Livestream (für den Chromecast im Heimnetz)
  /p/<token>/...    Video-Durchreiche (für den Chromecast, nur mit gültigem Token)
"""

from __future__ import annotations

import json
import os
import logging
import mimetypes
import threading
import time
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
            q = urllib.parse.parse_qs(parts.query)
            code, token = q.get("k", [""])[0], q.get("s", [""])[0]
            if code:
                # QR-Code gescannt → eigene Verbindung, QR-Code wird ausgetauscht
                new = self.app.pair(code)
                if not new:
                    return self._expired_page("Dieser QR-Code ist schon benutzt. Tippe den Code vom PC ein.")
                self.send_response(302)
                self.send_header("Location", f"/remote?s={new}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if not self._remote_ok(token):
                return self._expired_page("Die Verbindung ist abgelaufen – einfach neu verbinden." if token
                                          else "Tippe den 6-stelligen Code vom PC ein.")
            return self._static("remote.html", head)
        if path.startswith("/r/") and path.endswith("/ws"):
            return self._ws_input(path)
        if path.startswith("/r/") and path.endswith("/preview.mjpg"):
            return self._preview(path, parts.query)
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
        if path0 == "/pair":
            token, err = self.app.pair_pin(str(self._body().get("pin", "")))
            if token:
                return self._json({"ok": True, "s": token, "expiresAt": self.app.session_expires(token)})
            return self._json({"ok": False, "error": err})
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
            elif path == "/api/remote/disconnect":
                app.disconnect_phones()
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
    def _remote_ok(self, token: str) -> bool:
        """Gültige Handy-Verbindung (nicht der QR-Code selbst)."""
        return self.app.session_ok(token)

    def _expired_page(self, reason: str):
        """Verbinden-Seite: 6-stelligen Code eintippen (auch in der Home-Bildschirm-App)."""
        import html as _html
        page = (WEB / "connect.html").read_text(encoding="utf-8").replace("{{REASON}}", _html.escape(reason)).encode()
        self.send_response(403)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)

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
                               "input": bool(app.config["input_enabled"]),
                               "expiresAt": app.session_expires(parts[2]),
                               "volume": {"level": vol.get("level"), "muted": vol.get("muted")},
                               "device": (st.get("selected") or {}).get("name"),
                               "connected": st["connected"], "error": st["error"]})
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": str(e)})

    # ------------------------------------------------------------ Maus & Tastatur (WebSocket)
    def _input_allowed(self, path: str) -> bool:
        parts = path.split("/")  # ['', 'r', key, 'ws']
        return len(parts) == 4 and self._remote_ok(parts[2]) and bool(self.app.config["input_enabled"])

    def _ws_input(self, path: str):
        from . import ws
        if not self._input_allowed(path):
            return self.send_error(403)
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or "websocket" not in (self.headers.get("Upgrade") or "").lower():
            return self.send_error(400)
        self.protocol_version = "HTTP/1.1"
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws.accept_key(key))
        self.end_headers()
        self.close_connection = True

        def send(obj):
            self.wfile.write(ws.encode_frame(json.dumps(obj).encode()))
            self.wfile.flush()

        try:
            xi = self.app.get_xinput()
        except Exception as e:  # noqa: BLE001
            send({"error": str(e)})
            return
        w, h = xi.size
        send({"ok": True, "screen": [w, h]})
        try:
            while True:
                frame = ws.read_frame(self.rfile)
                if frame is None:
                    break
                op, data = frame
                if op == 0x8:  # schließen
                    break
                if op == 0x9:  # ping
                    self.wfile.write(ws.encode_frame(data, 0xA))
                    continue
                if op != 0x1:
                    continue
                if not self.app.config["input_enabled"]:
                    send({"error": "Maus & Tastatur wurden in LakuenCast ausgeschaltet."})
                    break
                if not self.app.session_ok(path.split("/")[2]):
                    send({"error": "expired"})
                    break
                try:
                    self._handle_input(xi, json.loads(data))
                except (ValueError, KeyError, TypeError) as e:
                    send({"error": str(e)})
        except (OSError, ConnectionError):
            pass
        finally:
            xi.release_all()

    @staticmethod
    def _handle_input(xi, m: dict):
        t = m.get("t")
        if t == "m":
            xi.move(float(m.get("x", 0)), float(m.get("y", 0)))
        elif t == "a":
            xi.move_to(float(m["x"]), float(m["y"]))
        elif t == "c":
            xi.click(str(m.get("b", "left")), bool(m.get("dbl")))
        elif t == "d":
            xi.button(str(m.get("b", "left")), True)
        elif t == "u":
            xi.button(str(m.get("b", "left")), False)
        elif t == "s":
            xi.scroll(int(m.get("y", 0)), int(m.get("x", 0)))
        elif t == "k":
            xi.key(str(m["k"]))
        elif t == "txt":
            xi.type_text(str(m.get("v", "")))
        elif t == "ping":
            pass
        else:
            raise ValueError("Unbekannte Eingabe")

    # ------------------------------------------------------------ Live-Vorschau (MJPEG)
    def _preview(self, path: str, query: str):
        import subprocess
        from .mirror import find_ffmpeg
        parts = path.split("/")
        if len(parts) != 4 or not self._remote_ok(parts[2]) or not self.app.config["input_enabled"]:
            return self.send_error(403)
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return self.send_error(500, "ffmpeg fehlt")
        q = urllib.parse.parse_qs(query)
        fps = max(2, min(15, int((q.get("fps") or ["10"])[0])))
        width = max(320, min(1280, int((q.get("w") or ["720"])[0])))
        old = self.app.preview_proc
        if old and old.poll() is None:
            old.terminate()
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "x11grab", "-framerate", str(fps), "-draw_mouse", "1", "-i", os.environ.get("DISPLAY", ":0"),
               "-vf", f"scale={width}:-2:flags=fast_bilinear", "-q:v", "7",
               "-f", "mpjpeg", "-boundary_tag", "lkframe", "pipe:1"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.app.preview_proc = proc
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=lkframe")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            token = parts[2]
            last_check = time.time()
            while True:
                chunk = proc.stdout.read1(1 << 16)
                if not chunk:
                    break
                self.wfile.write(chunk)
                if time.time() - last_check > 5:
                    last_check = time.time()
                    if not self.app.session_ok(token) or not self.app.config["input_enabled"]:
                        break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if proc.poll() is None:
                proc.terminate()

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
