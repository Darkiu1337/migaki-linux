//! Checkbox: a 14 px square with the omarchy control chrome, label to the
//! right. Used for the wizard's runner picker (exclusive group) and booleans.
use gpui::prelude::*;
use gpui::{AnyElement, App, ClickEvent, ElementId, SharedString, Window, div, px};

use crate::theme::Theme;

pub fn checkbox(
    theme: &Theme,
    id: impl Into<ElementId>,
    label: impl Into<SharedString>,
    checked: bool,
    enabled: bool,
    on_toggle: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
) -> AnyElement {
    let hover = theme.hover;
    let border = if enabled { theme.border } else { theme.text_disabled };
    let fill = if checked { theme.focus } else { theme.background };
    let text_color = if enabled { theme.text } else { theme.text_disabled };
    let radius = theme.radius;
    let gap = theme.lg;
    let text_size = theme.body;
    let family = theme.ui_font.clone();
    let mark_color = theme.accent;

    let mut row = div()
        .id(id)
        .flex()
        .items_center()
        .gap(px(gap))
        .font_family(family)
        .text_size(px(text_size))
        .text_color(text_color)
        .child(
            div()
                .flex()
                .items_center()
                .justify_center()
                .w(px(14.))
                .h(px(14.))
                .rounded(px(radius))
                .border_1()
                .border_color(border)
                .bg(fill)
                .text_size(px(theme.caption))
                .text_color(mark_color)
                .child(if checked { "✓" } else { "" }),
        )
        .child(label.into());
    if enabled {
        row = row
            .cursor_pointer()
            .hover(move |style| style.bg(hover))
            .on_click(on_toggle);
    }
    row.into_any_element()
}
