"""LakuenCast – eigenes Fenster (Qt) mit Symbol in der Taskleiste.

Die Oberfläche spricht direkt mit dem App-Objekt im selben Prozess. Der
HTTP-Server läuft daneben weiter (für LakuenWatcher und den Chromecast).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu, QProgressBar, QPushButton, QSizePolicy,
                               QSlider, QSystemTrayIcon, QVBoxLayout, QWidget)

from . import desktop, qr
from .app import App
from .filecast import VIDEO_EXT

ICON = Path(__file__).parent / "web" / "icon.png"

STYLE = """
* { font-size: 13px; color: #f2f0fa; }
QWidget#root { background: #12111a; }
QFrame.card { background: #1c1a27; border: 1px solid #34304a; border-radius: 14px; }
QFrame#updateBanner { background: #173326; border: 1px solid #30a46c; border-radius: 12px; }
QFrame#noticeWarn { background: #3a2d12; border: 1px solid #f5a524; border-radius: 12px; }
QFrame#noticeError { background: #3b1719; border: 1px solid #e5484d; border-radius: 12px; }
QLabel#title { font-size: 20px; font-weight: 700; }
QLabel.h2 { font-size: 15px; font-weight: 700; }
QLabel.muted { color: #a39fb5; }
QLabel.small { font-size: 12px; color: #a39fb5; }
QLabel#pill { background: #1c1a27; border-radius: 11px; padding: 4px 10px; color: #a39fb5; font-size: 12px; }
QLabel#pill[on="true"] { color: #8fe0b4; }
QPushButton { background: #262336; border: none; border-radius: 10px; padding: 8px 14px; }
QPushButton:hover { background: #302c44; }
QPushButton:disabled { color: #77738a; background: #201e2c; }
QPushButton.primary { background: #7c5cff; font-weight: 600; }
QPushButton.primary:hover { background: #9b83ff; }
QPushButton.primary:disabled { background: #4a3d8a; color: #c9c0ff; }
QPushButton.danger { background: #3a1f24; color: #ffb3b5; font-weight: 600; }
QPushButton.danger:disabled { background: #251a1d; color: #7d5a5c; }
QPushButton#mirror { font-size: 16px; padding: 16px 10px; border-radius: 12px; }
QPushButton#stop { font-size: 16px; padding: 16px 10px; border-radius: 12px; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { background: #262336; border: 2px solid transparent; border-radius: 10px; padding: 9px; margin: 3px 0; }
QListWidget::item:selected { border: 2px solid #7c5cff; background: #262336; }
QListWidget::item:hover { background: #2d293f; }
QLineEdit, QComboBox { background: #262336; border: 1px solid #34304a; border-radius: 8px; padding: 6px 8px; }
QComboBox QAbstractItemView { background: #262336; selection-background-color: #7c5cff; }
QProgressBar { background: #262336; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #7c5cff; border-radius: 3px; }
QSlider::groove:horizontal { height: 6px; background: #262336; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #7c5cff; border-radius: 3px; }
QSlider::handle:horizontal { background: #f2f0fa; width: 16px; margin: -5px 0; border-radius: 8px; }
QFrame#dropHint { background: rgba(124,92,255,.18); border: 2px dashed #7c5cff; border-radius: 14px; }
QLabel#qr { background: #ffffff; border-radius: 10px; padding: 6px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid #4a4566; background: #262336; }
QCheckBox::indicator:checked { background: #7c5cff; border-color: #7c5cff; }
"""


def fmt_time(s) -> str:
    try:
        s = max(0, int(float(s)))
    except (TypeError, ValueError):
        return "0:00"
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def cls(widget, name: str):
    widget.setProperty("class", name)
    return widget


class Bridge(QObject):
    """Meldungen aus Hintergrund-Threads in den GUI-Thread bringen."""
    notice = Signal(str, str)


class MainWindow(QWidget):
    def __init__(self, app: App, tray_available: bool = True):
        super().__init__()
        self.app = app
        self.tray_available = tray_available
        self.bridge = Bridge()
        self.bridge.notice.connect(self.show_notice)
        self.busy: set[str] = set()
        self.local_notice: str | None = None
        self._device_sig = None
        self._volume_drag = False
        self.status: dict = {}
        self.quit_requested = False

        self.setObjectName("root")
        self.setWindowTitle("LakuenCast")
        self.setWindowIcon(QIcon(str(ICON)))
        self.setStyleSheet(STYLE)
        self.resize(560, 860)
        self.setMinimumWidth(460)
        self._build()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    # ------------------------------------------------------------------ Aufbau
    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        f = QFrame()
        f.setProperty("class", "card")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        return f, lay

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        # Kopf
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ICON)).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(logo)
        tbox = QVBoxLayout()
        tbox.setSpacing(0)
        t = QLabel("LakuenCast")
        t.setObjectName("title")
        self.version = cls(QLabel("…"), "muted")
        tbox.addWidget(t)
        tbox.addWidget(self.version)
        head.addLayout(tbox, 1)
        self.pill = QLabel("Nicht verbunden")
        self.pill.setObjectName("pill")
        head.addWidget(self.pill, 0, Qt.AlignTop)
        root.addLayout(head)

        # Update-Hinweis
        self.update_box = QFrame()
        self.update_box.setObjectName("updateBanner")
        ul = QHBoxLayout(self.update_box)
        ul.setContentsMargins(12, 10, 12, 10)
        utext = QVBoxLayout()
        self.update_title = QLabel()
        self.update_title.setStyleSheet("font-weight:700")
        self.update_text = cls(QLabel(), "small")
        self.update_text.setWordWrap(True)
        self.update_bar = QProgressBar()
        self.update_bar.setTextVisible(False)
        self.update_bar.setRange(0, 1000)
        utext.addWidget(self.update_title)
        utext.addWidget(self.update_text)
        utext.addWidget(self.update_bar)
        ul.addLayout(utext, 1)
        self.update_btn = cls(QPushButton("Update"), "primary")
        self.update_btn.clicked.connect(self.on_update)
        ul.addWidget(self.update_btn)
        root.addWidget(self.update_box)

        # Hinweis / Fehler
        self.notice_box = QFrame()
        self.notice_box.setObjectName("noticeWarn")
        nl = QHBoxLayout(self.notice_box)
        nl.setContentsMargins(12, 10, 8, 10)
        self.notice_text = QLabel()
        self.notice_text.setWordWrap(True)
        nl.addWidget(self.notice_text, 1)
        nx = QPushButton("✕")
        nx.setFixedWidth(36)
        nx.clicked.connect(self.dismiss_notice)
        nl.addWidget(nx, 0, Qt.AlignTop)
        root.addWidget(self.notice_box)

        # Fernseher
        card, lay = self._card()
        row = QHBoxLayout()
        row.addWidget(cls(QLabel("Fernseher"), "h2"), 1)
        self.scan_btn = QPushButton("Suchen")
        self.scan_btn.clicked.connect(lambda: self.run("scan", self.app.scan))
        row.addWidget(self.scan_btn)
        lay.addLayout(row)
        self.devices = QListWidget()
        self.devices.setIconSize(QSize(22, 22))
        self.devices.setWordWrap(True)
        self.devices.setTextElideMode(Qt.ElideNone)
        self.devices.setUniformItemSizes(False)
        self.devices.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.devices.itemClicked.connect(self.on_device)
        lay.addWidget(self.devices)
        self.empty = cls(QLabel(), "muted")
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        mrow = QHBoxLayout()
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Nicht dabei? IP-Adresse, z. B. 192.168.178.40")
        self.manual.returnPressed.connect(self.on_add)
        mrow.addWidget(self.manual, 1)
        add = QPushButton("Hinzufügen")
        add.clicked.connect(self.on_add)
        mrow.addWidget(add)
        lay.addLayout(mrow)
        root.addWidget(card)

        # Läuft gerade
        self.now_card, nlay = self._card()
        r = QHBoxLayout()
        self.now_title = cls(QLabel("Läuft"), "h2")
        self.now_title.setWordWrap(True)
        r.addWidget(self.now_title, 1)
        self.now_state = QLabel()
        self.now_state.setObjectName("pill")
        r.addWidget(self.now_state, 0, Qt.AlignTop)
        nlay.addLayout(r)
        self.progress_row = QWidget()
        pr = QHBoxLayout(self.progress_row)
        pr.setContentsMargins(0, 0, 0, 0)
        self.t_cur = cls(QLabel("0:00"), "small")
        self.t_bar = QProgressBar()
        self.t_bar.setTextVisible(False)
        self.t_bar.setRange(0, 1000)
        self.t_dur = cls(QLabel("0:00"), "small")
        pr.addWidget(self.t_cur)
        pr.addWidget(self.t_bar, 1)
        pr.addWidget(self.t_dur)
        nlay.addWidget(self.progress_row)
        self.controls = QWidget()
        cr = QHBoxLayout(self.controls)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addStretch(1)
        back = QPushButton("−10 s")
        back.clicked.connect(lambda: self.run("seek", self.app.seek_relative, -10))
        self.playpause = cls(QPushButton("❚❚ Pause"), "primary")
        self.playpause.clicked.connect(self.on_playpause)
        fwd = QPushButton("+30 s")
        fwd.clicked.connect(lambda: self.run("seek", self.app.seek_relative, 30))
        nxt = QPushButton("⏭ Nächste Folge")
        nxt.setToolTip("Holt über LakuenWatcher die nächste Folge")
        nxt.clicked.connect(self.app.request_next)
        for b in (back, self.playpause, fwd, nxt):
            cr.addWidget(b)
        cr.addStretch(1)
        nlay.addWidget(self.controls)
        root.addWidget(self.now_card)

        # Aktionen
        card, lay = self._card()
        ar = QHBoxLayout()
        self.mirror_btn = cls(QPushButton("🖥  Bildschirm spiegeln  (mit Ton)"), "primary")
        self.mirror_btn.setObjectName("mirror")
        self.mirror_btn.clicked.connect(self.on_mirror)
        self.stop_btn = cls(QPushButton("■  Stopp"), "danger")
        self.stop_btn.setObjectName("stop")
        self.stop_btn.clicked.connect(lambda: self.run("stop", self.app.stop))
        ar.addWidget(self.mirror_btn, 2)
        ar.addWidget(self.stop_btn, 1)
        lay.addLayout(ar)
        q = QHBoxLayout()
        q.addWidget(QLabel("Qualität"), 1)
        self.quality = QComboBox()
        self.quality.addItem("720p (flüssig, empfohlen)", "720p")
        self.quality.addItem("1080p (schärfer, mehr Last)", "1080p")
        self.quality.activated.connect(self.on_quality)
        q.addWidget(self.quality)
        lay.addLayout(q)
        v = QHBoxLayout()
        v.addWidget(QLabel("Lautstärke Fernseher"), 1)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(230)
        self.volume.sliderPressed.connect(lambda: setattr(self, "_volume_drag", True))
        self.volume.sliderReleased.connect(self.on_volume)
        v.addWidget(self.volume)
        lay.addLayout(v)
        self.mute_pc = QCheckBox("PC-Ton beim Spiegeln stumm (Ton nur am Fernseher)")
        self.mute_pc.toggled.connect(lambda on: self.app.set_option(mute_pc=on))
        lay.addWidget(self.mute_pc)
        self.low_latency = QCheckBox("Geringe Verzögerung beim Spiegeln (experimentell)")
        self.low_latency.toggled.connect(lambda on: self.app.set_option(low_latency=on))
        lay.addWidget(self.low_latency)
        self.file_btn = QPushButton("🎬  Videodatei senden …   (oder hierher ziehen)")
        self.file_btn.clicked.connect(self.on_file)
        lay.addWidget(self.file_btn)
        root.addWidget(card)

        # Handy-Fernbedienung
        card, lay = self._card()
        lay.addWidget(cls(QLabel("📱 Handy als Fernbedienung"), "h2"))
        rr = QHBoxLayout()
        self.qr_label = QLabel()
        self.qr_label.setObjectName("qr")
        self.qr_label.setFixedSize(132, 132)
        self.qr_label.setAlignment(Qt.AlignCenter)
        rr.addWidget(self.qr_label)
        rtext = QVBoxLayout()
        rinfo = cls(QLabel("QR-Code mit der iPhone-Kamera scannen oder den Code in der Home-Bildschirm-App eintippen "
                           "(gleiches WLAN). Beide gelten nur einmal, eine Verbindung hält 5 Stunden."), "small")
        rinfo.setWordWrap(True)
        rtext.addWidget(rinfo)
        self.pin_label = QLabel()
        self.pin_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pin_label.setStyleSheet("font-size:24px;font-weight:800;letter-spacing:4px;color:#f2f0fa")
        self.pin_label.setToolTip("Auf dem Handy in der LakuenCast-App eintippen – gilt nur einmal")
        rtext.addWidget(self.pin_label)
        self.remote_url = QLabel()
        self.remote_url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.remote_url.setWordWrap(True)
        self.remote_url.setStyleSheet("color:#9b83ff;font-size:12px")
        rtext.addWidget(self.remote_url)
        self.input_ok = QCheckBox("Maus, Tastatur & Vorschau vom Handy erlauben")
        self.input_ok.setToolTip("Damit lässt sich der PC vom Handy aus komplett bedienen")
        self.input_ok.toggled.connect(lambda on: self.app.set_option(input_enabled=on))
        rtext.addWidget(self.input_ok)
        self.phones = cls(QLabel(), "small")
        rtext.addWidget(self.phones)
        prow = QHBoxLayout()
        newkey = QPushButton("Neuer QR-Code")
        newkey.setToolTip("Der angezeigte QR-Code wird ungültig")
        newkey.clicked.connect(self.on_newkey)
        prow.addWidget(newkey)
        self.kick = QPushButton("Alle Handys trennen")
        self.kick.clicked.connect(self.on_kick)
        prow.addWidget(self.kick)
        prow.addStretch(1)
        rtext.addLayout(prow)
        rtext.addStretch(1)
        rr.addLayout(rtext, 1)
        lay.addLayout(rr)
        root.addWidget(card)
        self._qr_for = None

        # Hinweis beim Hineinziehen
        self.setAcceptDrops(True)

        # Brave / Autostart
        card, lay = self._card()
        lay.addWidget(cls(QLabel("Videos aus Brave senden"), "h2"))
        info = cls(QLabel("In der Erweiterung <b>LakuenWatcher</b> auf „An Fernseher senden“ klicken. "
                          "Das Video läuft dann direkt auf dem Fernseher in voller Qualität, und nach jeder "
                          "Folge kommt automatisch die nächste."), "small")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.autostart = QCheckBox("Beim Hochfahren im Hintergrund starten")
        self.autostart.toggled.connect(self.on_autostart)
        lay.addWidget(self.autostart)
        root.addWidget(card)

        # Fuß
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.check_btn = QPushButton("Nach Update suchen")
        self.check_btn.setFlat(True)
        self.check_btn.setStyleSheet("background:transparent;color:#9b83ff;padding:2px")
        self.check_btn.clicked.connect(self.on_check)
        foot.addWidget(self.check_btn)
        foot.addStretch(1)
        root.addLayout(foot)
        root.addStretch(1)

    # ------------------------------------------------------------------ Hilfen
    def run(self, key: str, fn, *args):
        """App-Aufruf im Hintergrund, damit das Fenster nicht einfriert."""
        if key in self.busy:
            return
        self.busy.add(key)
        self.refresh()

        def work():
            try:
                fn(*args)
            except Exception as e:  # noqa: BLE001
                self.bridge.notice.emit(str(e), "error")
            finally:
                self.busy.discard(key)

        threading.Thread(target=work, daemon=True).start()

    def show_notice(self, text: str, kind: str = "warn"):
        self.local_notice = text or None
        self._set_notice(text, kind)

    def _set_notice(self, text: str | None, kind: str):
        if getattr(self, "_last_notice", None) == (text, kind):
            return
        self._last_notice = (text, kind)
        self.notice_box.setVisible(bool(text))
        self.notice_box.setObjectName("noticeError" if kind == "error" else "noticeWarn")
        self.notice_box.setStyleSheet(STYLE)
        self.notice_text.setText(text or "")

    def dismiss_notice(self):
        self.local_notice = None
        self.app.error = None
        self.app.message = None
        self._set_notice(None, "warn")

    # ------------------------------------------------------------------ Aktionen
    def on_device(self, item: QListWidgetItem):
        self.run("select", self.app.select, item.data(Qt.UserRole))

    def on_add(self):
        host = self.manual.text().strip()
        if not host:
            return

        def add():
            dev = self.app.add_manual(host)
            self.app.select(dev.id)

        self.manual.clear()
        self.run("add", add)

    def on_file(self):
        pattern = "Videos (" + " ".join(f"*{e}" for e in VIDEO_EXT) + ")"
        path, _ = QFileDialog.getOpenFileName(self, "Videodatei senden", str(Path.home()), pattern)
        if path:
            self.run("file", self.app.cast_file, path)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and urls[0].isLocalFile() and urls[0].toLocalFile().lower().endswith(VIDEO_EXT):
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        self.run("file", self.app.cast_file, path)

    def on_newkey(self):
        self.app.config.new_remote_key()
        self._qr_for = None
        self.refresh()

    def on_kick(self):
        self.app.disconnect_phones()
        self._qr_for = None
        self.refresh()

    def _render_qr(self, url: str):
        if url == self._qr_for:
            return
        self._qr_for = url
        m = qr.encode(url)
        n = len(m)
        img = QImage(n + 2, n + 2, QImage.Format_RGB32)
        img.fill(QColor("white"))
        black = QColor("black").rgb()
        for r, row in enumerate(m):
            for c, v in enumerate(row):
                if v:
                    img.setPixel(c + 1, r + 1, black)
        self.qr_label.setPixmap(QPixmap.fromImage(img).scaled(120, 120, Qt.KeepAspectRatio, Qt.FastTransformation))
        self.remote_url.setText(url)

    def on_mirror(self):
        self.run("mirror", self.app.start_mirror, self.quality.currentData())

    def on_quality(self):
        if self.status.get("mode") == "mirroring":
            self.on_mirror()
        else:
            self.app.quality = self.quality.currentData()

    def on_playpause(self):
        if (self.status.get("media") or {}).get("playerState") == "PAUSED":
            self.run("play", self.app.play)
        else:
            self.run("pause", self.app.pause)

    def on_volume(self):
        level = self.volume.value() / 100

        def setv():
            try:
                self.app.set_volume(level)
            finally:
                self._volume_drag = False

        self.run("volume", setv)

    def on_autostart(self, on: bool):
        if on != desktop.autostart_enabled():
            if not desktop.set_autostart(on):
                self.show_notice("Autostart geht nur, wenn LakuenCast als AppImage läuft.", "warn")

    def on_check(self):
        self.check_btn.setText("Suche …")

        def check():
            self.app.updater.check()
            self.check_btn.setText("Update gefunden" if self.app.updater.available else "Aktuell")

        self.run("check", check)

    def on_update(self):
        u = self.app.updater
        if not u.to_dict()["canInstall"]:
            import webbrowser
            webbrowser.open((u.available or {}).get("page") or "https://github.com/elkarbeenus/LakuenCast/releases")
            return

        def restart():
            self.app.stop()

        u.install_async(on_restart=restart)

    # ------------------------------------------------------------------ Anzeige
    def refresh(self):
        st = self.app.status()
        self.status = st
        self.render(st)

    def render(self, st: dict):
        u = st["update"]
        self.version.setText(u["installedDisplay"])

        sel = st.get("selected")
        self.pill.setText(("● " + sel["name"]) if st["connected"] and sel else (sel["name"] if sel else "Kein Gerät gewählt"))
        self.pill.setProperty("on", "true" if st["connected"] else "false")
        self.pill.style().unpolish(self.pill)
        self.pill.style().polish(self.pill)

        # Geräte
        sig = (tuple((d["id"], d["name"], d["host"], d["model"]) for d in st["devices"]), sel and sel["id"])
        if sig != self._device_sig:
            self._device_sig = sig
            self.devices.clear()
            for d in st["devices"]:
                sub = " · ".join(x for x in (d["model"], d["host"]) if x)
                item = QListWidgetItem(f"📺  {d['name']}\n      {sub}" if sub else f"📺  {d['name']}")
                item.setData(Qt.UserRole, d["id"])
                lines = 2 if sub else 1
                item.setSizeHint(QSize(0, 22 + 20 * lines))
                self.devices.addItem(item)
                if sel and sel["id"] == d["id"]:
                    item.setSelected(True)
            h = sum(self.devices.item(i).sizeHint().height() + 10 for i in range(self.devices.count()))
            self.devices.setFixedHeight(max(0, h + 6))
        self.devices.setVisible(bool(st["devices"]))
        scanning = st["scanning"] or "scan" in self.busy
        self.empty.setVisible(not st["devices"])
        self.empty.setText("Suche Chromecasts im Netzwerk …" if scanning
                           else "Kein Chromecast gefunden. Gleiches WLAN? Nochmal suchen.")
        self.scan_btn.setText("Suche …" if scanning else "Suchen")
        self.scan_btn.setEnabled(not scanning)

        # Läuft
        mode = st["mode"]
        active = mode != "idle" or "mirror" in self.busy
        self.now_card.setVisible(active)
        media = st.get("media") or {}
        ps = media.get("playerState")
        if active:
            state = {"starting": "Startet …", "mirroring": "Spiegeln", "casting": "Video"}.get(mode, "Startet …")
            if ps == "PAUSED":
                state = "Pausiert"
            elif ps == "BUFFERING":
                state = "Lädt …"
            self.now_state.setText(state)
            self.now_title.setText("Verbinde mit dem Fernseher …" if mode in ("starting", "idle") else (st["title"] or "Läuft"))
            is_video = mode == "casting"
            dur = media.get("duration")
            self.progress_row.setVisible(bool(is_video and dur))
            self.controls.setVisible(is_video)
            if is_video and dur:
                cur = float(media.get("currentTime") or 0)
                self.t_cur.setText(fmt_time(cur))
                self.t_dur.setText(fmt_time(dur))
                self.t_bar.setValue(int(1000 * min(1.0, cur / float(dur))))
            self.playpause.setText("▶ Weiter" if ps == "PAUSED" else "❚❚ Pause")

        starting = "mirror" in self.busy or mode == "starting"
        self.mirror_btn.setText("Startet …" if starting else "🖥  Bildschirm spiegeln  (mit Ton)")
        self.mirror_btn.setEnabled(not starting and mode != "mirroring")
        self.stop_btn.setEnabled(mode != "idle")
        if not self.quality.view().isVisible():
            i = self.quality.findData(st["quality"])
            if i >= 0:
                self.quality.setCurrentIndex(i)
        vol = st.get("volume") or {}
        if not self._volume_drag and isinstance(vol.get("level"), (int, float)):
            self.volume.blockSignals(True)
            self.volume.setValue(round(vol["level"] * 100))
            self.volume.blockSignals(False)
        self.volume.setEnabled(bool(sel))
        self.autostart.blockSignals(True)
        self.autostart.setChecked(st["autostart"])
        self.autostart.blockSignals(False)
        opts = st.get("options") or {}
        for box, key in ((self.mute_pc, "mute_pc"), (self.low_latency, "low_latency"), (self.input_ok, "input_enabled")):
            box.blockSignals(True)
            box.setChecked(bool(opts.get(key)))
            box.blockSignals(False)
        self.file_btn.setEnabled("file" not in self.busy and mode != "starting")
        self.file_btn.setText("Bereite Datei vor …" if "file" in self.busy else "🎬  Videodatei senden …   (oder hierher ziehen)")
        pin = str(self.app.config["pair_pin"] or "")
        self.pin_label.setText(f"Code: {pin[:3]} {pin[3:]}")
        try:
            self._render_qr(self.app.remote_url())  # QR-Code wechselt nach jedem Scan
        except Exception:  # noqa: BLE001
            pass
        ph = st.get("phones") or {}
        if ph.get("count"):
            import time as _t
            until = _t.strftime("%H:%M", _t.localtime(ph["until"]))
            self.phones.setText(f"📱 {ph['count']} Handy{'s' if ph['count'] > 1 else ''} verbunden – bis {until}")
        else:
            self.phones.setText("Kein Handy verbunden")
        self.kick.setEnabled(bool(ph.get("count")))

        # Hinweise
        if st.get("error"):
            self._set_notice(st["error"], "error")
        elif st.get("message"):
            self._set_notice(st["message"], "warn")
        elif self.local_notice:
            pass
        else:
            self._set_notice(None, "warn")

        # Update
        avail = u.get("available")
        self.update_box.setVisible(bool(avail))
        if avail:
            self.update_title.setText(f"Update {avail['display']} verfügbar")
            self.update_bar.setVisible(u["state"] == "downloading")
            self.update_bar.setValue(int(u["progress"] * 1000))
            if not u["canInstall"]:
                self.update_text.setText("Nicht als AppImage gestartet – neue Version bitte bei GitHub laden.")
                self.update_btn.setText("GitHub")
                self.update_btn.setEnabled(True)
            elif u["state"] in ("downloading", "installing", "restarting"):
                self.update_text.setText({"downloading": "Lade herunter …", "installing": "Installiere …",
                                          "restarting": "Starte neu …"}[u["state"]])
                self.update_btn.setEnabled(False)
            else:
                self.update_text.setText(f"Fehler: {u['error']}" if u.get("error") else
                                         "Lädt die neue Version, ersetzt das AppImage und startet LakuenCast neu.")
                self.update_btn.setText("Update")
                self.update_btn.setEnabled(True)

    # ------------------------------------------------------------------ Fenster
    def closeEvent(self, event):
        if self.tray_available and not self.quit_requested:
            event.ignore()
            self.hide()
        else:
            event.accept()


class Tray(QSystemTrayIcon):
    def __init__(self, window: MainWindow, qapp: QApplication):
        super().__init__(QIcon(str(ICON)))
        self.window = window
        self.qapp = qapp
        self.setToolTip("LakuenCast")
        menu = QMenu()
        self.a_show = QAction("Fenster öffnen", menu)
        self.a_show.triggered.connect(self.show_window)
        self.a_mirror = QAction("Bildschirm spiegeln", menu)
        self.a_mirror.triggered.connect(window.on_mirror)
        self.a_stop = QAction("Stopp", menu)
        self.a_stop.triggered.connect(lambda: window.run("stop", window.app.stop))
        a_quit = QAction("Beenden", menu)
        a_quit.triggered.connect(self.quit)
        for a in (self.a_show, self.a_mirror, self.a_stop):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(a_quit)
        menu.aboutToShow.connect(self.update_menu)
        self.setContextMenu(menu)
        self._menu = menu
        self.activated.connect(self.on_activated)

    def update_menu(self):
        mode = self.window.status.get("mode", "idle")
        self.a_mirror.setEnabled(mode != "mirroring")
        self.a_stop.setEnabled(mode != "idle")

    def on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.window.isVisible() and not self.window.isMinimized():
                self.window.hide()
            else:
                self.show_window()

    def show_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self):
        self.window.quit_requested = True
        self.window.app.stop()
        self.qapp.quit()


def run_gui(app: App, start_hidden: bool = False, screenshot: str | None = None) -> int:
    QApplication.setApplicationName("LakuenCast")
    QApplication.setDesktopFileName("lakuencast")
    qapp = QApplication.instance() or QApplication(sys.argv)
    qapp.setWindowIcon(QIcon(str(ICON)))
    tray_ok = QSystemTrayIcon.isSystemTrayAvailable() and not screenshot
    win = MainWindow(app, tray_available=tray_ok)
    qapp.setQuitOnLastWindowClosed(not tray_ok)

    if screenshot:
        # Nur für Tests: Fenster rendern, als Bild speichern, beenden
        win.show()
        QTimer.singleShot(1500, lambda: (win.refresh(), win.grab().save(screenshot), qapp.quit()))
        return qapp.exec()

    tray = None
    if tray_ok:
        tray = Tray(win, qapp)
        tray.show()
    if not start_hidden or not tray_ok:
        win.show()

    # Ein zweiter Start von LakuenCast bittet über /api/show, das Fenster zu zeigen
    def check_show_request():
        if app.show_requested:
            app.show_requested = False
            if tray:
                tray.show_window()
            else:
                win.showNormal()
                win.raise_()
                win.activateWindow()

    t = QTimer()
    t.timeout.connect(check_show_request)
    t.start(500)
    return qapp.exec()


def available() -> bool:
    return not os.environ.get("LAKUENCAST_NO_GUI")
