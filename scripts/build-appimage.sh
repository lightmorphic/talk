#!/bin/bash
# Builds Talkin-x86_64.AppImage from a clean checkout. Designed to run
# on a fresh Ubuntu CI runner (see .github/workflows/release.yml) but
# works on any Debian-family desktop with the right packages installed
# (see apt-get line below) — that's how this script was developed and
# tested, on the maintainer's own desktop.
#
# The speech model is NOT bundled — Talkin downloads it once on first
# run and caches it outside the AppImage (see talkin/config.py), so it
# survives every future update untouched. That keeps this build fast
# and the AppImage itself small.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-$REPO_ROOT/build-appimage}"
PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

echo "== Talkin AppImage build =="
echo "repo:  $REPO_ROOT"
echo "build: $BUILD_DIR"
echo "python: $PY_VERSION"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/tools"
cd "$BUILD_DIR"

# -- 1. fetch packaging tools -------------------------------------------

echo "-- fetching packaging tools --"
curl -fsSL -o tools/linuxdeploy-x86_64.AppImage \
  "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
curl -fsSL -o tools/linuxdeploy-plugin-gtk.sh \
  "https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh"
curl -fsSL -o tools/appimagetool-x86_64.AppImage \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x tools/*.AppImage tools/*.sh

# -- 2. a self-contained Python environment ------------------------------

echo "-- creating venv and installing requirements --"
python3 -m venv --system-site-packages pkgvenv
pkgvenv/bin/pip -q install --upgrade pip
pkgvenv/bin/pip install -r "$REPO_ROOT/requirements.txt"

SITE="pkgvenv/lib/python$PY_VERSION/site-packages"
DIST_PKGS="/usr/lib/python3/dist-packages"

# PyGObject/pycairo must match the system GTK they'll be bundled
# against, so they come from apt, not pip. (Ubuntu ships the actual
# `cairo` Python module in python3-cairo — NOT python3-gi-cairo, which
# is a different, GI-only package that doesn't provide it.)
echo "-- vendoring PyGObject/pycairo from apt --"
echo "SITE=$SITE (exists: $([ -d "$SITE" ] && echo yes || echo no))"
for pkg in gi cairo; do
  echo "checking $DIST_PKGS/$pkg"
  if [ ! -d "$DIST_PKGS/$pkg" ]; then
    echo "!! $DIST_PKGS/$pkg missing — is python3-gi / python3-cairo installed?" >&2
    exit 1
  fi
  echo "copying $pkg -> $SITE/"
  cp -rv "$DIST_PKGS/$pkg" "$SITE/" | tail -3
  echo "done $pkg"
done
echo "-- vendoring complete --"

# --system-site-packages venvs silently treat anything already
# importable via the system as "satisfied" without installing it
# locally. Detect and vendor every such straggler so the bundle is
# genuinely self-contained — verified against the REAL import chain
# actually exercised at runtime (recognizing audio, downloading the
# model, running the settings server), not just the top-level modules.
verify() {
  pkgvenv/bin/python -c "
import sys
sys.path = [p for p in sys.path if 'dist-packages' not in p]
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, AyatanaAppIndicator3
import cairo, numpy, sounddevice, flask, onnx_asr, onnxruntime
import httpx
from huggingface_hub import snapshot_download
import pynput.keyboard, pynput.mouse
print('CLEAN')
" 2>&1
}

for _ in $(seq 1 25); do
  # verify() failing must NOT abort the script here (that's the whole
  # point of this loop) — under `set -e`, a plain `out="$(verify)"`
  # assignment DOES still trigger errexit on a nonzero exit status,
  # silently skipping every line below it. The `|| true` is load-bearing.
  out="$(verify)" || true
  echo "$out" | grep -q CLEAN && { echo "== dependency closure clean =="; break; }
  missing="$(echo "$out" | grep -oP "No module named '\K[^']+" | head -1)"
  if [ -z "$missing" ]; then
    echo "$out"
    echo "!! could not resolve remaining import failure" >&2
    exit 1
  fi
  origin="$(python3 -c "
import importlib.util
spec = importlib.util.find_spec('$missing')
print(spec.origin if spec else '')
" 2>/dev/null || true)"
  if [[ "$origin" == "$DIST_PKGS"/*/__init__.py ]]; then
    echo "vendoring $missing (package) from system"
    cp -r "$(dirname "$origin")" "$SITE/"
  elif [[ -n "$origin" && "$origin" == "$DIST_PKGS"/* ]]; then
    echo "vendoring $missing (module) from system"
    cp "$origin" "$SITE/"
  else
    echo "installing $missing via pip"
    pkgvenv/bin/pip install -q --no-deps "$missing"
  fi
done
verify | grep -q CLEAN || { echo "!! dependency closure never converged" >&2; exit 1; }

# sounddevice loads PortAudio via ctypes.util.find_library, which on
# Linux shells out to ldconfig — invisible to linuxdeploy's normal
# ldd-based dependency scan, and ldconfig only knows about libraries
# actually installed on THIS machine, never what ends up bundled
# inside the AppImage. So this needs bundling explicitly; the runtime
# half of the fix (pointing find_library at this exact path) lives in
# talkin/config.py's patch_library_lookup().
PORTAUDIO_PATH="$(ldconfig -p | grep -m1 'libportaudio\.so\.2$' | awk '{print $NF}')"
if [ -z "$PORTAUDIO_PATH" ]; then
  echo "!! libportaudio2 not found — install it before building" >&2
  exit 1
fi
mkdir -p AppDir/usr/lib
cp "$PORTAUDIO_PATH" AppDir/usr/lib/libportaudio.so.2

# -- 3. AppDir skeleton ---------------------------------------------------

echo "-- assembling AppDir --"
mkdir -p AppDir/usr/bin AppDir/usr/lib "AppDir/usr/lib/python$PY_VERSION" \
         AppDir/usr/share/talkin AppDir/usr/share/applications \
         AppDir/usr/share/icons/hicolor/256x256/apps

cp -r "$REPO_ROOT/talkin" AppDir/usr/share/talkin/
cp -r "$REPO_ROOT/locales" AppDir/usr/share/talkin/
cp -r "$REPO_ROOT/assets" AppDir/usr/share/talkin/
find AppDir/usr/share/talkin -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

PY_BIN="$(readlink -f "$(command -v python$PY_VERSION)")"
PY_LIB="$(python3 -c "import sysconfig; print(sysconfig.get_config_var('LDLIBRARY'))")"
# ldconfig resolves the real path regardless of multiarch subdirectory
# layout (e.g. /usr/lib/x86_64-linux-gnu/ on Debian/Ubuntu).
PY_LIB_PATH="$(ldconfig -p | grep -m1 "${PY_LIB}\.[0-9]" | awk '{print $NF}')"
if [ -z "$PY_LIB_PATH" ]; then
  echo "!! could not locate $PY_LIB via ldconfig" >&2
  exit 1
fi
cp "$PY_BIN" AppDir/usr/bin/python3
cp "$PY_LIB_PATH" AppDir/usr/lib/
PY_LIB="$(basename "$PY_LIB_PATH")"
cp -r "/usr/lib/python$PY_VERSION"/* "AppDir/usr/lib/python$PY_VERSION/"
find "AppDir/usr/lib/python$PY_VERSION" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
rm -rf "AppDir/usr/lib/python$PY_VERSION"/{test,idlelib,tkinter}
rm -rf "AppDir/usr/lib/python$PY_VERSION/site-packages"
cp -r "$SITE" "AppDir/usr/lib/python$PY_VERSION/site-packages"
find "AppDir/usr/lib/python$PY_VERSION/site-packages" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find AppDir -xtype l -delete

cp "$REPO_ROOT/docs/talkin-512.png" AppDir/usr/share/icons/hicolor/256x256/apps/talkin.png
cp AppDir/usr/share/icons/hicolor/256x256/apps/talkin.png AppDir/talkin.png

cat > AppDir/talkin.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Talkin
Comment=Private, on-device dictation for the Linux desktop
Exec=talkin
Icon=talkin
Categories=Utility;Accessibility;
Terminal=false
EOF
cp AppDir/talkin.desktop AppDir/usr/share/applications/talkin.desktop

cat > AppDir/usr/bin/talkin <<'LAUNCHER'
#!/bin/bash
# Talkin's real entry point inside the AppImage. APPDIR is set by
# AppRun to the mounted bundle root; everything Talkin needs to READ
# (code, GTK/typelibs, icons) lives under it, read-only. Anything
# Talkin needs to WRITE (settings, the downloaded speech model) is
# routed by talkin/config.py to a persistent folder outside the
# bundle, so it survives every future AppImage update untouched.
HERE="${APPDIR:-$(dirname "$(dirname "$(readlink -f "$0")")")}"

export GI_TYPELIB_PATH="$HERE/usr/lib/girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
export XDG_DATA_DIRS="$HERE/usr/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas"
export GDK_PIXBUF_MODULE_FILE="$HERE/usr/lib/gdk-pixbuf-2.0/loaders.cache"
export PYTHONHOME="$HERE/usr"
export PYTHONPATH="$HERE/usr/lib/python3.13:$HERE/usr/lib/python3.13/site-packages"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export LD_LIBRARY_PATH="$HERE/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$HERE/usr/bin${PATH:+:$PATH}"
export APPIMAGE="${APPIMAGE:-$(readlink -f "$0")}"

exec "$HERE/usr/bin/python3" -S -m talkin "$@"
LAUNCHER
sed -i "s/python3.13/python$PY_VERSION/g" AppDir/usr/bin/talkin
chmod +x AppDir/usr/bin/talkin

# -- 4. bundle GTK/GObject-Introspection + resolve every shared lib -----

echo "-- running linuxdeploy (this is the slow part) --"
SO_ARGS=()
while IFS= read -r so; do
  SO_ARGS+=(-l "$so")
done < <(find AppDir -name "*.so" -o -name "*.so.*")

export DEPLOY_GTK_VERSION=3
export PATH="$PWD/tools:$PATH"
NO_STRIP=1 tools/linuxdeploy-x86_64.AppImage --appimage-extract-and-run \
  --appdir AppDir \
  -e AppDir/usr/bin/python3 \
  -l "AppDir/usr/lib/$PY_LIB" \
  "${SO_ARGS[@]}" \
  -d AppDir/talkin.desktop \
  -i AppDir/talkin.png \
  --plugin gtk \
  --output appimage

mv Talkin*.AppImage "$REPO_ROOT/Talkin-x86_64.AppImage" 2>/dev/null || \
  mv talkin*.AppImage "$REPO_ROOT/Talkin-x86_64.AppImage"

echo "== built: $REPO_ROOT/Talkin-x86_64.AppImage =="
ls -la "$REPO_ROOT/Talkin-x86_64.AppImage"
