"""Bildschirm + Ton aufnehmen und als HLS-Livestream bereitstellen (ffmpeg).

Video: X11-Bildschirm (x11grab). SteamOS nutzt im Desktop-Modus X11.
Ton:   Monitor der Standard-Audioausgabe (PipeWire/PulseAudio), entweder direkt
       über ffmpeg (-f pulse) oder über "parec" als Pipe.
Ausgabe: HLS mit 1-Sekunden-Stücken, H.264 + AAC (läuft auf jedem Chromecast).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("lakuencast.mirror")

QUALITY = {
    "720p": {"height": 720, "bitrate": "3500k", "fps": 30},
    "1080p": {"height": 1080, "bitrate": "6000k", "fps": 30},
}


def find_ffmpeg() -> str | None:
    candidates = [os.environ.get("LAKUENCAST_FFMPEG")]
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(os.path.join(appdir, "usr", "bin", "ffmpeg"))
    candidates.append(shutil.which("ffmpeg"))
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def ffmpeg_devices(ffmpeg: str) -> set[str]:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-devices"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    found = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and set(parts[0]) <= set("DE.") and parts[0] != "--":
            found.add(parts[1])
    return found


def default_sink() -> str | None:
    pactl = shutil.which("pactl")
    if not pactl:
        return None
    try:
        r = subprocess.run([pactl, "get-default-sink"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        info = subprocess.run([pactl, "info"], capture_output=True, text=True, timeout=5).stdout
        for line in info.splitlines():
            if line.lower().startswith(("default sink:", "standard-ziel:", "standard-senke:")):
                return line.split(":", 1)[1].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def session_problem() -> str | None:
    """Gibt einen Hinweis zurück, wenn die Bildschirmaufnahme nicht klappen wird."""
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return ("Wayland-Sitzung erkannt: Die Aufnahme zeigt evtl. nur ein schwarzes Bild. "
                "Im SteamOS-Desktop-Modus (X11) klappt es.")
    if not os.environ.get("DISPLAY"):
        return "Kein Bildschirm (DISPLAY) gefunden."
    return None


class Mirror:
    def __init__(self, quality: str = "720p"):
        self.quality = quality if quality in QUALITY else "720p"
        self.dir = Path(tempfile.mkdtemp(prefix="lakuencast-live-"))
        self.proc: subprocess.Popen | None = None
        self.audio_proc: subprocess.Popen | None = None
        self.error: str | None = None
        self.audio_source: str | None = None
        self._log_tail: list[str] = []

    @property
    def playlist(self) -> Path:
        return self.dir / "live.m3u8"

    def build_command(self, ffmpeg: str, devices: set[str], sink: str | None,
                      display: str, use_parec: bool) -> list[str]:
        q = QUALITY[self.quality]
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin" if not use_parec else "-y"]
        cmd += ["-thread_queue_size", "512", "-f", "x11grab", "-framerate", str(q["fps"]),
                "-draw_mouse", "1", "-i", display]
        has_audio = False
        if sink and "pulse" in devices and not use_parec:
            cmd += ["-thread_queue_size", "1024", "-f", "pulse", "-ac", "2", "-ar", "48000", "-i", f"{sink}.monitor"]
            has_audio = True
        elif sink and use_parec:
            cmd += ["-thread_queue_size", "1024", "-f", "s16le", "-ac", "2", "-ar", "48000", "-i", "pipe:0"]
            has_audio = True
        gop = q["fps"]
        cmd += [
            "-map", "0:v:0",
            "-vf", f"scale=-2:{q['height']}:flags=bicubic,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
            "-profile:v", "high", "-level", "4.1",
            "-b:v", q["bitrate"], "-maxrate", q["bitrate"], "-bufsize", q["bitrate"],
            "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
        ]
        if has_audio:
            cmd += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
        cmd += [
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "6",
            "-hls_flags", "delete_segments+independent_segments+omit_endlist",
            "-hls_segment_filename", str(self.dir / "seg_%05d.ts"),
            str(self.playlist),
        ]
        return cmd

    def start(self) -> None:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg wurde nicht gefunden.")
        devices = ffmpeg_devices(ffmpeg)
        if "x11grab" not in devices:
            raise RuntimeError("Dieses ffmpeg kann den Bildschirm nicht aufnehmen (x11grab fehlt).")
        display = os.environ.get("DISPLAY", ":0")
        sink = default_sink()
        use_parec = bool(sink) and "pulse" not in devices and shutil.which("parec") is not None
        if sink and "pulse" not in devices and not use_parec:
            sink = None
        self.audio_source = f"{sink}.monitor" if sink else None

        cmd = self.build_command(ffmpeg, devices, sink, display, use_parec)
        log.info("Starte: %s", " ".join(cmd))
        stdin = None
        if use_parec:
            self.audio_proc = subprocess.Popen(
                ["parec", f"--device={sink}.monitor", "--format=s16le", "--rate=48000", "--channels=2", "--raw",
                 "--latency-msec=20"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            stdin = self.audio_proc.stdout
        self.proc = subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._read_log, daemon=True).start()

    def _read_log(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            line = line.rstrip()
            if line:
                self._log_tail = (self._log_tail + [line])[-20:]
                log.debug("ffmpeg: %s", line)

    def wait_ready(self, segments: int = 2, timeout: float = 15.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc and self.proc.poll() is not None:
                self.error = "ffmpeg ist abgebrochen: " + (" | ".join(self._log_tail[-3:]) or "unbekannter Fehler")
                return False
            if self.playlist.exists():
                text = self.playlist.read_text(errors="replace")
                if text.count("#EXTINF") >= segments:
                    return True
            time.sleep(0.25)
        self.error = "Der Livestream startet nicht (Zeitüberschreitung)."
        return False

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def stop(self) -> None:
        for p in (self.proc, self.audio_proc):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.proc = self.audio_proc = None
        shutil.rmtree(self.dir, ignore_errors=True)
