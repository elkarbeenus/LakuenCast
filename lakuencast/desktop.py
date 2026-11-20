"""Einbindung in den Desktop: Startmenü-Eintrag, Autostart, Fenster öffnen."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

APP_ID = "lakuencast"
HOME = Path.home()
APPS_DIR = HOME / ".local/share/applications"
ICON_DIR = HOME / ".local/share/icons/hicolor/256x256/apps"
AUTOSTART = HOME / ".config/autostart" / f"{APP_ID}.desktop"


def _appimage() -> str | None:
    p = os.environ.get("APPIMAGE")
    return p if p and os.path.isfile(p) else None


def _desktop_entry(exec_line: str, autostart: bool = False) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=LakuenCast",
        "Comment=Bild und Ton auf den Chromecast übertragen",
        f"Exec={exec_line}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=AudioVideo;Video;Network;",
        "StartupWMClass=LakuenCast",
    ]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


def install_menu_entry() -> None:
    """Startmenü-Eintrag anlegen/aktualisieren (zeigt immer auf die aktuelle AppImage-Datei)."""
    app = _appimage()
    if not app:
        return
    try:
        APPS_DIR.mkdir(parents=True, exist_ok=True)
        (APPS_DIR / f"{APP_ID}.desktop").write_text(_desktop_entry(f'"{app}"'))
        icon_src = Path(os.environ.get("APPDIR", "")) / f"{APP_ID}.png"
        if icon_src.is_file():
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(icon_src, ICON_DIR / f"{APP_ID}.png")
        if AUTOSTART.exists():
            set_autostart(True)  # Pfad aktuell halten
    except OSError:
        pass


def autostart_enabled() -> bool:
    return AUTOSTART.exists()


def set_autostart(on: bool) -> bool:
    app = _appimage()
    if on:
        if not app:
            return False
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(_desktop_entry(f'"{app}" --background', autostart=True))
    else:
        try:
            AUTOSTART.unlink()
        except FileNotFoundError:
            pass
    return True


# ------------------------------------------------------------------ Fenster

_BROWSERS = [
    ["flatpak", "run", "com.brave.Browser"],
    ["brave"], ["brave-browser"],
    ["flatpak", "run", "org.chromium.Chromium"], ["chromium"], ["chromium-browser"],
    ["flatpak", "run", "com.google.Chrome"], ["google-chrome"], ["google-chrome-stable"],
    ["flatpak", "run", "com.microsoft.Edge"], ["microsoft-edge"],
]


def _flatpak_installed(app_id: str) -> bool:
    try:
        return subprocess.run(["flatpak", "info", app_id], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_window(url: str) -> None:
    """Oberfläche als eigenes App-Fenster öffnen (Chromium-Browser im --app-Modus)."""
    for cmd in _BROWSERS:
        if cmd[0] == "flatpak":
            if not shutil.which("flatpak") or not _flatpak_installed(cmd[2]):
                continue
        elif not shutil.which(cmd[0]):
            continue
        try:
            subprocess.Popen([*cmd, f"--app={url}", "--class=LakuenCast", "--window-size=580,920"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue
    opener = shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, url], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
