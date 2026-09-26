#!/usr/bin/env bash
# install.sh — the single installer for migaki-linux.
#
# One idempotent flow does everything: distro packages, vkBasalt, Proton,
# RPGMaker support, shaders, translation (Textractor + DeepL), textbox Top and
# desktop entries. It never overwrites your config or game library.
#
# Usage: ./install.sh [--check-only] [--dry-run] [--desktop] [--no-symlink] [-y]
#   --check-only   audit only; exit 1 if a required tool is missing
#   --dry-run      print what would be installed, change nothing
#   --desktop      also install .desktop entries (menu launchers)
#   --no-symlink   do not create ~/.local/bin symlinks
#   -y, --yes      assume yes (unattended)
#
# Internal (used by the runtime self-heal; not for humans):
#   --fetch-vendor [--bridge fixed|stock] [--all] [--dir DIR]
#   --provision-textractor [--prefix DIR] [--bridge fixed|stock]
#                          [--vendor-dir DIR] [--no-link]
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR_DEFAULT="$ROOT/translate/vendor"

# ===========================================================================
# Output kit (pure bash; colors only on a TTY and when NO_COLOR is unset)
# ===========================================================================
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
  C_RESET=$'\033[0m';  C_BOLD=$'\033[1m';  C_DIM=$'\033[2m'
  C_BLUE=$'\033[34m';  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_BLUE=""; C_CYAN=""
  C_GREEN=""; C_YELLOW=""; C_RED=""
fi

step() { printf '\n%s==>%s %s%s%s\n' "$C_BLUE" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
miss() { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; }
note() { printf '  %s·%s %s\n' "$C_DIM" "$C_RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
err()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; }

# ===========================================================================
# Flags
# ===========================================================================
CHECK_ONLY=0; DRY_RUN=0; DESKTOP=0; SYMLINK=1; ASSUME_YES=0
MODE="install"
FETCH_DIR="$VENDOR_DIR_DEFAULT"; FETCH_ALL=0
PROV_PREFIX=""; PROV_VDIR="$VENDOR_DIR_DEFAULT"; PROV_LINK=1
HIDDEN_BRIDGE="stock"

usage() {
  cat <<'EOF'
install.sh — the single installer for migaki-linux.

One idempotent flow: distro packages, vkBasalt, Proton, RPGMaker support,
shaders, VN translation, textbox Top and desktop entries. It never overwrites
your config (~/.config/migaki) or game library.

Usage: ./install.sh [--check-only] [--dry-run] [--desktop] [--no-symlink] [-y]
  --check-only   audit only; exit 1 if a required tool is missing
  --dry-run      print what would be installed, change nothing
  --desktop      also install .desktop menu entries
  --no-symlink   do not create ~/.local/bin symlinks
  -y, --yes      assume yes (unattended)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --check-only) CHECK_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --desktop) DESKTOP=1; shift ;;
    --no-symlink) SYMLINK=0; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --fetch-vendor) MODE="fetch"; shift ;;
    --provision-textractor) MODE="provision"; shift ;;
    --dir) FETCH_DIR="${2:?--dir needs a path}"; shift 2 ;;
    --all) FETCH_ALL=1; shift ;;
    --prefix) PROV_PREFIX="${2:?--prefix needs a path}"; shift 2 ;;
    --bridge) HIDDEN_BRIDGE="${2:?--bridge needs stock|fixed}"; shift 2 ;;
    --vendor-dir) PROV_VDIR="${2:?--vendor-dir needs a path}"; shift 2 ;;
    --no-link) PROV_LINK=0; shift ;;
    *) err "unknown option $1 (see --help)"; exit 1 ;;
  esac
done

# Test seam: point the distro detection at another os-release.
OS_RELEASE="${MIGAKI_OS_RELEASE:-/etc/os-release}"

# ===========================================================================
# Prompts
# ===========================================================================
tty_readable() {
  # True when /dev/tty can actually be opened (-r is not enough: without a
  # controlling terminal the node is "readable" but open() fails with ENXIO).
  [ -c /dev/tty ] && { true </dev/tty; } 2>/dev/null
}
_prompt_read() {
  if tty_readable; then read -r "$1" </dev/tty || eval "$1=''"
  else read -r "$1" || eval "$1=''"; fi
}
confirm() {   # default Yes
  if [ "$ASSUME_YES" = "1" ]; then return 0; fi
  local ans
  while true; do
    printf '  %s?%s %s %s[Y/n]%s ' "$C_CYAN" "$C_RESET" "$1" "$C_DIM" "$C_RESET"
    _prompt_read ans
    case "$ans" in ""|[Yy]|[Yy][Ee][Ss]) return 0 ;; [Nn]|[Nn][Oo]) return 1 ;; *) note "please answer y or n." ;; esac
  done
}
confirm_no() { # default No
  if [ "$ASSUME_YES" = "1" ]; then return 1; fi
  local ans
  while true; do
    printf '  %s?%s %s %s[y/N]%s ' "$C_CYAN" "$C_RESET" "$1" "$C_DIM" "$C_RESET"
    _prompt_read ans
    case "$ans" in ""|[Nn]|[Nn][Oo]) return 1 ;; [Yy]|[Yy][Ee][Ss]) return 0 ;; *) note "please answer y or n." ;; esac
  done
}

# ===========================================================================
# Config helpers
# ===========================================================================
config_set_key() {
  python3 - "$HOME/.config/migaki/config.json" "$1" "$2" <<'EOF'
import json, os, sys
p, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(p))
    if not isinstance(d, dict):
        d = {}
except (OSError, ValueError):
    d = {}
d[k] = v
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(d, open(p, "w"), indent=2)
EOF
}

vkbasalt_layer_present() {
  local d
  for d in "$HOME/.config/vulkan/implicit_layer.d" "$HOME/.local/share/vulkan/implicit_layer.d" \
           /usr/local/share/vulkan/implicit_layer.d /usr/share/vulkan/implicit_layer.d; do
    [ -d "$d" ] || continue
    find "$d" -maxdepth 1 -iname '*vkbasalt*.json' -print -quit 2>/dev/null | grep -q . && return 0
  done
  return 1
}

detect_gpu_vendor() {
  # nvidia|amd|intel|unknown — seeds the lib32-vulkan-driver provider so
  # pacman never shows the interactive provider menu.
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then echo nvidia; return 0; fi
  ls /usr/share/vulkan/icd.d/nvidia_icd*.json >/dev/null 2>&1 && { echo nvidia; return 0; }
  if command -v lspci >/dev/null 2>&1; then
    lspci -nnk 2>/dev/null | grep -qi 'nvidia' && { echo nvidia; return 0; }
    lspci -nnk 2>/dev/null | grep -qi 'amd\|radeon' && { echo amd; return 0; }
    lspci -nnk 2>/dev/null | grep -qi 'intel.*vga\|intel.*graphics\|intel.*display' && { echo intel; return 0; }
  fi
  ls /usr/share/vulkan/icd.d/radeon_icd*.json /usr/share/vulkan/icd.d/amd_icd*.json >/dev/null 2>&1 && { echo amd; return 0; }
  ls /usr/share/vulkan/icd.d/intel_icd*.json /usr/share/vulkan/icd.d/intel_hasvk*.json >/dev/null 2>&1 && { echo intel; return 0; }
  echo unknown
}
lib32_provider_pkg() {
  case "$(detect_gpu_vendor)" in
    nvidia) echo lib32-nvidia-utils ;;
    amd) echo lib32-vulkan-radeon ;;
    intel) echo lib32-vulkan-intel ;;
    *) echo "" ;;
  esac
}

# ===========================================================================
# Package manager abstraction (Arch / Debian / Fedora families)
# ===========================================================================
detect_pm() {
  local id="" like=""
  if [ -r "$OS_RELEASE" ]; then
    id="$(sed -n 's/^ID=//p' "$OS_RELEASE" | head -n1 | tr -d '"')"
    like="$(sed -n 's/^ID_LIKE=//p' "$OS_RELEASE" | head -n1 | tr -d '"')"
  fi
  case " $id $like " in
    *" arch "*|*" manjaro "*|*" endeavouros "*|*" cachyos "*|*" omarchy "*|*" artix "*) echo arch ;;
    *" debian "*|*" ubuntu "*|*" linuxmint "*|*" pop "*|*" kali "*|*" parrot "*|*" neon "*|*" zorin "*|*" elementary "*) echo deb ;;
    *" fedora "*|*" rhel "*|*" centos "*|*" nobara "*|*" bazzite "*) echo fed ;;
    *) echo "" ;;
  esac
}
PM="$(detect_pm)"
case "$PM" in arch) PM_NAME=pacman ;; deb) PM_NAME=apt ;; fed) PM_NAME=dnf ;; *) PM_NAME="" ;; esac

pkg_available() {
  case "$PM" in
    arch) pacman -Si "$1" >/dev/null 2>&1 ;;
    deb)  apt-cache show "$1" >/dev/null 2>&1 ;;
    fed)  dnf -q list --available "$1" >/dev/null 2>&1 ;;
    *)    return 1 ;;
  esac
}
pm_update() {
  case "$PM" in
    deb) echo "  refreshing apt package lists..."; sudo apt-get update ;;
    arch) sudo pacman -Sy >/dev/null 2>&1 || true ;;
  esac
}
pm_install_batch() {
  [ "$#" -gt 0 ] || return 0
  case "$PM" in
    arch) sudo pacman -S --needed --noconfirm "$@" ;;
    deb)  sudo apt-get install -y "$@" ;;
    fed)  sudo dnf install -y "$@" ;;
    *)    warn "no package manager detected — install manually: $*"; return 1 ;;
  esac
}
pm_col() { case "$PM" in arch) echo "$1" ;; deb) echo "$2" ;; fed) echo "$3" ;; *) echo "" ;; esac; }

# Collectors: everything goes into one list + one confirmation.
PKGS=(); PKG_FILES=(); PIP_PKGS=(); NOTES=()
declare -A _seen_pkg=()
add_pkg() { local p; for p in "$@"; do [ -n "$p" ] || continue; [ -n "${_seen_pkg[$p]:-}" ] && continue; _seen_pkg[$p]=1; PKGS+=("$p"); done; }
add_pip() { PIP_PKGS+=("$1"); }
add_note() { NOTES+=("$1"); }

# tool -> "arch-pkg deb-pkg fed-pkg"; @gum/@pip:name are special-cased.
DEPS="python3:python|python3|python3
jq:jq|jq|jq
git:git|git|git
curl:curl|curl|curl
tar:tar|tar|tar
zenity:zenity|zenity|zenity
gum:gum|@gum|gum
mangohud:mangohud|mangohud|mangohud
vkcube:vulkan-tools|vulkan-tools|vulkan-tools
icoextract:icoextract|python3-icoextract|@pip:icoextract"

# ===========================================================================
# GitHub release helpers
# ===========================================================================
gh_latest_tag() {
  curl -sL -o /dev/null -w '%{url_effective}' --max-time 30 \
    "https://github.com/$1/releases/latest" | sed 's|.*/tag/||'
}
fetch_latest_asset() {
  local repo="$1" re="$2" tag
  tag="$(gh_latest_tag "$repo")"
  [ -n "$tag" ] || return 1
  curl -sL --max-time 30 "https://github.com/$repo/releases/expanded_assets/$tag" \
    | grep -oE "$re" | sort -u | tail -n1
}
download_asset() {
  local repo="$1" asset="$2" dest="$3" tag
  tag="$(gh_latest_tag "$repo")"
  curl -L --retry 3 --retry-all-errors --max-time 300 \
    -o "$dest/$asset" "https://github.com/$repo/releases/download/$tag/$asset"
}
umu_deb_suffix() {
  local id="" codename="" vid=""
  if [ -r "$OS_RELEASE" ]; then
    id="$(sed -n 's/^ID=//p' "$OS_RELEASE" | head -n1 | tr -d '"')"
    codename="$(sed -n 's/^VERSION_CODENAME=//p' "$OS_RELEASE" | head -n1 | tr -d '"')"
    vid="$(sed -n 's/^VERSION_ID=//p' "$OS_RELEASE" | head -n1 | tr -d '"')"
  fi
  case "$id" in
    ubuntu) case "$codename" in resolute) echo ubuntu-resolute ;; *) echo ubuntu-noble ;; esac ;;
    debian) case "$vid" in 13|14) echo debian-13 ;; *) echo debian-12 ;; esac ;;
    *) echo ubuntu-noble ;;
  esac
}
fedora_major() { sed -n 's/^VERSION_ID=//p' "$OS_RELEASE" 2>/dev/null | head -n1 | tr -d '"' | cut -d. -f1; }

# ===========================================================================
# Chromium helpers (DeepL CDP automation target)
# ===========================================================================
chromium_default_bin() {
  command -v xdg-settings >/dev/null 2>&1 || return 0
  local id desktop exe d
  id="$(xdg-settings get default-web-browser 2>/dev/null)" || return 0
  [ -n "$id" ] || return 0
  for d in "$HOME/.local/share/applications" /usr/local/share/applications /usr/share/applications; do
    [ -f "$d/$id" ] && { desktop="$d/$id"; break; }
  done
  [ -n "$desktop" ] || return 0
  exe="$(grep -m1 '^Exec=' "$desktop" | cut -d= -f2- | awk '{print $1}')"
  [ -n "$exe" ] || return 0
  case "$exe" in
    /*) [ -x "$exe" ] && printf '%s' "$exe" ;;
    *) command -v "$exe" 2>/dev/null ;;
  esac
}
chromium_is() {
  [ -n "$1" ] && [ -x "$1" ] || return 1
  "$1" --version 2>/dev/null | grep -qi "chromium\|chrome\|brave\|vivaldi\|opera\|edge"
}
chromium_any_present() {
  local c; c="$(chromium_default_bin)" || true
  chromium_is "$c" && return 0
  for c in brave brave-browser brave-origin chromium chromium-browser google-chrome google-chrome-stable chrome microsoft-edge microsoft-edge-stable vivaldi opera; do
    c="$(command -v "$c" 2>/dev/null)" || continue
    chromium_is "$c" && return 0
  done
  return 1
}
chromium_smoke() {
  # Headless CDP handshake on a temp port + temp profile. Zero side effects.
  local bin="$1" port tmpd pid
  command -v curl >/dev/null 2>&1 || return 1
  port=$((20000 + RANDOM % 20000))
  tmpd="$(mktemp -d)" || return 1
  "$bin" --headless --no-first-run --remote-debugging-port="$port" \
    --remote-allow-origins="*" --user-data-dir="$tmpd" about:blank >/dev/null 2>&1 &
  pid=$!
  local i
  for i in $(seq 1 20); do
    if curl -sf --max-time 2 "http://127.0.0.1:$port/json/version" >/dev/null 2>&1; then
      kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; rm -rf "$tmpd"; return 0
    fi
    sleep 0.3
  done
  kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; rm -rf "$tmpd"
  return 1
}
pick_chromium() {
  local c
  TRANSLATE_BROWSER_PICK=""
  c="$(chromium_default_bin)" || true
  if chromium_is "$c" && chromium_smoke "$c"; then TRANSLATE_BROWSER_PICK="$c"; return 0; fi
  for c in brave brave-browser brave-origin chromium chromium-browser google-chrome google-chrome-stable chrome microsoft-edge microsoft-edge-stable vivaldi opera; do
    c="$(command -v "$c" 2>/dev/null)" || continue
    if chromium_is "$c" && chromium_smoke "$c"; then TRANSLATE_BROWSER_PICK="$c"; return 0; fi
  done
  return 1
}
translate_set_key() {
  python3 - "$ROOT/translate/config.json" "$1" "$2" <<'EOF'
import json, os, sys
p, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    d = json.load(open(p))
    if not isinstance(d, dict):
        d = {}
except (OSError, ValueError):
    d = {}
d[k] = v
os.makedirs(os.path.dirname(p), exist_ok=True)
json.dump(d, open(p, "w"), indent=2)
EOF
}

# ===========================================================================
# Textractor: vendor fetch + provisioning (merged from the old translate/ scripts)
# ===========================================================================
vendor_dl() { # <url> <sha256|""> <dest>
  local url="$1" sha="$2" dest="$3" tmp="$3.dl-tmp"
  if [ -f "$dest" ] && { [ -z "$sha" ] || [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$sha" ]; }; then
    note "cached: $(basename "$dest")"; return 0
  fi
  note "fetching $(basename "$dest")…"
  curl -fL --retry 3 -o "$tmp" "$url" || { rm -f "$tmp"; return 1; }
  if [ -n "$sha" ]; then
    [ "$(sha256sum "$tmp" 2>/dev/null | cut -d' ' -f1)" = "$sha" ] \
      || { warn "checksum MISMATCH: $dest"; rm -f "$tmp"; return 1; }
  fi
  mv "$tmp" "$dest"
}

install__fetch_vendor() {
  # Download pinned, checksum-verified translation binaries (never committed).
  local vdir="$VENDOR_DIR_DEFAULT" bridge="stock" all=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) vdir="${2:?--dir needs a path}"; shift 2 ;;
      --bridge) bridge="${2:?--bridge needs stock|fixed}"; shift 2 ;;
      --all) all=1; shift ;;
      *) err "fetch-vendor: unknown option $1"; return 2 ;;
    esac
  done
  mkdir -p "$vdir"

  local TRX_TAG="${TRX_TAG:-dev}" TRX_ZIP="Textractor_260801.zip"
  local TRX_URL="https://github.com/Chenx221/Textractor/releases/download/${TRX_TAG}/${TRX_ZIP}"
  local TRX_SHA="86346c71ba961e993b8b40419d8720204ccaf8fe20606bbd267eb765ba2ff2ef"
  local WS_TAG="${WS_TAG:-0.2.0}"
  local WS86_URL="https://github.com/kuroahna/textractor_websocket/releases/download/${WS_TAG}/textractor_websocket_x86.zip"
  local WS86_SHA="4e28ae0661433caa5d31904430650be1a81c47c80d63f5ddb0ba93470c150875"
  local WS64_URL="https://github.com/kuroahna/textractor_websocket/releases/download/${WS_TAG}/textractor_websocket_x64.zip"
  local WS64_SHA="16bbad511d0a43fa0e686144c4a507ec307745dba51f166fa822a094a26c75bf"

  # Textractor: pinned asset first; if upstream renamed/moved it, resolve the
  # latest release asset (unverified — warn loudly).
  if ! vendor_dl "$TRX_URL" "$TRX_SHA" "$vdir/$TRX_ZIP"; then
    warn "pinned Textractor asset unavailable ($TRX_ZIP @ $TRX_TAG)"
    local tag="" asset=""
    tag="$(gh_latest_tag Chenx221/Textractor || true)"
    if [ -n "$tag" ]; then
      asset="$(curl -sL --max-time 30 \
        "https://github.com/Chenx221/Textractor/releases/expanded_assets/${tag}" \
        | grep -oE 'Textractor[^"'"'"'/ ]*\.zip' | sort -u | tail -n1)"
    fi
    if [ -n "$asset" ]; then
      warn "falling back to latest asset: $asset (checksum UNVERIFIED)"
      vendor_dl "https://github.com/Chenx221/Textractor/releases/download/${tag}/${asset}" "" "$vdir/$asset" \
        || { err "Textractor download failed"; return 1; }
      TRX_ZIP="$asset"
    else
      err "no Textractor release asset found (network offline or upstream changed)"
      return 1
    fi
  fi
  # Both bridges: the game's bitness picks which one the session loads
  # (ak_pe_arch), so a 64-bit title needs the x64 build present.
  vendor_dl "$WS86_URL" "$WS86_SHA" "$vdir/textractor_websocket_x86.zip" || return 1
  vendor_dl "$WS64_URL" "$WS64_SHA" "$vdir/textractor_websocket_x64.zip" || return 1

  # Hardened bridge v2 (thread-tagged broadcast, needed by the in-app picker).
  if [ "$bridge" = "fixed" ]; then
    local FIXED_SHA="acc84db3227dc833a4895b6242c1e3fb0353bd87a43a730a39a15d853006b114"
    local TAG="${TRANSLATE_RELEASE_TAG:-translate-v2}"
    local FIXED_URL="https://github.com/Darkiu1337/migaki-linux/releases/download/${TAG}/textractor_websocket_x86.dll"
    if ! vendor_dl "$FIXED_URL" "$FIXED_SHA" "$vdir/textractor_websocket_x86.fixed.dll"; then
      warn "fixed bridge asset unavailable (release '${TAG}' not published?); using the stock bridge"
      note "publish the '${TAG}' release asset, or drop the DLL at $vdir/textractor_websocket_x86.fixed.dll"
    fi
    # x64 fork asset is optional/unpublished so far: best-effort, fall back to
    # the stock x64 bridge (loses the tagged picker on 64-bit titles).
    local FIXED64_URL="https://github.com/Darkiu1337/migaki-linux/releases/download/${TAG}/textractor_websocket_x64.dll"
    if ! vendor_dl "$FIXED64_URL" "" "$vdir/textractor_websocket_x64.fixed.dll"; then
      note "no x64 fixed bridge asset; 64-bit titles use the stock x64 bridge"
    fi
  fi

  # Extract into the layout install__provision_textractor consumes (idempotent).
  python3 - "$vdir" <<'PYEOF'
import glob, os, sys, zipfile
vdir = sys.argv[1]
def unzip(path, dest):
    if os.path.exists(dest):
        return
    with zipfile.ZipFile(path) as z:
        z.extractall(dest)
for trx in sorted(glob.glob(os.path.join(vdir, "Textractor*.zip"))):
    unzip(trx, os.path.join(vdir, "textractor-full"))
    break
for name in ("textractor_websocket_x86.zip", "textractor_websocket_x64.zip"):
    p = os.path.join(vdir, name)
    if os.path.exists(p):
        unzip(p, os.path.join(vdir, os.path.splitext(name)[0].replace("textractor_websocket_", "ws-")))
PYEOF
  note "vendor ready in $vdir"
}

install__provision_textractor() {
  # Provision Textractor for both architectures once per machine and link the
  # canonical dir into a Wine prefix. ak_pe_arch picks the build at launch
  # (x86 for 32-bit PE, x64 for 64-bit PE); a missing build self-heals here.
  local prefix="" bridge="stock" vdir="$VENDOR_DIR_DEFAULT" link=1
  while [ $# -gt 0 ]; do
    case "$1" in
      --prefix) prefix="${2:-}"; shift 2 ;;
      --bridge) bridge="${2:-stock}"; shift 2 ;;
      --vendor-dir) vdir="${2:-$VENDOR_DIR_DEFAULT}"; shift 2 ;;
      --no-link) link=0; shift ;;
      *) err "provision-textractor: unknown option $1"; return 2 ;;
    esac
  done
  if [ -z "$prefix" ]; then
    prefix="$(python3 -c "import json; print(json.load(open('$HOME/.config/migaki/config.json')).get('prefix', ''))" 2>/dev/null || true)"
    [ -n "$prefix" ] || prefix="$HOME/.local/share/migaki/prefixes/default"
  fi

  local HOME_DIR="${MIGAKI_TEXTTRACTOR_HOME:-$HOME/.local/share/migaki/textractor}"
  local CFG="$ROOT/translate/textractor-config"
  local ARCH SRC DST FIXED cand have_any=0
  for ARCH in x86 x64; do
    SRC="$vdir/textractor-full/$ARCH"
    if [ ! -d "$SRC" ]; then
      warn "missing $SRC (vendor fetch needed; skipping $ARCH)"
      continue
    fi
    DST="$HOME_DIR/$ARCH"
    mkdir -p "$DST"
    cp -r "$SRC/." "$DST/"
    cp -f "$SRC/Textractor.exe" "$SRC/TextractorCLI.exe" "$SRC/texthook.dll" "$DST/"

    # Textractor loads the REGISTERED copy (*.xdll), not the *.dll: both
    # filenames must carry the same build or sessions silently run the other.
    FIXED=""
    for cand in "$vdir/textractor_websocket_${ARCH}.fixed.dll" \
                "$vdir/bridge-fixed/textractor_websocket_${ARCH}.dll"; do
      [ -f "$cand" ] && { FIXED="$cand"; break; }
    done
    if [ "$bridge" = "fixed" ] && [ -n "$FIXED" ]; then
      cp -f "$FIXED" "$DST/textractor_websocket_${ARCH}.dll"
      cp -f "$FIXED" "$DST/textractor_websocket_${ARCH}.xdll"
      note "bridge ($ARCH): FIXED fork (sha256 $(sha256sum "$DST/textractor_websocket_${ARCH}.dll" | cut -c1-12))"
    elif [ -f "$vdir/ws-${ARCH}/textractor_websocket_${ARCH}.dll" ]; then
      cp -f "$vdir/ws-${ARCH}/textractor_websocket_${ARCH}.dll" "$DST/textractor_websocket_${ARCH}.dll"
      cp -f "$vdir/ws-${ARCH}/textractor_websocket_${ARCH}.dll" "$DST/textractor_websocket_${ARCH}.xdll"
      note "bridge ($ARCH): stock 0.2.0"
    else
      warn "no $ARCH bridge DLL in $vdir (translation stays silent for that arch)"
    fi
    if [ "$bridge" = "fixed" ] && [ -z "$FIXED" ] && [ "$ARCH" = "x86" ]; then
      note "the in-app Text Hooker picker needs v2 (or reveal Textractor via the wizard debug box)"
    fi

    # Bundled known-good config; Textractor.ini is a starting point only
    # (Textractor rewrites it on exit, so never clobber it).
    if [ -f "$CFG/Textractor.ini" ] && [ ! -f "$DST/Textractor.ini" ]; then
      cp -f "$CFG/Textractor.ini" "$DST/Textractor.ini"
    fi
    # Force bridge-only: the actual fix for the stock-extension stall.
    printf 'textractor_websocket_%s>' "$ARCH" > "$DST/SavedExtensions.txt"
    have_any=1
    ok "Textractor $ARCH -> $DST ($(ls "$DST" | wc -l) entries)"
  done
  if [ "$have_any" = "0" ]; then
    err "no Textractor arch could be provisioned (vendor fetch needed)"; return 1
  fi

  if [ "$link" = "1" ]; then
    local LNK="$prefix/drive_c/Textractor" f
    mkdir -p "$prefix/drive_c"
    # A legacy per-prefix (non-symlink) install migrates its x86 config first.
    if [ -d "$LNK" ] && [ ! -L "$LNK" ]; then
      for f in SavedHooks.txt SavedGames.txt SavedRegexFilters.txt Textractor.ini; do
        [ -f "$LNK/x86/$f" ] || continue
        if [ "$f" = "Textractor.ini" ] && [ -f "$HOME_DIR/x86/$f" ]; then continue; fi
        cp -f "$LNK/x86/$f" "$HOME_DIR/x86/$f"
      done
      rm -rf "$LNK"
    fi
    if [ -e "$LNK" ] || [ -L "$LNK" ]; then rm -rf "$LNK"; fi
    if ln -s "$HOME_DIR" "$LNK" 2>/dev/null; then
      note "linked prefix Textractor -> $HOME_DIR"
    else
      warn "symlink not supported here; copying Textractor into the prefix"
      mkdir -p "$LNK"
      cp -rn "$HOME_DIR/." "$LNK/"
      cp -f "$HOME_DIR/x86/Textractor.exe" "$LNK/x86/" 2>/dev/null || true
    fi
  fi
  for ARCH in x86 x64; do
    local D="$HOME_DIR/$ARCH"
    for f in Textractor.exe TextractorCLI.exe texthook.dll "textractor_websocket_${ARCH}.dll"; do
      [ -e "$D/$f" ] && ok "$ARCH/$f" || miss "$ARCH/$f"
    done
  done
}

run_hidden_mode() {
  case "$MODE" in
    fetch)
      local a=(--dir "$FETCH_DIR" --bridge "$HIDDEN_BRIDGE")
      [ "$FETCH_ALL" = "1" ] && a+=(--all)
      install__fetch_vendor "${a[@]}"
      ;;
    provision)
      if [ ! -d "$PROV_VDIR/textractor-full/x86" ]; then
        install__fetch_vendor --dir "$PROV_VDIR" --bridge "$HIDDEN_BRIDGE" || true
      fi
      local p=(--prefix "$PROV_PREFIX" --bridge "$HIDDEN_BRIDGE" --vendor-dir "$PROV_VDIR")
      [ "$PROV_LINK" = "0" ] && p+=(--no-link)
      install__provision_textractor "${p[@]}"
      ;;
    *) err "internal mode '$MODE' unknown"; return 1 ;;
  esac
}

# ===========================================================================
# Install steps
# ===========================================================================
missing=0

step_deps() {
  step "Dependencies"
  local tool pkgs arch_p deb_p fed_p p rest
  while IFS=: read -r tool pkgs; do
    tool="${tool//[[:space:]]/}"
    [ -z "$tool" ] && continue
    if command -v "$tool" >/dev/null 2>&1; then ok "$tool"; continue; fi
    miss "$tool"
    missing=1
    [ "$CHECK_ONLY" = "1" ] && continue
    arch_p="${pkgs%%|*}"; rest="${pkgs#*|}"; deb_p="${rest%%|*}"; fed_p="${rest##*|}"
    p="$(pm_col "$arch_p" "$deb_p" "$fed_p")"
    case "$p" in
      "@gum") if [ "$PM" = "deb" ]; then WANT_GUM_DEB=1; else add_pkg gum; fi ;;
      "@pip:"*) add_pip "${p#@pip:}" ;;
      ""|"-") add_note "$tool (see requirements.md)" ;;
      *) add_pkg $p ;;
    esac
  done <<EOF
 $DEPS
EOF

  # Python imports (no CLI to probe).
  local mod label a d f
  _pyimport() { # <module> <label> <arch> <deb> <fed>
    mod="$1"; label="$2"
    if python3 -c "import $1" 2>/dev/null; then ok "$label"; return 0; fi
    miss "$label"; missing=1
    [ "$CHECK_ONLY" = "1" ] && return 0
    a="$(pm_col "$3" "$4" "$5")"; [ -n "$a" ] && add_pkg $a
  }
  _pyimport websocket "python-websocket-client (translation bridge client)" \
    python-websocket-client python3-websocket python3-websocket-client
  _pyimport requests "python-requests (DeepL browser automation)" \
    python-requests python3-requests python-requests
  unset -f _pyimport

  # Chromium browser (DeepL CDP automation target).
  if chromium_any_present; then
    ok "chromium browser (translation CDP target)"
  else
    miss "chromium browser (translation needs one for DeepL automation)"
    missing=1
    if [ "$CHECK_ONLY" = "0" ]; then
      case "$PM" in
        arch|fed) add_pkg chromium ;;
        deb)
          if grep -qi ubuntu "$OS_RELEASE" 2>/dev/null; then
            add_note "Chromium browser (Ubuntu's chromium is a snap; install Brave/Chromium from a repo or .deb)"
          else
            add_pkg chromium
          fi ;;
        *) add_note "Chromium browser" ;;
      esac
    fi
  fi
}

step_gui_deps() {
  step "GUI dependencies"
  PYSIDE_PIP=0
  if [ -f "$ROOT/gui/app.py" ]; then
    if python3 -c "import PySide6" 2>/dev/null; then
      ok "PySide6"
    else
      miss "PySide6 (GUI)"; missing=1
      if [ "$CHECK_ONLY" = "0" ]; then
        case "$PM" in
          arch) add_pkg pyside6 ;;
          deb|fed) PYSIDE_PIP=1; add_pip PySide6; add_pkg python3-pip ;;
          *) add_note "PySide6 (pip install PySide6)" ;;
        esac
      fi
    fi
  fi
  if [ -f "$ROOT/gui/app.py" ] && [ "$PYSIDE_PIP" = "0" ]; then
    local qmldir qmlmiss m
    qmldir="$(python3 -c "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))" 2>/dev/null || true)"
    _qmlmod_ok() {
      [ -d "$1/$2" ] || return 1
      find "$1/$2" -maxdepth 1 \( -name 'qmldir' -o -name '*.so' \) -print -quit 2>/dev/null | grep -q .
    }
    qmlmiss=""
    if [ -z "$qmldir" ]; then
      qmlmiss="QtQuick Controls Layouts Dialogs Effects (no QML import path)"
    else
      for m in QtQuick QtQuick/Controls QtQuick/Layouts QtQuick/Dialogs QtQuick/Effects; do
        _qmlmod_ok "$qmldir" "$m" || qmlmiss="$qmlmiss ${m##*/}"
      done
    fi
    unset -f _qmlmod_ok
    if [ -z "$qmlmiss" ]; then
      ok "QtQuick QML modules (Controls/Layouts/Dialogs/Effects)"
    else
      miss "QML modules:$qmlmiss (the GUI and translation textbox need them)"; missing=1
      if [ "$CHECK_ONLY" = "0" ]; then
        case "$PM" in
          arch) add_pkg qt6-declarative ;;
          deb) for m in qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-layouts \
                       qml6-module-qtquick-dialogs qml6-module-qtquick-effects; do
                 pkg_available "$m" && add_pkg "$m"
               done ;;
          fed) pkg_available qt6-qtdeclarative && add_pkg qt6-qtdeclarative ;;
          *) add_note "Qt6 QML modules (see requirements.md)" ;;
        esac
      fi
    fi
    # KDE Quick Controls style (optional): follows kdeglobals; else Fusion.
    if [ -n "$qmldir" ] && [ -d "$qmldir/org/kde/desktop" ]; then
      ok "KDE Quick Controls style (org.kde.desktop)"
    else
      note "org.kde.desktop QML style not found — GUI/textbox fall back to Fusion"
      if [ "$CHECK_ONLY" = "0" ]; then
        case "$PM" in
          arch) pkg_available qqc2-desktop-style && add_pkg qqc2-desktop-style ;;
          deb)  pkg_available qqc2-desktop-style && add_pkg qqc2-desktop-style ;;
          fed)  for m in kf6-qqc2-desktop-style qqc2-desktop-style; do
                  pkg_available "$m" && { add_pkg "$m"; break; }
                done ;;
        esac
      fi
    fi
    # Desktop-native helpers the GUI/textbox use when present (optional).
    case "${XDG_CURRENT_DESKTOP:-${XDG_SESSION_DESKTOP:-}}" in
      *[Kk][Dd][Ee]*)
        command -v kdialog >/dev/null 2>&1 && ok "kdialog (native KDE file picker)" \
          || note "kdialog not found — the GUI falls back to its own file picker"
        if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
          { command -v qdbus6 >/dev/null 2>&1 || command -v qdbus >/dev/null 2>&1; } \
            && ok "qdbus (textbox Top via KWin)" \
            || note "qdbus/qdbus6 not found — textbox Top will be unavailable"
        fi ;;
      *[Gg][Nn][Oo][Mm][Ee]*)
        command -v gnome-extensions >/dev/null 2>&1 && ok "gnome-extensions (textbox Top)" \
          || note "gnome-extensions not found — textbox Top stays unsupported on GNOME Wayland" ;;
    esac
    unset qmldir qmlmiss m
  fi
  if [ "${#PIP_PKGS[@]}" -gt 0 ] && [ "$CHECK_ONLY" = "0" ]; then
    case "$PM" in deb|fed) add_pkg python3-pip ;; esac
  fi
}

step_umu() {
  step "Proton runner (umu-launcher)"
  if command -v umu-run >/dev/null 2>&1; then
    ok "umu-run"
  elif [ "$CHECK_ONLY" = "1" ]; then
    note "umu-run not found (needed for the proton runner; see requirements.md)"
  else
    miss "umu-run (proton runner)"
    case "$PM" in
      arch)
        if pacman -Si umu-launcher >/dev/null 2>&1; then
          add_pkg umu-launcher
          local present=0 p prov
          for p in lib32-nvidia-utils lib32-vulkan-radeon lib32-vulkan-intel lib32-vulkan-nouveau \
                   lib32-vulkan-swrast lib32-vulkan-virtio lib32-vulkan-broadcom lib32-vulkan-freedreno \
                   lib32-vulkan-panfrost lib32-vulkan-asahi lib32-vulkan-dzn lib32-vulkan-gfxstream \
                   lib32-vulkan-powervr; do
            pacman -Q "$p" >/dev/null 2>&1 && { present=1; break; }
          done
          if [ "$present" = "0" ]; then
            prov="$(lib32_provider_pkg)"
            [ -n "$prov" ] && add_pkg "$prov" \
              || add_note "lib32-vulkan-driver provider (pacman may ask; NVIDIA: lib32-nvidia-utils, AMD: lib32-vulkan-radeon, Intel: lib32-vulkan-intel)"
          fi
          unset present p prov
        else
          add_note "umu-launcher (enable [multilib] in /etc/pacman.conf, then re-run)"
        fi ;;
      deb) WANT_UMU_DEB=1 ;;
      fed) WANT_UMU_RPM=1 ;;
      *) add_note "umu-launcher (https://github.com/Open-Wine-Components/umu-launcher)" ;;
    esac
  fi
}

step_vkbasalt() {
  step "vkBasalt (filter runtime)"
  VKBASALT_ACTION=""
  if vkbasalt_layer_present; then
    ok "vkbasalt (system layer registered)"
  elif [ "$CHECK_ONLY" = "1" ]; then
    note "no system vkBasalt layer found (see requirements.md)"
  elif [ "$PM" = "arch" ]; then
    local vh=""
    command -v yay >/dev/null 2>&1 && vh=yay
    [ -z "$vh" ] && command -v paru >/dev/null 2>&1 && vh=paru
    if [ -n "$vh" ] && "$vh" -Si vkbasalt >/dev/null 2>&1; then
      VKBASALT_ACTION="aur"; VKBASALT_HELPER="$vh"
    else
      VKBASALT_ACTION="source"
      add_pkg meson ninja glslang spirv-headers vulkan-headers pkgconf gcc
    fi
    unset vh
  else
    if { [ "$PM" = "deb" ] && pkg_available vkbasalt && add_pkg vkbasalt; } \
       || { [ "$PM" = "fed" ] && pkg_available vkBasalt && add_pkg vkBasalt; }; then
      :
    else
      VKBASALT_ACTION="source"
      case "$PM" in
        deb) add_pkg meson ninja-build glslang-tools spirv-headers libvulkan-dev pkg-config build-essential ;;
        fed) add_pkg meson ninja-build glslang spirv-headers vulkan-headers pkgconf gcc gcc-c++ ;;
      esac
    fi
  fi
}

confirm_and_install_packages() {
  # One confirmation: print exactly what will be installed, then one transaction.
  [ "$CHECK_ONLY" = "0" ] || return 0
  local want_assets=0
  { [ -n "${WANT_GUM_DEB:-}" ] || [ -n "${WANT_UMU_DEB:-}" ] || [ -n "${WANT_UMU_RPM:-}" ]; } && want_assets=1
  if [ "${#PKGS[@]}" -gt 0 ] || [ "${#PIP_PKGS[@]}" -gt 0 ] || [ "$want_assets" = "1" ]; then
    printf '\n'
    if [ "$DRY_RUN" = "1" ]; then
      printf '%sWould install%s%s:\n' "$C_BOLD" "$C_RESET" "${PM_NAME:+ ($PM_NAME)}"
    else
      printf '%sThe following will be installed%s%s:\n' "$C_BOLD" "$C_RESET" "${PM_NAME:+ ($PM_NAME)}"
    fi
    [ "${#PKGS[@]}" -gt 0 ] && printf '  %s\n' "${PKGS[@]}"
    [ -n "${WANT_GUM_DEB:-}" ] && printf '  gum (.deb from charmbracelet/gum)\n'
    [ -n "${WANT_UMU_DEB:-}" ] && printf '  umu-launcher (.deb, matches your Debian/Ubuntu)\n'
    [ -n "${WANT_UMU_RPM:-}" ] && printf '  umu-launcher (.rpm, matches your Fedora)\n'
    [ "${#PIP_PKGS[@]}" -gt 0 ] && printf '  %s (pip)\n' "${PIP_PKGS[@]}"
    [ "$VKBASALT_ACTION" = "aur" ] && printf '  vkbasalt (AUR via %s)\n' "$VKBASALT_HELPER"
    [ "$VKBASALT_ACTION" = "source" ] && printf '  vkbasalt (build from source into ~/.local)\n'
    if [ "$DRY_RUN" = "1" ]; then
      printf '  (dry run: nothing installed)\n'
      return 0
    fi
    confirm "install all of the above?" || { note "skipped package install"; return 0; }
    local tmp; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    command -v curl >/dev/null 2>&1 || pm_install_batch curl || true
    if [ -n "${WANT_GUM_DEB:-}" ]; then
      local a=""
      if a="$(fetch_latest_asset charmbracelet/gum 'gum_[0-9.]+_amd64\.deb')" && download_asset charmbracelet/gum "$a" "$tmp"; then
        PKG_FILES+=("$tmp/$a")
      else
        add_note "gum (release .deb download failed; see requirements.md)"
      fi
      unset a
    fi
    if [ -n "${WANT_UMU_DEB:-}" ]; then
      local sfx re a
      sfx="$(umu_deb_suffix)"
      for re in "python3-umu-launcher_[^\"']*_amd64_${sfx}\.deb" "umu-launcher_[^\"']*_all_${sfx}\.deb"; do
        a="$(fetch_latest_asset Open-Wine-Components/umu-launcher "$re")" || continue
        download_asset Open-Wine-Components/umu-launcher "$a" "$tmp" && PKG_FILES+=("$tmp/$a")
      done
      [ "${#PKG_FILES[@]}" -gt 0 ] || add_note "umu-launcher (.deb download failed)"
      unset sfx re a
    fi
    if [ -n "${WANT_UMU_RPM:-}" ]; then
      local ver a
      ver="$(fedora_major)"
      a="$(fetch_latest_asset Open-Wine-Components/umu-launcher "umu-launcher-[^\"']*\.fc${ver}\.[^\"']*\.rpm")"
      if [ -n "$a" ] && download_asset Open-Wine-Components/umu-launcher "$a" "$tmp"; then
        PKG_FILES+=("$tmp/$a")
      else
        add_note "umu-launcher (.rpm for fc${ver} not found)"
      fi
      unset ver a
    fi
    pm_update
    if [ "${#PKGS[@]}" -gt 0 ] || [ "${#PKG_FILES[@]}" -gt 0 ]; then
      pm_install_batch "${PKGS[@]}" "${PKG_FILES[@]}" \
        || warn "package install reported an error (continuing; re-run to retry)"
    fi
    local p
    for p in "${PIP_PKGS[@]}"; do
      python3 -m pip install --user "$p" 2>/dev/null \
        || python3 -m pip install --user --break-system-packages "$p" 2>/dev/null \
        || warn "pip install $p failed (GUI may not start)"
    done
    unset p
    rm -rf "$tmp"; trap - EXIT; unset tmp
  fi
  if [ "${#NOTES[@]}" -gt 0 ]; then
    printf '\n%smanual installs still needed:%s\n' "$C_BOLD" "$C_RESET"
    printf '  %s\n' "${NOTES[@]}"
  fi
}

step_vkbasalt_build() {
  [ "$CHECK_ONLY" = "0" ] && [ "$DRY_RUN" = "0" ] || return 0
  ! vkbasalt_layer_present || return 0
  [ -n "$VKBASALT_ACTION" ] || return 0
  if [ "$VKBASALT_ACTION" = "aur" ]; then
    step "Building vkBasalt (AUR)"
    "$VKBASALT_HELPER" -S --needed --noconfirm --answerclean None --answerdiff None vkbasalt \
      || { warn "AUR install failed — falling back to source build."; VKBASALT_ACTION="source"; }
  fi
  if ! vkbasalt_layer_present && [ "$VKBASALT_ACTION" = "source" ]; then
    if [ "$PM" = "arch" ] || [ "$PM" = "deb" ] || [ "$PM" = "fed" ]; then
      step "Building vkBasalt from source (~/.local)"
      local vbdir; vbdir="$(mktemp -d)"; trap 'rm -rf "$vbdir"' EXIT
      git clone --depth 1 --recurse-submodules https://github.com/DadSchoorse/vkBasalt.git "$vbdir/vkbasalt" \
        || { err "clone failed"; exit 1; }
      export PATH="$HOME/.local/bin:$PATH"
      meson setup "$vbdir/vkbasalt/build" "$vbdir/vkbasalt" --prefix="$HOME/.local" \
        || { err "meson setup failed (missing build dep?)"; exit 1; }
      ninja -C "$vbdir/vkbasalt/build" || { err "build failed"; exit 1; }
      meson install -C "$vbdir/vkbasalt/build" || { err "install failed"; exit 1; }
      local vklib="$HOME/.local/lib/libvkbasalt.so"
      local vkjson="$HOME/.local/share/vulkan/implicit_layer.d/vkBasalt.json"
      [ -f "$vklib" ] || { err "expected $vklib after install"; exit 1; }
      [ -f "$vkjson" ] && sed -i -e "s|\"library_path\": *\"[^\"]*\"|\"library_path\": \"$vklib\"|" "$vkjson"
      unset vklib vkjson
      rm -rf "$vbdir"; trap - EXIT; unset vbdir
      mkdir -p "$HOME/.local/share/vkBasalt"
      touch "$HOME/.local/share/vkBasalt/.migaki-installed"
      mkdir -p "$HOME/.config/migaki"
      config_set_key layer_dir "$HOME/.local/share/vulkan/implicit_layer.d"
      ok "vkbasalt built + installed to ~/.local (layer_dir recorded in config.json)"
    else
      warn "install vkBasalt manually (see requirements.md), then re-run install.sh."
    fi
  fi
}

step_proton() {
  step "Proton (verified build)"
  local CACHYOS_PROTON_NAME="Proton-CachyOS Latest"
  local CACHYOS_PROTON_DIR="$HOME/.local/share/Steam/compatibilitytools.d/$CACHYOS_PROTON_NAME"
  if [ -x "$CACHYOS_PROTON_DIR/proton" ]; then
    ok "$CACHYOS_PROTON_NAME"
  elif [ "$CHECK_ONLY" = "1" ]; then
    note "$CACHYOS_PROTON_NAME not found (recommended; umu falls back to UMU-Proton)"
  elif [ "$DRY_RUN" = "1" ]; then
    note "would offer to install $CACHYOS_PROTON_NAME (~1GB)"
  elif confirm_no "install $CACHYOS_PROTON_NAME (verified Proton; ~1GB download)?"; then
    if install_cachyos_proton "$CACHYOS_PROTON_DIR" "$CACHYOS_PROTON_NAME"; then
      seed_proton_default "$CACHYOS_PROTON_DIR"
    else
      warn "$CACHYOS_PROTON_NAME install failed — continuing."
      note "umu will auto-fetch UMU-Proton on first launch; re-run install.sh to retry"
    fi
  else
    note "skipped (umu will auto-fetch UMU-Proton on first launch)"
  fi
}

install_cachyos_proton() { # <dest-dir> <name>
  local dest="$1" name="$2" t tag asset base v3="" tmp sha src
  for t in curl tar; do
    command -v "$t" >/dev/null 2>&1 || { err "'$t' needed for this step"; return 1; }
  done
  tag="$(gh_latest_tag CachyOS/proton-cachyos)"
  [ -n "$tag" ] || { err "could not resolve latest proton-cachyos release"; return 1; }
  note "latest proton-cachyos: $tag"
  if grep -q avx2 /proc/cpuinfo 2>/dev/null && grep -q bmi2 /proc/cpuinfo 2>/dev/null; then v3="_v3"; fi
  asset="proton-${tag}-x86_64${v3}.tar.xz"
  base="https://github.com/CachyOS/proton-cachyos/releases/download/${tag}"
  if ! curl -sfI --max-time 30 -o /dev/null "$base/$asset" >/dev/null 2>&1; then
    note "$asset not found, resolving from release page..."
    local archive_re="proton-cachyos-[0-9][^\"'/ ]*x86_64${v3}\.tar\.xz"
    asset="$(curl -sL --max-time 30 \
      "https://github.com/CachyOS/proton-cachyos/releases/expanded_assets/${tag}" \
      | grep -oE "$archive_re" | sort -u | tail -n1)"
    [ -n "$asset" ] || { err "no x86_64${v3} proton-cachyos asset for $tag"; return 1; }
  fi
  note "downloading $asset (~1GB, one time)..."
  tmp="$(mktemp -d)"
  curl -L -C - --retry 5 --retry-all-errors --speed-limit 10240 --speed-time 30 \
    -o "$tmp/$asset" "$base/$asset" || { err "download failed"; rm -rf "$tmp"; return 1; }
  [ -s "$tmp/$asset" ] || { err "empty download"; rm -rf "$tmp"; return 1; }
  sha="${asset%.tar.*}.sha512sum"
  if curl -sL --max-time 60 -o "$tmp/$sha" "$base/$sha"; then
    ( cd "$tmp" && sha512sum -c "$sha" ) \
      || { err "checksum mismatch (redownload or check the release page)"; rm -rf "$tmp"; return 1; }
  else
    warn "no checksum published, skipping verification"
  fi
  tar -xf "$tmp/$asset" -C "$tmp" || { err "extract failed"; rm -rf "$tmp"; return 1; }
  src="$(find "$tmp" -maxdepth 2 -name proton -type f | head -n 1)"
  [ -n "$src" ] || { err "extracted tree has no proton entrypoint"; rm -rf "$tmp"; return 1; }
  src="$(dirname "$src")"
  mkdir -p "$(dirname "$dest")"
  rm -rf "$dest"
  mv "$src" "$dest"
  rm -rf "$tmp"
  [ -x "$dest/proton" ] || { err "install landed wrong"; return 1; }
  ok "$name installed."
}

seed_proton_default() { # <dest-dir>
  python3 - "$HOME/.config/migaki/config.json" "$1" <<'EOF'
import json, os, sys
p, proton = sys.argv[1], sys.argv[2]
if not proton or not os.path.isdir(proton):
    sys.exit(0)
try:
    d = json.load(open(p))
    if not isinstance(d, dict):
        d = {}
except (OSError, ValueError):
    d = {}
if not d.get("proton"):
    d["proton"] = proton
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w"), indent=2)
    print("seeded config proton default: " + os.path.basename(proton))
EOF
}

step_rpgmaker() {
  step "RPGMaker runner (optional)"
  if command -v rpgmaker-linux >/dev/null 2>&1; then
    ok "rpgmaker-linux ($(rpgmaker-linux --version 2>/dev/null | head -n 1))"
  elif [ "$CHECK_ONLY" = "1" ] || [ "$DRY_RUN" = "1" ]; then
    note "rpgmaker-linux not installed (needed for the rpgmaker runner)"
  elif confirm_no "install rpgmaker-linux support? (downloads NW.js runtimes, several hundred MB)"; then
    local t
    for t in curl tar; do
      command -v "$t" >/dev/null 2>&1 || { err "'$t' needed for this step"; return 1; }
    done
    local arch ver tarball rurl rdir rsub
    arch="$(uname -m | sed -e 's/armv7l/armhf/g')"
    ver="${RPGMAKER_VERSION:-1.1.9}"
    tarball="rpgmakerlinux-${arch}-v${ver}.tar.gz"
    rurl="https://github.com/bakustarver/rpgmakermlinux-cicpoffs/releases/download/v${ver}/${tarball}"
    rdir="$(mktemp -d)"; trap 'rm -rf "$rdir"' EXIT
    note "downloading ${tarball}..."
    curl -fL --retry 3 -o "$rdir/$tarball" "$rurl" || { err "download failed"; exit 1; }
    [ -s "$rdir/$tarball" ] || { err "empty download"; exit 1; }
    tar -xf "$rdir/$tarball" -C "$rdir" || { err "extract failed"; exit 1; }
    rsub="$(find "$rdir" -maxdepth 1 -mindepth 1 -type d | head -n 1)"
    [ -x "$rsub/install.sh" ] || { err "bundled installer not found"; exit 1; }
    ( cd "$rsub" && ./install.sh ) || { err "rpgmaker-linux installer failed"; exit 1; }
    rm -rf "$rdir"; trap - EXIT
    command -v rpgmaker-linux >/dev/null 2>&1 && ok "rpgmaker-linux installed." \
      || warn "install finished but rpgmaker-linux not on PATH (restart shell?)"
  else
    note "skipped — proton/native runners are unaffected."
  fi
}

step_shaders() {
  step "Shaders (Restore + Clear)"
  local dst="$HOME/.local/share/gamescope/reshade/Shaders" f
  mkdir -p "$dst"
  for f in "$ROOT"/shaders/Anime4K_Restore_*.fx "$ROOT"/shaders/ClearColor.fx; do
    [ -f "$f" ] && cp "$f" "$dst/"
  done
  [ -f "$ROOT/shaders/presets.json" ] && cp "$ROOT/shaders/presets.json" "$dst/"
  ok "deployed to $dst"
}

step_config() {
  step "Configuration"
  mkdir -p "$HOME/.config/migaki"
  if [ ! -f "$HOME/.config/migaki/config.json" ]; then
    echo '{"_note":"optional overrides: prefix, proton, layer_dir, shader_dir, wow64"}' > "$HOME/.config/migaki/config.json"
    ok "seeded ~/.config/migaki/config.json"
  else
    ok "kept existing ~/.config/migaki/config.json"
  fi
}

step_textbox_exec() {
  [ -x "$ROOT/translate/textbox.py" ] && return 0
  miss "translate/textbox.py is not executable"
  missing=1
  [ "$CHECK_ONLY" = "0" ] && [ "$DRY_RUN" = "0" ] || return 0
  chmod +x "$ROOT/translate/textbox.py" && ok "fixed with chmod +x" || warn "chmod failed"
}

step_translation() {
  step "VN translation (Textractor + DeepL)"
  # Seed per-file settings from samples (first run only; never overwrite).
  if [ ! -f "$ROOT/translate/config.json" ] && [ -f "$ROOT/translate/config.json.sample" ]; then
    cp "$ROOT/translate/config.json.sample" "$ROOT/translate/config.json"
    ok "seeded translate/config.json"
  fi
  if [ ! -f "$ROOT/translate/translate.json" ] && [ -f "$ROOT/translate/translate.json.sample" ]; then
    cp "$ROOT/translate/translate.json.sample" "$ROOT/translate/translate.json"
    ok "seeded translate/translate.json"
  fi
  # Pick + smoke-test a Chromium for DeepL automation (never clobbers a valid pick).
  local cfg_browser
  cfg_browser="$(python3 -c "import json; print(json.load(open('$ROOT/translate/config.json')).get('brave_bin',''))" 2>/dev/null || true)"
  if chromium_is "$cfg_browser"; then
    ok "translation browser: $cfg_browser (configured)"
  elif pick_chromium; then
    translate_set_key brave_bin "$TRANSLATE_BROWSER_PICK"
    ok "translation browser: $TRANSLATE_BROWSER_PICK (CDP smoke-tested)"
  else
    warn "no Chromium browser found — DeepL automation needs one."
  fi
  unset cfg_browser TRANSLATE_BROWSER_PICK
  TRANSLATE_BRIDGE="${TRANSLATE_BRIDGE:-fixed}"
  if confirm "install VN translation support (Textractor hook + DeepL bridge)?"; then
    if install__fetch_vendor --bridge "$TRANSLATE_BRIDGE" \
       && install__provision_textractor --bridge "$TRANSLATE_BRIDGE"; then
      ok "translation support installed"
    else
      warn "translation install failed (see above); re-run install.sh to retry"
    fi
  else
    note "skipped translation support (re-run install.sh to add later)"
  fi
}

step_top() {
  step "Textbox always-on-top"
  local wayland=0 desk
  case "${XDG_SESSION_TYPE:-}" in wayland) wayland=1 ;; esac
  [ -n "${WAYLAND_DISPLAY:-}" ] && wayland=1
  desk="$(printf '%s' "${XDG_CURRENT_DESKTOP:-${XDG_SESSION_DESKTOP:-}}" | tr '[:lower:]' '[:upper:]')"
  if [ "$wayland" = "0" ]; then
    ok "X11 keep-above hint (native)"
    return 0
  fi
  case "$desk" in
    *HYPRLAND*)
      ok "Hyprland (hyprctl pin/bring-to-top)"
      command -v hyprctl >/dev/null 2>&1 || warn "hyprctl not found; Top will be unavailable" ;;
    *KDE*)
      ok "KDE KWin keep-above (script over qdbus6)"
      { command -v qdbus6 >/dev/null 2>&1 || command -v qdbus >/dev/null 2>&1; } \
        || warn "qdbus6 not found; Top will be unavailable" ;;
    *GNOME*)
      ok "GNOME Wayland (Shell extension required)"
      install_gnome_top_extension ;;
    *)
      note "unavailable on this Wayland compositor (Float still applies)" ;;
  esac
}

install_gnome_top_extension() {
  local src="$ROOT/translate/gnome-extension/vn-textbox-top@migaki"
  local dst="$HOME/.local/share/gnome-shell/extensions/vn-textbox-top@migaki"
  [ -d "$src" ] || return 0
  if ! command -v gnome-extensions >/dev/null 2>&1; then
    note "gnome-extensions CLI missing; textbox Top stays unsupported"
    return 0
  fi
  if [ "$CHECK_ONLY" = "1" ] || [ "$DRY_RUN" = "1" ]; then
    note "would install GNOME Shell extension vn-textbox-top@migaki"
    return 0
  fi
  if ! confirm_no "install the GNOME Shell extension for textbox always-on-top? (may need re-login)"; then
    note "skipped; textbox Top stays unsupported on GNOME Wayland"
    return 0
  fi
  # Remove the pre-rename extension (uuid changed with the project rename).
  local old="$HOME/.local/share/gnome-shell/extensions/vn-textbox-top@anime4k"
  if [ -d "$old" ]; then
    gnome-extensions disable vn-textbox-top@anime4k 2>/dev/null || true
    rm -rf "$old"
  fi
  mkdir -p "$dst"
  cp -f "$src/metadata.json" "$src/extension.js" "$dst/" 2>/dev/null || true
  if gnome-extensions enable vn-textbox-top@migaki 2>/dev/null; then
    ok "enabled vn-textbox-top@migaki (log out/in if it does not activate)"
  else
    note "installed; enable it: gnome-extensions enable vn-textbox-top@migaki"
  fi
}

step_migrate() {
  # One-time move of pre-rename (anime4k) state to the migaki paths. Runs only
  # when the old path exists and the new one does not, so it is safe to re-run.
  if [ "$CHECK_ONLY" = "1" ] || [ "$DRY_RUN" = "1" ]; then
    [ -d "$HOME/.config/anime4k" ] && \
      note "would migrate ~/.config, ~/.cache, ~/.local/share from anime4k to migaki"
    return 0
  fi
  local moved=0 pair old new
  for pair in \
    "$HOME/.config/anime4k:$HOME/.config/migaki" \
    "$HOME/.cache/anime4k:$HOME/.cache/migaki" \
    "$HOME/.local/share/anime4k:$HOME/.local/share/migaki"; do
    old="${pair%%:*}"; new="${pair#*:}"
    if [ -d "$old" ] && [ ! -e "$new" ]; then
      mv "$old" "$new" && moved=1
    fi
  done
  # vkBasalt source-build sentinel.
  local sdir="$HOME/.local/share/vkBasalt"
  if [ -f "$sdir/.anime4k-installed" ] && [ ! -f "$sdir/.migaki-installed" ]; then
    mv "$sdir/.anime4k-installed" "$sdir/.migaki-installed" && moved=1
  fi
  [ "$moved" = "1" ] && ok "migrated anime4k config/cache/prefixes to migaki"
  return 0
}

step_symlinks() {
  step "Command symlinks"
  [ "$SYMLINK" = "1" ] || { note "skipped (--no-symlink)"; return 0; }
  mkdir -p "$HOME/.local/bin"
  # Pre-rename symlinks are replaced by the migaki ones below.
  rm -f "$HOME/.local/bin/anime4k" "$HOME/.local/bin/anime4k-gui"
  # A moved/renamed checkout leaves dangling links (targets are absolute):
  # report before repointing so a broken migaki is never silent.
  local l p
  for l in migaki migaki-gui vn-launch vn-textbox vn-translate; do
    p="$HOME/.local/bin/$l"
    if [ -L "$p" ] && [ ! -e "$p" ]; then
      note "$l was dangling (-> $(readlink "$p")); repointing to $ROOT"
    fi
  done
  ln -sf "$ROOT/scripts/migaki" "$HOME/.local/bin/migaki"
  if [ -f "$ROOT/gui/app.py" ]; then
    ln -sf "$ROOT/gui/app.py" "$HOME/.local/bin/migaki-gui"
    ok "migaki, migaki-gui -> ~/.local/bin/"
  else
    ok "migaki -> ~/.local/bin/ (gui not built yet)"
  fi
  if [ -d "$ROOT/translate" ]; then
    ln -sf "$ROOT/translate/vn-launch.sh" "$HOME/.local/bin/vn-launch"
    ln -sf "$ROOT/translate/textbox.py" "$HOME/.local/bin/vn-textbox"
    ln -sf "$ROOT/translate/vn_translate.py" "$HOME/.local/bin/vn-translate"
    rm -f "$HOME/.local/bin/vn-textbox-qml"
    ok "vn-launch, vn-textbox, vn-translate -> ~/.local/bin/"
  fi
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
      note "~/.local/bin is not on your PATH."
      local rc
      for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
        if [ -f "$rc" ] && ! grep -q '\.local/bin' "$rc"; then
          if confirm "append ~/.local/bin to PATH in $rc?"; then
            printf '\n# added by migaki-linux install.sh\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
            note "added (restart your shell to take effect)"
          fi
        fi
      done ;;
  esac
}

step_desktop_entries() {
  [ "$DESKTOP" = "1" ] || return 0
  step "Desktop entry"
  mkdir -p "$HOME/.local/share/applications"
  # Drop the pre-rename entry + icon so nothing stale lingers.
  rm -f "$HOME/.local/share/applications/anime4k.desktop" \
        "$HOME/.local/share/applications/anime4k-gui.desktop" \
        "$HOME/.local/share/pixmaps/anime4k.png" \
        "$HOME/.local/share/icons/hicolor/scalable/apps/anime4k.svg"
  for s in 16 24 32 48 64 128 256; do
    rm -f "$HOME/.local/share/icons/hicolor/${s}x${s}/apps/anime4k.png"
  done
  if [ -f "$ROOT/gui/app.py" ]; then
    cat > "$HOME/.local/share/applications/migaki-gui.desktop" <<EOF2
[Desktop Entry]
Name=Migaki (GUI)
Comment=Launch games with real-time image restoration
Exec=$ROOT/gui/app.py
Icon=migaki
Terminal=false
Type=Application
Categories=Game;
StartupNotify=true
StartupWMClass=migaki-gui
EOF2
  fi

  # App icon: deploy the generated hicolor set (plus a pixmaps fallback) so
  # the entry above resolves Icon=migaki. Skipped on checkouts that predate
  # assets/icons/.
  if [ -d "$ROOT/assets/icons/png" ]; then
    for s in 16 24 32 48 64 128 256; do
      src="$ROOT/assets/icons/png/migaki-$s.png"
      [ -f "$src" ] || continue
      install -Dm644 "$src" \
        "$HOME/.local/share/icons/hicolor/${s}x${s}/apps/migaki.png"
    done
    [ -f "$ROOT/assets/icons/migaki.svg" ] && \
      install -Dm644 "$ROOT/assets/icons/migaki.svg" \
        "$HOME/.local/share/icons/hicolor/scalable/apps/migaki.svg"
    [ -f "$ROOT/assets/icons/png/migaki-256.png" ] && \
      install -Dm644 "$ROOT/assets/icons/png/migaki-256.png" \
        "$HOME/.local/share/pixmaps/migaki.png"
  fi

  command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

  ok "desktop entry + app icon installed"
}

# ===========================================================================
# Main
# ===========================================================================
main() {
  printf '\n%s%sMigaki%s %s— installer%s\n' \
    "$C_BOLD" "$C_CYAN" "$C_RESET" "$C_DIM" "$C_RESET"
  [ "$CHECK_ONLY" = "1" ] && note "check-only mode (no changes)"
  [ "$DRY_RUN" = "1" ] && note "dry-run mode (no changes)"
  [ -z "$PM_NAME" ] && warn "unknown distro family — package steps will be manual"

  step_migrate
  step_deps
  step_gui_deps
  step_umu
  step_vkbasalt
  confirm_and_install_packages
  step_vkbasalt_build
  step_proton
  step_rpgmaker
  step_textbox_exec

  if [ "$CHECK_ONLY" = "1" ]; then
    printf '\n'
    if [ "$missing" = "0" ]; then ok "all required tools present."; else err "missing tools (see above)."; exit 1; fi
    exit 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    printf '\ndry run complete (nothing was installed).\n'
    exit 0
  fi

  step_shaders
  step_config
  step_translation
  step_top
  step_symlinks
  step_desktop_entries

  printf '\n%s%s✓ Install complete%s\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
  printf '  Start the launcher:  %smigaki%s      (TUI)\n' "$C_CYAN" "$C_RESET"
  printf '  Graphical launcher:  %smigaki-gui%s\n' "$C_CYAN" "$C_RESET"
  printf '  Verify the chain:    %smigaki doctor%s\n' "$C_CYAN" "$C_RESET"
}

if [ "$MODE" != "install" ]; then
  if ! run_hidden_mode; then exit 1; fi
  exit 0
fi
main
