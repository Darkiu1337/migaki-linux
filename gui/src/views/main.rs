//! The main window: library list, detail pane, action row and live log.
//!
//! Layout mirrors gui/qml/Main.qml — a 300 px library column, a hairline, then
//! the detail/actions/log column — rendered with the shared widget primitives.
use std::path::PathBuf;

use gpui::prelude::*;
use gpui::{AnyElement, ClickEvent, Context, ObjectFit, Window, div, img, px};

use crate::app::{MainView, Modal, label_text};
use crate::ui::button::{Variant, button, button_fill, section_label};
use crate::ui::divider::{divider, vertical_divider};
use crate::ui::scroll::{scroll_area, thumb, with_thumb};
use crate::views::prompt::{PromptKind, PromptState};

impl MainView {
    pub fn render_main(&mut self, _window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let theme = self.theme.clone();
        let hover = theme.hover;

        let top = div()
            .flex()
            .flex_col()
            .w_full()
            .child(
                div()
                    .flex()
                    .flex_row()
                    .items_center()
                    .gap(px(theme.sm))
                    .px(px(theme.control_padding_x))
                    .py(px(theme.control_padding_y))
                    .child(button(
                        &theme,
                        "menu-settings",
                        "Settings",
                        true,
                        Variant::Text,
                        cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                            this.open_settings(window, cx);
                            cx.notify();
                        }),
                    ))
                    .child(button(
                        &theme,
                        "menu-quit",
                        "Quit",
                        true,
                        Variant::Text,
                        cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                            this.quit(window, cx);
                        }),
                    )),
            )
            .child(divider(&theme));

        // -- library -------------------------------------------------------
        let mut rows = div().flex().flex_col().w_full().gap(px(theme.row_gap));
        for (index, game) in self.games.iter().enumerate() {
            let selected = self.selected.as_deref() == Some(game.gid.as_str());
            let icon = self
                .icons
                .get(&game.gid)
                .cloned()
                .or_else(|| (!game.icon.is_empty()).then(|| game.icon.clone()));
            let mut row = div()
                .id(("game-row", index))
                .flex()
                .flex_row()
                .items_center()
                .gap(px(theme.sm))
                .w_full()
                .px(px(theme.row_padding_x))
                .py(px(theme.xxs))
                .rounded(px(theme.radius))
                .cursor_pointer()
                .hover(move |style| style.bg(hover))
                .on_click(cx.listener(move |this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.select_index(Some(index), window, cx);
                    cx.notify();
                }))
                .when(selected, |el| el.bg(theme.selected));
            if let Some(path) = icon {
                row = row.child(
                    img(PathBuf::from(path))
                        .w(px(32.))
                        .h(px(32.))
                        .object_fit(ObjectFit::Contain),
                );
            }
            row = row.child(
                div()
                    .flex()
                    .flex_col()
                    .flex_1()
                    .min_w(px(0.))
                    .child(
                        div()
                            .truncate()
                            .text_size(px(theme.body))
                            .text_color(if selected { theme.selected_text } else { theme.text })
                            .child(game.name.clone()),
                    )
                    .child(
                        div()
                            .truncate()
                            .text_size(px(theme.caption))
                            .text_color(if selected {
                                theme.selected_text
                            } else {
                                theme.text_secondary
                            })
                            .child(game.info.clone()),
                    ),
            );
            rows = rows.child(row);
        }
        let library_list = div()
            .id("library-list")
            .flex()
            .flex_col()
            .flex_1()
            .min_h(px(0.))
            .w_full()
            .overflow_y_scroll()
            .child(rows);

        let has_game = self.selected.is_some();
        let library = div()
            .flex()
            .flex_col()
            .w(px(300.))
            .flex_none()
            .h_full()
            .gap(px(theme.control_gap))
            .child(section_label(&theme, "Games"))
            .child(library_list)
            .child(
                div()
                    .flex()
                    .flex_row()
                    .w_full()
                    .gap(px(theme.sm))
                    .child(button_fill(
                        &theme,
                        "library-add",
                        "Add…",
                        true,
                        cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                            this.open_wizard(None, window, cx);
                            cx.notify();
                        }),
                    ))
                    .child(button_fill(
                        &theme,
                        "library-edit",
                        "Edit…",
                        has_game,
                        cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                            let gid = this.selected.clone();
                            this.open_wizard(gid, window, cx);
                            cx.notify();
                        }),
                    ))
                    .child(button_fill(
                        &theme,
                        "library-remove",
                        "Remove",
                        has_game,
                        cx.listener(|this: &mut MainView, _: &ClickEvent, _window, cx| {
                            this.modal = Modal::Prompt(PromptState {
                                kind: PromptKind::RemoveGame,
                                title: "Remove game".into(),
                                body: "Remove this game from the library?".into(),
                                buttons: vec!["Remove".into(), "Cancel".into()],
                            });
                            cx.notify();
                        }),
                    )),
            );

        // -- detail --------------------------------------------------------
        let mut detail = div()
            .flex()
            .flex_col()
            .flex_1()
            .min_w(px(0.))
            .gap(px(theme.xxs));
        if self.detail.name.is_empty() {
            detail = detail.child(label_text(&theme, "Select a game.", theme.body, theme.text));
        } else {
            detail = detail.child(
                div()
                    .text_size(px(theme.subtitle))
                    .text_color(theme.text)
                    .child(self.detail.name.clone()),
            );
            for line in &self.detail.lines {
                detail = detail.child(
                    div()
                        .text_size(px(theme.body_small))
                        .text_color(theme.text_secondary)
                        .child(line.clone()),
                );
            }
        }
        let icon_path = self
            .selected
            .as_ref()
            .and_then(|gid| self.icons.get(gid))
            .cloned();
        let mut header = div().flex().flex_row().items_center().w_full().gap(px(theme.md));
        if let Some(path) = icon_path {
            header = header
                .child(
                    img(PathBuf::from(path))
                        .w(px(64.))
                        .h(px(64.))
                        .object_fit(ObjectFit::Contain),
                )
                .child(
                    div()
                        .h(px(64.))
                        .w(px(1.))
                        .bg(theme.hairline)
                        .into_any_element(),
                );
        }
        header = header.child(detail);

        // -- actions -------------------------------------------------------
        let running = self.running.is_some();
        let ready = has_game && !running;
        let available = self.available();
        let actions = div()
            .flex()
            .flex_row()
            .flex_wrap()
            .w_full()
            .gap(px(theme.sm))
            .child(button(
                &theme,
                "action-launch",
                "Launch",
                ready && available,
                Variant::Normal,
                cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.launch(false, window, cx);
                    cx.notify();
                }),
            ))
            .child(button(
                &theme,
                "action-launch-unfiltered",
                "Launch unfiltered (A/B)",
                ready && available,
                Variant::Normal,
                cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.launch(true, window, cx);
                    cx.notify();
                }),
            ))
            .child(button(
                &theme,
                "action-translate",
                "Translate",
                ready && available,
                Variant::Normal,
                cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.translate(false, window, cx);
                    cx.notify();
                }),
            ))
            .child(button(
                &theme,
                "action-setup-hooker",
                "Setup Text Hooker for translation",
                ready && available,
                Variant::Normal,
                cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.translate(true, window, cx);
                    cx.notify();
                }),
            ))
            .child(button(
                &theme,
                "action-textbox",
                "Textbox",
                available,
                Variant::Normal,
                cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.open_textbox(window, cx);
                    cx.notify();
                }),
            ))
            .child(button(
                &theme,
                "action-stop",
                "Stop",
                running && available,
                Variant::Normal,
                cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.stop_game(window, cx);
                    cx.notify();
                }),
            ))
            .child(button(
                &theme,
                "action-preview",
                "Preview command",
                has_game && available,
                Variant::Normal,
                cx.listener(|this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.preview(window, cx);
                    cx.notify();
                }),
            ));

        let status = div()
            .w_full()
            .text_size(px(theme.body_small))
            .text_color(theme.text)
            .child(self.status.clone());
        let bridge_line = if let Some(err) = &self.bridge_error {
            div()
                .w_full()
                .text_size(px(theme.caption))
                .text_color(theme.error)
                .child(format!("UI bridge unavailable: {err}"))
        } else if self.bridge_up {
            div()
                .w_full()
                .text_size(px(theme.caption))
                .text_color(theme.success)
                .child("● text bridge up")
        } else {
            div()
                .w_full()
                .text_size(px(theme.caption))
                .text_color(theme.text_secondary)
                .child("○ text bridge down")
        };

        // -- log -----------------------------------------------------------
        let mut log_lines = div().flex().flex_col().w_full().py(px(theme.xs));
        for line in &self.log {
            log_lines = log_lines.child(
                div()
                    .w_full()
                    .font_family(theme.mono_font.clone())
                    .text_size(px(theme.body_small))
                    .text_color(theme.text)
                    .child(line.clone()),
            );
        }
        let area = scroll_area("log-area", &self.log_scroll).child(log_lines);
        let log_panel = div()
            .flex()
            .flex_col()
            .flex_1()
            .min_h(px(0.))
            .w_full()
            .border_1()
            .border_color(theme.border)
            .rounded(px(theme.radius))
            .bg(theme.raised)
            .child(
                div()
                    .flex()
                    .flex_row()
                    .items_center()
                    .w_full()
                    .px(px(theme.control_padding_x))
                    .child(
                        div()
                            .flex_1()
                            .text_size(px(theme.caption))
                            .text_color(theme.text_secondary)
                            .child("Launch output appears here…"),
                    )
                    .child(button(
                        &theme,
                        "log-copy",
                        "Copy",
                        !self.log.is_empty(),
                        Variant::Text,
                        cx.listener(|this: &mut MainView, _: &ClickEvent, _window, cx| {
                            this.copy_log(cx);
                        }),
                    )),
            )
            .child(divider(&theme))
            .child(with_thumb(area, thumb(&theme, &self.log_scroll, "log-thumb")));

        let body = div()
            .flex()
            .flex_row()
            .flex_1()
            .min_h(px(0.))
            .w_full()
            .gap(px(theme.panel_gap))
            .p(px(theme.panel_padding))
            .child(library)
            .child(vertical_divider(&theme))
            .child(
                div()
                    .flex()
                    .flex_col()
                    .flex_1()
                    .min_w(px(0.))
                    .min_h(px(0.))
                    .gap(px(theme.md))
                    .child(header)
                    .child(actions)
                    .child(status)
                    .child(bridge_line)
                    .child(log_panel),
            );

        div()
            .flex()
            .flex_col()
            .flex_1()
            .min_h(px(0.))
            .w_full()
            .child(top)
            .child(body)
            .into_any_element()
    }

    pub fn render_modal(&mut self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let theme = self.theme.clone();
        match &self.modal {
            Modal::None => div().into_any_element(),
            Modal::Prompt(state) => state.render(&theme, cx),
            Modal::Preview(command) => crate::views::preview::render(command, &theme, cx),
            Modal::Threads(state) => state.render(self, window, cx),
            Modal::Settings(state) => state.render(self, window, cx),
            Modal::Wizard(state) => state.render(self, window, cx),
        }
    }
}
