#!/usr/bin/env python3
"""clean.py — pre-translation text hygiene for the VN pipeline.

Hooked text is not always clean: engines leak zero-width characters, control
codes, ruby markup, decorative symbols, and inconsistent punctuation. This
module normalises a line before it reaches the translator. Every step is
opt-in so the pipeline can be conservative.

Pure module: no Qt, no network.
"""
import re

ZW = "\u200b\u200c\u200d\u2060\ufeff"
_DECOR = "♪♫♬♩♭♯♥♡❤★☆●○◎◯◆◇■□▲△▼▽※→←↑↓"
_RUBY = re.compile(r'([\u4e00-\u9fff]+)\{[\u3041-\u309f\u30a1-\u30ffー/：:]+\}')
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def strip_zero_width(text):
    return "".join(c for c in text if c not in ZW)


def strip_control(text):
    return _CONTROL.sub("", text)


def strip_ruby(text):
    """{漢字/かんじ} and {漢字:かんじ} -> 漢字 (keep the base kanji)."""
    return _RUBY.sub(r"\1", text)


def strip_decor(text):
    return "".join(c for c in text if c not in _DECOR)


def normalize_punct(text):
    """Collapse stylised runs to their canonical form (DeepL handles the
    canonical ones better and the output is tidier)."""
    text = re.sub(r'[…]{2,}', '…', text)
    text = re.sub(r'[—―]{2,}', '—', text)
    text = re.sub(r'[～~]{2,}', '～', text)
    text = re.sub(r'[。]{2,}', '。', text)
    text = re.sub(r'[！!]{2,}', '！', text)
    text = re.sub(r'[？?]{2,}', '？', text)
    return text


def collapse_ws(text):
    """Normalise full-width spaces and collapse runs, but keep the line whole."""
    text = text.replace('\u3000', ' ')
    return re.sub(r'[ \t]+', ' ', text).strip()


def normalize(text, decor=True, ruby=True, punct=True, control=True, zw=True):
    if zw:
        text = strip_zero_width(text)
    if control:
        text = strip_control(text)
    if ruby:
        text = strip_ruby(text)
    if decor:
        text = strip_decor(text)
    if punct:
        text = normalize_punct(text)
    return collapse_ws(text)


def speaker_parts(line):
    """Split `名前「セリフ」rest` into (name, dialogue, rest). A line that is
    not a clean speaker tag returns (None, line, "")."""
    m = re.match(r'^([\u4e00-\u9fff\u30a0-\u30ff]{1,5})「([^」]*)」(.*)$',
                 line.strip())
    if not m:
        return None, line, ""
    name, body, rest = m.groups()
    if any(c in 'をがはにでとものへや、。' for c in name):
        return None, line, ""
    return name, body, rest.strip()


def format_speaker(line, keep_quotes=True):
    """Rewrite `名前「セリフ」` as `名前: 「セリフ」` — an explicit, unambiguous
    speaker format for the translator. Non-dialogue lines pass through."""
    name, body, rest = speaker_parts(line)
    if name is None:
        return line
    head = f'{name}: ' + (f'「{body}」' if keep_quotes else body)
    return f'{head} {rest}'.strip() if rest else head
