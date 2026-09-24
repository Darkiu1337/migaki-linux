#!/bin/bash
# uninstall.sh — remove what install.sh deployed. Never touches your game
# library (~/.config/migaki/games.json) or config.json.
# Usage: ./uninstall.sh
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHADER_DST="$HOME/.local/share/gamescope/reshade/Shaders"
removed=0
kept=0
for v in S M L Soft_S Soft_M Soft_L VL UL Soft_VL Soft_UL; do
  dst="$SHADER_DST/Anime4K_Restore_$v.fx"
  src="$ROOT/shaders/Anime4K_Restore_$v.fx"
  if [ -f "$dst" ]; then
    if [ -f "$src" ] && cmp -s "$dst" "$src"; then
      rm -f "$dst"
      removed=1
    else
      echo "kept $dst (differs from this repo copy; remove by hand if unwanted)"
      kept=1
    fi
  fi
done
for f in ClearColor.fx presets.json; do
  dst="$SHADER_DST/$f"
  src="$ROOT/shaders/$f"
  if [ -f "$dst" ]; then
    if [ -f "$src" ] && cmp -s "$dst" "$src"; then
      rm -f "$dst"
      removed=1
    else
      echo "kept $dst (differs from this repo copy; remove by hand if unwanted)"
      kept=1
    fi
  fi
done
[ "$removed" = "1" ] && echo "removed deployed shaders."
[ "$kept" = "1" ] && echo "(some shader files were kept, see above.)"
for link in "$HOME/.local/bin/migaki" "$HOME/.local/bin/migaki-gui" \
            "$HOME/.local/bin/anime4k" "$HOME/.local/bin/anime4k-gui" \
            "$HOME/.local/bin/vn-launch" "$HOME/.local/bin/vn-textbox" \
            "$HOME/.local/bin/vn-translate" "$HOME/.local/bin/vn-textbox-qml"; do
  if [ -L "$link" ]; then
    rm -f "$link"
    echo "removed symlink $link"
  fi
done
# Stale DLX binary from older installs (DLX support was removed).
if [ -e "$HOME/.local/bin/dlx" ]; then
  rm -f "$HOME/.local/bin/dlx"
  echo "removed stale ~/.local/bin/dlx"
fi
for desk in migaki.desktop migaki-gui.desktop anime4k.desktop anime4k-gui.desktop; do
  if [ -f "$HOME/.local/share/applications/$desk" ]; then
    rm -f "$HOME/.local/share/applications/$desk"
    echo "removed desktop entry $desk"
  fi
done
# App icon deployed by install.sh --desktop (hicolor set + pixmaps fallback).
_icon_removed=0
for s in 16 24 32 48 64 128 256; do
  for n in migaki anime4k; do
    f="$HOME/.local/share/icons/hicolor/${s}x${s}/apps/$n.png"
    [ -f "$f" ] && { rm -f "$f"; _icon_removed=1; }
  done
done
for f in "$HOME/.local/share/icons/hicolor/scalable/apps/migaki.svg" \
         "$HOME/.local/share/icons/hicolor/scalable/apps/anime4k.svg" \
         "$HOME/.local/share/pixmaps/migaki.png" \
         "$HOME/.local/share/pixmaps/anime4k.png"; do
  [ -f "$f" ] && { rm -f "$f"; _icon_removed=1; }
done
if [ "$_icon_removed" = "1" ]; then
  command -v gtk-update-icon-cache >/dev/null 2>&1 && \
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
  command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
  echo "removed deployed app icon"
fi
# GNOME Shell extension for the textbox Top, if install.sh deployed it
# (both the current uuid and the pre-rename one).
for _ext_uuid in vn-textbox-top@migaki vn-textbox-top@anime4k; do
  _ak_ext="$HOME/.local/share/gnome-shell/extensions/$_ext_uuid"
  if [ -d "$_ak_ext" ]; then
    if command -v gnome-extensions >/dev/null 2>&1; then
      gnome-extensions disable "$_ext_uuid" 2>/dev/null || true
    fi
    rm -rf "$_ak_ext"
    echo "removed GNOME Shell extension $_ext_uuid"
  fi
done
rm -f "$HOME/.config/migaki"/vkbasalt-*.conf "$HOME/.config/anime4k"/vkbasalt-*.conf
if [ -f "$HOME/.local/share/vkBasalt/.migaki-installed" ] \
   || [ -f "$HOME/.local/share/vkBasalt/.anime4k-installed" ]; then
  rm -f "$HOME/.local/share/vkBasalt/.migaki-installed" \
        "$HOME/.local/share/vkBasalt/.anime4k-installed"
  rm -f "$HOME/.local/lib/libvkbasalt.so" "$HOME/.local/lib64/libvkbasalt.so"
  rm -f "$HOME/.local/share/vulkan/implicit_layer.d/vkBasalt.json"
  rmdir "$HOME/.local/share/vkBasalt" 2>/dev/null || true
  echo "removed source-built vkBasalt (system packages it pulled in are left alone)."
fi
# Regenerable caches/logs (icon extraction, GUI/translate logs, isolated
# DeepL browser profile). Your library and config are never touched.
for cache in "$HOME/.cache/migaki" "$HOME/.cache/anime4k" "$HOME/.cache/vn-translate"; do
  if [ -d "$cache" ]; then
    rm -rf "$cache"
    echo "removed cache $cache"
  fi
done
echo "done. Kept (your data): ~/.config/migaki/games.json and config.json."
echo "System packages are never removed. Delete the repo directory itself to finish: $ROOT"
