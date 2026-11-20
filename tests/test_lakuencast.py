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

    def test_query_is_valid(self):
        q = mdns.build_query()
        self.assertEqual(struct.unpack(">H", q[4:6])[0], 1)
        self.assertIn(b"_googlecast", q)


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
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"]["mode"], "casting")
        media = self.cc.loaded[-1]
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
