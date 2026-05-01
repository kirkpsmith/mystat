#!/bin/sh

.venv/bin/pyinstaller --windowed --noconsole --add-data ".venv/lib/python3.12/site-packages/PyQt5/Qt5/plugins:PyQt5/Qt5/plugins" mystat.py

set -e

APPNAME=MYSTAT
VERSION=$(grep -Po "__version__\s*=\s*['\"]\K[^'\"]+" version.py)
EXE=dist/mystat
ICON=icon/icon.png
APPDIR=${APPNAME}-${VERSION}.AppDir
case "$(uname -m)" in
  x86_64|amd64) ARCH=x86_64 ;;
  aarch64|arm64) ARCH=aarch64 ;;
  i386|i686) ARCH=i386 ;;
  *) ARCH="$(uname -m)" ;;  # fallback to raw value
esac

# Clean
rm -rf "$APPDIR" *.AppImage

# Make AppDir layout
mkdir -p "$APPDIR"/usr/bin
mkdir -p "$APPDIR"/usr/share/icons/hicolor/256x256/apps
mkdir -p "$APPDIR"/usr/share/applications

# Copy files
cp -r "$EXE" "$APPDIR"/usr/bin/mystat
# make the inner executable executable
chmod +x "$APPDIR"/usr/bin/mystat/mystat
cp "$ICON" "$APPDIR"/icon.png

# .desktop
cat > "$APPDIR"/mystat.desktop <<EOF
[Desktop Entry]
Type=Application
Name=$APPNAME
Exec=usr/bin/mystat/mystat
Icon=icon
Terminal=false
Categories=Utility;
EOF

# AppRun
cat > "$APPDIR"/AppRun <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/mystat/mystat" "$@"
EOF
chmod +x "$APPDIR"/AppRun

# Build AppImage
./appimagetool "$APPDIR"

mv *.AppImage "${APPNAME}-${VERSION}-${ARCH}.AppImage"

# Clean
rm -rf *.AppDir
rm -rf  build
rm -rf dist
rm mystat.spec

echo "Done: ${APPNAME}-${VERSION}-${ARCH}.AppImage"

