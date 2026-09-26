#!/usr/bin/env python3
"""textbox.py — Luna-style translation readout (Qt Quick frontend).
Usage: textbox.py [--smoke-test] [--self-test] [--start-workers]
  [--thread NAME|NUM|*]
Window behavior: see docs/translate.md (Textbox on Hyprland).
"""
import json
import os
import sys
import threading
import time
from collections import deque
from PySide6.QtCore import (QByteArray, QModelIndex, QAbstractListModel, QObject,
                            Qt, QTimer, QUrl, QRect, Signal, Slot, Property)
from PySide6.QtGui import QGuiApplication, QRegion, QColor, QPalette
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO_ROOT)
from cfg import load_config
import placement as placement_mod
from core import process as core_process
CONFIG = load_config()
ORG, APP = "vn-translate", "textbox"

HISTORY_MAX = 200


class PairModel(QAbstractListModel):
    JaRole = Qt.UserRole + 1
    EnRole = Qt.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []

    def roleNames(self):
        return {PairModel.JaRole: b"ja", PairModel.EnRole: b"en"}

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        ja, en = self._rows[index.row()]
        if role == PairModel.JaRole:
            return ja
        if role == PairModel.EnRole:
            return en
        return None

    def append(self, ja, en):
        if len(self._rows) >= HISTORY_MAX:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self._rows.pop(0)
            self.endRemoveRows()
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append((ja, en))
        self.endInsertRows()

    def clear(self):
        if not self._rows:
            return
        self.beginRemoveRows(QModelIndex(), 0, len(self._rows) - 1)
        self._rows.clear()
        self.endRemoveRows()

    def last_en(self):
        return self._rows[-1][1] if self._rows else ""


def _palette_defaults():
    """(text, dim, shadow) defaults from the desktop palette, so the box
    follows light/dark instead of assuming a dark desktop."""
    try:
        pal = QGuiApplication.palette()
        return (pal.color(QPalette.ColorRole.WindowText).name(),
                pal.color(QPalette.ColorRole.PlaceholderText).name(),
                pal.color(QPalette.ColorRole.Window).name())
    except Exception:
        return "#f0f0f0", "#9a9a9a", "#000000"


def _css_color(v, fallback):
    # str(QColor) is not a CSS color; use .name() for the QML-side string.
    try:
        if isinstance(v, QColor):
            return v.name()
        s = str(v or fallback)
        return s if (s.startswith("#") and QColor(s).isValid()) else fallback
    except Exception:
        return fallback


class Backend(QObject):
    showJaChanged = Signal()
    keepOnTopChanged = Signal()
    clickThroughChanged = Signal()
    autoHideChanged = Signal()
    statusTextChanged = Signal()
    fontSizeChanged = Signal()
    fontFamilyChanged = Signal()
    enColorChanged = Signal()
    jaColorChanged = Signal()
    shadowEnabledChanged = Signal()
    shadowColorChanged = Signal()
    panelAlphaChanged = Signal()
    chromeAutoHideChanged = Signal()
    chromeVisibleChanged = Signal()
    cornerRadiusChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pairs = PairModel(self)
        self.pending = deque()
        self.ja_queue = deque()
        self.translator = None
        self.glossary = None
        self.last_ja = ""
        self._show_ja = True
        self._keepontop = True
        self._clickthrough = False
        self._autohide = False
        self.autohide_delay = 4
        self._status_text = "● stopped"
        self._font_size = 11.0
        self._font_family = ""
        self._en_def, self._ja_def, self._sh_def = _palette_defaults()
        self._en_color = self._en_def
        self._ja_color = self._ja_def
        self._shadow_enabled = True
        self._shadow_color = self._sh_def
        self._panel_alpha = 0.88
        self._chrome_autohide = False
        self._chrome_visible = True
        self._corner_radius = 10
        self.last_text_time = time.time()
        self._placement = placement_mod.Placement()
        self._saved_geom = None
        self._window = None
        self.gameid = ""       # live-follow this game's stored translate.thread
        self.headless = False  # offscreen self-test: never touch the compositor
        self._title_h = 25
        self._tool_h = 27
        self._chrome_hovered = False
        self._drawer_open = False
        self._pointer_inside = False
        self._pointer_strip = False
        self._hover_override = None  # self-test hook: forces chrome_hovered()
        self.thread = "*"

    # ---- QML-bound properties ----
    showJa = Property(bool,
                      lambda s: s._show_ja,
                      lambda s, v: (setattr(s, "_show_ja", v), s.showJaChanged.emit()),
                      notify=showJaChanged)
    keepOnTop = Property(bool,
                         lambda s: s._keepontop,
                         lambda s, v: (setattr(s, "_keepontop", v), s.keepOnTopChanged.emit()),
                         notify=keepOnTopChanged)
    clickThrough = Property(bool,
                            lambda s: s._clickthrough,
                            lambda s, v: (setattr(s, "_clickthrough", v),
                                          s.clickThroughChanged.emit()),
                            notify=clickThroughChanged)
    autoHide = Property(bool,
                        lambda s: s._autohide,
                        lambda s, v: (setattr(s, "_autohide", v),
                                      s.autoHideChanged.emit()),
                        notify=autoHideChanged)
    statusText = Property(str,
                          lambda s: s._status_text,
                          lambda s, v: (setattr(s, "_status_text", v),
                                        s.statusTextChanged.emit()),
                          notify=statusTextChanged)
    fontSize = Property(float,
                        lambda s: s._font_size,
                        lambda s, v: (setattr(s, "_font_size", float(v)),
                                      s.fontSizeChanged.emit()),
                        notify=fontSizeChanged)
    fontFamily = Property(str,
                          lambda s: s._font_family,
                          lambda s, v: (setattr(s, "_font_family", str(v)),
                                        s.fontFamilyChanged.emit()),
                          notify=fontFamilyChanged)
    enColor = Property(str,
                       lambda s: s._en_color,
                       lambda s, v: (setattr(s, "_en_color", _css_color(v, s._en_def)),
                                     s.enColorChanged.emit()),
                       notify=enColorChanged)
    jaColor = Property(str,
                       lambda s: s._ja_color,
                       lambda s, v: (setattr(s, "_ja_color", _css_color(v, s._ja_def)),
                                     s.jaColorChanged.emit()),
                       notify=jaColorChanged)
    shadowEnabled = Property(bool,
                              lambda s: s._shadow_enabled,
                              lambda s, v: (setattr(s, "_shadow_enabled", bool(v)),
                                            s.shadowEnabledChanged.emit()),
                              notify=shadowEnabledChanged)
    shadowColor = Property(str,
                            lambda s: s._shadow_color,
                            lambda s, v: (setattr(s, "_shadow_color", _css_color(v, s._sh_def)),
                                          s.shadowColorChanged.emit()),
                            notify=shadowColorChanged)
    panelAlpha = Property(float,
                          lambda s: s._panel_alpha,
                          lambda s, v: (setattr(s, "_panel_alpha",
                                               min(1.0, max(0.15, float(v)))),
                                        s.panelAlphaChanged.emit()),
                          notify=panelAlphaChanged)
    def _set_chrome_autohide(self, v):
        v = bool(v)
        if self._chrome_autohide != v:
            self._chrome_autohide = v
            self.chromeAutoHideChanged.emit()
            self._update_chrome_visibility()

    chromeAutoHide = Property(bool,
                              lambda s: s._chrome_autohide,
                              _set_chrome_autohide,
                              notify=chromeAutoHideChanged)
    chromeVisible = Property(bool,
                             lambda s: s._chrome_visible,
                             notify=chromeVisibleChanged)

    def _set_corner_radius(self, v):
        try:
            v = min(16, max(0, int(v)))
        except (TypeError, ValueError):
            return
        if self._corner_radius != v:
            self._corner_radius = v
            self.cornerRadiusChanged.emit()

    cornerRadius = Property(int,
                            lambda s: s._corner_radius,
                            _set_corner_radius,
                            notify=cornerRadiusChanged)

    @staticmethod
    def _query_compositor_radius():
        try:
            import subprocess as _sp
            out = _sp.run(["hyprctl", "getoption", "decoration:rounding"],
                          capture_output=True, text=True, timeout=5).stdout
        except Exception:
            return None
        import re as _re
        m = _re.search(r"int:\s*(-?\d+)", out or "")
        if not m:
            return None
        try:
            return min(16, max(0, int(m.group(1))))
        except ValueError:
            return None

    # ---- top enforcement (delegated to the compositor backend) ----
    def sync_top(self):
        """Apply Top through the backend (stacking only). Never focus/move."""
        if self.headless:
            return
        try:
            self._placement.set_top(self._keepontop)
        except Exception:
            pass

    def _sync_top_deferred(self):
        self.sync_top()
        QTimer.singleShot(500, self.sync_top)
        QTimer.singleShot(1500, self.sync_top)

    def shutdown_compositor(self):
        try:
            self._placement.shutdown()
        except Exception:
            pass

    # ---- window wiring ----
    def attach_window(self, win, placement=None):
        self._window = win
        self._placement = placement or placement_mod.Placement()
        try:
            fmt = win.format()
            fmt.setAlphaBufferSize(8)
            win.setFormat(fmt)
        except Exception:
            pass
        # restore_state() runs while the window is still hidden (QML
        # visible: false), so the first map already has the restored size.
        self.restore_state()
        self.apply_flags(initial=True)
        # Compositor adapters that can only place the window once it exists
        # (sway) run here; Hyprland already floated it pre-map via a rule.
        _saved = self._saved_geom
        QTimer.singleShot(0, lambda: self._placement.after_map(_saved))
        # Always float (a tiled overlay is useless); Top additionally
        # raises/keeps above. Top-off un-raises (also neutralizes a stale rule).
        # The backend is stacking-only: it never focuses or moves the window.
        self._sync_top_deferred()
        self.drain_timer = QTimer(self)
        self.drain_timer.timeout.connect(self.drain)
        self.drain_timer.start(120)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.poll_status)
        self.status_timer.start(2000)
        self.hover_timer = QTimer(self)
        self.hover_timer.timeout.connect(self.hover_tick)
        self.hover_timer.start(100)
        self.hide_timer = QTimer(self)
        self.hide_timer.timeout.connect(self.hide_tick)
        self.hide_timer.start(500)
        self.pin_timer = QTimer(self)
        self.pin_timer.timeout.connect(
            lambda: None if self.headless
            else self._placement.tick(self._keepontop))
        self.pin_timer.start(1000)
        # Live-follow the game's stored translate.thread so picking a hook in
        # the Setup picker takes effect without restarting the session.
        self.thread_timer = QTimer(self)
        self.thread_timer.timeout.connect(self.sync_thread)
        self.thread_timer.start(1000)

    @Slot(float, float)
    def setChromeGeometry(self, title_h, tool_h):
        self._title_h = max(1, int(title_h))
        self._tool_h = max(1, int(tool_h))
        self._mask_applied = None

    @Slot(bool)
    def setChromeHovered(self, hovered):
        self._chrome_hovered = bool(hovered)
        self._update_chrome_visibility()

    @Slot(float, float, bool)
    def pointerAt(self, x, y, inside):
        # Event coords only; cursor/geometry queries do not work on Wayland,
        # so the QML HoverHandler feeds us the pointer position instead.
        inside = bool(inside)
        self._pointer_inside = inside
        strip = False
        if inside:
            try:
                h = self._window.height() if self._window is not None else 0
                strip = y < self._title_h or (h > 0 and y > h - self._tool_h)
            except Exception:
                strip = False
        self._pointer_strip = strip
        self._update_chrome_visibility()

    def _update_chrome_visibility(self):
        show = (not self._chrome_autohide or self._drawer_open
                or self._chrome_hovered or self._pointer_strip)
        if show != self._chrome_visible:
            self._chrome_visible = show
            self.chromeVisibleChanged.emit()

    @Slot(bool)
    def setDrawerOpen(self, opened):
        self._drawer_open = bool(opened)
        self._mask_applied = None
        self.apply_input_mask()
        self._update_chrome_visibility()

    # ---- window flags / input region ----
    @staticmethod
    def _on_wayland():
        try:
            return QGuiApplication.platformName() == "wayland"
        except Exception:
            return False

    def apply_flags(self, initial=False):
        if self._window is None:
            return
        # Bars-only mask architecture (see docs/translate.md); never
        # WindowTransparentForInput on Wayland (drops all mask updates).
        on_wayland = self._on_wayland()
        if on_wayland:
            want_transparent = False
        else:
            want_transparent = self.clickthrough_effective
        # On Wayland never toggle WindowStaysOnTopHint: it is ignored, and
        # setFlags() on a mapped window re-creates it (a focus-steal vector).
        # Top there is enforced by the compositor backend (sync_top).
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._keepontop and not on_wayland:
            flags |= Qt.WindowStaysOnTopHint
        if want_transparent:
            flags |= Qt.WindowTransparentForInput
        if initial or int(flags) != getattr(self, "_flags_applied", None):
            self._flags_applied = int(flags)
            self._window.setFlags(flags)
            if initial or not self._window.isVisible():
                self._window.show()
        self.apply_input_mask()
        self.keepOnTopChanged.emit()
        self.clickThroughChanged.emit()

    def chrome_region(self):
        try:
            w = self._window.width() if self._window is not None else 0
            h = self._window.height() if self._window is not None else 0
        except Exception:
            w, h = 0, 0
        region = QRegion()
        if w > 0 and self._title_h > 0:
            region |= QRegion(0, 0, w, self._title_h)
        if w > 0 and self._tool_h > 0 and h > 0:
            region |= QRegion(0, h - self._tool_h, w, self._tool_h)
        return region

    def _full_region(self):
        try:
            return QRegion(QRect(0, 0, self._window.width(), self._window.height()))
        except Exception:
            return QRegion()

    def apply_input_mask(self, force=False):
        if not self._on_wayland():
            return
        win = self._window
        if win is None:
            return
        try:
            if self.clickthrough_effective:
                want = self.chrome_region()
                if want.isEmpty():
                    want = QRegion()  # fail safe: null = full input
                key = ("chrome", want.boundingRect().getRect())
            else:
                # Null mask: the compositor treats null as full-window input.
                want = QRegion()
                key = ("full", ())
        except Exception:
            return
        if force or key != getattr(self, "_mask_applied", None):
            self._mask_applied = key
            try:
                win.setMask(want)
            except Exception:
                pass

    @property
    def clickthrough_effective(self):
        return self._clickthrough and not self.chrome_hovered() and not self._drawer_open

    def chrome_hovered(self):
        if self._hover_override is not None:
            return self._hover_override
        return self._chrome_hovered

    def hover_tick(self):
        want = self.clickthrough_effective
        if want != getattr(self, "_ct_applied", None):
            self._ct_applied = want
            self.apply_flags()

    def hide_tick(self):
        if not self._autohide or self._window is None:
            return
        try:
            visible = self._window.isVisible()
        except Exception:
            return
        if not visible:
            return
        if time.time() - self.last_text_time < self.autohide_delay:
            return
        if self._pointer_inside:
            self.last_text_time = time.time()
            return
        self._window.hide()

    @Slot()
    def toggleTop(self):
        self._keepontop = not self._keepontop
        if self._keepontop:
            try:
                self._placement.reset_home()
            except Exception:
                pass
            if not getattr(self._placement, "supports_top", True):
                self._status_text = (f"Top unsupported here "
                                     f"({self._placement.name})")
                self.statusTextChanged.emit()
        self._ct_applied = None
        self._mask_applied = None
        self.apply_flags()
        self._sync_top_deferred()
        QTimer.singleShot(800, self.apply_input_mask)

    @Slot()
    def toggleClickthrough(self):
        self._clickthrough = not self._clickthrough
        self._ct_applied = None
        self._mask_applied = None
        self.apply_flags()
        if self._keepontop:
            self._sync_top_deferred()

    @Slot()
    def toggleMode(self):
        self._show_ja = not self._show_ja
        self.showJaChanged.emit()

    @Slot()
    def toggleAutohide(self):
        self._autohide = not self._autohide
        self.autoHideChanged.emit()
        self.last_text_time = time.time()

    @Slot()
    def toggleChromeAutoHide(self):
        self._chrome_autohide = not self._chrome_autohide
        self.chromeAutoHideChanged.emit()
        self._update_chrome_visibility()

    @Slot(int)
    def bumpFont(self, delta):
        self._font_size = max(8.0, self._font_size + delta)
        self.fontSizeChanged.emit()

    @Slot()
    def clearHistory(self):
        self.pairs.clear()

    @Slot()
    def copyCurrent(self):
        try:
            QGuiApplication.clipboard().setText(self.pairs.last_en())
        except Exception:
            pass

    @Slot()
    def retranslate(self):
        rows = self.pairs._rows
        if rows:
            self.ja_queue.append(rows[-1][0])
            self.last_ja = ""

    @Slot()
    def minimize(self):
        try:
            if self._window is not None:
                self._window.showMinimized()
        except Exception:
            pass

    @Slot()
    def saveState(self):
        from PySide6.QtCore import QSettings
        s = QSettings(ORG, APP)
        try:
            if self._window is not None:
                s.setValue("geometry", self._window.saveGeometry())
        except Exception:
            pass
        # Compositor-reported geometry (Wayland cannot give Qt a position);
        # consumed by the placement adapter on the next launch.
        try:
            geom = self._placement.capture(os.getpid()) if self._placement else None
            if geom:
                s.setValue("compositor_geometry", json.dumps(geom))
        except Exception:
            pass
        s.setValue("show_ja", self._show_ja)
        s.setValue("fontsize", self._font_size)
        s.setValue("fontfamily", self._font_family)
        s.setValue("encolor", self._en_color)
        s.setValue("jacololr", self._ja_color)
        s.setValue("shadow", self._shadow_enabled)
        s.setValue("shadowcolor", self._shadow_color)
        s.setValue("panelalpha", self._panel_alpha)
        s.setValue("chromeautohide", self._chrome_autohide)
        s.setValue("cornerradius", self._corner_radius)
        s.setValue("keepontop", self._keepontop)
        s.setValue("autohide", self._autohide)

    # ---- persistence ----
    def restore_state(self):
        from PySide6.QtCore import QSettings
        s = QSettings(ORG, APP)
        try:
            g = s.value("geometry")
            if g and self._window is not None:
                self._window.restoreGeometry(g if isinstance(g, QByteArray) else QByteArray(g))
        except Exception:
            pass
        self._show_ja = s.value("show_ja", True, type=bool)
        try:
            v = float(s.value("fontsize", 11.0))
            self._font_size = v if v >= 8.0 else 11.0
        except (TypeError, ValueError):
            self._font_size = 11.0
        self._font_family = str(s.value("fontfamily", "") or "")
        self._en_color = _css_color(s.value("encolor", ""), self._en_def)
        self._ja_color = _css_color(s.value("jacololr", ""), self._ja_def)
        self._shadow_enabled = s.value("shadow", True, type=bool)
        self._shadow_color = _css_color(s.value("shadowcolor", ""), self._sh_def)
        try:
            _pa = float(s.value("panelalpha", 0.88))
            self._panel_alpha = min(1.0, max(0.15, _pa))
        except (TypeError, ValueError):
            self._panel_alpha = 0.88
        self._chrome_autohide = s.value("chromeautohide", False, type=bool)
        self._chrome_visible = True
        queried = self._query_compositor_radius()
        try:
            raw = s.value("cornerradius", None)
            self._corner_radius = min(16, max(0, int(raw))) if raw is not None else None
        except (TypeError, ValueError):
            self._corner_radius = None
        if self._corner_radius is None:
            self._corner_radius = queried if queried is not None else 10
        self._keepontop = s.value("keepontop", True, type=bool)
        self._autohide = s.value("autohide", False, type=bool)
        # Restores must emit notifies, or QML bindings keep their load-time
        # defaults instead of picking up the persisted values.
        for _sig in (self.showJaChanged, self.keepOnTopChanged,
                     self.clickThroughChanged, self.autoHideChanged,
                     self.statusTextChanged, self.fontSizeChanged,
                     self.fontFamilyChanged, self.enColorChanged,
                     self.jaColorChanged, self.shadowEnabledChanged,
                     self.shadowColorChanged, self.panelAlphaChanged,
                     self.chromeAutoHideChanged, self.chromeVisibleChanged,
                     self.cornerRadiusChanged):
            try:
                _sig.emit()
            except Exception:
                pass

    # ---- content ----
    def append_pair(self, ja, en, record=True):
        self.pairs.append(ja or "", en or "")
        self.last_text_time = time.time()
        try:
            if self._window is not None and not self._window.isVisible():
                self._window.show()
        except Exception:
            pass
        try:
            self._placement.raise_window(self._keepontop)
        except Exception:
            pass

    # ---- live thread sync (pick a hook without restarting) ----
    def sync_thread(self):
        """Follow this game's stored translate.thread live, so the Setup picker
        takes effect on the running backend instead of only next launch."""
        if not self.gameid:
            return
        try:
            from core import store
            games = store.load_games()
            tr = (games.get(self.gameid) or {}).get("translate") or {}
            thread = (tr.get("thread") or "").strip() or "*"
        except Exception:
            return
        if thread == self.thread:
            return
        self.thread = thread
        self.last_ja = ""  # do not suppress the new thread's first line
        self._status_text = (f"● live (thread {thread})" if thread != "*"
                             else "● live (auto)")
        self.statusTextChanged.emit()

    # ---- pipeline (hook thread -> translator thread -> GUI model) ----
    def ensure_workers(self):
        if self.translator is not None:
            return
        try:
            import vn_translate
            # Auto name glossary (per game): keeps names consistent and lets
            # the furigana reading lines resolve them (凛桜 -> Rio).
            save = None
            if self.gameid:
                from names import NameGlossary, glossary_path
                path = glossary_path(self.gameid)
                self.glossary = NameGlossary().load(path)
                save = lambda: self.glossary.save(path)
            self.translator = vn_translate.make_translator(
                True, glossary=self.glossary, save=save)
        except Exception as e:
            self._status_text = f"translator init failed: {e}"
            self.statusTextChanged.emit()
            return
        threading.Thread(target=self.hook_loop, daemon=True).start()
        threading.Thread(target=self.translate_loop, daemon=True).start()

    def hook_loop(self):
        import time as _time
        from hook_client import listen
        while True:
            try:
                listen(CONFIG.get("hook_url", "ws://localhost:6677"),
                       on_message=self.ja_queue.append,
                       thread=lambda: self.thread)
            except Exception as e:
                self.pending.append((None, f"[hook error: {e}]"))
            _time.sleep(5)

    def translate_loop(self):
        import time as _time
        while True:
            try:
                ja = self.ja_queue.popleft()
            except IndexError:
                _time.sleep(0.1)
                continue
            if ja == self.last_ja:
                continue
            self.last_ja = ja
            try:
                out, via = self.translator(ja)
            except Exception as e:
                out, via = f"[translation failed: {e}]", "none"
            if via == "skip":
                continue  # furigana reading line: consumed, not shown
            self.pending.append((ja, out))

    def drain(self):
        while self.pending:
            item = self.pending.popleft()
            try:
                if item[0] is None:
                    self._status_text = item[1]
                    self.statusTextChanged.emit()
                else:
                    ja, out = item
                    self.append_pair(ja, out)
                    self._status_text = "● live"
                    self.statusTextChanged.emit()
            except Exception as e:
                self._status_text = f"● display error: {e}"
                self.statusTextChanged.emit()

    def poll_status(self):
        if core_process.translate_bridge_ok():
            if not self.pending:
                if self.thread not in ("", "*"):
                    self._status_text = f"● live (thread {self.thread})"
                else:
                    self._status_text = "● live (:6677)"
                self.statusTextChanged.emit()
        else:
            self._status_text = "● stopped"
            self.statusTextChanged.emit()


def self_test(backend, window):
    backend.append_pair("テスト一行目", "first test line")
    backend.append_pair("二行目", "second test line")
    assert backend.pairs.rowCount() == 2, "model recorded nothing"
    assert backend.pairs.data(backend.pairs.index(0, 0), PairModel.EnRole) == "first test line"
    before_size = backend._font_size
    before_mode = backend._show_ja
    backend.toggleMode()
    assert backend._show_ja == (not before_mode), "mode toggle failed"
    backend.toggleMode()
    assert backend._show_ja == before_mode, "mode double-toggle must restore"
    backend.retranslate()
    backend.copyCurrent()
    backend.bumpFont(1)
    assert backend._font_size == before_size + 1, "bumpFont did not change fontSize"
    backend.bumpFont(-1)
    assert backend._font_size == before_size, "bumpFont down did not restore"
    backend.toggleTop()
    backend.toggleTop()
    assert backend._keepontop is True, "top double-toggle must restore"
    backend.toggleClickthrough()
    chrome = backend.chrome_region()
    assert not chrome.isEmpty(), "chrome guard region must cover the bars"
    backend._hover_override = False
    backend.hover_tick()
    assert backend.clickthrough_effective, "click-through should be effective off-chrome"
    if window is not None and Backend._on_wayland():
        try:
            m = window.mask()
            assert not m.isEmpty(), "Wayland mask must stay non-null (null = full input)"
        except Exception:
            pass
    backend._hover_override = True
    backend.hover_tick()
    assert not backend.clickthrough_effective, "bar hover must re-enable input"
    if window is not None and Backend._on_wayland():
        backend.apply_input_mask()
        assert window.mask().isEmpty(), "bar hover must restore full (null) input"
    backend._hover_override = None
    backend.toggleClickthrough()
    assert not backend.clickthrough_effective, "click-through off must restore input"
    if window is not None and Backend._on_wayland():
        backend.apply_input_mask()
        try:
            assert window.mask().isEmpty(), "click-through off must restore full (null) input"
        except AssertionError:
            raise
        except Exception:
            pass
    # Compositor backend selection is coherent and never raises.
    _bf = placement_mod.detect()
    assert isinstance(_bf, placement_mod.Placement), "detect must return a Placement"
    assert isinstance(_bf.supports_top, bool), "supports_top must be boolean"
    assert isinstance(_bf.enforces_top, bool), "enforces_top must be boolean"
    backend.toggleAutohide()
    backend.clearHistory()
    assert backend.pairs.rowCount() == 0, "clearHistory failed"
    backend.append_pair("復帰", "recovered")
    assert backend.pairs.rowCount() == 1, "append after clear failed"
    # Style prefs: shadow toggle round-trips (whatever persisted).
    before_shadow = backend._shadow_enabled
    backend._shadow_enabled = not before_shadow
    backend.shadowEnabledChanged.emit()
    assert backend._shadow_enabled is not before_shadow
    backend._shadow_enabled = before_shadow
    backend.shadowEnabledChanged.emit()
    backend.panelAlpha = 5.0
    assert backend._panel_alpha == 1.0, "panelAlpha must clamp to 1.0"
    backend.panelAlpha = -1.0
    assert backend._panel_alpha == 0.15, "panelAlpha must clamp to 0.15"
    backend.panelAlpha = 0.88
    # Drawer open forces full input even under click-through.
    backend._clickthrough = True
    backend._hover_override = False
    assert backend.clickthrough_effective, "click-through should be effective"
    backend.setDrawerOpen(True)
    assert not backend.clickthrough_effective, "open drawer must force full input"
    backend.setDrawerOpen(False)
    assert backend.clickthrough_effective, "closed drawer must restore guard"
    backend._clickthrough = False
    backend._hover_override = None
    # Chrome-autohide reveal zones follow event coordinates.
    assert backend._chrome_visible is True, "chrome starts visible"
    backend._chrome_autohide = True
    backend._chrome_hovered = False
    backend.pointerAt(-1, -1, False)  # outside window
    assert backend._chrome_visible is False, "outside window must hide chrome"
    backend._title_h, backend._tool_h = 30, 30
    backend.pointerAt(260, 5, True)  # top strip
    assert backend._chrome_visible is True, "top strip must reveal chrome"
    backend.pointerAt(260, 160, True)  # middle
    assert backend._chrome_visible is False, "middle must keep chrome hidden"
    backend.pointerAt(260, window.height() - 5, True)  # bottom strip
    assert backend._chrome_visible is True, "bottom strip must reveal chrome"
    backend.setChromeHovered(True)  # bar hover reports keep it shown
    backend.pointerAt(260, 160, True)
    assert backend._chrome_visible is True, "bar hover must keep chrome shown"
    backend.setChromeHovered(False)
    backend._chrome_autohide = False
    backend._update_chrome_visibility()
    assert backend._chrome_visible is True, "autohide off must restore chrome"
    # Restore must emit all notifies, or QML bindings keep load-time defaults.
    _fired = []
    for _sig in (backend.showJaChanged, backend.keepOnTopChanged,
                 backend.clickThroughChanged, backend.autoHideChanged,
                 backend.statusTextChanged, backend.fontSizeChanged,
                 backend.fontFamilyChanged, backend.enColorChanged,
                 backend.jaColorChanged, backend.shadowEnabledChanged,
                 backend.shadowColorChanged, backend.panelAlphaChanged,
                 backend.chromeAutoHideChanged, backend.chromeVisibleChanged,
                 backend.cornerRadiusChanged):
        _sig.connect(lambda _s=_sig: _fired.append(_s))
    backend.restore_state()
    assert len(_fired) == 15, f"restore must emit all 15 notifies, got {len(_fired)}"
    # Synthetic hover must reach pointerAt through the QML HoverHandler.
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QPoint as _QPoint
    QTest.mouseMove(window, _QPoint(10, 10))
    QTest.qWait(250)
    assert backend._pointer_inside is True, "hover handler must report window entry"
    assert backend._pointer_strip is True, "top corner must be a reveal strip"
    QTest.mouseMove(window, _QPoint(window.width() // 2, window.height() // 2))
    QTest.qWait(250)
    assert backend._pointer_inside is True, "middle must still be inside"
    assert backend._pointer_strip is False, "middle must not be a reveal strip"
    # Drawer must report open state.
    drawer = window.findChild(QObject, "styleDrawer")
    assert drawer is not None, "style drawer must exist"
    drawer.setProperty("visible", True)
    assert backend._drawer_open is True, "drawer must report open state"
    # Tap path: swatch tap must set target AND open the dialog.
    from PySide6.QtCore import QPointF as _QPointF
    from PySide6.QtGui import Qt as _Qt
    from PySide6.QtQuick import QQuickItem as _QQuickItem
    _en_btn = window.findChild(_QQuickItem, "enColorBtn")
    assert _en_btn is not None, "EN swatch must exist"
    _center = _en_btn.mapToScene(_QPointF(_en_btn.property("width") / 2.0,
                                          _en_btn.property("height") / 2.0))
    QTest.mouseClick(window, _Qt.LeftButton, _Qt.NoModifier,
                     _center.toPoint())
    QTest.qWait(300)
    assert drawer.property("colorTarget") == "en", "tap must select EN target"
    _dlg2 = window.findChild(QObject, "colorDialog")
    assert _dlg2 is not None and bool(_dlg2.property("visible")), \
        "tap must open the color dialog"
    # Font family model must be usable and show Default when unset.
    _combo = window.findChild(QObject, "fontCombo")
    assert _combo is not None, "font combo must exist"
    assert int(_combo.property("count")) > 1, "font model must list system fonts"
    if not backend._font_family:
        assert str(_combo.property("currentText")) == "Default", \
            "unset family must display Default"
    # Color picker wiring: dialog accept must recolor the backend (and through
    # it, every delegate) for each target.
    from PySide6.QtCore import QMetaObject as _QMeta
    _dlg = window.findChild(QObject, "colorDialog")
    assert _dlg is not None, "color dialog must exist"
    for _target, _attr, _color in (("en", "_en_color", "#ff0000"),
                                   ("ja", "_ja_color", "#00ff00"),
                                   ("shadow", "_shadow_color", "#0000ff")):
        drawer.setProperty("colorTarget", _target)
        QTest.qWait(100)
        _QMeta.invokeMethod(_dlg, "open")
        QTest.qWait(200)
        _dlg.setProperty("selectedColor", _color)
        _QMeta.invokeMethod(_dlg, "accept")
        QTest.qWait(100)
        assert getattr(backend, _attr) == _color, \
            f"accept must apply {_target} color"
    drawer.setProperty("visible", False)
    QTest.qWait(300)
    assert backend._drawer_open is False, "drawer must report closed state"
    # Drawer content must reserve a scrollbar lane.
    _col = window.findChild(QObject, "styleColumn")
    assert _col is not None, "style column must exist"
    _dw = drawer.property("width")
    assert abs(float(_col.property("width")) - (float(_dw) - 34.0)) < 1.0, \
        "style content must reserve a scrollbar lane"
    # Autoscroll must end at bottom.
    from PySide6.QtTest import QTest as _QTest2
    _view = window.findChild(QObject, "historyView")
    assert _view is not None, "history ListView must exist"
    for _i in range(30):
        backend.append_pair(f"スクロール行{_i}", f"scroll check line {_i} with padding words")
    _QTest2.qWait(600)
    assert bool(_view.property("atYEnd")), "must stick to bottom on new lines"
    # ...but never yank a user who scrolled up to read history.
    _view.setProperty("contentY", 0.0)
    _QTest2.qWait(200)
    backend.append_pair("新規", "fresh line while scrolled up")
    _QTest2.qWait(600)
    assert float(_view.property("contentY")) < 1.0, "must not yank scrolled-up readers"
    # Simulated drag: lines must not move a dragged reader.
    _mid = float(_view.property("contentHeight")) / 2.0
    _view.setProperty("contentY", _mid)
    _QTest2.qWait(200)
    assert float(_view.property("contentY")) > 1.0, "test setup: must be mid-list"
    backend.append_pair("追従", "line during simulated drag")
    _QTest2.qWait(600)
    assert abs(float(_view.property("contentY")) - _mid) < 2.0, \
        "dragged readers must stay put"
    # Scroll to the last *valid* position (max = contentHeight - height; using
    # contentHeight itself is out of range and makes the latch see a decrease).
    _max_y = max(0.0, float(_view.property("contentHeight"))
                 - float(_view.property("height")))
    _view.setProperty("contentY", _max_y)
    _QTest2.qWait(200)
    backend.append_pair("再開", "line after scrolling back down")
    _QTest2.qWait(600)
    assert bool(_view.property("atYEnd")), "must resume following at the end"
    # Compositor control is STACKING-ONLY: it must never focus/activate, move
    # or warp. (KDE Focus-follows-mouse warps the cursor on activation.)
    import tempfile
    import placement as _pm
    _calls = []

    # KWin script code: keepAbove only, no focus/activate/move/warp tokens
    # (comments are excluded so the warning text can name them).
    for _keep, _want in ((True, "true"), (False, "false")):
        _js = _pm.KdePlacement._script(_keep)
        assert f"w.keepAbove = {_want}" in _js, "KDE script must set keepAbove"
        _code = "\n".join(l for l in _js.splitlines()
                          if not l.strip().startswith("//"))
        for _bad in ("activeWindow", "activate", "raiseWindow", "geometry",
                     "move", "cursor", "focus", "warp"):
            assert _bad not in _code, f"KDE script must never contain {_bad!r}"
    # KDE set_top is idempotent (loads once per real state change).
    _kde = _pm.KdePlacement()
    _kde._write_script = lambda keep: True
    _kde._qdbus = lambda *a: (_calls.append(a), 0)[1]
    _kde.set_top(True)
    _kde.set_top(True)
    assert sum(1 for c in _calls if c[0] == "loadScript") == 1, \
        "KDE must load the script once per state change"
    _calls.clear()

    # Hyprland top enforcement with a canned compositor; every dispatch is a
    # stacking/float verb and forbidden focus/move verbs are dropped.
    _hy = _pm.HyprlandPlacement()
    _hy._ready = lambda: True
    _real_run = _pm._run
    _world = {"pinned": False, "ws": 1, "active": 1}

    def _fake_run(argv, timeout=5):
        _calls.append(argv)
        s = str(argv)
        if "action=\"disable\"" in s:
            _world["pinned"] = False
        elif "action=\"enable\"" in s:
            _world["pinned"] = True
        return None

    try:
        _pm._run = _fake_run
        _hy._json = lambda *a: (
            [{"pid": os.getpid(), "workspace": {"id": _world["ws"]},
              "pinned": _world["pinned"], "floating": True}]
            if a == ("clients",) else {"id": _world["active"]})
        # Pin decision matrix.
        W = _hy._want_pinned
        assert W(1, 1, True) is True, "same workspace + Top must pin"
        assert W(2, 1, True) is False, "other workspace must unpin"
        assert W(1, 1, False) is False, "Top off must never pin"
        assert W(1, None, True) is False, "unknown home must not pin"
        # Own workspace + Top -> pin enable/adopt.
        _hy.home_ws = None
        _hy.tick(True)
        assert _hy.home_ws == 1, "tick must adopt home workspace"
        assert any("action=\"enable\"" in str(c) for c in _calls), \
            "mismatch must dispatch pin enable"
        # Already pinned -> no dispatch.
        _calls.clear()
        _hy.tick(True)
        assert _calls == [], "matching state must not dispatch"
        # Leaving home -> unpin.
        _world["active"] = 2
        _hy.tick(True)
        assert any("action=\"disable\"" in str(c) for c in _calls), \
            "leaving home must dispatch pin disable"
        # Follow-residue must not re-adopt home.
        _calls.clear()
        _hy.home_ws = 1
        _hy.last_pinned = True
        _world["ws"], _world["active"] = 2, 2
        _hy.tick(True)
        assert _hy.home_ws == 1, "follow-residue must keep the old home"
        # Genuine user move adopts the new home (ws edge while unpinned).
        _calls.clear()
        _hy.last_ws = 1
        _hy.last_pinned = False
        _world["pinned"] = False
        _hy.tick(True)
        assert _hy.home_ws == 2, "user move must adopt the new home"
        # Every dispatched command is stacking-only.
        _flat = [c[2] for c in _calls if len(c) > 2]
        assert _flat, "expected at least one dispatched command"
        for _c in _flat:
            _low = _c.lower()
            assert (any(_low.startswith(a) for a in _pm.HyprlandPlacement.ALLOWED_DISPATCH)), \
                f"non-stacking dispatch: {_c!r}"
            for _bad in ("focus", "cursor", "movewindow", "moveactive",
                         "resize", "activate", "active"):
                assert _bad not in _low, f"focus/move dispatch: {_c!r}"
        # Forbidden dispatches are silently dropped.
        _calls.clear()
        _hy._dispatch('hl.dsp.window.focuswindow({window="title:^vn-translate$"})')
        _hy._dispatch('hl.dsp.window.movecursor({x=1, y=1})')
        _hy._dispatch('hl.dsp.window.movewindow({window="title:^vn-translate$"})')
        assert _calls == [], "focus/move/cursor dispatches must be dropped"
    finally:
        _pm._run = _real_run

    # GNOME: the only side effect is the state file (never activate/warp).
    _gn = _pm.GnomePlacement()
    _gn.supports_top = True
    _real_state = _pm.GNOME_STATE
    _tmp_state = os.path.join(tempfile.mkdtemp(), "gnome-top")
    _pm.GNOME_STATE = _tmp_state
    try:
        _gn.set_top(True)
        assert open(_tmp_state).read().strip() == "1", "GNOME Top on writes state"
        _gn.set_top(False)
        assert open(_tmp_state).read().strip() == "0", "GNOME Top off writes state"
    finally:
        _pm.GNOME_STATE = _real_state

    # Compositor radius query degrades to None/valid-int, never raises.
    _r = Backend._query_compositor_radius()
    assert _r is None or (isinstance(_r, int) and 0 <= _r <= 16), "radius query must be None or 0..16"

    # Live hook switch: stored thread is picked up without a restart.
    import json as _json2
    from core import paths as _paths
    _orig_games_json = _paths.GAMES_JSON
    _tmp_j = os.path.join(tempfile.mkdtemp(), "games.json")
    with open(_tmp_j, "w") as _f:
        _json2.dump({"games": {"unit": {"translate": {"thread": "Anim3"}}}}, _f)
    _paths.GAMES_JSON = _tmp_j
    try:
        backend.gameid = "unit"
        backend.thread = "*"
        backend.sync_thread()
        assert backend.thread == "Anim3", "sync_thread must follow the store"
    finally:
        _paths.GAMES_JSON = _orig_games_json
        backend.gameid = ""

    # Session purge: only ever touches an isolated automation profile.
    _cp = core_process
    tmp = tempfile.mkdtemp()
    prof = os.path.join(tmp, "brave-cdp-profile")
    os.makedirs(os.path.join(prof, "Default", "Sessions"))
    for name in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        open(os.path.join(prof, "Default", name), "w").close()
    assert _cp.is_safe_automation_profile(prof), "isolated profile must be allowed"
    assert not _cp.is_safe_automation_profile(
        "~/.config/BraveSoftware/Brave-Browser/Default"), "real profile must be refused"
    assert _cp.purge_browser_session(prof) is True, "purge must remove session state"
    assert not os.path.exists(os.path.join(prof, "Default", "Current Session"))
    assert not os.path.exists(os.path.join(prof, "Default", "Sessions"))
    assert _cp.purge_browser_session(
        "~/.config/BraveSoftware/Brave-Browser/Default") is False, \
        "purge must refuse a real profile"
    print("self-test: ALL OK")


def main():
    app = QGuiApplication(sys.argv)
    try:
        QQuickStyle.setFallbackStyle("Fusion")
        QQuickStyle.setStyle("org.kde.desktop")
    except Exception:
        pass
    engine = QQmlApplicationEngine()
    qml_errors = []
    engine.warnings.connect(lambda w: qml_errors.extend(w))
    # Closing the readout ends the session: shut the DeepL browser down
    # cleanly (tabs closed, no session restore) so nothing lingers.
    app.aboutToQuit.connect(core_process.close_translator_browser)
    backend = Backend(app)
    app.aboutToQuit.connect(backend.shutdown_compositor)
    args = sys.argv[1:]
    if "--thread" in args and args.index("--thread") + 1 < len(args):
        backend.thread = args[args.index("--thread") + 1]
    if "--gameid" in args and args.index("--gameid") + 1 < len(args):
        backend.gameid = args[args.index("--gameid") + 1]
    headless = "--self-test" in sys.argv or "--smoke-test" in sys.argv
    backend.headless = headless
    # Offscreen tests must never poke the real compositor.
    placement = (placement_mod.Placement() if headless
                 else placement_mod.detect())
    try:
        from PySide6.QtCore import QSettings
        raw = QSettings(ORG, APP).value("compositor_geometry")
        backend._saved_geom = json.loads(raw) if raw else None
    except Exception:
        backend._saved_geom = None
    # Float before the window maps (tiling compositors would otherwise tile it
    # and clobber the geometry). Skipped for the offscreen self-tests.
    if not headless:
        placement.pre_map(backend._saved_geom)
    app._qml_objects = (backend,)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("pairModel", backend.pairs)
    try:
        from PySide6.QtGui import QFontDatabase
        engine.rootContext().setContextProperty(
            "fontFamilies", QFontDatabase.families())
    except Exception:
        engine.rootContext().setContextProperty("fontFamilies", [])
    engine.load(QUrl.fromLocalFile(os.path.join(HERE, "qml", "Textbox.qml")))
    roots = engine.rootObjects()
    if not roots:
        print("qml load failed", "\n".join(str(e) for e in qml_errors), file=sys.stderr)
        sys.exit(2)
    window = roots[0]
    backend.attach_window(window, placement)
    if "--smoke-test" in sys.argv or "--self-test" in sys.argv:
        if "--self-test" in sys.argv:
            QTimer.singleShot(800, lambda: self_test(backend, window))
        QTimer.singleShot(4000, lambda: app.exit(0 if not qml_errors else 3))
    if "--start-workers" in sys.argv:
        backend.ensure_workers()
    if "--self-test" in sys.argv:
        QTimer.singleShot(3900, lambda: print("qml warnings:", [str(e) for e in qml_errors])
                          if qml_errors else None)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
