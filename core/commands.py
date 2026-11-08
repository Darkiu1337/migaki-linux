import os
import sys

from . import paths, system


def gpu_vendor(name):
    """Vendor token (nvidia|amd|intel) for a GPU display name, or '' when
    unknown (e.g. the 'auto …' entry)."""
    if "NVIDIA" in name:
        return "nvidia"
    if any(k in name for k in ("AMD", "ATI", "Radeon")):
        return "amd"
    if "Intel" in name:
        return "intel"
    return ""


def gpu_icd(name):
    if name.startswith("auto"):
        return "auto"
    if "NVIDIA" in name:
        return "nvidia"
    if any(k in name for k in ("AMD", "ATI", "Radeon")):
        return "amd"
    return "auto"


def _vn_engine(engine):
    """vn-launch.sh understands auto|textractor|tyrano. RPGMaker MV/MZ under
    Proton isn't hookable by vn-launch (NW.js has no CDP and no autoload there),
    so it falls back to Textractor; use the rpgmaker runner for MV/MZ."""
    return "tyrano" if engine == "tyrano" else "textractor"


def build_command(game, gid=None):
    """Argv for a library entry (mirrors the bash runners).

    An entry with `"upscale": "1"` is wrapped in scripts/gamescope-migaki.sh
    (the CuNNy 2x filter) when the packaged gamescope is installed; `gid` is
    what lets the wrapper record the run in the per-game upscale profile. The
    filter name is literal: cunny is the only model validated end to end, and
    the profile's `filter` field is display-only.

    `"shaders": "0"` is not an argv concern: callers pass the result to
    `disable_filter_env` so the runner scrubs vkBasalt (see `filter_disabled`).
    """
    runner = game["runner"]
    variant = game.get("variant", "L")
    fps = game.get("fps", "60")
    hud = game.get("hud", "0")
    gpu = game.get("gpu", "auto (discrete GPU preferred)")
    pmode = game.get("prefix_mode", "shared")
    lang = game.get("lang", "")
    wow64 = game.get("wow64", "")
    path = game["path"]
    if runner == "proton":
        argv = [os.path.join(paths.SCRIPTS_DIR, "proton-migaki.sh"),
                "--variant", variant, "--fps", fps,
                "--prefix-mode", pmode if pmode in ("shared", "game") else "shared"]
        if wow64 == "0":
            argv.append("--no-wow64")
        elif wow64 == "1":
            argv.append("--wow64")
        if hud == "1":
            argv.append("--hud")
        if lang:
            argv += ["--lang", lang]
        if not gpu.startswith("auto"):
            argv += ["--dxvk-device", gpu]
        argv.append(path)
    elif runner == "rpgmaker":
        argv = [os.path.join(paths.SCRIPTS_DIR, "rpgmaker-migaki.sh"),
                "--variant", variant, "--gpu", gpu_icd(gpu),
                "--fps", fps]
        if hud == "1":
            argv.append("--hud")
        argv += ["--gamepath", path]
    else:
        argv = [os.path.join(paths.SCRIPTS_DIR, "native-migaki.sh"),
                "--variant", variant, "--gpu", gpu_icd(gpu),
                "--fps", fps]
        if hud == "1":
            argv.append("--hud")
        if lang:
            argv += ["--lang", lang]
        argv.append(path)
    return _with_upscale(game, gid, argv)


def _with_upscale(game, gid, argv):
    """Wrap a runner argv in the gamescope CuNNy launcher when the game asks
    for it. A missing build is not fatal: the game still launches, unfiltered,
    with a warning (the TUI/doctor report the missing install separately)."""
    if game.get("upscale") != "1":
        return argv
    if gid and os.access(paths.GAMESCOPE_BIN, os.X_OK):
        return [os.path.join(paths.SCRIPTS_DIR, "gamescope-migaki.sh"),
                "--filter", "cunny", "--gameid", gid, "--"] + argv
    print("warning: upscale requested but gamescope is not installed — "
          "launching without the filter", file=sys.stderr)
    return argv


def filter_disabled(game):
    """True when the entry asked for no shader chain (`"shaders": "0"`)."""
    return game.get("shaders") == "0"


def disable_filter_env(env):
    """Scrub every inherited vkBasalt variable and set the structural off
    switch (`DISABLE_VKBASALT=1`), which the runners honour by exporting no
    layer at all."""
    for var in paths.VKBASALT_ENV_VARS:
        env.pop(var, None)
    env["DISABLE_VKBASALT"] = "1"
    return env


def build_unfiltered_command(game, gid=None):
    """The A/B baseline: same runner and variant, but no MangoHud overlay and
    no gamescope/CuNNy wrapper. The caller still has to disable the shader
    chain itself (`disable_filter_env`)."""
    baseline = dict(game)
    baseline["hud"] = "0"
    baseline["upscale"] = "0"
    return build_command(baseline, gid)


def build_translate_command(game, gid, setup=False):
    """Argv for a translation session (filter + text hook in one launch).
    Electron/TyranoScript titles use the CDP hook under Proton; RPGMaker MV/MZ
    use the rpgmaker runner's injected page hook (its NW.js has no CDP).
    setup=True opens the in-app Text Hooker picker (Textractor only);
    translate.show_hooker="1" reveals Textractor's window (debug)."""
    tr = game.get("translate") or {}
    if game.get("runner") == "rpgmaker":
        argv = [os.path.join(paths.SCRIPTS_DIR, "rpgmaker-migaki.sh"),
                "--variant", game.get("variant", "L"),
                "--gpu", gpu_icd(game.get("gpu", "auto (discrete GPU preferred)")),
                "--fps", game.get("fps", "60"),
                "--translate", "--gameid", gid,
                "--gamepath", game["path"]]
        if game.get("hud") == "1":
            argv.append("--hud")
        return argv
    argv = [os.path.join(paths.TRANSLATE_DIR, "vn-launch.sh"),
            "--exe", game["path"], "--gameid", gid,
            "--filter", game.get("variant", "L"),
            "--engine", _vn_engine(system.translate_engine(game.get("path", "")))]
    if game.get("wow64") == "0":
        argv.append("--no-wow64")
    elif game.get("wow64") == "1":
        argv.append("--wow64")
    if game.get("lang"):
        argv += ["--lang", game["lang"]]
    hook = (tr.get("hook_code") or "").strip()
    if hook:
        argv += ["--hook-code", hook]
    if setup:
        argv += ["--setup"]
        if tr.get("show_hooker") == "1":
            argv += ["--show-hooker"]
    return argv
