# gamescope fork (CuNNy 2x upscale filters)

This directory is the provenance record for the gamescope binary Migaki builds
and installs into `~/.local/lib/migaki/gamescope`. Nothing here is vendored
code except the shader sources: `base.txt` pins an upstream commit, the patches
are applied on top of it, and `build.sh` produces the binary.

```
base.txt                                   pinned upstream (PR + commit + wlroots)
patches/0001-wlroots-libinput-keypad-slide.patch
patches/0002-cunny-nvl-models.patch
shaders/CuNNy/                             copied into src/shaders/ before meson setup
  gen_cunny.py                             Magpie HLSL -> GLSL compute shaders
  4x12-NVL/ 4x16-NVL/ 4x24-NVL/ 8x32-NVL/  one directory per network
build.sh                                   clone -> patch -> ninja -> install
```

`base.txt` is one line: `<repo> <pr-ref> <commit> wlroots=<commit>`. The
build fails, rather than compiling something else, when the upstream ref no
longer resolves to the pinned commit.

The fork adds one thing: `-F cunny` (CuNNy 4x16-NVL, the default),
`-F cunny-4x12`, `-F cunny-4x24` and `-F cunny-8x32` run the vendored CuNNy
networks on the game window's buffer, and gamescope's own `linear`, `nearest`,
`fsr`, `nis`, `pixel` and `anime4k` keep working unchanged. Every model is one
input pass, N conv passes and one out-shuffle pass over `channels / 4` RGBA
targets; the patches carry the C++ side, `shaders/` the GLSL.

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
     `shaders/` into the tree, so a patch that also created them would fail;
     `MIGAKI_UPSCALE` is part of this patch because it lives inside the CuNNy
     dispatch branch).
4. `bash gamescope/build.sh --prefix /tmp/gsv --clean` and check the smoke
   test prints the filters.

## Adding another CuNNy model

The host side is table-driven, so a model is data plus shaders:

1. **Shaders.** Drop `CuNNy-<model>.hlsl` next to the other models and run
   `python3 gamescope/shaders/CuNNy/gen_cunny.py --model <model> --out
   gamescope/shaders/CuNNy/<model> <path>`. The generator derives the channel
   count (targets) from the HLSL and names each output after the model, because
   gamescope's build derives one header name per shader source.
2. **`src/main.hpp`** — one `CUNNY_<MODEL>_NVL` enum value, inside the
   contiguous CuNNy block (`is_cunny_upscale_filter`/`cunny_upscale_model`
   depend on that order).
3. **`src/main.cpp`** — the `-F` name in the parser and the `--help` line.
4. **`src/rendervulkan.hpp`** — `SHADER_TYPE_CUNNY_<MODEL>_{IN,CONVn,OUT}`
   entries; bump `k_nCunnyModels` (and `k_nCunnyTargets` only if the model needs
   more than 32 channels).
5. **`src/rendervulkan.cpp`** — the `kCunnyModels[k_nCunnyModels]` row (pass
   order, pass count, targets), the `SHADER(CUNNY_<MODEL>_*, …)` entries in
   `createShaders`/`compileAllPipelines`, and the `#include`s.
6. **`src/meson.build`** — list the `.comp` files in `shader_src`.

Keep the `MIGAKI_UPSCALE input=… cnn=… out=…` stderr line in the dispatch
branch: `scripts/gamescope-migaki.sh --gameid` parses it to record what the game
actually rendered at.

### Licensing

Each model keeps its own `licence.txt` and is never relicensed: CuNNy is
GPLv3 per its header (LGPLv3 per its README), and the shader sources stay under
that licence in `shaders/CuNNy/`. gamescope itself is BSD-2-Clause. The
combination is distributed **as source only** — `build.sh` compiles it on the
user's machine, so no GPL/BSD binary is redistributed here.
