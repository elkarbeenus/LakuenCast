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
        if not self.data.get("remote_key"):
            self.data["remote_key"] = secrets.token_urlsafe(12)
            self.save()

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, **kw) -> None:
        self.data.update(kw)
        self.save()

    def new_remote_key(self) -> str:
        self.set(remote_key=secrets.token_urlsafe(12))
        return self.data["remote_key"]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            os.replace(tmp, self.path)
        except OSError:
            pass
