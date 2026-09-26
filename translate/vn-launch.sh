#!/bin/bash
# vn-launch.sh — universal single-container VN launcher (replaces mlove-hook.sh).
# One umu-run -> wscript -> per-game launch.vbs (hooker + game, one wineserver).
# Filter (Anime4K Restore via vkBasalt) is applied in-process when --filter is
# given, by sourcing migaki-lib.sh — the same mechanism as proton-migaki.sh,
# so filter + translation compose in one launch.
# Usage: vn-launch.sh --game ID [--setup] [--show-hooker] [--filter VARIANT|off] [--engine auto|textractor|tyrano] [--dry-run]
#                      | --exe PATH --gameid ID [--lang LOCALE] [--hook-code CODE] [--setup] [--show-hooker] [--filter ...] [--engine ...] [--dry-run]
#                      | --stop ID | --stop-exe PATH | --status | --list
#   --exe bypasses the games registry (used by migaki GUI/TUI: games.json is
#     the single registry there). --gameid/--lang default sanely with --exe.
#     --hook-code seeds Textractor's SavedHooks so the recorded hook
#     auto-inserts at attach (no manual Add-hook).
#   --engine auto (default) sniffs the target: Electron/TyranoScript bundles
#     take the CDP DOM hook (tyrano_hook.py, --cdp-port) because Textractor
#     cannot hook Chromium; everything else uses Textractor, with the exe's
#     PE bitness picking the x86/x64 build. Force with textractor|tyrano.
#   --setup  Setup mode: opens the in-app Text Hooker picker. Textractor stays
#            HIDDEN; --show-hooker reveals Textractor's window (debug).
#            Default (play mode): Textractor HIDDEN (style 0), game normal.
set -e
# install.sh symlinks vn-launch into ~/.local/bin; resolve the real path or
# HERE points at the link and migaki-lib.sh / the .vbs template vanish.
_SRC="${BASH_SOURCE[0]}"
while [ -L "$_SRC" ]; do
  _DIR="$(cd "$(dirname "$_SRC")" && pwd)"
  _SRC="$(readlink "$_SRC")"
  case "$_SRC" in /*) : ;; *) _SRC="$_DIR/$_SRC" ;; esac
done
HERE="$(cd "$(dirname "$_SRC")" && pwd)"
unset _SRC _DIR
REG="$HERE/translate.json"
# Bridge build to provision on self-heal (fixed = tagged/v2; install.sh's
# internal provisioner falls back to stock when the asset is absent).
BRIDGE="${TRANSLATE_BRIDGE:-fixed}"
# Shared core (config, variants, vkBasalt, WoW64). Sourced early so --no-wow64
# applies even with --filter off; docs/limits.md.
AK_LIB="$(dirname "$HERE")/scripts/migaki-lib.sh"
if [ -f "$AK_LIB" ]; then
  # shellcheck disable=SC1090
  source "$AK_LIB"
fi
GAME=""; SETUP=0; SHOW_HOOKER=0; CMD="launch"; FILTER="off"; DRYRUN=0
EXE_FLAG=""; GAMEID_FLAG=""; LANG_FLAG=""; HOOKCODE_FLAG=""; WOW64_FLAG=""
ENGINE_OPT=""; CDP_PORT="9223"
while [ $# -gt 0 ]; do
  case "$1" in
    --game) GAME="$2"; shift 2 ;;
    --exe) EXE_FLAG="$2"; shift 2 ;;
    --gameid) GAMEID_FLAG="$2"; shift 2 ;;
    --lang) LANG_FLAG="$2"; shift 2 ;;
    --hook-code) HOOKCODE_FLAG="$2"; shift 2 ;;
    --engine) ENGINE_OPT="$2"; shift 2 ;;
    --cdp-port) CDP_PORT="$2"; shift 2 ;;
    --setup) SETUP=1; shift ;;
    --show-hooker) SHOW_HOOKER=1; shift ;;
    --filter) FILTER="$2"; shift 2 ;;
    --dry-run) DRYRUN=1; shift ;;
    --stop) GAME="$2"; CMD="stop"; shift 2 ;;
    --stop-exe) EXE_FLAG="$2"; CMD="stop-exe"; shift 2 ;;
    --status) CMD="status"; shift ;;
    --list) CMD="list"; shift ;;
    --prefix) PREFIX_OVERRIDE="$2"; shift 2 ;;
    --wow64) WOW64_FLAG=1; shift ;;
    --no-wow64) WOW64_FLAG=0; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

jget() { python3 -c "import json; print(json.load(open('$REG'))$1)" 2>/dev/null; }

need_reg() { # need_reg <cmd>: the games registry only ships as a sample
  if [ ! -f "$REG" ]; then
    echo "$1: no games registry at $REG" >&2
    echo "hint: cp $HERE/translate.json.sample $REG, then edit exe paths (or launch via --exe/--gameid from the GUI/TUI)" >&2
    exit 1
  fi
}

if [ "$CMD" = "list" ]; then
  need_reg "list"
  python3 -c "import json; [print(g['id']+' — '+g.get('name',g['id'])) for g in json.load(open('$REG'))['games']]"
  exit 0
fi

PREFIX="$(python3 -c "import json; print(json.load(open('$HOME/.config/migaki/config.json')).get('prefix', ''))" 2>/dev/null || true)"
[ -n "$PREFIX" ] || PREFIX="$HOME/.local/share/migaki/prefixes/default"
[ -n "${PREFIX_OVERRIDE:-}" ] && PREFIX="$PREFIX_OVERRIDE"
PROTON="$(python3 -c "import json; print(json.load(open('$HOME/.config/migaki/config.json')).get('proton', ''))" 2>/dev/null || true)"
# Resolve a path or bare build name to an absolute dir; otherwise fall back to
# umu-managed. A bare label would otherwise be exported as PROTONPATH and umu
# would fail to find Proton (game never boots, bridge stays down).
if [ -n "$PROTON" ] && command -v ak_proton_resolve >/dev/null 2>&1; then
  _raw="$PROTON"
  if _resolved="$(ak_proton_resolve "$PROTON")"; then
    PROTON="$_resolved"
    [ "$PROTON" != "$_raw" ] && echo "proton: resolved '$_raw' -> '$PROTON'" >&2
  else
    echo "warning: configured Proton '$_raw' not found — using umu-managed UMU-Proton" >&2
    PROTON=""
    unset PROTONPATH
  fi
  unset _raw _resolved
fi
if [ -n "$WOW64_FLAG" ]; then
  WOW64="$WOW64_FLAG"
elif command -v ak_config_get >/dev/null 2>&1; then
  WOW64="$(ak_config_get wow64 1)"
else
  WOW64="1"
fi
# A selected Proton without new WoW64 support falls back to umu-managed.
if [ "$WOW64" = "1" ] && [ -n "$PROTON" ] \
   && command -v ak_proton_wow64_capable >/dev/null 2>&1 \
   && ! ak_proton_wow64_capable "$PROTON"; then
  echo "warning: '$PROTON' has no new WoW64 support — falling back to umu-managed UMU-Proton" >&2
  PROTON=""
  unset PROTONPATH
fi
UMU="$(command -v umu-run)" || { echo "umu-run not found" >&2; exit 1; }

if [ "$CMD" = "status" ]; then
  "$HERE/watch-bridge.sh" --once
  exit $?
fi

[ -n "$GAME" ] || [ -n "$EXE_FLAG" ] || { echo "need --game ID or --exe PATH (see --list)" >&2; exit 1; }
if [ -n "$EXE_FLAG" ]; then
  EXE="$EXE_FLAG"
  GAMEID="${GAMEID_FLAG:-game}"
  LANG_SET="${LANG_FLAG:-ja_JP.UTF-8}"
  [ -n "$GAME" ] || GAME="$GAMEID"
else
  need_reg "--game"
  EXE="$(python3 -c "import json; print([g for g in json.load(open('$REG'))['games'] if g['id']=='$GAME'][0]['exe'])")"
  GAMEID="$(python3 -c "import json; print([g for g in json.load(open('$REG'))['games'] if g['id']=='$GAME'][0].get('gameid','$GAME'))")"
  LANG_SET="$(python3 -c "import json; print([g for g in json.load(open('$REG'))['games'] if g['id']=='$GAME'][0].get('lang','ja_JP.UTF-8'))")"
  [ -n "$HOOKCODE_FLAG" ] || HOOKCODE_FLAG="$(python3 -c "import json; print([g for g in json.load(open('$REG'))['games'] if g['id']=='$GAME'][0].get('hook_code',''))")"
fi
BASE="$(basename "$EXE")"
BASE_NOEXT="${BASE%.exe}"; BASE_NOEXT="${BASE_NOEXT%.EXE}"

# Engine + Textractor arch. Tyrano/Electron titles are not GDI-hookable via
# Textractor (their text lives in Chromium), so they take the CDP DOM hook
# (tyrano_hook.py). Everything else uses Textractor, and the exe's PE bitness
# picks the x86/x64 build (docs/translate.md).
ENGINE="$ENGINE_OPT"
if [ -z "$ENGINE" ]; then
  _det="$(ak_detect_engine "$EXE" 2>/dev/null || true)"
  case "${_det%%|*}" in
    electron|tyrano) ENGINE="tyrano" ;;
    *) ENGINE="textractor" ;;
  esac
fi
case "$ENGINE" in
  auto) ENGINE="textractor" ;;
  textractor|tyrano) ;;
  *) echo "error: --engine needs auto|textractor|tyrano" >&2; exit 1 ;;
esac
ARCH="$(ak_pe_arch "$EXE")"
TRX=""
if [ "$ENGINE" = "textractor" ]; then
  # Provision Textractor into the canonical dir + this prefix and force the
  # bridge-only extension set (docs/translate.md). Self-heals prefixes umu only
  # creates on first launch. --dry-run stays side-effect free.
  TRX="$PREFIX/drive_c/Textractor/$ARCH/Textractor.exe"
  if [ "$DRYRUN" != "1" ] && [ "$CMD" = "launch" ]; then
    if ! command -v ak_textractor_ensure >/dev/null 2>&1; then
      echo "error: migaki-lib.sh not found at $AK_LIB" >&2; exit 1
    fi
    TRX="$(ak_textractor_ensure "$PREFIX" "$BRIDGE" "$ARCH")" \
      || { echo "error: Textractor provisioning failed (re-run ./install.sh)" >&2; exit 1; }
    [ -n "$TRX" ] && [ -f "$TRX" ] \
      || { echo "error: Textractor missing after provisioning: ${TRX:-<none>}" >&2; exit 1; }
  fi
fi

stop_session() { # stop_session <label>: kill game exes, wscript, stale wineserver
  # Match the Wine-side argv form (X:\dir\game.exe); a backslash-anchored
  # pattern can never match a unix path, so it cannot kill our own shell.
  local label="$1"
  local esc="${BASE//./\\.}" pat1
  pat1="[\\\\]${esc:1}"
  pkill -f "$pat1" 2>/dev/null || true
  sleep 3
  pkill -9 -f "$pat1" 2>/dev/null || true
  pkill -f "[w]script.exe C" 2>/dev/null || true
  # The hook transports outlive the game otherwise: Textractor (Windows-side)
  # and tyrano_hook.py (CDP DOM hook). Kill both so the next launch is clean.
  pkill -f "[T]extractor.exe" 2>/dev/null || true
  pkill -f "[t]yrano_hook.py" 2>/dev/null || true
  sleep 5
  if pgrep -f "$pat1" >/dev/null 2>&1; then echo "stop: processes remain"; exit 1; fi
  # Drop lingering wineserver or the next launch wedges (docs/translate.md).
  # Use the configured Proton's wineserver, else any installed build, else PATH.
  _ws="$(ak_wineserver_bin "${PROTON:-}" || true)"
  if [ -n "$_ws" ]; then
    WINEPREFIX="$PREFIX" "$_ws" -k 2>/dev/null || true
  else
    echo "note: no wineserver found to drop; if the next launch stalls, run: wineserver -k" >&2
  fi
  unset _ws
  echo "stop: $label session ended"
}

if [ "$CMD" = "stop" ] || [ "$CMD" = "stop-exe" ]; then
  # Kill this game's processes only (bracketed patterns never match self).
  stop_session "$GAME"
  exit 0
fi

# --- launch ---
[ -f "$EXE" ] || { echo "game not found: $EXE" >&2; exit 1; }
if [ "$DRYRUN" != "1" ]; then mkdir -p "$PREFIX/drive_c/hook"; fi
seed_saved_hooks() { # <wine-exe-path> <hook-code>: Textractor auto-attach
  # Seeded lines never clobber a richer user-saved hook (docs/translate.md).
  local vexe="$1" code="$2" tdir="$PREFIX/drive_c/Textractor/$ARCH"
  [ -d "$tdir" ] || return 0
  python3 - "$tdir" "$vexe" "$code" <<'PYEOF'
import os, sys
tdir, vexe, code = sys.argv[1], sys.argv[2], sys.argv[3]
def raw(name):
    try:
        with open(os.path.join(tdir, name), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""
def load(name):
    # CRLF->LF: upstream exact-matches these lines (docs/translate.md).
    return [l.strip() for l in raw(name).splitlines() if l.strip()]
hooks, games = load("SavedHooks.txt"), load("SavedGames.txt")
have_rich = any((l.split(" , ")[0] == vexe and " , " in l) for l in hooks)
if not have_rich:
    hooks = [l for l in hooks if l.split(" , ")[0] != vexe]
    hooks.append(vexe + (" , " + code if code else ""))
    with open(os.path.join(tdir, "SavedHooks.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(hooks) + "\n")
    print("seeded SavedHooks.txt ({})".format("hook " + code if code else "attach only"))
else:
    print("SavedHooks.txt keeps user-saved hooks")
if vexe not in games:
    games.append(vexe)
new_games = "\n".join(games) + "\n"
if new_games != raw("SavedGames.txt"):
    with open(os.path.join(tdir, "SavedGames.txt"), "w", encoding="utf-8") as f:
        f.write(new_games)
PYEOF
}
# Hooker hidden by default (play AND setup); --show-hooker reveals it (debug).
if [ "$ENGINE" = "tyrano" ]; then
  HSTYLE=0
  if [ "$SETUP" = "1" ]; then MODE="setup (CDP DOM hook)"; else MODE="play (CDP DOM hook + translation)"; fi
elif [ "$SETUP" = "1" ] && [ "$SHOW_HOOKER" = "1" ]; then
  HSTYLE=1; MODE="setup (Textractor visible)"
elif [ "$SETUP" = "1" ]; then
  HSTYLE=0; MODE="setup (Textractor hidden, pick in-app)"
else
  HSTYLE=0; MODE="play (Textractor hidden)"
fi
GDIR="$(dirname "$EXE")"
to_winpath() { python3 -c "
import sys
p = sys.argv[1]
pfx = sys.argv[2]
print('Z:' + p.replace('/', chr(92)) if not p.startswith(pfx + '/drive_c') else 'C:' + p[len(pfx + '/drive_c'):].replace('/', chr(92)))
" "$1" "$PREFIX"; }
VGAME="$(to_winpath "$EXE")"
VGDIR="$(to_winpath "$GDIR")"
GBASE="${VGAME##*\\}"
# --dry-run stays side-effect free: no SavedHooks seeding, no .vbs written.
if [ "$ENGINE" = "textractor" ] && [ "$DRYRUN" != "1" ]; then
  seed_saved_hooks "$VGAME" "$HOOKCODE_FLAG"
  python3 - "$HERE/launch.vbs.template" "$PREFIX/drive_c/hook/$GAME.vbs" <<EOF
import re, sys
t = open(sys.argv[1], 'rb').read().decode('utf-8')
t = t.replace('@HOOKER_DIR@', r'C:\Textractor\$ARCH')
t = t.replace('@HOOKER_EXE@', r'C:\Textractor\$ARCH\Textractor.exe')
t = t.replace('@HOOKER_STYLE@', '$HSTYLE')
t = t.replace('@GAME_BASE@', r'$GBASE')
t = t.replace('@GAME_DIR@', r'$VGDIR')
t = t.replace('@GAME_EXE@', r'$VGAME')
# Wine's wscript only reads ASCII/ANSI .vbs (not UTF-16), so keep the file
# ASCII and emit non-ASCII path characters as ChrW() concatenations instead
# (handles e.g. Z:\home\dd\Área de trabalho\... ). docs/translate.md
def esc(m):
    return '" & ' + ' & '.join('ChrW(&h%04X)' % ord(c) for c in m.group(0)) + ' & "'
t = re.sub(r'[^\x00-\x7f]+', esc, t)
t = t.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\r\n')
open(sys.argv[2], 'wb').write(t.encode('ascii'))
print('rendered $GAME.vbs (hooker style $HSTYLE)')
EOF
fi
export WINEPREFIX="$PREFIX"
[ -n "$PROTON" ] && export PROTONPATH="$PROTON"
export GAMEID
if command -v ak_locale_env >/dev/null 2>&1; then
  ak_locale_env "$LANG_SET"
else
  export LANG="$LANG_SET" HOST_LC_ALL="$LANG_SET"
fi
if command -v ak_wow64_env >/dev/null 2>&1; then ak_wow64_env "$PREFIX" "$WOW64"; fi
# Filter: same vkBasalt mechanism as proton-migaki.sh (variant conf + layer env).
if [ "$FILTER" != "off" ]; then
  if command -v ak_vkbasalt_env >/dev/null 2>&1; then
    FILTER="$(ak_variant "$FILTER")"
    ak_vkbasalt_env "$FILTER"
    echo "filter=$FILTER conf=$VKBASALT_CONFIG_FILE"
  else
    echo "warning: migaki-lib.sh not found at $AK_LIB; launching unfiltered" >&2
    FILTER="off"
  fi
fi
if [ "$DRYRUN" = "1" ]; then
  echo "WINEPREFIX=$PREFIX PROTONPATH=${PROTON:-umu-managed} GAMEID=$GAMEID"
  echo "game=$EXE engine=$ENGINE arch=$ARCH lang=$LANG_SET filter=$FILTER wow64=$([ "$WOW64" = "1" ] && echo on || echo off) dxvk=${DXVK_FILTER_DEVICE_NAME:-loader-default}"
  echo "vkbasalt=${VKBASALT_CONFIG_FILE:-off} layer=${VK_INSTANCE_LAYERS:-off}"
  if [ "$ENGINE" = "tyrano" ]; then
    printf 'tyrano_hook.py --cdp-port %s --bridge-port 6677 --gameid %q\n' "$CDP_PORT" "$GAMEID"
    printf 'umu-run %q --remote-debugging-port=%s --remote-allow-origins=*\n' "$EXE" "$CDP_PORT"
  else
    printf 'umu-run %q %q\n' \
      "$PREFIX/drive_c/windows/system32/wscript.exe" "C:\\hook\\$GAME.vbs"
  fi
  exit 0
fi
echo "launching $GAME [$MODE] (end session with Ctrl-C)…"
# cwd = game dir (same as proton-migaki.sh): the engine loads its data files
# relative to cwd, and WScript.Shell.CurrentDirectory is unreliable for
# non-ASCII Windows paths (e.g. Z:\home\dd\Área de trabalho\...).
cd "$GDIR" || { echo "cannot enter game dir: $GDIR" >&2; exit 1; }
set -x
if [ "$ENGINE" = "tyrano" ]; then
  # Tyrano/Electron: the CDP DOM hook serves the same :6677 bridge the
  # Textractor fork does, so the textbox/picker consume it unchanged. The hook
  # is native (Linux) and self-exits when the game's CDP endpoint goes away.
  HOOKLOG="$HOME/.cache/migaki"
  mkdir -p "$HOOKLOG"
  nohup python3 "$HERE/tyrano_hook.py" --cdp-port "$CDP_PORT" \
       --bridge-port 6677 --gameid "$GAMEID" >>"$HOOKLOG/tyrano-hook.log" 2>&1 &
  # exec so the GUI's Stop terminates the container (no orphaned wineserver).
  exec "$UMU" "$EXE" --remote-debugging-port="$CDP_PORT" --remote-allow-origins=*
fi
# exec so the GUI's Stop terminates the container (no orphaned wineserver).
exec "$UMU" "$PREFIX/drive_c/windows/system32/wscript.exe" "C:\\hook\\$GAME.vbs"
