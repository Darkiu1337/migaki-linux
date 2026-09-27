#!/bin/bash
# migaki-doctor — self-test the Anime4K Restore filter chain + translation
# readiness, without any game.
# Checks, in order: layer manifest, library resolution, shaders, GPUs,
# runner backends, 32-bit/WoW64 posture, (with a display) a live
# vkcube+vkBasalt run, and the translation deps (bridge client, requests,
# textbox, QML modules, browser, GUI self-test).
# Exit 0 = chain ready, 1 = problems found. Never touches user config.
# Usage: migaki-doctor [--live/--no-live]  (also: migaki doctor)
_SRC="${BASH_SOURCE[0]}"
while [ -L "$_SRC" ]; do _SRC="$(readlink "$_SRC")"; case "$_SRC" in /*) :;; *) _SRC="$(dirname "${BASH_SOURCE[0]}")/$_SRC";; esac; done
SCRIPT_DIR="$(cd "$(dirname "$_SRC")" && pwd)"
unset _SRC
# shellcheck disable=SC1091
source "$SCRIPT_DIR/migaki-lib.sh"

LIVE="auto"
while [ $# -gt 0 ]; do
  case "$1" in
    --live) LIVE="yes"; shift ;;
    --no-live) LIVE="no"; shift ;;
    --help|-h) sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "error: unknown option $1 (see --help)" >&2; exit 1 ;;
  esac
done

pass=0
fail=0
ok() { echo "ok: $*"; pass=$((pass + 1)); }
bad() { echo "FAIL: $*"; fail=$((fail + 1)); }
skip() { echo "skip: $*"; }

# 1. Layer manifest (same lookup the launchers use).
MANIFEST=""
if MANIFEST="$(ak_vkbasalt_manifest)"; then
  ok "layer manifest: $MANIFEST"
else
  bad "no vkBasalt layer manifest (set layer_dir in $MIGAKI_CONFIG or install vkbasalt; see requirements.md)"
fi

# 2. Library the manifest points at must exist (catches stale registrations).
LIB=""
if [ -n "$MANIFEST" ] && LIB="$(ak_vkbasalt_lib "$MANIFEST")"; then
  ok "layer library: $LIB ($(file -b "$LIB" 2>/dev/null | cut -d, -f1-2))"
else
  [ -n "$MANIFEST" ] && bad "manifest $MANIFEST points at a missing library (reinstall vkbasalt)"
fi

# 3. Shaders the launchers will reference.
SHADERS_OK=1
for v in S M L Soft_S Soft_M Soft_L VL UL Soft_VL Soft_UL; do
  if [ -f "$MIGAKI_SHADER_DIR/Anime4K_Restore_$v.fx" ]; then
    :
  else
    bad "shader missing: $MIGAKI_SHADER_DIR/Anime4K_Restore_$v.fx"
    SHADERS_OK=0
  fi
done
[ "$SHADERS_OK" = "1" ] && ok "shaders (10 variants) in $MIGAKI_SHADER_DIR"

# 3b. Clear presets (3D clarity): manifest + custom color shader + a render
# smoke test of each chain (no game, nothing left behind).
_PRESETS_OK=1
_PJSON="$MIGAKI_ROOT/shaders/presets.json"
[ -f "$_PJSON" ] || _PJSON="$MIGAKI_SHADER_DIR/presets.json"
if [ -f "$_PJSON" ]; then :; else bad "presets manifest missing (shaders/presets.json)"; _PRESETS_OK=0; fi
if [ -f "$MIGAKI_SHADER_DIR/ClearColor.fx" ] || [ -f "$MIGAKI_ROOT/shaders/ClearColor.fx" ]; then :; else bad "ClearColor.fx missing (run install.sh)"; _PRESETS_OK=0; fi
_TMPCONF="$(mktemp 2>/dev/null || echo /tmp/a4k-preset.conf)"
for _p in Clear Clear_Vivid Clear_AA; do
  if ! ak_is_preset "$_p"; then
    bad "preset '$_p' missing from the manifest"
    _PRESETS_OK=0
    continue
  fi
  if ak_render_preset_conf "$_p" "$_TMPCONF" "$MIGAKI_SHADER_DIR" 2>/dev/null \
     && grep -q '^effects = ' "$_TMPCONF"; then
    :
  else
    bad "preset '$_p' failed to render"
    _PRESETS_OK=0
  fi
done
rm -f "$_TMPCONF"
[ "$_PRESETS_OK" = "1" ] && ok "Clear presets (3D: Clear, Clear_Vivid, Clear_AA)"
unset _PRESETS_OK _PJSON _TMPCONF _p

# 4. GPUs + what the proton runner will default to.
if command -v vulkaninfo >/dev/null 2>&1; then
  echo "GPUs (vulkaninfo):"
  vulkaninfo --summary 2>/dev/null | grep -i "deviceName" | sed 's/^/  /' | sort -u
  pass=$((pass + 1))
else
  skip "vulkaninfo not installed (device list unavailable)"
fi
if DGPU="$(ak_discrete_gpu_name)"; then
  ok "proton DXVK default: discrete GPU '$DGPU'"
else
  echo "note: no discrete GPU detected — proton launches use the loader default (override with --dxvk-device)"
fi
# Which GPU drives which output, and which one X11 renders with. A filtered
# Chromium/RPGMaker window can present on any GPU that owns an active output,
# so a hybrid laptop may legitimately pick the dGPU even when the compositor
# renders on the iGPU.
for _d in /sys/class/drm/card*-*; do
  [ -f "$_d/status" ] || continue
  _st="$(cat "$_d/status" 2>/dev/null)"
  _en="$(cat "$_d/enabled" 2>/dev/null)"; [ -n "$_en" ] || _en="?"
  _card="${_d##*/}"; _conn="$_card"; _card="${_card%%-*}"
  _v="$(cat "/sys/class/drm/$_card/device/vendor" 2>/dev/null)"
  case "$_v" in 0x10de) _v=nvidia ;; 0x1002) _v=amd ;; 0x8086) _v=intel ;; *) : ;; esac
  echo "  connector $_conn: $_st/$_en${_v:+ (vendor $_v)}"
done
unset _d _st _en _card _conn _v
if OUTGPU="$(ak_output_gpu_vendor)"; then
  ok "output GPU: $OUTGPU — drives the active display ('auto' picks it)"
else
  echo "note: output GPU unknown — the loader default is used"
fi
if DISPGPU="$(ak_display_gpu_vendor)"; then
  ok "X11/GLX renderer: $DISPGPU"
else
  echo "note: X11/GLX renderer unknown (glxinfo missing)"
fi

# 5. Runner backends.
if command -v umu-run >/dev/null 2>&1; then
  ok "umu-run (proton runner)"
else
  bad "umu-run missing (proton runner will not work; see requirements.md)"
fi
_CDIR="$HOME/.local/share/Steam/compatibilitytools.d"
if [ -d "$_CDIR" ]; then
  _found=""
  for _p in "$_CDIR"/*/; do
    [ -x "${_p}proton" ] || [ -x "${_p}proton.sh" ] || continue
    _found="${_found:+$_found | }$(basename "$_p")"
  done
  if [ -n "$_found" ]; then
    ok "installed Protons: $_found"
  else
    echo "note: no Proton builds in $_CDIR (umu auto-fetches UMU-Proton on first launch)"
  fi
else
  echo "note: no compatibilitytools.d yet (umu auto-fetches UMU-Proton on first launch)"
fi
unset _CDIR _found _p
# The configured Proton default must resolve to a real install: a bare display
# name (or a stale path) would be exported as PROTONPATH and umu would fail to
# find Proton, so the game never boots (bit us: install.sh seeded the label).
_cp="$(ak_config_get proton "")"
if [ -n "$_cp" ]; then
  if _cpr="$(ak_proton_resolve "$_cp")"; then
    ok "configured Proton: $_cpr"
  else
    bad "config proton '$_cp' does not resolve to a Proton install (pick one in Settings or clear it; otherwise launches fall back to UMU-Proton)"
  fi
  unset _cpr
fi
unset _cp
# 32-bit / WoW64 posture (docs/limits.md).
_w="$(ak_config_get wow64 1)"
if [ "$_w" = "0" ]; then
  echo "note: new WoW64 disabled (wow64=0) — 32-bit D3D needs Proton-CachyOS or a 32-bit vkBasalt"
else
  ok "new WoW64 default on (WINEARCH=wow64; 32-bit D3D filters through the 64-bit layer)"
fi
if ls /usr/lib32/libvkbasalt.so "$HOME/.local/lib32/libvkbasalt.so" >/dev/null 2>&1; then
  ok "32-bit vkBasalt present (old WoW64 32-bit titles filter too)"
else
  echo "note: no 32-bit vkBasalt (old WoW64 32-bit titles stay unfiltered)"
fi
unset _w
if command -v rpgmaker-linux >/dev/null 2>&1; then
  ok "rpgmaker-linux ($(rpgmaker-linux --version 2>/dev/null | head -n 1))"
else
  echo "note: rpgmaker-linux missing (only the rpgmaker runner needs it)"
fi

# 6. Live chain test: vkcube through the layer with a rendered L config.
if [ "$LIVE" = "no" ]; then
  skip "live vkcube test (--no-live)"
elif [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  skip "live vkcube test (no display)"
elif ! command -v vkcube >/dev/null 2>&1; then
  skip "live vkcube test (vulkan-tools not installed)"
elif ! command -v timeout >/dev/null 2>&1; then
  skip "live vkcube test (coreutils timeout missing)"
elif [ -z "$MANIFEST" ] || [ -z "$LIB" ] || [ "$SHADERS_OK" = "0" ]; then
  skip "live vkcube test (chain above is broken)"
else
  ak_vkbasalt_env L
  _out="$(timeout -s KILL 8 vkcube 2>&1 || true)"
  if printf '%s' "$_out" | grep -q "vkBasalt info"; then
    ok "live chain: vkcube presented through vkBasalt (effect: Anime4K_Restore_L)"
  else
    bad "live chain: vkcube ran but vkBasalt never engaged"
    printf '%s' "$_out" | grep -i -m5 "vkbasalt error" || true
  fi
  unset _out
fi

# 7. Translation readiness (read-only: presence only, no launches).
_TDIR="$SCRIPT_DIR/../translate"
if python3 -c "import websocket, requests" 2>/dev/null; then
  ok "python bridge deps (websocket, requests)"
else
  bad "python websocket/requests missing (translation pipeline needs them; see requirements.md)"
fi
if [ -x "$_TDIR/textbox.py" ]; then
  ok "translation textbox entry point"
else
  bad "translate/textbox.py missing or not executable"
fi
# The bridge only starts if Textractor is told to load it, and the extension
# set MUST be bridge-only: Textractor otherwise loads its six stock extensions
# on a missing SavedExtensions.txt, stalling the pipeline (docs/translate.md).
_TX="$(ak_config_get prefix "")"
[ -n "$_TX" ] || _TX="$HOME/.local/share/migaki/prefixes/default"
_TANY=0
for _ARCH in x86 x64; do
  _TD="$_TX/drive_c/Textractor/$_ARCH"
  _CD="$(ak_textractor_dir "$_ARCH")"
  if [ -f "$_TD/Textractor.exe" ]; then
    _TANY=1
    ok "Textractor ($_ARCH) installed (prefix: $_TD)"
    _SE="$_TD/SavedExtensions.txt"
    if [ -f "$_SE" ]; then
      if [ "$(tr -d '[:space:]' < "$_SE")" = "textractor_websocket_${_ARCH}>" ]; then
        ok "Textractor ($_ARCH) extensions: bridge-only"
      else
        bad "Textractor ($_ARCH) SavedExtensions.txt is not bridge-only — re-run ./install.sh (docs/translate.md)"
      fi
    else
      bad "Textractor ($_ARCH) SavedExtensions.txt missing — re-run ./install.sh"
    fi
    _BX="$_TD/textractor_websocket_${_ARCH}.xdll"
    if [ -f "$_TDIR/vendor/textractor_websocket_${_ARCH}.fixed.dll" ] \
       && [ -f "$_BX" ] \
       && cmp -s "$_TDIR/vendor/textractor_websocket_${_ARCH}.fixed.dll" "$_BX"; then
      ok "v2 (tagged) bridge installed ($_ARCH)"
    else
      echo "note: stock $_ARCH bridge installed — the in-app Text Hooker picker needs the v2 build (docs/translate.md)"
    fi
  elif [ -f "$_CD/Textractor.exe" ]; then
    ok "Textractor ($_ARCH) canonical install present ($_CD) but not linked into $_TX"
    _TANY=1
  fi
done
[ "$_TANY" = "0" ] && skip "Textractor not installed (run install.sh translation step)"
[ -L "$_TX/drive_c/Textractor" ] \
  && ok "Textractor dir is a symlink -> $(readlink "$_TX/drive_c/Textractor")"
unset _TX _TD _CD _SE _BX _ARCH _TANY
if [ -f "$_TDIR/tyrano_hook.py" ]; then
  ok "Tyrano/Electron CDP hook (tyrano_hook.py)"
else
  bad "translate/tyrano_hook.py missing"
fi
if [ -f "$_TDIR/rpgmaker_hook.js" ] && [ -f "$_TDIR/rpgmaker_bridge.py" ]; then
  ok "RPGMaker MV/MZ injected hook (rpgmaker_hook.js + relay)"
else
  bad "translate/rpgmaker_hook.js or rpgmaker_bridge.py missing"
fi
_QD="$(python3 -c "from PySide6.QtCore import QLibraryInfo; print(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))" 2>/dev/null || true)"
_QMISSING=""
if [ -z "$_QD" ]; then
  _QMISSING="QtQuick Controls Layouts Dialogs Effects (no QML import path)"
else
  for _m in QtQuick QtQuick/Controls QtQuick/Layouts QtQuick/Dialogs QtQuick/Effects; do
    if [ -d "$_QD/$_m" ] && find "$_QD/$_m" -maxdepth 1 \( -name 'qmldir' -o -name '*.so' \) -print -quit 2>/dev/null | grep -q .; then
      :
    else
      _QMISSING="$_QMISSING ${_m##*/}"
    fi
  done
fi
if [ -z "$_QMISSING" ]; then
  ok "QtQuick QML modules (Controls/Layouts/Dialogs/Effects)"
else
  bad "QML modules missing:$_QMISSING (GUI + textbox need qt6-declarative)"
fi
if [ -n "$_QD" ] && [ -d "$_QD/org/kde/desktop" ]; then
  ok "KDE Quick Controls style (follows desktop colour scheme)"
else
  echo "note: org.kde.desktop QML style absent — GUI/textbox use the Fusion fallback (install qqc2-desktop-style)"
fi
unset _QD _QMISSING _m
# Textbox always-on-top support for this session (stacking-only backends).
_ak_wl=0
case "${XDG_SESSION_TYPE:-}" in wayland) _ak_wl=1 ;; esac
[ -n "${WAYLAND_DISPLAY:-}" ] && _ak_wl=1
_ak_desk="$(printf '%s' "${XDG_CURRENT_DESKTOP:-${XDG_SESSION_DESKTOP:-}}" | tr '[:lower:]' '[:upper:]')"
if [ "$_ak_wl" = "0" ]; then
  ok "textbox Top: X11 keep-above hint (native)"
else
  case "$_ak_desk" in
    *HYPRLAND*)
      if command -v hyprctl >/dev/null 2>&1; then
        ok "textbox Top: Hyprland (hyprctl pin/bring-to-top)"
      else
        bad "textbox Top: Hyprland session but hyprctl not found"
      fi ;;
    *KDE*)
      if command -v qdbus6 >/dev/null 2>&1 || command -v qdbus >/dev/null 2>&1; then
        ok "textbox Top: KDE KWin keep-above (qdbus present)"
      else
        bad "textbox Top: KDE session but qdbus/qdbus6 not found"
      fi ;;
    *GNOME*)
      if command -v gnome-extensions >/dev/null 2>&1 \
         && gnome-extensions list --enabled 2>/dev/null | grep -q 'vn-textbox-top@migaki'; then
        ok "textbox Top: GNOME Shell extension enabled"
      else
        skip "textbox Top: GNOME Wayland needs the Shell extension (re-run install.sh)"
      fi ;;
    *)
      skip "textbox Top: unsupported on this Wayland compositor (Float still applies)" ;;
  esac
fi
unset _ak_wl _ak_desk
# GUI + textbox UI load smoke test (headless; no display needed).
_GUI="$SCRIPT_DIR/../gui/app.py"
if [ -f "$_GUI" ] && python3 -c "import PySide6" 2>/dev/null; then
  if QT_QPA_PLATFORM=offscreen timeout 40 python3 "$_GUI" --self-test >/dev/null 2>&1; then
    ok "GUI loads (QML self-test)"
  else
    bad "GUI self-test failed (run: migaki-gui --diagnose; log: ~/.cache/migaki/gui.log)"
  fi
fi
unset _GUI
_BR=""
for _b in brave brave-browser brave-origin chromium chromium-browser google-chrome google-chrome-stable chrome microsoft-edge microsoft-edge-stable vivaldi opera; do
  _p="$(command -v "$_b" 2>/dev/null)" || continue
  if "$_p" --version 2>/dev/null | grep -qi "chromium\|chrome\|brave\|vivaldi\|opera\|edge"; then _BR="$_p"; break; fi
done
# (default-browser resolution lives in install.sh; doctor only needs any pick.)
if [ -n "$_BR" ]; then
  ok "Chromium browser for DeepL automation: $_BR"
else
  bad "no Chromium browser found (translation needs one; run install.sh)"
fi
unset _BR _b _p _TDIR

echo "doctor: $pass passed, $fail failed."
[ "$fail" = "0" ]
