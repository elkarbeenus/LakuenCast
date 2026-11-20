"""LakuenCast starten.

  lakuencast               Server starten und Fenster öffnen
  lakuencast --background  nur Server (für Autostart), kein Fenster
  lakuencast --no-window   wie --background
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
import urllib.request

from . import desktop
from .app import App
from .server import make_server, serve_in_thread
from .version import VERSION, display

PORT = 47800


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as r:
            return json.load(r).get("app") == "LakuenCast"
    except Exception:  # noqa: BLE001
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lakuencast")
    p.add_argument("--background", action="store_true", help="ohne Fenster starten")
    p.add_argument("--no-window", action="store_true", help="ohne Fenster starten")
    p.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--debug", action="store_true")
    p.add_argument("--version", action="version", version=f"LakuenCast {display(VERSION)}")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="[LakuenCast] %(levelname)s %(name)s: %(message)s")
    url = f"http://127.0.0.1:{args.port}/"
    show_window = not (args.background or args.no_window or args.after_update)

    # Läuft schon? Dann nur das Fenster zeigen.
    if already_running(args.port):
        if show_window:
            desktop.open_window(url)
        return 0

    app = App(args.port)
    srv = None
    for _ in range(40):  # nach einem Update kann der alte Prozess den Port noch kurz halten
        try:
            srv = make_server(app, args.port)
            break
        except OSError:
            time.sleep(0.25)
    if srv is None:
        print(f"Port {args.port} ist belegt.", file=sys.stderr)
        return 1
    serve_in_thread(srv)
    logging.info("LakuenCast %s läuft auf %s", display(VERSION), url)

    desktop.install_menu_entry()
    app.scan_async()
    threading.Thread(target=app.updater.check, daemon=True).start()
    if show_window:
        desktop.open_window(url)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    last_check = time.time()
    while not stop.wait(5):
        if time.time() - last_check > 6 * 3600:
            last_check = time.time()
            threading.Thread(target=app.updater.check, daemon=True).start()
    app.stop()
    srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
