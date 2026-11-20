"""GUI-Test (nur wenn PySide6 da ist): Fenster offscreen aufbauen und
verschiedene Zustände darstellen. Bilder landen in $LAKUENCAST_SCREENS."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LAKUENCAST_NO_UPDATE_CHECK"] = "1"

try:
    from PySide6.QtWidgets import QApplication
    HAVE_QT = True
except Exception:  # noqa: BLE001
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 fehlt")
class TestGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from lakuencast.app import App
        from lakuencast.gui import MainWindow
        from lakuencast.mdns import Device
        cls.qapp = QApplication.instance() or QApplication([])
        cls.app = App(0)
        cls.app.devices = {"a": Device("a", "Wohnzimmer-TV", "192.168.178.40", model="Chromecast"),
                           "b": Device("b", "Schlafzimmer", "192.168.178.41", model="Chromecast Ultra")}
        cls.app.selected_id = "a"
        cls.win = MainWindow(cls.app, tray_available=False)
        cls.win.show()
        cls.out = Path(os.environ.get("LAKUENCAST_SCREENS", "/tmp/lakuencast-screens"))
        cls.out.mkdir(parents=True, exist_ok=True)

    def shot(self, name, st):
        self.win.render(st)
        self.qapp.processEvents()
        self.win.adjustSize()
        self.qapp.processEvents()
        self.assertTrue(self.win.grab().save(str(self.out / f"{name}.png")))

    def test_idle(self):
        st = self.app.status()
        self.shot("1-start", st)
        self.assertFalse(self.win.now_card.isVisible())
        self.assertEqual(self.win.devices.count(), 2)

    def test_casting_with_update(self):
        st = self.app.status()
        st.update(mode="casting", title="Serie Staffel 1 Folge 3", connected=True,
                  media={"playerState": "PLAYING", "currentTime": 754, "duration": 1420},
                  volume={"level": 0.4})
        st["update"] = {**st["update"], "available": {"version": "v0.2.0", "display": "v0.2.0", "page": ""},
                        "canInstall": True, "state": "idle"}
        self.shot("2-video-update", st)
        self.assertTrue(self.win.now_card.isVisible())
        self.assertTrue(self.win.update_box.isVisible())
        self.assertEqual(self.win.t_cur.text(), "12:34")

    def test_error(self):
        st = self.app.status()
        st.update(error="Der Hoster gibt das Video nicht heraus (HTTP 403).", mode="idle")
        self.shot("3-fehler", st)
        self.assertTrue(self.win.notice_box.isVisible())


if __name__ == "__main__":
    unittest.main()
