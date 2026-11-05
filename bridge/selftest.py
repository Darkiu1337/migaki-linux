"""--self-test assertions for the bridge (the gpui frontend's --self-test adds
the GUI/window side)."""
import os
import tempfile

from core import paths, store


def run(be):
    snap = be.snapshot()
    assert isinstance(snap["games"], list), "games must be a list"
    assert isinstance(snap["variants"], list) and snap["variants"], \
        "variants must be a non-empty list"
    assert isinstance(snap["gpus"], list) and snap["gpus"], \
        "gpus must be a non-empty list"
    protons = snap["protons"]
    assert protons and protons[0]["value"] == "", \
        "proton list must start with the umu-managed option"
    assert all("wow64" in p for p in protons), "proton entries need a wow64 flag"
    assert snap["runners"].get("proton"), "runners must describe the proton runner"
    assert isinstance(snap["fonts"], list) and snap["fonts"], \
        "the system font list must not be empty"
    assert isinstance(snap["locales"], list) and snap["locales"][0] == "System default"

    assert be.preview("__no_such_game__")["command"] == "", \
        "an unknown game has no command"
    assert be.details("__no_such_game__") == {"name": "", "lines": ["Select a game."]}
    assert be.game_data("__no_such_game__") == {}
    assert be.validate({})["error"] != "", "an empty entry must not validate"
    assert be.validate({"name": "x", "runner": "proton", "path": "/nope"})["error"] != ""
    assert be.launch("__no_such_game__", False)["result"] == "error"
    assert be.translate("__no_such_game__", False)["result"] == "error"
    assert be.threads_pick("__no_such_game__")["result"] == "Unknown game."
    rec = be.reconcile("", "native")
    assert set(rec) == {"runner", "target", "severity", "message"}, \
        f"reconcile keys: {sorted(rec)}"
    det = be.detect("/nonexistent/migaki-self-test")
    assert det == {"detected": None} or "engine" in det["detected"], \
        "detect must return a record or null"

    # Library round-trip in a throwaway games.json (MIGAKI_GAMES_JSON override).
    orig = paths.GAMES_JSON
    paths.GAMES_JSON = os.path.join(tempfile.mkdtemp(), "games.json")
    try:
        assert store.load_games() == {}
        gid = store.new_game(store.load_games(),
                             {"name": "Unit Test", "runner": "native",
                              "path": "/bin/true"})
        assert gid == "unit-test", f"new_game id: {gid}"
        assert "unit-test" in store.load_games(), "new_game must persist"
        store.remove_game(gid)
        assert store.load_games() == {}, "remove_game must persist"
    finally:
        paths.GAMES_JSON = orig
