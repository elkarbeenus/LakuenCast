#!/usr/bin/env bash
# Baut LakuenCast-x86_64.AppImage (läuft in GitHub Actions).
# Bestandteile: Python (python-appimage), LakuenCast, ffmpeg (statisch, BtbN).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-$ROOT/build}"
OUT="${OUT:-$ROOT/dist}"
PY_URL="${PY_URL:-https://github.com/niess/python-appimage/releases/download/python3.12/python3.12.14-cp312-cp312-manylinux2014_x86_64.AppImage}"
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
export APPIMAGE_EXTRACT_AND_RUN=1

rm -rf "$WORK" && mkdir -p "$WORK" "$OUT"
cd "$WORK"

echo "== Python: $PY_URL"
curl -fsSL -o python.AppImage "$PY_URL"
chmod +x python.AppImage
./python.AppImage --appimage-extract >/dev/null
APPDIR="$WORK/squashfs-root"
chmod -R u+rwX "$APPDIR"
# Desktop-/Metadaten des Python-AppImages entfernen (sonst Konflikt beim Bauen)
rm -rf "$APPDIR/usr/share/applications" "$APPDIR/usr/share/metainfo" "$APPDIR/usr/share/icons"

# Eigenes AppRun, das Python-AppRun bleibt als AppRun.python
mv "$APPDIR/AppRun" "$APPDIR/AppRun.python"
rm -f "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR"/*.svg "$APPDIR/.DirIcon"
install -m 755 "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
cp "$ROOT/packaging/lakuencast.desktop" "$APPDIR/lakuencast.desktop"
cp "$ROOT/packaging/lakuencast.png" "$APPDIR/lakuencast.png"
ln -sf lakuencast.png "$APPDIR/.DirIcon"

echo "== LakuenCast"
mkdir -p "$APPDIR/opt/lakuencast"
cp -r "$ROOT/lakuencast" "$APPDIR/opt/lakuencast/"
cp "$ROOT/packaging/run.py" "$APPDIR/opt/lakuencast/run.py"
find "$APPDIR/opt/lakuencast" -name "__pycache__" -prune -exec rm -rf {} +

echo "== Qt (PySide6) für das eigene Fenster"
"$APPDIR/AppRun.python" -m pip install --disable-pip-version-check --no-cache-dir --quiet \
  --target "$APPDIR/opt/lakuencast/vendor" "PySide6-Essentials==${PYSIDE_VERSION:-6.8.1}"
QT="$APPDIR/opt/lakuencast/vendor/PySide6"
# Nicht benötigte Teile entfernen (Qml/Quick, Designer, Werkzeuge) – spart viel Platz
rm -rf "$QT/Qt/qml" "$QT/include" "$QT/typesystems" "$QT/glue" "$QT/scripts" "$QT"/designer* \
       "$QT"/assistant* "$QT"/linguist* "$QT"/lupdate* "$QT"/lrelease* "$QT"/qmlls* "$QT"/qmllint* \
       "$QT"/qmlformat* "$QT"/balsam* "$QT"/svgtoqml* "$QT/Qt/libexec" "$QT"/Qt/translations/qtdeclarative_*
find "$QT/Qt/lib" -maxdepth 1 \( -name "libQt6Quick*" -o -name "libQt6Qml*" -o -name "libQt6Designer*" \
     -o -name "libQt6Pdf*" -o -name "libQt6ShaderTools*" -o -name "libQt6Test*" \) -delete
find "$QT" -maxdepth 1 \( -name "QtQuick*" -o -name "QtQml*" -o -name "QtDesigner*" -o -name "QtTest*" \
     -o -name "QtPdf*" \) -delete
du -sh "$APPDIR/opt/lakuencast/vendor"
QT_QPA_PLATFORM=offscreen PYTHONPATH="$APPDIR/opt/lakuencast/vendor" "$APPDIR/AppRun.python" -c \
  "from PySide6.QtWidgets import QApplication, QSystemTrayIcon; a=QApplication([]); print('Qt ok')"

echo "== ffmpeg"
curl -fsSL -o ffmpeg.tar.xz "$FFMPEG_URL"
tar -xJf ffmpeg.tar.xz
mkdir -p "$APPDIR/usr/bin"
install -m 755 ffmpeg-*/bin/ffmpeg "$APPDIR/usr/bin/ffmpeg"
DEVICES="$("$APPDIR/usr/bin/ffmpeg" -hide_banner -devices 2>/dev/null || true)"
echo "$DEVICES" | grep -E "x11grab|pulse" || true
if ! echo "$DEVICES" | grep -q x11grab; then
  echo "::error::Das gebündelte ffmpeg kann kein x11grab"; exit 1
fi

echo "== AppImage bauen"
curl -fsSL -o appimagetool "$TOOL_URL"
chmod +x appimagetool
if ! ARCH=x86_64 ./appimagetool --no-appstream "$APPDIR" "$OUT/LakuenCast-x86_64.AppImage" > tool.log 2>&1; then
  tail -40 tool.log
  MSG="$(tail -25 tool.log | sed 's/%/%25/g' | sed ':a;N;$!ba;s/\n/%0A/g')"
  echo "::error title=appimagetool::$MSG"
  exit 1
fi
tail -5 tool.log
(cd "$OUT" && sha256sum LakuenCast-x86_64.AppImage > LakuenCast-x86_64.AppImage.sha256)

echo "== Probelauf"
"$OUT/LakuenCast-x86_64.AppImage" --version
ls -la "$OUT"
