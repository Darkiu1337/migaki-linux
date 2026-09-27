# Migaki

**Migaki** (磨き, "polish") runs the **Anime4K Restore** CNN filters on Linux —
the Magpie-on-Windows look — without upscaling, capture tricks, or a compositor
in the middle. Each game renders normally; the filter processes every presented
frame through the **vkBasalt** Vulkan layer.

## Filter variants

Ten **Restore** networks ship: **S, M, L, Soft_S, Soft_M, Soft_L** and the much
larger **VL, UL, Soft_VL, Soft_UL** (S light … UL heaviest; *Soft* is tuned for
aliased or downscaled art). UL/VL are the heaviest tiers — measure them on your
GPU before daily use.

For **3D / rendered VNs**, where the Restore CNN has little to work with, three
**Clear** presets ship alongside them: **Clear** (sharpness), **Clear_Vivid**
(sharpness + saturation/contrast for washed-out art) and **Clear_AA** (SMAA +
sharpness) — built from vkBasalt effects plus one small color shader, on the
same launch path and per-game selection as a Restore variant.

## Gallery (Restore L, 1080p)

The effect is easier to notice while playing than in stills.

| Game | off | **Restore L** |
|---|---|---|
| Daily Lives of My Countryside (RPGMaker) | ![off](docs/assets/daily-off.png) | ![filtered](docs/assets/daily-filtered.png) |
| Useless Princess & the Village Renovation (RPGMaker) | ![off](docs/assets/princess-off.png) | ![filtered](docs/assets/princess-filtered.png) |
| Ochiru Hitozuma (KiriKiri/Proton) | ![off](docs/assets/ochiru-off.png) | ![filtered](docs/assets/ochiru-filtered.png) |

## Usage demo

[![Usage demo — filtered gameplay (Restore L)](docs/assets/usage-poster.jpg)](docs/assets/usage.mp4)

*Usage demo — filtered gameplay (Restore L).* Look for cleaner line art,
calmer gradients, and less compression noise — that is the whole effect.
Nothing is upscaled; resolution never changes.

## Capabilities

* **Three runners** — Proton/Windows games (D3D9–12, Vulkan), RPGMaker folders
  (MV/MZ filtered; other engines redirect), and native Linux executables
  (Vulkan direct, OpenGL via Zink).
* **32-bit D3D titles filter too** — the Proton runner defaults to Wine new
  WoW64, so 32-bit games present a 64-bit Vulkan swapchain the layer can hook
  (opt out per game with `wow64=0` if a title refuses it).
* **Per-game library** (TUI + Qt Quick GUI sharing one JSON store): variant,
  GPU, fps cap, overlay, locale, prefix mode.
* **A/B comparison** — unfiltered launches structurally withhold the layer, so
  the loader never sees vkBasalt.
* **GPU-aware** — an explicit GPU pick is honored; `auto` prefers the GPU that
  drives the active output (what you want on a hybrid laptop); set
  `MIGAKI_FORCE_GPU=1` to force the display renderer instead.
* **Frame caps everywhere** — DXVK on Proton, MangoHud elsewhere; optional
  overlay readout.
* **Wine prefixes** — one shared prefix by default, per-game opt-in.
* **Game locale selection** (Proton/native) for titles that need it (e.g.
  Japanese VNs).
* **VN translation** (Proton) — a per-game DeepL toggle that hooks dialogue and
  translates it live into a Luna-style textbox, composed with the filter in one
  launch. The hook transport is chosen automatically: **Textractor** for
  GDI/engine VNs (x86/x64 by the exe's PE bitness), a **CDP DOM hook** for
  Electron/TyranoScript bundles, and an **injected page hook** for RPGMaker
  MV/MZ. Textractor stays hidden — our window is the only interface — and the
  textbox **Top** width works on Hyprland, KDE, X11 and (via a bundled Shell
  extension) GNOME, always by changing stacking only (never focusing or moving
  the pointer). An optional auto-built **name glossary** and **context
  injection** keep names consistent and resolve pronouns across lines.
  Details: `docs/translate.md`.
* **Theme** — the GUI and the textbox use the KDE Quick Controls style and
  follow your desktop colour scheme (light or dark); no in-app switch.
* **Settings** (top bar) — a per-app GUI font (defaults to your environment
  font), a Proton picker over the common `compatibilitytools.d` folders
  (UMU-Proton is always available), and a **Refresh GPUs** button.
* **Background detection** — GPU enumeration and game-icon extraction run off
  the UI thread; the GPU list is cached and invalidated automatically by a
  cheap hardware fingerprint when a GPU is added, removed, or swapped.

## Game detection

Marker-based engine sniffing pre-selects the runner, and **Detect resolves the
real launch target**: a native Ren'Py distro is pointed at its Linux `.sh`
launcher (never the Windows `.exe`), rpgmaker at the game folder, and a Windows
build at its main `.exe`. A Windows `.exe` picked under the native runner is
caught — a Ren'Py title without a Linux runtime is routed to Proton
automatically, and anything else warns with a one-click switch. See
`docs/limits.md`.

## Install

```sh
git clone https://github.com/Darkiu1337/migaki-linux.git && cd migaki-linux
./install.sh                 # deps, vkBasalt, Proton, RPGMaker, shaders, translation, symlinks
./install.sh --check-only    # audit only (no changes)
./install.sh --dry-run       # show what would be installed
migaki                       # TUI  |  migaki-gui  # Qt GUI
```

Per-distro package names and prerequisites: `requirements.md`. A working Vulkan
**driver + loader** is assumed — the installer does not install graphics
drivers. The installer symlinks `migaki` / `migaki-gui` (plus `vn-launch` /
`vn-textbox` / `vn-translate` with translation support) into `~/.local/bin` and
offers to add it to `PATH`. One shared Wine prefix lives under
`~/.local/share/migaki/prefixes/`; personal defaults in
`~/.config/migaki/config.json`. Run `./uninstall.sh` to remove the deployed
pieces again (shaders, symlinks, desktop entries, GNOME extension, caches) — it
never touches your game library or config.

In the GUI the top bar has **Settings** and **Quit**; everything else is
per-game in the wizard and the game list.

## Troubleshooting

```sh
migaki doctor            # audit the whole chain (filter, runners, translation, GUI)
migaki-gui --diagnose    # versions, paths, style/font/palette, QML context validity
migaki-gui --self-test   # load the entire UI headlessly; fails on any QML error
```

Runtime QML errors are also appended to `~/.cache/migaki/gui.log`. Set
`MIGAKI_DEBUG=1` to keep the runner shell traces in the launch log.

## Layout

* `scripts/` — TUI + per-runner launchers (`proton`, `rpgmaker`, `native`)
* `core/` — Qt-free shared library: store, command building, process mgmt,
  detection, icons (imported by TUI/GUI/textbox). `python3 -m core
  <launch|add|set|tset|remove|gpus>` is the TUI's write path (one store,
  backups, validation).
* `gui/` — Qt Quick frontend (`app.py` bootstrap + `qml/` screens)
* `translate/` — VN translation: hook launcher, DeepL bridge, Luna-style textbox
* `shaders/` — ported `.fx` files + `gen_restore_fx.py` port generator
* `docs/` — `limits.md` (constraints), `translate.md` (VN translation manual),
  `assets/` (gallery + usage video)

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
* Verified Proton is **Proton-CachyOS** by the CachyOS team (a safe verified
  build; 32-bit D3D now filters through Wine new WoW64, the runner default):
  https://github.com/CachyOS/proton-cachyos
* D3D8/9/10/11 reach Vulkan through **DXVK** by doitsujin:
  https://github.com/doitsujin/dxvk
* D3D12 reaches Vulkan through **VKD3D-Proton** by HansKristian-Work:
  https://github.com/HansKristian-Work/vkd3d-proton
* Proton games run inside **Steam Runtime** containers by Valve
  (sniper/steamrt4, managed by umu):
  https://github.com/ValveSoftware/steam-runtime
* Frame caps and overlay use **MangoHud** by flightlessmango:
  https://github.com/flightlessmango/MangoHud
* RPGMaker MV/MZ render on **NW.js** (Chromium runtime under rpgmaker-linux):
  https://github.com/nwjs/nw.js
