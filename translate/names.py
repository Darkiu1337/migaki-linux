#!/usr/bin/env python3
"""names.py — automatic name/term glossary for the VN translation pipeline.

Names are the one thing sentence-MT reliably gets wrong: the same character
comes out as "Rinoh", "Rin Sakura", "Rinohara" across a scene. The game often
hands us the answer — many engines emit a furigana reading line right after a
name is introduced (e.g. 「小花衣凛桜」 then 「こはないりお」 → "Kohanai Rio").

This module harvests names from the hooked line stream and pins one English
rendering per Japanese name, so every engine (DeepL included) stays consistent.

Sources, best first:
  1. reading lines  (all-kana line after a kanji-name line) -> exact romanization
  2. ruby patterns  ({漢字/かんじ}, 漢字(かんじ), 漢字《かんじ》)
  3. speaker tags   (名前「…」) -> the kanji name; rendering locked from the
     first good engine output, user-editable

Pure module: no Qt, no network. The pipeline feeds it lines and asks it to
mask/unmask names around a translation call.

CLI (inspect what a capture yields):
  python3 names.py CAPTURE [--thread NAME]
"""
import argparse
import json
import os
import re
import sys


def glossary_path(gameid):
    """Per-game glossary file (auto-built, user-editable)."""
    d = os.path.expanduser("~/.config/migaki/glossary")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, f"{gameid or 'default'}.json")

# --------------------------------------------------------------------------- #
# kana -> Hepburn romaji
# --------------------------------------------------------------------------- #
_BASE = {
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo',
    'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'ゐ': 'i', 'ゑ': 'e', 'を': 'o', 'ん': 'n',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o',
    'ゃ': 'ya', 'ゅ': 'yu', 'ょ': 'yo', 'ゎ': 'wa', 'ー': '',
}
_DIGRAPH = {
    'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo', 'しゃ': 'sha', 'しゅ': 'shu',
    'しょ': 'sho', 'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho', 'にゃ': 'nya',
    'にゅ': 'nyu', 'にょ': 'nyo', 'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo',
    'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo', 'りゃ': 'rya', 'りゅ': 'ryu',
    'りょ': 'ryo', 'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo', 'じゃ': 'ja',
    'じゅ': 'ju', 'じょ': 'jo', 'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo',
    'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo',
}


def katakana_to_hiragana(s):
    return ''.join(chr(ord(c) - 0x60) if '\u30a1' <= c <= '\u30f6' else c
                   for c in s)


def is_kana(s):
    return bool(s) and all(('\u3041' <= c <= '\u309f') or
                           ('\u30a1' <= c <= '\u30ff') or c == 'ー'
                           for c in s)


def romaji(kana):
    """Hepburn romaji for a kana string (katakana accepted)."""
    kana = katakana_to_hiragana(kana)
    out, i = [], 0
    while i < len(kana):
        pair = kana[i:i + 2]
        if pair in _DIGRAPH:
            out.append(_DIGRAPH[pair]); i += 2; continue
        if kana[i] == 'っ' and i + 1 < len(kana):
            out.append(_BASE.get(kana[i + 1], '')[:1]); i += 1; continue
        out.append(_BASE.get(kana[i], kana[i])); i += 1
    return ''.join(out)


def split_and_romanize(kanji_name, reading, given_kanji=None):
    """Romanize a name's kana reading, splitting surname/given when we can.
    The reliable split uses a known short (given) name that suffixes the full
    name: speaker tags give 凛桜, so 小花衣凛桜 + こはないりお splits into
    小花衣|凛桜 -> Kohanai|Rio. Without that, the reading stays one token."""
    if given_kanji and reading and kanji_name.endswith(given_kanji) \
            and len(kanji_name) > len(given_kanji):
        surname = kanji_name[:-len(given_kanji)]
        cut = round(len(reading) * len(surname) / len(kanji_name))
        s, g = reading[:cut], reading[cut:]
        if s and g:
            return f"{romaji(s).capitalize()} {romaji(g).capitalize()}"
    return romaji(reading).capitalize() if reading else ""


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
# Japanese honorifics -> romaji, so a masked "名前さん" doesn't become "Mr. Name"
HONORIFICS = {
    "さん": "san", "ちゃん": "chan", "くん": "kun", "さま": "sama",
    "せんぱい": "senpai", "せんせい": "sensei", "どの": "dono",
}

_KANJI = r'[\u4e00-\u9fff]'
_SPEAKER_RE = re.compile(r'^([\u4e00-\u9fff\u30a0-\u30ff]{1,5})(?=[「（(])')
_PARTICLES = set('をがはにでとものへや、。')
_RUBY_RES = [
    re.compile(r'([\u4e00-\u9fff]+)\{([\u3041-\u309f\u30a1-\u30ffー]+)\}'),
    re.compile(r'([\u4e00-\u9fff]+)\(([\u3041-\u309f\u30a1-\u30ffー]+)\)'),
    re.compile(r'([\u4e00-\u9fff]+)《([\u3041-\u309f\u30a1-\u30ffー]+)》'),
]


def find_speaker(line):
    """The speaker name in `名前「…」`, or None. Rejects narration prefixes
    like 「出会いを訊くと『…』」 (particles / quote-in-quote)."""
    m = _SPEAKER_RE.match(line.strip())
    if not m:
        return None
    name = m.group(1)
    if any(c in _PARTICLES for c in name):
        return None
    return name


def find_ruby(line):
    for rx in _RUBY_RES:
        for m in rx.finditer(line):
            yield m.group(1), m.group(2)


def is_reading_line(line, prev_line=""):
    """A short all-kana line that reads the kanji name introduced on the
    previous line (小花衣凛桜 then こはないりお). The prev_line guard keeps
    ordinary kana dialogue (はい, うん) and post-dialogue replies from matching."""
    s = line.strip()
    if not (2 <= len(s) <= 12 and is_kana(s)):
        return False
    if prev_line:
        if '「' in prev_line or '『' in prev_line:
            return False
        if not re.search(r'[\u4e00-\u9fff]{2,}', prev_line):
            return False
    return True


# --------------------------------------------------------------------------- #
# glossary
# --------------------------------------------------------------------------- #
class NameGlossary:
    def __init__(self, entries=None):
        # source_ja -> {"en": str, "source": str, "locked": bool}
        self.entries = entries or {}
        self.known = set()  # name tokens seen, used to align a full name

    # -- discovery ---------------------------------------------------------- #
    def observe(self, line, prev_line=""):
        """Feed one line (plus the previous one). Returns the names seen."""
        seen = []
        for kanji, reading in find_ruby(line):
            self.known.add(kanji)
            self._set(kanji, split_and_romanize(kanji, reading), "ruby")
            seen.append(kanji)
        spk = find_speaker(line)
        if spk:
            self.known.add(spk)
            seen.append(spk)
        # A reading line resolves the most recent kanji name before it.
        if prev_line and is_reading_line(line, prev_line):
            full = self._trailing_name(prev_line)
            if full and full not in self.entries:
                given = self._known_suffix(full)
                en = split_and_romanize(full, line.strip(), given)
                self._set(full, en, "reading")
                seen.append(full)
                if given and ' ' in en:
                    self._set(given, en.split()[-1], "reading")
                    seen.append(given)
        return seen

    def _known_suffix(self, full):
        """Longest known name token that suffixes `full` (the given name)."""
        best = ""
        for tok in self.known:
            if tok != full and full.endswith(tok) and len(tok) > len(best):
                best = tok
        return best

    @staticmethod
    def _trailing_name(line):
        blocks = re.findall(r'[\u4e00-\u9fff]{2,}', line)
        return blocks[-1] if blocks else None

    def _set(self, ja, en, source, locked=True):
        if not ja or not en:
            return
        cur = self.entries.get(ja)
        # never overwrite a reading-derived or user entry
        if cur and cur.get("source") in ("reading", "ruby", "manual"):
            return
        self.entries[ja] = {"en": en, "source": source, "locked": locked}

    def lock_rendering(self, ja, en, source="engine"):
        """Pin a name's rendering from an engine's first output (used for
        names that have no reading line, e.g. 彰之 -> Akiyuki)."""
        if ja and en:
            self.entries.setdefault(ja, {"en": en, "source": source,
                                         "locked": True})

    # -- application -------------------------------------------------------- #
    def mask(self, text):
        """Replace known JA names (and name+honorific) with private-use
        sentinels DeepL passes through untouched. Returns
        (masked_text, {sentinel: english})."""
        items = []
        for ja, v in self.entries.items():
            if not ja:
                continue
            items.append((ja, v["en"]))
            for h, r in HONORIFICS.items():
                items.append((ja + h, f'{v["en"]}-{r}'))
        items.sort(key=lambda x: len(x[0]), reverse=True)
        mapping, masked = {}, text
        for i, (ja, en) in enumerate(items):
            if i >= 0x1900:  # stay inside the BMP private-use area
                break
            if ja in masked:
                sentinel = chr(0xE000 + i)
                mapping[sentinel] = en
                masked = masked.replace(ja, sentinel)
        return masked, mapping

    def unmask(self, text, mapping):
        names = set()
        for sentinel, en in mapping.items():
            text = text.replace(sentinel, en)
            names.add(en)
        # DeepL can drop or space out the separator after a masked speaker
        # name; restore a single "Name: " before the quote.
        for en in names:
            text = re.sub(r'(' + re.escape(en) + r')\s*(?=[“"「『])',
                          r'\1: ', text)
        return text

    def prompt_glossary(self):
        """One line per name, for an LLM system prompt (future use)."""
        return "\n".join(f"{ja} -> {v['en']}" for ja, v in self.entries.items())

    # -- persistence -------------------------------------------------------- #
    def to_dict(self):
        return {"entries": self.entries}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("entries", {}) if isinstance(d, dict) else {})

    def load(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                self.entries = json.load(f).get("entries", {})
        except (OSError, ValueError):
            pass
        return self

    def save(self, path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture")
    ap.add_argument("--thread", default="")
    ap.add_argument("--save", metavar="GAMEID", default="",
                    help="write the built glossary to the per-game store")
    args = ap.parse_args()

    sys.path.insert(0, __import__("os").path.dirname(
        __import__("os").path.realpath(__file__)))
    from hook_client import parse_thread, clean_ja  # noqa: E402

    g = NameGlossary()
    lines = []
    with open(args.capture, encoding="utf-8", errors="replace") as f:
        for raw in f:
            s = raw.rstrip("\n")
            if not s.strip() or s.startswith("#"):
                continue
            meta = None
            if s.startswith("~#"):
                meta, text = parse_thread(s)
                if meta is None:
                    continue
                if args.thread and meta.get("name") != args.thread:
                    continue
                ja = clean_ja(text)
            else:
                ja = clean_ja(s[5:] if s.startswith("JA: ") else s)
            if not ja:
                continue
            g.observe(ja, lines[-1] if lines else "")
            lines.append(ja)

    print(f"{len(lines)} lines, {len(g.entries)} glossary entries\n")
    for ja, v in g.entries.items():
        print(f"  {ja:8s} -> {v['en']:20s} [{v['source']}]")
    if args.save:
        path = glossary_path(args.save)
        g.save(path)
        print(f"\nsaved -> {path}")


if __name__ == "__main__":
    main()
