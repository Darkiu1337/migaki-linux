#!/bin/bash
# proton-migaki.sh — run any Windows game under Proton + Anime4K Restore (vkBasalt).
# The filter engages for Vulkan-rendered games: DXVK (D3D9/10/11), VKD3D (D3D12),
# native Vulkan. OpenGL/software titles launch fine but stay unfiltered.
#
# Usage: proton-migaki.sh [options] <game.exe> [game args...]
#   With no exe given, a file picker opens.
#
# Options:
#   --variant NAME                       Restore strength (S|M|L|Soft_S|Soft_M|Soft_L|VL|UL|Soft_VL|Soft_UL)
#                                        or a Clear 3D preset (Clear|Clear_Vivid|Clear_AA)
#   --fps N|off          DXVK frame cap (default: 60; off disables)
#   --prefix DIR         Wine prefix (default: shared project prefix, see --prefix-mode)
#   --prefix-mode shared|game  shared ~/.local/share/migaki/prefixes/default
#                        versus per-game prefixes/<gameid> (default: shared)
#   --proton NAME        Proton runner name/path; "umu" forces the umu-managed
#                        UMU-Proton (default: config `proton`, else UMU-Proton)
#   --no-wow64           old WoW64 (default new WoW64: 32-bit D3D9 filters
#                        through the 64-bit layer; wine≥11/Proton; docs/limits.md)
#   --gameid ID          umu GAMEID (default: derived from exe name)
#   --dxvk-device NAME|auto   force DXVK onto a GPU (substring, e.g. "NVIDIA GeForce GTX 1650");
#                        default: discrete GPU when detectable ("auto" forces the loader default)
#   --vkd3d-device N     force VKD3D onto Vulkan device index N
#   --lang LOCALE        game locale, e.g. ja_JP.UTF-8 (some VNs require Japanese;
#                        empty = system default). Sets both HOST_LC_ALL and LANG:
#                        Proton 10+ starts Wine with LC_ALL=C, which would
#                        otherwise override LANG silently.
#   --hud                show MangoHud overlay (fps readout; frame cap stays DXVK)
#   --dry-run            print the resolved launch command and exit
#   --help               this text
# Toggle filter off: DISABLE_VKBASALT=1 proton-migaki.sh ...
set -e
_SRC="${BASH_SOURCE[0]}"
while [ -L "$_SRC" ]; do _SRC="$(readlink "$_SRC")"; case "$_SRC" in /*) :;; *) _SRC="$(dirname "${BASH_SOURCE[0]}")/$_SRC";; esac; done
SCRIPT_DIR="$(cd "$(dirname "$_SRC")" && pwd)"
unset _SRC
# shellcheck disable=SC1091
source "$SCRIPT_DIR/migaki-lib.sh"

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

VARIANT="L"
FPS="60"
HUD=0
PREFIX_FLAG=""
PROTON_FLAG=""
WOW64_FLAG=""
WOW64="1"
PMODE="shared"
PROTON=""
GAMEID=""
DXVK_DEV=""
VKD3D_DEV=""
LANG_SET=""
DRYRUN=0
EXE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --variant) VARIANT="$(ak_variant "${2:?--variant needs a variant name}")"; shift 2 ;;
    --fps) FPS="${2:?--fps needs a number or off}"; shift 2 ;;
    --hud) HUD=1; shift ;;
    --prefix) PREFIX_FLAG="$2"; shift 2 ;;
    --prefix-mode) PMODE="$2"; shift 2 ;;
    --proton) PROTON_FLAG="$2"; shift 2 ;;
    --wow64) WOW64_FLAG=1; shift ;;
    --no-wow64) WOW64_FLAG=0; shift ;;
    --gameid) GAMEID="$2"; shift 2 ;;
    --dxvk-device) DXVK_DEV="$2"; shift 2 ;;
    --vkd3d-device) VKD3D_DEV="$2"; shift 2 ;;
    --lang) LANG_SET="$2"; shift 2 ;;
    --dry-run) DRYRUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; break ;;
    -*) if [ -n "$EXE" ]; then break; else ak_die "unknown option: $1 (see --help)"; fi ;;
    *) if [ -z "$EXE" ]; then EXE="$1"; shift; else break; fi ;;
  esac
done
# remaining args after the exe are passed to the game
GAME_ARGS=("$@")

if [ -z "$EXE" ]; then
  EXE="$(ak_pick_file 'Select Windows game executable' 'Windows executables | *.exe *.EXE')"
  [ -n "$EXE" ] || ak_die "no game selected"
fi
[ -f "$EXE" ] || ak_die "game not found: $EXE"
case "$EXE" in
  *.exe|*.EXE) ;;
  *) ak_log "warning: '$EXE' does not look like a Windows executable, continuing anyway" ;;
esac
# Steam parity: game dir as cwd (relative-path titles need it) — resolve
# relative CLI paths before changing directory.
case "$EXE" in
  /*) : ;;
  *) EXE="$PWD/$EXE" ;;
esac
cd "$(dirname "$EXE")" || ak_die "cannot enter game dir: $(dirname "$EXE")"
ak_log "working directory: $PWD"
# NOTE: prefix existence is checked after resolution below (umu creates it).
UMU="$(command -v umu-run || true)"
[ -n "$UMU" ] || ak_die "umu-run not found on PATH (install umu-launcher; see requirements.md)"

if [ -z "$GAMEID" ]; then
  GAMEID="$(basename "$EXE" .exe)"
  GAMEID="$(basename "$GAMEID" .EXE)"
  GAMEID="$(printf '%s' "$GAMEID" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//;s/-$//')"
  [ -n "$GAMEID" ] || GAMEID="game"
fi

# Prefix: explicit flag > config > mode default (shared keeps one for all).
if [ -n "$PREFIX_FLAG" ]; then
  PREFIX="$PREFIX_FLAG"
elif [ -n "$(ak_config_get prefix "")" ]; then
  PREFIX="$(ak_config_get prefix "")"
elif [ "$PMODE" = "game" ]; then
  PREFIX="$HOME/.local/share/migaki/prefixes/$GAMEID"
elif [ "$PMODE" = "shared" ]; then
  PREFIX="$HOME/.local/share/migaki/prefixes/default"
else
  ak_die "--prefix-mode needs shared or game"
fi
if [ ! -d "$PREFIX" ]; then
  ak_log "prefix will be created on first launch: $PREFIX"
fi
# Proton: explicit flag > config file > umu-managed (unset = UMU-Proton auto).
# "--proton umu" forces the umu-managed UMU-Proton even when config names another.
case "$PROTON_FLAG" in
  umu|umu-managed|default) PROTON=""; unset PROTONPATH ;;
  "") PROTON="$(ak_config_get proton "")" ;;
  *) PROTON="$PROTON_FLAG" ;;
esac
# Resolve a path or bare build name to an absolute dir; otherwise fall back to
# umu-managed. A bare label would otherwise be exported as PROTONPATH and umu
# would fail to find Proton (game never boots).
if [ -n "$PROTON" ]; then
  _raw="$PROTON"
  if _resolved="$(ak_proton_resolve "$PROTON")"; then
    PROTON="$_resolved"
    [ "$PROTON" != "$_raw" ] && ak_log "proton: resolved '$_raw' -> '$PROTON'"
  else
    ak_log "warning: configured Proton '$_raw' not found — using umu-managed UMU-Proton"
    PROTON=""
    unset PROTONPATH
  fi
  unset _raw _resolved
fi
# WoW64: flag > config (default on) — docs/limits.md.
if [ -n "$WOW64_FLAG" ]; then WOW64="$WOW64_FLAG"; else WOW64="$(ak_config_get wow64 1)"; fi
# A selected Proton without new WoW64 support can't serve 32-bit titles: warn
# and fall back to the umu-managed UMU-Proton (docs/limits.md).
if [ "$WOW64" = "1" ] && [ -n "$PROTON" ] && ! ak_proton_wow64_capable "$PROTON"; then
  ak_log "warning: '$PROTON' has no new WoW64 support — falling back to umu-managed UMU-Proton"
  PROTON=""
  unset PROTONPATH
fi

export WINEPREFIX="$PREFIX"
[ -n "$PROTON" ] && export PROTONPATH="$PROTON"
export GAMEID
ak_wow64_env "$PREFIX" "$WOW64"
# Ren'Py: force ANGLE (DirectX) so it lands on DXVK (docs/limits.md);
# pre-set RENPY_RENDERER to override.
if [ -z "${RENPY_RENDERER:-}" ] && [ -d "$(dirname "$EXE")/renpy" ]; then
  export RENPY_RENDERER="angle2"
  ak_log "Ren'Py game detected: forcing ANGLE renderer (RENPY_RENDERER=angle2)"
fi
[ -n "$VKD3D_DEV" ] && export VKD3D_VULKAN_DEVICE="$VKD3D_DEV"
# Device: flag > inherited env > discrete GPU > loader default (docs/limits.md).
if [ -n "$DXVK_DEV" ]; then
  if [ "$DXVK_DEV" = "auto" ]; then
    unset DXVK_FILTER_DEVICE_NAME
    ak_log "DXVK: loader-default GPU (explicit --dxvk-device auto)"
  else
    export DXVK_FILTER_DEVICE_NAME="$DXVK_DEV"
  fi
elif [ -n "${DXVK_FILTER_DEVICE_NAME:-}" ]; then
  ak_log "DXVK: keeping inherited device filter '$DXVK_FILTER_DEVICE_NAME'"
elif _AK_DGPU="$(ak_discrete_gpu_name)"; then
  export DXVK_FILTER_DEVICE_NAME="$_AK_DGPU"
  ak_log "DXVK: auto-selected discrete GPU '$_AK_DGPU' (override with --dxvk-device)"
else
  ak_log "DXVK: no discrete GPU detected, using loader default (override with --dxvk-device)"
fi
unset _AK_DGPU
if [ -n "$LANG_SET" ]; then
  ak_locale_env "$LANG_SET"
fi
if [ "$FPS" != "off" ]; then
  [[ "$FPS" =~ ^[0-9]+$ ]] || ak_die "--fps needs a number or off"
  export DXVK_CONFIG="${DXVK_CONFIG:-dxgi.maxFrameRate = $FPS; d3d9.maxFrameRate = $FPS}"
fi
# Frame cap stays on DXVK here; MangoHud is overlay-only (--hud).
if [ "$HUD" = "1" ]; then
  ak_mangohud_env off 1
fi
ak_vkbasalt_env "$VARIANT"

if [ "$DRYRUN" = "1" ]; then
  echo "WINEPREFIX=$PREFIX PROTONPATH=${PROTON:-umu-managed} GAMEID=$GAMEID"
  echo "filter=$VARIANT fps=$FPS hud=$HUD lang=${LANG_SET:-system} wow64=$([ "$WOW64" = "1" ] && echo on || echo off) dxvk=${DXVK_FILTER_DEVICE_NAME:-loader-default} cwd=$PWD"
  printf 'umu-run %q' "$EXE"
  if [ "${#GAME_ARGS[@]}" -gt 0 ]; then
    printf ' %q' "${GAME_ARGS[@]}"
  fi
  echo
  exit 0
fi

# Takeover: clear surviving processes of this game before relaunch.
_ak_base="$(basename "$EXE")"
_ak_base="${_ak_base%.exe}"
_ak_base="${_ak_base%.EXE}"
ak_kill_strays "$_ak_base"
unset _ak_base

set -x
exec "$UMU" "$EXE" "${GAME_ARGS[@]}"
