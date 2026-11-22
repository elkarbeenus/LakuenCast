"""Update aus dem Programm heraus: neues AppImage von GitHub laden, SHA-256
prüfen, die laufende AppImage-Datei ersetzen und neu starten.

LAKUENCAST_NO_UPDATE_CHECK=1   schaltet die Prüfung ab (offline)
LAKUENCAST_UPDATE_TAG=<tag>    bestimmtes Release statt "latest" (Entwicklung)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request

from .version import REPO, VERSION, display, is_newer

log = logging.getLogger("lakuencast.update")

ASSET = "LakuenCast-x86_64.AppImage"


class Updater:
    def __init__(self):
        self.available: dict | None = None
        self.state = "idle"        # idle | checking | downloading | installing | error | restarting
        self.progress = 0.0
        self.error: str | None = None
        self.checked_at = 0.0

    @property
    def appimage(self) -> str | None:
        p = os.environ.get("APPIMAGE")
        return p if p and os.path.isfile(p) else None

    def to_dict(self) -> dict:
        return {
            "installed": VERSION,
            "installedDisplay": display(VERSION),
            "available": self.available,
            "state": self.state,
            "progress": round(self.progress, 3),
            "error": self.error,
            "canInstall": self.appimage is not None,
        }

    def _api(self) -> str:
        tag = os.environ.get("LAKUENCAST_UPDATE_TAG")
        if tag:
            return f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
        return f"https://api.github.com/repos/{REPO}/releases/latest"

    def check(self) -> dict | None:
        if os.environ.get("LAKUENCAST_NO_UPDATE_CHECK"):
            return None
        self.state = "checking"
        try:
            req = urllib.request.Request(self._api(), headers={"Accept": "application/vnd.github+json",
                                                               "User-Agent": "LakuenCast"})
            with urllib.request.urlopen(req, timeout=15) as r:
                rel = json.load(r)
            assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
            tag = rel.get("tag_name", "")
            if is_newer(tag, VERSION) and ASSET in assets and f"{ASSET}.sha256" in assets:
                self.available = {
                    "version": tag,
                    "display": display(tag),
                    "url": assets[ASSET],
                    "sha256Url": assets[f"{ASSET}.sha256"],
                    "page": rel.get("html_url"),
                    "notes": next((ln.strip() for ln in (rel.get("body") or "").splitlines() if ln.strip()), ""),
                }
            else:
                self.available = None
            self.state = "idle"
            self.error = None
        except Exception as e:  # noqa: BLE001
            self.state = "idle"
            log.info("Update-Prüfung fehlgeschlagen: %s", e)
        self.checked_at = time.time()
        return self.available

    def install_async(self, on_restart) -> None:
        threading.Thread(target=self._install, args=(on_restart,), daemon=True).start()

    def _install(self, on_restart) -> None:
        target = self.appimage
        if not self.available or not target:
            self.state, self.error = "error", "Kein Update oder nicht als AppImage gestartet."
            return
        tmp = target + ".new"
        try:
            self.state, self.progress, self.error = "downloading", 0.0, None
            with urllib.request.urlopen(self.available["sha256Url"], timeout=30) as r:
                expected = r.read().decode().split()[0].lower()

            h = hashlib.sha256()
            req = urllib.request.Request(self.available["url"], headers={"User-Agent": "LakuenCast"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if total:
                        self.progress = done / total
            if h.hexdigest().lower() != expected:
                raise RuntimeError("SHA-256 stimmt nicht – Update abgebrochen, nichts geändert.")

            self.state = "installing"
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)  # atomar
            self.state = "restarting"
            log.info("Update installiert, starte neu …")
            on_restart()
            # Neue Version mit sauberer Umgebung starten (ohne Reste des alten AppImages)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("APPDIR", "APPIMAGE", "ARGV0", "OWD", "PYTHONHOME", "PYTHONPATH")}
            subprocess.Popen([target, "--after-update"], start_new_session=True, env=env, cwd=os.path.expanduser("~"),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.8)
            os._exit(0)
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", str(e)
            try:
                os.remove(tmp)
            except OSError:
                pass


def restart_args() -> list[str]:
    return [sys.executable, *sys.argv]
