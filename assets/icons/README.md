# App icon

One mark: a stylized anime eye split by a glowing vertical divider — the left
half is the "before" (soft, desaturated, dimmed), the right half the "after"
(crisp gradient iris + highlights). It reads as *Restore* and doubles as the
app's A/B feature. Neon-anime palette on a near-black rounded-square badge.

## Contents

| File | What |
|---|---|
| `migaki.svg` | full-detail master (used for ≥ 48px) |
| `migaki-small.svg` | simplified master (used for 16–32px) |
| `png/migaki-<size>.png` | 16, 24, 32, 48, 64, 128, 256, 512 |
| `migaki.ico` | multi-size Windows icon (16–256) |
| `contact-sheet.png` | real-size + 4× zoom preview for legibility checks |

## Why two masters

An anime eye carries detail that disappears at 16px. Small sizes therefore use
a simplified master: no blur filter, no secondary highlight, flat "before"
palette, thicker lash, and a slightly enlarged mark. The full master keeps the
gradient iris, limbal ring and blur.

## Where it is wired in

* `install.sh --desktop` installs the hicolor PNG set plus
  `hicolor/scalable/apps/migaki.svg` (and a `~/.local/share/pixmaps/`
  fallback), and writes `migaki-gui.desktop` with `Icon=migaki`.
* `gui/app.py` sets `setDesktopFileName("migaki-gui")` and loads
  `png/migaki-256.png` as the window icon.
* `uninstall.sh` removes the deployed icons again.
