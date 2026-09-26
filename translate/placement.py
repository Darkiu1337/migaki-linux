#!/usr/bin/env python3
"""placement.py — compositor-agnostic window placement + top control.

Two responsibilities:

* **placement** — float / pre-position the textbox before it maps.
  Wayland clients cannot set their own position, tiling compositors ignore the
  float hint, and a window that maps tiled loses its geometry before the app can
  float it. So the right behaviour is requested BEFORE the window maps:

  - Hyprland / sway: install a float rule (and size/position) so the first map is
    already floating at the saved spot.
  - Any other compositor (KDE, GNOME, X11, ...): windows already float, so Qt's
    saveGeometry/restoreGeometry handles size (and position on X11). No-op here.

* **top** — enforce "keep above" using **stacking-order only** mechanisms.

  SAFETY INVARIANT (see docs/translate.md "Compositor control safety"):
  top enforcement must NEVER focus/activate a window, move/resize it, or touch
  the pointer. Under KDE's "Focus follows mouse", activating a window makes the
  compositor warp the cursor onto it — a `keepAbove` property change does not.
  Backends here only ever change the stacking order.

All methods are best-effort and never raise: a failure silently falls back to
the pre-existing behaviour.
"""
import json
import os
import shutil
import subprocess
import time

TITLE = "vn-translate"
CACHE_DIR = os.path.expanduser("~/.cache/migaki")
KWIN_SCRIPT = os.path.join(CACHE_DIR, "kwin-vn-textbox.js")
KWIN_PLUGIN = "migaki-vn-textbox"
GNOME_STATE = os.path.join(CACHE_DIR, "gnome-vn-textbox-top")
GNOME_EXT_UUID = "vn-textbox-top@migaki"


def _run(argv, timeout=5):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _log(msg):
    """Diagnostic trail (stacking-only compositor commands + Top state)."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(os.path.join(CACHE_DIR, "textbox.log"), "a",
                  encoding="utf-8") as f:
            f.write(f"[placement] {msg}\n")
    except OSError:
        pass


def _wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")
                or os.environ.get("XDG_SESSION_TYPE") == "wayland")


def _desktop():
    # XDG_CURRENT_DESKTOP wins; XDG_SESSION_DESKTOP is only a fallback (they
    # can disagree, e.g. a nested session, and concatenating would misdetect).
    return (os.environ.get("XDG_CURRENT_DESKTOP")
            or os.environ.get("XDG_SESSION_DESKTOP") or "").upper()


class Placement:
    """Generic fallback: compositors that float normal windows need nothing.

    On X11 the textbox sets Qt's WindowStaysOnTopHint itself (this backend does
    not enforce anything). On an unknown Wayland compositor there is no client
    API, so top is reported unsupported instead of being faked with focus."""
    name = "generic"
    supports_top = True     # X11: Qt hint (textbox handles it)
    enforces_top = False    # this backend changes stacking itself
    top_note = "window hint"

    def pre_map(self, saved):
        pass

    def after_map(self, saved):
        pass

    def capture(self, pid):
        return None

    def set_top(self, keep):
        pass

    def raise_window(self, keep=True):
        pass

    def tick(self, keep):
        pass

    def reset_home(self):
        pass

    def shutdown(self):
        pass


class WaylandGenericPlacement(Placement):
    """Unknown Wayland compositor: no always-on-top client API exists."""
    name = "wayland-generic"
    supports_top = False
    enforces_top = False
    top_note = "unsupported on this compositor"


class X11Placement(Placement):
    """X11 (XFCE, KDE X11, GNOME X11, i3, ...): Qt's hint restacks, no focus."""
    name = "x11"
    supports_top = True
    enforces_top = False
    top_note = "Qt keep-above hint"


class HyprlandPlacement(Placement):
    name = "hyprland"
    supports_top = True
    enforces_top = True
    top_note = "Hyprland pin layer"

    # Only ever stacking/float verbs; focus/activate/move/pointer verbs are
    # dropped by _dispatch (the cursor-warp regression guard).
    ALLOWED_DISPATCH = (
        "hl.dsp.window.pin",
        "hl.dsp.window.float",
        "hl.dsp.window.bring_to_top",
    )
    FORBIDDEN = ("focus", "cursor", "movewindow", "moveactive", "movecursor",
                 "resizewindow", "resizeactive", "geometry")

    def __init__(self):
        self.home_ws = None
        self.last_ws = None
        self.last_pinned = None
        self._last_raise = 0.0

    @staticmethod
    def _want_pinned(active_ws, home_ws, keepontop):
        return bool(keepontop and home_ws is not None and active_ws == home_ws)

    def _json(self, *args):
        r = _run(["hyprctl", *args, "-j"])
        try:
            return json.loads(r.stdout) if r else None
        except (ValueError, AttributeError):
            return None

    def _eval(self, lua):
        _run(["hyprctl", "eval", lua])

    def _to_local(self, x, y):
        monitors = self._json("monitors")
        if not isinstance(monitors, list):
            return None
        for m in monitors:
            try:
                mx, my = int(m["x"]), int(m["y"])
                mw, mh = int(m["width"]), int(m["height"])
            except (KeyError, TypeError, ValueError):
                continue
            if mx <= x < mx + mw and my <= y < my + mh:
                return x - mx, y - my
        return None

    def pre_map(self, saved):
        move = ""
        if saved and saved.get("x") is not None and saved.get("y") is not None:
            try:
                local = self._to_local(int(saved["x"]), int(saved["y"]))
            except (TypeError, ValueError):
                local = None
            if local:
                move = f', move="{local[0]} {local[1]}"'
        # Re-declaring the same name updates the rule; disabling the previous
        # handle guards against duplicates if that is not the case.
        lua = ("pcall(function() if _G.__a4k_tb_rule then "
               "_G.__a4k_tb_rule:set_enabled(false) end end) "
               "_G.__a4k_tb_rule = hl.window_rule({ "
               f'name="migaki-vn-textbox", match={{ title="^{TITLE}$" }}, '
               f"float=true, persistent_size=true, no_blur=true{move} }})")
        self._eval(lua)

    def capture(self, pid):
        clients = self._json("clients")
        if not isinstance(clients, list):
            return None
        for c in clients:
            try:
                if int(c.get("pid", -1)) != pid:
                    continue
                at, size = c.get("at"), c.get("size")
                return {"x": int(at[0]), "y": int(at[1]),
                        "w": int(size[0]), "h": int(size[1])}
            except (TypeError, ValueError, IndexError):
                continue
        return None

    # ---- top (stacking only) ----
    def _ready(self):
        return (shutil.which("hyprctl")
                and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))

    def _dispatch(self, cmd):
        if not self._ready():
            return
        low = cmd.lower()
        if not any(low.startswith(a) for a in self.ALLOWED_DISPATCH):
            return
        if any(tok in low for tok in self.FORBIDDEN):
            _log(f"dropped forbidden dispatch: {cmd}")
            return
        _log(f"hyprctl dispatch {cmd}")
        _run(["hyprctl", "dispatch", cmd], timeout=5)

    def _float(self):
        self._dispatch('hl.dsp.window.float({window="title:^' + TITLE
                       + '$", action="enable"})')

    def _unpin(self):
        self._dispatch('hl.dsp.window.pin({window="title:^' + TITLE
                       + '$", action="disable"})')

    def _pin(self, action):
        self._dispatch('hl.dsp.window.pin({window="title:^' + TITLE
                       + '$", action="' + action + '"})')

    def _bring_to_top(self):
        self._dispatch('hl.dsp.window.bring_to_top({window="title:^' + TITLE
                       + '$"})')

    def set_top(self, keep):
        # Float + raise + unpin (see docs/translate.md). Never focuses/moves.
        self._float()
        self._unpin()
        if keep:
            self._bring_to_top()

    def raise_window(self, keep=True):
        if not keep:
            return
        now = time.time()
        if now - self._last_raise < 2.0:
            return
        self._last_raise = now
        self._bring_to_top()

    def reset_home(self):
        self.home_ws = None

    def tick(self, keep):
        # Workspace-aware Top enforcement, ~1s (see docs/translate.md).
        try:
            clients = self._json("clients")
            active = self._json("activeworkspace")
            if not isinstance(clients, list) or not isinstance(active, dict):
                return
            me = None
            for c in clients:
                try:
                    if int(c.get("pid", -1)) == os.getpid():
                        me = c
                        break
                except (TypeError, ValueError):
                    continue
            if me is None:
                return
            ws = (me.get("workspace") or {}).get("id")
            if ws is None:
                return
            # Edge-triggered home adoption only (see docs/translate.md).
            if ws != self.home_ws:
                if self.home_ws is None or (not self.last_pinned
                                            and ws != self.last_ws):
                    self.home_ws = ws
            self.last_ws = ws
            self.last_pinned = bool(me.get("pinned", False))
            # Floating is a hard requirement (overlay), Top or not; re-apply
            # if something tiled it (e.g. a manual toggle).
            if not me.get("floating", False):
                self._float()
                return
            want = self._want_pinned(active.get("id"), self.home_ws, keep)
            if bool(me.get("pinned", False)) != want:
                self._pin("enable" if want else "disable")
                if want:
                    self._bring_to_top()
                # Never move the window (see docs/translate.md).
        except Exception:
            pass


class SwayPlacement(Placement):
    name = "sway"
    supports_top = False   # sway exposes no always-on-top for xdg windows
    enforces_top = False
    top_note = "float only (sway has no keep-above)"

    def _msg(self, *args, cmd=None):
        argv = ["swaymsg"] + list(args)
        if cmd is not None:
            argv.append(cmd)
        return _run(argv)

    def pre_map(self, saved):
        self._msg(cmd=f'for_window [title="^{TITLE}$"] floating enable')

    def after_map(self, saved):
        if not saved:
            return
        parts = []
        if saved.get("w") and saved.get("h"):
            parts.append(f"resize set {int(saved['w'])} {int(saved['h'])}")
        if saved.get("x") is not None and saved.get("y") is not None:
            parts.append(f"move position {int(saved['x'])} {int(saved['y'])}")
        for p in parts:
            self._msg(cmd=f'[title="^{TITLE}$"] {p}')

    def _tree(self):
        r = self._msg("-t", "get_tree")
        try:
            return json.loads(r.stdout) if r else None
        except (ValueError, AttributeError):
            return None

    def capture(self, pid):
        tree = self._tree()
        if not isinstance(tree, dict):
            return None
        stack = [tree]
        while stack:
            n = stack.pop()
            if n.get("pid") == pid and n.get("rect"):
                r = n["rect"]
                return {"x": int(r["x"]), "y": int(r["y"]),
                        "w": int(r["width"]), "h": int(r["height"])}
            stack.extend(n.get("nodes", []))
            stack.extend(n.get("floating_nodes", []))
        return None


class KdePlacement(Placement):
    """KDE Plasma (Wayland): set the documented read/write KWin `keepAbove`
    property through a small KWin script. Stacking only — no activeWindow, no
    activate(), no geometry, so no focus/pointer jump under Focus-follows-mouse."""
    name = "kde"
    supports_top = True
    enforces_top = True
    top_note = "KWin keep-above"

    # Pure so it can be asserted in the self-test. NEVER add focus/activate/
    # move/warp calls here.
    TEMPLATE = (
        "// migaki vn-textbox: stacking-only keep-above.\n"
        "// NEVER focus/activate/move/warp (see docs/translate.md).\n"
        "function _a4kApply(w) {{\n"
        "    if (w && w.caption === \"{title}\") w.keepAbove = {value};\n"
        "}}\n"
        "var _a4kWins = workspace.windowList();\n"
        "for (var _i = 0; _i < _a4kWins.length; ++_i) _a4kApply(_a4kWins[_i]);\n"
        "workspace.windowAdded.connect(_a4kApply);\n"
    )

    def __init__(self):
        self._loaded = None

    @staticmethod
    def _script(keep):
        return KdePlacement.TEMPLATE.format(
            title=TITLE, value="true" if keep else "false")

    @staticmethod
    def _qdbus(*args):
        exe = shutil.which("qdbus6") or shutil.which("qdbus")
        if not exe:
            return None
        r = _run([exe, "org.kde.KWin", "/Scripting",
                  "org.kde.kwin.Scripting." + args[0], *args[1:]])
        if r is None or r.returncode != 0:
            return None
        return r

    def _unload(self):
        self._qdbus("unloadScript", KWIN_PLUGIN)

    def _write_script(self, keep):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(KWIN_SCRIPT, "w", encoding="utf-8") as f:
                f.write(self._script(keep))
            return True
        except OSError:
            return False

    def set_top(self, keep):
        keep = bool(keep)
        if self._loaded == keep:
            return
        if not self._write_script(keep):
            return
        self._unload()
        self._loaded = None
        r = self._qdbus("loadScript", KWIN_SCRIPT, KWIN_PLUGIN)
        if r is None:
            return
        self._qdbus("start")
        self._loaded = keep
        _log(f"kwin keepAbove={'true' if keep else 'false'} "
             f"(script {KWIN_SCRIPT})")

    def shutdown(self):
        self._unload()
        self._loaded = None


class GnomePlacement(Placement):
    """GNOME Wayland: no client always-on-top API. A bundled GNOME Shell
    extension (deployed by install.sh) watches a state file and calls
    `Meta.Window.make_above()` — stacking only, never `activate()`."""
    name = "gnome"
    enforces_top = True
    top_note = "GNOME Shell extension"

    def __init__(self):
        self.supports_top = self._extension_enabled()

    @staticmethod
    def _extension_enabled():
        exe = shutil.which("gnome-extensions")
        if not exe:
            return False
        r = _run([exe, "list", "--enabled"])
        return bool(r and GNOME_EXT_UUID in (r.stdout or ""))

    def set_top(self, keep):
        if not self.supports_top:
            return
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(GNOME_STATE, "w", encoding="utf-8") as f:
                f.write("1" if keep else "0")
            _log(f"gnome make_above state = {'1' if keep else '0'}")
        except OSError:
            pass

    def shutdown(self):
        # Leave the window's state; the extension only acts on vn-translate
        # windows while they exist.
        pass


def detect():
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        return HyprlandPlacement()
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        return SwayPlacement()
    if _wayland():
        desk = _desktop()
        if "KDE" in desk and (shutil.which("qdbus6") or shutil.which("qdbus")):
            return KdePlacement()
        if "GNOME" in desk:
            return GnomePlacement()
        return WaylandGenericPlacement()
    return X11Placement()
