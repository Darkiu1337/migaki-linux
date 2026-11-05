//! Integer spin box: `−` button, digit field, `+` button. The text is the
//! source of truth; values clamp to the range on read, and `0` may render as
//! a label ("Default", "off").
use gpui::prelude::*;
use gpui::{AnyElement, App, ClickEvent, Pixels, SharedString, Window, div, px};

use crate::app::{FieldId, MainView};
use crate::theme::Theme;
use crate::ui::button::{Variant, button};
use crate::ui::text_field::{FieldState, text_field};

pub struct SpinState {
    pub field: FieldState,
    pub min: i64,
    pub max: i64,
    pub off_label: Option<&'static str>,
}

impl SpinState {
    pub fn new(cx: &mut App, value: i64, min: i64, max: i64, off_label: Option<&'static str>) -> Self {
        let mut field = FieldState::new(cx);
        field.set_text(value.clamp(min, max).to_string());
        SpinState { field, min, max, off_label }
    }

    pub fn value(&self) -> i64 {
        self.field.number().clamp(self.min, self.max)
    }

    pub fn set_value(&mut self, value: i64) {
        self.field.set_text(value.clamp(self.min, self.max).to_string());
    }

    pub fn step(&mut self, delta: i64) {
        let next = (self.value() + delta).clamp(self.min, self.max);
        self.set_value(next);
    }
}

#[allow(clippy::too_many_arguments)]
pub fn spin(
    theme: &Theme,
    entity: gpui::Entity<MainView>,
    id_prefix: &str,
    state: &SpinState,
    field_id: FieldId,
    window: &Window,
    width: Option<Pixels>,
    on_step: impl Fn(i64, &ClickEvent, &mut Window, &mut App) + 'static,
) -> AnyElement {
    let on_step = std::rc::Rc::new(on_step);
    let minus_step = on_step.clone();
    let minus = button(
        theme,
        SharedString::from(format!("{id_prefix}-minus")),
        "−",
        true,
        Variant::Normal,
        move |event, window, cx| minus_step(-1, event, window, cx),
    );
    let plus_step = on_step.clone();
    let plus = button(
        theme,
        SharedString::from(format!("{id_prefix}-plus")),
        "+",
        true,
        Variant::Normal,
        move |event, window, cx| plus_step(1, event, window, cx),
    );
    let field = text_field(
        theme,
        entity,
        SharedString::from(format!("{id_prefix}-field")),
        field_id,
        &state.field,
        window,
        "0",
        state.off_label,
        width,
        false,
    );
    div()
        .flex()
        .flex_row()
        .items_center()
        .gap(px(theme.sm))
        .child(minus)
        .child(field)
        .child(plus)
        .into_any_element()
}
