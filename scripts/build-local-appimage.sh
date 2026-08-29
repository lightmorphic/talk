#!/usr/bin/env bash
# Build Talkmorphic-x86_64.AppImage locally, without CI.
#
# scripts/build-appimage.sh builds a bundle from scratch and needs the
# host to supply python3-gi, python3-cairo and friends. This script takes
# the shortcut instead: it reuses the runtime already inside a Talkmorphic
# AppImage (same Python, same GTK, same onnx-asr) and swaps Talkmorphic's
# source in. Fine for a personal build; use the real script for anything
# published.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# A pristine copy kept aside on purpose: the output has the same name as
# the donor used to, so building would otherwise eat the thing it builds
# from and only work once.
DONOR="${1:-$HERE/vendor/donor.AppImage}"
OUT="$HERE/Talkmorphic-x86_64.AppImage"

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

# The donor is a pristine build of an earlier name (Talkin). Its own
# launcher script, icon files and paths still say so — rename every
# such path, and rewrite "talkin" to "talkmorphic" inside every text
# file the rename touches (the launcher script itself, principally),
# before this checkout's own code is copied in below. A plain string
# rename plus a plain text substitution, the same two operations used
# to rebrand this repo in the first place, just scoped to the donor.
find "$A" -depth -iname '*talkin*' | while read -r old; do
  new="$(dirname "$old")/$(basename "$old" | sed 's/Talkin/Talkmorphic/g; s/talkin/talkmorphic/g')"
  [ "$old" != "$new" ] && mv "$old" "$new"
done
grep -rlI '\btalkin\b\|\bTalkin\b' "$A" 2>/dev/null | while read -r f; do
  sed -i 's/\bTalkin\b/Talkmorphic/g; s/\btalkin\b/talkmorphic/g' "$f"
done
# The rename above only touches a path's OWN name; a symlink whose
# TARGET mentions the old name (AppRun.wrapped -> usr/bin/talkin, for
# one) is left pointing at a file that no longer exists under that
# name. Re-point every such link at the renamed file instead.
find "$A" -type l | while read -r link; do
  target="$(readlink "$link")"
  case "$target" in
    *talkin*)
      new_target="$(echo "$target" | sed 's/Talkin/Talkmorphic/g; s/talkin/talkmorphic/g')"
      ln -sf "$new_target" "$link"
      ;;
  esac
done

# Swap in this checkout's code, translations and icons.
rm -rf "$A/usr/share/talkmorphic/talkmorphic" "$A/usr/share/talkmorphic/locales"
cp -a "$HERE/talkmorphic"   "$A/usr/share/talkmorphic/talkmorphic"
cp -a "$HERE/locales"  "$A/usr/share/talkmorphic/locales"
cp -a "$HERE/assets/." "$A/usr/share/talkmorphic/assets/" 2>/dev/null || true
find "$A/usr/share/talkmorphic/talkmorphic" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

# pynput stays: it is the X11 typing backend. The donor bundle already
# carries it, which is the only reason this shortcut can build a working
# X11 binary at all.

# NOTE: the AppDir root entries are SYMLINKS into usr/share — write the
# real files first, then relink, or you end up with dangling links and
# appimagetool fails in a confusing way.
rm -f "$A/usr/share/applications/talkmorphic.desktop"
cat > "$A/usr/share/applications/uk.co.lightmorphic.Talkmorphic.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Talkmorphic
Comment=Private, on-device dictation for Linux
Exec=talkmorphic
Icon=talkmorphic
Categories=Utility;Accessibility;
Terminal=false
StartupWMClass=talkmorphic
EOF
rm -f "$A"/*.desktop "$A"/talkmorphic.png "$A/.DirIcon"
ln -s usr/share/applications/uk.co.lightmorphic.Talkmorphic.desktop "$A/uk.co.lightmorphic.Talkmorphic.desktop"
ln -s usr/share/icons/hicolor/256x256/apps/talkmorphic.png "$A/talkmorphic.png"
ln -s talkmorphic.png "$A/.DirIcon"

rm -f "$OUT"
ARCH=x86_64 "$HERE/tools/appimagetool" --no-appstream "$A" "$OUT"
echo "== built: $OUT =="
ls -lh "$OUT"
