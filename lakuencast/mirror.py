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
    """Bildschirm + Ton aufnehmen.

    Normal: HLS-Livestream (live.m3u8), läuft stabil, ca. 3–6 s Verzögerung.
    low_latency: fragmentiertes MP4 direkt pro Abruf (stream.mp4), ca. 1–2 s.
    mute_pc: PC-Ton in ein unsichtbares Gerät umleiten, Ton nur am Fernseher.
    """

    def __init__(self, quality: str = "720p", low_latency: bool = False, mute_pc: bool = False):
        self.quality = quality if quality in QUALITY else "720p"
        self.low_latency = low_latency
        self.mute_pc = mute_pc
        self.dir = Path(tempfile.mkdtemp(prefix="lakuencast-live-"))
        self.proc: subprocess.Popen | None = None
        self.audio_proc: subprocess.Popen | None = None
        self.error: str | None = None
        self.audio_source: str | None = None
        self.pc_muted = False
        self._pcmute = None
        self._log_tail: list[str] = []
        self._ffmpeg = None
        self._devices: set[str] = set()
        self._use_parec = False
        self._lock = threading.Lock()

    @property
    def playlist(self) -> Path:
        return self.dir / "live.m3u8"

    @property
    def stream_name(self) -> str:
        return "stream.mp4" if self.low_latency else "live.m3u8"

    def build_command(self, ffmpeg: str, devices: set[str], audio_source: str | None,
                      display: str, use_parec: bool, output: str = "hls") -> list[str]:
        q = QUALITY[self.quality]
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]
        if not use_parec:
            cmd.append("-nostdin")
        if output == "fmp4":
            cmd += ["-fflags", "nobuffer", "-probesize", "32"]
        cmd += ["-thread_queue_size", "512", "-f", "x11grab", "-framerate", str(q["fps"]),
                "-draw_mouse", "1", "-i", display]
        has_audio = False
        if audio_source and "pulse" in devices and not use_parec:
            cmd += ["-thread_queue_size", "1024", "-f", "pulse", "-ac", "2", "-ar", "48000", "-i", audio_source]
            has_audio = True
        elif audio_source and use_parec:
            cmd += ["-thread_queue_size", "1024", "-f", "s16le", "-ac", "2", "-ar", "48000", "-i", "pipe:0"]
            has_audio = True
        gop = q["fps"] // 2 if output == "fmp4" else q["fps"]
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
        if output == "fmp4":
            cmd += ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                    "-frag_duration", "200000", "pipe:1"]
        else:
            cmd += [
                "-f", "hls", "-hls_time", "1", "-hls_list_size", "6",
                "-hls_flags", "delete_segments+independent_segments+omit_endlist",
                "-hls_segment_filename", str(self.dir / "seg_%05d.ts"),
                str(self.playlist),
            ]
        return cmd

    def _prepare(self) -> None:
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("ffmpeg wurde nicht gefunden.")
        devices = ffmpeg_devices(ffmpeg)
        if "x11grab" not in devices:
            raise RuntimeError("Dieses ffmpeg kann den Bildschirm nicht aufnehmen (x11grab fehlt).")
        sink = default_sink()
        source = f"{sink}.monitor" if sink else None
        if sink and self.mute_pc:
            from .pcaudio import PcMute
            if PcMute.available():
                try:
                    self._pcmute = PcMute()
                    source = self._pcmute.start(sink)
                    self.pc_muted = True
                except Exception as e:  # noqa: BLE001
                    log.warning("PC-Ton nicht umleitbar: %s", e)
                    self._pcmute = None
        use_parec = bool(source) and "pulse" not in devices and shutil.which("parec") is not None
        if source and "pulse" not in devices and not use_parec:
            source = None
        self.audio_source = source
        self._ffmpeg, self._devices, self._use_parec = ffmpeg, devices, use_parec

    def _spawn(self, output: str) -> subprocess.Popen:
        display = os.environ.get("DISPLAY", ":0")
        cmd = self.build_command(self._ffmpeg, self._devices, self.audio_source, display, self._use_parec, output)
        log.info("Starte: %s", " ".join(cmd))
        stdin = None
        if self._use_parec:
            self.audio_proc = subprocess.Popen(
                ["parec", f"--device={self.audio_source}", "--format=s16le", "--rate=48000", "--channels=2",
                 "--raw", "--latency-msec=20"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            stdin = self.audio_proc.stdout
        proc = subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.PIPE if output == "fmp4" else subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        self.proc = proc
        threading.Thread(target=self._read_log, args=(proc,), daemon=True).start()
        return proc

    def start(self) -> None:
        self._prepare()
        if not self.low_latency:
            self._spawn("hls")

    def open_stream(self) -> subprocess.Popen:
        """Nur geringe Verzögerung: pro Abruf ein frischer MP4-Strom (alter wird beendet)."""
        with self._lock:
            self._kill()
            return self._spawn("fmp4")

    def _read_log(self, proc: subprocess.Popen) -> None:
        assert proc.stderr
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self._log_tail = (self._log_tail + [line])[-20:]
                log.debug("ffmpeg: %s", line)

    def wait_ready(self, segments: int = 2, timeout: float = 15.0) -> bool:
        if self.low_latency:
            return True
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
        return self.low_latency or bool(self.proc and self.proc.poll() is None)

    def _kill(self) -> None:
        for p in (self.proc, self.audio_proc):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.proc = self.audio_proc = None

    def stop(self) -> None:
        self._kill()
        if self._pcmute:
            self._pcmute.stop()
            self._pcmute = None
            self.pc_muted = False
        shutil.rmtree(self.dir, ignore_errors=True)
