#!/bin/bash
# gamescope/build.sh — clone the pinned gamescope base, apply Migaki's patches
# and install a gamescope with the CuNNy `-F` filters into --prefix.
#
# Usage: build.sh --prefix DIR [--jobs N] [--source DIR] [--clean]
#
# Options:
#   --prefix DIR   install destination, required (binaries land in DIR/bin)
#   --jobs N       parallel compile jobs (default: nproc)
#   --source DIR   working checkout, reused between builds
#                  (default: $HOME/gamescope-migaki-src)
#   --clean        delete the build directory first (full rebuild)
#   --help         this text
#
# The checkout is reset to the pinned commit on every run and the patches are
# re-applied from scratch, so a failed build never leaves a half-patched tree
# behind. `base.txt` pins the upstream commit; if it moved, this exits instead
# of building something else.
#
# Environment:
#   MIGAKI_LIBXRES_PREFIX  libXRes prefix probed when pkg-config has no xres.pc
#                          (default: $HOME/.local/libxres)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_FILE="$HERE/base.txt"
PATCHES="$HERE/patches"
SHADERS="$HERE/shaders"

PREFIX=""
JOBS="$(nproc 2>/dev/null || echo 4)"
SOURCE="${HOME}/gamescope-migaki-src"
CLEAN=0
XRES_LOCAL=0

note() { printf '  · %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die()  { printf 'gamescope/build: %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; /^set -euo/d'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="${2:?--prefix needs a directory}"; shift 2 ;;
    --jobs)   JOBS="${2:?--jobs needs a number}"; shift 2 ;;
    --source) SOURCE="${2:?--source needs a directory}"; shift 2 ;;
    --clean)  CLEAN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown option '$1' (see --help)" ;;
  esac
done

[ -n "$PREFIX" ] || { usage >&2; die "--prefix is required"; }
case "$JOBS" in ''|*[!0-9]*) die "--jobs must be a number" ;; esac
[ -f "$BASE_FILE" ] || die "missing $BASE_FILE"

read -r REPO PR_REF PINNED WLROOTS_PINNED < "$BASE_FILE" \
  || die "cannot parse $BASE_FILE (expected: <repo> <pr-ref> <commit> wlroots=<commit>)"
case "$PINNED" in
  *[!0-9a-f]*) die "bad commit in $BASE_FILE: '$PINNED'" ;;
esac
[ "${#PINNED}" -eq 40 ] || die "bad commit in $BASE_FILE: '$PINNED'"
[ -n "$REPO" ] && [ -n "$PR_REF" ] || die "bad base line in $BASE_FILE"

# --- 1. base checkout ------------------------------------------------------
step "gamescope base $PINNED"
if [ ! -d "$SOURCE/.git" ]; then
  note "cloning $REPO into $SOURCE (blobless)"
  git clone --filter=blob:none "https://github.com/$REPO.git" "$SOURCE" \
    || die "clone failed"
fi
cd "$SOURCE"
git fetch --quiet origin "$PR_REF" || true
if ! git cat-file -e "${PINNED}^{commit}" 2>/dev/null; then
  git fetch --quiet origin "$PINNED" || die "cannot fetch pinned commit $PINNED"
fi
git checkout -f --quiet "$PINNED" || die "cannot check out $PINNED"
got="$(git rev-parse HEAD)"
[ "$got" = "$PINNED" ] \
  || die "base moved: expected $PINNED, got $got — update gamescope/base.txt"
note "HEAD $got"

# --- 2. submodules --------------------------------------------------------
step "submodules"
# The pinned wlroots commit is not a branch tip, so a shallow fetch can fail
# depending on the server; fall back to a full one instead of dying.
git submodule update --init --recursive --depth 1 \
  || { note "shallow submodule fetch failed — retrying full"; \
       git submodule update --init --recursive || die "submodule update failed"; }

# --- 3. wlroots patch -----------------------------------------------------
step "patches"
WL="$SOURCE/subprojects/wlroots"
[ -d "$WL" ] || die "subprojects/wlroots missing after submodule update"
if grep -q LIBINPUT_SWITCH_KEYPAD_SLIDE "$WL/backend/libinput/switch.c" 2>/dev/null; then
  note "wlroots already has the keypad-slide case — skipping 0001"
else
  git -C "$WL" apply --3way "$PATCHES/0001-wlroots-libinput-keypad-slide.patch" \
    || die "failed to apply 0001 to wlroots"
  note "applied 0001 (wlroots libinput keypad slide)"
fi

# --- 4. shader sources ----------------------------------------------------
# 0002 only wires the .comp files into meson's shader_src; the sources
# themselves (one directory per CuNNy model) are copied here first so the patch
# never creates them (a patch that did would conflict with this copy on a
# rebuild).
step "shaders"
[ -d "$SHADERS/CuNNy" ] || die "missing $SHADERS/CuNNy"
cp -a "$SHADERS/." "$SOURCE/src/shaders/"
note "copied CuNNy shaders into src/shaders/"

# --- 5. gamescope patches -------------------------------------------------
for p in "$PATCHES"/0002-*.patch; do
  [ -f "$p" ] || die "missing patch $(basename "$p")"
  git apply --3way "$p" || die "failed to apply $(basename "$p")"
  note "applied $(basename "$p")"
done

# --- 6. dependencies + build ---------------------------------------------
step "build dependencies"
missing=""
for t in meson ninja glslangValidator pkg-config; do
  command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done
if ! pkg-config --exists xres 2>/dev/null; then
  XR_PREFIX="${MIGAKI_LIBXRES_PREFIX:-$HOME/.local/libxres}"
  if [ -f "$XR_PREFIX/lib/pkgconfig/xres.pc" ]; then
    export PKG_CONFIG_PATH="$XR_PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    XRES_LOCAL=1
    note "using libXRes from $XR_PREFIX (no system xres.pc)"
  else
    missing="$missing libXRes(xres.pc)"
  fi
fi
if [ -n "$missing" ]; then
  osid=""
  [ -r /etc/os-release ] && osid="$(. /etc/os-release; printf '%s %s' "${ID:-}" "${ID_LIKE:-}")"
  case "$osid" in
    *arch*) cmd="sudo pacman -S --needed meson ninja glslang vulkan-headers pkgconf gcc libxres" ;;
    *debian*|*ubuntu*) cmd="sudo apt install meson ninja-build glslang-tools libvulkan-dev pkg-config build-essential libxres-dev" ;;
    *fedora*|*rhel*) cmd="sudo dnf install meson ninja-build glslang vulkan-headers pkgconf gcc gcc-c++ libXres-devel" ;;
    *) cmd="install meson, ninja, glslang, vulkan headers, pkg-config, a C/C++ compiler and libXRes" ;;
  esac
  die "missing build dependencies:$missing
  install them with:
    $cmd"
fi

step "meson setup"
BUILD="$SOURCE/build"
[ "$CLEAN" = "1" ] && { rm -rf "$BUILD"; note "--clean: removed $BUILD"; }
if [ -f "$BUILD/build.ninja" ]; then
  meson setup --reconfigure "$BUILD" "$SOURCE" \
    --buildtype=release -Denable_openvr_support=false \
    || die "meson setup failed (the missing dependency is named above)"
else
  meson setup "$BUILD" "$SOURCE" \
    --buildtype=release -Denable_openvr_support=false \
    || die "meson setup failed (the missing dependency is named above)"
fi

step "ninja -j $JOBS"
ninja -C "$BUILD" -j "$JOBS" || die "compile failed"

# --- 7. install -----------------------------------------------------------
step "install into $PREFIX"
for b in gamescope gamescopereaper; do
  [ -x "$BUILD/src/$b" ] || die "expected $BUILD/src/$b after the build"
  install -Dm755 "$BUILD/src/$b" "$PREFIX/bin/$b"
done
printf '%s\n' "$PINNED" > "$PREFIX/VERSION"
if [ "$XRES_LOCAL" = "1" ]; then
  # The binary's RUNPATH points at the local prefix; keep a copy next to it so
  # the install survives that prefix going away (the launcher prepends
  # $prefix/lib to LD_LIBRARY_PATH).
  mkdir -p "$PREFIX/lib"
  cp -a "$XR_PREFIX/lib/"libXRes.so* "$PREFIX/lib/"
  note "bundled libXRes into $PREFIX/lib"
fi
note "gamescope $(cat "$PREFIX/VERSION") -> $PREFIX/bin/gamescope"

# --- 8. smoke -------------------------------------------------------------
step "smoke test"
help_out="$("$PREFIX/bin/gamescope" --help 2>&1 || true)"
case "$help_out" in
  *'cunny-8x32 =>'*) note "gamescope --help lists the CuNNy filters" ;;
  *) die "smoke test failed: 'gamescope --help' does not list the CuNNy filters" ;;
esac

printf '\ndone: %s\n' "$PREFIX/bin/gamescope"
