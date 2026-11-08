# gamescope fork (CuNNy 4x16-NVL upscaler)

This directory is the provenance record for the gamescope binary Migaki builds
and installs into `~/.local/lib/migaki/gamescope`. Nothing here is vendored
code except the shader sources: `base.txt` pins an upstream commit, the patches
are applied on top of it, and `build.sh` produces the binary.

```
base.txt                                   pinned upstream (PR + commit + wlroots)
patches/0001-wlroots-libinput-keypad-slide.patch
patches/0002-cunny-4x16-nvl.patch
patches/0003-migaki-upscale-line.patch
shaders/CuNNy/                             copied into src/shaders/ before meson setup
build.sh                                   clone -> patch -> ninja -> install
```

`base.txt` is one line: `<repo> <pr-ref> <commit> wlroots=<commit>`. The
build fails, rather than compiling something else, when the upstream ref no
longer resolves to the pinned commit.

## Bumping the base

1. Build the new base and re-apply the changes by hand in a working checkout
   (the same 11 files plus the shaders).
2. Update `base.txt` with the new commit and wlroots commit.
3. Regenerate the patches from that checkout, keeping the split:
   - `0001` — `git -C subprojects/wlroots diff -- backend/libinput/switch.c`
   - `0002` — `git diff -- src/Backends/{DRM,OpenVR,Wayland}Backend.cpp
     src/main.cpp src/main.hpp src/meson.build src/rendervulkan.cpp
     src/rendervulkan.hpp src/shaders/descriptor_set.h
     src/shaders/descriptor_set_constants.h src/steamcompmgr.cpp`
     (the new shader files are **not** in this patch — `build.sh` copies
     `shaders/` into the tree, so a patch that also created them would fail)
   - `0003` — only the `MIGAKI_UPSCALE` line in `vulkan_composite`, so it
     survives a regeneration of `0002`.
4. `bash gamescope/build.sh --prefix /tmp/gsv --clean` and check the smoke
   test prints the filter.

## Adding another upscaler

Six places, in this order. Everything is keyed off a new
`GamescopeUpscaleFilter` value, so a second model never touches the first.

1. **`src/main.hpp`** — add the enum value to `GamescopeUpscaleFilter` (before
   `FROM_VIEW`, which is internal).
2. **`src/main.cpp`** — accept the name in the filter parser next to
   `"cunny"`, and add the `--help` line (`cunny => CuNNy 4x16-NVL 2x CNN
   upscaling`).
3. **`src/rendervulkan.hpp`** — the `SHADER_TYPE_*` entries for every pass,
   the pipeline accessor (`cunnyPipeline`) and its array
   (`m_cunnyPipelines[6]`), and the intermediate textures in the output struct
   (`cunnyA[4]`, `cunnyB[4]`, `cunnyOut`).
4. **`src/rendervulkan.cpp`** — the `SHADER(CUNNY_4X16_*, …)` entries in
   `createShaders`, a `create*Pipelines` function modelled on
   `createCunnyPipelines` plus its call from `CVulkanDevice::init` (before
   `compileAllPipelines` is spawned), and the
   `else if ( frameInfo->useCunny4x16Layer0 )` branch in
   `vulkan_composite`. The branch owns the dispatch chain and the final blit
   into `compositeImage`; a model that emits a different scale must compute
   `outX`/`outY` itself (CuNNy is fixed 2x).
5. **`src/steamcompmgr.cpp` + the three backends** — set
   `frameInfo.useCunny4x16Layer0` from `g_upscaleFilter` where the frame info
   is built, and add the field to `bNeedsFullComposite` in
   `DRMBackend.cpp`, `OpenVRBackend.cpp` and `WaylandBackend.cpp` (without
   that, the backend takes the plane path and the filter never runs).
6. **`src/meson.build`** — list the `.comp` files in `shader_src` so
   `glslang` compiles them into the build. The sources themselves live in
   `shaders/<model>/` here and are copied in by `build.sh`.

Keep the `MIGAKI_UPSCALE input=… cnn=… out=…` stderr line in the new branch
too: `scripts/gamescope-migaki.sh --gameid` parses it to record what the game
actually rendered at.

### Licensing

Each model keeps its own `licence.txt` and is never relicensed: CuNNy is
GPLv3 per its header (LGPLv3 per its README), and the shader sources stay under
that licence in `shaders/CuNNy/`. gamescope itself is BSD-2-Clause. The
combination is distributed **as source only** — `build.sh` compiles it on the
user's machine, so no GPL/BSD binary is redistributed here.
