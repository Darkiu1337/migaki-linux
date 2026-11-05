//! In-window modal card: scrim over the whole window, centered card with a
//! title, hairline, body and an optional footer row.
use gpui::prelude::*;
use gpui::{AnyElement, ElementId, FontWeight, MouseButton, Pixels, SharedString, div, px};

use crate::theme::Theme;

pub struct ModalCard {
    pub title: SharedString,
    pub width: Pixels,
    pub height: Option<Pixels>,
}

#[allow(clippy::too_many_arguments)]
pub fn overlay(
    theme: &Theme,
    id: impl Into<ElementId>,
    card: ModalCard,
    body: AnyElement,
    footer: Option<AnyElement>,
) -> AnyElement {
    let scrim = theme.scrim;
    let background = theme.background;
    let border = theme.border;
    let radius = theme.radius;
    let padding = theme.panel_padding;
    let gap = theme.panel_gap;
    let title_size = theme.heading;
    let text_color = theme.text;
    let family = theme.ui_font.clone();
    let id: ElementId = id.into();

    let mut panel = div()
        .id(SharedString::from(format!("card-{id}")))
        .occlude()
        .flex()
        .flex_col()
        .w(card.width)
        .gap(px(gap))
        .p(px(padding))
        .bg(background)
        .border_1()
        .border_color(border)
        .rounded(px(radius))
        .font_family(family)
        .text_size(px(theme.body))
        .text_color(text_color)
        .child(
            div()
                .text_size(px(title_size))
                .font_weight(FontWeight::BOLD)
                .child(card.title),
        )
        .child(crate::ui::divider::divider(theme))
        .child(div().flex_1().min_h(px(0.)).child(body));
    if let Some(height) = card.height {
        panel = panel.h(height);
    }
    if let Some(footer) = footer {
        panel = panel.child(footer);
    }

    div()
        .id(SharedString::from(format!("scrim-{id}")))
        .absolute()
        .inset_0()
        .size_full()
        .bg(scrim)
        .occlude()
        // The scrim swallows clicks: dialogs close on Esc/their own buttons.
        .on_mouse_down(MouseButton::Left, |_, _, _| {})
        .on_mouse_up(MouseButton::Left, |_, _, _| {})
        .flex()
        .items_center()
        .justify_center()
        .child(panel)
        .into_any_element()
}

/// Right-aligned footer row (dialog buttons).
pub fn footer(theme: &Theme, buttons: Vec<AnyElement>) -> AnyElement {
    div()
        .flex()
        .flex_row()
        .items_center()
        .justify_end()
        .gap(px(theme.sm))
        .children(buttons)
        .into_any_element()
}

/// Right-aligned footer with a leading button (the wizard's Cancel).
pub fn footer_split(theme: &Theme, leading: AnyElement, buttons: Vec<AnyElement>) -> AnyElement {
    div()
        .flex()
        .flex_row()
        .items_center()
        .gap(px(theme.sm))
        .child(leading)
        .child(div().flex_1())
        .children(buttons)
        .into_any_element()
}
