# Limits

What the filter can and cannot touch. The rule of thumb: **vkBasalt must see
a Vulkan swapchain**. Everything below follows from that.

## Per runner

### proton (Windows games)
| API | Filter? | Notes |
|---|---|---|
| D3D9 / 10 / 11 (DXVK) | yes | the common case, verified (incl. 32-bit exes under new WoW64: Wine forwards their Vulkan calls to the 64-bit loader, so the 64-bit layer hooks them) |
| D3D12 (VKD3D-Proton) | yes | same mechanism, verified compatible |
| Native Vulkan | yes | passes straight through |
| OpenGL (wined3d) / software / very old titles | no | game launches unfiltered, no error — if a title that filters on one machine doesn't on another, compare `PROTON_LOG=1` renderer lines and the DXVK device (`--dry-run` shows it) |
| 32-bit executables | yes (new WoW64, default) | Old WoW64 runs the exe in a 32-bit process (needs a 32-bit vkBasalt we do not ship); the default new WoW64 runs it in the 64-bit host so the 64-bit layer hooks it — details and the verified test below |
| 32-bit native Linux binaries (not Proton) | no | our vkBasalt build is 64-bit only; new WoW64 only helps Windows PE, not native ELF |

Ren'Py Windows builds are auto-switched to the ANGLE (DirectX) renderer so
they land on DXVK; override with `RENPY_RENDERER` if you know better.

32-bit Windows titles use Wine's **new WoW64** by default: the runner exports
`WINEARCH=wow64` + `PROTON_USE_WOW64=1`, the 32-bit PE code runs inside the
64-bit host process, and the 64-bit vkBasalt sees the game's Vulkan swapchain.
Verified with a 32-bit D3D9 title (`mlove.exe`, UMU-Proton-10.0-4): old WoW64 =
32-bit process, 0 vkBasalt effects; new WoW64 = 64-bit process, `ReshadeEffect`
created and applied. Wine 11 considers new WoW64 fully supported; Proton 10
(UMU-Proton) supports it behind the flag. Turn it off per game with
`--no-wow64` / `"wow64": "0"` when a title (notably anti-cheat DRM) refuses new
WoW64. A legacy `win32` prefix can't use it and the runner silently falls back
to old WoW64 (unfiltered for 32-bit). If a selected Proton build has no new
WoW64 support at all, the runner warns and falls back to the umu-managed
UMU-Proton. The same fallback applies when the configured `proton` (an
absolute path; `""` = umu-managed) can't be resolved — a bare name is looked
up under `compatibilitytools.d`, otherwise the launch uses UMU-Proton.

### rpgmaker (RPGMaker game folders)
| Engine | Filter? | Notes |
|---|---|---|
| MV / MZ (Chromium) | yes | temp Vulkan-flag patch, restored on exit; X11 ozone |
| XP / VX / VXAce, Tyrano, Godot, other | no | script prints the Proton equivalent and launches unfiltered |

### native (Linux executables)
| Case | Filter? | Notes |
|---|---|---|
| Native Vulkan, 64-bit | yes | direct hook |
| OpenGL, 64-bit | yes, via Zink translation | set automatically; `--gl auto` bypasses |
| 32-bit anything | no | vkBasalt build limitation |
| Software-rendered (llvmpipe/SWDraw) | no | nothing for the filter to accelerate |

## GPU selection

The filter runs **where the game renders** (vkBasalt lives inside the game
process), so the GPU picker moves game and filter together — there is no
separate filter device. Pick per game based on where you want the heat.

Defaults: the proton runner auto-selects the discrete GPU when one is
detectable (`--dry-run` prints the choice; `--dxvk-device auto` forces the
loader default, an explicit name overrides). The rpgmaker/native runners
take `--gpu nvidia|amd|auto`. (A missed filter on a 32-bit title is a
WoW64-mode issue, not a GPU one — see the proton table above.)

Run `migaki doctor` on a new machine to verify the whole chain (manifest,
library, shaders, GPU, runner backends, 32-bit/WoW64 posture, translation
deps, KDE QML style, live vkcube run and a headless GUI self-test) without any
game.

## Display servers and compositors

Verified on Hyprland/Wayland (AMD iGPU + NVIDIA dGPU, single 1080p output).
XWayland versus native Wayland makes no difference to the filter. Gamescope's
`--reshade-effect` path segfaults on the reference machine (stock builds too),
so compositing tricks are out; everything here is in-process filtering.

## Variants

| Variant | Passes | Relative cost | Live status |
|---|---|---|---|
| S | 4 (3 conv + output) | 1× (baseline) | played, 100fps class |
| M | 7 (6 conv + dense output) | ~2× S | played, no errors |
| L | 9 (split dual convs + output) | ~4× S | played, 60fps capped |
| Soft_S | 4, soft-line tuned | ~1× S | played, no errors |
| Soft_M | 7, soft-line tuned | ~2× S | generated/compiled |
| Soft_L | 9-way split, soft-line tuned | ~4× S | played, no errors |
| VL | 20 (8-pass split chain, 6 targets) | heavy | generated/compiled |
| UL | 25 (8-pass split chain, 8 targets) | heaviest | generated/compiled |
| Soft_VL | 20, soft-line tuned | heavy | generated/compiled |
| Soft_UL | 25, soft-line tuned | heaviest | generated/compiled |

Soft variants target aliased/downscaled art rather than compression blur. UL/VL
are much larger networks (more passes and fullscreen RGBA16F targets), so they
are the heaviest and most VRAM-hungry tiers; measure on your GPU before daily
use. Cost scales with input pixels; S/M/L figures are at 1080p on a
GTX 1650-class GPU. Regenerate any variant with
`shaders/gen_restore_fx.py --variant <name> --magpie-dir <Magpie checkout>`.

### Clear presets (3D / rendered VNs)

The Restore CNN is trained on 2D line art, so 3D titles see little benefit.
The **Clear** family targets what actually looks wrong there — soft/TAA-blurred
output and washed-out color — using vkBasalt's built-in effects plus one small
custom color shader. They are data in `shaders/presets.json` (an ordered
`effects` chain + params), rendered per run by the same `ak_vkbasalt_env` path,
so they compose with translation exactly like Restore variants.

| Preset | Chain (applied left→right) | Fixes |
|---|---|---|
| Clear | `cas` | softness / TAA blur, neutral color |
| Clear_Vivid | `cas` → `ClearColor.fx` | softness + washed-out color (saturation/vibrance/contrast lift) |
| Clear_AA | `smaa` → `cas` | jaggies/shimmer + softness |

`cas` is nearly free; `ClearColor` is one fullscreen pass; `Clear_AA` pays for
SMAA (the only real cost risk at 1080p on a GTX 1650-class GPU — if it stutters,
fall back to `Clear` or add an FXAA-based preset). These are **enhance at native
resolution**, not upscalers. Add a preset by editing `shaders/presets.json`;
`migaki doctor` renders and verifies each chain.

## Game detection (`migaki detect <path>`)

Marker-based engine sniffing, shared by the TUI, GUI and the rpgmaker
runner. High confidence auto-fills the runner (confirmed on save);
anything else asks with the suggestion pre-selected.

| Markers | Engine | Runner |
|---|---|---|
| `index.html` (root or `www/`) + `js/rpg_core.js` or `js/rmmz_core.js` | rpgmaker-mv (MV/MZ) | rpgmaker |
| `Data/*.rxdata` / `*.rvdata*` + `Game.ini` | rpgmaker-xp | proton |
| `renpy/` + `game/` + Linux launcher (`.sh`/ELF) | renpy-native | native |
| `renpy/` + `game/` + `.exe`, no `.sh` | renpy-windows | proton |
| `*_Data/` + `GameAssembly.dll`/`MonoBleedingEdge` + `.exe` | unity-windows | proton |
| `*_Data/` + `GameAssembly.so` + ELF launcher | unity-linux | native |
| `*.pck` + exe / ELF | godot | proton / native |
| `resources/*.asar` + `.exe` | electron | proton (experimental) |
| `tyrano/` + `data/` + `index.html` | tyrano | rpgmaker (filter unlikely) |
| `*.AppImage` | appimage | native |
| lone `.exe` / ELF / nothing recognizable | exe / elf / unknown | proton / native / ask |

Helper executables (`UnityCrashHandler*`, `nwjc*`, `payload*`,
uninstallers, redist installers…) are never mistaken for the game.

## Game language (`--lang`, Proton/native)

Some titles (notably Japanese VNs) only run correctly under their native
locale. Setting it exports `LANG`, plus `HOST_LC_ALL` **only when the host
already has that locale generated**: since Proton 10, Proton starts Wine
with `LC_ALL=C`, which silently overrides a lone `LANG`, so the
pressure-vessel host hint is what carries the locale through.

Forcing `HOST_LC_ALL` to a locale the host has not generated is worse than
useless — some engines (the Emote/.NET one, e.g. `mlove`) exit in ~1s when
it is set, while `LANG` alone runs fine. So `ak_locale_env` checks
`locale -a` first and leaves `HOST_LC_ALL` unset (with a warning) when the
locale is absent. The container's `pv-locale-gen` still generates the
locale for the process, so text renders correctly either way; to silence the
warning and take the `HOST_LC_ALL` path, add the locale to
`/etc/locale.gen` and run `sudo locale-gen`. Set `MIGAKI_NO_HOST_LC_ALL=1`
to never export `HOST_LC_ALL` (escape hatch if a title still dies with it).

## Interface notes

* **A/B comparison** is structural: the unfiltered run launches with the
  layer environment entirely withheld, so the loader never sees vkBasalt.
  (Relying on the loader's disable flag is unreliable for explicitly-listed
  layers.) Proof: unfiltered logs contain zero vkBasalt lines.
* **Game icons** (GUI): extracted from Windows `.exe` files (`icoextract`),
  RPGMaker manifest art or shipped icon files, cached under
  `~/.cache/migaki/icons/`. Missing sources fall back to a generic icon.
  Cached icons survive unplugged drives; delete the cache dir to refresh.
* **File pickers**: the GUI prefers the desktop's native dialog (KDE
  `kdialog`, else `zenity`), falling back to Qt Quick Dialogs when neither is
  installed; the TUI uses `zenity`. The last-used folder is remembered. Paths
  can always be pasted into the wizard instead.
* **Logs** belong to the selected game: switching games clears the log view.
  Expected NW.js/Chromium chatter is hidden (the wrapper's "wayland not
  detected" note, signin/push-messaging and Ozone "Binding to … version"
  warnings) so real errors stand out; the full stream still reaches the
  terminal when a launcher is run by hand.
* **Mouse in fullscreen**: fixed-resolution titles may mis-map clicks under
  compositor fullscreen (they keep stale input geometry when scaled).
  Prefer the game's own fullscreen option; a Wine virtual desktop at
  output resolution is the fallback. Windowed play is always exact.
