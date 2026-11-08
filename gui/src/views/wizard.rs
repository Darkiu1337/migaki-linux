//! Add/edit game dialog: one card with a tab strip (Game, System, Performance,
//! Translation), ported from `Wizard.qml`.
//!
//! The state is built when the dialog opens (`open_wizard`); editing an
//! existing game fills it from `game.data` once the bridge answers. Detect and
//! path picks both run `reconcile` so the runner always points at a launchable
//! target. The footer saves in place, and Save stays disabled until the
//! required fields are set.
use std::time::Duration;

use gpui::prelude::*;
use gpui::{
    Animation, AnimationExt, AnyElement, ClickEvent, FontWeight, SharedString, Window, div,
    ease_out_quint, px,
};
use serde_json::{Value, json};

use crate::app::{ComboKind, FieldId, MainView, Modal};
use crate::theme::Theme;
use crate::ui::button::{Variant, button, section_label};
use crate::ui::checkbox::checkbox;
use crate::ui::combo::{ComboItem, ComboState, combo};
use crate::ui::divider::divider;
use crate::ui::modal::{ModalCard, footer_split, overlay};
use crate::ui::spin::{SpinState, spin};
use crate::ui::tabs::tab_bar;
use crate::ui::text_field::{FieldState, text_field};

/// Tab labels in strip order; `self.page` is the index into both.
const TABS: [(&str, &str); 4] = [
    ("game", "Game"),
    ("system", "System"),
    ("visuals", "Visuals"),
    ("translation", "Translation"),
];
const PAGES: usize = TABS.len();

/// Fade-in of a tab's content; short enough that switching tabs is instant.
const PAGE_FADE: Duration = Duration::from_millis(120);

const RENDER_NOTE: &str = "Render is the virtual mode gamescope exposes; a game that changes \
     resolution while running can only pick sizes up to it. Unchecked keeps the automatic \
     value (engine base, else half the monitor).";

const DETECT_TIP: &str = "Tip: Detect picks the runner from the game files.";

const RPGMAKER_GPU_WARNING: &str = "RPGMaker titles are Chromium/ANGLE-Vulkan and can only filter on \
     the display GPU. On any other GPU Chromium drops Vulkan — no filter, no overlay, and no WebGL. \
     Pick the GPU marked (display).";

const TRANSLATE_NOTE: &str = "Proton/Windows games only. Filter and translation compose in one launch. \
     First run: enable, then Translate (or Setup Text Hooker) — Textractor stays hidden and the in-app \
     picker records the story thread. Debug boxes reveal the DeepL browser and Textractor.";

pub struct WizardState {
    pub gid: String,
    pub runner: String,
    pub path: FieldState,
    pub name: FieldState,
    pub fps: SpinState,
    pub variant: ComboState,
    pub upscaler: ComboState,
    pub gpu: ComboState,
    pub lang: ComboState,
    pub hud: bool,
    pub shaders: bool,
    pub upscale: bool,
    pub render_set: bool,
    pub render: FieldState,
    pub upscale_info: String,
    pub prefix_game: bool,
    pub tr_enable: bool,
    pub show_browser: bool,
    pub show_hooker: bool,
    pub page: usize,
    pub detect: String,
    pub detect_severity: String,
    pub err: String,
}

fn is_display_gpu(app: &MainView, name: &str) -> bool {
    let lowered = name.to_lowercase();
    app.display_vendors
        .iter()
        .any(|vendor| !vendor.is_empty() && lowered.contains(&vendor.to_lowercase()))
}

/// The vkBasalt variants are listed by name only: the chain's notes described
/// GPU cost, not what a title should pick, so the quality is the label.
fn variant_items(app: &MainView) -> Vec<ComboItem> {
    let items: Vec<ComboItem> = app
        .variants
        .iter()
        .map(|name| ComboItem::new(name.clone(), name.clone()))
        .collect();
    if items.is_empty() {
        return vec![ComboItem::new("L", "L")];
    }
    items
}

/// Upscalers the bridge offers (gamescope `-F`), as `(value, label)` pairs.
fn upscaler_items(app: &MainView) -> Vec<ComboItem> {
    let items: Vec<ComboItem> = app
        .upscale_filters
        .iter()
        .map(|(value, label)| ComboItem::new(label.clone(), value.clone()))
        .collect();
    if items.is_empty() {
        return vec![ComboItem::new("CuNNy 4x16-NVL", "cunny")];
    }
    items
}

fn gpu_items(app: &MainView) -> Vec<ComboItem> {
    if app.gpus.is_empty() {
        // The probe runs off-thread; the `gpus` event refills the combo when
        // it lands, but the trigger must never render as an empty box.
        return vec![ComboItem::new(
            "auto (discrete GPU preferred)",
            "auto (discrete GPU preferred)",
        )];
    }
    app.gpus
        .iter()
        .map(|name| {
            let label = if is_display_gpu(app, name) {
                format!("{name}  (display)")
            } else {
                name.clone()
            };
            ComboItem::new(label, name.clone())
        })
        .collect()
}

fn lang_items(app: &MainView) -> Vec<ComboItem> {
    std::iter::once(ComboItem::new("(system)", ""))
        .chain(
            app.locales
                .iter()
                .map(|name| ComboItem::new(name.clone(), name.clone())),
        )
        .collect()
}

/// The bridge reports failures as `{"error": "..."}` (or the channel error).
fn reply_error(reply: &Result<Value, String>) -> String {
    match reply {
        Ok(value) => value
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        Err(err) => err.clone(),
    }
}

fn text_of(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// One line describing the recorded upscale profile (`game.data` ships it as
/// `upscale_profile`).
fn upscale_line(profile: Option<&Value>) -> String {
    let Some(profile) = profile else {
        return String::new();
    };
    let render = text_of(profile, "render");
    let output = text_of(profile, "output");
    let verdict = text_of(profile, "verdict");
    let note = text_of(profile, "note");
    if verdict.is_empty() {
        return format!("Base {render} -> {output}");
    }
    if note.is_empty() {
        format!("Base {render} -> {output} | {verdict}")
    } else {
        format!("Base {render} -> {output} | {verdict}: {note}")
    }
}

/// Label column plus a control that fills the rest of the row.
fn field_row(theme: &Theme, text: &str, control: AnyElement) -> AnyElement {
    div()
        .flex()
        .flex_row()
        .items_center()
        .gap(px(theme.xxl))
        .child(
            div()
                .w(px(170.))
                .flex_none()
                .text_size(px(theme.body))
                .text_color(theme.text)
                .child(text.to_string()),
        )
        .child(div().flex_1().min_w(px(0.)).child(control))
        .into_any_element()
}

fn muted_line(theme: &Theme, text: impl Into<SharedString>) -> AnyElement {
    div()
        .text_size(px(theme.caption))
        .text_color(theme.text_secondary)
        .child(text.into())
        .into_any_element()
}

/// Ask the bridge to reconcile `(path, runner)`: it answers with the real
/// launch target, the corrected runner, a severity and a message.
fn reconcile(
    app: &mut MainView,
    path: &str,
    runner: &str,
    preface: &str,
    window: &Window,
    cx: &mut Context<MainView>,
) {
    let path = path.trim().to_string();
    let runner = runner.to_string();
    let preface = preface.to_string();
    app.rpc(
        window,
        cx,
        "reconcile",
        json!({ "path": path, "runner": runner }),
        move |app, reply, _window, _cx| {
            if let Modal::Wizard(state) = &mut app.modal
                && !preface.is_empty()
                && reply.is_err()
            {
                state.detect = preface;
                return;
            }
            let Ok(value) = reply else {
                return;
            };
            let Modal::Wizard(state) = &mut app.modal else {
                return;
            };
            let runner = text_of(&value, "runner");
            if !runner.is_empty() && runner != state.runner {
                state.runner = runner;
            }
            let target = text_of(&value, "target");
            if !target.is_empty() && target != state.path.text {
                state.path.set_text(target);
            }
            let severity = text_of(&value, "severity");
            if !severity.is_empty() {
                state.detect_severity = severity;
            }
            let message = text_of(&value, "message");
            if message.is_empty() && preface.is_empty() {
                return;
            }
            state.detect = if message.is_empty() {
                preface
            } else if preface.is_empty() {
                message
            } else {
                format!("{preface} {message}")
            };
        },
    );
}

impl WizardState {
    /// The required fields: a game location and a runner. Existence is not
    /// stat'ed per frame (a removable or NFS path would stall the animated UI);
    /// the bridge's `validate_entry` still checks it on save and its message
    /// lands in `err`.
    pub fn can_save(&self) -> bool {
        !self.path.text.trim().is_empty() && !self.runner.is_empty()
    }

    /// Refill the GPU dropdown when the background probe reports its list,
    /// keeping the current pick (by value) selected.
    pub(crate) fn refresh_gpus(&mut self, app: &MainView) {
        let current = self.gpu.value();
        self.gpu.items = gpu_items(app);
        if current.is_empty() || !self.gpu.select_value(&current) {
            self.gpu.selected = self.gpu.selected.min(self.gpu.items.len() - 1);
        }
    }

    pub fn new(app: &MainView, gid: Option<String>, cx: &mut Context<MainView>) -> Self {
        let mut variant = ComboState::new(cx, variant_items(app));
        let _ = variant.select_value("L");
        let mut upscaler = ComboState::new(cx, upscaler_items(app));
        let _ = upscaler.select_value("cunny");
        let gpu = ComboState::new(cx, gpu_items(app));
        let lang = ComboState::new(cx, lang_items(app));
        WizardState {
            gid: gid.unwrap_or_default(),
            runner: "proton".to_string(),
            path: FieldState::new(cx),
            name: FieldState::new(cx),
            fps: SpinState::new(cx, 60, 0, 480, Some("off")),
            variant,
            upscaler,
            gpu,
            lang,
            hud: false,
            shaders: true,
            upscale: false,
            render_set: false,
            render: FieldState::new(cx),
            upscale_info: String::new(),
            prefix_game: false,
            tr_enable: false,
            show_browser: false,
            show_hooker: false,
            page: 0,
            detect: DETECT_TIP.to_string(),
            detect_severity: "ok".to_string(),
            err: String::new(),
        }
    }

    /// Fill the form from a `game.data` record (editing an existing game).
    fn fill(&mut self, data: &Value) {
        let runner = text_of(data, "runner");
        if !runner.is_empty() {
            self.runner = runner;
        }
        self.path.set_text(text_of(data, "path"));
        self.name.set_text(text_of(data, "name"));
        let variant = text_of(data, "variant");
        let _ = self.variant.select_value(&variant);
        let gpu = text_of(data, "gpu");
        let _ = self.gpu.select_value(&gpu);
        let fps = text_of(data, "fps");
        let value = if fps == "off" {
            0
        } else {
            fps.parse::<i64>().unwrap_or(60)
        };
        self.fps.set_value(value);
        self.hud = text_of(data, "hud") == "1";
        self.shaders = text_of(data, "shaders") != "0";
        self.upscale = text_of(data, "upscale") == "1";
        // The variant only matters while the in-game chain runs.
        self.variant.enabled = self.shaders && !self.upscale;
        self.upscale_info = upscale_line(data.get("upscale_profile"));
        if let Some(profile) = data.get("upscale_profile") {
            // The profile always carries a filter (`cunny` when unset); an
            // unknown name keeps the default selection instead of clearing it.
            let _ = self.upscaler.select_value(&text_of(profile, "filter"));
            // Only an explicit user choice checks the box; a render the runs
            // resolved themselves must not look like one.
            self.render_set = profile
                .get("render_set")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            self.render.set_text(text_of(profile, "render"));
        }
        let lang = text_of(data, "lang");
        if !lang.is_empty() && !self.lang.select_value(&lang) {
            self.lang.items.push(ComboItem::new(lang.clone(), lang));
            self.lang.selected = self.lang.items.len() - 1;
        }
        self.prefix_game = text_of(data, "prefix_mode") == "game";
        if let Some(translate) = data.get("translate") {
            self.tr_enable = text_of(translate, "enabled") == "1";
            self.show_browser = text_of(translate, "show_browser") == "1";
            self.show_hooker = text_of(translate, "show_hooker") == "1";
        }
    }

    pub fn path_chosen(
        &mut self,
        app: &mut MainView,
        path: &str,
        window: &Window,
        cx: &mut Context<MainView>,
    ) {
        self.path.set_text(path);
        app.rpc(
            window,
            cx,
            "path.remember",
            json!({ "path": path }),
            |_, _, _, _| {},
        );
        if self.runner == "native" {
            reconcile(app, &self.path.text, &self.runner, "", window, cx);
        }
    }

    /// Validate then save the record, then close the dialog.
    fn finish(&mut self, app: &mut MainView, window: &Window, cx: &mut Context<MainView>) {
        self.err.clear();
        let mut name = self.name.text.trim().to_string();
        if name.is_empty() {
            name = self
                .path
                .text
                .trim()
                .rsplit('/')
                .next()
                .unwrap_or_default()
                .to_string();
        }
        let fps = self.fps.value();
        let data = json!({
            "name": name,
            "runner": self.runner.clone(),
            "path": self.path.text.trim(),
            "variant": self.variant.value(),
            "gpu": self.gpu.value(),
            "fps": if fps == 0 { "off".to_string() } else { fps.to_string() },
            "hud": if self.hud { "1" } else { "0" },
            "shaders": if self.shaders { "1" } else { "0" },
            "upscale": if self.upscale { "1" } else { "0" },
            "upscale_render": if self.render_set {
                self.render.text.trim().to_string()
            } else {
                String::new()
            },
            "upscale_filter": self.upscaler.value(),
            "lang": self.lang.value(),
            "prefix_mode": if self.prefix_game { "game" } else { "shared" },
            "translate": {
                "enabled": if self.tr_enable && self.runner == "proton" { "1" } else { "0" },
                "show_browser": if self.show_browser { "1" } else { "0" },
                "show_hooker": if self.show_hooker { "1" } else { "0" },
            },
        });
        let gid = self.gid.clone();
        let saved = data.clone();
        app.rpc(
            window,
            cx,
            "game.validate",
            json!({ "data": data }),
            move |app, reply, window, cx| {
                let error = reply_error(&reply);
                if !error.is_empty() {
                    if let Modal::Wizard(state) = &mut app.modal {
                        state.err = error;
                    }
                    return;
                }
                app.rpc(
                    window,
                    cx,
                    "game.save",
                    json!({ "gid": gid, "data": saved }),
                    |app, reply, _window, _cx| {
                        let error = reply_error(&reply);
                        if !error.is_empty() {
                            if let Modal::Wizard(state) = &mut app.modal {
                                state.err = error;
                            }
                            return;
                        }
                        app.modal = Modal::None;
                    },
                );
            },
        );
    }

    pub fn render(
        &self,
        app: &MainView,
        window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        let page = match self.page {
            0 => self.page_game(app, window, cx),
            1 => self.page_system(app, window, cx),
            2 => self.page_visuals(app, window, cx),
            _ => self.page_translation(app, cx),
        };
        // The animation id carries the tab index, so every switch fades the new
        // page in instead of reusing the finished one.
        let page = div()
            .child(page)
            .with_animation(
                SharedString::from(format!("wizard-page-{}", self.page)),
                Animation::new(PAGE_FADE).with_easing(ease_out_quint()),
                |page, t| page.opacity(t),
            );
        let entity = cx.entity();
        let tabs = tab_bar(
            theme,
            "wizard-tab",
            &TABS,
            self.page,
            move |index, _event, _window, cx| {
                entity.update(cx, |this, cx| {
                    if let Modal::Wizard(state) = &mut this.modal {
                        state.page = index;
                        state.variant.open = false;
                        state.gpu.open = false;
                        state.lang.open = false;
                    }
                    cx.notify();
                });
            },
        );

        let mut body = div()
            .flex()
            .flex_col()
            .flex_1()
            .min_h(px(0.))
            .gap(px(theme.lg))
            .child(tabs)
            .child(
                div()
                    .id("wizard-pages")
                    .flex_1()
                    .min_h(px(0.))
                    .overflow_y_scroll()
                    .child(page),
            );
        if !self.err.is_empty() {
            body = body.child(
                div()
                    .text_size(px(theme.body))
                    .text_color(theme.error)
                    .child(self.err.clone()),
            );
        }

        let cancel = button(
            theme,
            "wizard-cancel",
            "Cancel",
            true,
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                this.modal = Modal::None;
                cx.notify();
            }),
        );
        let save = button(
            theme,
            "wizard-save",
            if self.gid.is_empty() { "Add game" } else { "Save" },
            self.can_save(),
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, window, cx| {
                this.finish_wizard(window, cx);
            }),
        );
        let mut leading = div()
            .flex()
            .flex_row()
            .items_center()
            .gap(px(theme.lg))
            .child(cancel);
        if !self.can_save() {
            leading = leading.child(muted_line(theme, "Set the game location to save."));
        }

        let title = if self.gid.is_empty() {
            "Add game"
        } else {
            "Edit game"
        };
        overlay(
            theme,
            "wizard",
            ModalCard {
                title: SharedString::from(title),
                width: px(760.),
                height: Some(px(560.)),
            },
            body.into_any_element(),
            Some(footer_split(theme, leading.into_any_element(), vec![save])),
        )
    }

    /// Game tab: how it launches (runner), where it lives, and its name.
    fn page_game(
        &self,
        app: &MainView,
        window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        div()
            .flex()
            .flex_col()
            .gap(px(theme.lg))
            .child(self.page_runner(app, cx))
            .child(divider(theme))
            .child(self.page_location(app, window, cx))
            .child(divider(theme))
            .child(self.page_name(app, window, cx))
            .into_any_element()
    }

    fn page_runner(&self, app: &MainView, cx: &mut Context<MainView>) -> AnyElement {
        let theme = &app.theme;
        let locked = !self.gid.is_empty();
        let title = if locked {
            "Runner (locked: runner changes mean re-adding)"
        } else {
            "Runner"
        };
        let mut column = div()
            .flex()
            .flex_col()
            .gap(px(theme.lg))
            .child(section_label(theme, title));
        for (key, description) in &app.runners {
            let value = key.clone();
            column = column.child(checkbox(
                theme,
                SharedString::from(format!("wizard-runner-{key}")),
                format!("{key} — {description}"),
                self.runner == *key,
                !locked,
                cx.listener(move |this, _event: &ClickEvent, _window, cx| {
                    if let Modal::Wizard(state) = &mut this.modal {
                        state.runner = value.clone();
                    }
                    cx.notify();
                }),
            ));
        }
        column.into_any_element()
    }

    fn page_location(
        &self,
        app: &MainView,
        window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        let entity = cx.entity();
        let path = text_field(
            theme,
            entity,
            SharedString::from("wizard-path"),
            FieldId::WizardPath,
            &self.path,
            window,
            "/path/to/game",
            None,
            None,
            false,
        );
        let browse = button(
            theme,
            "wizard-browse",
            "Browse…",
            true,
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, window, cx| {
                let Modal::Wizard(state) = &this.modal else {
                    return;
                };
                let runner = state.runner.clone();
                let target = if runner == "rpgmaker" { "dir" } else { "file" };
                this.rpc(
                    window,
                    cx,
                    "path.pick",
                    json!({ "target": target, "runner": runner }),
                    |_, _, _, _| {},
                );
            }),
        );
        let detect = button(
            theme,
            "wizard-detect",
            "Detect",
            true,
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, window, cx| {
                let Modal::Wizard(state) = &this.modal else {
                    return;
                };
                let path = state.path.text.clone();
                this.rpc(
                    window,
                    cx,
                    "detect",
                    json!({ "path": path }),
                    |this, reply, window, cx| {
                        let detected = match &reply {
                            Ok(value) => value.get("detected").filter(|v| !v.is_null()).cloned(),
                            Err(_) => None,
                        };
                        let Some(detected) = detected else {
                            if let Modal::Wizard(state) = &mut this.modal {
                                state.detect_severity = "warn".to_string();
                                state.detect = "Detection failed to run.".to_string();
                            }
                            return;
                        };
                        let runner = text_of(&detected, "runner");
                        let confidence = text_of(&detected, "confidence");
                        let detail = text_of(&detected, "detail");
                        let known = this
                            .runners
                            .iter()
                            .any(|(key, _)| *key == runner);
                        let automatic = !runner.is_empty()
                            && known
                            && (confidence == "high" || confidence == "medium");
                        if automatic && let Modal::Wizard(state) = &mut this.modal {
                            state.runner = runner.clone();
                        }
                        let preface = if automatic {
                            format!("Detected: {detail} → runner '{runner}' ({confidence}).")
                        } else {
                            format!(
                                "Detected: {detail} (confidence: {confidence}) — pick the runner manually."
                            )
                        };
                        let (path, runner) = match &this.modal {
                            Modal::Wizard(state) => {
                                (state.path.text.clone(), state.runner.clone())
                            }
                            _ => return,
                        };
                        reconcile(this, &path, &runner, &preface, window, cx);
                    },
                );
            }),
        );
        let path_row = div()
            .flex()
            .flex_row()
            .items_center()
            .gap(px(theme.sm))
            .child(path)
            .child(browse)
            .child(detect);

        let warn = self.detect_severity == "warn";
        let mut detect_row = div()
            .flex()
            .flex_row()
            .items_center()
            .gap(px(theme.lg))
            .child(
                div()
                    .flex_1()
                    .min_w(px(0.))
                    .text_size(px(theme.body))
                    .text_color(if warn {
                        theme.warning
                    } else {
                        theme.text_secondary
                    })
                    .child(self.detect.clone()),
            );
        if warn && self.runner == "native" {
            detect_row = detect_row.child(button(
                theme,
                "wizard-switch-proton",
                "Switch to Proton",
                true,
                Variant::Normal,
                cx.listener(|this, _event: &ClickEvent, _window, cx| {
                    if let Modal::Wizard(state) = &mut this.modal {
                        state.runner = "proton".to_string();
                        state.detect_severity = "ok".to_string();
                        state.detect = "Runner set to proton.".to_string();
                    }
                    cx.notify();
                }),
            ));
        }

        div()
            .flex()
            .flex_col()
            .gap(px(theme.lg))
            .child(section_label(theme, "Game location"))
            .child(path_row)
            .child(detect_row)
            .into_any_element()
    }

    /// System tab: GPU, language, FPS cap and the Wine prefix choice.
    fn page_system(
        &self,
        app: &MainView,
        window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        let entity = cx.entity();
        let gpu = combo(
            theme,
            "wizard-gpu",
            "wizard-gpu",
            &self.gpu,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                this.toggle_combo(ComboKind::WizardGpu, cx);
            }),
            {
                let entity = entity.clone();
                move |index, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        if let Modal::Wizard(state) = &mut this.modal {
                            state.gpu.selected = index;
                            state.gpu.open = false;
                        }
                        cx.notify();
                    });
                }
            },
        );
        let fps = spin(
            theme,
            entity.clone(),
            "wizard-fps",
            &self.fps,
            FieldId::WizardFps,
            window,
            None,
            {
                let entity = entity.clone();
                move |delta, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        if let Modal::Wizard(state) = &mut this.modal {
                            state.fps.step(delta);
                        }
                        cx.notify();
                    });
                }
            },
        );
        let lang = combo(
            theme,
            "wizard-lang",
            "wizard-lang",
            &self.lang,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                this.toggle_combo(ComboKind::WizardLang, cx);
            }),
            {
                let entity = entity.clone();
                move |index, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        if let Modal::Wizard(state) = &mut this.modal {
                            state.lang.selected = index;
                            state.lang.open = false;
                        }
                        cx.notify();
                    });
                }
            },
        );
        let prefix = checkbox(
            theme,
            "wizard-prefix",
            "Separate Wine prefix for this game (Proton only)",
            self.prefix_game,
            true,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.prefix_game = !state.prefix_game;
                }
                cx.notify();
            }),
        );

        let mismatch = self.runner == "rpgmaker" && !is_display_gpu(app, &self.gpu.value());
        let mut column = div()
            .flex()
            .flex_col()
            .gap(px(theme.lg))
            .child(section_label(theme, "System"))
            .child(field_row(theme, "Game GPU:", gpu));
        if mismatch {
            column = column.child(
                div()
                    .text_size(px(theme.body_small))
                    .text_color(theme.warning)
                    .child(RPGMAKER_GPU_WARNING),
            );
        }
        column
            .child(field_row(theme, "Language:", lang))
            .child(field_row(theme, "FPS cap (0 = off):", fps))
            .child(prefix)
            .into_any_element()
    }

    /// Visuals tab: the run-wide overlay readout, the in-game vkBasalt chain
    /// and the 2x upscale with the upscaler it runs and the render mode it
    /// exposes.
    fn page_visuals(
        &self,
        app: &MainView,
        window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        let entity = cx.entity();
        let hud = checkbox(
            theme,
            "wizard-hud",
            "Show fps overlay while playing",
            self.hud,
            true,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.hud = !state.hud;
                }
                cx.notify();
            }),
        );
        // The CuNNy wrapper disables vkBasalt itself, so the in-game chain and
        // the 2x upscale are mutually exclusive; grey the switch out while the
        // upscaler owns the frame.
        let shaders = checkbox(
            theme,
            "wizard-shaders",
            "Anime4K shaders",
            self.shaders,
            !self.upscale,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.shaders = !state.shaders;
                    state.variant.enabled = state.shaders && !state.upscale;
                    if !state.variant.enabled {
                        state.variant.open = false;
                    }
                }
                cx.notify();
            }),
        );
        let variant = combo(
            theme,
            "wizard-variant",
            "wizard-variant",
            &self.variant,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                this.toggle_combo(ComboKind::WizardVariant, cx);
            }),
            {
                let entity = entity.clone();
                move |index, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        if let Modal::Wizard(state) = &mut this.modal {
                            state.variant.selected = index;
                            state.variant.open = false;
                        }
                        cx.notify();
                    });
                }
            },
        );
        let upscale = checkbox(
            theme,
            "wizard-upscale",
            "2x upscale (gamescope)",
            self.upscale,
            true,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.upscale = !state.upscale;
                    state.variant.enabled = state.shaders && !state.upscale;
                    if !state.variant.enabled {
                        state.variant.open = false;
                    }
                }
                cx.notify();
            }),
        );
        let upscaler = combo(
            theme,
            "wizard-upscaler",
            "wizard-upscaler",
            &self.upscaler,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                this.toggle_combo(ComboKind::WizardUpscaler, cx);
            }),
            {
                let entity = entity.clone();
                move |index, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        if let Modal::Wizard(state) = &mut this.modal {
                            state.upscaler.selected = index;
                            state.upscaler.open = false;
                        }
                        cx.notify();
                    });
                }
            },
        );
        let upscale_note = div()
            .text_size(px(theme.body_small))
            .text_color(theme.text_secondary)
            .child(self.upscale_info.clone());
        let render_toggle = checkbox(
            theme,
            "wizard-render",
            "Set the render resolution (gamescope -w/-h)",
            self.render_set,
            true,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.render_set = !state.render_set;
                    if state.render_set && state.render.text.trim().is_empty() {
                        state.render.set_text("960x720");
                    }
                }
                cx.notify();
            }),
        );
        let render_field = text_field(
            theme,
            entity.clone(),
            SharedString::from("wizard-render-field"),
            FieldId::WizardRender,
            &self.render,
            window,
            "WIDTHxHEIGHT, e.g. 960x720",
            None,
            Some(px(200.)),
            !self.render_set,
        );
        let render_row = div()
            .flex()
            .flex_row()
            .items_center()
            .gap(px(theme.xxl))
            .child(render_toggle)
            .child(render_field);

        div()
            .flex()
            .flex_col()
            .gap(px(theme.lg))
            .child(hud)
            .child(divider(theme))
            .child(section_label(theme, "In-game shaders"))
            .child(shaders)
            .child(field_row(theme, "Variant:", variant))
            .child(muted_line(theme, "Off while the 2x upscale runs: it owns the frame."))
            .child(divider(theme))
            .child(section_label(theme, "2x upscale"))
            .child(upscale)
            .child(field_row(theme, "Upscaler:", upscaler))
            .child(upscale_note)
            .child(render_row)
            .child(muted_line(theme, RENDER_NOTE))
            .into_any_element()
    }

    fn page_name(&self, app: &MainView, window: &Window, cx: &mut Context<MainView>) -> AnyElement {
        let theme = &app.theme;
        let entity = cx.entity();
        let name = text_field(
            theme,
            entity,
            SharedString::from("wizard-name"),
            FieldId::WizardName,
            &self.name,
            window,
            "Display name",
            None,
            None,
            false,
        );
        div()
            .flex()
            .flex_col()
            .gap(px(theme.lg))
            .child(section_label(theme, "Name"))
            .child(name)
            .into_any_element()
    }

    fn page_translation(&self, app: &MainView, cx: &mut Context<MainView>) -> AnyElement {
        let theme = &app.theme;
        let mut column = div()
            .flex()
            .flex_col()
            .gap(px(theme.lg))
            .child(section_label(theme, "Translation (Japanese VNs)"))
            .child(checkbox(
                theme,
                "wizard-tr-enable",
                "Translate Japanese dialogue via DeepL",
                self.tr_enable,
                true,
                cx.listener(|this, _event: &ClickEvent, _window, cx| {
                    if let Modal::Wizard(state) = &mut this.modal {
                        state.tr_enable = !state.tr_enable;
                    }
                    cx.notify();
                }),
            ))
            .child(
                div()
                    .pt(px(theme.lg))
                    .text_size(px(theme.body))
                    .font_weight(FontWeight::BOLD)
                    .text_color(if self.tr_enable {
                        theme.text
                    } else {
                        theme.text_disabled
                    })
                    .child("Debug"),
            )
            .child(checkbox(
                theme,
                "wizard-tr-browser",
                "Show the DeepL browser window (debug)",
                self.show_browser,
                self.tr_enable,
                cx.listener(|this, _event: &ClickEvent, _window, cx| {
                    if let Modal::Wizard(state) = &mut this.modal {
                        state.show_browser = !state.show_browser;
                    }
                    cx.notify();
                }),
            ))
            .child(checkbox(
                theme,
                "wizard-tr-hooker",
                "Show Textractor during Setup (debug)",
                self.show_hooker,
                self.tr_enable,
                cx.listener(|this, _event: &ClickEvent, _window, cx| {
                    if let Modal::Wizard(state) = &mut this.modal {
                        state.show_hooker = !state.show_hooker;
                    }
                    cx.notify();
                }),
            ));
        column = column.child(muted_line(theme, TRANSLATE_NOTE));
        column.into_any_element()
    }
}

impl MainView {
    pub fn open_wizard(
        &mut self,
        gid: Option<String>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let gid = gid.unwrap_or_default();
        let editing = !gid.is_empty();
        let state = WizardState::new(self, if editing { Some(gid.clone()) } else { None }, cx);
        let focus = state.path.focus.clone();
        self.modal = Modal::Wizard(state);
        window.focus(&focus);
        if editing {
            self.rpc(
                window,
                cx,
                "game.data",
                json!({ "gid": gid }),
                move |this, reply, _window, _cx| {
                    let Ok(value) = reply else {
                        return;
                    };
                    let Modal::Wizard(state) = &mut this.modal else {
                        return;
                    };
                    if state.gid != gid {
                        return;
                    }
                    state.fill(&value);
                },
            );
        }
        cx.notify();
    }

    pub fn wizard_key(&mut self, key: &str, window: &mut Window, cx: &mut Context<Self>) -> bool {
        match key {
            "pageup" => {
                if let Modal::Wizard(state) = &mut self.modal {
                    state.page = state.page.saturating_sub(1);
                }
                cx.notify();
                true
            }
            "pagedown" => {
                if let Modal::Wizard(state) = &mut self.modal {
                    state.page = (state.page + 1).min(PAGES - 1);
                }
                cx.notify();
                true
            }
            "enter" | "return" => {
                if matches!(&self.modal, Modal::Wizard(state) if state.can_save()) {
                    self.finish_wizard(window, cx);
                }
                true
            }
            _ => false,
        }
    }

    fn finish_wizard(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        match std::mem::replace(&mut self.modal, Modal::None) {
            Modal::Wizard(mut state) => {
                state.finish(self, window, cx);
                if matches!(&self.modal, Modal::None) {
                    self.modal = Modal::Wizard(state);
                }
            }
            other => self.modal = other,
        }
        cx.notify();
    }
}
