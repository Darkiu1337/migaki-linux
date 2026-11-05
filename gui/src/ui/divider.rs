//! Hairline rules: the top bar's underline, column splits and modal headers.
use gpui::prelude::*;
use gpui::{AnyElement, div, px};

use crate::theme::Theme;

pub fn divider(theme: &Theme) -> AnyElement {
    div()
        .w_full()
        .h(px(1.))
        .bg(theme.hairline)
        .into_any_element()
}

pub fn vertical_divider(theme: &Theme) -> AnyElement {
    div()
        .h_full()
        .w(px(1.))
        .bg(theme.hairline)
        .into_any_element()
}
