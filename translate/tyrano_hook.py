#!/usr/bin/env python3
"""tyrano_hook.py — TyranoScript/Electron text hook over the Chrome DevTools
Protocol, serving the same ws://127.0.0.1:6677 bridge the Textractor fork does.

Textractor cannot hook Chromium: TyranoScript draws dialogue in the DOM, not
through GDI, so no Textractor hook ever fires for these titles. This attaches
to the game's page target, installs TyranoScript's own `tag-text-message`
listener (fires once per text tag with the raw source line), and broadcasts
each line tagged with a synthetic thread

    ~#1*~5452414E~Tyrano~<text>

so hook_client.py / textbox.py / the GUI picker consume it unchanged.

The hook is native (Linux); it launches no Wine process. It exits when the
game's CDP endpoint has been gone for a while after having been up.
"""
import argparse
import json
import re
import sys
import threading
import time
import urllib.request

import websocket

from ws_bridge import WSBridgeServer

THREAD_NUM = 1
THREAD_ADDR = "5452414E"  # ASCII "TRAN"
THREAD_NAME = "Tyrano"

# The game page lives in app.asar; prefer it over DevTools/blank pages.
_PAGE_HINTS = ("app.asar", "index.html")

# TyranoScript inline control markers. Dialogue lines carry a trailing [p];
# [l]/[r]/[cm] appear in other titles. Real dialogue in the reference game has
# no other markup, but strip defensively so a stray tag never reaches DeepL.
_MARKERS = re.compile(r"\[(?:p|l|r|cm|er|page|clear|nowait|auto|ws)\]", re.I)
_ANY_TAG = re.compile(r"\[[^\]]*\]")

BOOTSTRAP = r"""
(() => {
  try {
    if (window.__migakiTyrano) return 'already';
    const install = () => {
      const kag = (window.tyrano && tyrano.plugin && tyrano.plugin.kag) || null;
      if (!kag || typeof kag.on !== 'function') return false;
      const send = (t) => { try { migakiSend(String(t == null ? '' : t)); } catch (e) {} };
      kag.on('tag-text-message', (e) => { send(e && e.target ? e.target.val : ''); });
      try { send(kag.stat && kag.stat.current_message_str); } catch (e) {}
      window.__migakiTyrano = true;
      return true;
    };
    if (install()) return 'ok';
    // The page is loaded before TyranoScript boots; poll until it is ready.
    if (!window.__migakiPoll) {
      window.__migakiPoll = setInterval(() => {
        if (install() && window.__migakiPoll) {
          clearInterval(window.__migakiPoll);
          window.__migakiPoll = null;
        }
      }, 500);
    }
    return 'pending';
  } catch (e) { return 'err:' + e; }
})()
"""


def log(*a):
    print("tyrano_hook:", *a, file=sys.stderr, flush=True)


def clean_text(raw):
    if not raw:
        return ""
    t = _MARKERS.sub(" ", raw)
    t = _ANY_TAG.sub("", t)
    return " ".join(t.split())


def find_page(port):
    targets = json.load(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/list" % port, timeout=3))
    pages = [t for t in targets
             if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    for t in pages:
        url = t.get("url") or ""
        if any(h in url for h in _PAGE_HINTS):
            return t
    return pages[0] if pages else None


class CDPHook:
    def __init__(self, port, on_line, verbose=False):
        self.port = port
        self.on_line = on_line
        self.verbose = verbose
        self._id = 0

    def _send(self, ws, method, params):
        self._id += 1
        ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        return self._id

    def _connect(self):
        page = find_page(self.port)
        if page is None:
            raise RuntimeError("no page target")
        ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                         timeout=5, suppress_origin=True)
        ws.settimeout(1.0)
        self._send(ws, "Runtime.enable", {})
        self._send(ws, "Runtime.addBinding", {"name": "migakiSend"})
        self._send(ws, "Runtime.evaluate",
                   {"expression": BOOTSTRAP, "returnByValue": True})
        if self.verbose:
            log("attached to", page.get("url"))
        return ws

    def run(self, stop):
        """Block until stopped or the game's CDP endpoint stays gone."""
        connected_once = False
        misses = 0
        while not stop.is_set():
            limit = 15 if connected_once else 120
            if misses >= limit:
                log("CDP gone for %ds — game closed, exiting" % limit)
                return
            try:
                ws = self._connect()
            except Exception as e:
                misses += 1
                if self.verbose and misses % 5 == 0:
                    log("waiting for CDP: %s" % e)
                time.sleep(1)
                continue
            connected_once = True
            misses = 0
            try:
                while not stop.is_set():
                    try:
                        msg = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
                    if not msg:
                        break
                    try:
                        obj = json.loads(msg)
                    except ValueError:
                        continue
                    method = obj.get("method")
                    if method == "Runtime.bindingCalled":
                        payload = obj.get("params", {}).get("payload", "")
                        self.on_line(clean_text(payload))
                    elif method == "Runtime.executionContextCreated":
                        # Re-install after a navigation/context swap.
                        self._send(ws, "Runtime.evaluate",
                                   {"expression": BOOTSTRAP, "returnByValue": True})
            except Exception as e:
                if self.verbose:
                    log("cdp session error:", e)
            finally:
                try:
                    ws.close()
                except Exception:
                    pass
            misses = 1
            time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description="TyranoScript/Electron CDP text hook")
    ap.add_argument("--cdp-port", type=int, default=9223)
    ap.add_argument("--bridge-port", type=int, default=6677)
    ap.add_argument("--gameid", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    server = WSBridgeServer(port=args.bridge_port).start()
    log("bridge on ws://127.0.0.1:%d (cdp :%d%s)"
        % (args.bridge_port, args.cdp_port,
           ", gameid=" + args.gameid if args.gameid else ""))

    last = {"text": ""}
    lock = threading.Lock()

    def emit(text):
        if not text:
            return
        with lock:
            if text == last["text"]:
                return
            last["text"] = text
        server.broadcast("~#%d*~%s~%s~%s" % (THREAD_NUM, THREAD_ADDR,
                                            THREAD_NAME, text))
        if args.verbose:
            log("JA:", text[:60])

    stop = threading.Event()
    try:
        CDPHook(args.cdp_port, emit, verbose=args.verbose).run(stop)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
