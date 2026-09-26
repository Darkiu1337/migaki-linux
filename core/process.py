import json
import os
import re
import signal
import subprocess
import time

from . import paths, store, system


def stray_token(game):
    """pgrep token identifying this game's processes (bracketed by caller)."""
    if game.get("runner") == "rpgmaker":
        return "nw --ozone-platform"
    return os.path.basename(game.get("path", ""))


def find_strays(token):
    """PIDs matching token, self-excluding via bracket pattern. Never raises."""
    if not token:
        return []
    if re.search(r"\s", token):
        # Multi-word token (the rpgmaker NW.js argv): plain substring match,
        # self-excluded by bracket-wrapping the first char.
        pat = f"[{token[0]}]{token[1:]}"
    else:
        # Plain game basename: require a path separator before it. Without the
        # anchor, Textractor's own argv ("Textractor.exe /p<game>.exe") matches
        # and the GUI mistakes the lingering hooker for a running game.
        pat = r"[\\/]" + re.escape(token)
    try:
        out = subprocess.run(["pgrep", "-f", pat], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    me = os.getpid()
    return [int(p) for p in out.split() if p.isdigit() and int(p) != me]


def kill_strays(token):
    """TERM, grace wait, then KILL leftovers. Returns True if anything was found."""
    pids = find_strays(token)
    if not pids:
        return False
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(2)
    for pid in pids:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return True


def translate_bridge_ok():
    """True when something answers :6677 with a real ws handshake.
    Never probe with bare TCP: the stock bridge panics on non-handshakes
    (see docs/translate.md)."""
    try:
        import websocket
        ws = websocket.create_connection("ws://127.0.0.1:6677", timeout=3)
        ws.close()
        return True
    except Exception:
        return False


def textbox_pids():
    """PIDs of a running textbox backend (workers = owns the translator)."""
    try:
        out = subprocess.run(["pgrep", "-f", r"[t]extbox\.py --start-workers"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [p for p in (x.strip() for x in out.splitlines()) if p]


def translate_cdp_profile():
    """(full path, basename) of the isolated translator-browser profile.
    Never the real browser profile — automation always gets its own dir."""
    try:
        with open(os.path.join(paths.TRANSLATE_DIR, "config.json"), encoding="utf-8") as f:
            prof = (json.load(f) or {}).get("brave_profile", "")
    except (OSError, ValueError):
        prof = ""
    prof = os.path.expanduser(os.path.expandvars(
        prof or "~/.cache/vn-translate/brave-cdp-profile"))
    base = os.path.basename(prof.rstrip("/")) or "brave-cdp-profile"
    return prof, base


def textbox_brave_pattern():
    """pkill -f pattern matching ONLY translator browsers: the isolated
    profile marker in the cmdline, whatever the binary."""
    _, base = translate_cdp_profile()
    esc = "".join(("\\" + ch) if ch in ".+*?()[]{}^$|\\" else ch for ch in base)
    if esc and esc[0].isalnum():
        return f"user-data-dir=[^ ]*[{esc[0]}]{esc[1:]}"
    return f"user-data-dir=[^ ]*{esc}"


_BLOCKED_PROFILE_ROOTS = (
    "~/.config/BraveSoftware", "~/.config/brave", "~/.config/Brave-Browser",
    "~/.config/chromium", "~/.config/google-chrome",
    "~/.config/google-chrome-beta", "~/.config/microsoft-edge",
    "~/.config/vivaldi", "~/.config/opera",
)


def is_safe_automation_profile(path):
    """False for a real browser profile (or a bare home/config/cache root).
    Automation may only touch its own isolated --user-data-dir."""
    if not path:
        return False
    p = os.path.realpath(os.path.expanduser(path))
    home = os.path.expanduser("~")
    for blocked in _BLOCKED_PROFILE_ROOTS:
        rb = os.path.realpath(os.path.expanduser(blocked))
        if p == rb or p.startswith(rb + os.sep):
            return False
    for root in (home, os.path.join(home, ".config"), os.path.join(home, ".cache")):
        if p == os.path.realpath(root):
            return False
    return True


def translate_cdp_port():
    try:
        with open(os.path.join(paths.TRANSLATE_DIR, "config.json"), encoding="utf-8") as f:
            return int((json.load(f) or {}).get("debugport", 9222))
    except (OSError, ValueError, TypeError):
        return 9222


def purge_browser_session(profile=None):
    """Drop the isolated browser's session files so a fresh launch can never
    session-restore a pile of DeepL tabs (docs/translate.md). Guarded."""
    prof = profile or translate_cdp_profile()[0]
    if not is_safe_automation_profile(prof):
        return False
    import shutil
    default = os.path.join(os.path.expanduser(prof), "Default")
    removed = False
    for name in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        f = os.path.join(default, name)
        if os.path.isfile(f):
            try:
                os.remove(f)
                removed = True
            except OSError:
                pass
    sessions = os.path.join(default, "Sessions")
    if os.path.isdir(sessions):
        try:
            shutil.rmtree(sessions)
            removed = True
        except OSError:
            pass
    return removed


def _browser_is_ours(profile, port):
    """True only when a live process was started with BOTH our isolated
    --user-data-dir and the CDP port (so a user's own browser is never hit)."""
    rp = os.path.realpath(os.path.expanduser(profile))
    user_arg = f"--user-data-dir={rp}"
    port_arg = f"--remote-debugging-port={port}"
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        if user_arg in cmd and port_arg in cmd:
            return True
    return False


def close_translator_browser(timeout=4):
    """Close the translator browser's tabs and exit it cleanly (no session
    restore), never touching a browser that isn't ours. Never raises."""
    port = translate_cdp_port()
    profile = translate_cdp_profile()[0]
    if not is_safe_automation_profile(profile) or not _browser_is_ours(profile, port):
        return False
    try:
        import requests
        import websocket
        ver = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=2).json()
        targets = requests.get(f"http://127.0.0.1:{port}/json/list", timeout=2).json()
        if not _browser_is_ours(profile, port):  # recheck after the probes
            return False
        ws = websocket.create_connection(ver["webSocketDebuggerUrl"], timeout=3)
        for t in targets:
            if t.get("type") == "page" and t.get("id"):
                ws.send(json.dumps({"id": 1, "method": "Target.closeTarget",
                                    "params": {"targetId": t["id"]}}))
                try:
                    ws.recv()
                except Exception:
                    pass
        ws.send(json.dumps({"id": 2, "method": "Browser.close"}))
        try:
            ws.recv()
        except Exception:
            pass
        ws.close()
        return True
    except Exception:
        return False


def kill_textbox_group(proc, sig):
    """Signal the textbox process group (backend + translator browser it
    spawned). Returns True if a live group was signaled."""
    try:
        if proc is None or proc.poll() is not None:
            return False
        os.killpg(os.getpgid(proc.pid), sig)
        return True
    except (OSError, ProcessLookupError):
        return False


def kill_orphan_browsers():
    """Orphaned translator browsers (backend died without cleanup): only the
    isolated CDP profile ever matches — real browsers are safe. Returns True
    when a kill landed."""
    try:
        r = subprocess.run(["pkill", "-f", textbox_brave_pattern()],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def translate_wedge_pids(game_base=""):
    """PIDs of wedged translate containers: umu-run ... hook .vbs older than
    ~3 min while no game/hooker process lives and the bridge is down.
    That's the wineserver -w stall signature (see docs/translate.md)."""
    try:
        out = subprocess.run(["ps", "-eo", "pid,etimes,args"], capture_output=True,
                             text=True, timeout=10).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    game_alive = False
    old_launchers = []
    gb = os.path.basename(game_base or "").lower()
    for line in out:
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        pid, etime, args = parts
        if not pid.isdigit():
            continue
        if ("umu-run" in args and "hook" in args and ".vbs" in args
                and "ps -eo" not in args):
            try:
                if int(etime) > 180:
                    old_launchers.append(int(pid))
            except ValueError:
                pass
        low = args.lower()
        if (("textractor.exe" in low or (gb and gb in low))
                and "umu-run" not in low and "ps -eo" not in args):
            game_alive = True
    if old_launchers and not game_alive and not translate_bridge_ok():
        return old_launchers
    return []


def stop_session(game_path, timeout=90, runner="proton"):
    """End a translation session: game hooks, textbox backend and its browser
    (a backend left running keeps translating and re-shows). The rpgmaker
    runner also drops the injected page hook and the :6677 relay."""
    try:
        if runner == "rpgmaker":
            gamedir = game_path if os.path.isdir(game_path) else os.path.dirname(game_path)
            subprocess.run([os.path.join(paths.SCRIPTS_DIR, "rpgmaker-migaki.sh"),
                            "--stop", "--gamepath", gamedir], timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run([os.path.join(paths.TRANSLATE_DIR, "vn-launch.sh"),
                            "--stop-exe", game_path], timeout=timeout,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def spawn_textbox(gid=None):
    """Start the textbox backend (single instance, stderr kept). Returns
    (proc, message). proc is None on failure/refusal."""
    argv = [os.path.join(paths.TRANSLATE_DIR, paths.TEXTBOX_PROG), "--start-workers"]
    env = dict(os.environ)
    show_browser = False
    try:
        game = store.load_games().get(gid or "")
        tr = (game or {}).get("translate") or {}
        if gid:
            # Live-follow this game's stored thread (picker takes effect
            # without restarting the session).
            argv += ["--gameid", gid]
        # Tyrano/Electron and RPGMaker MV/MZ use their hook's synthetic,
        # pre-selected thread; a stale Textractor thread must not mask it.
        engine = system.translate_engine((game or {}).get("path", ""))
        thread = "" if engine in ("tyrano", "rpgmaker") else (tr.get("thread") or "").strip()
        if thread:
            argv += ["--thread", thread]
        show_browser = tr.get("show_browser") == "1"
    except Exception:
        pass
    # Per-game: whether the DeepL browser window is visible (hidden = headless).
    env["VN_BROWSER_HIDDEN"] = "0" if show_browser else "1"
    if textbox_pids():
        return None, "textbox: already running (one instance only)."
    try:
        logdir = os.path.expanduser("~/.cache/migaki")
        os.makedirs(logdir, exist_ok=True)
        logf = open(os.path.join(logdir, "textbox.log"), "ab", buffering=0)
    except OSError:
        logf = subprocess.DEVNULL
    try:
        proc = subprocess.Popen(argv, stdout=logf, stderr=logf,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                env=env)
        return proc, "textbox: started (stderr -> ~/.cache/migaki/textbox.log)"
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"textbox: could not open ({e})"
