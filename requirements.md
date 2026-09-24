# Requirements

Rows marked **[verified]** were tested on Arch/Omarchy. Others are best-effort
from upstream docs — confirmations welcome.

## Runtime (all runners)

| Need | Arch (verified) | Debian/Ubuntu | Fedora | Notes |
|---|---|---|---|---|
| python3 | `python` [verified] | `python3` | `python3` | JSON/config handling |
| jq | `jq` [verified] | `jq` | `jq` | TUI library |
| gum | `gum` [verified] | release `.deb` (charmbracelet/gum) | `gum` | TUI only |
| zenity | `zenity` [verified] | `zenity` | `zenity` | file pickers |
| vkBasalt | AUR `vkbasalt`, else automated source build [verified: source build] | `vkbasalt`, else source build | `vkBasalt`, else source build | the filter runtime; `install.sh` handles both; verify with `migaki doctor` |
| mangohud | `mangohud` [verified] | `mangohud` | `mangohud` | fps cap + overlay |
| vulkan-tools | `vulkan-tools` [verified] | `vulkan-tools` | `vulkan-tools` | optional: device list, `vkcube` test |
| pciutils | `pciutils` [verified] | `pciutils` | `pciutils` | optional: `lspci` fallback for GPU detection when `/sys/class/drm` is unavailable |
| icoextract | `icoextract` [verified] | `python3-icoextract` | `pip install icoextract` | game-icon extraction for the GUI |
| git | `git` [verified] | `git` | `git` | cloning this repo |
| curl | `curl` [verified] | `curl` | `curl` | downloads: vendor bundle, Proton, releases |
| tar | `tar` [verified] | `tar` | `tar` | extracting those downloads |

Optional desktop helpers (detected, never required): `kdialog` for the GUI's
native KDE file picker, `qdbus6` for the textbox Top on KDE Wayland, and
`gnome-extensions` for the textbox Top on GNOME Wayland.

Filter selection needs nothing extra: the Restore variants are `.fx` files
shipped here, and the 3D **Clear** presets use vkBasalt built-ins (`cas`,
`smaa`) plus the shipped `shaders/ClearColor.fx`. `install.sh` deploys both to
the shader dir; `migaki doctor` verifies each preset renders.

## Per-runner optionals

| Runner | Need | Notes |
|---|---|---|
| proton | `umu-launcher` (Arch multilib — installer handles it; enable multilib if missing; installer pre-installs the matching `lib32-vulkan-driver` provider from the detected GPU so pacman doesn't ask) or Faugus | provides `umu-run`; Steam Proton works too with adapted env |
| proton | Proton-CachyOS (verified, recommended) — offered by `./install.sh` (upstream release tarball, checksum-verified; x86_64_v3 on capable CPUs) | 32-bit D3D is handled by Wine **new WoW64** (runner default; `WINEARCH=wow64` + `PROTON_USE_WOW64=1`), so CachyOS is no longer required for it. CachyOS remains the safest verified build and is seeded as the config default (override per-launch with `--proton`; other builds like GE-Proton work too). Disable new WoW64 per game with `wow64=0` for anti-cheat titles |
| rpgmaker | `rpgmaker-linux` — offered by `./install.sh` (pinned upstream release) or the [upstream install script](https://github.com/bakustarver/rpgmakermlinux-cicpoffs) |
| native | Mesa with Zink (Mesa ≥ 23) | for the GL→Vulkan translation path |

## Build-only (vkBasalt from source)

`meson ninja glslang spirv-headers vulkan-headers` (Arch names [verified];
Debian: `meson ninja-build glslang-tools spirv-headers libvulkan-dev`;
Fedora: `meson ninja-build glslang spirv-headers vulkan-headers`).

## GUI (PySide6 + Qt Quick)

The launcher GUI and the translation textbox are both Qt Quick (QML). Prefer
the system package; the app follows the desktop via the KDE colour scheme
(kdeglobals) and the Quick Controls style below:

| Distro | Install |
|---|---|
| Arch | `sudo pacman -S pyside6` [verified] |
| Others | `pip install PySide6` (bundled Qt) |

QML modules needed: `QtQuick`, `QtQuick.Controls`, `QtQuick.Layouts`
(screens), `QtQuick.Dialogs` (file/font/color pickers) and `QtQuick.Effects`
(text shadow). Arch: `qt6-declarative` [verified: 6.11.2]; Debian/Ubuntu:
`qml6-module-qtquick{,-controls,-layouts,-dialogs,-effects}`; Fedora:
`qt6-qtdeclarative`. (`pip install PySide6` bundles them.)

The GUI and textbox use the **KDE Quick Controls style**
(`org.kde.desktop`) so both follow your desktop colour scheme (e.g. Omarchy's
`kdeglobals` theme) and light/dark automatically. Arch: `qqc2-desktop-style`
[verified]; Debian/Ubuntu: `qqc2-desktop-style`; Fedora:
`kf6-qqc2-desktop-style`. Without it they fall back to Fusion. The GUI font
defaults to your environment font (e.g. qt6ct/KDE) and can be overridden
per-app in Settings. The top-bar **Settings** dialog also has a Proton picker
(`core.system.list_protons()`): it lists the builds under the common
`compatibilitytools.d` folders (Steam, Flatpak, `/usr`) with UMU-Proton
pinned; a build without new WoW64 support is flagged and falls back to
UMU-Proton at launch. The picker stores an **absolute path** in `proton`
(`""` = umu-managed); `install.sh` seeds the verified build's path, and a bare
or stale value is resolved by name — otherwise the runner falls back to
UMU-Proton. `migaki doctor` validates that the configured Proton resolves.
There is no in-app theme switch. `migaki-gui --diagnose`
prints the resolved style, font and palette, and `--self-test` loads the whole
UI headlessly (see README).

## VN translation (translate/)

Fetched at install time (pinned + checksum-verified, never committed):

| Need | Arch (verified) | Debian/Ubuntu | Fedora | Notes |
|---|---|---|---|---|
| python-websocket-client | `python-websocket-client` [verified] | `python3-websocket` | `python3-websocket-client` | hook bridge client; installer handles it |
| python-requests | `python-requests` [verified] | `python3-requests` | `python-requests` | DeepL browser automation; installer handles it |
| Textractor + bridge | fetched by `install.sh` | same | same | Chenx221 build + kuroahna bridge (or hardened v2 fork asset). Provisioned once under `~/.local/share/migaki/textractor`, symlinked into each prefix; the bundled bridge-only `SavedExtensions.txt` is force-applied so Textractor never loads its stock translate extensions |
| Chromium browser (any) | auto-detected [verified: default-browser-first + CDP smoke test] | same | same | Brave/Chromium/Chrome/Edge/Vivaldi/Opera; default browser preferred, isolated debug profile always; installer records the pick in `translate/config.json` |

The textbox **Top** button is enforced per compositor, always **stacking-only**
(never focus/activate/move/warp — that would make KDE's Focus-follows-mouse
warp the cursor onto the box): Hyprland via `hyprctl`, KDE via a small KWin
script over `qdbus6` (from `qt6-tools`/`qttools`, normally already present),
X11 via Qt's native keep-above hint, and GNOME Wayland via the optional bundled
Shell extension (`gnome-extensions`, from `gnome-shell`; installed by
`install.sh`, may need a re-login). Sway and unknown Wayland compositors expose
no keep-above for normal windows — Float still applies. `migaki doctor`
reports which backend is active.
