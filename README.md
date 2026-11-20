# LakuenCast

Bild **und Ton** vom PC auf den Chromecast übertragen, auch auf ältere Modelle. Gebaut für SteamOS im Desktop-Modus, läuft auf jedem Linux-Desktop mit X11.

- **Bildschirm spiegeln mit Ton.** Brave überträgt beim Bildschirm-Streamen unter Linux keinen Ton, LakuenCast schon.
- **Videos direkt an den Fernseher senden.** Mit der Browser-Erweiterung [LakuenWatcher](https://github.com/elkarbeenus/lakuenwatcher) läuft das Video in voller Qualität auf dem Chromecast. Nach jeder Folge kommt automatisch die nächste.
- Findet Chromecasts im Heimnetz automatisch. Notfalls trägst du die IP-Adresse von Hand ein.
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

## Bekannte Grenzen

- **Wayland:** Das Bild bleibt schwarz. Der SteamOS-Desktop-Modus nutzt X11, dort klappt es.
- **Ton auf PC und Fernseher gleichzeitig:** Beim Spiegeln spielt der PC den Ton weiter. Dreh die PC-Lautsprecher einfach leiser.
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
lakuencast/server.py    HTTP-Server (Oberfläche, API, Stream)
lakuencast/updater.py   Update aus dem Programm
lakuencast/gui.py       eigenes Fenster + Taskleisten-Symbol (Qt)
lakuencast/web/         Browser-Oberfläche (Ersatz ohne Qt)
packaging/              AppImage-Bau
```

**Release:** `VERSION` in `lakuencast/version.py` erhöhen und auf `main` pushen. GitHub Actions übernimmt dann Tests, AppImage-Bau, Probelauf und Release. Eine Version wie `0.2.0-test.1` ergibt einen Testbuild.
