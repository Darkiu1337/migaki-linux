//! Horizontal tab strip: one click swaps the dialog's page, so every topic is
//! visible at once instead of hidden behind Back/Next.
use gpui::prelude::*;
use gpui::{AnyElement, App, ClickEvent, SharedString, Window, div, px};

use crate::theme::Theme;

/// `tabs` is `(id, label)` pairs; the id only shapes the element id, so the
/// selected tab animates under a stable name.
pub fn tab_bar(
    theme: &Theme,
    prefix: &str,
    tabs: &[(&str, &str)],
    active: usize,
    on_pick: impl Fn(usize, &ClickEvent, &mut Window, &mut App) + 'static,
) -> AnyElement {
    let selected_fill = theme.selected;
    let selected_text = theme.accent;
    let hover = theme.hover;
    let idle_text = theme.text_secondary;
    let radius = theme.radius;
    let height = theme.control_height;
    let pad_x = theme.control_padding_x;
    let text_size = theme.body;
    let family = theme.ui_font.clone();
    let on_pick = std::rc::Rc::new(on_pick);

    let mut row = div()
        .flex()
        .flex_none()
        .flex_row()
        .items_center()
        .gap(px(theme.xs));
    for (ix, (key, label)) in tabs.iter().enumerate() {
        let is_active = ix == active;
        let on_pick = on_pick.clone();
        let mut tab = div()
            .id(SharedString::from(format!("{prefix}-{key}")))
            .flex()
            .items_center()
            .h(px(height))
            .px(px(pad_x))
            .rounded(px(radius))
            .font_family(family.clone())
            .text_size(px(text_size))
            .text_color(if is_active { selected_text } else { idle_text })
            .cursor_pointer()
            .hover(move |style| style.bg(hover))
            .on_click(move |event, window, cx| on_pick(ix, event, window, cx))
            .child(label.to_string());
        if is_active {
            tab = tab.bg(selected_fill);
        }
        row = row.child(tab);
    }
    row.into_any_element()
}
