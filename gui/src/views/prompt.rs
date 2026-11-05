//! Modal prompt: backend questions (`prompt` event) and local confirmations.
//!
//! The buttons are stacked full width, as in the QML dialog: a backend prompt
//! can carry any number of them and their index is the answer we send back.
use gpui::prelude::*;
use gpui::{AnyElement, ClickEvent, Context, div, px};

use crate::app::MainView;
use crate::theme::Theme;
use crate::ui::button::{Variant, button};
use crate::ui::modal::{ModalCard, overlay};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum PromptKind {
    /// A question from the bridge; the answer goes back as `prompt.resolve`.
    Backend,
    /// The library's "Remove game?" confirmation.
    RemoveGame,
    /// A local error message with one OK button.
    Notice,
}

pub struct PromptState {
    pub kind: PromptKind,
    pub title: String,
    pub body: String,
    pub buttons: Vec<String>,
}

impl PromptState {
    pub fn render(&self, theme: &Theme, cx: &mut Context<MainView>) -> AnyElement {
        let mut column = div()
            .flex()
            .flex_col()
            .w_full()
            .gap(px(theme.md))
            .child(
                div()
                    .w_full()
                    .text_size(px(theme.body))
                    .text_color(theme.text)
                    .child(self.body.clone()),
            );
        for (index, label) in self.buttons.iter().enumerate() {
            column = column.child(button(
                theme,
                ("prompt-button", index),
                label.clone(),
                true,
                Variant::Normal,
                cx.listener(move |this: &mut MainView, _: &ClickEvent, window, cx| {
                    this.resolve_prompt(index as i32, window, cx);
                    cx.notify();
                }),
            ));
        }
        overlay(
            theme,
            "prompt",
            ModalCard {
                title: self.title.clone().into(),
                width: px(440.),
                height: None,
            },
            column.into_any_element(),
            None,
        )
    }
}
