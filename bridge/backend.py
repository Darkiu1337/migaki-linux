"""Qt-free backend for the gpui frontend.

Successor to the retired PySide6 `GuiBackend`: the transport is NDJSON events
instead of Qt signals, and the same user-visible strings, status, prompt and
log lines are kept. All launch/library/process logic stays in core/, which the
TUI shares.
"""
import datetime
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from core import commands, icons, library, paths, process, store, system


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


class Backend:
    """One process-wide backend; every RPC handler runs on the backend."""

    def __init__(self, emit, ui_pid=None):
        self._emit = emit
        self.ui_pid = ui_pid
        self._lock = threading.RLock()
        self._status = "Idle."
        self._bridge_up = False
        self.proc = None
        self.textbox_proc = None
        self._running = False
        self._running_gid = None
        self._running_translate = False
        self._pending = None
        self._pick_cancel = threading.Event()
        self._gpu_warming = False
        self._gpu_fp = system.gpu_fingerprint()
        # Load the cached list (instant, no vulkaninfo at startup); a cold or
        # stale cache warms lazily on the first list.
        self._gpus, self._gpu_stale = system.gpu_cache(fingerprint=self._gpu_fp)
        self._display_vendor = None
        self._present_vendors = None
        self._icon_pending = set()
        self._icon_attempted = set()
        self._icon_wanted = set()
        self._icon_flush_scheduled = False
        self._bridge_stop = threading.Event()
        self._poll_bridge()
        threading.Thread(target=self._poll_bridge_loop, daemon=True).start()

    # -- protocol helpers ---------------------------------------------------

    def _log(self, text):
        self._emit("log", text=text)

    def _set_status(self, text):
        if text != self._status:
            self._status = text
            self._emit("status", text=text)

    def _set_running(self, value):
        if value != self._running:
            self._running = value
            self._emit("running", gid=self._running_gid if value else None)

    def _emit_prompt(self, kind, title, text, buttons):
        self._emit("prompt", kind=kind, title=title, text=text, buttons=buttons)

    # -- bridge poll --------------------------------------------------------

    def _poll_bridge(self):
        try:
            up = process.translate_bridge_ok()
        except Exception:
            up = False
        if up != self._bridge_up:
            self._bridge_up = up
            self._emit("bridge", up=up)

    def _poll_bridge_loop(self):
        while not self._bridge_stop.wait(3.0):
            self._poll_bridge()

    # -- snapshot -----------------------------------------------------------

    def snapshot(self, ui_pid=None):
        if ui_pid:
            self.ui_pid = ui_pid
        variants = system.list_variants() or ["L"]
        return {
            "games": self.refresh_games(),
            "status": self._status,
            "bridge_up": self._bridge_up,
            "running": self._running_gid if self._running else None,
            "variants": variants,
            "variant_notes": {v: system.variant_note(v) for v in variants},
            "gpus": self.list_gpus(),
            "display_vendors": self.present_vendors(),
            "locales": ["System default"] + list(paths.LOCALES),
            "protons": system.list_protons(),
            "runners": dict(paths.RUNNERS),
            "settings": store.load_config(),
            "fonts": self.list_fonts(),
            "last_dir": self.last_dir(),
        }

    # -- library ------------------------------------------------------------

    def refresh_games(self):
        """Rows for the list, sorted by name; uncached icons resolve in the
        background (icoextract must never block the window)."""
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
                "info": f"{g.get('runner', '?')}, {g.get('variant', '?')}"
                        f"{', +translate' if tr else ''}",
                "icon": ip or "",
            })
        self._want_icons(missing)
        return rows

    def refresh_and_emit(self):
        rows = self.refresh_games()
        self._emit("games", games=rows)
        return rows

    def _want_icons(self, gids):
        """Queue icons but defer the worker past the first paint so icoextract
        never competes with the window map."""
        with self._lock:
            self._icon_wanted.update(
                g for g in gids if g and g not in self._icon_attempted)
            if not self._icon_wanted or self._icon_flush_scheduled:
                return
            self._icon_flush_scheduled = True
        threading.Timer(1.2, self._flush_icons).start()

    def _flush_icons(self):
        with self._lock:
            self._icon_flush_scheduled = False
            gids = list(self._icon_wanted)
            self._icon_wanted.clear()
        todo = [g for g in gids
                if g and g not in self._icon_pending
                and g not in self._icon_attempted]
        if not todo:
            return
        with self._lock:
            self._icon_pending.update(todo)
        threading.Thread(target=self._icon_worker, args=(todo,), daemon=True).start()

    def _icon_worker(self, gids):
        games = store.load_games()
        for gid in gids:
            with self._lock:
                self._icon_pending.discard(gid)
                self._icon_attempted.add(gid)
            g = games.get(gid) or {}
            try:
                path = icons.resolve_icon(g.get("runner", ""), g.get("path", ""), gid)
            except Exception:
                path = None
            if path:
                self._emit("icons", items={gid: path})

    def details(self, gid):
        g = store.load_games().get(gid, {})
        if not g:
            return {"name": "", "lines": ["Select a game."]}
        tr = g.get("translate", {})
        lines = [
            f"Runner: {g.get('runner', '?')}   Variant: {g.get('variant', '?')}",
            f"GPU: {g.get('gpu', '?')}   FPS cap: {g.get('fps', '?')}   "
            f"Overlay: {'on' if g.get('hud') == '1' else 'off'}",
        ]
        if g.get("runner") == "proton":
            lines.append(f"Prefix: {g.get('prefix_mode', 'shared')}")
        if g.get("lang"):
            lines.append(f"Language: {g.get('lang')}")
        if tr.get("enabled") == "1":
            src = (tr.get("hook_code") or "").strip()
            thread = (tr.get("thread") or "").strip()
            if src and thread:
                src = f"{src}, thread {thread}"
            elif thread:
                src = f"thread {thread}"
            lines.append(f"Translation: on ({src or 'hook auto-detect'})")
        lines.append(f"Path: {g.get('path', '?')}")
        return {"name": g.get("name", gid), "lines": lines}

    def game_data(self, gid):
        return store.load_games().get(gid, {})

    def validate(self, data):
        try:
            return {"error": library.validate_entry(data) or ""}
        except (ValueError, TypeError):
            return {"error": "Invalid game data."}

    def save(self, gid, data):
        if not isinstance(data, dict):
            return {"error": "Invalid game data."}
        reason = library.validate_entry(data)
        if reason:
            return {"error": reason}
        games = store.load_games()
        if gid:
            prev = games.get(gid, {})
            games[gid] = library.normalize_edit(data, prev)
            store.save_games(games)
        else:
            gid = store.new_game(games, data)
        self.refresh_and_emit()
        return {"gid": gid}

    def remove(self, gid):
        store.remove_game(gid)
        self.refresh_and_emit()
        return {}

    def preview(self, gid):
        game = store.load_games().get(gid)
        if not game:
            return {"command": ""}
        return {"command": " ".join(commands.build_command(game, gid))}

    # -- launching ----------------------------------------------------------

    def launch(self, gid, unfiltered=False):
        game = store.load_games().get(gid)
        if not game:
            return {"result": "error", "message": "Unknown game."}
        if not os.path.exists(game.get("path", "")):
            return {"result": "error",
                    "message": f"Path no longer exists:\n{game['path']}"}
        with self._lock:
            if self.proc is not None:
                return {"result": "error", "message": "A game is already running."}
            if process.find_strays(process.stray_token(game)):
                mode = "unfiltered" if unfiltered else "filtered"
                self._pending = ("launch", gid, unfiltered)
                self._emit_prompt(
                    "stale_launch", "Stale game processes",
                    f"Leftover processes of '{game.get('name', gid)}' are still "
                    f"running.\nLaunching {mode} now would attach to them "
                    f"instead of starting fresh.",
                    ["Kill && Launch", "Launch anyway", "Cancel"])
                return {"result": "pending", "message": ""}
        return self._start_game(gid, game, unfiltered)

    def _spawn(self, argv, env=None):
        """Runner process with merged output, own session."""
        return subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace",
            start_new_session=True, env=env)

    def _start_game(self, gid, game, unfiltered):
        argv = commands.build_command(game, gid)
        env = dict(os.environ)
        if unfiltered:
            for var in paths.VKBASALT_ENV_VARS:
                env.pop(var, None)
            env["DISABLE_VKBASALT"] = "1"
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        mode = "unfiltered A/B" if unfiltered else f"filtered ({game.get('variant', '')})"
        self._emit("log_clear")
        self._log(f"[{stamp}] {game.get('name', gid)} — {mode}")
        self._log(f"$ {' '.join(argv)}\n")
        try:
            proc = self._spawn(argv, env)
        except (OSError, subprocess.SubprocessError):
            return {"result": "error",
                    "message": "Failed to start the launcher script."}
        with self._lock:
            self.proc = proc
            self._running_gid = gid
            self._running_translate = False
        self._watch(proc, gid, False)
        self._set_status(f"Running {game.get('name', '')} — {mode}…")
        self._set_running(True)
        return {"result": "ok", "message": ""}

    def _watch(self, proc, gid, translating):
        def reader():
            try:
                for line in proc.stdout:
                    if _is_benign_log_line(line):
                        continue
                    self._log(line if line.endswith("\n") else line + "\n")
            except (OSError, ValueError):
                pass
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        def waiter():
            try:
                code = proc.wait()
            except (OSError, ValueError):
                code = -1
            thread.join(2.0)
            self._finished(code, gid, translating)
        threading.Thread(target=waiter, daemon=True).start()

    def translate(self, gid, setup=False):
        game = store.load_games().get(gid)
        if not game:
            return {"result": "error", "message": "Unknown game."}
        if game.get("runner") not in ("proton", "rpgmaker"):
            return {"result": "error",
                    "message": "Translation needs a Proton/Windows or RPGMaker MV/MZ game."}
        tr = game.get("translate") or {}
        if tr.get("enabled") != "1":
            return {"result": "error",
                    "message": "Enable translation for this game first (Edit…)."}
        # A hook is "saved" when either a hook code or a picked thread exists.
        # Tyrano (CDP) and RPGMaker MV/MZ (injected page hook) auto-select a
        # synthetic thread, so don't force the Textractor Setup for them.
        is_auto_hook = system.translate_engine(game.get("path", "")) in ("tyrano", "rpgmaker")
        hook_saved = is_auto_hook or bool((tr.get("hook_code") or "").strip()
                                          or (tr.get("thread") or "").strip())
        if not setup and not hook_saved:
            self._log("No hook recorded for this game — starting Setup Text Hooker.")
            setup = True
        if not os.path.exists(game.get("path", "")):
            return {"result": "error",
                    "message": f"Path no longer exists:\n{game['path']}"}
        with self._lock:
            if self.proc is not None:
                return {"result": "error", "message": "A game is already running."}
            if process.translate_bridge_ok():
                self._pending = ("translate", gid, setup)
                self._emit_prompt(
                    "translate_live", "Translation session live",
                    "A translation session is already running.\n"
                    "Replace it with a fresh launch, or open the Textbox?",
                    ["Stop && Launch new", "Open Textbox", "Cancel"])
                return {"result": "pending", "message": ""}
            if process.translate_wedge_pids(game.get("path", "")):
                self._pending = ("translate", gid, setup)
                self._emit_prompt(
                    "translate_wedged", "Wedged translation session",
                    "A previous translation container is stuck (no game running, "
                    "prefix held).\nNew launches stall behind it until cleared.",
                    ["Clear && Launch", "Cancel"])
                return {"result": "pending", "message": ""}
            if process.find_strays(process.stray_token(game)):
                self._pending = ("translate", gid, setup)
                self._emit_prompt(
                    "stale_translate", "Stale game processes",
                    f"Leftover processes of '{game.get('name', gid)}' are still "
                    f"running.\nA translation launch would stall behind them.",
                    ["Kill && Launch", "Launch anyway", "Cancel"])
                return {"result": "pending", "message": ""}
        return self._start_translate(gid, game, setup)

    def _start_translate(self, gid, game, setup):
        argv = commands.build_translate_command(game, gid, setup)
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self._emit("log_clear")
        mode = "setup (pick the text hook)" if setup else "filtered + translation"
        self._log(f"[{stamp}] {game.get('name', gid)} — {mode}")
        self._log(f"$ {' '.join(argv)}\n")
        try:
            proc = self._spawn(argv)
        except (OSError, subprocess.SubprocessError):
            return {"result": "error",
                    "message": "Failed to start the translation launcher."}
        with self._lock:
            self.proc = proc
            self._running_gid = gid
            self._running_translate = True
        self._watch(proc, gid, True)
        self._set_status(f"Running {game.get('name', '')} — {mode}…")
        self._set_running(True)
        self.textbox_open(gid)
        if setup:
            self._emit("setup_launched", gid=gid)
        return {"result": "ok", "message": ""}

    def resolve_prompt(self, index):
        """Button index of the prompt that was shown (-1 = cancelled)."""
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return {}
        kind, gid, extra = pending
        game = store.load_games().get(gid)
        if game is None:
            return {}
        if kind == "stale_launch":
            if index == 0:
                self._log("cleaned stray processes.")
                process.kill_strays(process.stray_token(game))
                res = self._start_game(gid, game, extra)
            elif index == 1:
                res = self._start_game(gid, game, extra)
            else:
                return {}
            if res["message"]:
                self._log(res["message"])
            return {}
        if kind == "translate_live":
            if index == 1:
                self.textbox_open(gid)
                return {}
            if index != 0:
                return {}
            self._log("stopping live session for relaunch…")
            self._set_status("Stopping live session…")
            process.stop_session(game["path"], runner=game.get("runner", "proton"))
            self._log("stopped.")
        elif kind == "translate_wedged":
            if index != 0:
                return {}
            self._log("clearing wedged session…")
            self._set_status("Clearing wedged session…")
            process.stop_session(game["path"], runner=game.get("runner", "proton"))
            self._log("cleared.")
        elif kind == "stale_translate":
            if index == 0:
                self._log("cleaned stray processes.")
                process.kill_strays(process.stray_token(game))
            elif index != 1:
                return {}
        else:
            return {}
        res = self._start_translate(gid, game, extra)
        if res["message"]:
            self._log(res["message"])
        return {}

    def textbox_open(self, gid):
        proc, msg = process.spawn_textbox(gid or None)
        if proc is not None:
            self.textbox_proc = proc
        self._log(msg)
        return {"message": msg}

    def stop(self):
        with self._lock:
            proc = self.proc
            textbox = self.textbox_proc
        if proc is not None:
            self._log("stopping…")
            try:
                proc.terminate()
            except OSError:
                pass
        # Close the DeepL browser cleanly first (tabs + Browser.close), so it
        # never session-restores a pile of tabs; kill is the fallback.
        if process.close_translator_browser():
            self._log("translation browser closed.")
        if textbox is not None and textbox.poll() is None:
            self._log("stopping translation readout…")
            process.kill_textbox_group(textbox, signal.SIGTERM)
        threading.Timer(3.0, self._force_stop).start()
        return {}

    def _force_stop(self):
        with self._lock:
            proc = self.proc
            gid = self._running_gid
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if proc is not None and gid:
            game = store.load_games().get(gid)
            if game and process.kill_strays(process.stray_token(game)):
                self._log("cleaned stray processes.")
            if game and game.get("runner") == "rpgmaker":
                # Drop the injected hook + the :6677 relay too.
                process.stop_session(game["path"], runner="rpgmaker")
        textbox = self.textbox_proc
        if textbox is not None:
            if textbox.poll() is None:
                process.kill_textbox_group(textbox, signal.SIGKILL)
                self._log("translation readout stopped.")
            self.textbox_proc = None
        process.close_translator_browser()
        if process.kill_orphan_browsers():
            self._log("translator browser stopped.")

    def _finished(self, code, gid, translating):
        self._log(f"\n[exited with code {code}]")
        self._set_status("Idle.")
        with self._lock:
            self.proc = None
        self._set_running(False)
        with self._lock:
            self._running_gid = None
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
                            self._log(line + "\n")
                except (OSError, subprocess.SubprocessError):
                    pass
            self.refresh_and_emit()

    # -- threads ------------------------------------------------------------

    def threads_pick(self, gid):
        game = store.load_games().get(gid)
        if not game:
            return {"result": "Unknown game."}
        if not process.translate_bridge_ok():
            return {"result": "nobridge"}
        self._pick_cancel.set()
        self._pick_cancel = threading.Event()
        self._set_status("Searching for text threads… (advance the game text)")
        threading.Thread(target=self._sample_threads, args=(gid,), daemon=True).start()
        return {"result": ""}

    def threads_cancel(self):
        self._pick_cancel.set()
        return {}

    def _sample_threads(self, gid):
        """Continuously watch the bridge and stream candidates until the
        dialog is accepted/cancelled (docs/translate.md)."""
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
            now = time.time()
            if sig == state["sig"] or (not force and now - state["emit"] < 0.75):
                return
            state["sig"], state["emit"] = sig, now
            out = [{"num": num, "name": name, "addr": addr, "n": b["n"],
                    "last": b["last"], "current": cur in (name, str(num))}
                   for (num, name, addr), b in sorted(buckets.items())]
            self._emit("threads", data={"threads": out})

        while not cancel.is_set():
            try:
                ws = websocket.create_connection("ws://127.0.0.1:6677", timeout=15)
            except Exception as e:
                self._emit("threads", data={"error": str(e)})
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

    def threads_save(self, gid, thread):
        games = store.load_games()
        if gid in games:
            games[gid].setdefault("translate", {})["thread"] = thread
            store.save_games(games)
            self.refresh_and_emit()
        return {}

    # -- system / settings --------------------------------------------------

    def detect(self, path):
        p = path.strip()
        res = system.detect(p)
        if res is None:
            return {"detected": None}
        # 6th field: the concrete launch target (the .sh for a native Ren'Py
        # distro, the folder for rpgmaker, the main .exe for Windows).
        target = system.launch_target(p) if p else ""
        engine, runner, confidence, root, detail = res
        return {"detected": {"engine": engine, "runner": runner,
                             "confidence": confidence, "root": root,
                             "detail": detail, "target": target}}

    def reconcile(self, path, runner):
        return system.reconcile(path.strip(), runner)

    def list_gpus(self):
        # Instant: cached list, or the fallback until the background warm
        # finishes (then the gpus event lets the wizard refill the combo).
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

    def gpus_refresh(self):
        """Force a fresh GPU probe (Settings > Refresh GPUs)."""
        self._gpu_stale = True
        self.warm_gpus()
        return {}

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
        self._emit("gpus", items=self._gpus or [])

    def present_vendors(self):
        """Vendors that can present a window (own an active output or are the
        GLX renderer). Cached: the bash probe runs once per session."""
        if self._present_vendors is None:
            self._present_vendors = system.present_gpu_vendors()
        return self._present_vendors

    def gpu_vendor(self, name):
        return commands.gpu_vendor(name)

    def display_vendor(self):
        if self._display_vendor is None:
            self._display_vendor = system.display_gpu_vendor()
        return self._display_vendor

    def save_settings(self, patch):
        base = store.load_config()
        base.update(patch)
        store.save_config(base)
        return {"settings": base}

    def list_fonts(self):
        """Font families (names only). `fc-list ": family"` prints
        `file: family:style=...` lines, so ask for the family element."""
        try:
            out = subprocess.run(["fc-list", "--format=%{family}\\n"],
                                 capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        fams = set()
        for line in out.splitlines():
            for fam in line.split(","):
                fam = fam.strip()
                if fam and os.path.isabs(fam):
                    fam = fam.rsplit("/", 1)[-1]
                if fam:
                    fams.add(fam)
        return sorted(fams)

    def last_dir(self):
        d = store.load_config().get("gui.last_dir", os.path.expanduser("~"))
        return d if os.path.isdir(d) else os.path.expanduser("~")

    def remember_dir(self, path):
        cfg = store.load_config()
        cfg["gui.last_dir"] = path if os.path.isdir(path) else os.path.dirname(path)
        store.save_config(cfg)
        return {}

    def _picker_argv(self, target, runner="proton"):
        """Native picker argv: KDE's kdialog first, zenity fallback, None when
        neither exists. Proton games default to a .exe filter; native Linux
        executables show all files."""
        start = self.last_dir()
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

    def pick_path(self, target, runner="proton"):
        """Open the desktop's native file/folder picker. Runs outside the
        backend lock so log and status events keep flowing."""
        argv = self._picker_argv(target, runner)
        if not argv:
            return {"path": None, "unsupported": True}
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.SubprocessError):
            return {"path": None, "unsupported": False}
        lines = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
        path = lines[0] if lines else ""
        if path:
            self.remember_dir(path)
        self._emit("picked", target=target, path=path)
        return {"path": path or None, "unsupported": False}

    # -- teardown -----------------------------------------------------------

    def shutdown(self):
        """Force-clean a running runner; the textbox and the DeepL browser are
        left running, exactly as the Qt QProcess teardown did."""
        self._bridge_stop.set()
        with self._lock:
            proc, self.proc = self.proc, None
            gid = self._running_gid
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except OSError:
                pass
            game = store.load_games().get(gid or "")
            if game:
                process.kill_strays(process.stray_token(game))
        return {}
