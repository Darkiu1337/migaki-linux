"""Per-game upscale profile: the resolution a game really renders at, and what
the CuNNy 2x filter does with it.

Discovery has two layers, cheapest first:

  A. runtime (universal) — gamescope prints
     `MIGAKI_UPSCALE input=… cnn=… out=…` when the filter engages, and
     scripts/gamescope-migaki.sh feeds that line to `upscale-note`. This is the
     only source that sees what the game actually did.
  B. engine config (only where a readable source exists) — today RPGMaker
     MV/MZ, via SRD_GameUpgrade's "Game Resolution" or package.json's window.
     Ren'Py (.rpa), Unity (boot.config), KiriKiri (.xp3) and Godot (.pck) keep
     their settings inside packed assets, so they return "" and the decision
     rests on layer A alone.

The verdict compares the network's output (input x2, fixed by the model) with
the presented resolution: `exact` when 2x lands on it, `downscale` when the
network over-shoots, `upscale` when the compositor stretches after the network,
and `disabled` when the filter never engaged because the game already fills the
output on its own.
"""
import datetime
import json
import os
import subprocess

from . import paths, store, system

VERDICTS = ("exact", "downscale", "upscale", "disabled")
DEFAULT_MONITOR = (1920, 1080)

# What the packaged gamescope can run (`-F`). The CuNNy entries are the four
# Magpie networks vendored in gamescope/shaders/CuNNy; `cunny` is the 4x16-NVL
# model and stays the default so existing profiles keep their meaning.
FILTERS = (
    ("cunny", "CuNNy 4x16-NVL"),
    ("cunny-4x12", "CuNNy 4x12-NVL"),
    ("cunny-4x24", "CuNNy 4x24-NVL"),
    ("cunny-8x32", "CuNNy 8x32-NVL"),
    ("anime4k", "Anime4K 2x CNN UL"),
    ("fsr", "FSR 1"),
    ("nis", "NIS"),
    ("linear", "Bilinear"),
    ("nearest", "Nearest"),
    ("pixel", "Pixel"),
)
FILTER_NAMES = tuple(name for name, _ in FILTERS)
FILTER_LABELS = dict(FILTERS)
DEFAULT_FILTER = "cunny"


def normalize_filter(name):
    """A selectable upscaler name, falling back to the default."""
    candidate = (name or "").strip().lower()
    return candidate if candidate in FILTER_LABELS else DEFAULT_FILTER


def filter_for(gid):
    """The upscaler stored for a game (`cunny` when none was chosen)."""
    return normalize_filter((load().get(gid) or {}).get("filter"))


def load():
    """The stored profiles, keyed by game id."""
    return store.load_json(paths.UPSCALE_JSON, {"games": {}}).get("games", {})


def save(games):
    store.save_json(paths.UPSCALE_JSON, {"games": games})


def _size(text):
    """"WxH" -> (w, h); None when it is not a positive size."""
    try:
        w, h = str(text or "").strip().lower().split("x", 1)
        w, h = int(w), int(h)
    except (ValueError, AttributeError):
        return None
    return (w, h) if w > 0 and h > 0 else None


def monitor_size():
    """Focused monitor's (width, height) from Hyprland, else the first
    monitor, else (1920, 1080) when Hyprland cannot be asked (no compositor,
    bad JSON, no monitors)."""
    try:
        raw = subprocess.run(["hyprctl", "monitors", "-j"],
                             capture_output=True, text=True,
                             timeout=10).stdout
        monitors = json.loads(raw)
    except (OSError, ValueError, subprocess.SubprocessError):
        return DEFAULT_MONITOR
    if not isinstance(monitors, list):
        return DEFAULT_MONITOR
    for focused_only in (True, False):
        for mon in monitors:
            if not isinstance(mon, dict):
                continue
            if focused_only and not mon.get("focused"):
                continue
            size = _size("%sx%s" % (mon.get("width"), mon.get("height")))
            if size:
                return size
    return DEFAULT_MONITOR


def default_render(mon=None):
    """Half the monitor: the largest base whose 2x output still fits."""
    w, h = mon or monitor_size()
    return "%dx%d" % (w // 2, h // 2)


def engine_base(path, runner):
    """Resolution the engine itself is configured for ("960x720"), or "" when
    there is no readable source (see the module docstring)."""
    if runner != "rpgmaker" or not path:
        return ""
    root = path if os.path.isdir(path) else os.path.dirname(path)
    # MV ships the runtime under www/; MZ keeps js/ at the root.
    for sub in ("www", ""):
        found = _plugins_resolution(os.path.join(root, sub, "js", "plugins.js"))
        if found:
            return found
    return _package_resolution(os.path.join(root, "package.json"))


def _plugins_resolution(file):
    """SRD_GameUpgrade's "Game Resolution" parameter from a plugins.js.

    The file is a JS assignment (`var $plugins = [ … ];`), so the array is
    sliced out before parsing. The parameter itself is stored as a JSON string
    inside the JSON, hence the second decode."""
    try:
        with open(file, encoding="utf-8-sig", errors="replace") as f:
            raw = f.read()
        plugins = json.loads(raw[raw.index("["):].rstrip().rstrip(";"))
    except (OSError, ValueError):
        return ""
    if not isinstance(plugins, list):
        return ""
    for plugin in plugins:
        if not isinstance(plugin, dict) or plugin.get("name") != "SRD_GameUpgrade":
            continue
        params = plugin.get("parameters") or {}
        # "Game Resolution" is the window the game renders at; "Screen
        # Resolution" is the optional upscale target (usually left empty).
        for key in ("Game Resolution", "Screen Resolution"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                try:
                    value = json.loads(value)
                except ValueError:
                    continue
            if not isinstance(value, dict):
                continue
            size = _size("%sx%s" % (value.get("Width"), value.get("Height")))
            if size:
                return "%dx%d" % size
    return ""


def _package_resolution(file):
    """NW.js window size from package.json (MV/MZ default when no plugin
    overrides it)."""
    try:
        with open(file, encoding="utf-8-sig", errors="replace") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ""
    window = data.get("window") if isinstance(data, dict) else None
    if not isinstance(window, dict):
        return ""
    size = _size("%sx%s" % (window.get("width"), window.get("height")))
    return "%dx%d" % size if size else ""


def profile(gid):
    """The full profile for a game, with `render`/`output` resolved.

    Resolution order for `render`: the saved profile, then the engine's own
    base, then half the monitor. Nothing is persisted here — `upscale-render`
    writes the pair it resolves, and `note` writes what a run observed.
    `engine_base` rides along so `recommend` can flag a render below the
    engine's own resolution without re-reading the game."""
    game = store.load_games().get(gid) or {}
    saved = load().get(gid) or {}
    path = game.get("path", "")
    runner = game.get("runner", "")
    mon = monitor_size()
    detected = system.detect(path) if path else None
    base = engine_base(path, runner)
    return {
        "render": saved.get("render") or base or default_render(mon),
        # `render` alone is what a run resolved, so only a real user choice
        # carries the marker the wizard's checkbox reads.
        "render_set": bool(saved.get("render_set")),
        "output": saved.get("output") or "%dx%d" % mon,
        "filter": normalize_filter(saved.get("filter")),
        "observed_input": saved.get("observed_input", ""),
        "observed_cnn": saved.get("observed_cnn", ""),
        "verdict": saved.get("verdict", "disabled"),
        "scale": saved.get("scale", 0.0),
        "engine": (detected[0] if detected else "") or saved.get("engine", ""),
        "engine_base": base,
        "updated": saved.get("updated", ""),
    }


def recommend(prof, mon=None):
    """{"verdict", "scale", "cnn", "note"} for a profile.

    `scale` is the factor the compositor applies to the network's output
    (gamescope blits with one uniform scale, `max` of the per-axis ratios in
    `vulkan_composite`): >1 means the network produces more pixels than are
    shown, <1 means the image is stretched after it. It equals
    cnn_width / monitor_width when the aspects match."""
    mon = mon or monitor_size()
    mon_size = "%dx%d" % mon
    inp = _size(prof.get("observed_input"))
    cnn = _size(prof.get("observed_cnn"))
    render = prof.get("render") or default_render(mon)
    if inp and cnn:
        ratios = (cnn[0] / mon[0], cnn[1] / mon[1])
        if cnn == mon:
            verdict, scale = "exact", 1.0
            note = "2x lands exactly on %s" % mon_size
        elif max(ratios) > 1:
            # Over-produced: the blit shrinks the largest axis to fit.
            verdict, scale = "downscale", max(ratios)
            note = ("reducing %.2fx: set the game to %s for an exact 2x"
                    % (scale, default_render(mon)))
        else:
            # Under-produced: the blit stretches to fill, so the smaller ratio
            # is the uniform factor.
            verdict, scale = "upscale", min(ratios)
            note = "stretching %.2fx after the network" % (1 / scale)
    else:
        verdict, scale = "disabled", 0.0
        if inp or not prof.get("updated"):
            note = "not measured yet: launch once with the filter on"
        else:
            note = ("the filter never engaged: the game already fills %s on its "
                    "own; lower the in-game resolution to %s to use it"
                    % (mon_size, default_render(mon)))

    hints = []
    # gamescope's virtual output exposes a single mode equal to -w/-h, so a
    # fullscreen game can only change its base by restarting with another
    # --render; a windowed game (input below the mode) is free to pick any
    # size below it.
    if prof.get("observed_input") == render:
        hints.append("base pinned to the virtual mode %s: change --render to "
                     "move it" % render)
    # A render below what the engine is configured for cannot be reached by the
    # game at all, which matters more than anything above.
    base = _size(prof.get("engine_base"))
    render_size = _size(render)
    if base and render_size and (render_size[0] < base[0] or render_size[1] < base[1]):
        hints.append("render %s is below the engine's own resolution (%dx%d): "
                     "the game cannot use it" % (render, base[0], base[1]))
    if hints:
        note = "%s — %s" % (note, "; ".join(hints))
    return {"verdict": verdict, "scale": round(scale, 4),
            "cnn": prof.get("observed_cnn", ""), "note": note}


def note(gid, input=None, cnn=None, out=None, render=None, output=None):
    """Record what a run observed and return the new recommendation.

    `input`/`cnn`/`out` come from the fork's MIGAKI_UPSCALE line. A call with
    no argument at all records a run where the filter never engaged, which
    drops the previous observation so the profile reads `disabled` instead of
    reporting a stale size. `render`/`output` alone are manual overrides: they
    change the profile without claiming (or erasing) an observation."""
    prof = profile(gid)
    entry = load().get(gid) or {}
    if input is None and cnn is None and out is None \
            and render is None and output is None:
        # Blank, not pop: the merge below must override the previous values
        # that `prof` still carries, and the profile keeps a stable shape.
        entry["observed_input"] = ""
        entry["observed_cnn"] = ""
    if input is not None:
        entry["observed_input"] = input
    if cnn is not None:
        entry["observed_cnn"] = cnn
    if out is not None:
        entry["output"] = out
    if render is not None:
        entry["render"] = render
    if output is not None:
        entry["output"] = output
    entry["filter"] = prof["filter"]
    entry["engine"] = prof["engine"]
    entry.setdefault("render", prof["render"])
    entry.setdefault("output", prof["output"])
    entry["updated"] = _now()
    rec = recommend(dict(prof, **entry))
    entry["verdict"] = rec["verdict"]
    entry["scale"] = rec["scale"]
    games = load()
    games[gid] = entry
    save(games)
    return rec


def set_filter(gid, value):
    """Persist the upscaler for a game. The default needs no marker, so
    choosing it drops the field instead of pinning it; a game with no other
    recorded state leaves `upscale.json`."""
    name = normalize_filter(value)
    games = load()
    entry = dict(games.get(gid) or {})
    # Not an observation: `updated` stays whatever a run recorded.
    if name == DEFAULT_FILTER:
        entry.pop("filter", None)
    else:
        entry["filter"] = name
    if entry:
        games[gid] = entry
    else:
        games.pop(gid, None)
    save(games)
    return entry


def set_render(gid, value):
    """Persist (`"WxH"`) or clear (`""`) the explicit render override.

    Clearing drops only the override: `profile` falls back to the engine's own
    base, then half the monitor. A game with no other recorded state leaves
    `upscale.json` instead of keeping an empty record."""
    games = load()
    entry = dict(games.get(gid) or {})
    if value:
        entry["render"] = value
        entry["render_set"] = True
        entry["updated"] = _now()
    else:
        entry.pop("render", None)
        entry.pop("render_set", None)
    if entry:
        games[gid] = entry
    else:
        games.pop(gid, None)
    save(games)
    return entry


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
