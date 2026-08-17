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

# pynput's Linux backend connects to an X display the moment it's
# imported — not just when a Controller is instantiated — so even the
# dependency-completeness check below needs one. Transparently re-exec
# under a virtual display if there's no real one (headless CI); a no-op
# wherever a real X session already exists (the maintainer's desktop).
if [ -z "${DISPLAY:-}" ] && command -v xvfb-run >/dev/null 2>&1; then
  exec xvfb-run -a "$0" "$@"
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-$REPO_ROOT/build-appimage}"
PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

echo "== Talkin AppImage build =="
echo "repo:  $REPO_ROOT"
echo "build: $BUILD_DIR"
echo "python: $PY_VERSION"

# Wipe everything EXCEPT tools/ - the downloaded packaging tools are
# byte-identical between runs, and re-fetching them on every build is
# exactly what got local builds 429-rate-limited by GitHub after a day
# of releases. (CI starts from an empty workspace either way.)
mkdir -p "$BUILD_DIR/tools"
find "$BUILD_DIR" -mindepth 1 -maxdepth 1 ! -name tools -exec rm -rf {} +
cd "$BUILD_DIR"

# -- 1. fetch packaging tools -------------------------------------------

echo "-- fetching packaging tools --"
# Skipped for any tool already sitting in tools/ from a previous build:
# repeated local builds were getting 429-rate-limited by GitHub over
# re-downloading identical binaries. CI always starts from an empty
# tools/ dir, so it still fetches fresh ones every release.
fetch() {
  # Download to a temp name and move only on success, so an aborted
  # run can never leave a truncated file that then gets "skipped" as
  # if it were complete.
  [ -s "tools/$1" ] || { curl -fsSL -o "tools/$1.part" "$2" \
    && mv "tools/$1.part" "tools/$1"; }
}
fetch linuxdeploy-x86_64.AppImage \
  "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage"
fetch linuxdeploy-plugin-gtk.sh \
  "https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh"
fetch appimagetool-x86_64.AppImage \
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
# model, showing the settings window), not just the top-level modules.
verify() {
  pkgvenv/bin/python -c "
import sys
sys.path = [p for p in sys.path if 'dist-packages' not in p]
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import Gtk, AyatanaAppIndicator3
import cairo, numpy, sounddevice, onnx_asr, onnxruntime
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
  echo "--- verify() output ---"
  echo "$out"
  echo "-----------------------"
  # tail -1, not head -1: huggingface_hub prints an optional-dependency
  # warning containing this same phrase before the real traceback, so
  # the FIRST match is often a red herring — the actual unhandled
  # exception (what we need to fix) is always the last one.
  missing="$(echo "$out" | grep -oP "No module named '\K[^']+" | tail -1)"
  if [ -z "$missing" ]; then
    echo "!! could not resolve remaining import failure" >&2
    exit 1
  fi
  # Check via pkgvenv's OWN interpreter (not the bare system python3,
  # which can be a different install entirely) so this matches exactly
  # what verify() itself sees.
  origin="$(pkgvenv/bin/python -c "
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
    # --ignore-installed: pip's own "already satisfied" check also
    # looks at the system site-packages (same --system-site-packages
    # gotcha as the main requirements.txt install above) and silently
    # no-ops instead of actually installing a local copy — confirmed
    # with `pip install -v`, which showed exactly that for filelock.
    echo "installing $missing via pip"
    pkgvenv/bin/pip install -q --ignore-installed --no-deps "$missing"
  fi
done
# Same `|| true` + capture-then-grep pattern as the loop above — piping
# verify()'s live output straight into `grep -q` let grep exit the
# instant it saw CLEAN, SIGPIPEing the still-writing verify() process.
final="$(verify)" || true
echo "$final" | grep -q CLEAN || { echo "!! dependency closure never converged" >&2; exit 1; }

# sounddevice loads PortAudio via ctypes.util.find_library, which on
# Linux shells out to ldconfig — invisible to linuxdeploy's normal
# ldd-based dependency scan, and ldconfig only knows about libraries
# actually installed on THIS machine, never what ends up bundled
# inside the AppImage. So this needs bundling explicitly; the runtime
# half of the fix (pointing find_library at this exact path) lives in
# talkin/config.py's patch_library_lookup().
#
# ldconfig's ~1300-line listing is written straight to a FILE, not
# captured through a pipe (neither a literal `|` into grep, nor even
# `$(ldconfig -p)` command substitution — that still funnels the
# child's output through bash's own internal pipe). A burst that size
# through any pipe in this environment reliably killed the whole
# script with SIGPIPE right at this step, every time. Reading it back
# with grep -m1 from a plain file has no live writer to SIGPIPE at all.
LDCONFIG_CACHE="$BUILD_DIR/ldconfig-cache.txt"
ldconfig -p > "$LDCONFIG_CACHE"
PORTAUDIO_PATH="$(grep -m1 'libportaudio\.so\.2$' "$LDCONFIG_CACHE" | awk '{print $NF}')"
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
# layout (e.g. /usr/lib/x86_64-linux-gnu/ on Debian/Ubuntu). Same
# capture-first pattern as PORTAUDIO_PATH above — `grep -m1` piped
# directly onto a live ldconfig would risk SIGPIPEing it again.
PY_LIB_PATH="$(grep -m1 "${PY_LIB}\.[0-9]" "$LDCONFIG_CACHE" | awk '{print $NF}')"
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

cp "$REPO_ROOT/docs/images/talkin-512.png" AppDir/usr/share/icons/hicolor/256x256/apps/talkin.png
cp AppDir/usr/share/icons/hicolor/256x256/apps/talkin.png AppDir/talkin.png

cat > AppDir/talkin.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Lightmorphic Talkin
Comment=Private, on-device dictation for the Linux desktop
Exec=talkin
Icon=talkin
Categories=Utility;Accessibility;
Terminal=false
StartupWMClass=talkin
EOF
cp AppDir/talkin.desktop AppDir/usr/share/applications/talkin.desktop

# AppStream metadata: what software centers and AppImage catalogs read
# to show the app properly (name, description, screenshot) instead of
# an anonymous binary. Version/date are stamped from the source tree at
# build time so the file can't quietly go stale between releases.
APP_VERSION="$(grep -oP '__version__ = "\K[^"]+' "$REPO_ROOT/talkin/__init__.py")"
mkdir -p AppDir/usr/share/metainfo
# Installed under the AppStream component id's name, NOT the desktop
# file's basename appimagetool looks for. Deliberate: when appimagetool
# finds a file at its expected name it runs its own NETWORKED
# validation and treats any warning as fatal - including a transient
# 404 on the screenshot URL - which would leave every future release
# hostage to the website being reachable mid-build. Catalog tools read
# everything in usr/share/metainfo/ regardless of filename, so the only
# cost is one cosmetic "metadata is missing" line in appimagetool's
# output. Validation still happens, offline and on our terms, below.
sed "s/@VERSION@/$APP_VERSION/; s/@DATE@/$(date +%F)/" \
  "$REPO_ROOT/packaging/uk.co.lightmorphic.Talkin.appdata.xml" \
  > AppDir/usr/share/metainfo/uk.co.lightmorphic.Talkin.appdata.xml
if command -v appstreamcli >/dev/null 2>&1; then
  appstreamcli validate --no-net \
    AppDir/usr/share/metainfo/uk.co.lightmorphic.Talkin.appdata.xml \
    || { echo "!! AppStream metadata failed validation" >&2; exit 1; }
fi

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
# Losing the system's XDG_DATA_DIRS (e.g. /usr/share) isn't just
# cosmetic: gdk-pixbuf's format sniffing depends on the shared MIME
# database that lives there, and without it every icon load fails
# with "Couldn't recognise the image file format" — confirmed by
# reproducing it directly. Falls back to the XDG-spec default rather
# than the bundle alone if the launch context has no XDG_DATA_DIRS at
# all (most desktop sessions do, but not every launch context does).
export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export GSETTINGS_SCHEMA_DIR="$HERE/usr/share/glib-2.0/schemas"
# The real file is nested under a gdk-pixbuf ABI version directory
# (e.g. 2.10.0) that varies by build machine — glob for it rather than
# hardcoding a version, so this doesn't silently point at nothing.
GDK_PIXBUF_CACHE="$(find "$HERE/usr/lib/gdk-pixbuf-2.0" \
  -maxdepth 2 -name loaders.cache 2>/dev/null | head -1)"
[ -n "$GDK_PIXBUF_CACHE" ] && export GDK_PIXBUF_MODULE_FILE="$GDK_PIXBUF_CACHE"
export PYTHONHOME="$HERE/usr"
export PYTHONPATH="$HERE/usr/share/talkin:$HERE/usr/lib/python3.13:$HERE/usr/lib/python3.13/site-packages"
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

# AyatanaAppIndicator3's actual .so is never bundled — GObject
# Introspection typelibs reference their library by name and resolve
# it via dlopen at runtime, invisible to linuxdeploy's static ldd
# scan (same reason libportaudio needed handling separately above) —
# so it ALWAYS comes from the target system. That's fine on its own,
# but if we also bundle our OWN (older, build-machine) glib alongside
# it, a target system whose own glib is newer than ours can hand that
# system AppIndicator a glib it doesn't expect, and it crashes on a
# missing symbol — confirmed exactly this way while testing a build.
# glib's ABI is strongly backward-compatible (newer glib satisfies
# code built against older glib, not the reverse), so excluding just
# glib/gobject/gio/gmodule — plus gio's own version-sensitive runtime
# deps — and letting that whole cluster resolve from the target
# system instead keeps everything mutually consistent, however new or
# old that system's glib turns out to be. GTK3/pango/cairo etc. stay
# bundled as normal: an older bundled GTK3 calling a newer system glib
# is exactly the safe direction.
EXCLUDE_ARGS=(
  --exclude-library="libglib-2.0*"
  --exclude-library="libgobject-2.0*"
  --exclude-library="libgio-2.0*"
  --exclude-library="libgmodule-2.0*"
  --exclude-library="libmount*"
  --exclude-library="libblkid*"
  --exclude-library="libselinux*"
)

export DEPLOY_GTK_VERSION=3
export PATH="$PWD/tools:$PATH"
# Deliberately NOT --output appimage here: that would make linuxdeploy
# invoke appimagetool itself as the last part of this same command,
# sealing the squashfs before the cleanup below ever runs. Packaging
# is a separate, explicit step instead, after AppDir is truly final.
NO_STRIP=1 tools/linuxdeploy-x86_64.AppImage --appimage-extract-and-run \
  --appdir AppDir \
  -e AppDir/usr/bin/python3 \
  -l "AppDir/usr/lib/$PY_LIB" \
  "${SO_ARGS[@]}" \
  "${EXCLUDE_ARGS[@]}" \
  -d AppDir/talkin.desktop \
  -i AppDir/talkin.png \
  --plugin gtk

# Belt and braces: the GTK plugin's own bundling pass ignores
# --exclude-library and re-deploys these regardless (confirmed), so
# remove them for real here, before packaging, letting the dynamic
# linker fall through to the target system's self-consistent set.
rm -f AppDir/usr/lib/{libglib-2.0,libgobject-2.0,libgio-2.0,libgmodule-2.0,libmount,libblkid,libselinux}.so*

echo "-- packaging AppImage --"
tools/appimagetool-x86_64.AppImage --appimage-extract-and-run \
  AppDir "$REPO_ROOT/Talkin-x86_64.AppImage"

echo "== built: $REPO_ROOT/Talkin-x86_64.AppImage =="
ls -la "$REPO_ROOT/Talkin-x86_64.AppImage"
