//! Scrollable region with a hairline thumb drawn over the right edge.
//!
//! Wheel and keyboard scrolling come from gpui; the thumb is painted from the
//! tracked `ScrollHandle` (no thumb dragging).
use gpui::prelude::*;
use gpui::{AnyElement, ElementId, ScrollHandle, Stateful, div, px};

use crate::theme::Theme;

/// Height of the scrollbar track, needed to place the thumb.
pub fn thumb(theme: &Theme, handle: &ScrollHandle, id: impl Into<ElementId>) -> AnyElement {
    let max = handle.max_offset().height;
    let offset = handle.offset().y;
    let visible = handle.bounds().size.height;
    if max <= px(0.) || visible <= px(0.) {
        return div().into_any_element();
    }
    let content = visible + max;
    let ratio = (visible / content).clamp(0.05, 1.0);
    let thumb_h = visible * ratio;
    let travel = visible - thumb_h;
    let progress = ((px(0.) - offset) / max).clamp(0.0, 1.0);
    let top = travel * progress;
    let color = theme.hairline;

    div()
        .id(id)
        .absolute()
        .top(top)
        .right(px(1.))
        .w(px(6.))
        .h(thumb_h)
        .rounded(px(3.))
        .bg(color)
        .into_any_element()
}

/// A vertical scroller that tracks `handle`.
pub fn scroll_area(id: impl Into<ElementId>, handle: &ScrollHandle) -> Stateful<gpui::Div> {
    div()
        .id(id)
        .flex_1()
        .w_full()
        .overflow_y_scroll()
        .track_scroll(handle)
}

/// Wraps a scrolled column so its thumb paints on top.
pub fn with_thumb(area: Stateful<gpui::Div>, extra: AnyElement) -> Stateful<gpui::Div> {
    area.relative().child(extra)
}
