"""Video-Durchreiche für den Chromecast.

Viele Hoster geben Videos nur heraus, wenn die Anfrage den richtigen Referer,
User-Agent oder Cookie mitschickt. Der Chromecast kann das nicht. Deshalb holt
LakuenCast das Video mit diesen Angaben und reicht es an den Chromecast durch.
HLS-Playlists (.m3u8) werden dabei so umgeschrieben, dass auch alle Stücke über
LakuenCast laufen.
"""

from __future__ import annotations

import base64
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

ALLOWED_HEADERS = {"referer", "user-agent", "cookie", "origin", "accept-language"}
DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0 Safari/537.36")


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def unb64(s: str) -> str:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


@dataclass
class ProxySession:
    token: str
    headers: dict = field(default_factory=dict)


class Proxy:
    def __init__(self):
        self.sessions: dict[str, ProxySession] = {}

    def new_session(self, headers: dict | None) -> ProxySession:
        clean = {}
        for k, v in (headers or {}).items():
            if k.lower() in ALLOWED_HEADERS and isinstance(v, str) and v:
                clean[k.title()] = v
        clean.setdefault("User-Agent", DEFAULT_UA)
        if "Referer" in clean and "Origin" not in clean:
            u = urllib.parse.urlsplit(clean["Referer"])
            if u.scheme and u.netloc:
                clean["Origin"] = f"{u.scheme}://{u.netloc}"
        token = secrets.token_urlsafe(16)
        sess = ProxySession(token, clean)
        # Nur die letzten paar Sitzungen behalten
        if len(self.sessions) > 8:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[token] = sess
        return sess

    @staticmethod
    def make_url(base: str, token: str, target: str, name: str) -> str:
        return f"{base}/p/{token}/{b64(target)}/{name}"

    # -------------------------------------------------------------- Abruf

    def open(self, sess: ProxySession, target: str, range_header: str | None = None, timeout: float = 20.0):
        headers = dict(sess.headers)
        if range_header:
            headers["Range"] = range_header
        req = urllib.request.Request(target, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            return e  # HTTPError ist auch ein Response-Objekt


def is_playlist(url: str, content_type: str, head: bytes) -> bool:
    ct = (content_type or "").lower()
    return ("mpegurl" in ct or urllib.parse.urlsplit(url).path.lower().endswith(".m3u8")
            or head.lstrip().startswith(b"#EXTM3U"))


_URI_ATTR = re.compile(r'URI="([^"]+)"')
_SEGMENT_EXT = (".ts", ".m4s", ".mp4", ".aac", ".m4a", ".mp3", ".vtt", ".webvtt")


def _segment_name(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    for ext in _SEGMENT_EXT:
        if path.endswith(ext):
            return "seg" + ext
    return "seg.ts"  # getarnte Stücke (.jpg/.png/.html) als MPEG-TS ausgeben


def rewrite_playlist(text: str, playlist_url: str, make: callable) -> str:
    """Alle URIs einer Playlist durch Proxy-URLs ersetzen.

    make(absolute_url, name) -> neue URL
    """
    is_master = "#EXT-X-STREAM-INF" in text
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            out.append(raw)
            continue
        if line.startswith("#"):
            if 'URI="' in line:
                tag = line.split(":", 1)[0]

                def repl(m, tag=tag):
                    absu = urllib.parse.urljoin(playlist_url, m.group(1))
                    if tag == "#EXT-X-KEY":
                        name = "key.bin"
                    elif tag == "#EXT-X-MAP":
                        name = "init.mp4"
                    elif tag in ("#EXT-X-MEDIA", "#EXT-X-I-FRAME-STREAM-INF"):
                        name = "index.m3u8"
                    else:
                        name = "file.bin"
                    return f'URI="{make(absu, name)}"'

                line = _URI_ATTR.sub(repl, line)
            out.append(line)
            continue
        absu = urllib.parse.urljoin(playlist_url, line)
        name = "index.m3u8" if is_master else _segment_name(absu)
        out.append(make(absu, name))
    return "\n".join(out) + "\n"


def segment_format(playlist_text: str) -> str | None:
    """'fmp4' wenn die Playlist fMP4-Stücke nutzt, sonst None (MPEG-TS)."""
    if "#EXT-X-MAP" in playlist_text or ".m4s" in playlist_text:
        return "fmp4"
    return None
