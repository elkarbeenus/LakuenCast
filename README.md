# LakuenCast

Bild **und Ton** vom PC auf den Chromecast übertragen, auch auf ältere Modelle. Gebaut für SteamOS im Desktop-Modus, läuft auf jedem Linux-Desktop mit X11.

- **Bildschirm spiegeln mit Ton.** Brave überträgt beim Bildschirm-Streamen unter Linux keinen Ton, LakuenCast schon.
- **Videos direkt an den Fernseher senden.** Mit der Browser-Erweiterung [LakuenWatcher](https://github.com/elkarbeenus/lakuenwatcher) läuft das Video in voller Qualität auf dem Chromecast. Nach jeder Folge kommt automatisch die nächste.
- Findet Chromecasts im Heimnetz automatisch. Notfalls trägst du die IP-Adresse von Hand ein.
- **Handy als Fernbedienung**: Den QR-Code in LakuenCast mit dem iPhone scannen. Dann gehen Pause, Spulen, Lautstärke, Spiegeln und „Nächste Folge“ von der Couch aus.
- **Maus & Tastatur vom Handy**: Touchpad mit Gesten, Tastatur (auch Umlaute und Sonderzeichen), Sondertasten und eine Live-Vorschau des PC-Bildschirms, auf die man direkt tippen kann
- **PC-Ton beim Spiegeln stumm**: Der Ton kommt nur aus dem Fernseher.
- **Eigene Videodateien senden**: Datei wählen oder ins Fenster ziehen. Passende MP4-Dateien laufen direkt, alles andere wird beim Abspielen umgewandelt.
- **Geringe Verzögerung** beim Spiegeln (experimentell): etwa 1–2 s statt 3–6 s
- Lautstärke, Pause und Spulen direkt in LakuenCast
- Auf Wunsch Autostart beim Hochfahren, im Hintergrund
- **Update mit einem Klick** im Programm

## Installation

1. Unter [Releases](https://github.com/elkarbeenus/LakuenCast/releases/latest) die Datei `LakuenCast-x86_64.AppImage` herunterladen.
2. In Dolphin: Rechtsklick → **Eigenschaften** → **Berechtigungen** → **Ausführbar** anhaken.
3. Doppelklick. LakuenCast öffnet sich als eigenes Fenster mit Symbol in der Taskleiste und erscheint danach auch im Startmenü. Schließt du das Fenster, läuft LakuenCast in der Taskleiste weiter; „Beenden“ gibt es per Rechtsklick auf das Symbol.

Tipp: Lege das AppImage an einen festen Ort, z. B. `~/Programme/`. Startmenü und Autostart zeigen auf diesen Pfad.

## So funktioniert es

| | |
|---|---|
| **Bildschirm spiegeln** | ffmpeg nimmt den Bildschirm (X11) und den Ton der Standard-Audioausgabe (PipeWire/PulseAudio) auf. Daraus entsteht ein HLS-Livestream mit H.264 und AAC, den der Chromecast abruft. Das Bild kommt etwa 3 bis 6 Sekunden später an. |
| **Video senden** | LakuenWatcher gibt die Videoadresse samt Referer und Cookies an LakuenCast weiter. LakuenCast holt das Video beim Hoster ab und reicht es an den Chromecast durch, auch HLS mit allen Stücken. |
| **Auto-Next** | Ist die Folge auf dem Fernseher zu Ende, meldet LakuenCast das an LakuenWatcher. Die Erweiterung holt dann die nächste Folge und schickt sie hinterher. |

LakuenCast nutzt Port **47800**. Steuern lässt es sich nur vom eigenen PC aus. Der Chromecast darf nur den Livestream und die Videos abrufen, die gerade laufen.

## Handy-Fernbedienung

LakuenCast zeigt einen QR-Code und eine Adresse wie `http://192.168.178.20:47800/remote?k=…`. Scanne ihn mit der iPhone-Kamera, das Handy muss im selben WLAN sein. Im Browser gehst du dann auf Teilen → **Zum Home-Bildschirm**, so liegt die Fernbedienung wie eine App auf dem Handy.

**Als App auf dem iPhone:** Öffne `http://<PC-IP>:47800/remote` in Safari → Teilen → **Zum Home-Bildschirm**. Ist keine Verbindung da, zeigt die App ein Ziffernfeld: Tippe den 6-stelligen Code aus dem LakuenCast-Fenster ein, fertig. Ein eigener Kamera-Scanner in der Seite ist nicht möglich, weil iPhones die Kamera nur auf `https://`-Seiten freigeben.

**Sicherheit:** QR-Code und 6-stelliger Code gelten nur einmal. Beim Scannen bekommt das Handy eine eigene Verbindung, und LakuenCast zeigt sofort einen neuen Code. Jede Verbindung gilt **5 Stunden** und wird danach automatisch getrennt. Mit „Alle Handys trennen“ geht das sofort. Ein Lesezeichen auf dem Home-Bildschirm hält deshalb auch nur 5 Stunden, danach scannst du einfach neu.

„Nächste Folge“ funktioniert, wenn das Video mit LakuenWatcher an den Fernseher gesendet wurde.

### Maus & Tastatur

Den zweiten Reiter „Maus & Tastatur“ schaltest du in LakuenCast mit **„Maus, Tastatur & Vorschau vom Handy erlauben“** frei. Standardmäßig ist er aus.

| Geste | Wirkung |
|---|---|
| 1 Finger wischen | Maus bewegen (mit Beschleunigung, Tempo einstellbar) |
| Tippen | Linksklick |
| 2 Finger tippen | Rechtsklick |
| 2 Finger wischen | Scrollen |
| 2× tippen und halten | Ziehen (z. B. Fenster verschieben) |

Die Live-Vorschau am Handy lässt sich zuschalten. Sie kommt mit etwa 0,2 s Verzögerung, also viel schneller als das Bild am Fernseher. Die Steuerung läuft über eine WebSocket-Verbindung. Die Eingaben macht LakuenCast über X11/XTest, das ist im SteamOS-Desktop-Modus schon dabei.

## Bekannte Grenzen

- **Wayland:** Das Bild bleibt schwarz. Der SteamOS-Desktop-Modus nutzt X11, dort klappt es.
- **Geringe Verzögerung:** Das ist ein Versuch. Ruckelt es auf deinem Chromecast, schalte es wieder aus.
- **Umgewandelte Dateien:** Bei Dateien, die umgewandelt werden müssen (z. B. MKV mit HEVC), geht Spulen nur im schon umgewandelten Teil.
- **Hoster:** Einzelne Hoster lassen sich nicht durchreichen. Dann hilft „Bildschirm spiegeln“.

## Entwicklung

Python 3, Oberfläche mit Qt (PySide6). Ohne PySide6 öffnet sich die Oberfläche im Browser. ffmpeg und Qt stecken im AppImage.

```bash
python3 -m lakuencast            # starten
python3 -m unittest discover -s tests -v
```

```
lakuencast/castv2.py    Chromecast-Protokoll (TLS + Protobuf, von Hand)
lakuencast/mdns.py      Chromecasts im Netzwerk finden
lakuencast/mirror.py    Bildschirm + Ton → HLS (ffmpeg)
lakuencast/proxy.py     Video-Durchreiche, Playlists umschreiben
lakuencast/filecast.py  eigene Videodateien (direkt oder umwandeln)
lakuencast/pcaudio.py   PC-Ton beim Spiegeln umleiten
lakuencast/qr.py        QR-Code für die Handy-Fernbedienung
lakuencast/xinput.py    Maus & Tastatur (X11/XTest per ctypes)
lakuencast/ws.py        WebSocket für flüssige Handy-Eingaben
lakuencast/web/remote.html  Handy-Fernbedienung
lakuencast/server.py    HTTP-Server (Oberfläche, API, Stream)
lakuencast/updater.py   Update aus dem Programm
lakuencast/gui.py       eigenes Fenster + Taskleisten-Symbol (Qt)
lakuencast/web/         Browser-Oberfläche (Ersatz ohne Qt)
packaging/              AppImage-Bau
```

**Release:** `VERSION` in `lakuencast/version.py` erhöhen und auf `main` pushen. GitHub Actions übernimmt dann Tests, AppImage-Bau, Probelauf und Release. Eine Version wie `0.2.0-test.1` ergibt einen Testbuild.
