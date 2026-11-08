//! Settings dialog: Wine prefix, Proton default, UI font and vkBasalt layer.
//!
//! The state is rebuilt from the app's live config every time the dialog
//! opens (`open_settings`), so a refresh of the Proton or font lists is
//! reflected without any cache to invalidate.
use gpui::prelude::*;
use gpui::{AnyElement, ClickEvent, Context, SharedString, Window, div, px};
use serde_json::json;

use crate::app::{ComboKind, FieldId, MainView, Modal, Proton};
use crate::theme::Theme;
use crate::ui::button::{Variant, button};
use crate::ui::combo::{ComboItem, ComboState, combo};
use crate::ui::modal::{ModalCard, overlay};
use crate::ui::spin::{SpinState, spin};
use crate::ui::text_field::{FieldState, text_field};

pub struct SettingsState {
    pub prefix: FieldState,
    pub layer: FieldState,
    pub proton: ComboState,
    pub font: ComboState,
    pub font_size: SpinState,
    pub gpu_status: String,
    /// How many families the (long) font list currently renders.
    font_visible: usize,
}

/// Families render a page at a time: shaping several hundred rows in one
/// frame stalls the window, and nobody scrolls that far anyway.
const FONT_PAGE: usize = 60;
const FONT_MORE: &str = "\u{22ef} show more fonts";

fn font_items(app: &MainView, visible: usize) -> Vec<ComboItem> {
    let mut items: Vec<ComboItem> =
        std::iter::once(ComboItem::new("Default (system)", "")).collect();
    items.extend(
        app.fonts
            .iter()
            .take(visible)
            .map(|name| ComboItem::new(name.clone(), name.clone())),
    );
    if app.fonts.len() > visible {
        items.push(ComboItem::new(FONT_MORE, "__more__"));
    }
    items
}

fn setting(app: &MainView, key: &str) -> String {
    app.settings
        .get(key)
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string()
}

/// Combo items for `protons`, selecting `configured`. A configured value the
/// scan did not find stays selectable (and is saved back verbatim).
fn proton_items(protons: &[Proton], configured: &str) -> (Vec<ComboItem>, usize) {
    let mut items: Vec<ComboItem> = protons
        .iter()
        .map(|proton| ComboItem::new(proton.label.clone(), proton.value.clone()))
        .collect();
    let mut selected = items.iter().position(|item| item.value == configured);
    if selected.is_none() && !configured.is_empty() {
        items.push(ComboItem::new(
            format!("{configured}  (not detected)"),
            configured.to_string(),
        ));
        selected = Some(items.len() - 1);
    }
    if items.is_empty() {
        items.push(ComboItem::new("(none detected)", ""));
    }
    (items, selected.unwrap_or(0))
}

fn field_row(theme: &Theme, text: &str, control: AnyElement) -> AnyElement {
    div()
        .flex()
        .flex_row()
        .items_center()
        .gap(px(theme.xxl))
        .child(
            div()
                .w(px(200.))
                .flex_none()
                .text_size(px(theme.body))
                .text_color(theme.text)
                .child(text.to_string()),
        )
        .child(div().flex_1().min_w(px(0.)).child(control))
        .into_any_element()
}

impl SettingsState {
    pub fn new(app: &MainView, cx: &mut Context<MainView>) -> Self {
        let mut prefix = FieldState::new(cx);
        prefix.set_text(setting(app, "prefix"));
        let mut layer = FieldState::new(cx);
        layer.set_text(setting(app, "layer_dir"));

        let configured_proton = setting(app, "proton");
        let (proton_choices, proton_selected) = proton_items(&app.protons, &configured_proton);
        let mut proton = ComboState::new(cx, proton_choices);
        proton.selected = proton_selected;

        let font_visible = FONT_PAGE;
        let mut font = ComboState::new(cx, font_items(app, font_visible));
        font.select_value(&setting(app, "gui.font"));

        let size = app
            .settings
            .get("gui.font_size")
            .and_then(|value| value.as_i64())
            .unwrap_or(0)
            .max(0);

        SettingsState {
            prefix,
            layer,
            proton,
            font,
            font_size: SpinState::new(cx, size, 0, 72, Some("Default")),
            gpu_status: "Cached; refresh after a GPU change.".to_string(),
            font_visible,
        }
    }

    pub fn render(
        &self,
        app: &MainView,
        window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        let entity = cx.entity();
        let caption = px(theme.caption);
        let muted = theme.text_secondary;

        let prefix = text_field(
            theme,
            entity.clone(),
            SharedString::from("settings-prefix"),
            FieldId::SettingsPrefix,
            &self.prefix,
            window,
            "(project shared prefix)",
            None,
            None,
            false,
        );

        let proton_combo = combo(
            theme,
            "settings-proton",
            "settings-proton",
            &self.proton,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                this.toggle_combo(ComboKind::SettingsProton, cx);
            }),
            {
                let entity = entity.clone();
                move |index, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        if let Modal::Settings(state) = &mut this.modal {
                            state.proton.selected = index;
                            state.proton.open = false;
                        }
                        cx.notify();
                    });
                }
            },
        );
        let proton_refresh = button(
            theme,
            "settings-proton-refresh",
            "Refresh",
            true,
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                if let Modal::Settings(state) = &mut this.modal {
                    let current = state.proton.value();
                    let (items, selected) = proton_items(&this.protons, &current);
                    state.proton.items = items;
                    state.proton.selected = selected;
                    state.proton.open = false;
                }
                cx.notify();
            }),
        );
        let proton_row = div()
            .flex()
            .flex_row()
            .items_center()
            .gap(px(theme.sm))
            .child(proton_combo)
            .child(proton_refresh);

        let font_combo = combo(
            theme,
            "settings-font",
            "settings-font",
            &self.font,
            None,
            cx.listener(|this, _event: &ClickEvent, _window, cx| {
                this.toggle_combo(ComboKind::SettingsFont, cx);
            }),
            {
                let entity = entity.clone();
                move |index, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        let mut reload = None;
                        if let Modal::Settings(state) = &mut this.modal {
                            if state.font.items.get(index).map(|item| item.value.as_str())
                                == Some("__more__")
                            {
                                state.font_visible += FONT_PAGE;
                                reload = Some(state.font_visible);
                            } else {
                                state.font.selected = index;
                                state.font.open = false;
                            }
                        }
                        if let Some(visible) = reload {
                            let items = font_items(this, visible);
                            if let Modal::Settings(state) = &mut this.modal {
                                state.font.items = items;
                                state.font.selected = 0;
                                state.font.open = true;
                            }
                        }
                        cx.notify();
                    });
                }
            },
        );
        let font_size = spin(
            theme,
            entity.clone(),
            "settings-font-size",
            &self.font_size,
            FieldId::SettingsFontSize,
            window,
            Some(px(120.)),
            {
                let entity = entity.clone();
                move |delta, _event, _window, cx| {
                    entity.update(cx, |this, cx| {
                        if let Modal::Settings(state) = &mut this.modal {
                            state.font_size.step(delta);
                        }
                        cx.notify();
                    });
                }
            },
        );
        let font_row = div()
            .flex()
            .flex_col()
            .items_start()
            .gap(px(theme.sm))
            .child(font_combo)
            .child(font_size);

        let gpu_row = div()
            .flex()
            .flex_row()
            .items_center()
            .gap(px(theme.sm))
            .child(
                div()
                    .flex_1()
                    .min_w(px(0.))
                    .text_size(caption)
                    .text_color(muted)
                    .child(self.gpu_status.clone()),
            )
            .child(button(
                theme,
                "settings-gpu-refresh",
                "Refresh",
                true,
                Variant::Normal,
                cx.listener(|this, _event: &ClickEvent, window, cx| {
                    if let Modal::Settings(state) = &mut this.modal {
                        state.gpu_status = "Probing GPUs…".to_string();
                    }
                    this.rpc(window, cx, "gpus.refresh", json!({}), |_, _, _, _| {});
                    cx.notify();
                }),
            ));

        let layer = text_field(
            theme,
            entity.clone(),
            SharedString::from("settings-layer"),
            FieldId::SettingsLayer,
            &self.layer,
            window,
            "empty = system layer",
            None,
            None,
            false,
        );

        let selected_proton = self.proton.value();
        let no_wow64 = app
            .protons
            .iter()
            .any(|proton| proton.value == selected_proton && !proton.wow64);

        let mut body = div()
            .flex()
            .flex_col()
            .gap(px(theme.xl))
            .child(field_row(theme, "Wine prefix default:", prefix))
            .child(field_row(
                theme,
                "Proton default:",
                proton_row.into_any_element(),
            ))
            .child(div().text_size(caption).text_color(muted).child(
                "Found under compatibilitytools.d; UMU-Proton is the safe default.",
            ));
        if no_wow64 {
            body = body.child(div().text_size(caption).text_color(theme.error).child(
                "This build has no new WoW64 support — launching falls back to \
                         UMU-Proton.",
            ));
        }
        body = body
            .child(field_row(
                theme,
                "Font:",
                font_row.into_any_element(),
            ))
            .child(field_row(
                theme,
                "GPU list:",
                gpu_row.into_any_element(),
            ))
            .child(field_row(theme, "vkBasalt layer dir:", layer))
            .child(div().text_size(caption).text_color(muted).child(
                "Font applies immediately; the other changes apply on the next launch.",
            ));

        let footer = crate::ui::modal::footer(
            theme,
            vec![
                button(
                    theme,
                    "settings-cancel",
                    "Cancel",
                    true,
                    Variant::Normal,
                    cx.listener(|this, _event: &ClickEvent, _window, cx| {
                        this.modal = Modal::None;
                        cx.notify();
                    }),
                ),
                button(
                    theme,
                    "settings-save",
                    "Save",
                    true,
                    Variant::Normal,
                    cx.listener(|this, _event: &ClickEvent, window, cx| {
                        this.save_settings(window, cx);
                    }),
                ),
            ],
        );

        overlay(
            theme,
            "settings",
            ModalCard {
                title: SharedString::from("Settings"),
                width: px(600.),
                height: None,
            },
            body.into_any_element(),
            Some(footer),
        )
    }
}

impl MainView {
    pub fn open_settings(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let state = SettingsState::new(self, cx);
        self.modal = Modal::Settings(state);
        if let Modal::Settings(state) = &self.modal {
            window.focus(&state.prefix.focus);
        }
        cx.notify();
    }

    pub fn save_settings(&mut self, window: &Window, cx: &mut Context<Self>) {
        let Modal::Settings(state) = &self.modal else {
            return;
        };
        let patch = json!({
            "prefix": state.prefix.text.trim(),
            "proton": state.proton.value(),
            "layer_dir": state.layer.text.trim(),
            "gui.font": state.font.value(),
            "gui.font_size": state.font_size.value(),
        });
        self.rpc(
            window,
            cx,
            "settings.save",
            json!({ "patch": patch }),
            |this, reply, _window, cx| {
                let Ok(value) = reply else {
                    return;
                };
                let Some(settings) = value.get("settings") else {
                    return;
                };
                this.settings = settings.clone();
                this.apply_theme_from_settings();
                this.modal = Modal::None;
                cx.notify();
            },
        );
    }

    pub fn settings_key(&mut self, key: &str, window: &mut Window, cx: &mut Context<Self>) -> bool {
        match key {
            "enter" | "return" => {
                self.save_settings(window, cx);
                true
            }
            "tab" | "up" | "down" => {
                let Modal::Settings(state) = &self.modal else {
                    return false;
                };
                let handles = [
                    &state.prefix.focus,
                    &state.proton.focus,
                    &state.font.focus,
                    &state.font_size.field.focus,
                    &state.layer.focus,
                ];
                let current = handles
                    .iter()
                    .position(|handle| handle.is_focused(window))
                    .unwrap_or(0);
                let backwards = key == "up";
                let count = handles.len() as isize;
                let step = if backwards { -1 } else { 1 };
                let next = (current as isize + step).rem_euclid(count) as usize;
                window.focus(handles[next]);
                true
            }
            _ => false,
        }
    }
}
