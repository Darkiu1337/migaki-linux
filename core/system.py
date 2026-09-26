import glob
import json
import os
import re
import subprocess
import time

from . import paths

# GPU list cache lifetime. Hardware changes are caught by gpu_fingerprint();
# the TTL only covers driver/name changes without a hardware change.
GPU_CACHE_TTL = 24 * 3600


def list_presets():
    """Clear-preset names from shaders/presets.json, preferred order first.
    Auto-discovers future entries; never raises."""
    try:
        with open(paths.PRESETS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    order = ["Clear", "Clear_Vivid", "Clear_AA"]
    return [n for n in order if n in data] + [n for n in data if n not in order]


def preset_note(name):
    """The manifest `note` for a preset, or ""."""
    try:
        with open(paths.PRESETS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    entry = data.get(name) if isinstance(data, dict) else None
    if isinstance(entry, dict):
        return str(entry.get("note", ""))
    return ""


def variant_note(name):
    """Dropdown note: manifest note for presets, static note for Restore."""
    return preset_note(name) or paths.VARIANT_NOTES.get(name, "")


def list_variants():
    """Filter names for the GUI/TUI: Restore shaders (auto-discovered) first,
    then the Clear presets from the manifest."""
    found = []
    try:
        for fn in sorted(os.listdir(paths.SHADERS_DIR)):
            m = re.fullmatch(r"Anime4K_Restore_(.+)\.fx", fn)
            if m:
                found.append(m.group(1))
    except OSError:
        pass
    order = ["L", "M", "S", "Soft_S", "Soft_M", "Soft_L",
             "VL", "UL", "Soft_VL", "Soft_UL"]
    restore = [v for v in order if v in found] + [v for v in found if v not in order]
    return restore + [p for p in list_presets() if p not in restore]


def list_gpus():
    """Display names from the Vulkan loader; first entry is auto (= discrete
    GPU when detectable)."""
    names = ["auto (discrete GPU preferred)"]
    try:
        out = subprocess.run(["vulkaninfo", "--summary"], capture_output=True,
                             text=True, timeout=15).stdout
        seen = set()
        for line in out.splitlines():
            m = re.search(r"deviceName\s*=\s*(.+)", line)
            if m:
                name = m.group(1).strip()
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
    except (OSError, subprocess.SubprocessError):
        pass
    return names


def display_gpu_vendor():
    """Vendor of the GPU owning the X11/XWayland session ('nvidia'|'amd'|
    'intel'), or ''. That is the only GPU that can present a Vulkan swapchain
    for a windowed app here: Chromium/ANGLE-Vulkan needs X11 ozone, so a forced
    non-display GPU cannot filter (see scripts/rpgmaker-migaki.sh)."""
    try:
        out = subprocess.run(["glxinfo", "-B"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if line.startswith("OpenGL renderer string:"):
            r = line.split(":", 1)[1]
            if "NVIDIA" in r:
                return "nvidia"
            if any(k in r for k in ("AMD", "Radeon", "ATI")):
                return "amd"
            if "Intel" in r:
                return "intel"
            return ""
    return ""


def gpu_fingerprint():
    """Cheap hardware fingerprint (vendor:device per DRM card) used to
    invalidate the cached GPU list on a GPU add/remove/swap. Reads sysfs
    (~0.4ms, no driver init); falls back to `lspci -nn`."""
    ids = []
    for d in sorted(glob.glob("/sys/class/drm/card*/device")):
        try:
            with open(os.path.join(d, "vendor"), encoding="utf-8") as f:
                vendor = f.read().strip().lower()
            with open(os.path.join(d, "device"), encoding="utf-8") as f:
                device = f.read().strip().lower()
        except OSError:
            continue
        if vendor and device:
            ids.append(f"{vendor}:{device}")
    if ids:
        return "|".join(ids)
    try:
        out = subprocess.run(["lspci", "-nn"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in out.splitlines():
        if not re.search(r"VGA compatible controller|3D controller|"
                         r"Display controller", line):
            continue
        m = re.search(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]", line)
        if m:
            ids.append(f"0x{m.group(1).lower()}:0x{m.group(2).lower()}")
    return "|".join(ids)


def gpu_cache(ttl=GPU_CACHE_TTL, fingerprint=None):
    """(gpus, stale) from ~/.cache/migaki/gpus.json. gpus is None when the
    cache is missing/malformed; stale also when the fingerprint differs or
    the entry is older than ttl. Never calls vulkaninfo."""
    try:
        with open(paths.GPU_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        gpus = data.get("gpus")
        ts = float(data.get("ts", 0))
        fp = data.get("fingerprint", "")
    except (OSError, ValueError, TypeError):
        return None, True
    if not isinstance(gpus, list) or len(gpus) < 2:
        return None, True
    stale = (time.time() - ts) > ttl
    if fingerprint is not None and fp != fingerprint:
        stale = True
    return gpus, stale


def save_gpu_cache(gpus, fingerprint=""):
    """Persist a real GPU list (>1 entry) for the next launch. Never raises."""
    if not isinstance(gpus, list) or len(gpus) < 2:
        return
    try:
        os.makedirs(os.path.dirname(paths.GPU_CACHE), exist_ok=True)
        tmp = paths.GPU_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "fingerprint": fingerprint,
                       "gpus": gpus}, f, indent=2)
        os.replace(tmp, paths.GPU_CACHE)
    except OSError:
        pass


# Proton discovery for the launcher's Proton dropdown. Only Proton-type
# builds (an executable `proton`/`proton.sh`) are listed; Wine runners are not
# selectable through the umu/PROTONPATH runner. `value` is an absolute path
# (umu accepts a path), "" means the umu-managed UMU-Proton.
PROTON_DIRS = (
    "~/.local/share/Steam/compatibilitytools.d",
    "~/.steam/steam/compatibilitytools.d",
    "~/.steam/root/compatibilitytools.d",
    "~/.var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d",
    "/usr/share/steam/compatibilitytools.d",
    "/usr/local/share/steam/compatibilitytools.d",
)


def proton_wow64_capable(path):
    """True when a Proton dir can run 32-bit PE through new WoW64.
    A dir without a readable `proton` script is assumed capable (latest)."""
    if not os.path.isdir(path):
        return False
    if os.path.isfile(os.path.join(path, "files", "bin-wow64", "wine")):
        return True
    try:
        with open(os.path.join(path, "proton"), encoding="utf-8",
                  errors="replace") as f:
            if "PROTON_USE_WOW64" in f.read():
                return True
    except OSError:
        pass
    # A 64-bit-only build (no wine64 loader) always runs new WoW64.
    return (os.path.isfile(os.path.join(path, "files", "bin", "wine"))
            and not os.path.exists(os.path.join(path, "files", "bin", "wine64")))


def list_protons():
    """Pinned umu-managed entry first, then one per detected Proton build.
    Each entry: {label, value, wow64}."""
    out = [{"label": "umu-managed (UMU-Proton — always works)",
            "value": "", "wow64": True}]
    seen = set()
    found = []
    for root in PROTON_DIRS:
        root = os.path.expanduser(root)
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            d = os.path.join(root, name)
            real = os.path.realpath(d)
            if real in seen or not os.path.isdir(d):
                continue
            proton = os.path.join(d, "proton")
            if not (os.path.isfile(proton) and os.access(proton, os.X_OK)):
                continue
            seen.add(real)
            found.append({"label": name, "value": d,
                          "wow64": proton_wow64_capable(d)})
    found.sort(key=lambda e: e["label"].lower())
    out.extend(found)
    return out


def detect(path):
    """Engine detection; wraps the proven bash implementation in
    scripts/migaki-lib.sh. Returns (engine, runner, confidence, root, detail)
    or None when detection fails to run/returns garbage."""
    lib = os.path.join(paths.SCRIPTS_DIR, "migaki-lib.sh")
    try:
        out = subprocess.run(
            ["bash", "-c", f'source "{lib}" && ak_detect_engine "$0"', path],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.split("|", 4)
    if len(parts) != 5:
        return None
    return tuple(parts)


def translate_engine(path):
    """Hook transport for a game's translation session:
      "tyrano"      — Electron/Chromium bundle, CDP DOM hook (Proton path)
      "rpgmaker"    — RPGMaker MV/MZ (NW.js normal build: no CDP), injected
                      page hook via the rpgmaker runner
      "textractor"  — everything else (GDI/engine hooks)
    """
    det = detect(path) if path else None
    if det:
        if det[0] in ("electron", "tyrano"):
            return "tyrano"
        if det[0] == "rpgmaker-mv":
            return "rpgmaker"
    return "textractor"
