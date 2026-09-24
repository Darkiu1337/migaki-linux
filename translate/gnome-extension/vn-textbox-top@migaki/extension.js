// vn-textbox-top@migaki
//
// Keeps the migaki translation textbox ("vn-translate") above other windows
// on GNOME/Wayland, where no client always-on-top API exists.
//
// SAFETY: stacking order only. This extension must NEVER focus/activate a
// window or touch the pointer (see docs/translate.md "Compositor control
// safety"); it only calls Meta.Window.make_above()/unmake_above().
//
// State: ~/.cache/migaki/gnome-vn-textbox-top holds "1" (Top on) or "0"
// (Top off); the textbox writes it when the Top button toggles.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const TITLE = 'vn-translate';
const POLL_MS = 1000;

function stateFile() {
    return GLib.build_filenamev([
        GLib.get_user_cache_dir(), 'migaki', 'gnome-vn-textbox-top']);
}

function wantTop() {
    try {
        const file = Gio.File.new_for_path(stateFile());
        const [, bytes] = file.load_contents(null);
        return new TextDecoder().decode(bytes).trim() === '1';
    } catch (e) {
        return false;
    }
}

export default class VnTextboxTopExtension extends Extension {
    enable() {
        this._createdId = global.display.connect('window-created', () => {
            this._apply();
        });
        this._timeoutId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, POLL_MS, () => {
                this._apply();
                return GLib.SOURCE_CONTINUE;
            });
        this._apply();
    }

    disable() {
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = 0;
        }
        if (this._createdId) {
            global.display.disconnect(this._createdId);
            this._createdId = 0;
        }
    }

    _apply() {
        const want = wantTop();
        for (const actor of global.get_window_actors()) {
            const win = actor.meta_window;
            if (!win)
                continue;
            let title = '';
            try {
                title = win.get_title() || '';
            } catch (e) {
                continue;
            }
            if (title !== TITLE)
                continue;
            // Stacking only — never activate()/warp_pointer.
            if (want)
                win.make_above();
            else
                win.unmake_above();
        }
    }
}
