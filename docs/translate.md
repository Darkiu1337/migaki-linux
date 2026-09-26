# VN translation

Hooked Japanese dialogue from Proton VNs, translated live via DeepL into a
Luna-style textbox — composed with the Restore filter in a single launch.
`translate/` holds the implementation; this page is the operator manual.

## Flow

1. **Enable** per game: GUI game wizard/edit (Translation page) or
   `migaki edit` → `translate`. Proton/Windows games only.
2. **First run (Setup Text Hooker for translation)**: this GUI button — or
   just pressing **Translate** when no hook is saved yet, which auto-runs
   Setup — launches the game and opens the Text Hooker picker. **Textractor
   stays hidden**; our window is the only interface. The picker waits for the
   bridge (the game must be running), then listens continuously and lists
   every text thread it sees (name, #num, hook address, line count, last
   line). Advance the game text, then click the story thread — that stores it
   as the game's `translate.thread` and daily play follows it by name. (A
   saved `hook_code` works the same way, but the wizard no longer exposes it —
   paste one via the TUI/JSON if you ever have it.) If Textractor's auto-hooks
   find nothing, tick **Show Textractor during Setup (debug)** in the wizard
   and do the hook ladder manually. Both debug toggles live under a **Debug**
   heading on the Translation page and are only enabled once translation is on.
3. **Daily play**: `Translate` (picker never opens; the recorded thread is
   followed). The textbox opens with it. It toggles EN-only / JA+EN. The
   standalone Textbox button just re-opens the reader onto a live session.
   The **Setup Text Hooker for translation** button stays available to change
   the hook.
4. **End**: Stop in the UI (or Ctrl-C); `--stop` also drops the wineserver
   so the next launch boots fresh. Stop ends the whole session: game hooks,
   textbox backend, and its isolated DeepL browser (a backend left running
   would keep translating and re-show on new text).

## Architecture (one picture)

One `umu-run` → `wscript` → per-game `C:\hook\<id>.vbs` starts Textractor
(`/pgame.exe` = attach on boot) + game in a single Wine session (one
wineserver — dual containers serialize and never boot, proven twice).
Auto-attach comes from `SavedHooks.txt`/`SavedGames.txt`, seeded by the
launcher from the game entry (recorded hook auto-inserts; user-saved lines
always win). The v2 bridge streams **every** thread tagged with its
identity (`~#<num>[*]~<addr>~<name>~<text>`, `*` = Textractor's own
selection) on `:6677`; native code filters by thread, translates via Brave CDP
DeepL and displays. Before translation each line passes through an auto-built
**name glossary**, and DeepL may be fed **context** (previous lines) — see
"Translation quality" below.

Filter + translation compose: `vn-launch.sh --filter <variant>` applies the
same vkBasalt mechanism as `proton-migaki.sh`. A/B unfiltered launches stay
untranslated by design.

## Thread picking without Textractor

The v2 bridge tags every sentence with thread number, hook address, and
hook name (stable across sessions). The Text Hooker picker (opened by Setup
Text Hooker for translation, waiting for the bridge first) **listens
continuously**: it streams candidates with their last line as they appear and
keeps updating the list while the game runs, until you pick one or cancel —
there is no sampling timeout. Choosing one stores it
as the game's `translate.thread` and the Textbox/hook_client then follow
that thread by name (falling back to Textractor's selection if unset —
stock-bridge installs simply keep following the selection). Manual control:
`hook_client.py --thread <name|number|hex-addr|*>`, `textbox.py --thread …`,
`vn-translate --thread …`.

## Hook ladder (unknown engine? work down, stop at first clean thread)

1. **Engine hook** (`vnreng: INSERT <name>` at attach): pick it (picker or
   Textractor), remove the generic bulk (see rule below), Save hooks.
2. **Minimal generic set**: no engine hook → keep only `TextOut`/`ExtTextOut`
   (+ `W`/`A` as needed); add hooks only while silent.
3. **Junk filters**: Textractor's Remove Repeated Characters/Phrases + Regex
   Filter for GDI noise.
4. **Hook search last**: crash-prone — in-game save first.

## Crash doctrine (proven across sessions, do not rediscover)

* **Keep the extension set stripped: bridge only.** Proven live: with the
  full stock set (Google Translate ext ON, target Tajik) the hook fired in
  Textractor yet the bridge stayed totally silent for minutes; unchecking
  everything but `textractor_websocket` restored tagged traffic instantly.
  A loaded translate ext can stall the whole sentence pipeline, so translate
  in the textbox, never in Textractor. Textractor loads six stock extensions
  whenever `SavedExtensions.txt` is missing, so provisioning **always forces**
  the bundled bridge-only file (`translate/textractor-config/SavedExtensions.txt`)
  and `vn-launch.sh` re-forces it before every session — a stale or missing
  file can never re-enable the stock set.
* **Non-ASCII game paths.** Wine's `wscript` reads ASCII/ANSI `.vbs` only —
  a UTF-16 template silently does nothing. The renderer keeps the file ASCII
  and emits non-ASCII path characters as `ChrW(&hXXXX)` concatenations, so
  paths like `Z:\home\dd\Área de trabalho\...` work.
* **Remove, don't deselect.** Unselected hooks stay inserted and keep
  processing. Four crashes with the GDI bulk inserted, zero Anim3-only —
  but bulk is *not* universally fatal (one title stable with everything),
  so removal is remedy, not ritual. Record per-game behavior.
* **cwd decides engine detection.** Launchers must set each program's working
  directory (VBS `CurrentDirectory`); without it, engine hooks don't insert.
  `WScript.Shell.CurrentDirectory` is unreliable for non-ASCII paths, so
  `vn-launch.sh` also `cd`s into the game dir before `umu-run` (the VBS set is
  best-effort, wrapped in `On Error Resume Next`).
* **Never bare-TCP `:6677`.** The stock bridge panics the host on
  non-handshake connections (and on abrupt disconnects); every health check
  must complete a real websocket handshake. The hardened fork (default)
  degrades gracefully instead.
* **Registered `.xdll`, not `.dll`.** Textractor loads the renamed copy —
  install any bridge build under BOTH filenames or sessions silently run
  the other one.
* **SavedHooks/SavedGames lines must be bare-LF/CR-stripped**: upstream
  exact-matches them against process paths; a stray `\r` (Wine CRLF
  defaults) silently disables auto-attach. The launcher normalizes both.
* **Stale wineserver wedges new containers** (launcher exits silently, nothing
  spawns). The shipped launcher blocks on the *game*, `--stop` also drops the
  server, the GUI refuses double-launches and offers Stop && Launch /
  Clear && Launch on live/wedged state. Always stop before relaunching.
* **One ws client besides Textractor itself.** Extra ad-hoc taps raise crash
  odds on stock; the fixed bridge tolerates them.
* Textractor's `SavedHooks.txt` writes survive clean exits; the library
  (`translate.hook_code`, auto-harvested) is the portable source of truth.

## Bridge builds

* **Stock** kuroahna 0.2.0 (upstream, MIT/Apache): works, fragile (see
  above); only the Textractor-selected thread flows, untagged.
* **Fixed fork v2** (default): host-safety patches (no panics,
  non-blocking lossy host send, 256-deep drain-all queue, double-init
  tolerated) **plus** the thread-tagged broadcast that powers the native
  picker. A/B proven. Rebuild from `translate/bridge-fork.patch`
  (rustup stable + `i686-pc-windows-gnu` + mingw-w64-gcc; the fork's
  `textractor.rs` decodes `"text name"` as UTF-16 — upstream passes a
  `wchar_t*`); the prebuilt DLL is used from the `translate-v2` release asset
  when published, else the stock build — sha256-verified at install.

## DeepL browser (hidden by default)

Translation drives deepl.com through an isolated Chromium/Brave profile over
CDP. The browser runs **headless** (`--headless=new`), so no window appears and
nothing steals focus. Per game, the Translation page has **Show the DeepL
browser window (debug)** — tick it to watch the automation (remembered per
game; both Translate and Setup Text Hooker honour it). Globally, set
`"browser_hidden": false` in `translate/config.json`. The browser is
single-instance and reused while its debug port is live, so stop the session
before a visibility change takes effect.

Headless is hidden from you but detectable by DeepL, which shows its
`clearance.deepl.com` "Checking if the connection is secure" widget; that
widget steals focus but **cannot** stop the translator: the launcher masks the
headless markers (a normal desktop `--user-agent`, no
`AutomationControlled` feature), renders on the real GPU (`--enable-gpu`;
headless otherwise forces SwiftShader) with timers unthrottled, and sets the
source text plus dispatches `input` events **without relying on focus**, so
DeepL translates underneath the overlay. (Focus-based typing used to trigger
a per-call page reload — that latency regression is gone: steady state is a
few hundred ms.) If DeepL ever blocks that too, flip
`browser_hidden`/the checkbox (visible = a real window) as the escape hatch.

It keeps exactly **one** DeepL tab: the launcher reuses an existing tab,
closes any extras, and purges stale session state before a fresh start, so a
new session never reopens a pile of DeepL pages. On Stop — and when the
textbox window closes — the browser closes its tabs and exits cleanly
(`Browser.close`) instead of being killed, so it never session-restores.
Both the purge and the close are guarded to the isolated automation profile:
a browser that wasn't started with that `--user-data-dir` is never touched,
and the real browser profile is never read or modified.

## Translation quality: name glossary + context

Sentence-level MT gets two things wrong that the game itself can fix: **names**
and **who is who**.

**Name glossary** (`translate/names.py`). Many engines emit a furigana reading
line right after a name is introduced (「小花衣凛桜」 then 「こはないりお」 →
*Kohanai Rio*). The pipeline harvests those readings, `名前「…」` speaker tags
and ruby patterns, and pins one English rendering per Japanese name. Names with
no reading are *learned* from the engine's own first rendering (彰之 →
"Akiyuki"). Known names are masked with private-use sentinels before DeepL and
restored after, so they stop drifting — "Rinoh" / "Rinohara" / "Rin Sakura" all
become "Rio". Reading lines are consumed for the glossary and never sent to
DeepL (they made it hallucinate). The glossary is per game, auto-built and
editable at `~/.config/migaki/glossary/<game>.json`.

**Context** (`context_lines`, default 6). The previous N lines are prepended to
the DeepL input and only the last output line is kept. This fixes discourse a
single sentence cannot resolve (who "she" is, よろしく, 彼女が欲しかった). It
costs latency (~2–4 s/line vs ~1 s) and **characters**: each line sends roughly
(N+1)× the text, which counts against DeepL's allowance. Set `context_lines` to
0 to disable it.

Both levers are independent and model-agnostic: the glossary fixes names, the
context fixes discourse. `translate/clean.py` adds optional pre-translation
hygiene (punctuation, zero-width/control characters, ruby).

## Textbox window behavior (Float + Click + Top)

* **Click** = click-through. Clicks on the text area fall through to the game
  below; hovering either bar (titlebar or toolbar) restores full input so
  Top/Click stay clickable and the window stays draggable. (Technical note:
  click-through is a bars-only surface input mask; `Qt.WindowTransparentForInput`
  is deliberately never set — while it is set, Qt silently drops every mask
  update. Both facts verified at the Wayland protocol level.)
* **Float** is enforced unconditionally (an overlay must never tile). Before
  the window maps, `translate/placement.py` installs a Hyprland float rule
  (`hyprctl eval`, title `^vn-translate$`: `float`, `persistent_size`,
  `no_blur`, and the saved position) so the first map is already floating at
  the last geometry — no tiled flash. `no_blur` opts the overlay out of
  Hyprland's global backdrop blur, which would otherwise frost the
  translucent panel. A ~1s poller still re-floats it if something tiles it later,
  independent of Top. No config change needed (Hyprland ≥ 0.55).
* **Geometry is compositor-agnostic.** The QML window starts hidden, so
  `restore_state()` runs before the first map and Qt's `saveGeometry` /
  `restoreGeometry` restores the size on any compositor (X11, KDE, GNOME, …).
  Wayland gives clients no way to set their position, so on Hyprland the
  adapter also captures the compositor's `at`/`size` on close
  (`compositor_geometry` in the textbox settings) and feeds it back through
  the float rule; on compositors without a rule API (e.g. GNOME/KDE) the
  position stays compositor-chosen — size is still restored.
* **Top is compositor-specific but always stacking-only** (see the next
  section):
  * **Hyprland** — pinned to the box's workspace, above everything there. Qt's
    stay-on-top hint is ignored, so a ~1s poller enforces it: pinned while Top
    is on *and* you're on the box's workspace, unpinned everywhere else (stays
    put, normal stacking, freely movable — including Top-off state). Leaving
    drags it along once (pin mechanics), then it unpins and simply stays where
    it landed — it is deliberately never moved back, because moving a window
    makes the compositor flip the active workspace to follow it, which fights
    you in a loop. Coming back repins it into view. Moving it by hand adopts
    the new workspace as home (follow-residue can never fake a move: adoption
    needs a workspace edge while unpinned; re-arm any time with a Top toggle).
    Every new translated line also raises it (no focus steal).
  * **KDE Plasma (Wayland)** — a small KWin script sets the documented
    `Window.keepAbove` property. It is loaded/unloaded over the
    `org.kde.KWin /Scripting` D-Bus interface and lives in
    `~/.cache/migaki/kwin-vn-textbox.js`; no user config is touched.
  * **GNOME (Wayland)** — no client API exists. A bundled GNOME Shell
    extension (only if installed/enabled by `install.sh`) reads a state file
    and calls `Meta.Window.make_above()`. Without the extension, Top reports
    *unsupported* instead of faking it with focus.
  * **X11** (XFCE, KDE X11, GNOME X11, i3, …) — Qt's `WindowStaysOnTopHint`
    (`_NET_WM_STATE_ABOVE`) restacks natively; no helper needed.
  * **Sway / unknown Wayland** — normal xdg windows have no keep-above, so
    only Float applies; Top is reported unsupported.
* Corner rounding follows the compositor (`decoration:rounding` on Hyprland;
  a Style override is available everywhere).

### Compositor control safety (never regress this)

Top/z-order enforcement **must never focus or activate a window, move/resize
it, or touch the pointer.** It may only change the stacking order. This is not
cosmetic: under KDE's *Focus follows mouse*, activating a window makes KWin
warp the cursor onto it, so any "raise it by focusing it" implementation makes
the mouse jump to the textbox on every poll. The same rule keeps the overlay
from stealing keyboard focus from the game.

Concretely, these are forbidden (and the self-test asserts they never appear):

* Hyprland: `focuswindow`, `movecursor`, `movewindow`, `resizewindow`,
  `dispatch focus` — allowed: `pin`, `float`, `bring_to_top` only.
* KWin script: `workspace.activeWindow = …`, `w.activate()`, `w.geometry = …`,
  `raiseWindow` — allowed: `w.keepAbove` only.
* GNOME: `metaWindow.activate()`, `Main.activateWindow()`, `warp_pointer`,
  `move_frame`/`move_resize_frame` — allowed: `make_above()` only.
* X11: `activateWindow()`, `wmctrl -a`, `xdotool windowactivate/mousemove`,
  `QCursor.setPos` — allowed: the keep-above hint / restack only.
* Textbox: never toggle window flags on a mapped Wayland window (`setFlags()`
  re-creates it — a focus-steal vector); flags are set once on Wayland.

`translate/placement.py` allow-lists only stacking verbs and drops anything
else; every command is appended to `~/.cache/migaki/textbox.log` for
diagnosis.

### Picking a hook without restarting (live)

The textbox is spawned with `--gameid`, so it watches the game's
`translate.thread` in the shared store. When the Setup picker saves a thread,
the running backend re-filters within ~1s (`hook_client` re-evaluates the
selector per message) — the picked hook starts translating immediately, no
stop/relaunch. Re-picking via **Setup Text Hooker for translation** mid-session
switches live too.

## Textbox style

`translate/textbox.py` is a Qt Quick readout: same pipeline and window
behavior as always, but text styling binds live — font size/family/color
changes restyle the whole history, including existing lines. Window chrome
(bars, panel, drawer and the default text colours) follows the desktop
colour scheme through the KDE Quick Controls style, same as the launcher
GUI. The **Style**
toolbar button opens a drawer with font, size, EN/JA colors, a soft text
**shadow** (GPU halo that keeps text selectable), background **opacity**,
top/bottom-bar **autohide** (bars reveal when the cursor enters their edge
strips), and corner rounding (follows the compositor's
`decoration:rounding`, override in the drawer). All prefs persist in the
textbox settings store. The launcher refuses a second backend (two would
fight over one DeepL page); closing the window quits it, so it can never
resurrect on new text. There is no scrollbar — the view sticks to the
bottom and scrolls when you scroll up.

## Per-game notes

* **mlove** (Anim engine): hook `HSX10@54DC0:mlove.exe`, story thread
  `Anim3` (addr `454DC0`). Anim3-only required (GDI bulk crashed 4×).
  Reference title.
* **mmg / Start.exe** (Atelier KAGUYA2/6 engine hooks): generic-ladder
  title, bulk-tolerant so far.

## Install behavior

`install.sh` offers translation support (default Yes): fetches the pinned
Textractor bundle + bridge (fixed v2 asset preferred, stock fallback),
provisions Textractor **once per machine** under
`~/.local/share/migaki/textractor` (applying the bundled
`translate/textractor-config/` config), symlinks each Wine prefix's
`drive_c/Textractor` to it, and symlinks `vn-launch` / `vn-textbox` /
`vn-translate` into `~/.local/bin`. `vn-launch.sh` re-provisions/links on
demand, so a prefix umu only creates on first launch is covered too — and it
forces the bridge-only extension set every session.
Settings come from `translate/config.json` and the games registry from
`translate/translate.json` — both seeded from their `.sample` files on
first install (never overwritten); the Python entry points also start on a
bare clone by falling back to the samples/builtins. `vn-launch.sh` resolves the shared
`~/.config/migaki/config.json` `proton` value the same way as the filter
runner: an absolute path (or a bare name looked up under
`compatibilitytools.d`); otherwise it falls back to the umu-managed
UMU-Proton. `requirements.md` lists every dependency per
distro. `install.sh --check-only` audits the translate deps too.

`install.sh` also detects the session for the textbox **Top** support: X11 is
native, Hyprland needs `hyprctl`, KDE needs `qdbus6` (both normally present),
and on GNOME Wayland it offers to deploy+enable the bundled Shell extension
`vn-textbox-top@migaki` under
`~/.local/share/gnome-shell/extensions/` (a re-login may be required on
Wayland). Sway/unknown Wayland is reported unsupported. `migaki doctor` prints
the detected backend.

## Limits

* Proton/Windows games only (hook injection needs Wine + one shared session).
* One live session at a time (shared prefix design).
* The Text Hooker needs live text: run Setup Text Hooker for translation and
  advance the game while it listens (it keeps listening until you pick or
  cancel). Thread picking by name needs the v2 bridge — stock installs follow
  Textractor's selection instead.
* **DeepL's free web translator has a 30-day character cap.** Heavy automated
  use — or context injection, which sends ~7× the text — can hit it, after
  which the web translator is blocked until it resets. Context is the expensive
  part; the name glossary is roughly free (a name becomes one character).
* **A Cloudflare challenge can stall requests** (uniform ~10–30 s lines, or
  "DeepL source editor never appeared"). Running the automation browser
  **visible** (`browser_hidden: false`, or the per-game "Show the DeepL browser
  window" checkbox) clears it — a real window passes, headless does not.
* DeepL runs only through the isolated browser (CDP); a browser failure
  reports a short "DeepL unavailable — retrying" line.
* **Clean up after tests.** A session that isn't stopped cleanly leaves
  orphaned Wine/Proton services under `systemd --user` (`services.exe`,
  `winedevice.exe`, `svchost.exe`, `plugplay.exe`, `explorer.exe /desktop`,
  `rpcss.exe`, `tabtip.exe`) and stale `/tmp/.wine-*/server-*` lock dirs. Kill
  them after testing (`pgrep -af 'C:\windows'`, then `kill`/`kill -9`) and
  remove the stale wine dirs once `pgrep -x wineserver` is empty, so the next
  launch starts clean.
