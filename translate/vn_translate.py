#!/usr/bin/env python3
"""vn_translate.py — hook line in, translation out.
Reads JA lines from the Textractor websocket (or stdin), translates via
Brave-CDP DeepL, prints EN and forwards JA:/EN: pairs to the overlay over
stdout when piped.
Usage:
  vn_translate.py [--filter SUFFIX] [--thread NAME|NUM|*] [--no-cdp] [--print-only]
  echo "おはよう" | vn_translate.py --print-only   # headless check, no hook needed
"""
import os
import re
import sys
from collections import deque

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

from cfg import load_config
CONFIG = load_config()

try:
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except Exception:
    pass


def make_translator(use_cdp=True, glossary=None, save=None, context_lines=None):
    """Return tr(text) -> (out, via). Two free DeepL levers, both optional:

    * NameGlossary: every line is fed to it and known names are masked/unmasked
      around the DeepL call, so they stay consistent (凛桜 -> Rio, not "Rinoh").
      Furigana reading lines are consumed and signalled via="skip".
    * Context: the previous `context_lines` lines are prepended to the DeepL
      input and the last output line is kept — this fixes discourse errors
      (who "she" is, よろしく) at the cost of a little latency.
    """
    cdp = None
    if use_cdp:
        try:
            from deepl_cdp import BraveCDP
            cdp = BraveCDP(CONFIG)
            print("translate: Brave CDP ready", flush=True)
        except Exception as e:
            print(f"translate: CDP unavailable ({e})", flush=True)

    n_ctx = CONFIG.get("context_lines", 0) if context_lines is None \
        else context_lines
    ctx = deque(maxlen=max(0, int(n_ctx)))
    state = {"prev": ""}

    def tr(text):
        masked, mapping = text, {}
        if glossary is not None:
            import names
            prev = state["prev"]
            before = len(glossary.entries)
            glossary.observe(text, prev)
            state["prev"] = text
            if len(glossary.entries) != before and save:
                save()
            if names.is_reading_line(text, prev):
                return None, "skip"
            masked, mapping = glossary.mask(text)
        if cdp is not None:
            try:
                if ctx:
                    raw = cdp.translate("\n".join(list(ctx) + [masked]))
                    parts = [ln for ln in raw.splitlines() if ln.strip()]
                    out = parts[-1] if parts else raw
                else:
                    out = cdp.translate(masked)
                if glossary is not None:
                    if mapping:
                        out = glossary.unmask(out, mapping)
                    # A name with no reading line gets pinned from the engine's
                    # own first rendering (彰之 -> "Akiyuki: ...").
                    spk = names.find_speaker(text)
                    if spk and spk not in glossary.entries:
                        head = re.match(
                            r"^\s*([A-Za-z][A-Za-z'\-\. ]{0,24}?)\s*[:：]", out)
                        if head:
                            glossary.lock_rendering(spk, head.group(1).strip())
                            if save:
                                save()
                ctx.append(masked)
                return out, "cdp"
            except Exception as e:
                print(f"translate: CDP failed ({e})", flush=True)
        return "[DeepL unavailable — retrying]", "none"
    return tr


def main():
    args = sys.argv[1:]
    filt = ""
    if "--filter" in args:
        filt = args[args.index("--filter") + 1]
    thread = "*"
    if "--thread" in args and args.index("--thread") + 1 < len(args):
        thread = args[args.index("--thread") + 1]
    use_cdp = "--no-cdp" not in args
    tr = make_translator(use_cdp)
    if not sys.stdin.isatty():  # piped JA lines (or hook_client output)
        last_in = ""
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith(("hook:", "RAW:", "translate:")):
                continue
            if line.startswith("TEXT:"):
                line = line[5:].strip()
            elif line.startswith("[") and "] " in line:
                hook, line = line[1:].split("] ", 1)
                if filt and filt not in hook:
                    continue
            elif line.startswith("JA:"):
                line = line[3:].strip()
            if not line or line == last_in:
                continue
            last_in = line
            out, via = tr(line)
            print(f"JA: {line}", flush=True)
            print(f"EN[{via}]: {out}", flush=True)
        return
    # Direct bridge listen: route through hook_client so vn-bridge v2 thread
    # tags are parsed/filtered (default follows Textractor's own selection).
    from hook_client import listen
    def on_ja(ja):
        out, via = tr(ja)
        print(f"JA: {ja}", flush=True)
        print(f"EN[{via}]: {out}", flush=True)
    listen(CONFIG["hook_url"], filt, False, False, on_message=on_ja, thread=thread)


if __name__ == "__main__":
    main()
