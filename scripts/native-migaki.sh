#!/bin/bash
# native-migaki.sh — run native Linux games (e.g. Ren'Py) with Anime4K Restore.
# Ren'Py and most native 2D titles render with OpenGL, which vkBasalt cannot
# hook directly. This script routes GL through Zink (OpenGL-on-Vulkan) so the
# game presents via a Vulkan swapchain that vkBasalt + Restore can process.
#
# Usage: native-migaki.sh [options] <executable> [args...]
#   With no executable given, a file picker opens (any file, not just .sh).
#   Ren'Py example: native-migaki.sh "/path/to/Game/Game.sh"
#
# Options:
#   --variant NAME                 Restore strength (S|M|L|Soft_S|Soft_M|Soft_L|VL|UL|Soft_VL|Soft_UL) or a
#                                  Clear 3D preset (Clear|Clear_Vivid|Clear_AA)
#   --gl zink|auto       zink = force translation+filter (default);
#                        auto = launch unfiltered if Zink misbehaves
#   --gpu nvidia|amd|intel|auto  Vulkan device under Zink (default: auto)
#   --fps N|off          MangoHud frame cap (default: 60; off disables)
#   --hud                show MangoHud overlay (fps readout)
#   --lang LOCALE        game locale, e.g. ja_JP.UTF-8 (empty = system default)
#   --dry-run            print the resolved launch command and exit
#   --help               this text
# Toggle filter off: DISABLE_VKBASALT=1 native-migaki.sh ...
# 64-bit titles only (matches our vkBasalt build).
set -e
_SRC="${BASH_SOURCE[0]}"
while [ -L "$_SRC" ]; do _SRC="$(readlink "$_SRC")"; case "$_SRC" in /*) :;; *) _SRC="$(dirname "${BASH_SOURCE[0]}")/$_SRC";; esac; done
SCRIPT_DIR="$(cd "$(dirname "$_SRC")" && pwd)"
unset _SRC
# shellcheck disable=SC1091
source "$SCRIPT_DIR/migaki-lib.sh"

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

VARIANT="L"
GLMODE="zink"
GPU="auto"
FPS="60"
HUD=0
LANG_SET=""
DRYRUN=0
EXE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --variant) VARIANT="$(ak_variant "${2:?--variant needs a variant name}")"; shift 2 ;;
    --gl) GLMODE="$2"; shift 2 ;;
    --gpu) GPU="$2"; shift 2 ;;
    --fps) FPS="$2"; shift 2 ;;
    --hud) HUD=1; shift ;;
    --lang) LANG_SET="$2"; shift 2 ;;
    --dry-run) DRYRUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; break ;;
    -*) if [ -n "$EXE" ]; then break; else ak_die "unknown option: $1 (see --help)"; fi ;;
    *) if [ -z "$EXE" ]; then EXE="$1"; shift; else break; fi ;;
  esac
done
ARGS=("$@")

if [ -z "$EXE" ]; then
  EXE="$(ak_pick_file 'Select game executable' '')"
  [ -n "$EXE" ] || ak_die "no game selected"
fi
[ -e "$EXE" ] || ak_die "game not found: $EXE"
# Resolve the real launch target: a Ren'Py distro handed to us as a Windows
# .exe (or as its folder) becomes the Linux .sh launcher.
if _tgt="$(ak_launch_target "$EXE")" && [ -n "$_tgt" ] && [ "$_tgt" != "$EXE" ]; then
  ak_log "resolved launch target: $_tgt"
  EXE="$_tgt"
fi
unset _tgt
[ -f "$EXE" ] || ak_die "not a runnable file: $EXE"
[ -x "$EXE" ] || ak_log "warning: '$EXE' is not marked executable, trying anyway"
case "$GLMODE" in
  zink|auto) ;;
  *) ak_die "--gl needs zink or auto" ;;
esac

if [ "$GLMODE" = "zink" ]; then
  ak_zink_env "$GPU"
fi
if [ -n "$LANG_SET" ]; then
  export LANG="$LANG_SET"
  if ! locale -a 2>/dev/null | grep -qi "^${LANG_SET%%.*}"; then
    ak_log "warning: locale $LANG_SET not generated on this system; if text looks wrong, add it to /etc/locale.gen and run: sudo locale-gen"
  fi
fi
ak_vkbasalt_env "$VARIANT"
ak_mangohud_env "$FPS" "$HUD"

if [ "$DRYRUN" = "1" ]; then
  echo "filter=$VARIANT gl=$GLMODE gpu=$GPU fps=$FPS hud=$HUD lang=${LANG_SET:-system}"
  printf '%q' "$EXE"
  if [ "${#ARGS[@]}" -gt 0 ]; then
    printf ' %q' "${ARGS[@]}"
  fi
  echo
  exit 0
fi

# A Windows binary can never exec here; fail with guidance, not "Exec format
# error" from the kernel (which confused a native-runner-on-a-.exe pick).
if ak_is_windows_pe "$EXE"; then
  ak_die "'$(basename "$EXE")' is a Windows executable — the native runner only runs Linux binaries. Pick the game's .sh launcher, or use the proton runner."
fi
# Trace only on request (it floods the GUI run log otherwise).
[ "${MIGAKI_DEBUG:-0}" = "1" ] && set -x
# Relaunch means takeover: clear surviving processes of this same game first.
# A Ren'Py .sh re-execs the same-stem engine binary, so match the stem too.
ak_kill_strays "$(basename "$EXE")"
case "$EXE" in
  *.sh) ak_kill_strays "$(basename "${EXE%.sh}")" ;;
esac
exec "$EXE" "${ARGS[@]}"
