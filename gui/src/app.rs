//! Application state: one window, one bridge, one modal at a time.
use std::collections::HashMap;
use std::ops::Range;
use std::sync::{Arc, Mutex};

use gpui::prelude::*;
use gpui::{
    App, Bounds, ClipboardItem, Context, EntityInputHandler, FocusHandle, Focusable, KeyDownEvent,
    MouseButton, Pixels, Point, Render, ScrollHandle, SharedString, UTF16Selection, Window, div,
    px,
};
use serde_json::{Value, json};

use crate::bridge::Bridge;
use crate::theme::Theme;
use crate::ui::combo::ComboState;
use crate::ui::text_field::{
    FieldState, offset_from_utf16, offset_to_utf16, range_from_utf16, range_to_utf16,
};
use crate::views::prompt::{PromptKind, PromptState};
use crate::views::settings::SettingsState;
use crate::views::threads::ThreadsState;
use crate::views::wizard::WizardState;

/// A library row (the `Game` shape the bridge emits).
#[derive(Clone, Debug, Default)]
pub struct Game {
    pub gid: String,
    pub name: String,
    pub info: String,
    pub icon: String,
}

/// The detail pane for the selected entry.
#[derive(Clone, Debug, Default)]
pub struct Detail {
    pub name: String,
    pub lines: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Proton {
    pub label: String,
    pub value: String,
    pub wow64: bool,
}

/// Every editable line in the app; the input handler routes by this.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FieldId {
    WizardPath,
    WizardName,
    WizardFps,
    WizardRender,
    SettingsPrefix,
    SettingsLayer,
    SettingsFontSize,
}

impl FieldId {
    pub fn is_numeric(self) -> bool {
        matches!(self, FieldId::WizardFps | FieldId::SettingsFontSize)
    }

    /// Digits plus the "x" separator: the upscale render size.
    pub fn is_size(self) -> bool {
        matches!(self, FieldId::WizardRender)
    }
}

pub enum Modal {
    None,
    Prompt(PromptState),
    Preview(String),
    Threads(ThreadsState),
    Settings(SettingsState),
    Wizard(WizardState),
}

/// Which dropdown a click or key targets: only one list is open per modal, so
/// the toggle needs to know its siblings.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ComboKind {
    WizardVariant,
    WizardUpscaler,
    WizardGpu,
    WizardLang,
    SettingsProton,
    SettingsFont,
}

pub struct MainView {
    pub theme: Theme,
    pub bridge: Option<Bridge>,
    pub bridge_error: Option<String>,
    pub games: Vec<Game>,
    pub icons: HashMap<String, String>,
    pub selected: Option<String>,
    pub detail: Detail,
    pub status: String,
    pub bridge_up: bool,
    pub running: Option<String>,
    pub log: Vec<String>,
    pub log_scroll: ScrollHandle,
    pub modal: Modal,
    pub focus: FocusHandle,
    pub variants: Vec<String>,
    pub variant_notes: HashMap<String, String>,
    /// Upscalers the bridge offers: `(value, label)` in display order.
    pub upscale_filters: Vec<(String, String)>,
    pub gpus: Vec<String>,
    pub display_vendors: Vec<String>,
    pub locales: Vec<String>,
    pub protons: Vec<Proton>,
    pub runners: Vec<(String, String)>,
    pub fonts: Vec<String>,
    pub settings: Value,
    pub last_dir: String,
    pub theme_cfg: Arc<Mutex<(String, i64)>>,
}

/// Keep only string members (the bridge always emits strings).
fn strings(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn text_field_value<'a>(value: &'a Value, key: &str) -> &'a str {
    value.get(key).and_then(Value::as_str).unwrap_or_default()
}

impl MainView {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let theme_cfg = Arc::new(Mutex::new((String::new(), 0i64)));
        let mut view = MainView {
            theme: Theme::load("", 0),
            bridge: None,
            bridge_error: None,
            games: Vec::new(),
            icons: HashMap::new(),
            selected: None,
            detail: Detail { name: String::new(), lines: vec!["Select a game.".into()] },
            status: "Idle.".into(),
            bridge_up: false,
            running: None,
            log: Vec::new(),
            log_scroll: ScrollHandle::new(),
            modal: Modal::None,
            focus: cx.focus_handle(),
            variants: Vec::new(),
            variant_notes: HashMap::new(),
            upscale_filters: Vec::new(),
            gpus: Vec::new(),
            display_vendors: Vec::new(),
            locales: Vec::new(),
            protons: Vec::new(),
            runners: Vec::new(),
            fonts: Vec::new(),
            settings: json!({}),
            last_dir: String::new(),
            theme_cfg: theme_cfg.clone(),
        };

        match crate::root::find().and_then(|root| Bridge::spawn(&root)) {
            Ok(bridge) => view.bridge = Some(bridge),
            Err(err) => {
                let detail = err.to_string();
                view.bridge_error = Some(detail.clone());
                view.status = format!("Bridge unavailable: {detail}");
                if let Ok(text) = std::fs::read_to_string(crate::bridge::log_path()) {
                    let tail: Vec<&str> = text.lines().collect();
                    let start = tail.len().saturating_sub(20);
                    view.log.extend(tail[start..].iter().map(|line| (*line).to_string()));
                }
            }
        }

        let (theme_tx, theme_rx) = async_channel::bounded(4);
        crate::theme::spawn_poller(theme_tx, theme_cfg);
        cx.spawn_in(window, async move |this, cx| {
            while let Ok(theme) = theme_rx.recv().await {
                let stop = this
                    .update_in(cx, |this, _window, cx| {
                        this.theme = theme;
                        cx.notify();
                    })
                    .is_err();
                if stop {
                    break;
                }
            }
        })
        .detach();

        view.start_event_loop(window, cx);
        view.request_init(window, cx);
        window.focus(&view.focus);
        view
    }

    // -- bridge plumbing ---------------------------------------------------

    fn start_event_loop(&self, window: &Window, cx: &mut Context<Self>) {
        let Some(bridge) = self.bridge.as_ref() else {
            return;
        };
        let events = bridge.events.clone();
        cx.spawn_in(window, async move |this, cx| {
            while let Ok(value) = events.recv().await {
                let stop = this
                    .update_in(cx, |this, window, cx| {
                        this.handle_event(&value, window, cx);
                        cx.notify();
                    })
                    .is_err();
                if stop {
                    break;
                }
            }
        })
        .detach();
    }

    pub(crate) fn rpc<F>(
        &self,
        window: &Window,
        cx: &mut Context<Self>,
        method: &str,
        params: Value,
        handler: F,
    )
    where
        F: FnOnce(&mut Self, Result<Value, String>, &mut Window, &mut Context<Self>) + 'static,
    {
        let Some(bridge) = self.bridge.as_ref() else {
            return;
        };
        let rx = bridge.request(method, params);
        cx.spawn_in(window, async move |this, cx| {
            let reply = match rx.recv().await {
                Ok(reply) => reply,
                Err(_) => Err("bridge channel closed".to_string()),
            };
            let _ = this.update_in(cx, |this, window, cx| {
                handler(this, reply, window, cx);
                cx.notify();
            });
        })
        .detach();
    }

    fn request_init(&mut self, window: &Window, cx: &mut Context<Self>) {
        let pid = std::process::id();
        self.rpc(window, cx, "init", json!({"ui_pid": pid}), |this, reply, window, cx| {
            match reply {
                Ok(value) => this.apply_init(&value, window, cx),
                Err(err) => {
                    this.status = format!("Bridge unavailable: {err}");
                }
            }
        });
    }

    fn apply_init(&mut self, value: &Value, window: &mut Window, cx: &mut Context<Self>) {
        self.settings = value.get("settings").cloned().unwrap_or_else(|| json!({}));
        self.apply_theme_from_settings();
        self.variants = strings(value.get("variants"));
        if let Some(notes) = value.get("variant_notes").and_then(Value::as_object) {
            self.variant_notes = notes
                .iter()
                .map(|(k, v)| (k.clone(), v.as_str().unwrap_or_default().to_string()))
                .collect();
        }
        self.upscale_filters = value
            .get("upscale_filters")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| {
                        (
                            text_field_value(item, "value").to_string(),
                            text_field_value(item, "label").to_string(),
                        )
                    })
                    .filter(|(value, _)| !value.is_empty())
                    .collect()
            })
            .unwrap_or_default();
        self.gpus = strings(value.get("gpus"));
        self.display_vendors = strings(value.get("display_vendors"));
        self.locales = strings(value.get("locales"));
        self.fonts = strings(value.get("fonts"));
        self.last_dir = text_field_value(value, "last_dir").to_string();
        self.status = text_field_value(value, "status").to_string();
        self.bridge_up = value.get("bridge_up").and_then(Value::as_bool).unwrap_or(false);
        self.running = value
            .get("running")
            .and_then(Value::as_str)
            .map(str::to_string);
        self.protons = value
            .get("protons")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| Proton {
                        label: text_field_value(item, "label").to_string(),
                        value: text_field_value(item, "value").to_string(),
                        wow64: item.get("wow64").and_then(Value::as_bool).unwrap_or(true),
                    })
                    .collect()
            })
            .unwrap_or_default();
        self.runners = value
            .get("runners")
            .and_then(Value::as_object)
            .map(|map| {
                ["proton", "rpgmaker", "native"]
                    .iter()
                    .filter_map(|key| {
                        map.get(*key)
                            .and_then(Value::as_str)
                            .map(|desc| ((*key).to_string(), desc.to_string()))
                    })
                    .chain(
                        map.iter()
                            .filter(|(k, _)| !["proton", "rpgmaker", "native"].contains(&k.as_str()))
                            .map(|(k, v)| (k.clone(), v.as_str().unwrap_or_default().to_string())),
                    )
                    .collect()
            })
            .unwrap_or_default();
        let games = parse_games(value.get("games"));
        if self.selected.is_none() {
            self.log.clear();
        }
        self.adopt_games(games, window, cx);
        self.sync_gpu_dropdown();
    }

    /// Refill an open wizard's GPU dropdown after the list arrives (init or a
    /// `gpus` event); the trigger must never keep a stale/empty list.
    pub(crate) fn sync_gpu_dropdown(&mut self) {
        match std::mem::replace(&mut self.modal, Modal::None) {
            Modal::Wizard(mut state) => {
                state.refresh_gpus(self);
                self.modal = Modal::Wizard(state);
            }
            other => self.modal = other,
        }
    }

    /// Rebuild the theme with the app's own font override from config.json.
    pub(crate) fn apply_theme_from_settings(&mut self) {
        let family = self
            .settings
            .get("gui.font")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let size = self
            .settings
            .get("gui.font_size")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        if let Ok(mut cfg) = self.theme_cfg.lock() {
            *cfg = (family.clone(), size);
        }
        self.theme = Theme::load(&family, size);
    }

    fn handle_event(&mut self, value: &Value, window: &mut Window, cx: &mut Context<Self>) {
        let name = text_field_value(value, "event");
        match name {
            "log" => self.push_log(text_field_value(value, "text")),
            "log_clear" => self.log.clear(),
            "status" => {
                self.status = text_field_value(value, "text").to_string();
            }
            "bridge" => {
                self.bridge_up = value.get("up").and_then(Value::as_bool).unwrap_or(false);
            }
            "running" => {
                self.running = value
                    .get("gid")
                    .and_then(Value::as_str)
                    .map(str::to_string);
            }
            "prompt" => {
                self.modal = Modal::Prompt(PromptState {
                    kind: PromptKind::Backend,
                    title: text_field_value(value, "title").to_string(),
                    body: text_field_value(value, "text").to_string(),
                    buttons: strings(value.get("buttons")),
                });
            }
            "games" => {
                let games = parse_games(value.get("games"));
                self.adopt_games(games, window, cx);
            }
            "icons" => {
                if let Some(items) = value.get("items").and_then(Value::as_object) {
                    for (gid, path) in items {
                        if let Some(path) = path.as_str() {
                            self.icons.insert(gid.clone(), path.to_string());
                        }
                    }
                }
            }
            "gpus" => {
                self.gpus = strings(value.get("items"));
                if let Modal::Settings(state) = &mut self.modal {
                    state.gpu_status = "GPU list refreshed.".to_string();
                }
                self.sync_gpu_dropdown();
                cx.notify();
            }
            "threads" => {
                if let Modal::Threads(state) = &mut self.modal {
                    state.apply(value.get("data").cloned().unwrap_or(Value::Null));
                }
            }
            "setup_launched" => {
                let gid = text_field_value(value, "gid").to_string();
                self.open_threads(gid, true, window, cx);
            }
            "picked" => {
                let path = text_field_value(value, "path").to_string();
                if !path.is_empty() {
                    match std::mem::replace(&mut self.modal, Modal::None) {
                        Modal::Wizard(mut state) => {
                            state.path_chosen(self, &path, window, cx);
                            self.modal = Modal::Wizard(state);
                        }
                        other => self.modal = other,
                    }
                }
            }
            "bridge_down" => {
                let detail = text_field_value(value, "detail").to_string();
                self.bridge = None;
                self.bridge_error = Some(detail.clone());
                self.status = format!("Bridge unavailable: {detail}");
                if let Ok(text) = std::fs::read_to_string(crate::bridge::log_path()) {
                    let tail: Vec<&str> = text.lines().collect();
                    let start = tail.len().saturating_sub(20);
                    for line in &tail[start..] {
                        self.log.push((*line).to_string());
                    }
                }
            }
            _ => {}
        }
    }

    // -- log ---------------------------------------------------------------

    fn at_log_bottom(&self) -> bool {
        let max = self.log_scroll.max_offset().height;
        self.log_scroll.offset().y <= px(0.) - max + px(2.)
    }

    pub fn push_log(&mut self, text: &str) {
        let stick = self.at_log_bottom();
        for line in text.split('\n') {
            if line.is_empty() {
                continue;
            }
            self.log.push(line.to_string());
        }
        if self.log.len() > 5000 {
            let drop = self.log.len() - 5000;
            self.log.drain(0..drop);
        }
        if stick {
            self.log_scroll.scroll_to_bottom();
        }
    }

    // -- library -----------------------------------------------------------

    fn adopt_games(&mut self, games: Vec<Game>, window: &Window, cx: &mut Context<Self>) {
        self.games = games;
        let keep = self
            .selected
            .as_ref()
            .and_then(|gid| self.games.iter().position(|game| &game.gid == gid));
        let target = keep.or(if self.games.is_empty() { None } else { Some(0) });
        let next_gid = target.map(|ix| self.games[ix].gid.clone());
        if next_gid != self.selected {
            self.select_index(target, window, cx);
        }
    }

    pub fn select_index(
        &mut self,
        index: Option<usize>,
        window: &Window,
        cx: &mut Context<Self>,
    ) {
        let gid = index
            .and_then(|ix| self.games.get(ix))
            .map(|game| game.gid.clone());
        self.selected = gid.clone();
        self.log.clear();
        let Some(gid) = gid else {
            self.detail = Detail {
                name: String::new(),
                lines: vec!["Select a game.".to_string()],
            };
            return;
        };
        self.log.push("— launch to populate logs —".to_string());
        self.log_scroll.scroll_to_bottom();
        self.rpc(window, cx, "game.details", json!({ "gid": gid }), |this, reply, _, cx| {
            if let Ok(value) = reply {
                this.detail = Detail {
                    name: text_field_value(&value, "name").to_string(),
                    lines: strings(value.get("lines")),
                };
            }
            cx.notify();
        });
    }

    fn move_selection(&mut self, delta: isize, window: &Window, cx: &mut Context<Self>) {
        if self.games.is_empty() {
            return;
        }
        let current = self
            .selected
            .as_ref()
            .and_then(|gid| self.games.iter().position(|game| &game.gid == gid))
            .unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, self.games.len() as isize - 1) as usize;
        if Some(next) != current.try_into().ok() {
            self.select_index(Some(next), window, cx);
        }
    }

    // -- actions -----------------------------------------------------------

    pub(crate) fn available(&self) -> bool {
        self.bridge.is_some()
    }

    pub(crate) fn launch(&mut self, unfiltered: bool, window: &Window, cx: &mut Context<Self>) {
        let Some(gid) = self.selected.clone() else {
            return;
        };
        if !self.available() {
            return;
        }
        self.rpc(
            window,
            cx,
            "game.launch",
            json!({"gid": gid, "unfiltered": unfiltered}),
            |this, reply, window, cx| {
                if let Ok(value) = reply {
                    this.notice_on_error(&value, window, cx);
                }
            },
        );
    }

    pub(crate) fn translate(&mut self, setup: bool, window: &Window, cx: &mut Context<Self>) {
        let Some(gid) = self.selected.clone() else {
            return;
        };
        if !self.available() {
            return;
        }
        self.rpc(
            window,
            cx,
            "game.translate",
            json!({"gid": gid, "setup": setup}),
            |this, reply, window, cx| {
                if let Ok(value) = reply {
                    this.notice_on_error(&value, window, cx);
                }
            },
        );
    }

    fn notice_on_error(&mut self, value: &Value, _window: &mut Window, cx: &mut Context<Self>) {
        if text_field_value(value, "result") != "error" {
            return;
        }
        let message = text_field_value(value, "message").to_string();
        if message.is_empty() {
            return;
        }
        self.modal = Modal::Prompt(PromptState {
            kind: PromptKind::Notice,
            title: "Notice".to_string(),
            body: message,
            buttons: vec!["OK".to_string()],
        });
        cx.notify();
    }

    pub(crate) fn stop_game(&mut self, window: &Window, cx: &mut Context<Self>) {
        if !self.available() {
            return;
        }
        self.rpc(window, cx, "game.stop", json!({}), |_, _, _, _| {});
    }

    pub(crate) fn open_textbox(&mut self, window: &Window, cx: &mut Context<Self>) {
        if !self.available() {
            return;
        }
        let gid = self.selected.clone().unwrap_or_default();
        self.rpc(window, cx, "textbox.open", json!({"gid": gid}), |_, _, _, _| {});
    }

    pub(crate) fn preview(&mut self, window: &Window, cx: &mut Context<Self>) {
        let Some(gid) = self.selected.clone() else {
            return;
        };
        if !self.available() {
            return;
        }
        self.rpc(window, cx, "game.preview", json!({"gid": gid}), |this, reply, _, cx| {
            if let Ok(value) = reply {
                this.modal = Modal::Preview(text_field_value(&value, "command").to_string());
            }
            cx.notify();
        });
    }

    pub(crate) fn copy_log(&mut self, cx: &mut Context<Self>) {
        cx.write_to_clipboard(ClipboardItem::new_string(self.log.join("\n")));
    }

    pub fn quit(&mut self, window: &Window, cx: &mut Context<Self>) {
        if self.available() {
            self.rpc(window, cx, "shutdown", json!({}), |_, _, _, cx| {
                cx.quit();
            });
        } else {
            cx.quit();
        }
    }

    pub(crate) fn remove_selected(&mut self, window: &Window, cx: &mut Context<Self>) {
        let Some(gid) = self.selected.clone() else {
            return;
        };
        self.rpc(window, cx, "game.remove", json!({"gid": gid}), |_, _, _, _| {});
    }

    // -- modal resolution --------------------------------------------------

    pub fn resolve_prompt(&mut self, index: i32, window: &Window, cx: &mut Context<Self>) {
        match std::mem::replace(&mut self.modal, Modal::None) {
            Modal::Prompt(state) => match state.kind {
                PromptKind::Backend => {
                    self.rpc(
                        window,
                        cx,
                        "prompt.resolve",
                        json!({"index": index}),
                        |_, _, _, _| {},
                    );
                }
                PromptKind::RemoveGame => {
                    if index == 0 {
                        self.remove_selected(window, cx);
                    }
                }
                PromptKind::Notice => {}
            },
            other => self.modal = other,
        }
    }

    fn escape(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        match std::mem::replace(&mut self.modal, Modal::None) {
            Modal::None => {}
            Modal::Prompt(_) => self.resolve_prompt(-1, window, cx),
            Modal::Preview(_) => {}
            Modal::Threads(state) => self.close_threads(state, window, cx),
            Modal::Settings(_) => {}
            Modal::Wizard(_) => {}
        }
        cx.notify();
    }

    // -- keyboard ----------------------------------------------------------

    /// Caret, selection and deletion keys for the focused dialog field.
    ///
    /// Typed text arrives through `EntityInputHandler`; movement, selection and
    /// deletion have to be driven here because the fields are plain state.
    fn edit_field(
        &mut self,
        event: &KeyDownEvent,
        window: &Window,
        cx: &mut Context<Self>,
    ) -> bool {
        let Some(id) = self.focused_field_id(window, cx) else {
            return false;
        };
        let key = event.keystroke.key.as_str();
        let shift = event.keystroke.modifiers.shift;
        let select_all =
            event.keystroke.modifiers.control || event.keystroke.modifiers.platform;
        let Some(field) = self.field_mut(id) else {
            return false;
        };
        let handled = match key {
            "left" => {
                let range = field.selected_range();
                let to = if !shift && !range.is_empty() {
                    range.start
                } else {
                    field.previous_boundary(field.caret)
                };
                if shift {
                    field.select_to(to);
                } else {
                    field.move_to(to);
                }
                true
            }
            "right" => {
                let range = field.selected_range();
                let to = if !shift && !range.is_empty() {
                    range.end
                } else {
                    field.next_boundary(field.caret)
                };
                if shift {
                    field.select_to(to);
                } else {
                    field.move_to(to);
                }
                true
            }
            "home" => {
                field.move_to(0);
                true
            }
            "end" => {
                let end = field.text.len();
                field.move_to(end);
                true
            }
            "backspace" => {
                let range = field.selected_range();
                if range.is_empty() {
                    let from = field.caret;
                    let to = field.previous_boundary(from);
                    field.replace(to..from, "");
                } else {
                    field.replace(range, "");
                }
                true
            }
            "delete" => {
                let range = field.selected_range();
                if range.is_empty() {
                    let from = field.caret;
                    let to = field.next_boundary(from);
                    field.replace(from..to, "");
                } else {
                    field.replace(range, "");
                }
                true
            }
            "a" if select_all => {
                field.move_to(0);
                let end = field.text.len();
                field.select_to(end);
                true
            }
            _ => false,
        };
        if handled {
            cx.notify();
        }
        handled
    }

    /// Open one dropdown of the active modal and close its siblings: two lists
    /// never stay open together, and a disabled combo ignores the toggle.
    pub(crate) fn toggle_combo(&mut self, kind: ComboKind, cx: &mut Context<Self>) {
        let open = {
            let target = match (&self.modal, kind) {
                (Modal::Wizard(state), ComboKind::WizardVariant) => Some(&state.variant),
                (Modal::Wizard(state), ComboKind::WizardUpscaler) => Some(&state.upscaler),
                (Modal::Wizard(state), ComboKind::WizardGpu) => Some(&state.gpu),
                (Modal::Wizard(state), ComboKind::WizardLang) => Some(&state.lang),
                (Modal::Settings(state), ComboKind::SettingsProton) => Some(&state.proton),
                (Modal::Settings(state), ComboKind::SettingsFont) => Some(&state.font),
                _ => None,
            };
            match target {
                Some(combo) if combo.enabled => !combo.open,
                _ => return,
            }
        };
        match &mut self.modal {
            Modal::Wizard(state) => {
                state.variant.open = false;
                state.upscaler.open = false;
                state.gpu.open = false;
                state.lang.open = false;
                match kind {
                    ComboKind::WizardVariant => state.variant.open = open,
                    ComboKind::WizardUpscaler => state.upscaler.open = open,
                    ComboKind::WizardGpu => state.gpu.open = open,
                    ComboKind::WizardLang => state.lang.open = open,
                    _ => {}
                }
            }
            Modal::Settings(state) => {
                state.proton.open = false;
                state.font.open = false;
                match kind {
                    ComboKind::SettingsProton => state.proton.open = open,
                    ComboKind::SettingsFont => state.font.open = open,
                    _ => {}
                }
            }
            _ => {}
        }
        cx.notify();
    }

    /// The dropdown whose trigger owns focus, if any (Tab cycles into them).
    fn focused_combo(&mut self, window: &Window) -> Option<&mut ComboState> {
        match &mut self.modal {
            Modal::Settings(state) => {
                if state.proton.focus.is_focused(window) && state.proton.enabled {
                    return Some(&mut state.proton);
                }
                if state.font.focus.is_focused(window) && state.font.enabled {
                    return Some(&mut state.font);
                }
                None
            }
            Modal::Wizard(state) => {
                if state.variant.focus.is_focused(window) && state.variant.enabled {
                    return Some(&mut state.variant);
                }
                if state.upscaler.focus.is_focused(window) && state.upscaler.enabled {
                    return Some(&mut state.upscaler);
                }
                if state.gpu.focus.is_focused(window) && state.gpu.enabled {
                    return Some(&mut state.gpu);
                }
                if state.lang.focus.is_focused(window) && state.lang.enabled {
                    return Some(&mut state.lang);
                }
                None
            }
            _ => None,
        }
    }

    /// The same lookup without a mutable borrow: Enter/Space go through
    /// `toggle_combo` so the keyboard honours the one-open-at-a-time rule.
    fn focused_combo_kind(&self, window: &Window) -> Option<ComboKind> {
        match &self.modal {
            Modal::Settings(state) => {
                if state.proton.focus.is_focused(window) && state.proton.enabled {
                    return Some(ComboKind::SettingsProton);
                }
                if state.font.focus.is_focused(window) && state.font.enabled {
                    return Some(ComboKind::SettingsFont);
                }
                None
            }
            Modal::Wizard(state) => {
                if state.variant.focus.is_focused(window) && state.variant.enabled {
                    return Some(ComboKind::WizardVariant);
                }
                if state.upscaler.focus.is_focused(window) && state.upscaler.enabled {
                    return Some(ComboKind::WizardUpscaler);
                }
                if state.gpu.focus.is_focused(window) && state.gpu.enabled {
                    return Some(ComboKind::WizardGpu);
                }
                if state.lang.focus.is_focused(window) && state.lang.enabled {
                    return Some(ComboKind::WizardLang);
                }
                None
            }
            _ => None,
        }
    }

    /// Enter/Space open the focused dropdown; Up/Down move inside it.
    fn combo_key(&mut self, key: &str, window: &Window, cx: &mut Context<Self>) -> bool {
        match key {
            "enter" | "return" | "space" => match self.focused_combo_kind(window) {
                Some(kind) => {
                    self.toggle_combo(kind, cx);
                    true
                }
                None => false,
            },
            "up" | "down" => {
                let Some(combo) = self.focused_combo(window) else {
                    return false;
                };
                if !combo.open || combo.items.is_empty() {
                    return false;
                }
                let last = combo.items.len() - 1;
                let step: isize = if key == "up" { -1 } else { 1 };
                let next = (combo.selected as isize + step).clamp(0, last as isize) as usize;
                combo.selected = next;
                cx.notify();
                true
            }
            _ => false,
        }
    }

    pub fn on_key_down(
        &mut self,
        event: &KeyDownEvent,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let key = event.keystroke.key.to_string();
        let shift = event.keystroke.modifiers.shift;
        if key == "tab" {
            self.cycle_focus(shift, window, cx);
            return;
        }
        if key == "escape" {
            self.escape(window, cx);
            return;
        }
        if self.edit_field(event, window, cx) {
            return;
        }
        if self.combo_key(&key, window, cx) {
            return;
        }
        if self.modal_key(&key, window, cx) {
            return;
        }
        match key.as_str() {
            "up" => self.move_selection(-1, window, cx),
            "down" => self.move_selection(1, window, cx),
            _ => {}
        }
    }

    fn modal_key(&mut self, key: &str, window: &mut Window, cx: &mut Context<Self>) -> bool {
        match &self.modal {
            Modal::None => false,
            Modal::Prompt(_) | Modal::Preview(_) | Modal::Threads(_) => false,
            Modal::Settings(_) => self.settings_key(key, window, cx),
            Modal::Wizard(_) => self.wizard_key(key, window, cx),
        }
    }

    fn cycle_focus(&mut self, backwards: bool, window: &mut Window, cx: &mut Context<Self>) {
        let handles = self.focusables();
        if handles.is_empty() {
            return;
        }
        let current = window.focused(cx);
        let index = current
            .as_ref()
            .and_then(|handle| handles.iter().position(|h| h == handle));
        let next = match (index, backwards) {
            (Some(ix), false) => (ix + 1) % handles.len(),
            (Some(ix), true) => (ix + handles.len() - 1) % handles.len(),
            (None, false) => 0,
            (None, true) => handles.len() - 1,
        };
        window.focus(&handles[next]);
    }

    fn focusables(&self) -> Vec<FocusHandle> {
        match &self.modal {
            Modal::Settings(state) => vec![
                state.prefix.focus.clone(),
                state.proton.focus.clone(),
                state.font.focus.clone(),
                state.font_size.field.focus.clone(),
                state.layer.focus.clone(),
            ],
            Modal::Wizard(state) => {
                let mut handles = vec![state.path.focus.clone()];
                if state.variant.enabled {
                    handles.push(state.variant.focus.clone());
                }
                handles.extend([
                    state.upscaler.focus.clone(),
                    state.gpu.focus.clone(),
                    state.fps.field.focus.clone(),
                    state.render.focus.clone(),
                    state.lang.focus.clone(),
                    state.name.focus.clone(),
                ]);
                handles
            }
            _ => Vec::new(),
        }
    }

    // -- text fields -------------------------------------------------------

    pub fn field_mut(&mut self, id: FieldId) -> Option<&mut FieldState> {
        match (&mut self.modal, id) {
            (Modal::Wizard(state), FieldId::WizardPath) => Some(&mut state.path),
            (Modal::Wizard(state), FieldId::WizardName) => Some(&mut state.name),
            (Modal::Wizard(state), FieldId::WizardFps) => Some(&mut state.fps.field),
            (Modal::Wizard(state), FieldId::WizardRender) => Some(&mut state.render),
            (Modal::Settings(state), FieldId::SettingsPrefix) => Some(&mut state.prefix),
            (Modal::Settings(state), FieldId::SettingsLayer) => Some(&mut state.layer),
            (Modal::Settings(state), FieldId::SettingsFontSize) => Some(&mut state.font_size.field),
            _ => None,
        }
    }

    fn field_ref(&self, id: FieldId) -> Option<&FieldState> {
        match (&self.modal, id) {
            (Modal::Wizard(state), FieldId::WizardPath) => Some(&state.path),
            (Modal::Wizard(state), FieldId::WizardName) => Some(&state.name),
            (Modal::Wizard(state), FieldId::WizardFps) => Some(&state.fps.field),
            (Modal::Wizard(state), FieldId::WizardRender) => Some(&state.render),
            (Modal::Settings(state), FieldId::SettingsPrefix) => Some(&state.prefix),
            (Modal::Settings(state), FieldId::SettingsLayer) => Some(&state.layer),
            (Modal::Settings(state), FieldId::SettingsFontSize) => Some(&state.font_size.field),
            _ => None,
        }
    }

    pub fn caret_to_end(&mut self, id: FieldId, end: usize) {
        if let Some(field) = self.field_mut(id) {
            field.move_to(end);
        }
    }

    fn focused_field_id(&self, window: &Window, cx: &App) -> Option<FieldId> {
        let candidates: [(FieldId, &FocusHandle); 7] = match &self.modal {
            Modal::Wizard(state) => [
                (FieldId::WizardPath, &state.path.focus),
                (FieldId::WizardName, &state.name.focus),
                (FieldId::WizardFps, &state.fps.field.focus),
                (FieldId::WizardRender, &state.render.focus),
                (FieldId::WizardPath, &state.path.focus),
                (FieldId::WizardPath, &state.path.focus),
                (FieldId::WizardPath, &state.path.focus),
            ],
            Modal::Settings(state) => [
                (FieldId::SettingsPrefix, &state.prefix.focus),
                (FieldId::SettingsLayer, &state.layer.focus),
                (FieldId::SettingsFontSize, &state.font_size.field.focus),
                (FieldId::SettingsPrefix, &state.prefix.focus),
                (FieldId::SettingsPrefix, &state.prefix.focus),
                (FieldId::SettingsPrefix, &state.prefix.focus),
                (FieldId::SettingsPrefix, &state.prefix.focus),
            ],
            _ => return None,
        };
        let _ = cx;
        let focused = candidates.iter().find(|(_, handle)| handle.is_focused(window));
        focused.map(|(id, _)| *id)
    }
}

fn parse_games(value: Option<&Value>) -> Vec<Game> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| Game {
                    gid: text_field_value(item, "gid").to_string(),
                    name: text_field_value(item, "name").to_string(),
                    info: text_field_value(item, "info").to_string(),
                    icon: text_field_value(item, "icon").to_string(),
                })
                .collect()
        })
        .unwrap_or_default()
}

impl Focusable for MainView {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus.clone()
    }
}

impl EntityInputHandler for MainView {
    fn text_for_range(
        &mut self,
        range_utf16: Range<usize>,
        adjusted_range: &mut Option<Range<usize>>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Option<String> {
        let id = self.focused_field_id(window, cx)?;
        let field = self.field_mut(id)?;
        let range = range_from_utf16(&field.text, &range_utf16);
        adjusted_range.replace(range_to_utf16(&field.text, &range));
        Some(field.text[range].to_string())
    }

    fn selected_text_range(
        &mut self,
        _ignore_disabled_input: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Option<UTF16Selection> {
        let id = self.focused_field_id(window, cx)?;
        let field = self.field_mut(id)?;
        let selection = field.selected_range();
        let reversed = field.anchor.map(|a| a > field.caret).unwrap_or(false);
        Some(UTF16Selection {
            range: range_to_utf16(&field.text, &selection),
            reversed,
        })
    }

    fn marked_text_range(
        &self,
        window: &mut Window,
        _cx: &mut Context<Self>,
    ) -> Option<Range<usize>> {
        let id = self.focused_field_id(window, _cx)?;
        let field = self.field_ref(id)?;
        field
            .marked
            .as_ref()
            .map(|range| range_to_utf16(&field.text, range))
    }

    fn unmark_text(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(id) = self.focused_field_id(window, cx)
            && let Some(field) = self.field_mut(id)
        {
            field.marked = None;
        }
    }

    fn replace_text_in_range(
        &mut self,
        range_utf16: Option<Range<usize>>,
        new_text: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(id) = self.focused_field_id(window, cx) else {
            return;
        };
        let text = if id.is_numeric() {
            let digits: String = new_text.chars().filter(char::is_ascii_digit).collect();
            if digits.is_empty() && !new_text.is_empty() {
                return;
            }
            digits
        } else if id.is_size() {
            let size: String = new_text
                .chars()
                .filter(|c| c.is_ascii_digit() || *c == 'x' || *c == 'X')
                .map(|c| c.to_ascii_lowercase())
                .collect();
            if size.is_empty() && !new_text.is_empty() {
                return;
            }
            size
        } else {
            new_text.to_string()
        };
        if let Some(field) = self.field_mut(id) {
            let range = range_utf16
                .as_ref()
                .map(|range| range_from_utf16(&field.text, range))
                .or_else(|| field.marked.clone())
                .unwrap_or_else(|| field.selected_range());
            field.replace(range, &text);
        }
        cx.notify();
    }

    fn replace_and_mark_text_in_range(
        &mut self,
        range_utf16: Option<Range<usize>>,
        new_text: &str,
        new_selected_range_utf16: Option<Range<usize>>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let Some(id) = self.focused_field_id(window, cx) else {
            return;
        };
        if let Some(field) = self.field_mut(id) {
            let range = range_utf16
                .as_ref()
                .map(|range| range_from_utf16(&field.text, range))
                .or_else(|| field.marked.clone())
                .unwrap_or_else(|| field.selected_range());
            let start = range.start;
            field.replace(range, new_text);
            if new_text.is_empty() {
                field.marked = None;
            } else {
                field.marked = Some(start..start + new_text.len());
            }
            if let Some(selected) = new_selected_range_utf16 {
                let local = range_from_utf16(new_text, &selected);
                field.caret = start + local.end;
            }
        }
        cx.notify();
    }

    fn bounds_for_range(
        &mut self,
        range_utf16: Range<usize>,
        bounds: Bounds<Pixels>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Option<Bounds<Pixels>> {
        let id = self.focused_field_id(window, cx)?;
        let field = self.field_ref(id)?;
        let line = field.layout.as_ref()?;
        let range = range_from_utf16(&field.text, &range_utf16);
        Some(Bounds::from_corners(
            Point {
                x: bounds.left() + line.x_for_index(range.start),
                y: bounds.top(),
            },
            Point {
                x: bounds.left() + line.x_for_index(range.end),
                y: bounds.bottom(),
            },
        ))
    }

    fn character_index_for_point(
        &mut self,
        point: Point<Pixels>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> Option<usize> {
        let id = self.focused_field_id(window, cx)?;
        let field = self.field_ref(id)?;
        let line = field.layout.as_ref()?;
        let bounds = field.bounds?;
        let local = bounds.localize(&point)?;
        let index = line.index_for_x(point.x - local.x)?;
        Some(offset_to_utf16(&field.text, index))
    }
}

impl Render for MainView {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let theme = self.theme.clone();
        let main = self.render_main(window, cx);
        let overlay = self.render_modal(window, cx);
        div()
            .id("root")
            .size_full()
            .flex()
            .flex_col()
            .bg(theme.background)
            .text_color(theme.text)
            .font_family(theme.ui_font.clone())
            .text_size(px(theme.body))
            .track_focus(&self.focus)
            .on_key_down(cx.listener(Self::on_key_down))
            .on_mouse_down(MouseButton::Left, |_, window, _| {
                window.refresh();
            })
            .child(main)
            .child(overlay)
    }
}

/// Shared helper for views: a label/value text row.
pub fn label_text(
    theme: &Theme,
    text: impl Into<SharedString>,
    size: f32,
    color: gpui::Hsla,
) -> gpui::AnyElement {
    div()
        .text_size(px(size))
        .text_color(color)
        .font_family(theme.ui_font.clone())
        .child(text.into())
        .into_any_element()
}

pub fn _unused_offsets() {
    let _ = offset_from_utf16;
}
