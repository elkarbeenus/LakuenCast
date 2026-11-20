"""Eigene Videodateien an den Chromecast senden.

Passt die Datei (MP4 mit H.264 + AAC/MP3), wird sie direkt bereitgestellt
(mit Spulen). Sonst wandelt ffmpeg sie während des Abspielens in HLS um:
Video wird nur umgepackt, wenn es schon H.264 ist, sonst neu kodiert.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .mirror import find_ffmpeg

log = logging.getLogger("lakuencast.file")

VIDEO_EXT = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts", ".m2ts", ".wmv", ".flv", ".mpg", ".mpeg")


def find_ffprobe() -> str | None:
    ff = find_ffmpeg()
    if ff:
        cand = os.path.join(os.path.dirname(ff), "ffprobe")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return shutil.which("ffprobe")


def probe(path: str) -> dict:
    tool = find_ffprobe()
    if not tool:
        raise RuntimeError("ffprobe wurde nicht gefunden.")
    r = subprocess.run([tool, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError("Datei lässt sich nicht lesen: " + (r.stderr.strip().splitlines() or ["?"])[-1])
    info = json.loads(r.stdout)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {
        "format": (info.get("format") or {}).get("format_name", ""),
        "duration": float((info.get("format") or {}).get("duration") or 0),
        "vcodec": v.get("codec_name") if v else None,
        "pix_fmt": v.get("pix_fmt") if v else None,
        "height": int(v.get("height") or 0) if v else 0,
        "acodec": a.get("codec_name") if a else None,
        "channels": int(a.get("channels") or 0) if a else 0,
    }


def plan(p: dict) -> str:
    """'direct' | 'remux' (Video kopieren, Ton umwandeln) | 'transcode'."""
    h264_ok = p["vcodec"] == "h264" and p["pix_fmt"] in (None, "yuv420p", "yuvj420p") and p["height"] <= 1080
    audio_ok = p["acodec"] in (None, "aac", "mp3")
    is_mp4 = "mp4" in p["format"] or "mov" in p["format"]
    if is_mp4 and h264_ok and audio_ok:
        return "direct"
    if h264_ok:
        return "remux"
    return "transcode"


class FileCast:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        if not os.path.isfile(self.path):
            raise RuntimeError("Datei nicht gefunden.")
        self.info = probe(self.path)
        if not self.info["vcodec"]:
            raise RuntimeError("Die Datei enthält kein Video.")
        self.mode = plan(self.info)
        self.dir: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None
        self._log: list[str] = []

    @property
    def title(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def name(self) -> str:
        return "video.mp4" if self.mode == "direct" else "index.m3u8"

    def start(self) -> None:
        if self.mode == "direct":
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg wurde nicht gefunden.")
        self.dir = Path(tempfile.mkdtemp(prefix="lakuencast-file-"))
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y", "-i", self.path,
               "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
        if self.mode == "remux":
            cmd += ["-c:v", "copy"]
        else:
            cmd += ["-vf", "scale=-2:'min(1080,ih)':flags=bicubic,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "8M", "-bufsize", "16M",
                    "-profile:v", "high", "-level", "4.1", "-g", "96"]
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
                "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "event",
                "-hls_segment_filename", str(self.dir / "seg_%05d.ts"), str(self.dir / "index.m3u8")]
        log.info("Wandle um (%s): %s", self.mode, " ".join(cmd))
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        threading.Thread(target=self._read_log, daemon=True).start()

    def _read_log(self) -> None:
        assert self.proc and self.proc.stderr
        for raw in self.proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._log = (self._log + [line])[-10:]

    def wait_ready(self, segments: int = 2, timeout: float = 60.0) -> bool:
        if self.mode == "direct":
            return True
        pl = self.dir / "index.m3u8"  # type: ignore[operator]
        end = time.time() + timeout
        while time.time() < end:
            if pl.exists():
                text = pl.read_text(errors="replace")
                if text.count("#EXTINF") >= segments or "#EXT-X-ENDLIST" in text:
                    return True
            if self.proc and self.proc.poll() is not None and self.proc.returncode != 0:
                self.error = "Umwandeln fehlgeschlagen: " + (" | ".join(self._log[-2:]) or "?")
                return False
            time.sleep(0.3)
        self.error = "Umwandeln dauert zu lange."
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None
