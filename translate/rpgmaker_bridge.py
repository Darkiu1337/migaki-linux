#!/usr/bin/env python3
"""rpgmaker_bridge.py — native relay for the injected RPGMaker MV/MZ hook.

The normal NW.js build has no remote debugging, so the text is captured by an
injected page script (translate/rpgmaker_hook.js) which pushes tagged lines to
ws://127.0.0.1:<port>. This server relays those uplink lines to every other
client, so the textbox/picker consume them exactly like the Textractor/CDP
bridges. Dependency-free (translate/ws_bridge.py).

Runs until SIGTERM/SIGINT (the rpgmaker runner starts and stops it).
"""
import argparse
import signal
import sys
import threading
import time

from ws_bridge import WSBridgeServer


def main():
    ap = argparse.ArgumentParser(description="RPGMaker text-hook relay")
    ap.add_argument("--port", type=int, default=6677)
    ap.add_argument("--gameid", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    def on_text(text):
        if args.verbose:
            print("rpgmaker_bridge: line:", text[:80], file=sys.stderr, flush=True)

    server = WSBridgeServer(port=args.port, relay=True, on_text=on_text).start()
    print("rpgmaker_bridge: ws://127.0.0.1:%d (gameid=%s)"
          % (args.port, args.gameid or "-"), file=sys.stderr, flush=True)

    stop = threading.Event()

    def _sig(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    while not stop.is_set():
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
