#!/usr/bin/env python3
"""harvest-hooks.py — record Textractor's saved hook codes into games.json.

After a Setup session where the user clicked "Save hook(s)" in Textractor,
the codes live in <prefix>/drive_c/Textractor/x86/SavedHooks.txt. This reads
the last saved line for the game, writes the code into the game's
translate.hook_code field, and (if present) the selected thread's identity
into translate.thread. Closing the loop: nothing is ever hand-copied.

Usage: harvest-hooks.py --exe /path/game.exe --game ID [--lib games.json]
       [--prefix DIR]   (default: prefix from ~/.config/migaki/config.json)
Exit: 0 recorded something, 1 nothing to harvest (not an error for callers).
"""
import argparse
import json
import os
import sys


def wine_path(unix, prefix):
    p = os.path.abspath(unix)
    drive_c = os.path.join(prefix, "drive_c")
    if p.startswith(drive_c + "/"):
        return "C:" + p[len(drive_c):].replace("/", "\\")
    return "Z:" + p.replace("/", "\\")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--game", required=True)
    ap.add_argument("--lib", default=os.path.expanduser("~/.config/migaki/games.json"))
    ap.add_argument("--prefix", default="")
    args = ap.parse_args()

    prefix = args.prefix
    if not prefix:
        try:
            with open(os.path.expanduser("~/.config/migaki/config.json")) as f:
                prefix = json.load(f).get("prefix", "")
        except (OSError, ValueError):
            prefix = ""
    if not prefix:
        prefix = os.path.expanduser("~/.local/share/migaki/prefixes/default")

    saved = os.path.join(prefix, "drive_c", "Textractor", "x86", "SavedHooks.txt")
    vexe = wine_path(args.exe, prefix)
    lines = []
    try:
        with open(saved, encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f.read().splitlines() if l.strip()]
    except OSError:
        print(f"harvest: no SavedHooks.txt at {saved}")
        return 1

    # Upstream format: "path , code1 , code2 ... [|ctx:ctx2:code]" — last line
    # for this exe wins (matches Textractor's own lookup order).
    match = None
    for line in lines:
        if line.split(" , ")[0] == vexe:
            match = line
    if match is None or " , " not in match:
        print(f"harvest: no saved hooks for {vexe} (run Setup and click Save hook(s) in Textractor)")
        return 1

    parts = [p.strip() for p in match.split(" , ")][1:]
    codes = [p for p in parts if p and not p.startswith("|")]
    select = next((p for p in parts if p.startswith("|")), "")
    # The |ctx:ctx2:code suffix carries the code of the thread the user had
    # SELECTED when saving — the story thread. Prefer it as the hook code.
    sel_code = select.split(":")[-1] if select else ""
    hook_code = next((c for c in ([sel_code] if sel_code else []) + codes if c), "")

    try:
        with open(args.lib) as f:
            lib = json.load(f)
    except (OSError, ValueError) as e:
        print(f"harvest: cannot read {args.lib}: {e}")
        return 1
    game = (lib.get("games") or {}).get(args.game)
    if game is None:
        print(f"harvest: unknown game id '{args.game}' in {args.lib}")
        return 1
    tr = game.setdefault("translate", {})
    changed = False
    if hook_code and hook_code != tr.get("hook_code"):
        tr["hook_code"] = hook_code
        changed = True
        print(f"harvest: hook_code = {hook_code}")
    if len(codes) > 1 and sorted(codes) != sorted(tr.get("all_hooks", [])):
        tr["all_hooks"] = codes
        changed = True
        print(f"harvest: all_hooks = {', '.join(codes)}")
    if not changed:
        print("harvest: games.json already up to date")
        return 0
    tmp = args.lib + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lib, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, args.lib)
    print(f"harvest: recorded into {args.lib}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
