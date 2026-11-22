"""Gespeicherte Einstellungen (~/.config/lakuencast/config.json)."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "lakuencast" / "config.json"

DEFAULTS = {
    "remote_key": "",        # Schlüssel für die Handy-Fernbedienung
    "mute_pc": True,         # PC-Ton beim Spiegeln stumm (Ton nur am Fernseher)
    "low_latency": False,    # Spiegeln mit geringer Verzögerung (experimentell)
    "input_enabled": False,  # Maus, Tastatur & Vorschau vom Handy erlauben
    "sessions": {},          # Handy-Verbindungen: SHA-256 des Tokens → läuft ab um (Unix-Zeit)
    "quality": "720p",
    "last_device": "",
}


class Config:
    def __init__(self, path: Path = PATH):
        self.path = path
        self.data = dict(DEFAULTS)
        try:
            self.data.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass
        if not self.data.get("remote_key") or not self.data.get("pair_pin"):
            self.new_remote_key()  # legt QR-Code und 6-stelligen Code an und speichert

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, **kw) -> None:
        self.data.update(kw)
        self.save()

    def new_remote_key(self) -> str:
        """Neuer QR-Code und neuer 6-stelliger Code (beide gelten nur einmal)."""
        self.set(remote_key=secrets.token_urlsafe(12), pair_pin=f"{secrets.randbelow(1_000_000):06d}")
        return self.data["remote_key"]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            os.replace(tmp, self.path)
        except OSError:
            pass
