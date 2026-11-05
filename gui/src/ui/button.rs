//! Buttons: one chrome for the top bar, action rows, dialog footers and the
//! log's Copy. omarchy has no filled CTA, so `Primary` is unused by design.
use gpui::prelude::*;
use gpui::{
    AnyElement, App, ClickEvent, ElementId, FontWeight, SharedString, Window, div, px,
};

use crate::theme::Theme;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Variant {
    Normal,
    Text,
}

pub fn button(
    theme: &Theme,
    id: impl Into<ElementId>,
    label: impl Into<SharedString>,
    enabled: bool,
    variant: Variant,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> AnyElement {
    let hover = theme.hover;
    let focus = theme.focus;
    let border = theme.border;
    let radius = theme.radius;
    let height = theme.control_height;
    let pad_x = theme.control_padding_x;
    let text_size = theme.body;
    let text_color = if enabled { theme.text } else { theme.text_disabled };
    let normal_fill = theme.background;
    let text_family = theme.ui_font.clone();

    let mut el = div()
        .id(id)
        .flex()
        .items_center()
        .justify_center()
        .h(px(height))
        .px(px(pad_x))
        .rounded(px(radius))
        .font_family(text_family)
        .text_size(px(text_size))
        .text_color(text_color)
        .child(label.into());
    if variant == Variant::Normal {
        el = el.border_1().border_color(border);
    }
    el = el.bg(normal_fill);
    if enabled {
        el = el
            .cursor_pointer()
            .hover(move |style| style.bg(hover))
            .focus(move |style| style.bg(focus).border_color(border))
            .on_click(on_click);
    }
    el.into_any_element()
}

/// A button that fills the row it sits in (Add…/Edit…/Remove).
pub fn button_fill(
    theme: &Theme,
    id: impl Into<ElementId>,
    label: impl Into<SharedString>,
    enabled: bool,
    on_click: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> AnyElement {
    div()
        .flex_1()
        .child(button(theme, id, label, enabled, Variant::Normal, on_click))
        .into_any_element()
}

/// Bold section label used by the wizard's page headers and "Games".
pub fn section_label(theme: &Theme, text: impl Into<SharedString>) -> AnyElement {
    div()
        .text_size(px(theme.subtitle))
        .font_weight(FontWeight::BOLD)
        .text_color(theme.text)
        .child(text.into())
        .into_any_element()
}
