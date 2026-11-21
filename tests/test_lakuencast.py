"""Tests:  python3 -m unittest discover -s tests -v"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import tempfile as _tf
os.environ["XDG_CONFIG_HOME"] = _tf.mkdtemp(prefix="lakuencast-test-config-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lakuencast import castv2, mdns, proxy  # noqa: E402
from lakuencast.version import display, is_newer  # noqa: E402


class TestVersion(unittest.TestCase):
    def test_update_decision(self):
        self.assertTrue(is_newer("v0.2.0", "0.1.0"))
        self.assertFalse(is_newer("v0.1.0", "0.1.0"))
        self.assertFalse(is_newer("v0.1.0", "0.2.0"))
        self.assertTrue(is_newer("v0.2.0-test.1", "0.1.0"))      # Testbuild über Release
        self.assertTrue(is_newer("v0.2.0", "0.2.0-test.4"))
        self.assertFalse(is_newer("v0.2.0-test.9", "0.2.0"))
        self.assertEqual(display("0.2.0-test.3"), "v0.2.0 test 3")


class TestProtobuf(unittest.TestCase):
    def test_roundtrip(self):
        frame = castv2.encode_message("a", "b", castv2.NS_MEDIA, json.dumps({"type": "LOAD", "ü": "x" * 300}))
        (ln,) = struct.unpack(">I", frame[:4])
        self.assertEqual(ln, len(frame) - 4)
        msg = castv2.decode_message(frame[4:])
        self.assertEqual(msg["source"], "a")
        self.assertEqual(msg["dest"], "b")
        self.assertEqual(msg["namespace"], castv2.NS_MEDIA)
        self.assertEqual(json.loads(msg["payload"])["ü"], "x" * 300)


def _name(n: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in n.split(".")) + b"\x00"


def _rr(name: bytes, rtype: int, rdata: bytes) -> bytes:
    return name + struct.pack(">HHIH", rtype, 1, 120, len(rdata)) + rdata


class TestMdns(unittest.TestCase):
    def test_parse_chromecast_answer(self):
        inst = "Chromecast-abc123._googlecast._tcp.local"
        txt = b"".join(bytes([len(e)]) + e for e in [b"id=abc123", b"md=Chromecast", b"fn=Wohnzimmer"])
        pkt = struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 3)
        pkt += _rr(_name(mdns.SERVICE), mdns.T_PTR, _name(inst))
        pkt += _rr(_name(inst), mdns.T_SRV, struct.pack(">HHH", 0, 0, 8009) + _name("abc123.local"))
        pkt += _rr(_name(inst), mdns.T_TXT, txt)
        pkt += _rr(_name("abc123.local"), mdns.T_A, socket.inet_aton("192.168.1.50"))
        devs = mdns.devices_from_records(mdns.parse_response(pkt))
        self.assertEqual(len(devs), 1)
        d = devs[0]
        self.assertEqual((d.name, d.host, d.port, d.model, d.id), ("Wohnzimmer", "192.168.1.50", 8009, "Chromecast", "abc123"))

    def test_avahi_output(self):
        line = ('=;wlan0;IPv4;Chromecast-1a2b;_googlecast._tcp;local;1a2b.local;192.168.178.40;8009;'
                '"rs=" "fn=Wohnzimmer TV" "md=Chromecast" "id=1a2b3c"')
        devs = mdns.parse_avahi("+;wlan0;IPv4;x;_googlecast._tcp;local\n" + line)
        self.assertEqual(len(devs), 1)
        self.assertEqual((devs[0].name, devs[0].host, devs[0].model, devs[0].id),
                         ("Wohnzimmer TV", "192.168.178.40", "Chromecast", "1a2b3c"))

    def test_discover_runs(self):
        self.assertIsInstance(mdns.discover(timeout=1.0), list)

    def test_query_is_valid(self):
        q = mdns.build_query()
        self.assertEqual(struct.unpack(">H", q[4:6])[0], 1)
        self.assertIn(b"_googlecast", q)


class TestQr(unittest.TestCase):
    def test_qr_decodes(self):
        from lakuencast import qr
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("OpenCV fehlt")
        for text in ["http://192.168.178.20:47800/remote?k=Ab3dEf9hJkLm", "x" * 150]:
            m = qr.encode(text)
            n = len(m)
            img = np.full((n + 8, n + 8), 255, np.uint8)
            for r, row in enumerate(m):
                for c, v in enumerate(row):
                    if v:
                        img[r + 4, c + 4] = 0
            img = cv2.resize(img, ((n + 8) * 8, (n + 8) * 8), interpolation=cv2.INTER_NEAREST)
            self.assertEqual(cv2.QRCodeDetector().detectAndDecode(img)[0], text)


class TestFilePlan(unittest.TestCase):
    def test_plan(self):
        from lakuencast.filecast import plan
        base = {"format": "mov,mp4,m4a,3gp", "vcodec": "h264", "pix_fmt": "yuv420p", "height": 1080, "acodec": "aac"}
        self.assertEqual(plan(base), "direct")
        self.assertEqual(plan({**base, "format": "matroska,webm"}), "remux")
        self.assertEqual(plan({**base, "acodec": "ac3"}), "remux")
        self.assertEqual(plan({**base, "vcodec": "hevc"}), "transcode")
        self.assertEqual(plan({**base, "height": 2160}), "transcode")


def _pactl_ok() -> bool:
    if not shutil.which("pactl"):
        return False
    return subprocess.run(["pactl", "info"], capture_output=True).returncode == 0


@unittest.skipUnless(_pactl_ok(), "kein PulseAudio/PipeWire")
class TestPcMute(unittest.TestCase):
    def test_redirect_and_restore(self):
        from lakuencast.mirror import default_sink
        from lakuencast.pcaudio import PcMute
        old = default_sink()
        pm = PcMute()
        src = pm.start(old)
        self.assertEqual(src, "lakuencast.monitor")
        self.assertEqual(default_sink(), "lakuencast")
        pm.stop()
        self.assertEqual(default_sink(), old)
        sinks = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True).stdout
        self.assertNotIn("lakuencast", sinks)


class TestPlaylist(unittest.TestCase):
    def make(self, u, n):
        return f"P[{n}]{u}"

    def test_master(self):
        text = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\n720/index.m3u8\n#EXT-X-MEDIA:TYPE=AUDIO,URI=\"a/aud.m3u8\"\n"
        out = proxy.rewrite_playlist(text, "https://cdn.x/v/master.m3u8", self.make)
        self.assertIn("P[index.m3u8]https://cdn.x/v/720/index.m3u8", out)
        self.assertIn('URI="P[index.m3u8]https://cdn.x/v/a/aud.m3u8"', out)

    def test_media_with_key_and_disguised_segments(self):
        text = ("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"/key\"\n#EXTINF:4,\nseg1.jpg\n"
                "#EXTINF:4,\nhttps://other.cdn/seg2.ts?tok=1\n#EXT-X-ENDLIST\n")
        out = proxy.rewrite_playlist(text, "https://cdn.x/v/720/index.m3u8", self.make)
        self.assertIn('URI="P[key.bin]https://cdn.x/key"', out)
        self.assertIn("P[seg.ts]https://cdn.x/v/720/seg1.jpg", out)
        self.assertIn("P[seg.ts]https://other.cdn/seg2.ts?tok=1", out)
        self.assertIn("#EXT-X-ENDLIST", out)

    def test_fmp4(self):
        self.assertEqual(proxy.segment_format("#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n"), "fmp4")
        self.assertIsNone(proxy.segment_format("#EXTM3U\n#EXTINF:4,\na.ts\n"))


# ------------------------------------------------------------------ Integration

class Origin(BaseHTTPRequestHandler):
    """Gespielter Hoster: gibt das Video nur mit richtigem Referer heraus."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Referer") != "https://serie.example/folge-1":
            self.send_error(403)
            return
        files = {
            "/v/master.m3u8": b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n720/index.m3u8\n",
            "/v/720/index.m3u8": b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nseg0.png\n#EXT-X-ENDLIST\n",
            "/v/720/seg0.png": b"G" + b"\x00" * 187,
        }
        body = files.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl" if self.path.endswith("m3u8") else "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@unittest.skipUnless(shutil.which("openssl"), "openssl fehlt")
class TestIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fake_chromecast import FakeChromecast
        from lakuencast.app import App
        from lakuencast.server import make_server, serve_in_thread

        os.environ["LAKUENCAST_NO_UPDATE_CHECK"] = "1"
        cls.cc = FakeChromecast()
        cls.origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        threading.Thread(target=cls.origin.serve_forever, daemon=True).start()
        cls.port = free_port()
        cls.app = App(cls.port)
        cls.srv = make_server(cls.app, cls.port)
        serve_in_thread(cls.srv)

    def post(self, path, body=None, header=True):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json", **({"X-LakuenCast": "1"} if header else {})})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def test_1_post_needs_header(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/api/stop", header=False)
        self.assertEqual(e.exception.code, 400)

    def test_2_cast_through_proxy_and_auto_next_signal(self):
        r = self.post("/api/add", {"host": f"127.0.0.1:{self.cc.port}"})
        self.assertTrue(r["ok"], r)
        origin = f"http://127.0.0.1:{self.origin.server_address[1]}"
        r = self.post("/api/cast", {"url": f"{origin}/v/master.m3u8", "title": "Folge 1",
                                    "headers": {"Referer": "https://serie.example/folge-1", "X-Evil": "no"}})
        self.assertTrue(r["ok"], (r.get("error"), self.cc.errors))
        self.assertEqual(r["status"]["mode"], "casting")
        media = self.cc.loaded[-1]
        self.assertEqual(media["_start"], 0)
        self.assertEqual(media["contentType"], "application/x-mpegurl")
        self.assertIn("/p/", media["contentId"])

        # Chromecast lädt Master → Variante → Stück, alles über die Durchreiche
        for _ in range(50):
            if len(self.cc.fetched) >= 3:
                break
            time.sleep(0.1)
        self.assertGreaterEqual(len(self.cc.fetched), 3, self.cc.fetched)
        self.assertTrue(all(st == 200 for _, st, _ in self.cc.fetched), self.cc.fetched)
        self.assertTrue(self.cc.fetched[-1][0].endswith("/seg.ts"))
        self.assertTrue(self.cc.fetched[-1][2].startswith(b"G"))

        # Ohne gültiges Token gibt es nichts
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/p/falsch/{proxy.b64(origin)}/x.ts", timeout=5)

        # Folge zu Ende → finishedSeq zählt hoch
        before = self.app.status()["finishedSeq"]
        self.cc.finish()
        for _ in range(30):
            if self.app.status()["finishedSeq"] > before:
                break
            time.sleep(0.1)
        self.assertEqual(self.app.status()["finishedSeq"], before + 1)

    def test_3_volume_and_stop(self):
        self.post("/api/add", {"host": f"127.0.0.1:{self.cc.port}"})
        self.post("/api/volume", {"level": 0.3})
        self.assertAlmostEqual(self.cc.volume, 0.3)
        r = self.post("/api/stop")
        self.assertEqual(r["status"]["mode"], "idle")
        self.assertFalse(self.cc.app_running)

    def test_5_remote(self):
        key = self.app.config["remote_key"]
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/remote?k=falsch", timeout=5)
        self.assertEqual(e.exception.code, 403)
        html = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/remote?k={key}", timeout=5).read().decode()
        self.assertIn("Fernbedienung", html)
        before = self.app.next_seq
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/r/{key}/next", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=5))
        self.assertTrue(r["ok"])
        self.assertEqual(self.app.next_seq, before + 1)
        self.assertEqual(self.app.status()["nextSeq"], before + 1)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{self.port}/r/falsch/next", data=b"{}"),
                                   timeout=5)
        self.assertIn(f"/remote?k={key}", self.app.remote_url())

        # Zeitleiste ziehen → absoluter Sprung
        self.post("/api/add", {"host": f"127.0.0.1:{self.cc.port}"})
        origin = f"http://127.0.0.1:{self.origin.server_address[1]}"
        self.post("/api/cast", {"url": f"{origin}/v/master.m3u8", "headers": {"Referer": "https://serie.example/folge-1"},
                                "startAt": 612.5})
        self.assertEqual(self.cc.loaded[-1]["_start"], 612.5)  # mitten in der Folge weiter
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/r/{key}/seekto", data=b'{"time": 754}',
                                     headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=5))
        self.assertTrue(r["ok"], r)
        for _ in range(30):
            if self.cc.seeks:
                break
            time.sleep(0.1)
        self.assertEqual(self.cc.seeks[-1], 754)
        self.assertGreaterEqual(r["media"]["currentTime"], 754)

        # PC schlafen legen / ausschalten (Probelauf, ohne echt auszuführen)
        os.environ["LAKUENCAST_POWER_DRYRUN"] = "1"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/r/{key}/power", data=b'{"action": "suspend"}',
                                     headers={"Content-Type": "application/json"})
        self.assertTrue(json.load(urllib.request.urlopen(req, timeout=5))["ok"])
        self.assertEqual(self.app.power_requested, "suspend")
        self.assertEqual(self.app.mode, "idle")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/r/{key}/power", data=b'{"action": "rm -rf"}',
                                     headers={"Content-Type": "application/json"})
        self.assertFalse(json.load(urllib.request.urlopen(req, timeout=5))["ok"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg fehlt")
    def test_6_cast_files(self):
        tmp = Path(_tf.mkdtemp())
        mp4, avi = tmp / "film.mp4", tmp / "alt.avi"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
                        "-f", "lavfi", "-i", "sine=frequency=440", "-t", "12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(mp4)], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25",
                        "-t", "12", "-c:v", "mpeg4", str(avi)], check=True)
        self.post("/api/add", {"host": f"127.0.0.1:{self.cc.port}"})

        n0 = len(self.cc.fetched)
        r = self.post("/api/castfile", {"path": str(mp4)})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["file"]["mode"], "direct")
        self.assertEqual(self.cc.loaded[-1]["contentType"], "video/mp4")
        for _ in range(30):
            if len(self.cc.fetched) > n0:
                break
            time.sleep(0.1)
        url, st, head = self.cc.fetched[-1]
        self.assertEqual(st, 200)
        self.assertIn(b"ftyp", head[:16])
        # Spulen (Range)
        rq = urllib.request.Request(self.cc.loaded[-1]["contentId"], headers={"Range": "bytes=100-199"})
        with urllib.request.urlopen(rq, timeout=5) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(len(resp.read()), 100)

        r = self.post("/api/castfile", {"path": str(avi)})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["file"]["mode"], "transcode")
        self.assertEqual(self.cc.loaded[-1]["contentType"], "application/x-mpegurl")
        pl = urllib.request.urlopen(self.cc.loaded[-1]["contentId"], timeout=5).read().decode()
        self.assertIn("#EXTINF", pl)
        self.post("/api/stop")

    @unittest.skipUnless(shutil.which("Xvfb") and shutil.which("ffmpeg"), "Xvfb/ffmpeg fehlt")
    def test_7_mirror_low_latency(self):
        disp = ":96"
        xvfb = subprocess.Popen(["Xvfb", disp, "-screen", "0", "1280x720x24"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        old = os.environ.get("DISPLAY")
        os.environ["DISPLAY"] = disp
        try:
            time.sleep(1)
            self.post("/api/add", {"host": f"127.0.0.1:{self.cc.port}"})
            self.post("/api/option", {"low_latency": True})
            r = self.post("/api/mirror", {"quality": "720p"})
            self.assertTrue(r["ok"], r)
            media = self.cc.loaded[-1]
            self.assertTrue(media["contentId"].endswith("/live/stream.mp4"))
            self.assertEqual(media["contentType"], "video/mp4")
            with urllib.request.urlopen(media["contentId"], timeout=10) as resp:
                data = resp.read(4096)
            self.assertIn(b"ftyp", data[:32])
            self.post("/api/stop")
        finally:
            self.post("/api/option", {"low_latency": False})
            xvfb.terminate()
            if old is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = old

    @unittest.skipUnless(shutil.which("Xvfb") and shutil.which("ffmpeg"), "Xvfb/ffmpeg fehlt")
    def test_4_mirror_screen(self):
        disp = ":97"
        xvfb = subprocess.Popen(["Xvfb", disp, "-screen", "0", "1280x720x24"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        old = os.environ.get("DISPLAY")
        os.environ["DISPLAY"] = disp
        try:
            time.sleep(1)
            self.post("/api/add", {"host": f"127.0.0.1:{self.cc.port}"})
            n_before = len(self.cc.fetched)
            r = self.post("/api/mirror", {"quality": "720p"})
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["status"]["mode"], "mirroring")
            media = self.cc.loaded[-1]
            self.assertEqual(media["streamType"], "LIVE")
            self.assertTrue(media["contentId"].endswith("/live/live.m3u8"))
            time.sleep(1)
            got = self.cc.fetched[n_before:]
            self.assertTrue(got and got[0][1] == 200 and got[0][2].startswith(b"#EXTM3U"), got)
            # Livestream-Stück abrufbar
            pl = urllib.request.urlopen(media["contentId"], timeout=5).read().decode()
            seg = [ln for ln in pl.splitlines() if ln.endswith(".ts")][-1]
            data = urllib.request.urlopen(media["contentId"].rsplit("/", 1)[0] + "/" + seg, timeout=5).read()
            self.assertEqual(data[0], 0x47)  # MPEG-TS
            self.post("/api/stop")
        finally:
            xvfb.terminate()
            if old is None:
                os.environ.pop("DISPLAY", None)
            else:
                os.environ["DISPLAY"] = old


if __name__ == "__main__":
    unittest.main()
