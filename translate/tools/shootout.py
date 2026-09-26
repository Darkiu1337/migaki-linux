#!/usr/bin/env python3
"""shootout.py — offline translation quality shoot-out for Migaki's VN pipeline.

Dev-only tool (never referenced by install.sh or the launchers). It answers one
question: does giving a translator the preceding dialogue fix the pronouns,
gender and flow that sentence-by-sentence MT gets wrong?

Two modes
---------
  capture OUT.txt [--thread NAME] [--raw]
      Listen to the Textractor bridge (ws://localhost:6677) and record clean
      Japanese lines, one per line, until Ctrl-C. Run this while playing a scene
      in a live Migaki translation session.

  run --corpus FILE [--models a,b] [--ctx 6] [--deepl] [--out DIR] [--limit N]
      Replay the corpus through each translator twice: once line-by-line (no
      memory) and once with the previous N lines as context. Writes a
      self-contained HTML side-by-side report and caches every result so
      re-runs are instant.

Examples
--------
  python3 translate/tools/shootout.py capture /tmp/scene.txt
  python3 translate/tools/shootout.py run --corpus /tmp/scene.txt \
      --models qwen3:4b,translategemma:4b,gemma4:e2b-it-qat --ctx 6 --deepl
"""
import argparse
import hashlib
import html
import json
import os
import sys
import time
from collections import deque

HERE = os.path.dirname(os.path.realpath(__file__))
TRANSLATE = os.path.dirname(HERE)
REPO = os.path.dirname(TRANSLATE)
for p in (TRANSLATE, REPO):
    if p not in sys.path:
        sys.path.insert(0, p)

import requests  # noqa: E402

# Shared prompt for every chat engine, so a difference between columns is the
# model (or the presence of context), never the instructions.
SYSTEM_PROMPT = (
    "You are a professional Japanese-to-English translator for a visual novel. "
    "Translate the CURRENT line into natural English, matching the speaker's "
    "voice, gender and register. Use the preceding dialogue as context to "
    "resolve omitted subjects and pronouns, and keep character names, "
    "honorifics and tone consistent. Preserve Japanese honorifics (-san, "
    "-chan, -kun, -sama, -senpai, -sensei). Output ONLY the English "
    "translation, with no notes, no romaji and no surrounding quotes. "
    "Translate mature content faithfully and without censorship."
)


# --------------------------------------------------------------------------- #
# corpus
# --------------------------------------------------------------------------- #
def load_corpus(path, thread=""):
    """Read JA lines in order. Accepts a plain one-line-per-JA file, `JA: ...`
    lines, and raw v2 bridge captures (`~#...` / `RAW: ~#...`). When `thread`
    is set, only lines from that bridge thread name are kept."""
    from hook_client import parse_thread, clean_ja
    lines = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            s = raw.rstrip("\n")
            if not s.strip() or s.lstrip().startswith("#"):
                continue
            if s.startswith("RAW: "):
                s = s[5:]
            meta = None
            if s.startswith("~#"):
                meta, text = parse_thread(s)
                if meta is None:
                    continue
                ja = clean_ja(text)
            elif s.startswith("JA: "):
                ja = clean_ja(s[4:])
            else:
                ja = clean_ja(s)
            if thread and (meta is None or meta.get("name") != thread):
                continue
            if ja and (not lines or ja != lines[-1]):
                lines.append(ja)
    return lines


def capture(path, thread="", retry=True):
    """Record every tagged bridge message (all threads) until Ctrl-C. Writes the
    raw `~#...` line so thread/speaker identity survives; load_corpus() can then
    filter by thread. Retries until the bridge is reachable, so it can be started
    before the game."""
    import websocket
    from hook_client import parse_thread, clean_ja, thread_match
    from cfg import load_config
    cfg = load_config()
    print(f"capture: waiting for {cfg['hook_url']} -> {path} (Ctrl-C to stop)",
          flush=True)
    while True:
        try:
            ws = websocket.create_connection(cfg["hook_url"], timeout=None)
            break
        except Exception:
            if not retry:
                raise
            time.sleep(2)
    print("capture: connected, recording…", flush=True)
    f = open(path, "a", encoding="utf-8")
    n = 0
    try:
        while True:
            msg = ws.recv()
            meta, text = parse_thread(msg)
            if thread and not thread_match(meta, thread):
                continue
            ja = clean_ja(text)
            if not ja:
                continue
            f.write(msg + "\n")
            f.flush()
            n += 1
            print(f"JA[{meta['name'] if meta else '?'}]: {ja}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        f.close()
        print(f"capture: wrote {n} lines to {path}", flush=True)


# --------------------------------------------------------------------------- #
# engines
# --------------------------------------------------------------------------- #
class OllamaEngine:
    """Local model over Ollama's native /api/chat (think disabled)."""

    def __init__(self, model, url="http://127.0.0.1:11434", num_ctx=4096,
                 temperature=0.2, num_predict=256):
        self.model = model
        self.url = url.rstrip("/")
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.num_predict = num_predict

    def translate(self, ja, context):
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        for pja, pen in context:
            msgs.append({"role": "user", "content": pja})
            msgs.append({"role": "assistant", "content": pen})
        msgs.append({"role": "user", "content": ja})
        body = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "think": False,
            "options": {"temperature": self.temperature,
                        "num_ctx": self.num_ctx,
                        "num_predict": self.num_predict},
        }
        r = requests.post(f"{self.url}/api/chat", json=body, timeout=900)
        r.raise_for_status()
        return (r.json().get("message", {}).get("content") or "").strip()


class DeepLEngine:
    """The current shipping path (Brave CDP -> deepl.com). No context input."""

    def __init__(self, with_context=False):
        from cfg import load_config
        from deepl_cdp import BraveCDP
        self.cdp = BraveCDP(load_config())
        self.with_context = with_context

    def translate(self, ja, context):
        if self.with_context and context:
            src = "\n".join(pja for pja, _ in context) + "\n" + ja
            out = self.cdp.translate(src)
            # DeepL translated the whole block; keep the last non-empty line.
            parts = [p for p in out.splitlines() if p.strip()]
            return parts[-1] if parts else out
        return self.cdp.translate(ja)


class GlossaryDeepLEngine(DeepLEngine):
    """DeepL with a name glossary: swap known JA names for sentinels DeepL
    passes through, translate, then restore the pinned English names."""

    def __init__(self, glossary, with_context=False):
        super().__init__(with_context=with_context)
        self.glossary = glossary

    def translate(self, ja, context):
        masked, mapping = self.glossary.mask(ja)
        out = super().translate(masked, context)
        return self.glossary.unmask(out, mapping)


class CleanDeepLEngine(DeepLEngine):
    """DeepL with pre-translation text hygiene (punctuation/whitespace)."""

    def __init__(self, with_context=False):
        super().__init__(with_context=with_context)

    def translate(self, ja, context):
        import clean
        return super().translate(clean.normalize(ja), context)


class NarrationTrailingDeepLEngine(DeepLEngine):
    """clean + glossary + full context, but a narration line drops *trailing*
    dialogue lines from its context — that removes the "resolve the omitted
    subject to the last speaker" trigger without losing the deeper context."""

    def __init__(self, glossary, with_context=False):
        super().__init__(with_context=False)
        self.glossary = glossary

    @staticmethod
    def _dialogue(s):
        return '「' in s or '『' in s

    def translate(self, ja, context):
        import clean
        ctx_lines = [pja for pja, _ in context]
        if not self._dialogue(ja):
            while ctx_lines and self._dialogue(ctx_lines[-1]):
                ctx_lines.pop()
        parts = [self.glossary.mask(clean.normalize(x))[0] for x in ctx_lines]
        masked, mapping = self.glossary.mask(clean.normalize(ja))
        parts.append(masked)
        out = self.cdp.translate("\n".join(parts))
        lines = [ln for ln in out.splitlines() if ln.strip()]
        last = lines[-1] if lines else out
        return self.glossary.unmask(last, mapping)


class NarrationAwareDeepLEngine(DeepLEngine):
    """clean + glossary + context, but narration lines only see preceding
    narration (not dialogue), so DeepL stops resolving their omitted subject
    to the last speaker."""

    def __init__(self, glossary, with_context=False):
        super().__init__(with_context=False)
        self.glossary = glossary

    @staticmethod
    def _dialogue(s):
        return '「' in s or '『' in s

    def translate(self, ja, context):
        import clean
        cur_dlg = self._dialogue(ja)
        ctx_lines = [pja for pja, _ in context
                     if cur_dlg or not self._dialogue(pja)]
        parts = [self.glossary.mask(clean.normalize(x))[0] for x in ctx_lines]
        masked, mapping = self.glossary.mask(clean.normalize(ja))
        parts.append(masked)
        out = self.cdp.translate("\n".join(parts))
        lines = [ln for ln in out.splitlines() if ln.strip()]
        last = lines[-1] if lines else out
        return self.glossary.unmask(last, mapping)


class BestDeepLEngine(DeepLEngine):
    """Everything free at once: text hygiene + name glossary + context."""

    def __init__(self, glossary, with_context=False):
        super().__init__(with_context=False)
        self.glossary = glossary

    def translate(self, ja, context):
        import clean
        parts = [self.glossary.mask(clean.normalize(pja))[0] for pja, _ in context]
        masked, mapping = self.glossary.mask(clean.normalize(ja))
        parts.append(masked)
        out = self.cdp.translate("\n".join(parts))
        lines = [ln for ln in out.splitlines() if ln.strip()]
        last = lines[-1] if lines else out
        return self.glossary.unmask(last, mapping)


class GlossaryContextDeepLEngine(DeepLEngine):
    """Both free levers at once: name glossary + context injection. Names are
    masked across the context block and the current line, the whole block is
    translated, the last output line (the current line) is kept, then names are
    restored."""

    def __init__(self, glossary, with_context=False):
        super().__init__(with_context=False)  # we build the source ourselves
        self.glossary = glossary

    def translate(self, ja, context):
        parts = [self.glossary.mask(pja)[0] for pja, _ in context]
        masked, mapping = self.glossary.mask(ja)
        parts.append(masked)
        out = self.cdp.translate("\n".join(parts))
        lines = [ln for ln in out.splitlines() if ln.strip()]
        last = lines[-1] if lines else out
        return self.glossary.unmask(last, mapping)


# --------------------------------------------------------------------------- #
# replay + report
# --------------------------------------------------------------------------- #
def cache_key(engine, ctx_n, corpus_id, index):
    """Key by (engine, context size, corpus, line index). The replay is
    deterministic given those, so this is stable across runs — unlike keying on
    the model's own prior outputs, which made +ctx lookups miss and re-run."""
    blob = f"{engine}|{ctx_n}|{corpus_id}|{index}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def replay(engine, label, lines, ctx_n, cache, engine_name, corpus_id,
           save=None):
    """Translate `lines` in order. Context = this engine's own prior outputs
    (src+hyp), which the literature shows works best without gold references.
    `save` (if given) is called periodically so an interrupted run keeps its
    completed work instead of losing everything."""
    out = []
    ctx = deque(maxlen=ctx_n) if ctx_n > 0 else deque(maxlen=0)
    for i, ja in enumerate(lines, 1):
        key = cache_key(engine_name, ctx_n, corpus_id, i)
        hit = cache.get(key)
        if hit is not None:
            en, latency = hit["en"], hit["latency"]
            via = "cache"
        else:
            t0 = time.time()
            try:
                en = engine.translate(ja, list(ctx))
                via = "ok"
            except Exception as e:
                en = f"[{label} failed: {e}]"
                via = "error"
            latency = round(time.time() - t0, 2)
            if via == "ok":
                cache[key] = {"en": en, "latency": latency}
                if save and i % 10 == 0:
                    save()
        out.append({"ja": ja, "en": en, "latency": latency, "via": via})
        if via == "ok":
            ctx.append((ja, en))
        print(f"  [{label}] {i}/{len(lines)} {latency}s", flush=True)
    if save:
        save()
    return out


def write_html(path, lines, columns, ctx_n, models):
    def esc(s):
        return html.escape(s or "")

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>Migaki translation shoot-out</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;margin:1rem;background:#111;color:#eee}",
        "table{border-collapse:collapse;width:100%}",
        "th,td{border:1px solid #333;padding:.4rem .6rem;vertical-align:top;text-align:left}",
        "th{background:#1b1b1b;position:sticky;top:0}",
        "tr:nth-child(even){background:#161616}",
        ".ja{color:#9ab;white-space:pre-wrap;max-width:22em}",
        ".en{white-space:pre-wrap}",
        ".lat{color:#666;font-size:.75rem;display:block}",
        ".err{color:#e66}",
        "h1{font-size:1.1rem} .meta{color:#888;font-size:.85rem}",
        "</style>",
        "<h1>Migaki translation shoot-out</h1>",
        f"<p class='meta'>{len(lines)} lines &middot; context window {ctx_n} "
        f"&middot; models: {esc(', '.join(models) or 'none')}</p>",
        "<table><thead><tr><th>#</th><th>Japanese</th>",
    ]
    for c in columns:
        parts.append(f"<th>{esc(c['label'])}</th>")
    parts.append("</tr></thead><tbody>")

    for i in range(len(lines)):
        parts.append("<tr>")
        parts.append(f"<td>{i + 1}</td>")
        parts.append(f"<td class='ja'>{esc(lines[i])}</td>")
        for c in columns:
            row = c["rows"][i]
            cls = "err" if row["via"] == "error" else ""
            parts.append(
                f"<td class='en {cls}'>{esc(row['en'])}"
                f"<span class='lat'>{row['latency']}s</span></td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="record JA lines from the live bridge")
    cap.add_argument("out")
    cap.add_argument("--thread", default="",
                     help="only record this bridge thread name (default: all)")
    cap.add_argument("--no-retry", action="store_true",
                     help="fail immediately if the bridge is down")

    run = sub.add_parser("run", help="replay a corpus through translators")
    run.add_argument("--corpus", required=True)
    run.add_argument("--thread", default="",
                     help="only use corpus lines from this bridge thread name")
    run.add_argument("--models", default="qwen3:4b",
                     help="comma-separated Ollama model names")
    run.add_argument("--ctx", type=int, default=6,
                     help="context lines fed to each model (0 = none)")
    run.add_argument("--deepl", action="store_true",
                     help="also include the DeepL baseline (launches Brave)")
    run.add_argument("--deepl-context", action="store_true",
                     help="also include DeepL fed the context block")
    run.add_argument("--deepl-glossary", action="store_true",
                     help="also include DeepL with the auto name glossary applied")
    run.add_argument("--deepl-gloss-context", action="store_true",
                     help="also include DeepL with glossary + context together")
    run.add_argument("--deepl-clean", action="store_true",
                     help="also include DeepL with pre-translation text hygiene")
    run.add_argument("--deepl-best", action="store_true",
                     help="also include DeepL with clean + glossary + context")
    run.add_argument("--deepl-narr", action="store_true",
                     help="also include narration-only context")
    run.add_argument("--deepl-narr2", action="store_true",
                     help="also include full context minus trailing dialogue")
    run.add_argument("--out", default="/tmp/migaki-shootout")
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    run.add_argument("--num-ctx", type=int, default=4096)
    run.add_argument("--num-predict", type=int, default=256)
    run.add_argument("--temperature", type=float, default=0.2)

    args = ap.parse_args()

    if args.cmd == "capture":
        capture(args.out, args.thread, retry=not args.no_retry)
        return

    os.makedirs(args.out, exist_ok=True)
    cache_path = os.path.join(args.out, "cache.json")
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}

    def save_cache():
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f)
        except OSError:
            pass

    lines = load_corpus(args.corpus, args.thread)
    if args.limit:
        lines = lines[:args.limit]
    if not lines:
        print(f"no lines in {args.corpus}", file=sys.stderr)
        sys.exit(1)

    corpus_id = hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:16]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    columns = []

    if args.deepl:
        try:
            eng = DeepLEngine(with_context=False)
            columns.append({"label": "DeepL", "rows": replay(
                eng, "DeepL", lines, 0, cache, "deepl", corpus_id, save_cache)})
        except Exception as e:
            print(f"deepl unavailable: {e}", file=sys.stderr)

    if args.deepl_context:
        try:
            eng = DeepLEngine(with_context=True)
            columns.append({"label": "DeepL+ctx", "rows": replay(
                eng, "DeepL+ctx", lines, args.ctx, cache, "deepl+ctx",
                corpus_id, save_cache)})
        except Exception as e:
            print(f"deepl+ctx unavailable: {e}", file=sys.stderr)

    if args.deepl_glossary:
        try:
            from names import NameGlossary
            gloss = NameGlossary()
            prev = ""
            for ln in lines:
                gloss.observe(ln, prev)
                prev = ln
            eng = GlossaryDeepLEngine(gloss)
            columns.append({"label": "DeepL + name glossary", "rows": replay(
                eng, "DeepL+gloss", lines, 0, cache, "deepl+gloss", corpus_id,
                save_cache)})
        except Exception as e:
            print(f"deepl+glossary unavailable: {e}", file=sys.stderr)

    if args.deepl_clean:
        try:
            eng = CleanDeepLEngine()
            columns.append({"label": "DeepL + cleaned input", "rows": replay(
                eng, "DeepL+clean", lines, 0, cache, "deepl+clean", corpus_id,
                save_cache)})
        except Exception as e:
            print(f"deepl+clean unavailable: {e}", file=sys.stderr)

    if args.deepl_narr2:
        try:
            from names import NameGlossary
            gloss = NameGlossary()
            prev = ""
            for ln in lines:
                gloss.observe(ln, prev)
                prev = ln
            eng = NarrationTrailingDeepLEngine(gloss)
            columns.append({"label": "DeepL narr-trailing", "rows": replay(
                eng, "DeepL+narr2", lines, args.ctx, cache, "deepl+narr2",
                corpus_id, save_cache)})
        except Exception as e:
            print(f"deepl+narr2 unavailable: {e}", file=sys.stderr)

    if args.deepl_narr:
        try:
            from names import NameGlossary
            gloss = NameGlossary()
            prev = ""
            for ln in lines:
                gloss.observe(ln, prev)
                prev = ln
            eng = NarrationAwareDeepLEngine(gloss)
            columns.append({"label": "DeepL narr-aware", "rows": replay(
                eng, "DeepL+narr", lines, args.ctx, cache, "deepl+narr",
                corpus_id, save_cache)})
        except Exception as e:
            print(f"deepl+narr unavailable: {e}", file=sys.stderr)

    if args.deepl_best:
        try:
            from names import NameGlossary
            gloss = NameGlossary()
            prev = ""
            for ln in lines:
                gloss.observe(ln, prev)
                prev = ln
            eng = BestDeepLEngine(gloss)
            columns.append({"label": "DeepL BEST (clean+gloss+ctx)", "rows": replay(
                eng, "DeepL+best", lines, args.ctx, cache, "deepl+best",
                corpus_id, save_cache)})
        except Exception as e:
            print(f"deepl+best unavailable: {e}", file=sys.stderr)

    if args.deepl_gloss_context:
        try:
            from names import NameGlossary
            gloss = NameGlossary()
            prev = ""
            for ln in lines:
                gloss.observe(ln, prev)
                prev = ln
            eng = GlossaryContextDeepLEngine(gloss)
            columns.append({"label": "DeepL + glossary + context", "rows": replay(
                eng, "DeepL+gloss+ctx", lines, args.ctx, cache, "deepl+gloss+ctx",
                corpus_id, save_cache)})
        except Exception as e:
            print(f"deepl+gloss+ctx unavailable: {e}", file=sys.stderr)

    for m in models:
        eng = OllamaEngine(m, url=args.ollama_url, num_ctx=args.num_ctx,
                           temperature=args.temperature,
                           num_predict=args.num_predict)
        columns.append({"label": f"{m} (no memory)", "rows": replay(
            eng, f"{m} no-mem", lines, 0, cache, m, corpus_id, save_cache)})
        columns.append({"label": f"{m} + {args.ctx}-line memory", "rows": replay(
            eng, f"{m} +ctx", lines, args.ctx, cache, m + "+ctx", corpus_id,
            save_cache)})

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)

    report = write_html(os.path.join(args.out, "compare.html"), lines,
                        columns, args.ctx, models)
    print(f"\nreport: {report}", flush=True)


if __name__ == "__main__":
    main()
