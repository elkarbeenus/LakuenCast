"""LakuenCast – Zustand und Steuerung (Geräte, Spiegeln, Casten)."""

from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.parse

from . import desktop, mdns, proxy as proxymod
from .castv2 import CastDevice
from .config import Config
from .filecast import FileCast
from .mirror import Mirror, session_problem
from .updater import Updater

log = logging.getLogger("lakuencast")


def lan_ip_for(host: str) -> str:
    """Die eigene IP, unter der der Chromecast uns erreicht."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


class App:
    def __init__(self, port: int, config: Config | None = None):
        self.port = port
        self.config = config or Config()
        self.lock = threading.RLock()
        self.devices: dict[str, mdns.Device] = {}
        self.scanning = False
        self.selected_id: str | None = None
        self.cast: CastDevice | None = None
        self.mode = "idle"            # idle | starting | mirroring | casting
        self.title = ""
        self.message: str | None = None
        self.error: str | None = None
        self.mirror: Mirror | None = None
        self.quality = self.config["quality"] or "720p"
        self.file: FileCast | None = None
        self.next_seq = 0             # "Nächste Folge" auf der Handy-Fernbedienung
        self.proxy = proxymod.Proxy()
        self.finished_seq = 0         # zählt hoch, wenn eine gecastete Folge zu Ende ist
        self._finished_msid = None
        self.updater = Updater()
        self.show_requested = False   # zweiter Start → Fenster nach vorne holen

    # ---------------------------------------------------------------- Geräte

    def scan(self) -> None:
        with self.lock:
            if self.scanning:
                return
            self.scanning = True
        try:
            found = mdns.discover(timeout=3.0)
            with self.lock:
                for d in found:
                    self.devices[d.id] = d
                last = self.config["last_device"]
                if not self.selected_id and last in self.devices:
                    self.selected_id = last
                elif not self.selected_id and len(self.devices) == 1:
                    self.selected_id = next(iter(self.devices))
        except OSError as e:
            self.error = f"Suche im Netzwerk fehlgeschlagen: {e}"
        finally:
            self.scanning = False

    def scan_async(self) -> None:
        threading.Thread(target=self.scan, daemon=True).start()

    def add_manual(self, host: str) -> mdns.Device:
        port = 8009
        if host.count(":") == 1:
            host, p = host.split(":")
            port = int(p)
        if not host:
            raise ValueError("Bitte eine IP-Adresse eingeben")
        dev = mdns.Device(id=f"manual-{host}:{port}", name=host, host=host, port=port)
        with self.lock:
            self.devices[dev.id] = dev
        return dev

    def select(self, dev_id: str) -> None:
        with self.lock:
            if dev_id not in self.devices:
                raise ValueError("Unbekanntes Gerät")
            if self.selected_id != dev_id:
                self.stop()
                self._disconnect()
            self.selected_id = dev_id
            self.config.set(last_device=dev_id)

    def _device(self) -> mdns.Device:
        if not self.selected_id or self.selected_id not in self.devices:
            if len(self.devices) == 1:
                self.selected_id = next(iter(self.devices))
            else:
                raise RuntimeError("Bitte zuerst einen Chromecast auswählen.")
        return self.devices[self.selected_id]

    def _connect(self) -> CastDevice:
        dev = self._device()
        if self.cast and self.cast.connected and self.cast.host == dev.host:
            return self.cast
        self._disconnect()
        c = CastDevice(dev.host, dev.port, dev.name,
                       on_media_status=self._on_media_status, on_disconnect=self._on_disconnect)
        c.connect()
        self.cast = c
        return c

    def _disconnect(self) -> None:
        if self.cast:
            self.cast.close()
        self.cast = None

    def _on_disconnect(self) -> None:
        with self.lock:
            self.cast = None
            if self.mode in ("mirroring", "casting"):
                self.message = "Verbindung zum Chromecast verloren."
                self._stop_mirror()
                self.mode = "idle"

    def _on_media_status(self, st: dict) -> None:
        if self.mode == "casting" and st.get("playerState") == "IDLE" and st.get("idleReason") == "FINISHED":
            msid = st.get("mediaSessionId")
            if msid != self._finished_msid:
                self._finished_msid = msid
                self.finished_seq += 1
                log.info("Folge auf dem Fernseher zu Ende (%s)", self.finished_seq)
        if st.get("playerState") == "IDLE" and st.get("idleReason") == "ERROR":
            self.error = "Der Chromecast konnte das Video nicht abspielen."

    def base_url(self, host: str) -> str:
        return f"http://{lan_ip_for(host)}:{self.port}"

    # ---------------------------------------------------------------- Spiegeln

    def start_mirror(self, quality: str | None = None) -> None:
        with self.lock:
            problem = session_problem()
            if problem and "DISPLAY" in problem:
                raise RuntimeError(problem)
            self.stop()
            self.mode = "starting"
            self.error = None
            self.message = problem
            if quality:
                self.quality = quality
        try:
            cast = self._connect()
            self.config.set(quality=self.quality)
            m = Mirror(self.quality, low_latency=bool(self.config["low_latency"]), mute_pc=bool(self.config["mute_pc"]))
            m.start()
            self.mirror = m
            if not m.wait_ready():
                raise RuntimeError(m.error or "Livestream startet nicht")
            url = f"{self.base_url(cast.host)}/live/{m.stream_name}"
            ctype = "video/mp4" if m.low_latency else "application/x-mpegurl"
            cast.load(url, ctype, title="PC-Bildschirm (LakuenCast)", live=True)
            with self.lock:
                self.mode = "mirroring"
                self.title = "Bildschirm wird gespiegelt"
                if not m.audio_source:
                    self.message = "Kein Ton gefunden – es wird nur das Bild übertragen."
        except Exception as e:
            with self.lock:
                self._stop_mirror()
                self.mode = "idle"
                self.error = str(e)
            raise

    def _stop_mirror(self) -> None:
        if self.mirror:
            self.mirror.stop()
        self.mirror = None
        if self.file:
            self.file.stop()
        self.file = None

    # ---------------------------------------------------------------- Datei senden

    def cast_file(self, path: str) -> None:
        with self.lock:
            self.stop()
            self.mode = "starting"
            self.error = None
            self.message = None
        try:
            fc = FileCast(path)
            cast = self._connect()
            fc.start()
            self.file = fc
            if fc.mode != "direct":
                self.message = ("Wird umgewandelt – Spulen geht nur im schon umgewandelten Teil."
                                if fc.mode == "transcode" else None)
            if not fc.wait_ready():
                raise RuntimeError(fc.error or "Datei startet nicht")
            url = f"{self.base_url(cast.host)}/file/{fc.name}"
            ctype = "video/mp4" if fc.mode == "direct" else "application/x-mpegurl"
            cast.load(url, ctype, title=fc.title, live=False)
            with self.lock:
                self.mode = "casting"
                self.title = fc.title
        except Exception as e:
            with self.lock:
                self._stop_mirror()
                self.mode = "idle"
                self.error = str(e)
            raise

    # ---------------------------------------------------------------- Fernbedienung

    def lan_ip(self) -> str:
        if self.cast and self.cast.connected:
            try:
                return lan_ip_for(self.cast.host)
            except OSError:
                pass
        ips = mdns.local_ipv4s()
        return ips[0] if ips else "127.0.0.1"

    def remote_url(self) -> str:
        return f"http://{self.lan_ip()}:{self.port}/remote?k={self.config['remote_key']}"

    def request_next(self) -> None:
        self.next_seq += 1

    def set_option(self, **kw) -> None:
        allowed = {k: bool(v) for k, v in kw.items() if k in ("mute_pc", "low_latency")}
        self.config.set(**allowed)

    # ---------------------------------------------------------------- Video casten

    def cast_url(self, url: str, headers: dict | None = None, title: str = "") -> dict:
        if not url.startswith(("http://", "https://")):
            raise ValueError("Ungültige Video-Adresse")
        with self.lock:
            self.stop()
            self.mode = "starting"
            self.error = None
            self.message = None
        try:
            cast = self._connect()
            sess = self.proxy.new_session(headers)
            base = self.base_url(cast.host)

            # Vorab prüfen, was es ist (und ob der Hoster uns das Video gibt)
            resp = self.proxy.open(sess, url, range_header="bytes=0-")
            status = getattr(resp, "status", None) or resp.getcode()
            if status >= 400:
                raise RuntimeError(f"Der Hoster gibt das Video nicht heraus (HTTP {status}).")
            ctype = resp.headers.get("Content-Type", "")
            head = resp.read(512)
            playlist_text = None
            if proxymod.is_playlist(url, ctype, head):
                playlist_text = (head + resp.read()).decode("utf-8", "replace")
            resp.close()

            seg_fmt = None
            if playlist_text is not None:
                content_type = "application/x-mpegurl"
                name = "index.m3u8"
                seg_fmt = proxymod.segment_format(playlist_text)
                if seg_fmt is None and "#EXT-X-STREAM-INF" in playlist_text:
                    # Master-Playlist: erste Variante anschauen
                    for line in playlist_text.splitlines():
                        if line.strip() and not line.startswith("#"):
                            sub = urllib.parse.urljoin(url, line.strip())
                            try:
                                r2 = self.proxy.open(sess, sub)
                                seg_fmt = proxymod.segment_format(r2.read(65536).decode("utf-8", "replace"))
                                r2.close()
                            except Exception:  # noqa: BLE001
                                pass
                            break
            else:
                content_type = ctype.split(";")[0].strip() if ctype.startswith(("video/", "audio/")) else "video/mp4"
                name = "video.mp4"

            proxied = proxymod.Proxy.make_url(base, sess.token, url, name)
            cast.load(proxied, content_type, title=title or "LakuenCast", live=False, hls_segment_format=seg_fmt)
            with self.lock:
                self.mode = "casting"
                self.title = title or "Video"
            return {"ok": True}
        except Exception as e:
            with self.lock:
                self.mode = "idle"
                self.error = str(e)
            raise

    # ---------------------------------------------------------------- Steuerung

    def stop(self) -> None:
        with self.lock:
            was = self.mode
            self._stop_mirror()
            if self.cast and self.cast.connected and was in ("mirroring", "casting", "starting"):
                try:
                    self.cast.stop_app()
                except Exception:  # noqa: BLE001
                    pass
            self.mode = "idle"
            self.title = ""

    def pause(self):
        if self.cast:
            self.cast.pause()

    def play(self):
        if self.cast:
            self.cast.play()

    def seek_relative(self, seconds: float):
        if self.cast:
            cur = float(self.cast.media_status.get("currentTime") or 0)
            self.cast.seek(max(0.0, cur + seconds))

    def set_volume(self, level: float):
        cast = self._connect()
        cast.set_volume(level)

    # ---------------------------------------------------------------- Status

    def status(self) -> dict:
        with self.lock:
            media = {}
            volume = {}
            if self.cast and self.cast.connected:
                st = self.cast.media_status or {}
                media = {
                    "playerState": st.get("playerState"),
                    "idleReason": st.get("idleReason"),
                    "currentTime": st.get("currentTime"),
                    "duration": (st.get("media") or {}).get("duration"),
                }
                volume = self.cast.volume
            sel = self.devices.get(self.selected_id) if self.selected_id else None
            return {
                "app": "LakuenCast",
                "mode": self.mode,
                "title": self.title,
                "message": self.message,
                "error": self.error,
                "scanning": self.scanning,
                "devices": [d.to_dict() for d in self.devices.values()],
                "selected": sel.to_dict() if sel else None,
                "connected": bool(self.cast and self.cast.connected),
                "media": media,
                "volume": volume,
                "quality": self.quality,
                "finishedSeq": self.finished_seq,
                "nextSeq": self.next_seq,
                "options": {"mute_pc": bool(self.config["mute_pc"]), "low_latency": bool(self.config["low_latency"])},
                "pcMuted": bool(self.mirror and self.mirror.pc_muted),
                "file": {"title": self.file.title, "mode": self.file.mode} if self.file else None,
                "autostart": desktop.autostart_enabled(),
                "update": self.updater.to_dict(),
                "sessionHint": session_problem(),
                "time": time.time(),
            }
