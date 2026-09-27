#!/bin/bash
# migaki-lib.sh — shared core for the Migaki launchers.
# Source it:  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#             source "$SCRIPT_DIR/migaki-lib.sh"
# Not meant to be executed directly.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "migaki-lib.sh is a library, source it instead of running it." >&2
  exit 1
fi

_MIGAKI_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIGAKI_ROOT="$(cd "$_MIGAKI_LIB_DIR/.." && pwd)"
unset _MIGAKI_LIB_DIR

# Desktop-launched sessions often omit ~/.local/bin from PATH; user tools
# installed there (rpgmaker-linux, …) must still resolve when a runner is
# spawned from the GUI rather than a login shell.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) PATH="$HOME/.local/bin:$PATH" ;;
esac

# User config (~/.config/migaki/config.json, optional). Keys: prefix, proton,
# layer_dir, shader_dir, wow64. Missing file/keys fall back to builtins below.
MIGAKI_CONFIG="$HOME/.config/migaki/config.json"
ak_config_get() {
  python3 -c "import json,sys; print(json.load(open('$MIGAKI_CONFIG')).get('$1', '$2'))" 2>/dev/null || printf '%s' "$2"
}

# Shader dir: explicit env wins, then config shader_dir, then the deployed XDG
# dir, then the repo copy (dev). docs/limits.md.
_MIGAKI_XDG_SHADERS="$HOME/.local/share/gamescope/reshade/Shaders"
_MIGAKI_CFG_SHADERS="$(ak_config_get shader_dir "")"
if [ -n "${MIGAKI_SHADER_DIR:-}" ]; then
  : # kept as-is
elif [ -n "$_MIGAKI_CFG_SHADERS" ] && [ -d "$_MIGAKI_CFG_SHADERS" ]; then
  MIGAKI_SHADER_DIR="$_MIGAKI_CFG_SHADERS"
elif [ -d "$_MIGAKI_XDG_SHADERS" ]; then
  MIGAKI_SHADER_DIR="$_MIGAKI_XDG_SHADERS"
else
  MIGAKI_SHADER_DIR="$MIGAKI_ROOT/shaders"
fi
unset _MIGAKI_XDG_SHADERS _MIGAKI_CFG_SHADERS

ak_die() { echo "error: $*" >&2; exit 1; }
ak_log() { echo "migaki: $*" >&2; }

ak_need() {
  command -v "$1" >/dev/null 2>&1 || ak_die "required tool '$1' not found on PATH"
}

# Locale handling for the game. HOST_LC_ALL is the pressure-vessel host-locale
# hint: forcing a locale the host has not generated makes some engines
# (Emote/.NET, e.g. mlove) exit in ~1s, while LANG alone is tolerated
# (docs/limits.md). So HOST_LC_ALL is only exported for a locale the host can
# actually provide; otherwise the caller is warned to generate it.
ak_locale_available() {
  local loc="$1" want
  [ -n "$loc" ] || return 1
  command -v locale >/dev/null 2>&1 || return 1
  want="$(printf '%s' "$loc" | tr 'A-Z' 'a-z' | sed 's/\.utf-8$/.utf8/')"
  locale -a 2>/dev/null | tr 'A-Z' 'a-z' | grep -qxF "$want"
}

ak_locale_env() {
  local loc="$1"
  [ -n "$loc" ] || return 0
  export LANG="$loc"
  if [ -n "${MIGAKI_NO_HOST_LC_ALL:-}" ]; then
    unset HOST_LC_ALL
    return 0
  fi
  if ak_locale_available "$loc"; then
    export HOST_LC_ALL="$loc"
  else
    unset HOST_LC_ALL
    ak_log "warning: locale $loc is not generated on this system; leaving HOST_LC_ALL unset so the game can start. To force the locale, add '$loc UTF-8' to /etc/locale.gen and run: sudo locale-gen"
  fi
}

# New WoW64 (default): run 32-bit Windows PE inside the single 64-bit host
# process, so Wine forwards its Vulkan calls to the 64-bit loader and the
# 64-bit vkBasalt hooks 32-bit D3D9 too. Requires a 64-bit prefix; a legacy
# win32 prefix (new WoW64-unsupported) keeps old WoW64. Opt out with
# `wow64=0` / --no-wow64. Why: docs/limits.md.
# Resolve the configured `proton` value to an absolute Proton directory:
# accepts a path or a bare build name (searched in the common
# compatibilitytools.d dirs). Prints the path, or nothing (return 1) when it
# cannot be resolved — callers then fall back to the umu-managed UMU-Proton.
ak_proton_resolve() {
  local p="$1" d
  [ -n "$p" ] || return 1
  if [ -d "$p" ] && { [ -x "$p/proton" ] || [ -x "$p/proton.sh" ]; }; then
    printf '%s' "$p"
    return 0
  fi
  for d in "$HOME/.local/share/Steam/compatibilitytools.d" \
           "$HOME/.steam/steam/compatibilitytools.d" \
           "$HOME/.steam/root/compatibilitytools.d" \
           "$HOME/.var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d" \
           "/usr/share/steam/compatibilitytools.d" \
           "/usr/local/share/steam/compatibilitytools.d"; do
    [ -d "$d/$p" ] || continue
    if [ -x "$d/$p/proton" ] || [ -x "$d/$p/proton.sh" ]; then
      printf '%s' "$d/$p"
      return 0
    fi
  done
  return 1
}

# True when a Proton build can run 32-bit PE through new WoW64. An explicit
# value that isn't a local dir is NOT assumed capable: the runner resolves the
# path first and otherwise falls back to umu-managed (docs/limits.md).
ak_proton_wow64_capable() {
  local p="$1"
  [ -n "$p" ] || return 0
  [ -d "$p" ] || return 1
  [ -x "$p/files/bin-wow64/wine" ] && return 0
  grep -qa 'PROTON_USE_WOW64' "$p/proton" 2>/dev/null && return 0
  # A 64-bit-only build (no wine64 loader) always runs new WoW64.
  [ -e "$p/files/bin/wine" ] && [ ! -e "$p/files/bin/wine64" ] && return 0
  return 1
}

ak_wow64_env() {
  local prefix="$1" want="${2:-1}"
  if [ "$want" != "1" ]; then
    ak_log "wow64: off (old WoW64 — 32-bit titles need a 32-bit vkBasalt)"
    return 0
  fi
  if [ -f "$prefix/system.reg" ] && grep -qa '#arch=win32' "$prefix/system.reg"; then
    ak_log "wow64: prefix is win32 — using old WoW64 (new WoW64 needs a 64-bit prefix)"
    return 0
  fi
  export WINEARCH=wow64
  export PROTON_USE_WOW64=1
  ak_log "wow64: on (32-bit titles route through the 64-bit Vulkan loader)"
}

# Kill leftover processes of a previous run of the SAME game (best effort).
# The token is matched against full command lines; the first character is
# bracketed so pkill can never match our own command line. Own PID and
# parent are always spared. Never fails (missing pgrep/pkill included).
# Example: ak_kill_strays "Spooky Milk Life" ; ak_kill_strays "nw --ozone-platform"
ak_kill_strays() {
  local token="$1" pat pids pid
  [ -n "$token" ] || return 0
  command -v pgrep >/dev/null 2>&1 || return 0
  pat="[${token:0:1}]${token:1}"
  pids="$(pgrep -f "$pat" 2>/dev/null || true)"
  [ -z "$pids" ] && return 0
  for pid in $pids; do
    [ "$pid" = "$$" ] && continue
    [ "$pid" = "$PPID" ] && continue
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 2
  for pid in $pids; do
    [ "$pid" = "$$" ] && continue
    [ "$pid" = "$PPID" ] && continue
    if kill -0 "$pid" 2>/dev/null; then
      ak_log "cleaned stray processes matching '$token'"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  return 0
}

# Locate the vkBasalt layer manifest in effect: explicit layer_dir first,
# then the system implicit-layer dirs (case-insensitive: the source build
# installs vkBasalt.json, which exact-case globs miss). Prints the path,
# or nothing when no layer is registered.
ak_vkbasalt_manifest() {
  local dir d hit
  dir="$(ak_config_get layer_dir "")"
  if [ -n "$dir" ] && [ -f "$dir/vkBasalt.json" ]; then
    printf '%s' "$dir/vkBasalt.json"
    return 0
  fi
  for d in "$HOME/.config/vulkan/implicit_layer.d" "$HOME/.local/share/vulkan/implicit_layer.d" \
           /usr/local/share/vulkan/implicit_layer.d /usr/share/vulkan/implicit_layer.d; do
    [ -d "$d" ] || continue
    hit="$(find "$d" -maxdepth 1 -iname '*vkbasalt*.json' -print -quit 2>/dev/null)"
    if [ -n "$hit" ]; then
      printf '%s' "$hit"
      return 0
    fi
  done
  return 1
}

# Resolve a layer manifest's library_path to an existing file: absolute
# paths directly, bare filenames via ldconfig + standard lib dirs.
# Prints the path, or nothing when the registration is broken.
ak_vkbasalt_lib() {
  local manifest="$1" lib p d
  [ -f "$manifest" ] || return 1
  lib="$(python3 - "$manifest" <<'EOF'
import json, sys
print(json.load(open(sys.argv[1]))["layer"]["library_path"])
EOF
)" 2>/dev/null || return 1
  case "$lib" in
    /*)
      [ -f "$lib" ] && printf '%s' "$lib" && return 0
      return 1
      ;;
    *)
      p="$(ldconfig -p 2>/dev/null | awk -v L="$lib" '$1 == L { print $NF; exit }')"
      if [ -n "$p" ] && [ -f "$p" ]; then
        printf '%s' "$p"
        return 0
      fi
      for d in /usr/lib /usr/lib64 "$HOME/.local/lib" "$HOME/.local/lib64"; do
        if [ -f "$d/$lib" ]; then
          printf '%s' "$d/$lib"
          return 0
        fi
      done
      return 1
      ;;
  esac
}

# Print a DXVK device-name substring for the discrete GPU (NVIDIA preferred),
# or nothing when none is detectable (caller then leaves DXVK unfiltered).
# vulkaninfo deviceType picks real discrete GPUs; the ICD fallback only
# yields vendor substrings, which is all DXVK_FILTER_DEVICE_NAME needs.
ak_discrete_gpu_name() {
  if command -v vulkaninfo >/dev/null 2>&1; then
    local pick
    pick="$(vulkaninfo --summary 2>/dev/null | python3 -c '
import re, sys
gpus, cur = [], {}
for line in sys.stdin:
    s = line.strip()
    if re.match(r"^(GPU\d+:|GPU id)", s):
        if cur:
            gpus.append(cur)
        cur = {}
    elif s.startswith("deviceName"):
        if "name" in cur:
            gpus.append(cur)
            cur = {}
        cur["name"] = s.split("=", 1)[1].strip()
    elif s.startswith("deviceType"):
        t = s.split("=", 1)[1].strip()
        cur["type"] = t.split("PHYSICAL_DEVICE_TYPE_")[-1]
if cur:
    gpus.append(cur)
disc = [g["name"] for g in gpus if g.get("type") == "DISCRETE_GPU" and g.get("name")]
nvidia = [n for n in disc if "NVIDIA" in n]
print((nvidia or disc or [""])[0])
')" 2>/dev/null
    if [ -n "$pick" ]; then
      printf '%s' "$pick"
      return 0
    fi
  fi
  if ls /usr/share/vulkan/icd.d/nvidia_icd*.json >/dev/null 2>&1; then
    printf 'NVIDIA'
    return 0
  fi
  if ls /usr/share/vulkan/icd.d/radeon_icd*.json /usr/share/vulkan/icd.d/amd_icd*.json >/dev/null 2>&1; then
    printf 'AMD'
    return 0
  fi
  return 1
}

# Vendor of the GPU that owns the X11/XWayland session — the only one that can
# present a Vulkan swapchain for a windowed app. Chromium/ANGLE-Vulkan only
# works on X11 ozone, so a forced non-display GPU cannot filter (its GPU
# process fails vkCreateSwapchainKHR and the game drops to Canvas2D). Prints
# nvidia|amd|intel, or nothing (returns 1) when glxinfo is unavailable.
ak_display_gpu_vendor() {
  command -v glxinfo >/dev/null 2>&1 || return 1
  local r
  r="$(glxinfo -B 2>/dev/null | sed -n 's/^OpenGL renderer string: //p' | head -n 1)"
  [ -n "$r" ] || return 1
  case "$r" in
    *NVIDIA*) printf 'nvidia' ;;
    *AMD*|*Radeon*|*ATI*) printf 'amd' ;;
    *Intel*) printf 'intel' ;;
    *) return 1 ;;
  esac
}

# Preset family manifest (Clear: 3D-clarity effect chains, see the file's
# comments). Prefer the repo copy so tuning shaders/presets.json takes effect
# on the next launch; a deployed copy is the fallback for standalone installs.
ak_preset_json() {
  if [ -f "$MIGAKI_ROOT/shaders/presets.json" ]; then
    printf '%s' "$MIGAKI_ROOT/shaders/presets.json"
  else
    printf '%s' "$MIGAKI_SHADER_DIR/presets.json"
  fi
}

# True when <name> is a preset in the manifest (Restore variants are not).
ak_is_preset() {
  local name="$1" manifest
  [ -n "$name" ] || return 1
  manifest="$(ak_preset_json)"
  [ -f "$manifest" ] || return 1
  python3 - "$manifest" "$name" <<'EOF' 2>/dev/null
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if isinstance(data, dict) and sys.argv[2] in data else 1)
EOF
}

# Render a preset's vkBasalt conf: an ordered effect chain of built-ins (cas,
# smaa, ...) plus optional custom .fx shaders and their params. Kept in one
# place so future presets need only a presets.json entry.
ak_render_preset_conf() { # <preset> <conf> <shader_dir> [repo_shader_dir]
  local preset="$1" conf="$2" shader_dir="$3" repo_dir="${4:-$MIGAKI_ROOT/shaders}" manifest
  manifest="$(ak_preset_json)"
  python3 - "$manifest" "$preset" "$conf" "$shader_dir" "$repo_dir" <<'EOF'
import json, os, sys
manifest, preset, conf, shader_dir, repo_dir = sys.argv[1:6]
try:
    data = json.load(open(manifest))
    p = data[preset]
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(3)
shader_dir = shader_dir.rstrip("/")
repo_dir = repo_dir.rstrip("/")
lines = ["# generated by migaki — Clear preset: %s" % preset,
         "# 3D clarity chain; edit shaders/presets.json, not this copy.",
         "effects = " + ":".join(p["effects"])]
for name, fname in (p.get("shaders") or {}).items():
    if os.path.isabs(fname):
        path = fname
    else:
        # Prefer the repo copy (so tuning ClearColor.fx applies on relaunch);
        # fall back to the deployed shader dir for standalone installs.
        path = os.path.join(repo_dir, fname)
        if not os.path.exists(path):
            path = os.path.join(shader_dir, fname)
    lines.append("%s = %s" % (name, path))
for key, val in (p.get("params") or {}).items():
    lines.append("%s = %s" % (key, val))
lines += ["reshadeTexturePath = %s" % shader_dir,
          "reshadeIncludePath = %s" % shader_dir,
          "depthCapture = off",
          "toggleKey = Home",
          "enableOnLaunch = True"]
open(conf, "w").write("\n".join(lines) + "\n")
EOF
}

# Validate a filter name: a Restore variant (S, M, L, Soft_S, Soft_M, Soft_L,
# VL, UL, Soft_VL, Soft_UL) or a Clear preset from shaders/presets.json.
# Unknown names die here (once).
ak_variant() {
  local v="${1:-L}"
  case "$v" in
    S|M|L|Soft_S|Soft_M|Soft_L|VL|UL|Soft_VL|Soft_UL) printf '%s' "$v"; return 0 ;;
  esac
  if ak_is_preset "$v"; then
    printf '%s' "$v"
    return 0
  fi
  ak_die "unknown variant '$v' (Restore: S, M, L, Soft_S, Soft_M, Soft_L, VL, UL, Soft_VL, Soft_UL; Clear presets: see shaders/presets.json)"
}

# Set up vkBasalt layer env for the given variant or preset.
# Structural off switch: with DISABLE_VKBASALT=1 set, export nothing and
# scrub any inherited layer variables, so the loader never sees vkBasalt.
# (Relying on the loader's disable flag is unreliable for explicitly-listed
# layers, which is how uninstalled builds are loaded.)
ak_vkbasalt_env() {
  if [ -n "${DISABLE_VKBASALT:-}" ]; then
    unset VK_ADD_LAYER_PATH VK_INSTANCE_LAYERS ENABLE_VKBASALT VKBASALT_CONFIG_FILE
    return 0
  fi
  local variant="$1"
  local confdir="$HOME/.config/migaki"
  mkdir -p "$confdir"
  local conf="$confdir/vkbasalt-${variant}.conf"
  if ak_is_preset "$variant"; then
    ak_render_preset_conf "$variant" "$conf" "$MIGAKI_SHADER_DIR" \
      || ak_die "failed to render preset '$variant' (check shaders/presets.json)"
  else
    local shader="$MIGAKI_SHADER_DIR/Anime4K_Restore_${variant}.fx"
    [ -f "$shader" ] || ak_die "shader missing: $shader"
    sed -e "s|@SHADER_DIR@|$(dirname "$shader")|g" -e "s|@VARIANT@|$variant|g" \
      "$MIGAKI_ROOT/shaders/vkbasalt.conf.in" > "$conf"
  fi
  local layer_dir
  layer_dir="$(ak_config_get layer_dir "")"
  local manifest lib
  manifest="$(ak_vkbasalt_manifest)" \
    || ak_die "no vkBasalt layer found (set layer_dir in $MIGAKI_CONFIG or install vkbasalt; see requirements.md)"
  lib="$(ak_vkbasalt_lib "$manifest")" \
    || ak_die "vkBasalt layer registered at $manifest but its library is missing (reinstall vkbasalt; run: migaki doctor)"
  if [ -n "$layer_dir" ] && [ "$manifest" = "$layer_dir/vkBasalt.json" ]; then
    export VK_ADD_LAYER_PATH="$layer_dir"
    export VK_INSTANCE_LAYERS="VK_LAYER_VKBASALT_post_processing"
  else
    unset VK_ADD_LAYER_PATH VK_INSTANCE_LAYERS
  fi
  export ENABLE_VKBASALT=1
  export VKBASALT_CONFIG_FILE="$conf"
}

# --- Textractor provisioning (canonical dir + per-prefix symlink) -----------
# One install per machine under ~/.local/share/migaki/textractor, holding both
# x86 and x64 builds (<home>/x86, <home>/x64); each prefix's drive_c/Textractor
# is a symlink to it. The game's bitness picks the build (ak_pe_arch), because
# Textractor can only inject a hook DLL matching the target process. The
# extension set is ALWAYS forced bridge-only: Textractor otherwise loads its
# six stock extensions (Google Translate, ...) when SavedExtensions.txt is
# missing, which stalls the sentence pipeline (docs/translate.md).
ak_textractor_home() {
  printf '%s' "${MIGAKI_TEXTTRACTOR_HOME:-$HOME/.local/share/migaki/textractor}"
}
# Build dir for an architecture (x86 | x64). Unknown/empty defaults to x86
# (the proven default used by the reference 32-bit titles).
ak_textractor_dir() { # [arch]
  local arch="${1:-x86}"
  case "$arch" in x64|X64|amd64|AMD64) arch="x64" ;; *) arch="x86" ;; esac
  printf '%s/%s' "$(ak_textractor_home)" "$arch"
}
# Back-compat alias (x86 build dir).
ak_textractor_x86() { ak_textractor_dir x86; }

# Textractor build to use for a Windows executable: reads the PE Machine field
# (0x8664 = AMD64/x64, 0x014c = i386/x86). Anything unparseable or non-PE
# defaults to x86, the proven default. Override with MIGAKI_TEXTTRACTOR_ARCH.
ak_pe_arch() { # <exe>
  if [ -n "${MIGAKI_TEXTTRACTOR_ARCH:-}" ]; then
    case "$MIGAKI_TEXTTRACTOR_ARCH" in
      x64|X64|amd64|AMD64) printf 'x64' ;;
      *) printf 'x86' ;;
    esac
    return 0
  fi
  python3 - "$1" <<'PY' 2>/dev/null || printf 'x86'
import struct, sys
try:
    with open(sys.argv[1], "rb") as f:
        if f.read(2) != b"MZ":
            raise ValueError
        f.seek(0x3C)
        e_lfanew = struct.unpack("<I", f.read(4))[0]
        f.seek(e_lfanew)
        if f.read(4) != b"PE\0\0":
            raise ValueError
        machine = struct.unpack("<H", f.read(2))[0]
    print("x64" if machine == 0x8664 else "x86")
except Exception:
    print("x86")
PY
}

ak_textractor_bridge_only() { # [arch]
  local dir
  dir="$(ak_textractor_dir "${1:-x86}")"
  [ -d "$dir" ] || return 0
  printf 'textractor_websocket_%s>' "$(basename "$dir")" > "$dir/SavedExtensions.txt"
}

# Ensure Textractor is provisioned in <prefix> for the given arch and
# bridge-only is enforced. A legacy per-prefix install is used as-is (no vendor
# needed); only a prefix with no Textractor triggers canonical provisioning.
# Prints the Textractor.exe path ("" when it could not be provisioned).
ak_textractor_ensure() { # <prefix> [bridge] [arch]
  local prefix="$1" bridge="${2:-fixed}" arch
  arch="$(basename "$(ak_textractor_dir "${3:-x86}")")"
  [ -n "$prefix" ] || return 1
  local exe="$prefix/drive_c/Textractor/$arch/Textractor.exe"
  if [ ! -f "$exe" ]; then
    # Single installer, internal mode: fetches the pinned vendor bundle when
    # missing, provisions the canonical dir and links it into this prefix.
    local inst="$MIGAKI_ROOT/install.sh"
    [ -f "$inst" ] || return 1
    bash "$inst" --provision-textractor --prefix "$prefix" --bridge "$bridge" >&2 || return 1
  fi
  ak_textractor_bridge_only "$arch"
  printf '%s' "$exe"
}

# Find a wineserver for a Proton prefix: the resolved PROTON first, then any
# compatibilitytools.d build, then PATH. A stale wineserver wedges the next
# container (docs/translate.md).
ak_wineserver_bin() { # [proton-dir]
  local p="$1" f
  [ -n "$p" ] && [ -x "$p/files/bin/wineserver" ] && { printf '%s' "$p/files/bin/wineserver"; return 0; }
  for f in "$HOME"/.local/share/Steam/compatibilitytools.d/*/files/bin/wineserver \
           "$HOME"/.steam/steam/compatibilitytools.d/*/files/bin/wineserver \
           "$HOME"/.local/share/umu/*/files/bin/wineserver; do
    [ -x "$f" ] && { printf '%s' "$f"; return 0; }
  done
  command -v wineserver 2>/dev/null
}

# Resolve the rpgmaker-linux wrapper's shared NW.js manifest (honors the
# wrapper's custom-path file, then its default location).
ak_rpgmaker_template() {
  local mainfd=""
  if [ -r "$HOME/.config/defrpgmakerlinuxpath.txt" ]; then
    mainfd="$(head -n 1 "$HOME/.config/defrpgmakerlinuxpath.txt")"
    mainfd="${mainfd%/}"
  fi
  [ -n "$mainfd" ] || mainfd="$HOME/desktopapps"
  local tpl="$mainfd/nwjs/nwjs/packagefiles/package.json"
  [ -f "$tpl" ] || ak_die "rpgmaker-linux manifest not found at $tpl (is rpgmaker-linux installed?)"
  printf '%s' "$tpl"
}

# Pick a file via zenity when no path was given (empty string = cancelled).
# $2 (optional): file-filter string, e.g. 'Windows executables | *.exe *.EXE'.
# Empty/missing $2 means no filter (all files selectable).
ak_pick_file() {
  local title="$1" pattern="$2"
  ak_need zenity
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    ak_die "no display for file picker; pass the path as an argument instead"
  fi
  if [ -n "$pattern" ]; then
    zenity --file-selection --title="$title" --file-filter="$pattern" 2>/dev/null || true
  else
    zenity --file-selection --title="$title" 2>/dev/null || true
  fi
}

# Pick a directory via zenity when none was given (empty string = cancelled).
ak_pick_dir() {
  local title="$1"
  ak_need zenity
  if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    ak_die "no display for folder picker; pass --gamepath instead"
  fi
  zenity --file-selection --directory --title="$title" 2>/dev/null || true
}

# Wrapper-template patch/restore (shared NW.js manifest used by rpgmaker-linux).
# The template location honors the wrapper's own custom-path file, then default.
MIGAKI_TEMPLATE=""
MIGAKI_TEMPLATE_BACKUP="/tmp/rpg-template-package.json.bak-migaki"

ak_template_path() {
  if [ -z "$MIGAKI_TEMPLATE" ]; then
    MIGAKI_TEMPLATE="$(ak_rpgmaker_template)"
  fi
  printf '%s' "$MIGAKI_TEMPLATE"
}

ak_template_patch() {
  local tpl
  tpl="$(ak_template_path)"
  ak_need python3
  cp "$tpl" "$MIGAKI_TEMPLATE_BACKUP"
  python3 - "$tpl" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
extra = " --use-gl=angle --use-angle=vulkan --disable-gpu-sandbox --ignore-gpu-blocklist --enable-features=Vulkan,VulkanFromANGLE,DefaultANGLEVulkan"
if "--use-angle=vulkan" not in d.get("chromium-args", ""):
    d["chromium-args"] = d.get("chromium-args", "") + extra
json.dump(d, open(p, "w"), indent=2)
EOF
}

ak_template_restore() {
  if [ -f "$MIGAKI_TEMPLATE_BACKUP" ]; then
    local tpl
    tpl="$(ak_template_path)"
    cp "$MIGAKI_TEMPLATE_BACKUP" "$tpl"
    rm -f "$MIGAKI_TEMPLATE_BACKUP"
  fi
}

# MangoHud fps limit (+optional overlay) for runners without a DXVK cap.
# FPS: number or off. HUD: 1 = show overlay, 0 = limit silently (no_display).
ak_mangohud_env() {
  local fps="${1:-60}" hud="${2:-0}"
  if [ "$fps" = "off" ] && [ "$hud" = "0" ]; then
    return 0
  fi
  local cfg=""
  if [ "$fps" != "off" ]; then
    [[ "$fps" =~ ^[0-9]+$ ]] || ak_die "--fps needs a number or off"
    cfg="fps_limit=$fps"
  fi
  if [ "$hud" = "0" ]; then
    cfg="${cfg:+,}no_display"
  fi
  cfg="${cfg#,}"
  export MANGOHUD=1
  export MANGOHUD_CONFIG="$cfg"
}

# Locate a Vulkan ICD manifest by vendor. File names differ across distros
# (nvidia_icd.json, 10_nvidia.json, radeon_icd.x86_64.json, intel_icd.*), so
# glob instead of hardcoding. Prints the path, or nothing when absent.
ak_icd_file() { # nvidia|amd|intel
  local vendor="$1" d f
  case "$vendor" in nvidia|amd|intel) ;; *) return 1 ;; esac
  for d in /usr/share/vulkan/icd.d /usr/local/share/vulkan/icd.d \
           /etc/vulkan/icd.d "$HOME/.local/share/vulkan/icd.d"; do
    [ -d "$d" ] || continue
    case "$vendor" in
      nvidia)
        for f in "$d"/*nvidia*icd*.json; do
          [ -f "$f" ] && { printf '%s' "$f"; return 0; }
        done ;;
      amd)
        for f in "$d"/*radeon*icd*.json "$d"/*amd*icd*.json; do
          [ -f "$f" ] && { printf '%s' "$f"; return 0; }
        done ;;
      intel)
        for f in "$d"/*intel*icd*.json; do
          [ -f "$f" ] && { printf '%s' "$f"; return 0; }
        done ;;
    esac
  done
  return 1
}

# Zink (OpenGL-on-Vulkan) env so vkBasalt can hook GL-only games.
# GPU select: nvidia | amd | intel | auto (loader default).
ak_zink_env() {
  local gpu="${1:-auto}" icd
  case "$gpu" in
    nvidia|amd|intel)
      if icd="$(ak_icd_file "$gpu")"; then
        export VK_ICD_FILENAMES="$icd"
      else
        ak_log "warning: no $gpu Vulkan ICD found; using the loader default"
        unset VK_ICD_FILENAMES
      fi
      ;;
    auto) unset VK_ICD_FILENAMES ;;
    *) ak_die "unknown GPU '$gpu' (expected nvidia, amd, intel or auto)" ;;
  esac
  export __GLX_VENDOR_LIBRARY_NAME=mesa
  export MESA_LOADER_DRIVER_OVERRIDE=zink
  export GALLIUM_DRIVER=zink
}

# Helper .exe names that are never the game itself (launchers, crash
# handlers, redist installers). Used by engine detection and exe picking.
ak_helper_exe() {
  case "$(basename "$1" | tr '[:upper:]' '[:lower:]')" in
    unitycrashhandler*|notification_helper*|nwjc*|payload*|*uninstall*|\
    vcredist*|dxsetup*|dotnetfx*|crashpad_handler*|crash_reporter*|*setup*)
      return 0 ;;
  esac
  return 1
}

# Pick the main executable from a game dir: Game.exe, then <dirname>.exe,
# then the single remaining non-helper exe. Prints path or nothing.
ak_main_exe() {
  local dir="$1" f base
  for f in "$dir"/Game.exe "$dir"/GAME.EXE; do
    [ -f "$f" ] && { printf '%s' "$f"; return 0; }
  done
  base="$(basename "$dir")"
  for f in "$dir/$base.exe" "$dir/$base.EXE"; do
    [ -f "$f" ] && { printf '%s' "$f"; return 0; }
  done
  local cands="" n=0
  for f in "$dir"/*.exe "$dir"/*.EXE; do
    [ -f "$f" ] || continue
    ak_helper_exe "$f" && continue
    cands="$f"; n=$((n + 1))
  done
  [ "$n" = "1" ] && printf '%s' "$cands"
  return 0
}

# Game/engine detection. Prints: engine|runner|confidence|root|detail
# Engines: rpgmaker-mv, rpgmaker-xp (covers VX/VXAce), renpy-native,
#   renpy-windows, unity-windows, unity-linux, godot, electron, tyrano,
#   appimage, exe (generic Windows), elf (generic native), unknown.
# Runners: proton, rpgmaker, native, ask.
# Confidence: high, medium, low. Never fails (unknown is a valid outcome).
ak_detect_engine() {
  local target="$1" dir base
  if [ -f "$target" ]; then
    dir="$(dirname "$target")"
  elif [ -d "$target" ]; then
    dir="$target"
  else
    printf 'unknown|ask|low|%s|no such path' "$target"
    return 0
  fi
  base="$(basename "$dir")"

  # RPGMaker MV/MZ (Chromium/NW.js). Marker is js/rpg_core.js (MV) or
  # js/rmmz_core.js (MZ). Two shapes:
  #   * <root>/www/index.html ...   -> game root as given
  #   * <dir>/index.html + js/...   -> if <dir> is named "www" the user picked
  #     the web subfolder and the game root is its parent; otherwise <dir> IS
  #     the game root (MZ/MV desktop exports keep index.html + js/ at root).
  if [ -f "$dir/www/index.html" ] \
     && { [ -f "$dir/www/js/rpg_core.js" ] || [ -f "$dir/www/js/rmmz_core.js" ]; }; then
    local _core="MV"
    [ -f "$dir/www/js/rmmz_core.js" ] && _core="MZ"
    printf 'rpgmaker-mv|rpgmaker|high|%s|RPGMaker %s markers' "$dir" "$_core"
    return 0
  fi
  if [ -f "$dir/index.html" ] \
     && { [ -f "$dir/js/rpg_core.js" ] || [ -f "$dir/js/rmmz_core.js" ]; }; then
    local _core="MV"
    [ -f "$dir/js/rmmz_core.js" ] && _core="MZ"
    if [ "$base" = "www" ]; then
      printf 'rpgmaker-mv|rpgmaker|high|%s|RPGMaker %s markers (www/ depth)' "$(dirname "$dir")" "$_core"
    else
      printf 'rpgmaker-mv|rpgmaker|high|%s|RPGMaker %s desktop export' "$dir" "$_core"
    fi
    return 0
  fi

  # RPGMaker XP/VX/VXAce (RGSS data + ini).
  if ls "$dir"/Data/*.rxdata >/dev/null 2>&1 || ls "$dir"/Data/*.rvdata >/dev/null 2>&1 || \
     ls "$dir"/Data/*.rvdata2 >/dev/null 2>&1; then
    if [ -f "$dir/Game.ini" ]; then
      printf 'rpgmaker-xp|proton|high|%s|RGSS data + Game.ini (runs best under Proton)' "$dir"
    else
      printf 'rpgmaker-xp|proton|medium|%s|RGSS data without Game.ini' "$dir"
    fi
    return 0
  fi

  # Ren'Py (engine dirs decide native vs Windows).
  if [ -d "$dir/renpy" ] && [ -d "$dir/game" ]; then
    local sh launcher=""
    for sh in "$dir"/*.sh; do
      [ -f "$sh" ] && [ -x "$sh" ] && { launcher="$sh"; break; }
    done
    if [ -n "$launcher" ]; then
      printf 'renpy-native|native|high|%s|RenPy distro with Linux launcher' "$dir"
      return 0
    fi
    local exe
    exe="$(ak_main_exe "$dir")"
    if [ -n "$exe" ]; then
      printf 'renpy-windows|proton|high|%s|RenPy Windows build (ANGLE-forced under Proton)' "$dir"
      return 0
    fi
    printf 'renpy-unknown|ask|low|%s|RenPy layout, no runnable found' "$dir"
    return 0
  fi

  # Unity (Data dir + runtime markers; .dll = Windows, .so = Linux).
  local data
  data="$(find "$dir" -maxdepth 1 -type d -name '*_Data' | head -n 1)"
  if [ -n "$data" ]; then
    if [ -f "$data/../GameAssembly.dll" ] || [ -f "$data/../GameAssembly.so" ] || \
       [ -d "$data/../MonoBleedingEdge" ]; then
      local exe
      exe="$(ak_main_exe "$dir")"
      if [ -n "$exe" ]; then
        printf 'unity-windows|proton|high|%s|Unity IL2CPP/Mono bundle' "$dir"
        return 0
      fi
    fi
    local elf
    elf="$(find "$dir" -maxdepth 1 -type f \( -iname '*.x86_64' -o -iname '*.x86' \) | head -n 1)"
    if [ -z "$elf" ]; then
      for f in "$dir"/*; do
        if [ -f "$f" ] && [ -x "$f" ] && [ "${f##*.}" = "$f" ]; then elf="$f"; break; fi
      done
    fi
    if [ -n "$elf" ]; then
      printf 'unity-linux|native|high|%s|Unity Linux bundle' "$dir"
      return 0
    fi
  fi

  # Godot (.pck + engine binary).
  if ls "$dir"/*.pck >/dev/null 2>&1; then
    local exe
    exe="$(ak_main_exe "$dir")"
    if [ -n "$exe" ]; then
      printf 'godot-windows|proton|medium|%s|Godot .pck + Windows exe' "$dir"
      return 0
    fi
    printf 'godot|ask|low|%s|Godot data without a clear launcher' "$dir"
    return 0
  fi

  # Electron / Chromium-app bundles.
  if [ -d "$dir/resources" ] && ls "$dir"/resources/*.asar >/dev/null 2>&1; then
    printf 'electron|proton|medium|%s|Electron bundle (filter support experimental)' "$dir"
    return 0
  fi

  # KiriKiri visual novels (data.xp3 archives + Windows exe, DirectX-based).
  if [ -f "$dir/data.xp3" ]; then
    local kexe
    kexe="$(ak_main_exe "$dir")"
    if [ -n "$kexe" ]; then
      printf 'kirikiri|proton|high|%s|KiriKiri bundle (DirectX, hookable)' "$dir"
      return 0
    fi
  fi

  # TyranoBuilder / web exports handled by the rpgmaker wrapper.
  if [ -d "$dir/tyrano" ] && [ -d "$dir/data" ] && [ -f "$dir/index.html" ]; then
    printf 'tyrano|rpgmaker|medium|%s|TyranoScript layout (filter unlikely)' "$dir"
    return 0
  fi

  # AppImage.
  local appimg
  appimg="$(find "$dir" -maxdepth 1 -name '*.AppImage' 2>/dev/null | head -n 1)"
  if [ -n "$appimg" ]; then
    printf 'appimage|native|high|%s|AppImage bundle' "$dir"
    return 0
  fi

  # Fallbacks by launcher kind.
  if [ -f "$target" ]; then
    case "$target" in
      *.exe|*.EXE)
        printf 'exe|proton|low|%s|unrecognized Windows executable' "$dir"
        return 0
        ;;
    esac
    if [ -x "$target" ]; then
      printf 'elf|native|low|%s|unrecognized native executable' "$dir"
      return 0
    fi
  fi
  local exe
  exe="$(ak_main_exe "$dir")"
  if [ -n "$exe" ]; then
    printf 'exe|proton|low|%s|unrecognized dir with Windows executable' "$dir"
    return 0
  fi
  printf 'unknown|ask|low|%s|no recognizable game markers' "$dir"
  return 0
}

# True when the file is a Windows PE (starts with the "MZ" magic). Reads only
# the first two bytes, so a multi-GB .exe costs nothing.
ak_is_windows_pe() {
  local magic
  [ -f "${1:-}" ] || return 1
  magic="$(head -c 2 "$1" 2>/dev/null | od -An -tx1 | tr -d ' \n')"
  [ "$magic" = "4d5a" ]
}

# Ren'Py distro helper: prints the Linux launcher (.sh) for a distro dir, or
# nothing when the distro cannot actually run natively. Requires the Ren'Py
# layout, an executable .sh (preferring the one whose stem pairs with a sibling
# .py — Ren'Py's own naming), AND a lib/<platform>/ engine binary. A distro
# that ships a .sh but no Linux payload (Windows-only build) fails here.
ak_renpy_launcher() {
  local dir="$1" sh="" stem py found eng lib plat
  [ -d "$dir/renpy" ] && [ -d "$dir/game" ] || return 1
  for py in "$dir"/*.py; do
    [ -f "$py" ] || continue
    stem="$(basename "$py" .py)"
    if [ -f "$dir/$stem.sh" ] && [ -x "$dir/$stem.sh" ]; then
      sh="$dir/$stem.sh"; break
    fi
  done
  if [ -z "$sh" ]; then
    for found in "$dir"/*.sh; do
      [ -f "$found" ] && [ -x "$found" ] && { sh="$found"; break; }
    done
  fi
  [ -n "$sh" ] || return 1
  stem="$(basename "$sh" .sh)"
  for plat in linux-x86_64 linux-i686 linux-aarch64 linux-armv7l; do
    lib="$dir/lib/$plat"
    [ -d "$lib" ] || continue
    [ -x "$lib/$stem" ] && { printf '%s' "$sh"; return 0; }
    for eng in "$lib"/*; do
      [ -x "$eng" ] && [ ! -d "$eng" ] && { printf '%s' "$sh"; return 0; }
    done
  done
  return 1
}

# The exact path a runner should be pointed at for a game: resolves the detected
# engine's real launch target (Ren'Py native -> the Linux .sh; rpgmaker/tyrano
# -> the game folder; Windows engines -> the main .exe). Prints the input
# unchanged when the layout is unrecognized. Never fails.
ak_launch_target() {
  local target="$1" det engine root t
  [ -n "$target" ] || { printf '%s' ""; return 0; }
  det="$(ak_detect_engine "$target")"
  engine="${det%%|*}"; det="${det#*|}"   # runner
  det="${det#*|}"; det="${det#*|}"        # confidence, then root
  root="${det%%|*}"
  case "$engine" in
    renpy-native)
      t="$(ak_renpy_launcher "$root")" || t=""
      printf '%s' "${t:-$target}"; return 0 ;;
    rpgmaker-mv|tyrano)
      printf '%s' "$root"; return 0 ;;
    renpy-windows|unity-windows|rpgmaker-xp|kirikiri|electron|godot-windows|exe)
      t="$(ak_main_exe "$root")"
      [ -n "$t" ] || t="$target"
      printf '%s' "${t:-$target}"; return 0 ;;
    unity-linux|appimage|elf)
      if [ -f "$target" ] && [ -x "$target" ]; then printf '%s' "$target"; return 0; fi
      t="$(find "$root" -maxdepth 1 -name '*.AppImage' 2>/dev/null | head -n 1)"
      if [ -z "$t" ]; then
        for t in "$root"/*; do
          if [ -f "$t" ] && [ -x "$t" ] && [ "${t##*.}" != "exe" ]; then break; fi
          t=""
        done
      fi
      printf '%s' "${t:-$target}"; return 0 ;;
    *)
      printf '%s' "$target"; return 0 ;;
  esac
}

# Reconcile a chosen (path, runner): point the runner at a launchable target
# and catch the native-runner-on-a-Windows-exe mistake. For a Ren'Py title the
# runner is kept on native only when a Linux runtime exists; otherwise it is
# routed to proton. A non-Ren'Py .exe under native warns (the caller offers the
# proton switch). Prints: runner|target|severity|message  (severity ok|info|warn)
ak_reconcile() {
  local path="$1" runner="$2" det engine conf root detail
  local target="$path" out_runner="$runner" sev="ok" msg=""

  [ -n "$path" ] || { printf '%s|%s|warn|%s' "$runner" "$path" "No path given."; return 0; }
  if [ ! -e "$path" ]; then
    printf '%s|%s|warn|%s' "$runner" "$path" "Path does not exist."
    return 0
  fi

  det="$(ak_detect_engine "$path")"
  IFS='|' read -r engine _ conf root detail <<<"$det"

  case "$runner" in
    native)
      # Anything that isn't a runnable Linux file belongs on proton for Ren'Py,
      # and is a mistake for other Windows engines.
      local pe=0
      if [ -f "$path" ] && ak_is_windows_pe "$path"; then
        pe=1
        target="$path"
      else
        target="$(ak_launch_target "$path")"
        [ -n "$target" ] || target="$path"
        if [ -f "$target" ] && ak_is_windows_pe "$target"; then pe=1; fi
      fi
      if [ "$pe" = "1" ] || [ ! -f "$target" ]; then
        case "$engine" in
          renpy-native)
            local sh
            sh="$(ak_renpy_launcher "$root")" || sh=""
            if [ -n "$sh" ]; then
              target="$sh"; sev="info"
              msg="Ren'Py title with a Linux runtime — using $(basename "$target")."
            else
              out_runner="proton"; target="$(ak_main_exe "$root")"
              [ -n "$target" ] || target="$path"
              sev="info"; msg="Ren'Py build without a Linux runtime — routed to the proton runner."
            fi ;;
          renpy-windows|renpy-unknown)
            out_runner="proton"; target="$(ak_main_exe "$root")"
            [ -n "$target" ] || target="$path"
            sev="info"; msg="Ren'Py Windows-only build — routed to the proton runner." ;;
          *)
            if [ "$pe" = "1" ]; then
              sev="warn"
              msg="That's a Windows .exe — the native runner only runs Linux binaries, so it won't launch. Switch to the proton runner."
            else
              sev="warn"
              msg="No Linux executable found here — point the native runner at the game's launcher, or use the proton runner."
            fi ;;
        esac
      elif [ "$target" != "$path" ]; then
        sev="info"; msg="Using $(basename "$target")."
      fi ;;
    rpgmaker)
      # rpgmaker always consumes the game folder.
      if [ ! -d "$path" ] && [ -d "$root" ]; then
        target="$root"; sev="info"
        msg="rpgmaker needs the game folder — using $(basename "$target")."
      else
        target="$path"
      fi ;;
    *)
      # proton (and anything else) runs a Windows executable: keep a .exe as
      # given; a folder resolves to its main .exe.
      if [ ! -f "$path" ]; then
        target="$(ak_main_exe "$path")"
        [ -n "$target" ] || target="$path"
        if [ "$target" != "$path" ]; then
          sev="info"; msg="Using $(basename "$target")."
        fi
      fi ;;
  esac

  printf '%s|%s|%s|%s' "$out_runner" "$target" "$sev" "$msg"
}
