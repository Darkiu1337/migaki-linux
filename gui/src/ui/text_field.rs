//! Single-line text field with caret, selection, clipboard and IME.
//!
//! gpui has no text widget of its own, so the field paints a shaped line and
//! registers an input handler for the focused frame; every edit lands in the
//! modal state that owns the field (`MainView: EntityInputHandler`).
use std::ops::Range;

use gpui::prelude::*;
use gpui::{
    App, Bounds, Element, ElementId, ElementInputHandler, Entity, FocusHandle, GlobalElementId,
    Hsla, InspectorElementId, IntoElement, LayoutId, MouseButton, PaintQuad, Pixels, ShapedLine,
    SharedString, Style, TextRun, Window, div, fill, point, px, relative, size,
};

use crate::app::{FieldId, MainView};
use crate::theme::Theme;

#[derive(Clone, Debug)]
pub struct FieldState {
    pub text: String,
    /// Byte index, always on a char boundary.
    pub caret: usize,
    /// Anchor of a shift-selection; `None` when nothing is selected.
    pub anchor: Option<usize>,
    pub marked: Option<Range<usize>>,
    pub focus: FocusHandle,
    pub layout: Option<ShapedLine>,
    pub bounds: Option<Bounds<Pixels>>,
}

impl FieldState {
    pub fn new(cx: &mut App) -> Self {
        FieldState {
            text: String::new(),
            caret: 0,
            anchor: None,
            marked: None,
            focus: cx.focus_handle(),
            layout: None,
            bounds: None,
        }
    }

    pub fn set_text(&mut self, text: impl Into<String>) {
        self.text = text.into();
        self.caret = self.text.len();
        self.anchor = None;
        self.marked = None;
    }

    pub fn selected_range(&self) -> Range<usize> {
        match self.anchor {
            Some(anchor) if anchor != self.caret => anchor.min(self.caret)..anchor.max(self.caret),
            _ => self.caret..self.caret,
        }
    }

    pub fn move_to(&mut self, offset: usize) {
        self.caret = clamp_boundary(&self.text, offset);
        self.anchor = None;
    }

    pub fn select_to(&mut self, offset: usize) {
        if self.anchor.is_none() {
            self.anchor = Some(self.caret);
        }
        self.caret = clamp_boundary(&self.text, offset);
    }

    pub fn previous_boundary(&self, offset: usize) -> usize {
        self.text
            .char_indices()
            .rev()
            .find_map(|(idx, _)| (idx < offset).then_some(idx))
            .unwrap_or(0)
    }

    pub fn next_boundary(&self, offset: usize) -> usize {
        self.text
            .char_indices()
            .find_map(|(idx, _)| (idx > offset).then_some(idx))
            .unwrap_or(self.text.len())
    }

    pub fn replace(&mut self, range: Range<usize>, text: &str) {
        self.text.replace_range(range.clone(), text);
        self.caret = range.start + text.len();
        self.anchor = None;
        self.marked = None;
    }

    /// Integer value of a spin field (0 when the text is not a number).
    pub fn number(&self) -> i64 {
        self.text.trim().parse::<i64>().unwrap_or(0)
    }
}

pub fn clamp_boundary(text: &str, offset: usize) -> usize {
    let mut offset = offset.min(text.len());
    while offset > 0 && !text.is_char_boundary(offset) {
        offset -= 1;
    }
    offset
}

pub fn offset_from_utf16(text: &str, offset: usize) -> usize {
    let mut utf8 = 0;
    let mut utf16 = 0;
    for ch in text.chars() {
        if utf16 >= offset {
            break;
        }
        utf16 += ch.len_utf16();
        utf8 += ch.len_utf8();
    }
    utf8
}

pub fn offset_to_utf16(text: &str, offset: usize) -> usize {
    let mut utf16 = 0;
    let mut utf8 = 0;
    for ch in text.chars() {
        if utf8 >= offset {
            break;
        }
        utf8 += ch.len_utf8();
        utf16 += ch.len_utf16();
    }
    utf16
}

pub fn range_to_utf16(text: &str, range: &Range<usize>) -> Range<usize> {
    offset_to_utf16(text, range.start)..offset_to_utf16(text, range.end)
}

pub fn range_from_utf16(text: &str, range: &Range<usize>) -> Range<usize> {
    offset_from_utf16(text, range.start)..offset_from_utf16(text, range.end)
}

pub struct TextFieldElement {
    entity: Entity<MainView>,
    field_id: FieldId,
    focus: FocusHandle,
    display: SharedString,
    caret: usize,
    selected: Range<usize>,
    marked: Option<Range<usize>>,
    focused: bool,
    show_caret: bool,
    text_color: Hsla,
    caret_color: Hsla,
    selection_color: Hsla,
    padding_x: Pixels,
}

pub struct FieldPrepaint {
    line: Option<ShapedLine>,
    caret: Option<PaintQuad>,
    selection: Option<PaintQuad>,
}

impl IntoElement for TextFieldElement {
    type Element = Self;

    fn into_element(self) -> Self::Element {
        self
    }
}

impl Element for TextFieldElement {
    type RequestLayoutState = ();
    type PrepaintState = FieldPrepaint;

    fn id(&self) -> Option<ElementId> {
        None
    }

    fn source_location(&self) -> Option<&'static core::panic::Location<'static>> {
        None
    }

    fn request_layout(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        window: &mut Window,
        cx: &mut App,
    ) -> (LayoutId, Self::RequestLayoutState) {
        let mut style = Style::default();
        style.size.width = relative(1.).into();
        style.size.height = window.line_height().into();
        (window.request_layout(style, [], cx), ())
    }

    fn prepaint(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _request_layout: &mut Self::RequestLayoutState,
        window: &mut Window,
        _cx: &mut App,
    ) -> Self::PrepaintState {
        let style = window.text_style();
        let run = TextRun {
            len: self.display.len(),
            font: style.font(),
            color: self.text_color,
            background_color: None,
            underline: None,
            strikethrough: None,
        };
        let runs = match self.marked.clone() {
            Some(marked) if marked.end <= self.display.len() && !marked.is_empty() => vec![
                TextRun { len: marked.start, ..run.clone() },
                TextRun { len: marked.len(), ..run.clone() },
                TextRun { len: self.display.len() - marked.end, ..run.clone() },
            ]
            .into_iter()
            .filter(|r| r.len > 0)
            .collect::<Vec<_>>(),
            _ => vec![run],
        };
        let font_size = style.font_size.to_pixels(window.rem_size());
        let origin = point(bounds.left() + self.padding_x, bounds.top());
        let line = window
            .text_system()
            .shape_line(self.display.clone(), font_size, &runs, None);

        let selection = if self.selected.is_empty() || self.display.is_empty() {
            None
        } else {
            let start = line.x_for_index(self.selected.start.min(self.display.len()));
            let end = line.x_for_index(self.selected.end.min(self.display.len()));
            Some(fill(
                Bounds::new(
                    point(origin.x + start, bounds.top() + px(1.)),
                    size(end - start, bounds.bottom() - bounds.top() - px(2.)),
                ),
                self.selection_color,
            ))
        };
        let caret = if self.show_caret {
            let x = line.x_for_index(self.caret.min(self.display.len()));
            Some(fill(
                Bounds::new(
                    point(origin.x + x, bounds.top() + px(1.)),
                    size(px(1.), bounds.bottom() - bounds.top() - px(2.)),
                ),
                self.caret_color,
            ))
        } else {
            None
        };

        FieldPrepaint { line: Some(line), caret, selection }
    }

    fn paint(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _request_layout: &mut Self::RequestLayoutState,
        prepaint: &mut Self::PrepaintState,
        window: &mut Window,
        cx: &mut App,
    ) {
        window.handle_input(
            &self.focus,
            ElementInputHandler::new(bounds, self.entity.clone()),
            cx,
        );
        if let Some(selection) = prepaint.selection.take() {
            window.paint_quad(selection);
        }
        let Some(line) = prepaint.line.take() else {
            return;
        };
        let origin = point(bounds.left() + self.padding_x, bounds.top());
        let _ = line.paint(origin, window.line_height(), window, cx);
        if self.focused
            && let Some(caret) = prepaint.caret.take()
        {
            window.paint_quad(caret);
        }
        let field_id = self.field_id;
        self.entity.update(cx, |view, _| {
            if let Some(field) = view.field_mut(field_id) {
                field.layout = Some(line);
                field.bounds = Some(bounds);
            }
        });
    }
}

/// One editable line. `off_label` renders `0` as that label (spin fields).
#[allow(clippy::too_many_arguments)]
pub fn text_field(
    theme: &Theme,
    entity: Entity<MainView>,
    id: impl Into<ElementId>,
    field_id: FieldId,
    state: &FieldState,
    window: &Window,
    placeholder: &str,
    off_label: Option<&str>,
    width: Option<Pixels>,
    readonly_look: bool,
) -> gpui::AnyElement {
    let focused = state.focus.is_focused(window);
    let showing_off = off_label.is_some() && state.text.trim() == "0" && !focused;
    let placeholder_shown = state.text.is_empty() && !focused;
    let display: SharedString = if showing_off {
        SharedString::from(off_label.unwrap_or_default().to_string())
    } else if placeholder_shown {
        SharedString::from(placeholder.to_string())
    } else {
        state.text.clone().into()
    };
    let muted = placeholder_shown || showing_off || readonly_look;

    let mut wrapper = div()
        .id(id)
        .flex()
        .items_center()
        .h(px(theme.control_height))
        .px(px(theme.control_padding_x))
        .rounded(px(theme.radius))
        .border_1()
        .border_color(if focused { theme.hairline } else { theme.border })
        .bg(theme.background)
        .font_family(theme.ui_font.clone())
        .text_size(px(theme.body))
        .text_color(if muted { theme.text_secondary } else { theme.text });
    if let Some(width) = width {
        wrapper = wrapper.w(width);
    } else {
        wrapper = wrapper.flex_1().min_w(px(0.));
    }
    if !readonly_look {
        let focus_handle = state.focus.clone();
        let target = entity.clone();
        let end = state.text.len();
        wrapper = wrapper.cursor_text().track_focus(&state.focus).on_mouse_down(
            MouseButton::Left,
            move |_event, window, cx| {
                window.focus(&focus_handle);
                target.update(cx, |view, cx| {
                    view.caret_to_end(field_id, end);
                    cx.notify();
                });
            },
        );
    }

    wrapper
        .child(TextFieldElement {
            entity,
            field_id,
            focus: state.focus.clone(),
            display,
            caret: state.caret,
            selected: state.selected_range(),
            marked: state.marked.clone(),
            focused,
            show_caret: focused && !showing_off && !placeholder_shown,
            text_color: if muted { theme.text_secondary } else { theme.text },
            caret_color: theme.text,
            selection_color: theme.selected,
            padding_x: px(0.),
        })
        .into_any_element()
}
