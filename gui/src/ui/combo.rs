//! Dropdown: a button showing the selected label plus an `anchored` popup
//! card (label and stored value are separate, e.g. the GPU list's
//! "  (display)" suffix and the variant notes).
use gpui::prelude::*;
use gpui::{
    AnyElement, App, ClickEvent, ElementId, FocusHandle, Pixels, SharedString, Window, div, px,
};

use crate::theme::Theme;

#[derive(Clone, Debug)]
pub struct ComboItem {
    pub label: String,
    pub value: String,
}

impl ComboItem {
    pub fn new(label: impl Into<String>, value: impl Into<String>) -> Self {
        ComboItem { label: label.into(), value: value.into() }
    }
}

pub struct ComboState {
    pub items: Vec<ComboItem>,
    pub selected: usize,
    pub open: bool,
    pub focus: FocusHandle,
}

impl ComboState {
    pub fn new(cx: &mut App, items: Vec<ComboItem>) -> Self {
        ComboState {
            items,
            selected: 0,
            open: false,
            focus: cx.focus_handle(),
        }
    }

    pub fn label(&self) -> String {
        self.items
            .get(self.selected)
            .map(|item| item.label.clone())
            .unwrap_or_default()
    }

    pub fn value(&self) -> String {
        self.items
            .get(self.selected)
            .map(|item| item.value.clone())
            .unwrap_or_default()
    }

    pub fn find_value(&self, value: &str) -> Option<usize> {
        self.items.iter().position(|item| item.value == value)
    }

    pub fn select_value(&mut self, value: &str) -> bool {
        match self.find_value(value) {
            Some(ix) => {
                self.selected = ix;
                true
            }
            None => false,
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub fn combo(
    theme: &Theme,
    id: impl Into<ElementId>,
    prefix: &str,
    state: &ComboState,
    width: Option<Pixels>,
    on_toggle: impl Fn(&ClickEvent, &mut Window, &mut App) + 'static,
    on_pick: impl Fn(usize, &ClickEvent, &mut Window, &mut App) + 'static,
) -> AnyElement {
    let id = id.into();
    let row_height = theme.popup_row_height;
    let hover = theme.hover;
    let selected_fill = theme.selected;
    let selected_text = theme.selected_text;
    let text_color = theme.text;
    let radius = theme.radius;
    let border = theme.border;
    let popup_bg = theme.background;
    let panel_padding = theme.popup_padding;
    let text_size = theme.body;
    let family = theme.ui_font.clone();
    let popup_width = width.unwrap_or(px(theme.dropdown_width.max(300.)));
    let control_height = theme.control_height;
    let pad_x = theme.control_padding_x;

    let mut trigger = div()
        .id(id.clone())
        .flex()
        .items_center()
        .justify_between()
        .h(px(control_height))
        .px(px(pad_x))
        .gap(px(theme.sm))
        .rounded(px(radius))
        .border_1()
        .border_color(border)
        .bg(theme.background)
        .font_family(family.clone())
        .text_size(px(text_size))
        .text_color(text_color)
        .cursor_pointer()
        .track_focus(&state.focus)
        .hover(move |style| style.bg(hover))
        .on_click(on_toggle)
        .child(div().truncate().child(state.label()))
        .child(div().text_color(theme.text_secondary).child("▾"));
    trigger = match width {
        Some(w) => trigger.w(w),
        None => trigger.flex_1().min_w(px(0.)),
    };

    let mut rows = div()
        .id(SharedString::from(format!("{prefix}-popup")))
        .flex()
        .flex_col()
        .flex_none()
        .w(popup_width)
        .max_h(px(220.))
        .overflow_y_scroll()
        .bg(popup_bg)
        .border_1()
        .border_color(border)
        .rounded(px(radius))
        .p(px(panel_padding))
        .occlude();
    let on_pick = std::rc::Rc::new(on_pick);
    for (ix, item) in state.items.iter().enumerate() {
        let is_selected = ix == state.selected;
        let on_pick = on_pick.clone();
        rows = rows.child(
            div()
                .id(SharedString::from(format!("{prefix}-row-{ix}")))
                .flex()
                .items_center()
                .h(px(row_height))
                .px(px(pad_x))
                .rounded(px(radius))
                .font_family(family.clone())
                .text_size(px(text_size))
                .text_color(if is_selected { selected_text } else { text_color })
                .bg(if is_selected { selected_fill } else { popup_bg })
                .cursor_pointer()
                .hover(move |style| style.bg(hover))
                .on_click(move |event, window, cx| on_pick(ix, event, window, cx))
                .child(div().truncate().child(item.label.clone())),
        );
    }

    let mut wrapper = div()
        .id(SharedString::from(format!("combo-wrap-{id}")))
        .flex()
        .flex_col();
    wrapper = wrapper.child(trigger);
    if state.open && !state.items.is_empty() {
        // Inline list: an `anchored` popup is clipped to the parent row, so the
        // options were cut off after a row or two. Expanding in place keeps
        // every entry reachable inside a scrolling dialog.
        wrapper = wrapper.child(rows);
    }
    wrapper.into_any_element()
}
