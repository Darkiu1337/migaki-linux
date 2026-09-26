#!/usr/bin/env python3
"""cfg.py — shared translator settings loader.

Resolution order: translate/config.json (user overrides, seeded by
install.sh) -> translate/config.json.sample (shipped defaults) ->
builtin DEFAULTS. Never raises on missing files, so textbox.py and
vn_translate.py launch on a fresh clone.
"""
import json
import os
import shutil

# Entry points may run through ~/.local/bin symlinks: resolve them, or the
# sample/override lookup lands next to the link instead of this directory.
HERE = os.path.dirname(os.path.realpath(__file__))

# Chromium-family binaries (DeepL automation target). Probed only when the
# configured path is missing, so a stale/seeded absolute path can't wedge a
# fresh machine.
BROWSER_CANDIDATES = (
    "brave", "brave-browser", "brave-origin", "chromium", "chromium-browser",
    "google-chrome", "google-chrome-stable", "chrome",
    "microsoft-edge", "microsoft-edge-stable", "vivaldi", "opera",
)

DEFAULTS = {
    "hook_url": "ws://localhost:6677",
    "hook_filter": "",
    "debugport": 9222,
    # "" = auto-detect on PATH at load time (install.sh records an absolute path).
    "brave_bin": "",
    "brave_profile": "~/.cache/vn-translate/brave-cdp-profile",
    "deepl_url": "https://www.deepl.com/en/translator",
    "srclang": "ja",
    "tgtlang": "en",
    "cdp_timeout": 30,
    "browser_hidden": True,
    # Previous lines prepended to the DeepL input (0 = off). Fixes discourse
    # errors (who "she" is) at the cost of a little latency.
    "context_lines": 6,
}


def _resolve_browser(bin_path):
    """Keep a working configured path; otherwise probe PATH."""
    if bin_path and os.path.isfile(bin_path) and os.access(bin_path, os.X_OK):
        return bin_path
    for name in BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return bin_path


def load_config():
    cfg = dict(DEFAULTS)
    for name in ("config.json", "config.json.sample"):
        try:
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                cfg.update(json.load(f))
            break
        except (OSError, ValueError):
            continue
    for key in ("brave_profile",):
        val = cfg.get(key)
        if isinstance(val, str):
            cfg[key] = os.path.expanduser(os.path.expandvars(val))
    cfg["brave_bin"] = _resolve_browser(cfg.get("brave_bin"))
    # Per-game override set by the launcher (0 = show the browser, 1 = headless).
    env = os.environ.get("VN_BROWSER_HIDDEN")
    if env in ("0", "1"):
        cfg["browser_hidden"] = env == "1"
    return cfg
