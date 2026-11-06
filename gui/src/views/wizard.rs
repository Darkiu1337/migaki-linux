//! Add/edit game wizard: runner, location, filter/performance, name and
//! translation, ported from `Wizard.qml`.
//!
//! The state is built when the dialog opens (`open_wizard`); editing an
//! existing game fills it from `game.data` once the bridge answers. Detect and
//! path picks both run `reconcile` so the runner always points at a launchable
//! target.
use gpui::prelude::*;
use gpui::{AnyElement, ClickEvent, FontWeight, SharedString, Window, div, px};
use serde_json::{Value, json};

use crate::app::{FieldId, MainView, Modal};
use crate::theme::Theme;
use crate::ui::button::{Variant, button, section_label};
use crate::ui::checkbox::checkbox;
use crate::ui::combo::{ComboItem, ComboState, combo};
use crate::ui::modal::{ModalCard, footer_split, overlay};
use crate::ui::spin::{SpinState, spin};
use crate::ui::text_field::{FieldState, text_field};

/// Page count: runner, location, performance, name, translation.
const PAGES: usize = 5;

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
    pub gpu: ComboState,
    pub lang: ComboState,
    pub hud: bool,
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

fn variant_items(app: &MainView) -> Vec<ComboItem> {
    let items: Vec<ComboItem> = app
        .variants
        .iter()
        .map(|name| {
            let note = app.variant_notes.get(name).map_or("", String::as_str);
            let label = if note.is_empty() {
                name.clone()
            } else {
                format!("{name} — {note}")
            };
            ComboItem::new(label, name.clone())
        })
        .collect();
    if items.is_empty() {
        return vec![ComboItem::new("L", "L")];
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
        let gpu = ComboState::new(cx, gpu_items(app));
        let lang = ComboState::new(cx, lang_items(app));
        WizardState {
            gid: gid.unwrap_or_default(),
            runner: "proton".to_string(),
            path: FieldState::new(cx),
            name: FieldState::new(cx),
            fps: SpinState::new(cx, 60, 0, 480, Some("off")),
            variant,
            gpu,
            lang,
            hud: false,
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
            0 => self.page_runner(app, cx),
            1 => self.page_location(app, window, cx),
            2 => self.page_performance(app, window, cx),
            3 => self.page_name(app, window, cx),
            _ => self.page_translation(app, cx),
        };

        let mut body = div().flex().flex_col().gap(px(theme.lg)).child(
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
        let back = button(
            theme,
            "wizard-back",
            "Back",
            self.page > 0,
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.page = state.page.saturating_sub(1);
                }
                cx.notify();
            }),
        );
        let label = if self.page + 1 >= PAGES {
            "Finish"
        } else {
            "Next"
        };
        let next = button(
            theme,
            "wizard-next",
            label,
            true,
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, window, cx| {
                let last = matches!(&this.modal, Modal::Wizard(state) if state.page + 1 >= PAGES);
                if last {
                    this.finish_wizard(window, cx);
                } else if let Modal::Wizard(state) = &mut this.modal {
                    state.page = (state.page + 1).min(PAGES - 1);
                }
                cx.notify();
            }),
        );

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
            Some(footer_split(theme, cancel, vec![back, next])),
        )
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

    fn page_performance(
        &self,
        app: &MainView,
        window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        let entity = cx.entity();
        let variant = combo(
            theme,
            "wizard-variant",
            "wizard-variant",
            &self.variant,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.variant.open = !state.variant.open;
                }
                cx.notify();
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
        let gpu = combo(
            theme,
            "wizard-gpu",
            "wizard-gpu",
            &self.gpu,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.gpu.open = !state.gpu.open;
                }
                cx.notify();
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
        let lang = combo(
            theme,
            "wizard-lang",
            "wizard-lang",
            &self.lang,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Wizard(state) = &mut this.modal {
                    state.lang.open = !state.lang.open;
                }
                cx.notify();
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
            .child(section_label(theme, "Filter and performance"))
            .child(field_row(theme, "Variant:", variant))
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
            .child(field_row(theme, "FPS cap (0 = off):", fps))
            .child(hud)
            .child(field_row(theme, "Language:", lang))
            .child(prefix)
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
                self.finish_wizard(window, cx);
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
