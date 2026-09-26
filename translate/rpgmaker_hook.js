// Migaki RPGMaker MV/MZ live text hook.
//
// Injected by the rpgmaker runner via the wrapper's plugins_autoload dir
// (nwjs/nwjs/packagefiles/jspatches/plugins_autoload, executed in the page by
// menu.js ~400ms after load). The game's NW.js build is the normal (non-SDK)
// flavour, which has no remote debugging / CDP, so the text is captured in the
// page itself: Window_Message.startMessage reads $gameMessage.allText(), we
// strip the draw codes and push the line to the native ws bridge the textbox
// already listens on (:6677).
//
// Not a plugin header (no /*: ... */): executeScript must not register it.
(function () {
    "use strict";

    var WS_URL = "ws://127.0.0.1:6677";
    var ADDR = "5250474D"; // "RPGM"
    var NAME = "RPGMaker";
    var last = "";
    var ws = null;

    function connect() {
        try {
            ws = new WebSocket(WS_URL);
        } catch (e) {
            setTimeout(connect, 3000);
            return;
        }
        ws.onclose = function () { ws = null; setTimeout(connect, 3000); };
        ws.onerror = function () {};
    }
    connect();

    function emit(text) {
        if (!text) return;
        text = text.replace(/\s+/g, " ").trim();
        if (!text || text === last) return;
        last = text;
        try {
            if (ws && ws.readyState === 1) {
                ws.send("~#1*~" + ADDR + "~" + NAME + "~" + text);
            }
        } catch (e) {}
    }

    // convertEscapeCharacters already resolved \V[n]/\N[n]/\P[n]/\G and turned
    // the remaining backslash codes into \x1b (ESC). Drop those draw codes.
    function clean(raw) {
        var t = String(raw == null ? "" : raw);
        t = t.replace(/\x1bC\[\d+\]/gi, " ");
        t = t.replace(/\x1bI\[\d+\]/gi, " ");
        t = t.replace(/\x1bFS\[\d+\]/gi, " ");
        t = t.replace(/\x1b[A-Za-z]+\[[^\]]*\]/g, " ");
        t = t.replace(/\x1b[{}|!.$^<>G]/g, "");
        t = t.replace(/\x1b/g, "");
        t = t.replace(/[\n\f\r]+/g, " ");
        return t.replace(/\s+/g, " ").trim();
    }

    function install() {
        if (window.__migakiRpg) return true;
        var WM = window.Window_Message;
        if (!WM || !WM.prototype || typeof WM.prototype.startMessage !== "function") {
            return false;
        }
        var orig = WM.prototype.startMessage;
        WM.prototype.startMessage = function () {
            try {
                var gm = window.$gameMessage;
                if (gm && typeof gm.allText === "function") {
                    emit(clean(this.convertEscapeCharacters(gm.allText())));
                }
            } catch (e) {}
            return orig.apply(this, arguments);
        };
        window.__migakiRpg = true;
        try { console.log("migaki: RPGMaker text hook installed"); } catch (e) {}
        return true;
    }

    if (!install()) {
        var iv = setInterval(function () {
            if (install()) clearInterval(iv);
        }, 500);
    }
})();
