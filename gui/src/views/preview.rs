//! "Resolved command" modal: the argv the runner would use for this game.
use gpui::prelude::*;
use gpui::{AnyElement, ClickEvent, Context, div, px};

use crate::app::{MainView, Modal};
use crate::theme::Theme;
use crate::ui::button::{Variant, button};
use crate::ui::modal::{ModalCard, footer, overlay};

pub fn render(command: &str, theme: &Theme, cx: &mut Context<MainView>) -> AnyElement {
    let body = div()
        .w_full()
        .font_family(theme.mono_font.clone())
        .text_size(px(theme.body_small))
        .text_color(theme.text)
        .child(command.to_string());
    let ok = button(
        theme,
        "preview-ok",
        "OK",
        true,
        Variant::Normal,
        cx.listener(|this: &mut MainView, _: &ClickEvent, _window, cx| {
            this.modal = Modal::None;
            cx.notify();
        }),
    );
    overlay(
        theme,
        "preview",
        ModalCard {
            title: "Resolved command".into(),
            width: px(640.),
            height: None,
        },
        body.into_any_element(),
        Some(footer(theme, vec![ok])),
    )
}
