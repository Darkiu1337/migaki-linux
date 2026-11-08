"""core CLI — the shell-facing surface used by the TUI.

Commands: launch, add, set, tset, remove, gpus, upscale-{show,render,note}.
The GUI imports the modules directly; this exists so the bash frontend shares
one store/validation path, and so scripts/gamescope-migaki.sh has a stable
machine-readable surface for the upscale profile.
"""
import argparse
import os
import sys


def _launch(args):
    from . import commands, store
    game = store.load_games().get(args.gid)
    if not game:
        print(f"error: unknown game id '{args.gid}'", file=sys.stderr)
        return 1
    path = game.get("path", "")
    if not os.path.exists(path):
        print(f"error: path no longer exists: {path}", file=sys.stderr)
        return 1
    tr = game.get("translate") or {}
    if game.get("runner", "proton") == "proton" and tr.get("enabled") == "1":
        hook_saved = bool((tr.get("hook_code") or "").strip()
                          or (tr.get("thread") or "").strip())
        setup = not hook_saved
        if setup:
            print(f"no hook recorded for '{args.gid}': launching SETUP "
                  "(pick the story thread in the in-app picker)")
        argv = commands.build_translate_command(game, args.gid, setup=setup)
    else:
        argv = commands.build_command(game, args.gid)
    os.execv(argv[0], argv)
    return 0


def _add(args):
    from . import library, store
    data = {
        "name": args.name,
        "runner": args.runner,
        "path": args.path,
        "variant": args.variant,
        "gpu": args.gpu,
        "fps": args.fps,
        "hud": args.hud,
        "prefix_mode": args.prefix_mode,
        "lang": args.lang,
        "upscale": args.upscale,
        "translate": {
            "enabled": args.tr_enabled,
            "hook_code": args.tr_hook,
            "hook_mode": "unknown",
        },
    }
    reason = library.validate_entry(data)
    if reason:
        print(f"error: {reason}", file=sys.stderr)
        return 1
    games = store.load_games()
    print(store.new_game(games, data))
    return 0


def _set(args):
    from . import store
    games = store.load_games()
    if args.gid not in games:
        print(f"error: unknown game id '{args.gid}'", file=sys.stderr)
        return 1
    games[args.gid][args.field] = args.value
    store.save_games(games)
    return 0


def _tset(args):
    from . import store
    games = store.load_games()
    if args.gid not in games:
        print(f"error: unknown game id '{args.gid}'", file=sys.stderr)
        return 1
    games[args.gid].setdefault("translate", {})[args.field] = args.value
    store.save_games(games)
    return 0


def _remove(args):
    from . import store
    store.remove_game(args.gid)
    return 0


def _gpus(args):
    from . import system
    for name in system.list_gpus():
        print(name)
    return 0


def _upscale_show(args):
    """Profile + recommendation as one JSON line (the TUI formats it)."""
    import json
    from . import upscale
    prof = upscale.profile(args.gid)
    rec = upscale.recommend(prof)
    print(json.dumps(dict(prof, verdict=rec["verdict"], scale=rec["scale"],
                          recommend=rec), sort_keys=True))
    return 0


def _upscale_render(args):
    """Emit "<render> <output>" for scripts/gamescope-migaki.sh.

    Resolves the pair when the profile has none yet (engine base, then half
    the monitor) and persists it, so later launches reuse the same mode."""
    from . import upscale
    prof = upscale.profile(args.gid)
    games = upscale.load()
    entry = games.get(args.gid) or {}
    changed = False
    for field in ("render", "output"):
        if not entry.get(field):
            entry[field] = prof[field]
            changed = True
    if changed:
        entry.setdefault("filter", prof["filter"])
        entry.setdefault("engine", prof["engine"])
        games[args.gid] = entry
        upscale.save(games)
    print("%s %s" % (entry["render"], entry["output"]))
    return 0


def _upscale_note(args):
    """Record what a run observed (or a manual render/output override) and
    print one human line."""
    from . import upscale
    upscale.note(args.gid, input=args.input, cnn=args.cnn, out=args.out,
                 render=args.render, output=args.output)
    prof = upscale.profile(args.gid)
    rec = upscale.recommend(prof)
    print("upscale: %s input=%s cnn=%s out=%s"
          % (rec["verdict"], prof["observed_input"] or "-",
             prof["observed_cnn"] or "-", prof["output"] or "-"))
    return 0


def _size(value):
    """argparse type for a WxH size."""
    parts = value.lower().split("x")
    if len(parts) != 2 or not all(p.isdigit() and int(p) > 0 for p in parts):
        raise argparse.ArgumentTypeError("expected WxH, got %r" % value)
    return value


def build_parser():
    ap = argparse.ArgumentParser(prog="python3 -m core")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("launch")
    p.add_argument("gid")
    p.set_defaults(fn=_launch)

    p = sub.add_parser("add")
    p.add_argument("--name", required=True)
    p.add_argument("--runner", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--variant", default="L")
    p.add_argument("--gpu", default="auto (discrete GPU preferred)")
    p.add_argument("--fps", default="60")
    p.add_argument("--hud", default="0")
    p.add_argument("--prefix-mode", default="shared")
    p.add_argument("--lang", default="")
    p.add_argument("--tr-enabled", default="0")
    p.add_argument("--tr-hook", default="")
    p.add_argument("--upscale", choices=("0", "1"), default="0")
    p.set_defaults(fn=_add)

    for name, fn in (("set", _set), ("tset", _tset)):
        p = sub.add_parser(name)
        p.add_argument("gid")
        p.add_argument("field")
        p.add_argument("value")
        p.set_defaults(fn=fn)

    p = sub.add_parser("remove")
    p.add_argument("gid")
    p.set_defaults(fn=_remove)

    p = sub.add_parser("gpus")
    p.set_defaults(fn=_gpus)

    p = sub.add_parser("upscale-show")
    p.add_argument("--gid", required=True)
    p.set_defaults(fn=_upscale_show)

    p = sub.add_parser("upscale-render")
    p.add_argument("--gid", required=True)
    p.set_defaults(fn=_upscale_render)

    p = sub.add_parser("upscale-note")
    p.add_argument("--gid", required=True)
    p.add_argument("--input", type=_size)
    p.add_argument("--cnn", type=_size)
    p.add_argument("--out", type=_size)
    p.add_argument("--render", type=_size)
    p.add_argument("--output", type=_size)
    p.set_defaults(fn=_upscale_note)
    return ap


def main():
    args = build_parser().parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
