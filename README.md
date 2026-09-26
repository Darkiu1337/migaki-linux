# Migaki

**Migaki** (磨き, "polish") runs the **Anime4K Restore** CNN filters on Linux —
the Magpie-on-Windows look
(game at full resolution, restoration filter on top), without upscaling,
capture tricks, or a compositor in the middle. Each game renders normally;
the filter processes every presented frame through the vkBasalt Vulkan layer.

Ten Restore variants ship: **S, M, L, Soft_S, Soft_M, Soft_L** plus the much
larger **VL, UL, Soft_VL, Soft_UL** networks (S light … UL heaviest; Soft
tuned for aliased or downscaled art). UL/VL are the heaviest tiers — measure
them on your GPU before daily use.

For **3D / rendered VNs**, where the Restore CNN has little to work with,
three **Clear** presets ship alongside them: **Clear** (sharpness),
**Clear_Vivid** (sharpness + saturation/contrast for washed-out art) and
**Clear_AA** (SMAA + sharpness) — built from vkBasalt effects plus one small
color shader, same launch path and per-game selection as a Restore variant.

## Origin

This is a toy project 100% vibecoded with Muse Spark 1.3 free, made for
personal use first.

## Gallery (Restore L, 1080p)

The effect is easier to notice while playing than in stills.

| Daily Lives of My Countryside (RPGMaker) | |
|---|---|
| ![Daily without filter](docs/assets/daily-off.png) | ![Daily with Restore L](docs/assets/daily-filtered.png) |
| off | **Restore L** |

| Useless Princess & the Village Renovation (RPGMaker) | |
|---|---|
| ![Princess without filter](docs/assets/princess-off.png) | ![Princess with Restore L](docs/assets/princess-filtered.png) |
| off | **Restore L** |

| Ochiru Hitozuma (KiriKiri/Proton) | |
|---|---|
| ![Ochiru without filter](docs/assets/ochiru-off.png) | ![Ochiru with Restore L](docs/assets/ochiru-filtered.png) |
| off | **Restore L** |

## Usage example with comparison

[![Usage demo — filtered gameplay (Restore L)](docs/assets/usage-poster.jpg)](https://darkiu1337.github.io/migaki-linux/docs/assets/usage.mp4)

*Usage demo — filtered gameplay (Restore L).*

Look for cleaner line art, calmer gradients, and less compression noise —
that is the whole effect. Nothing is upscaled; resolution never changes.

## Capabilities

* **Three runners**: Proton/Windows games (D3D9–12, Vulkan), RPGMaker
  folders (MV/MZ filtered; other engines redirect), native Linux
  executables (Vulkan direct, OpenGL via Zink).
* **32-bit D3D titles filter too**: the Proton runner defaults to Wine
  new WoW64, so 32-bit games present a 64-bit Vulkan swapchain the layer
  can hook (opt out per game with `wow64=0` if a title refuses it).
* **Per-game library** (TUI + Qt Quick GUI sharing one JSON store): variant,
  GPU, fps cap, overlay, locale, prefix mode.
* **Theme**: the GUI and the translation textbox use the KDE Quick Controls
  style and follow your desktop colour scheme (e.g. Omarchy's KDE theme),
  light or dark.
* **Settings** (top bar): a per-app GUI font (defaults to your environment
  font; the textbox keeps its own), a Proton picker that scans the common
  `compatibilitytools.d` folders, and a **Refresh GPUs** button. UMU-Proton is
  always available; a selected build without new WoW64 support is flagged and
  falls back to it at launch. The picker stores an absolute path in `proton`
  (the installer seeds the verified build); a bare/unknown value is resolved by
  name or falls back to UMU-Proton.
* **Background detection**: GPU enumeration and game-icon extraction run off
  the UI thread (never block the window), and the GPU list is cached, keyed by
  a cheap hardware fingerprint that invalidates it automatically when a GPU is
  added, removed, or swapped.
* **Game detection**: engine sniffing pre-selects the runner.
* **Frame caps everywhere**: DXVK on Proton, MangoHud elsewhere; optional
  overlay readout.
* **A/B comparison**: unfiltered launches structurally exclude the layer.
* **Wine prefixes**: one shared prefix by default, per-game opt-in.
* **Game locale selection** (Proton/native) for titles that need it
  (e.g. Japanese VNs).
* **VN translation** (Proton): per-game DeepL toggle — hooked Japanese dialogue
  translated live into a Luna-style textbox; composes with the filter in one
  launch. Three hook transports are chosen automatically: **Textractor**
  (x86/x64 by the exe's PE bitness) for GDI/engine VNs, a **CDP DOM hook** for
  Electron/TyranoScript bundles Textractor cannot see, and an **injected page
  hook** for RPGMaker MV/MZ (NW.js normal build — no CDP) on the rpgmaker
  runner. **Setup Text Hooker for translation** runs the game and
  opens an
  in-app text-hook picker; **Textractor stays hidden** (our window is the only
  interface). Pressing **Translate** with no hook saved auto-runs Setup; once
  a thread is picked it starts translating immediately (the running textbox
  follows the saved thread live — no restart). The textbox Top works on
  Hyprland, KDE, X11 and (via a bundled Shell extension) GNOME, always by
  changing stacking only — it never focuses a window or moves the pointer.
  An auto-built **name glossary** keeps character names consistent (harvesting
  the furigana readings the game emits), and optional **context injection**
  resolves pronouns across lines. Details: `docs/translate.md`.

## Use cases

* Visual-novel players on Linux who want the Anime4K Restore look from
  Magpie/Windows without leaving Linux.
* RPGMaker fans (MV/MZ run natively filtered).
* Anyone comparing filtered vs unfiltered output frame by frame.

## Install

```sh
git clone https://github.com/Darkiu1337/migaki-linux.git && cd migaki-linux
./install.sh            # one flow: deps, vkBasalt, Proton, RPGMaker, shaders, translation, symlinks
./install.sh --check-only   # audit only (no changes)
./install.sh --dry-run      # show what would be installed
migaki                 # TUI  |  migaki-gui  # Qt GUI
```

Details: `requirements.md`. One shared Wine prefix lives under
`~/.local/share/migaki/prefixes/`; personal defaults in
`~/.config/migaki/config.json`. The installer symlinks `migaki` /
`migaki-gui` (plus `vn-launch` / `vn-textbox` / `vn-translate` with
translation support) into `~/.local/bin` and offers to add it to `PATH`.

In the GUI, the top bar has **Settings** (Wine prefix, Proton picker, per-app
font, GPU-list refresh, vkBasalt layer dir) and **Quit**; everything else is
per-game in the wizard and the game list.

To remove the deployed pieces again (shaders, symlinks, desktop entries,
GNOME extension, caches), run `./uninstall.sh`; it never touches your game
library or config.

## Troubleshooting

```sh
migaki doctor                 # audit the whole chain (filter, runners, translation, GUI)
migaki-gui --diagnose         # versions, paths, style/font/palette, QML context validity
migaki-gui --self-test        # load the entire UI headlessly; fails on any QML error
```

The GUI and the translation textbox are Qt Quick using the **KDE Quick
Controls style**, so both follow your desktop colour scheme (e.g. Omarchy's
KDE theme) including light/dark — no in-app theme switch. Runtime QML errors
are also appended to `~/.cache/migaki/gui.log`.

## Layout

* `scripts/` — TUI + per-runner launchers
* `core/` — Qt-free shared library: store, command building, process mgmt,
  detection, icons (imported by TUI/GUI/textbox). `python3 -m core
  <launch|add|set|tset|remove|gpus>` is the TUI's write path (one store,
  backups, validation).
* `gui/` — Qt Quick frontend (`app.py` bootstrap + `qml/` screens)
* `translate/` — VN translation: hook launcher, DeepL bridge, Luna-style textbox
* `shaders/` — ported `.fx` files + `gen_restore_fx.py` port generator
* `docs/` — `limits.md` (constraints), `translate.md` (VN translation
  manual), `assets/` (gallery + usage video)

## Attributions

* Anime4K algorithm, shaders and trained weights by **bloc97** (MIT):
  https://github.com/bloc97/Anime4K
* Port structure follows Magpie's HLSL effects by **Blinue**:
  https://github.com/Blinue/Magpie
* Run-time filtering uses **vkBasalt** by DadSchoorse and contributors:
  https://github.com/DadSchoorse/vkBasalt
* RPGMaker support wraps **rpgmaker-linux** by bakustarver (opt-in install):
  https://github.com/bakustarver/rpgmakermlinux-cicpoffs
* Windows games launch through **umu-launcher** by Open-Wine-Components
  (`umu-run` backend): https://github.com/Open-Wine-Components/umu-launcher
* Verified Proton is **Proton-CachyOS** by the CachyOS team (a safe
  verified build; 32-bit D3D now filters through Wine new WoW64, the
  runner default): https://github.com/CachyOS/proton-cachyos
* D3D8/9/10/11 reach Vulkan through **DXVK** by doitsujin:
  https://github.com/doitsujin/dxvk
* D3D12 reaches Vulkan through **VKD3D-Proton** by HansKristian-Work:
  https://github.com/HansKristian-Work/vkd3d-proton
* Proton games run inside **Steam Runtime** containers by Valve
  (sniper/steamrt4, managed by umu):
  https://github.com/ValveSoftware/steam-runtime
* Frame caps and overlay use **MangoHud** by flightlessmango:
  https://github.com/flightlessmango/MangoHud
* RPGMaker MV/MZ render on **NW.js** (Chromium runtime under
  rpgmaker-linux): https://github.com/nwjs/nw.js
