"""PC-Ton beim Spiegeln stumm schalten: Alle Töne laufen in ein unsichtbares
Audiogerät ("LakuenCast"), dessen Ton nur an den Fernseher geht.
Nutzt pactl (PipeWire/PulseAudio). Beim Stoppen wird alles zurückgestellt."""

from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("lakuencast.audio")
SINK = "lakuencast"


def _pactl(*args: str) -> str:
    r = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"pactl {' '.join(args)} fehlgeschlagen")
    return r.stdout.strip()


class PcMute:
    def __init__(self):
        self.module_id: str | None = None
        self.old_sink: str | None = None
        self.moved: list[str] = []

    @staticmethod
    def available() -> bool:
        return shutil.which("pactl") is not None

    def start(self, old_sink: str) -> str:
        """Gibt den Namen der Quelle zurück, die aufgenommen werden soll."""
        self.old_sink = old_sink
        self.module_id = _pactl("load-module", "module-null-sink", f"sink_name={SINK}",
                                "sink_properties=device.description=LakuenCast")
        _pactl("set-default-sink", SINK)
        for line in _pactl("list", "short", "sink-inputs").splitlines():
            sid = line.split()[0] if line.strip() else ""
            if sid:
                try:
                    _pactl("move-sink-input", sid, SINK)
                    self.moved.append(sid)
                except RuntimeError:
                    pass
        log.info("PC-Ton umgeleitet (%d Wiedergaben)", len(self.moved))
        return f"{SINK}.monitor"

    def stop(self) -> None:
        if not self.module_id:
            return
        try:
            if self.old_sink:
                _pactl("set-default-sink", self.old_sink)
                for line in _pactl("list", "short", "sink-inputs").splitlines():
                    parts = line.split()
                    if len(parts) > 1:
                        try:
                            _pactl("move-sink-input", parts[0], self.old_sink)
                        except RuntimeError:
                            pass
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            log.warning("Ton zurückstellen: %s", e)
        try:
            _pactl("unload-module", self.module_id)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            pass
        self.module_id = None
        self.moved = []
