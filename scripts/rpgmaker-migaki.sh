#!/bin/bash
# rpgmaker-migaki.sh — run RPGMaker games with Anime4K Restore (vkBasalt) where possible.
#
# Usage: rpgmaker-migaki.sh [options] --gamepath DIR
#   With no --gamepath given, a folder picker opens.
#
# RPGMaker MV/MZ (Chromium-based) get the full treatment: the wrapper's NW.js
# manifest is temporarily patched with Vulkan backend flags (restored on exit),
# the game runs through X11 ozone on the selected GPU, and vkBasalt applies Restore.
#
# Other engines (XP/VX/VXAce, Tyrano, Godot, ...) cannot use this path
# (no Chromium/Vulkan presentation). The script prints the proton-migaki.sh
# equivalent (those titles usually run fine under Proton, where RGSS's D3D9
# goes through DXVK and stays hookable) and launches unfiltered unless
# --no-fallback is given.
#
# Options:
#   --gamepath DIR       game folder (or omit for picker)
#   --variant NAME                 Restore strength (S|M|L|Soft_S|Soft_M|Soft_L|VL|UL|Soft_VL|Soft_UL) or a
#                                  Clear 3D preset (Clear|Clear_Vivid|Clear_AA)
#   --gpu nvidia|amd|intel|auto  Vulkan device for game+filter (default: auto)
#   --fps N|off          MangoHud frame cap (default: 60; off disables)
#   --hud                show MangoHud overlay (fps readout)
#   --nwjsversion VER    pass through to rpgmaker-linux (e.g. 0.115.0)
#   --translate          live VN translation: inject the page hook (the NW.js
#                        normal build has no CDP) and start the :6677 relay;
#                        composes with the filter. --gameid ID names the game.
#   --stop               end this game's session (game + translation bridge +
#                        injected hook)
#   --no-fallback        for non-Chromium games: print guidance and exit 1
#   --dry-run            print the resolved launch command and exit
#   --help               this text
# Toggle filter off: DISABLE_VKBASALT=1 rpgmaker-migaki.sh ...
set -e
_SRC="${BASH_SOURCE[0]}"
while [ -L "$_SRC" ]; do _SRC="$(readlink "$_SRC")"; case "$_SRC" in /*) :;; *) _SRC="$(dirname "${BASH_SOURCE[0]}")/$_SRC";; esac; done
SCRIPT_DIR="$(cd "$(dirname "$_SRC")" && pwd)"
unset _SRC
# shellcheck disable=SC1091
source "$SCRIPT_DIR/migaki-lib.sh"

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

VARIANT="L"
GAMEPATH=""
GPU="auto"
NWJSVER=""
NOFALLBACK=0
FPS="60"
HUD=0
DRYRUN=0
TRANSLATE=0
GAMEID=""
CMD_MODE="run"

while [ $# -gt 0 ]; do
  case "$1" in
    --gamepath) GAMEPATH="$2"; shift 2 ;;
    --variant) VARIANT="$(ak_variant "${2:?--variant needs a variant name}")"; shift 2 ;;
    --gpu) GPU="$2"; shift 2 ;;
    --nwjsversion) NWJSVER="$2"; shift 2 ;;
    --fps) FPS="$2"; shift 2 ;;
    --hud) HUD=1; shift ;;
    --translate) TRANSLATE=1; shift ;;
    --gameid) GAMEID="$2"; shift 2 ;;
    --stop) CMD_MODE="stop"; shift ;;
    --no-fallback) NOFALLBACK=1; shift ;;
    --dry-run) DRYRUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    -*) ak_die "unknown option: $1 (see --help)" ;;
    *) ak_die "unexpected argument: $1 (use --gamepath DIR)" ;;
  esac
done

if [ -z "$GAMEPATH" ]; then
  GAMEPATH="$(ak_pick_dir 'Select RPGMaker game folder')"
  [ -n "$GAMEPATH" ] || ak_die "no game selected"
fi
if [ -f "$GAMEPATH" ]; then
  ak_log "got a file, not a folder: using parent $(dirname "$GAMEPATH") as game root"
  GAMEPATH="$(dirname "$GAMEPATH")"
fi
[ -d "$GAMEPATH" ] || ak_die "game folder not found: $GAMEPATH"
ak_need rpgmaker-linux
ak_mangohud_env "$FPS" "$HUD"

# --- translation plumbing: injected page hook + native :6677 relay ----------
# The bundled NW.js is the normal build (no CDP), so we inject via the wrapper's
# plugins_autoload dir, which menu.js executes in the page. The page hook pushes
# tagged lines to the relay; the textbox consumes them like any other bridge.
TRANSLATE_DIR="$(dirname "$SCRIPT_DIR")/translate"
RPG_HOOK_SRC="$TRANSLATE_DIR/rpgmaker_hook.js"
RPG_AUTOLOAD=""; RPG_HOOK_DST=""; RPG_BRIDGE_PID=""

rpg_autoload_dir() {
  local tpl
  tpl="$(ak_rpgmaker_template)" || return 1
  printf '%s/jspatches/plugins_autoload' "$(dirname "$tpl")"
}
rpg_remove_hook() {
  local d
  d="$(rpg_autoload_dir 2>/dev/null || true)"
  [ -n "$d" ] && rm -f "$d/migaki_hook.js"
}
rpg_stop_bridge() {
  pkill -f "[r]pgmaker_bridge.py" 2>/dev/null || true
}
rpg_translate_start() {
  RPG_AUTOLOAD="$(rpg_autoload_dir)" || return 1
  RPG_HOOK_DST="$RPG_AUTOLOAD/migaki_hook.js"
  [ -f "$RPG_HOOK_SRC" ] || ak_die "translation hook missing: $RPG_HOOK_SRC"
  mkdir -p "$RPG_AUTOLOAD"
  cp -f "$RPG_HOOK_SRC" "$RPG_HOOK_DST"
  rpg_stop_bridge   # never two relays on :6677
  mkdir -p "$HOME/.cache/migaki"
  nohup python3 "$TRANSLATE_DIR/rpgmaker_bridge.py" --port 6677 --gameid "$GAMEID" \
        >>"$HOME/.cache/migaki/rpgmaker-bridge.log" 2>&1 &
  RPG_BRIDGE_PID=$!
  ak_log "translation: hook installed, bridge on :6677 (pid $RPG_BRIDGE_PID)"
}
rpg_translate_cleanup() {
  [ -n "$RPG_HOOK_DST" ] && rm -f "$RPG_HOOK_DST"
  [ -n "$RPG_BRIDGE_PID" ] && kill "$RPG_BRIDGE_PID" 2>/dev/null || true
  RPG_BRIDGE_PID=""
}

if [ "$CMD_MODE" = "stop" ]; then
  ak_kill_strays "nw --ozone-platform"
  rpg_stop_bridge
  rpg_remove_hook
  ak_log "stop: rpgmaker session ended"
  exit 0
fi

# A hook left behind by a crash must not load into an unrelated game.
rpg_remove_hook

# Engine sniff via the shared detector (MV/MZ incl. www/ depth normalization).
_DETECT="$(ak_detect_engine "$GAMEPATH")"
ENGINE="${_DETECT%%|*}"; _rest="${_DETECT#*|}"
_RUNNER="${_rest%%|*}"; _rest="${_rest#*|}"
_CONF="${_rest%%|*}"; _rest="${_rest#*|}"
GAMEPATH="${_rest%%|*}"; DETAIL="${_rest#*|}"
unset _DETECT _rest _RUNNER _CONF
ak_log "engine: $ENGINE — $DETAIL"
if [ "$ENGINE" != "rpgmaker-mv" ]; then
  EXE_CANDIDATE="$(find "$GAMEPATH" -maxdepth 1 -iname 'Game.exe' | head -n 1)"
  ak_log "not an MV/MZ (Chromium) game: vkBasalt filtering is not possible here."
  ak_log "(if you picked a www/ subfolder, try its parent folder instead.)"
  if [ "$TRANSLATE" = "1" ]; then
    ak_log "warning: translation needs a Chromium (MV/MZ) title; launching untranslated."
  fi
  if [ -n "$EXE_CANDIDATE" ]; then
    ak_log "this title usually runs fine under Proton instead, try:"
    ak_log "  proton-migaki.sh \"$EXE_CANDIDATE\""
  fi
  if [ "$NOFALLBACK" = "1" ]; then
    exit 1
  fi
  ak_log "launching unfiltered via rpgmaker-linux..."
  if [ "$DRYRUN" = "1" ]; then
    echo "rpgmaker-linux --gamepath '$GAMEPATH'"
    exit 0
  fi
  set -x
  # NOTE: no exec so future traps survive; nothing shared is modified on this path.
  rpgmaker-linux --gamepath "$GAMEPATH"
  exit $?
fi

# --- Chromium (MV/MZ) path: temporary Vulkan-flag patch, restored on exit ---
cleanup() { ak_template_restore; rpg_translate_cleanup; }
trap cleanup EXIT INT TERM
ak_template_patch
if [ "$TRANSLATE" = "1" ] && [ "$DRYRUN" != "1" ]; then
  rpg_translate_start
fi

# Stale Chromium singleton locks (from killed runs) break startup; clear them.
rm -f "$HOME/.config/RPG Maker MV/MZ (cicpoffs mount)/Singleton"* 2>/dev/null || true

# X11 ozone + Vulkan device for game+filter. ICD file names vary by distro.
export XDG_SESSION_TYPE=x11
case "${GPU:-auto}" in
  nvidia|amd|intel)
    # Chromium/ANGLE-Vulkan only works on X11 ozone here, and X11 belongs to a
    # single GPU: a forced GPU other than the display one cannot present a
    # swapchain (its GPU process fails vkCreateSwapchainKHR, the game drops to
    # Canvas2D — no filter, no overlay, no WebGL). Use the display GPU unless
    # explicitly forced.
    _disp="$(ak_display_gpu_vendor || true)"
    if [ -z "${MIGAKI_FORCE_GPU:-}" ] && [ -n "$_disp" ] && [ "$_disp" != "$GPU" ]; then
      ak_log "warning: '$GPU' cannot present this Chromium/Vulkan window — X11 is on '$_disp'."
      ak_log "         forcing it drops the filter, the overlay and WebGL."
      ak_log "         using the display GPU ($_disp); set MIGAKI_FORCE_GPU=1 to override."
      GPU="$_disp"
    fi
    if _icd="$(ak_icd_file "$GPU")"; then
      export VK_ICD_FILENAMES="$_icd"
    else
      ak_log "warning: no $GPU Vulkan ICD found; using the loader default"
      unset VK_ICD_FILENAMES
    fi
    unset _icd _disp
    ;;
  auto) unset VK_ICD_FILENAMES ;;
  *) ak_die "--gpu needs nvidia, amd, intel or auto" ;;
esac
ak_vkbasalt_env "$VARIANT"

CMD=(rpgmaker-linux)
[ -n "$NWJSVER" ] && CMD+=(--nwjsversion "$NWJSVER")
CMD+=(--gamepath "$GAMEPATH")

if [ "$DRYRUN" = "1" ]; then
  echo "filter=$VARIANT engine=mv-mz gpu=$GPU fps=$FPS hud=$HUD translate=$TRANSLATE (template patched temporarily)"
  if [ "$TRANSLATE" = "1" ]; then
    echo "inject: $(rpg_autoload_dir)/migaki_hook.js"
    echo "relay:  rpgmaker_bridge.py --port 6677 --gameid ${GAMEID:-<id>}"
  fi
  printf '%q ' "${CMD[@]}"
  echo
  cleanup
  trap - EXIT INT TERM
  exit 0
fi

set -x
# No exec (EXIT trap restores the template); clear stale NW.js runtimes first
# or a single-instance stale process would hijack this launch.
ak_kill_strays "nw --ozone-platform"
"${CMD[@]}"
status=$?
cleanup
trap - EXIT INT TERM
exit $status
