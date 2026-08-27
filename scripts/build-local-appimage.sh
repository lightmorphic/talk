#!/usr/bin/env bash
# Build Talkin-x86_64.AppImage locally, without CI.
#
# scripts/build-appimage.sh builds a bundle from scratch and needs the
# host to supply python3-gi, python3-cairo and friends. This script takes
# the shortcut instead: it reuses the runtime already inside a Talkin
# AppImage (same Python, same GTK, same onnx-asr) and swaps Talkin's
# source in. Fine for a personal build; use the real script for anything
# published.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# A pristine copy kept aside on purpose: the output has the same name as
# the donor used to, so building would otherwise eat the thing it builds
# from and only work once.
DONOR="${1:-$HERE/vendor/donor.AppImage}"
OUT="$HERE/Talkin-x86_64.AppImage"

[ -x "$DONOR" ] || { echo "donor AppImage not found: $DONOR" >&2; exit 1; }
command -v file >/dev/null || { echo "install 'file' first (appimagetool needs it)" >&2; exit 1; }

mkdir -p "$HERE/tools"
[ -x "$HERE/tools/appimagetool" ] || {
  curl -fsSL -o "$HERE/tools/appimagetool" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$HERE/tools/appimagetool"
}

rm -rf "$HERE/runtime" "$HERE/build"
mkdir -p "$HERE/runtime" "$HERE/build"
(cd "$HERE/runtime" && "$DONOR" --appimage-extract >/dev/null)
cp -a "$HERE/runtime/squashfs-root" "$HERE/build/AppDir"
A="$HERE/build/AppDir"

# Swap in this checkout's code, translations and icons.
rm -rf "$A/usr/share/talkin/talkin" "$A/usr/share/talkin/locales"
cp -a "$HERE/talkin"   "$A/usr/share/talkin/talkin"
cp -a "$HERE/locales"  "$A/usr/share/talkin/locales"
cp -a "$HERE/assets/." "$A/usr/share/talkin/assets/" 2>/dev/null || true
find "$A/usr/share/talkin/talkin" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

# pynput stays: it is the X11 typing backend. The donor bundle already
# carries it, which is the only reason this shortcut can build a working
# X11 binary at all.

# NOTE: the AppDir root entries are SYMLINKS into usr/share — write the
# real files first, then relink, or you end up with dangling links and
# appimagetool fails in a confusing way.
rm -f "$A/usr/share/applications/talkin.desktop"
cat > "$A/usr/share/applications/uk.co.lightmorphic.Talkin.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Talkin
Comment=Private, on-device dictation for Linux
Exec=talkin
Icon=talkin
Categories=Utility;Accessibility;
Terminal=false
StartupWMClass=talkin
EOF
rm -f "$A"/*.desktop "$A"/talkin.png "$A/.DirIcon"
ln -s usr/share/applications/uk.co.lightmorphic.Talkin.desktop "$A/uk.co.lightmorphic.Talkin.desktop"
ln -s usr/share/icons/hicolor/256x256/apps/talkin.png "$A/talkin.png"
ln -s talkin.png "$A/.DirIcon"

rm -f "$OUT"
ARCH=x86_64 "$HERE/tools/appimagetool" --no-appstream "$A" "$OUT"
echo "== built: $OUT =="
ls -lh "$OUT"
