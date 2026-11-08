#!/bin/bash
# gamescope-migaki.sh — run a game under gamescope with a 2x neural upscale filter.
#
# Usage: gamescope-migaki.sh [options] [--] <command ...>
#
# Options:
#   --filter NAME      upscale filter (default: cunny)
#                      CuNNy models: cunny (4x16-NVL), cunny-4x12, cunny-4x24,
#                      cunny-8x32; gamescope's own: anime4k, fsr, nis, linear,
#                      nearest, pixel
#   --render WxH       resolution the game renders at (default: the game's
#                      upscale profile, else 960x720)
#   --output WxH       fullscreen resolution presented (default: the game's
#                      upscale profile, else 1920x1080)
#   --gameid ID        use the game's upscale profile for the render/output
#                      pair and record what the run observed back into it
#   --probe            measure the game instead of scaling it: the virtual mode
#                      and the output both become the monitor size and --render
#                      is ignored, so the game picks its own resolution
#   --log FILE         where to tee gamescope's output (default with --gameid:
#                      ~/.cache/migaki/upscale/<gameid>.log)
#   --vk-device ID     Vulkan device for compositing (default: $MIGAKI_VK_DEVICE or 10de:1f9d)
#   --backend NAME     gamescope backend (default: sdl)
#   --restore          let the game runner apply its vkBasalt Restore chain
#                      (default: DISABLE_VKBASALT=1, CuNNy/Anime4K only)
#   --dry-run          print the gamescope command and the inner launcher, run nothing
#   --help             this text
#
# Environment:
#   MIGAKI_GAMESCOPE   gamescope binary (default: ~/.local/lib/migaki/gamescope/bin/gamescope,
#                      then a dev build in ~/gamescope-migaki/build/src)
#   MIGAKI_GAMESCOPE_LIB  extra directory with libXRes (default: $HOME/.local/libxres/lib)
#
# Limitations:
#   - X11/XWayland only: the SDL backend is used because the Wayland backend does
#     not present on Hyprland.
#   - 2x only. --render must be the resolution the game really renders at; the
#     filter works on the game window's buffer size, not on this number.
#   - The virtual screen exposes a single mode, equal to --render: a fullscreen
#     game cannot offer more than it, and lowering the base needs a restart with
#     a smaller --render.
#   - Do not resize the gamescope window: the upscale path is rebuilt on size
#     changes and the intermediate buffers are not freed.
#   - The game's argv never reaches the gamescope command line, so the Migaki
#     runners' pgrep-based stray killer does not match the wrapper.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
# The profile lives in core/, which needs the checkout on PYTHONPATH.
core_cmd() { env PYTHONPATH="$REPO" python3 -m core "$@"; }

FILTER="cunny"
RENDER=""
OUTPUT=""
VKDEV="${MIGAKI_VK_DEVICE:-10de:1f9d}"
BACKEND="sdl"
RESTORE=0
DRYRUN=0
PROBE=0
GAMEID=""
LOGFILE=""
GSLIB="${MIGAKI_GAMESCOPE_LIB:-$HOME/.local/libxres/lib}"

usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; /^set -euo/d'; }

die() { echo "gamescope-migaki: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --filter)   FILTER="${2:?--filter needs a value}"; shift 2 ;;
    --render)   RENDER="${2:?--render needs WxH}"; shift 2 ;;
    --output)   OUTPUT="${2:?--output needs WxH}"; shift 2 ;;
    --gameid)   GAMEID="${2:?--gameid needs an id}"; shift 2 ;;
    --log)      LOGFILE="${2:?--log needs a file}"; shift 2 ;;
    --probe)    PROBE=1; shift ;;
    --vk-device) VKDEV="${2:?--vk-device needs an id}"; shift 2 ;;
    --backend)  BACKEND="${2:?--backend needs a value}"; shift 2 ;;
    --restore)  RESTORE=1; shift ;;
    --dry-run)  DRYRUN=1; shift ;;
    --help|-h)  usage; exit 0 ;;
    --)         shift; break ;;
    *)          break ;;
  esac
done

[ $# -gt 0 ] || die "no game command given (use -- before the command)"

case "$FILTER" in
  cunny|cunny-4x12|cunny-4x16|cunny-4x24|cunny-8x32| \
  anime4k|fsr|nis|linear|nearest|pixel) ;;
  *) die "unknown --filter '$FILTER' (cunny, cunny-4x12, cunny-4x16, cunny-4x24, cunny-8x32, anime4k, fsr, nis, linear, nearest, pixel)" ;;
esac

# The packaged build first, then the dev checkout used while developing it.
GS="${MIGAKI_GAMESCOPE:-}"
if [ -z "$GS" ]; then
  for cand in "$HOME/.local/lib/migaki/gamescope/bin/gamescope" \
              "$HOME/gamescope-migaki/build/src/gamescope"; do
    if [ -x "$cand" ]; then GS="$cand"; break; fi
  done
fi
[ -n "$GS" ] || die "gamescope with -F cunny not found: run gamescope/build.sh or install.sh"
[ -x "$GS" ] || die "gamescope not found at $GS (set MIGAKI_GAMESCOPE)"
# gamescope spawns `gamescopereaper` by name (src/Utils/Process.cpp), so its
# own directory has to be on PATH.
export PATH="$(dirname "$GS"):$PATH"

# --probe measures what the game wants: a mode as large as the monitor, so the
# game's own window size is what reaches the filter.
if [ "$PROBE" = "1" ]; then
  mon="$(env PYTHONPATH="$REPO" python3 -c \
    'from core import upscale; print("%dx%d" % upscale.monitor_size())' 2>/dev/null || true)"
  [ -n "$mon" ] || mon="1920x1080"
  RENDER="$mon"
  OUTPUT="$mon"
elif [ -n "$GAMEID" ]; then
  if pair="$(core_cmd upscale-render --gid "$GAMEID" 2>/dev/null)"; then
    read -r prof_render prof_output <<< "$pair"
    [ -n "$RENDER" ] || RENDER="$prof_render"
    [ -n "$OUTPUT" ] || OUTPUT="$prof_output"
  else
    echo "gamescope-migaki: could not read the upscale profile for '$GAMEID'" >&2
  fi
fi

RENDER="${RENDER:-960x720}"
OUTPUT="${OUTPUT:-1920x1080}"

[[ "$RENDER" =~ ^[0-9]+x[0-9]+$ ]] || die "--render must be WxH"
[[ "$OUTPUT" =~ ^[0-9]+x[0-9]+$ ]] || die "--output must be WxH"
RW="${RENDER%x*}"; RH="${RENDER#*x}"
OW="${OUTPUT%x*}"; OH="${OUTPUT#*x}"

# The game command lives in a throwaway script so that the gamescope argv holds
# no game name and no game path.
inner="$(mktemp -t migaki-gs-inner-XXXXXX.sh)"
{
  echo '#!/bin/sh'
  echo "rm -f \"\$0\""
  printf 'exec'
  printf ' %q' "$@"
  printf '\n'
} > "$inner"
chmod +x "$inner"

if [ "$RESTORE" = "1" ]; then
  unset DISABLE_VKBASALT
else
  export DISABLE_VKBASALT=1
fi

# The install ships its own libXRes copy (build.sh bundles it when the system
# has no xres.pc), so the packaged gamescope keeps working without that prefix.
gs_libdir=""
gs_root="$(cd "$(dirname "$GS")/.." 2>/dev/null && pwd || true)"
if [ -n "$gs_root" ] && [ -d "$gs_root/lib" ]; then gs_libdir="$gs_root/lib"; fi
if [ -n "$gs_libdir" ]; then
  export LD_LIBRARY_PATH="$gs_libdir:$GSLIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
else
  export LD_LIBRARY_PATH="$GSLIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
unset gs_libdir gs_root

# Optional extra gamescope arguments, space separated (e.g. "-T /tmp/gs-stats").
extra=()
if [ -n "${MIGAKI_GAMESCOPE_ARGS:-}" ]; then
  read -r -a extra <<< "$MIGAKI_GAMESCOPE_ARGS"
fi

gs_cmd=("$GS" --backend "$BACKEND" --fullscreen --prefer-vk-device "$VKDEV"
        -w "$RW" -h "$RH" -W "$OW" -H "$OH" -F "$FILTER" "${extra[@]}" -- "$inner")

# A run with a game id keeps a log to read the filter's observation from.
if [ -z "$LOGFILE" ] && [ -n "$GAMEID" ]; then
  LOGFILE="${UPSCALE_LOG_DIR:-$HOME/.cache/migaki/upscale}/$GAMEID.log"
fi
if [ -n "$LOGFILE" ]; then
  mkdir -p "$(dirname "$LOGFILE")"
fi

if [ "$DRYRUN" = "1" ]; then
  echo "inner script: $inner"
  sed 's/^/  /' "$inner"
  echo "command:"
  printf '  %q' "${gs_cmd[@]}"
  printf '\n'
  if [ -n "$LOGFILE" ]; then echo "log: $LOGFILE"; fi
  rm -f "$inner"
  exit 0
fi

# Ctrl-C reaches the whole process group; the handler keeps this shell alive so
# the observation below is still recorded, then the child's status is returned.
trap ':' INT TERM
status=0
if [ -n "$LOGFILE" ]; then
  "${gs_cmd[@]}" 2>&1 | tee "$LOGFILE" || status=$?
else
  "${gs_cmd[@]}" || status=$?
fi

# Record what the run observed. The fork prints one MIGAKI_UPSCALE line per size
# change; its absence means the filter never engaged (the game already fills the
# output), which is itself the useful answer.
if [ -n "$GAMEID" ]; then
  note_args=()
  line=""
  if [ -n "$LOGFILE" ] && [ -f "$LOGFILE" ]; then
    # Not anchored to the line start: gamescope's stderr and the game's stdout
    # share one pipe, and a child that writes without a newline (mpv's progress
    # readout) leaves the observation mid-line. The last match is the final
    # state after any resize.
    line="$(grep -a 'MIGAKI_UPSCALE input=' "$LOGFILE" | tail -n 1 || true)"
  fi
  if [ -n "$line" ]; then
    in_s="$(printf '%s\n' "$line" | sed -n 's/.*input=\([0-9][0-9]*x[0-9][0-9]*\).*/\1/p')"
    cnn_s="$(printf '%s\n' "$line" | sed -n 's/.*cnn=\([0-9][0-9]*x[0-9][0-9]*\).*/\1/p')"
    out_s="$(printf '%s\n' "$line" | sed -n 's/.*out=\([0-9][0-9]*x[0-9][0-9]*\).*/\1/p')"
    if [ -n "$in_s" ] && [ -n "$cnn_s" ] && [ -n "$out_s" ]; then
      note_args=(--input "$in_s" --cnn "$cnn_s" --out "$out_s")
    fi
  fi
  core_cmd upscale-note --gid "$GAMEID" "${note_args[@]}" || true
fi

exit "$status"
