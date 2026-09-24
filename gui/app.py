#!/usr/bin/env python3
"""migaki-gui — Qt Quick frontend for the Migaki launchers.

Thin UI over the scripts/ runners; shares ~/.config/migaki/games.json
with the `migaki` TUI. All launch/library/process logic lives in core/;
this file is the QML bootstrap plus the QML-bound backend.
"""
import json
import os
import shutil
import sys
import threading

APP_DIR = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.path.dirname(APP_DIR)
sys.path.insert(0, REPO_ROOT)
from core import paths, store, library, commands, process, system, icons

from PySide6.QtCore import (QAbstractListModel, QModelIndex, QObject, Qt,
                            QProcess, QProcessEnvironment, QTimer, QUrl,
                            Signal, Slot, Property, QMetaObject, Q_ARG,
                            QCoreApplication, qInstallMessageHandler)
from PySide6.QtGui import (QGuiApplication, QPalette, QFont, QFontDatabase,
                           QIcon)
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

STYLE = "org.kde.desktop"


def _is_benign_log_line(line):
    """Stderr chatter that is expected and not a real problem: the NW.js
    wrapper's X11 note and stock Chromium warnings. Hidden from the run log so
    actual errors stand out (see docs/limits.md)."""
    low = line.lower()
    if "wayland not detected, starting in x11" in low:
        return True
    if "account_consistency_mode_manager" in low:
        return True
    if "chrome/browser/push_messaging" in low:
        return True
    if "wayland_object.cc" in low and "binding to" in low:
        return True
    return False


def apply_style():
    """Native KDE Quick Controls (follows kdeglobals); Fusion fallback."""
    try:
        QQuickStyle.setFallbackStyle("Fusion")
        QQuickStyle.setStyle(STYLE)
    except Exception:
        try:
            QQuickStyle.setStyle("Fusion")
        except Exception:
            pass


def apply_gui_font(app, base_font):
    """Per-app GUI font from config over the environment font.
    Empty `gui.font` / `gui.font_size` <= 0 keep the environment value
    (qt6ct/KDE), so the default is whatever the user already configured."""
    cfg = store.load_config()
    family = str(cfg.get("gui.font", "") or "").strip()
    try:
        size = int(cfg.get("gui.font_size", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    font = QFont(base_font)
    if family:
        font.setFamily(family)
    if size > 0:
        font.setPointSize(size)
    app.setFont(font)
    # Push it onto live windows so the change shows without a relaunch.
    engine = getattr(app, "_engine", None)
    if engine is not None:
        for obj in engine.rootObjects():
            try:
                obj.setProperty("font", font)
            except Exception:
                pass
    return font

QML_ERRORS = []


def _capture_qt_messages(mode, context, message):
    text = str(message)
    # The KDE Quick Controls style emits its own warnings (notably a benign
    # implicitHeight binding loop from its Menu.qml, where the ListView feeds
    # the template's implicitHeight). That is not our code and never
    # actionable — drop vendor-style output so it cannot spam the log or fail
    # the self-test. Genuine errors in our own QML are still captured below.
    if "org/kde/desktop/" in text or "qrc:/qt/qml/org/kde/" in text:
        return
    if ".qml:" in text or "TypeError" in text or "ReferenceError" in text \
            or "is not defined" in text:
        QML_ERRORS.append(text)
        sys.stderr.write("[qml] " + text + "\n")
        sys.stderr.flush()


def _log_path():
    d = os.path.expanduser("~/.cache/migaki")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return None
    return os.path.join(d, "gui.log")


class GamesModel(QAbstractListModel):
    GidRole = Qt.UserRole + 1
    NameRole = Qt.UserRole + 2
    InfoRole = Qt.UserRole + 3
    IconRole = Qt.UserRole + 4

    iconResolved = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._icon_pending = set()
        self._icon_attempted = set()
        self._icon_wanted = set()
        self._icon_flush_scheduled = False
        # Emitted from the icon worker; auto-queued onto the GUI thread.
        self.iconResolved.connect(self._apply_icon)

    def roleNames(self):
        return {GamesModel.GidRole: b"gid", GamesModel.NameRole: b"name",
                GamesModel.InfoRole: b"info", GamesModel.IconRole: b"icon"}

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        return self._rows[index.row()].get(
            {GamesModel.GidRole: "gid", GamesModel.NameRole: "name",
             GamesModel.InfoRole: "info", GamesModel.IconRole: "icon"}.get(role))

    @Slot()
    def refresh(self):
        # Cached icons only: extraction (icoextract) runs in the background so
        # opening/refreshing the list never blocks the GUI thread.
        self.beginResetModel()
        rows = []
        missing = []
        for gid, g in sorted(store.load_games().items(),
                             key=lambda kv: kv[1].get("name", "")):
            ip = icons.cached_icon(gid)
            if ip is None and gid not in self._icon_attempted:
                missing.append(gid)
            tr = (g.get("translate") or {}).get("enabled") == "1"
            rows.append({
                "gid": gid,
                "name": g.get("name", gid),
                "info": f"{g.get('runner', '?')}, {g.get('variant', '?')}{', +translate' if tr else ''}",
                "icon": ("file://" + ip) if ip else "",
            })
        self._rows = rows
        self.endResetModel()
        self._want_icons(missing)

    @Slot(str)
    def request_icon(self, gid):
        """Ask for one game's icon (detail pane); resolves in the background."""
        self._want_icons([gid])

    def _want_icons(self, gids):
        """Queue icons but defer the worker past the first paint so icoextract
        never competes with the window map."""
        self._icon_wanted.update(
            g for g in gids if g and g not in self._icon_attempted)
        if self._icon_wanted and not self._icon_flush_scheduled:
            self._icon_flush_scheduled = True
            QTimer.singleShot(1200, self._flush_icons)

    @Slot()
    def _flush_icons(self):
        self._icon_flush_scheduled = False
        gids = list(self._icon_wanted)
        self._icon_wanted.clear()
        self._queue_icons(gids)

    def _queue_icons(self, gids):
        todo = [g for g in gids
                if g and g not in self._icon_pending
                and g not in self._icon_attempted]
        if not todo:
            return
        self._icon_pending.update(todo)
        threading.Thread(target=self._icon_worker, args=(todo,), daemon=True).start()

    def _icon_worker(self, gids):
        games = store.load_games()
        for gid in gids:
            self._icon_pending.discard(gid)
            self._icon_attempted.add(gid)
            g = games.get(gid) or {}
            try:
                path = icons.resolve_icon(g.get("runner", ""),
                                          g.get("path", ""), gid)
            except Exception:
                path = None
            if path:
                self.iconResolved.emit(gid, path)

    @Slot(str, str)
    def _apply_icon(self, gid, path):
        for i, r in enumerate(self._rows):
            if r["gid"] == gid:
                r["icon"] = ("file://" + path) if path else ""
                idx = self.index(i, 0)
                self.dataChanged.emit(idx, idx, [GamesModel.IconRole])
                return

    @Slot(str, result=int)
    def index_of(self, gid):
        for i, r in enumerate(self._rows):
            if r["gid"] == gid:
                return i
        return -1

    @Slot(int, result=str)
    def gidAt(self, index):
        if 0 <= index < len(self._rows):
            return self._rows[index]["gid"]
        return ""


class GuiBackend(QObject):
    statusChanged = Signal()
    bridgeChanged = Signal()
    runningChanged = Signal()
    logAppended = Signal(str)
    logCleared = Signal()
    prompt = Signal(str, str, str)
    threadResults = Signal(str)
    gamesChanged = Signal()
    setupLaunched = Signal(str)
    gpusChanged = Signal()
    pathPicked = Signal(str, str)  # (target "file"|"dir", chosen path or "")

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self._model = model
        self._status = "Idle."
        self._bridge = "○ bridge down"
        self._running = False
        self.proc = None
        self.textbox_proc = None
        self._picker = None
        self._log_carry = ""  # incomplete trailing log line between reads
        self._running_gid = None
        self._running_translate = False
        self._pending = None
        self._pick_cancel = threading.Event()
        self._gpu_warming = False
        self._gpu_fp = system.gpu_fingerprint()
        # Load the cached list (instant, no vulkaninfo at startup); a cold or
        # stale cache warms lazily on the first listGpus() call.
        self._gpus, self._gpu_stale = system.gpu_cache(fingerprint=self._gpu_fp)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll_bridge)
        self._timer.start(3000)
        self.poll_bridge()

    def _get_status(self):
        return self._status

    def _set_status(self, v):
        if v != self._status:
            self._status = v
            self.statusChanged.emit()

    statusText = Property(str, _get_status, _set_status, notify=statusChanged)

    def _get_bridge(self):
        return self._bridge

    bridgeText = Property(str, _get_bridge, notify=bridgeChanged)

    def _get_running(self):
        return self._running

    def _set_running(self, v):
        if v != self._running:
            self._running = v
            self.runningChanged.emit()

    running = Property(bool, _get_running, _set_running, notify=runningChanged)

    @Slot()
    def poll_bridge(self):
        try:
            up = process.translate_bridge_ok()
        except Exception:
            up = False
        txt = "● translation bridge (:6677)" if up else "○ bridge down"
        if txt != self._bridge:
            self._bridge = txt
            self.bridgeChanged.emit()

    @Slot(str, result=str)
    def detect(self, path):
        res = system.detect(path.strip())
        if res is None:
            return ""
        return "|".join(res)

    @Slot(str, result=str)
    def validateGame(self, data_json):
        try:
            return library.validate_entry(json.loads(data_json)) or ""
        except (ValueError, TypeError):
            return "Invalid game data."

    @Slot(str, str, result=str)
    def saveGame(self, gid, data_json):
        try:
            data = json.loads(data_json)
        except (ValueError, TypeError):
            return "Invalid game data."
        reason = library.validate_entry(data)
        if reason:
            return reason
        games = store.load_games()
        if gid:
            prev = games.get(gid, {})
            games[gid] = library.normalize_edit(data, prev)
            store.save_games(games)
        else:
            gid = store.new_game(games, data)
        self._model.refresh()
        self.gamesChanged.emit()
        return ""

    @Slot(str, result=str)
    def gameData(self, gid):
        return json.dumps(store.load_games().get(gid, {}))

    @Slot(str)
    def removeGame(self, gid):
        store.remove_game(gid)
        self._model.refresh()
        self.gamesChanged.emit()

    @Slot(str, result=str)
    def iconFor(self, gid):
        # Cached only; kick background extraction and fill in when ready
        # (gamesModel.iconResolved → Main.qml).
        ip = icons.cached_icon(gid)
        if ip is None:
            self._model.request_icon(gid)
            return ""
        return "file://" + ip

    @Slot(str, result=str)
    def gameDetails(self, gid):
        g = store.load_games().get(gid, {})
        if not g:
            return "Select a game."
        tr = g.get("translate", {})
        tr_txt = ""
        if tr.get("enabled") == "1":
            src = (tr.get("hook_code") or "").strip()
            thread = (tr.get("thread") or "").strip()
            if src and thread:
                src = f"{src}, thread {thread}"
            elif thread:
                src = f"thread {thread}"
            tr_txt = f"Translation: on ({src or 'hook auto-detect'})<br>"
        return (
            f"<b>{g.get('name', gid)}</b><br>"
            f"Runner: {g.get('runner', '?')} &nbsp; Variant: {g.get('variant', '?')}<br>"
            f"GPU: {g.get('gpu', '?')} &nbsp; FPS cap: {g.get('fps', '?')} &nbsp; "
            f"Overlay: {'on' if g.get('hud') == '1' else 'off'}<br>"
            + (f"Prefix: {g.get('prefix_mode', 'shared')}<br>" if g.get("runner") == "proton" else "")
            + (f"Language: {g.get('lang')}<br>" if g.get("lang") else "")
            + tr_txt
            + f"Path: {g.get('path', '?')}")

    @Slot(result="QVariant")
    def listVariants(self):
        return system.list_variants() or ["L"]

    @Slot(str, result=str)
    def variantNote(self, v):
        return system.variant_note(v)

    @Slot(result="QVariant")
    def listGpus(self):
        # Instant: cached list, or the fallback until the background warm
        # finishes (then gpusChanged lets the wizard refill the combo).
        # Re-check the cheap fingerprint to catch a GPU added/removed.
        fp = system.gpu_fingerprint()
        if fp != self._gpu_fp:
            self._gpu_fp = fp
            self._gpu_stale = True
        if self._gpus is None or self._gpu_stale:
            self.warm_gpus()
            if self._gpus is None:
                return ["auto (discrete GPU preferred)"]
        return self._gpus

    @Slot()
    def refreshGpus(self):
        """Force a fresh GPU probe (Settings > Refresh GPUs)."""
        self._gpu_stale = True
        self.warm_gpus()

    def warm_gpus(self):
        if self._gpu_warming:
            return
        self._gpu_warming = True
        threading.Thread(target=self._gpu_worker, daemon=True).start()

    def _gpu_worker(self):
        try:
            gpus = system.list_gpus() or []
        except Exception:
            gpus = []
        if len(gpus) > 1:
            self._gpus = gpus
            self._gpu_fp = system.gpu_fingerprint()
            system.save_gpu_cache(gpus, self._gpu_fp)
        elif self._gpus is None:
            self._gpus = ["auto (discrete GPU preferred)"]
        self._gpu_stale = False
        self._gpu_warming = False
        self.gpusChanged.emit()

    @Slot(result=str)
    def runnersJson(self):
        return json.dumps(paths.RUNNERS)

    @Slot(result=str)
    def protonsJson(self):
        """Detected Proton builds for the Settings dropdown ('' = umu-managed)."""
        return json.dumps(system.list_protons())

    @Slot(result="QVariant")
    def locales(self):
        return ["System default"] + list(paths.LOCALES)

    @Slot(QUrl, result=str)
    def fileUrlToPath(self, url):
        try:
            return url.toLocalFile()
        except Exception:
            return ""

    @Slot(str, result=QUrl)
    def pathToFileUrl(self, path):
        try:
            return QUrl.fromLocalFile(path)
        except Exception:
            return QUrl()

    @Slot(str, result=bool)
    def isDir(self, path):
        return os.path.isdir(path.strip())

    @Slot(result=str)
    def homeDir(self):
        return os.path.expanduser("~")

    @Slot(result=str)
    def mediaDir(self):
        return f"/run/media/{os.environ.get('USER', '')}"

    @Slot(result=str)
    def lastDir(self):
        d = store.load_config().get("gui.last_dir", os.path.expanduser("~"))
        return d if os.path.isdir(d) else os.path.expanduser("~")

    @Slot(str)
    def rememberDir(self, path):
        cfg = store.load_config()
        cfg["gui.last_dir"] = path if os.path.isdir(path) else os.path.dirname(path)
        store.save_config(cfg)

    def _picker_argv(self, target, runner="proton"):
        """Native picker argv: KDE's kdialog first, zenity fallback, None when
        neither exists (the QML dialogs are then used). Proton games default
        to a .exe filter; native Linux executables show all files."""
        start = self.lastDir()
        if target == "dir":
            title = "Select RPGMaker game folder"
            if shutil.which("kdialog"):
                return ["kdialog", "--getexistingdirectory", start,
                        "--title", title]
            if shutil.which("zenity"):
                return ["zenity", "--file-selection", "--directory",
                        "--title", title, "--filename", start + os.sep]
        exe_filter = runner != "native"
        title = ("Select Windows game executable" if exe_filter
                 else "Select game executable")
        if shutil.which("kdialog"):
            argv = ["kdialog", "--getopenfilename", start]
            if exe_filter:
                argv.append("Windows executables (*.exe *.EXE)\nAll files (*)")
            argv += ["--title", title]
            return argv
        if shutil.which("zenity"):
            argv = ["zenity", "--file-selection", "--title", title]
            if exe_filter:
                argv += ["--file-filter", "Windows executables | *.exe *.EXE"]
            argv += ["--file-filter", "All files | *",
                     "--filename", start + os.sep]
            return argv
        return None

    @Slot(str, str, result=bool)
    def pickPath(self, target, runner="proton"):
        """Open the desktop's native file/folder picker (KDE kdialog, zenity
        fallback). Returns False when neither is installed, so QML can fall
        back to its own dialog."""
        argv = self._picker_argv(target, runner)
        if not argv:
            return False
        proc = QProcess(self)
        proc.setProgram(argv[0])
        proc.setArguments(argv[1:])
        proc.finished.connect(
            lambda _code, _status, p=proc, t=target: self._picked_path(t, p))
        self._picker = proc
        proc.start()
        return True

    def _picked_path(self, target, proc):
        out = proc.readAllStandardOutput().data().decode("utf-8", "replace")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        path = lines[0] if lines else ""
        if path:
            self.rememberDir(path)
        self.pathPicked.emit(target, path)
        proc.deleteLater()

    @Slot(str, bool, result=str)
    def launchGame(self, gid, unfiltered):
        game = store.load_games().get(gid)
        if not game:
            return "Unknown game."
        if not os.path.exists(game.get("path", "")):
            return f"Path no longer exists:\n{game['path']}"
        if self.proc is not None:
            return "A game is already running."
        if process.find_strays(process.stray_token(game)):
            mode = "unfiltered" if unfiltered else "filtered"
            self._pending = ("launch", gid, unfiltered)
            self.emit_prompt("Stale game processes",
                             f"Leftover processes of '{game.get('name', gid)}' are still "
                             f"running.\nLaunching {mode} now would attach to them "
                             f"instead of starting fresh.",
                             ["Kill && Launch", "Launch anyway", "Cancel"])
            return "pending"
        return self._start_game(gid, game, unfiltered)

    def _start_game(self, gid, game, unfiltered):
        import datetime
        argv = commands.build_command(game)
        env = QProcessEnvironment.systemEnvironment()
        if unfiltered:
            for var in paths.VKBASALT_ENV_VARS:
                env.remove(var)
            env.insert("DISABLE_VKBASALT", "1")
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        mode = "unfiltered A/B" if unfiltered else f"filtered ({game.get('variant', '')})"
        self.logCleared.emit()
        self._log_carry = ""
        self.logAppended.emit(f"[{stamp}] {game.get('name', gid)} — {mode}")
        self.logAppended.emit(f"$ {' '.join(argv)}\n")
        self.proc = QProcess(self)
        self.proc.setProgram(argv[0])
        self.proc.setArguments(argv[1:])
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read_log)
        self.proc.finished.connect(self._finished)
        self.proc.start()
        if not self.proc.waitForStarted(10000):
            self.proc = None
            return "Failed to start the launcher script."
        self._set_status(f"Running {game.get('name', '')} — {mode}…")
        self._set_running(True)
        self._running_gid = gid
        self._running_translate = False
        return ""

    @Slot(str, bool, result=str)
    def translateGame(self, gid, setup):
        game = store.load_games().get(gid)
        if not game:
            return "Unknown game."
        if game.get("runner") != "proton":
            return "Translation needs a Proton/Windows game."
        tr = game.get("translate") or {}
        if tr.get("enabled") != "1":
            return "Enable translation for this game first (Edit…)."
        # A hook is "saved" when either a hook code or a picked thread exists.
        hook_saved = bool((tr.get("hook_code") or "").strip()
                          or (tr.get("thread") or "").strip())
        if not setup and not hook_saved:
            self.logAppended.emit(
                "No hook recorded for this game — starting Setup Text Hooker.")
            setup = True
        if not os.path.exists(game.get("path", "")):
            return f"Path no longer exists:\n{game['path']}"
        if self.proc is not None:
            return "A game is already running."
        if process.translate_bridge_ok():
            self._pending = ("translate", gid, setup)
            self.emit_prompt("Translation session live",
                             "A translation session is already running.\n"
                             "Replace it with a fresh launch, or open the Textbox?",
                             ["Stop && Launch new", "Open Textbox", "Cancel"])
            return "pending"
        if process.translate_wedge_pids(game.get("path", "")):
            self._pending = ("translate", gid, setup)
            self.emit_prompt("Wedged translation session",
                             "A previous translation container is stuck (no game running, "
                             "prefix held).\nNew launches stall behind it until cleared.",
                             ["Clear && Launch", "Cancel"])
            return "pending"
        if process.find_strays(process.stray_token(game)):
            self._pending = ("translate", gid, setup)
            self.emit_prompt("Stale game processes",
                             f"Leftover processes of '{game.get('name', gid)}' are still "
                             f"running.\nA translation launch would stall behind them.",
                             ["Kill && Launch", "Launch anyway", "Cancel"])
            return "pending"
        return self._start_translate(gid, game, setup)

    def _start_translate(self, gid, game, setup):
        import datetime
        argv = commands.build_translate_command(game, gid, setup)
        env = QProcessEnvironment.systemEnvironment()
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.logCleared.emit()
        self._log_carry = ""
        mode = "setup (pick the text hook)" if setup else "filtered + translation"
        self.logAppended.emit(f"[{stamp}] {game.get('name', gid)} — {mode}")
        self.logAppended.emit(f"$ {' '.join(argv)}\n")
        self.proc = QProcess(self)
        self.proc.setProgram(argv[0])
        self.proc.setArguments(argv[1:])
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read_log)
        self.proc.finished.connect(self._finished)
        self.proc.start()
        if not self.proc.waitForStarted(10000):
            self.proc = None
            return "Failed to start the translation launcher."
        self._set_status(f"Running {game.get('name', '')} — {mode}…")
        self._set_running(True)
        self._running_gid = gid
        self._running_translate = True
        self.openTextbox(gid)
        if setup:
            # Let the picker attach once the bridge is up.
            self.setupLaunched.emit(gid)
        return ""

    @Slot(int)
    def resolvePrompt(self, idx):
        pending, self._pending = self._pending, None
        if pending is None:
            return
        kind, gid, extra = pending
        game = store.load_games().get(gid)
        if game is None:
            return
        if kind == "launch":
            if idx == 0:
                self.logAppended.emit("cleaned stray processes.")
                process.kill_strays(process.stray_token(game))
                err = self._start_game(gid, game, extra)
            elif idx == 1:
                err = self._start_game(gid, game, extra)
            else:
                return
            if err:
                self.logAppended.emit(err)
        elif kind == "translate":
            if idx < 0:
                return
            btn = idx
            if self._last_prompt_title == "Translation session live":
                if btn == 1:
                    self.openTextbox(gid)
                    return
                if btn != 0:
                    return
                self.logAppended.emit("stopping live session for relaunch…")
                self._set_status("Stopping live session…")
                process.stop_session(game["path"])
                self.logAppended.emit("stopped.")
            elif self._last_prompt_title == "Wedged translation session":
                if btn != 0:
                    return
                self.logAppended.emit("clearing wedged session…")
                self._set_status("Clearing wedged session…")
                process.stop_session(game["path"])
                self.logAppended.emit("cleared.")
            elif self._last_prompt_title == "Stale game processes":
                if btn == 0:
                    self.logAppended.emit("cleaned stray processes.")
                    process.kill_strays(process.stray_token(game))
                elif btn != 1:
                    return
            err = self._start_translate(gid, game, extra)
            if err:
                self.logAppended.emit(err)

    _last_prompt_title = ""

    def emit_prompt(self, title, text, buttons):
        self._last_prompt_title = title
        self.prompt.emit(title, text, json.dumps(buttons))

    @Slot(str, result=str)
    def previewCommand(self, gid):
        game = store.load_games().get(gid)
        if not game:
            return ""
        return " ".join(commands.build_command(game))

    @Slot(str, result=str)
    def openTextbox(self, gid):
        proc, msg = process.spawn_textbox(gid or None)
        if proc is not None:
            self.textbox_proc = proc
        self.logAppended.emit(msg)
        return msg

    @Slot()
    def stopGame(self):
        import signal as _sig
        if self.proc is not None:
            self.logAppended.emit("stopping…")
            self.proc.terminate()
        # Close the DeepL browser cleanly first (tabs + Browser.close), so it
        # never session-restores a pile of tabs; kill is the fallback.
        if process.close_translator_browser():
            self.logAppended.emit("translation browser closed.")
        if self.textbox_proc is not None and self.textbox_proc.poll() is None:
            self.logAppended.emit("stopping translation readout…")
            process.kill_textbox_group(self.textbox_proc, _sig.SIGTERM)
        QTimer.singleShot(3000, self._force_stop)

    def _force_stop(self):
        import signal as _sig
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            self.proc.kill()
        if self.proc is not None:
            gid = self._running_gid
            if gid:
                game = store.load_games().get(gid)
                if game and process.kill_strays(process.stray_token(game)):
                    self.logAppended.emit("cleaned stray processes.")
        if self.textbox_proc is not None:
            if self.textbox_proc.poll() is None:
                process.kill_textbox_group(self.textbox_proc, _sig.SIGKILL)
                self.logAppended.emit("translation readout stopped.")
            self.textbox_proc = None
        process.close_translator_browser()
        if process.kill_orphan_browsers():
            self.logAppended.emit("translator browser stopped.")

    def _read_log(self):
        if self.proc is None:
            return
        chunk = str(self.proc.readAllStandardOutput(), "utf-8", "replace")
        if not chunk:
            return
        # Buffer the trailing partial line so a warning split across reads is
        # still filtered; only complete lines are emitted.
        data = self._log_carry + chunk
        lines = data.split("\n")
        self._log_carry = lines.pop()
        kept = [ln for ln in lines if not _is_benign_log_line(ln)]
        if kept:
            self.logAppended.emit("\n".join(kept) + "\n")

    def _flush_log_carry(self):
        if self._log_carry:
            if not _is_benign_log_line(self._log_carry):
                self.logAppended.emit(self._log_carry)
            self._log_carry = ""

    def _finished(self, code, status):
        import subprocess
        self._flush_log_carry()
        self.logAppended.emit(f"\n[exited with code {code}]")
        self._set_status("Idle.")
        self._set_running(False)
        self.proc = None
        gid = self._running_gid
        self._running_gid = None
        translating, self._running_translate = self._running_translate, False
        if translating and gid:
            game = store.load_games().get(gid) or {}
            tr = game.get("translate") or {}
            # Hidden Setup writes no SavedHooks: only harvest when Textractor
            # was shown (debug) or a manual hook code exists.
            if game.get("path") and (tr.get("show_hooker") == "1"
                                     or (tr.get("hook_code") or "").strip()):
                try:
                    r = subprocess.run(
                        [sys.executable,
                         os.path.join(paths.TRANSLATE_DIR, "harvest-hooks.py"),
                         "--exe", game["path"], "--game", gid],
                        capture_output=True, text=True, timeout=30)
                    for line in (r.stdout + r.stderr).splitlines():
                        if line.strip():
                            self.logAppended.emit(line)
                except (OSError, subprocess.SubprocessError):
                    pass
            self._model.refresh()
            self.gamesChanged.emit()

    @Slot(str, result=str)
    def pickThread(self, gid):
        game = store.load_games().get(gid)
        if not game:
            return "Unknown game."
        if not process.translate_bridge_ok():
            return "nobridge"
        self._pick_cancel.set()
        self._pick_cancel = threading.Event()
        self._set_status("Searching for text threads… (advance the game text)")
        threading.Thread(target=self._sample_threads, args=(gid,),
                         daemon=True).start()
        return ""

    @Slot()
    def cancelPick(self):
        self._pick_cancel.set()

    def _sample_threads(self, gid):
        """Continuously watch the bridge and stream candidates until the
        dialog is accepted/cancelled (docs/translate.md)."""
        import time as _time
        cancel = self._pick_cancel
        game = store.load_games().get(gid, {})
        cur = ((game.get("translate") or {}).get("thread") or "").strip()
        buckets = {}
        sys.path.insert(0, paths.TRANSLATE_DIR)
        from hook_client import parse_thread, clean_ja
        import websocket

        state = {"emit": 0.0, "sig": None}

        def snapshot(force=False):
            if cancel.is_set() or not buckets:
                return
            sig = tuple(sorted((k, v["n"], v["last"]) for k, v in buckets.items()))
            now = _time.time()
            if sig == state["sig"] or (not force and now - state["emit"] < 0.75):
                return
            state["sig"], state["emit"] = sig, now
            out = [{"num": num, "name": name, "addr": addr, "n": b["n"],
                    "last": b["last"], "current": cur in (name, str(num))}
                   for (num, name, addr), b in sorted(buckets.items())]
            self.threadResults.emit(json.dumps({"threads": out}))

        while not cancel.is_set():
            try:
                ws = websocket.create_connection("ws://127.0.0.1:6677", timeout=15)
            except Exception as e:
                self.threadResults.emit(json.dumps({"error": str(e)}))
                cancel.wait(2.0)
                continue
            ws.settimeout(1.0)
            try:
                while not cancel.is_set():
                    try:
                        msg = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        snapshot()
                        continue
                    except Exception:
                        break
                    meta, text = parse_thread(msg)
                    if meta is None:
                        continue
                    ja = clean_ja(text)
                    if not ja:
                        continue
                    key = (meta["number"], meta["name"], meta["addr"])
                    b = buckets.setdefault(key, {"n": 0, "last": ""})
                    b["n"] += 1
                    b["last"] = ja[-120:]
                    snapshot(force=state["sig"] is None)
            finally:
                try:
                    ws.close()
                except Exception:
                    pass
        self._set_status("Idle.")

    @Slot(str, str)
    def saveThread(self, gid, thread):
        games = store.load_games()
        if gid in games:
            games[gid].setdefault("translate", {})["thread"] = thread
            store.save_games(games)
            self._model.refresh()
            self.gamesChanged.emit()

    @Slot(result=str)
    def loadSettings(self):
        return json.dumps(store.load_config())

    @Slot(str)
    def saveSettings(self, cfg_json):
        try:
            cfg = json.loads(cfg_json)
        except (ValueError, TypeError):
            return
        base = store.load_config()
        base.update(cfg)
        store.save_config(base)
        app = QGuiApplication.instance()
        if app is not None:
            apply_gui_font(app, getattr(app, "_base_font", app.font()))


def self_test(backend, model, window, warnings, qml_errors=None):
    assert model.rowCount() == len(store.load_games()), "model must mirror the library"
    assert qml_errors is not None and not qml_errors, \
        f"runtime QML errors: {qml_errors[:3]}"
    import tempfile
    _orig = paths.GAMES_JSON
    paths.GAMES_JSON = os.path.join(tempfile.mkdtemp(), "games.json")
    try:
        assert store.load_games() == {}
        gid = store.new_game(store.load_games(),
                             {"name": "Unit Test", "runner": "native",
                              "path": "/bin/true"})
        assert gid == "unit-test", f"new_game id: {gid}"
        assert "unit-test" in store.load_games(), "new_game must persist"
        store.remove_game(gid)
        assert store.load_games() == {}, "remove_game must persist"
    finally:
        paths.GAMES_JSON = _orig
    assert backend.previewCommand("__no_such_game__") == ""
    assert backend.validateGame("not json") != ""
    assert backend.validateGame(json.dumps({"name": "x", "runner": "proton", "path": "/nope"})) != ""
    assert backend.launchGame("__no_such_game__", False) != ""
    assert backend.translateGame("__no_such_game__", False) != ""
    assert backend.pickThread("__no_such_game__") != ""
    assert backend.gameDetails("__no_such_game__") == "Select a game."
    assert isinstance(backend.listVariants(), list) and backend.listVariants()
    assert isinstance(backend.listGpus(), list) and backend.listGpus()
    assert json.loads(backend.runnersJson()).get("proton")
    _protons = json.loads(backend.protonsJson())
    assert _protons and _protons[0]["value"] == "", \
        "proton list must start with the umu-managed option"
    assert all("wow64" in p for p in _protons), "proton entries need a wow64 flag"
    assert window is not None, "Main.qml must create a root window"
    assert window.findChild(QObject, "protonCombo") is not None, "proton combo must exist"
    assert window.findChild(QObject, "guiFontCombo") is not None, "GUI font combo must exist"
    if store.load_games():
        assert window.property("gid"), "first game must be auto-selected"
    else:
        assert window.property("gid") == "", "empty library must select nothing"
    assert not warnings, f"QML warnings: {warnings[:3]}"
    # Wizard edit path (binds every field from an existing game) must not
    # raise runtime QML errors.
    wizard = window.findChild(QObject, "wizardDlg")
    assert wizard is not None, "wizard dialog must exist"
    games = store.load_games()
    if games:
        gids = sorted(games)
        gid = next((g for g in gids if not (games[g].get("lang") or "")), gids[0])
        assert QMetaObject.invokeMethod(wizard, "start", Qt.DirectConnection,
                                        Q_ARG("QVariant", gid)), \
            "wizard.start must be invokable"
        QCoreApplication.processEvents()
        assert wizard.property("title"), "wizard must title itself for a game"
        assert not qml_errors, f"wizard edit raised QML errors: {qml_errors[:2]}"
        QMetaObject.invokeMethod(wizard, "close")
        QCoreApplication.processEvents()
    print("self-test: ALL OK")


def _diagnose(app, engine, model, backend):
    print("python:", sys.version.split()[0])
    try:
        from PySide6 import __version__ as pv
        from PySide6.QtCore import qVersion
        print("pyside:", pv, "qt:", qVersion())
    except Exception:
        pass
    print("app_dir:", APP_DIR)
    print("qml_dir:", os.path.join(APP_DIR, "qml"))
    print("platform:", QGuiApplication.platformName())
    try:
        pal = app.palette()
        print("style:", STYLE,
              "windowText:", pal.color(QPalette.ColorRole.WindowText).name(),
              "window:", pal.color(QPalette.ColorRole.Window).name(),
              "highlight:", pal.color(QPalette.ColorRole.Highlight).name())
    except Exception as e:
        print("style/palette: n/a", e)
    try:
        f = app.font()
        print("gui font:", f.family(), f.pointSize())
    except Exception as e:
        print("gui font: n/a", e)
    print("games:", model.rowCount())
    print("roots:", len(engine.rootObjects()))
    ctx = engine.rootContext()
    for name in ("backend", "gamesModel"):
        obj = ctx.contextProperty(name)
        valid = False
        try:
            valid = obj is not None and obj.property("objectName") is not None
        except Exception:
            valid = obj is not None
        print(f"contextProperty {name!r}: {type(obj).__name__} valid={valid}")
    roots = engine.rootObjects()
    if roots:
        win = roots[0]
        print("qt window size:", win.width(), "x", win.height(), "pos:", win.position())
        try:
            scr = win.screen()
            print("screen:", scr.name(), scr.size().width(), "x",
                  scr.size().height(), "dpr:", scr.devicePixelRatio())
        except Exception as e:
            print("screen: n/a", e)
    try:
        import subprocess
        out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True,
                             text=True, timeout=5).stdout
        for c in json.loads(out):
            if c.get("pid") == os.getpid() or "Migaki" in (c.get("title") or ""):
                print("hypr client:", repr(c.get("title")), "at", c.get("at"),
                      "size", c.get("size"), "scale", c.get("scale"),
                      "floating", c.get("floating"))
    except Exception as e:
        print("hyprctl: n/a", e)
    print("runtime qml errors:", len(QML_ERRORS))
    for e in QML_ERRORS[:5]:
        print("  ", e)


def main():
    # Ensure user-installed tools (~/.local/bin) resolve for every child we
    # spawn, even when launched from a desktop session without that on PATH.
    os.environ["PATH"] = paths.tool_path()
    app = QGuiApplication(sys.argv)
    app.setApplicationName("Migaki")
    # Match the .desktop entry (Icon=migaki) so the compositor shows our
    # icon on the window/taskbar, and set it directly as a fallback.
    app.setDesktopFileName("migaki-gui")
    _icon = os.path.join(REPO_ROOT, "assets", "icons", "png", "migaki-256.png")
    if os.path.isfile(_icon):
        app.setWindowIcon(QIcon(_icon))
    qInstallMessageHandler(_capture_qt_messages)
    apply_style()
    app._base_font = app.font()
    engine = QQmlApplicationEngine()
    app._engine = engine
    apply_gui_font(app, app._base_font)
    qml_warnings = []
    engine.warnings.connect(lambda w: qml_warnings.extend(str(x) for x in w))
    model = GamesModel(app)
    model.refresh()
    backend = GuiBackend(model, app)
    app._qml_objects = (model, backend)
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("gamesModel", model)
    engine.rootContext().setContextProperty("fontFamilies", QFontDatabase.families())
    engine.load(QUrl.fromLocalFile(os.path.join(APP_DIR, "qml", "Main.qml")))
    for _ in range(20):
        app.processEvents()
    diagnose = "--diagnose" in sys.argv[1:]
    if diagnose:
        _diagnose(app, engine, model, backend)
    if qml_warnings or QML_ERRORS:
        logp = _log_path()
        if logp:
            try:
                with open(logp, "a", encoding="utf-8") as f:
                    f.write(f"--- {__import__('datetime').datetime.now()}\n")
                    for e in list(qml_warnings) + list(QML_ERRORS):
                        f.write(str(e) + "\n")
            except OSError:
                pass
    if "--self-test" in sys.argv[1:]:
        roots = engine.rootObjects()
        try:
            self_test(backend, model, roots[0] if roots else None,
                      qml_warnings, QML_ERRORS)
        except AssertionError as e:
            print(f"self-test FAILED: {e}")
            for w in list(qml_warnings)[:5] + list(QML_ERRORS)[:5]:
                print("qml:", w)
            sys.exit(3)
        sys.exit(0)
    if diagnose:
        sys.exit(0)
    if "--screenshot" in sys.argv[1:]:
        from PySide6.QtQuick import QQuickWindow
        idx = sys.argv.index("--screenshot")
        out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "/tmp/migaki-gui.png"
        roots = engine.rootObjects()
        if roots:
            win = roots[0]
            win.setWidth(1100)
            win.setHeight(700)
            for _ in range(20):
                app.processEvents()
            img = win.grabWindow()
            print("screenshot:", out, "ok=" + str(img.save(out)))
        sys.exit(0)
    if not engine.rootObjects():
        sys.exit(1)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
