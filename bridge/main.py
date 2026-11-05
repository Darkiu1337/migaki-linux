#!/usr/bin/env python3
"""migaki-gui bridge: NDJSON RPC over stdin/stdout for the Rust frontend.

Every request runs on its own thread; a lock inside the backend guards
launch/stop/prompt state, while detect/path.pick deliberately run outside it
so log and status events keep flowing while a native dialog is open.
"""
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import paths  # noqa: E402

# ~/.local/bin must resolve for every child we spawn, even when launched from
# a desktop session whose PATH omits it.
os.environ["PATH"] = paths.tool_path()

from bridge import backend as backend_mod  # noqa: E402
from bridge import protocol, selftest  # noqa: E402


def handlers(be):
    def init(params):
        return be.snapshot(params.get("ui_pid"))

    return {
        "init": init,
        "games.refresh": lambda p: {"games": be.refresh_and_emit()},
        "game.details": lambda p: be.details(p.get("gid", "")),
        "game.data": lambda p: be.game_data(p.get("gid", "")),
        "game.validate": lambda p: be.validate(p.get("data") or {}),
        "game.save": lambda p: be.save(p.get("gid", ""), p.get("data") or {}),
        "game.remove": lambda p: be.remove(p.get("gid", "")),
        "game.preview": lambda p: be.preview(p.get("gid", "")),
        "game.launch": lambda p: be.launch(p.get("gid", ""),
                                           bool(p.get("unfiltered"))),
        "game.translate": lambda p: be.translate(p.get("gid", ""),
                                                 bool(p.get("setup"))),
        "game.stop": lambda p: be.stop(),
        "textbox.open": lambda p: be.textbox_open(p.get("gid", "")),
        "prompt.resolve": lambda p: be.resolve_prompt(int(p.get("index", -1))),
        "threads.pick": lambda p: be.threads_pick(p.get("gid", "")),
        "threads.cancel": lambda p: be.threads_cancel(),
        "threads.save": lambda p: be.threads_save(p.get("gid", ""),
                                                  p.get("thread", "")),
        "detect": lambda p: be.detect(p.get("path", "")),
        "reconcile": lambda p: be.reconcile(p.get("path", ""),
                                            p.get("runner", "proton")),
        "gpus.refresh": lambda p: be.gpus_refresh(),
        "settings.save": lambda p: be.save_settings(p.get("patch") or {}),
        "path.pick": lambda p: be.pick_path(p.get("target", "file"),
                                            p.get("runner", "proton")),
        "path.remember": lambda p: be.remember_dir(p.get("path", "")),
        "fonts.list": lambda p: {"fonts": be.list_fonts()},
        "shutdown": lambda p: be.shutdown(),
    }


def _run(writer, table, req_id, method, params):
    fn = table.get(method)
    if fn is None:
        writer.respond(req_id, error=f"Unknown method: {method}")
        return
    try:
        writer.respond(req_id, fn(params))
    except Exception as e:
        writer.respond(req_id, error=f"{type(e).__name__}: {e}")


def serve(writer, be):
    table = handlers(be)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        req_id = req.get("id")
        method = req.get("method") or ""
        params = req.get("params") or {}
        if method == "shutdown":
            # Reply first so the frontend can quit at once, then clean up.
            be_out = table["shutdown"](params)
            writer.respond(req_id, be_out)
            writer.flush()
            os._exit(0)
        threading.Thread(target=_run,
                         args=(writer, table, req_id, method, params),
                         daemon=True).start()


def run_self_test():
    be = backend_mod.Backend(lambda *a, **k: None)
    try:
        selftest.run(be)
    except AssertionError as e:
        print(f"self-test FAILED: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"self-test FAILED: {type(e).__name__}: {e}")
        return 1
    print("self-test: ALL OK")
    return 0


def main(argv):
    if "--self-test" in argv[1:]:
        return run_self_test()
    writer = protocol.Writer()
    be = backend_mod.Backend(writer.event)
    serve(writer, be)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
