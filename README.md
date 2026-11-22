# LakuenCast

**English** · [Deutsch](#deutsch)

Cast your PC screen **with audio** — and videos from the browser — to a Chromecast, including older models. Built for SteamOS desktop mode; works on any Linux desktop running X11. Comes with a phone remote that doubles as a touchpad and keyboard.

## Features

- **Mirror the screen with audio**: browsers on Linux mirror without sound; LakuenCast includes it
- **Send videos from the browser** with [LakuenWatcher](https://github.com/elkarbeenus/lakuenwatcher): plays in full quality on the TV, continues at the current position and advances to the next episode automatically
- **Send local video files** by choosing or dragging them into the window; compatible MP4 files play directly, other formats are converted on the fly
- **Phone remote** in the browser: play/pause, seek (draggable timeline), volume, next episode, mirroring, sleep and shutdown of the PC
- **Mouse and keyboard from the phone**: touchpad with gestures, on-screen keyboard (including umlauts and special characters), special keys and an optional live preview
- **Mute the PC while mirroring** so audio only plays on the TV
- **Low-latency mode** (experimental): about 1–2 seconds instead of 3–6
- Automatic Chromecast discovery, manual IP entry as fallback, remembers the last device
- Native window with tray icon, optional autostart in the background
- **One-click updates** from within the application

## Installation

1. Download `LakuenCast-x86_64.AppImage` from [Releases](https://github.com/elkarbeenus/LakuenCast/releases/latest).
2. Make it executable (file manager: *Properties → Permissions → Executable*, or `chmod +x LakuenCast-x86_64.AppImage`).
3. Start it. LakuenCast opens its window and adds itself to the application menu.

Keep the AppImage in a permanent location (for example `~/Applications/`); menu entry and autostart point to that path. FFmpeg and Qt are bundled; nothing else needs to be installed.

## Phone remote

The LakuenCast window shows a **QR code** and a **6-digit code** under *Phone remote*.

- **Scan the QR code** with the phone camera, or
- **Open `http://<PC-IP>:47800/remote`** on the phone and enter the 6-digit code.

The phone must be on the same network. To use the remote like an app, add the page to the home screen (Safari or Chrome: *Share → Add to Home Screen*).

### Mouse and keyboard

Enable **Allow mouse, keyboard & preview from the phone** in the LakuenCast window (off by default). The remote then offers a second tab:

| Gesture | Action |
|---|---|
| Swipe with one finger | Move the pointer (with acceleration; speed adjustable) |
| Tap | Left click |
| Two-finger tap | Right click |
| Two-finger swipe | Scroll |
| Double tap and hold | Drag |

Text typed on the phone is entered on the PC. Special keys (Enter, Backspace, Esc, Tab, arrows, F11, media keys) are available as buttons. The live preview has a latency of roughly 0.2 seconds.

### Security

- The QR code and the 6-digit code are **single-use**; after pairing, new codes are generated immediately.
- Each phone connection is valid for **5 hours** and then ends automatically. It survives restarts of the phone and of LakuenCast; only checksums of the session tokens are stored.
- Five wrong code entries trigger a new code and a 30-second pause.
- **Disconnect all phones** in the window ends every connection immediately.
- Control endpoints accept requests only from the PC itself or from a paired phone; the Chromecast can only fetch the stream that is currently playing.

## How it works

| | |
|---|---|
| Mirroring | FFmpeg captures the X11 screen and the default audio output (PipeWire/PulseAudio) and serves an HLS stream (H.264/AAC) that any Chromecast can play. |
| Browser videos | LakuenWatcher passes the video URL, referrer and cookies to LakuenCast, which fetches the stream and relays it to the Chromecast, rewriting HLS playlists as needed. |
| Next episode | When an episode finishes on the TV, LakuenCast notifies LakuenWatcher, which loads the next one and sends it. |
| Mouse/keyboard | Input is sent over a WebSocket and injected through X11/XTest. |

LakuenCast listens on port **47800**.

## Known limitations

- **Wayland**: screen capture and mouse control require an X11 session (SteamOS desktop mode uses X11).
- **Low-latency mode** may stutter on some Chromecast models; switch it off if it does.
- **Converted files** (for example MKV with HEVC) can only be seeked within the part already converted.
- Some video hosts cannot be relayed; screen mirroring works in that case.
- A camera scanner inside the remote page is not possible because iOS only allows camera access on HTTPS pages.

## Development

Python 3 with a Qt (PySide6) interface. Without PySide6 the interface opens in the browser.

```bash
python3 -m lakuencast                       # run from source
python3 -m unittest discover -s tests -v    # tests
```

```
lakuencast/castv2.py     Cast protocol v2 (TLS + Protobuf, no dependencies)
lakuencast/mdns.py       Chromecast discovery (mDNS, avahi fallback)
lakuencast/mirror.py     Screen and audio capture (FFmpeg)
lakuencast/pcaudio.py    Redirect PC audio while mirroring
lakuencast/proxy.py      Video relay and playlist rewriting
lakuencast/filecast.py   Local video files (direct or converted)
lakuencast/xinput.py     Mouse and keyboard (X11/XTest via ctypes)
lakuencast/ws.py         WebSocket for low-latency phone input
lakuencast/qr.py         QR code generator
lakuencast/server.py     HTTP server (UI, API, streams, pairing)
lakuencast/updater.py    In-app updates
lakuencast/gui.py        Native window and tray icon (Qt)
lakuencast/web/          Phone remote and browser UI
packaging/               AppImage build
```

**Releases:** increase `VERSION` in `lakuencast/version.py` and push to `main`. GitHub Actions runs the tests, builds and smoke-tests the AppImage and publishes the release. A version such as `0.4.0-test.1` produces a test build.

---

<a id="deutsch"></a>

# LakuenCast (Deutsch)

[English](#lakuencast) · **Deutsch**

LakuenCast überträgt den PC-Bildschirm **mit Ton** und Videos aus dem Browser auf einen Chromecast, auch auf ältere Modelle. Entwickelt für den Desktop-Modus von SteamOS, lauffähig auf jedem Linux-Desktop mit X11. Dazu gehört eine Handy-Fernbedienung, die auch als Touchpad und Tastatur dient.

## Funktionen

- **Bildschirm mit Ton spiegeln**: Browser spiegeln unter Linux ohne Ton, LakuenCast überträgt ihn mit.
- **Videos aus dem Browser senden** mit [LakuenWatcher](https://github.com/elkarbeenus/lakuenwatcher): Wiedergabe in voller Qualität auf dem Fernseher. Sie setzt an der aktuellen Stelle fort und wechselt automatisch zur nächsten Folge.
- **Eigene Videodateien senden** per Auswahl oder durch Hineinziehen ins Fenster. Passende MP4-Dateien laufen direkt, andere Formate werden beim Abspielen umgewandelt.
- **Handy-Fernbedienung** im Browser: Wiedergabe/Pause, Spulen (ziehbare Zeitleiste), Lautstärke, nächste Folge, Spiegeln, Energiesparmodus und Herunterfahren des PCs.
- **Maus und Tastatur vom Handy**: Touchpad mit Gesten, Bildschirmtastatur (auch Umlaute und Sonderzeichen), Sondertasten und eine zuschaltbare Live-Vorschau.
- **PC beim Spiegeln stumm**, der Ton kommt nur aus dem Fernseher.
- **Modus mit geringer Verzögerung** (experimentell): etwa 1–2 Sekunden statt 3–6.
- Findet Chromecasts automatisch, als Ausweg auch per IP-Eingabe. Merkt sich das zuletzt genutzte Gerät.
- Eigenes Fenster mit Taskleisten-Symbol, auf Wunsch Autostart im Hintergrund.
- **Update mit einem Klick** direkt im Programm.

## Installation

1. `LakuenCast-x86_64.AppImage` unter [Releases](https://github.com/elkarbeenus/LakuenCast/releases/latest) herunterladen.
2. Ausführbar machen (Dateimanager: *Eigenschaften → Berechtigungen → Ausführbar* oder `chmod +x LakuenCast-x86_64.AppImage`).
3. Starten. LakuenCast öffnet sein Fenster und trägt sich ins Anwendungsmenü ein.

Das AppImage an einem festen Ort ablegen, z. B. in `~/Anwendungen/`. Menüeintrag und Autostart zeigen auf diesen Pfad. FFmpeg und Qt sind enthalten, sonst muss nichts installiert werden.

## Handy-Fernbedienung

Im LakuenCast-Fenster stehen unter *Handy als Fernbedienung* ein **QR-Code** und ein **6-stelliger Code**:

- den **QR-Code** mit der Handy-Kamera scannen, oder
- auf dem Handy **`http://<PC-IP>:47800/remote`** öffnen und den 6-stelligen Code eingeben.

Das Handy muss im selben Netzwerk sein. Damit die Fernbedienung wie eine App funktioniert, die Seite zum Home-Bildschirm hinzufügen (Safari und Chrome: *Teilen → Zum Home-Bildschirm*).

### Maus und Tastatur

Im LakuenCast-Fenster **Maus, Tastatur & Vorschau vom Handy erlauben** einschalten (standardmäßig aus). Die Fernbedienung zeigt dann einen zweiten Reiter:

| Geste | Wirkung |
|---|---|
| Mit einem Finger wischen | Mauszeiger bewegen (mit Beschleunigung, Tempo einstellbar) |
| Tippen | Linksklick |
| Mit zwei Fingern tippen | Rechtsklick |
| Mit zwei Fingern wischen | Scrollen |
| Doppelt tippen und halten | Ziehen |

Was auf dem Handy getippt wird, landet am PC. Sondertasten wie Enter, Löschen, Esc, Tab, Pfeiltasten, F11 und Medientasten gibt es als Schaltflächen. Die Live-Vorschau hat eine Verzögerung von etwa 0,2 Sekunden.

### Sicherheit

- QR-Code und 6-stelliger Code gelten **nur einmal**. Nach dem Verbinden werden sofort neue Codes erzeugt.
- Jede Handy-Verbindung gilt **5 Stunden** und endet dann automatisch. Sie übersteht Neustarts von Handy und LakuenCast. Gespeichert werden nur Prüfsummen der Verbindungsschlüssel.
- Nach fünf falschen Code-Eingaben wird ein neuer Code erzeugt, und es gibt 30 Sekunden Pause.
- **Alle Handys trennen** im Fenster beendet sofort alle Verbindungen.
- Steuerbefehle werden nur vom PC selbst oder von einem verbundenen Handy angenommen. Der Chromecast kann nur den Stream abrufen, der gerade läuft.

## Funktionsweise

| | |
|---|---|
| Spiegeln | FFmpeg nimmt den X11-Bildschirm und die Standard-Audioausgabe (PipeWire/PulseAudio) auf und stellt daraus einen HLS-Stream (H.264/AAC) bereit, den jeder Chromecast abspielen kann. |
| Browser-Videos | LakuenWatcher übergibt Video-Adresse, Referer und Cookies an LakuenCast. LakuenCast holt den Stream ab und reicht ihn an den Chromecast weiter, HLS-Playlists werden dabei bei Bedarf umgeschrieben. |
| Nächste Folge | Endet eine Folge auf dem Fernseher, meldet LakuenCast das an LakuenWatcher. LakuenWatcher lädt dann die nächste Folge und sendet sie. |
| Maus/Tastatur | Eingaben laufen über einen WebSocket und werden per X11/XTest ausgelöst. |

LakuenCast nutzt den Port **47800**.

## Bekannte Einschränkungen

- **Wayland**: Bildschirmaufnahme und Maussteuerung brauchen eine X11-Sitzung (der SteamOS-Desktop-Modus nutzt X11).
- **Geringe Verzögerung** kann auf manchen Chromecast-Modellen ruckeln. In dem Fall ausschalten.
- **Umgewandelte Dateien** (z. B. MKV mit HEVC) lassen sich nur im schon umgewandelten Teil spulen.
- Manche Video-Hoster lassen sich nicht durchreichen. Dann funktioniert das Spiegeln.
- Ein Kamera-Scanner direkt auf der Fernbedienungsseite ist nicht möglich, weil iOS den Kamerazugriff nur auf HTTPS-Seiten erlaubt.

## Entwicklung

Aufbau des Projekts und Tests: siehe Abschnitt [Development](#development).

**Releases:** `VERSION` in `lakuencast/version.py` erhöhen und auf `main` pushen. GitHub Actions führt die Tests aus, baut und prüft das AppImage und veröffentlicht das Release. Eine Version wie `0.4.0-test.1` ergibt einen Testbuild.
