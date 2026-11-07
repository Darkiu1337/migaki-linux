#!/bin/bash
# gamescope-migaki.sh — run a game under gamescope with a 2x neural upscale filter.
#
# Usage: gamescope-migaki.sh [options] [--] <command ...>
#
# Options:
#   --filter NAME      upscale filter: linear | anime4k | cunny (default: cunny)
#   --render WxH       resolution the game renders at (default: 960x720)
#   --output WxH       fullscreen resolution presented (default: 1920x1080)
#   --vk-device ID     Vulkan device for compositing (default: $MIGAKI_VK_DEVICE or 10de:1f9d)
#   --backend NAME     gamescope backend (default: sdl)
#   --restore          let the game runner apply its vkBasalt Restore chain
#                      (default: DISABLE_VKBASALT=1, CuNNy/Anime4K only)
#   --dry-run          print the gamescope command and the inner launcher, run nothing
#   --help             this text
#
# Environment:
#   MIGAKI_GAMESCOPE   gamescope binary (default: $HOME/gamescope-migaki/build/src/gamescope)
#   MIGAKI_GAMESCOPE_LIB  directory with libXRes (default: $HOME/.local/libxres/lib)
#
# Limitations:
#   - X11/XWayland only: the SDL backend is used because the Wayland backend does
#     not present on Hyprland.
#   - 2x only. --render must be the resolution the game really renders at; the
#     filter works on the game window's buffer size, not on this number.
#   - Do not resize the gamescope window: the upscale path is rebuilt on size
#     changes and the intermediate buffers are not freed.
#   - The game's argv never reaches the gamescope command line, so the Migaki
#     runners' pgrep-based stray killer does not match the wrapper.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FILTER="cunny"
RENDER="960x720"
OUTPUT="1920x1080"
VKDEV="${MIGAKI_VK_DEVICE:-10de:1f9d}"
BACKEND="sdl"
RESTORE=0
DRYRUN=0
GS="${MIGAKI_GAMESCOPE:-$HOME/gamescope-migaki/build/src/gamescope}"
GSLIB="${MIGAKI_GAMESCOPE_LIB:-$HOME/.local/libxres/lib}"

usage() { sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//; /^set -euo/d'; }

die() { echo "gamescope-migaki: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --filter)   FILTER="${2:?--filter needs a value}"; shift 2 ;;
    --render)   RENDER="${2:?--render needs WxH}"; shift 2 ;;
    --output)   OUTPUT="${2:?--output needs WxH}"; shift 2 ;;
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
  linear|nearest|anime4k|cunny) ;;
  *) die "unknown --filter '$FILTER' (linear, nearest, anime4k, cunny)" ;;
esac

[[ "$RENDER" =~ ^[0-9]+x[0-9]+$ ]] || die "--render must be WxH"
[[ "$OUTPUT" =~ ^[0-9]+x[0-9]+$ ]] || die "--output must be WxH"
RW="${RENDER%x*}"; RH="${RENDER#*x}"
OW="${OUTPUT%x*}"; OH="${OUTPUT#*x}"

[ -x "$GS" ] || die "gamescope not found at $GS (set MIGAKI_GAMESCOPE)"

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

export LD_LIBRARY_PATH="$GSLIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Optional extra gamescope arguments, space separated (e.g. "-T /tmp/gs-stats").
extra=()
if [ -n "${MIGAKI_GAMESCOPE_ARGS:-}" ]; then
  read -r -a extra <<< "$MIGAKI_GAMESCOPE_ARGS"
fi

gs_cmd=("$GS" --backend "$BACKEND" --fullscreen --prefer-vk-device "$VKDEV"
        -w "$RW" -h "$RH" -W "$OW" -H "$OH" -F "$FILTER" "${extra[@]}" -- "$inner")

if [ "$DRYRUN" = "1" ]; then
  echo "inner script: $inner"
  sed 's/^/  /' "$inner"
  echo "command:"
  printf '  %q' "${gs_cmd[@]}"
  printf '\n'
  rm -f "$inner"
  exit 0
fi

exec "${gs_cmd[@]}"
