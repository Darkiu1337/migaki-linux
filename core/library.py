import os
import re


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "game"


def _is_pe(path):
    """True when the file starts with the PE/Windows magic ("MZ")."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def validate_entry(data):
    """None when valid, else a user-facing reason string."""
    if not data.get("path") or not os.path.exists(data["path"]):
        return "That path does not exist."
    if data.get("runner") == "rpgmaker" and not os.path.isdir(data["path"]):
        return ("The rpgmaker runner needs the game folder, not a file.\n"
                "Use Detect (or pick the folder containing www/).")
    if (data.get("runner") == "native" and os.path.isfile(data["path"])
            and _is_pe(data["path"])):
        return ("That's a Windows .exe — the native runner only runs Linux "
                "binaries.\nPick the game's .sh launcher, or use the proton "
                "runner.")
    return None


def normalize_edit(data, prev):
    data["runner"] = prev.get("runner", data.get("runner", "proton"))
    if "wow64" in prev:
        data["wow64"] = prev["wow64"]
    tr_prev = prev.get("translate", {})
    tr = data.setdefault("translate", {})
    tr["hook_mode"] = tr_prev.get("hook_mode", "unknown")
    tr["thread"] = tr_prev.get("thread", "")
    # hook_code/all_hooks are no longer edited in the wizard; keep whatever the
    # session or the TUI recorded so an edit never wipes them.
    tr["hook_code"] = tr.get("hook_code") or tr_prev.get("hook_code", "")
    if not tr.get("all_hooks") and tr_prev.get("all_hooks"):
        tr["all_hooks"] = tr_prev["all_hooks"]
    tr["show_browser"] = tr.get("show_browser") or tr_prev.get("show_browser", "0")
    tr["show_hooker"] = tr.get("show_hooker") or tr_prev.get("show_hooker", "0")
    return data
