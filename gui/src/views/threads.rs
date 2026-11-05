//! "Pick Text Hooker" dialog: live list of the game's text threads.
//!
//! The bridge streams `threads` events while the user advances the game text;
//! `ThreadsState::apply` rebuilds the list from each event and keeps the
//! selection on the same thread when possible (QML's `selectKey` behaviour).
//! The last row is the "follow Textractor's own selection" fallback.
use std::time::Duration;

use gpui::prelude::*;
use gpui::{AnyElement, ClickEvent, Context, FontWeight, SharedString, Window, div, px};
use serde_json::{Value, json};

use crate::app::{MainView, Modal};
use crate::ui::button::{Variant, button};
use crate::ui::modal::{ModalCard, footer, overlay};

/// One candidate text hook, plus the special "follow" fallback row.
pub struct ThreadRow {
    pub name: String,
    pub num: String,
    pub addr: String,
    pub n: i64,
    pub last: String,
    pub current: bool,
    pub follow: bool,
}

pub struct ThreadsState {
    pub gid: String,
    pub status: String,
    pub rows: Vec<ThreadRow>,
    pub selected: Option<usize>,
    pub sampling: bool,
    pub waiting: bool,
}

/// JSON truthiness for the bridge's `error` member (QML's `if (data.error)`).
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::String(text) => !text.is_empty(),
        Value::Number(number) => number.as_f64() != Some(0.0),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// Thread numbers arrive as either numbers or strings.
fn num_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Number(number) => number.to_string(),
        Value::Bool(flag) => flag.to_string(),
        _ => String::new(),
    }
}

fn line_count(value: &Value) -> i64 {
    value
        .as_i64()
        .or_else(|| value.as_f64().map(|count| count as i64))
        .unwrap_or(0)
}

fn text_of(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

impl ThreadsState {
    pub fn new(gid: String, wait_for_bridge: bool) -> Self {
        ThreadsState {
            gid,
            status: if wait_for_bridge {
                "Waiting for the game and the text bridge…".to_string()
            } else {
                "Searching for text threads… (advance the game text)".to_string()
            },
            rows: Vec::new(),
            selected: None,
            sampling: !wait_for_bridge,
            waiting: wait_for_bridge,
        }
    }

    /// Apply a `threads` event's `data` object. Missing or unexpected members
    /// simply leave the dialog unchanged.
    pub fn apply(&mut self, data: Value) {
        if data.get("error").is_some_and(truthy) {
            self.status = "Waiting for the text bridge…".to_string();
            self.sampling = true;
            return;
        }
        let Some(items) = data.get("threads").and_then(Value::as_array) else {
            return;
        };

        // QML keeps the picked thread across rebuilds (by thread name; the
        // fallback row is keyed by "*").
        let keep = self
            .selected
            .and_then(|index| self.rows.get(index))
            .map(|row| {
                if row.follow {
                    "*".to_string()
                } else {
                    row.name.clone()
                }
            })
            .filter(|key| !key.is_empty());

        let mut rows: Vec<ThreadRow> = items
            .iter()
            .map(|item| ThreadRow {
                name: text_of(item, "name"),
                num: num_text(item.get("num").unwrap_or(&Value::Null)),
                addr: text_of(item, "addr"),
                n: line_count(item.get("n").unwrap_or(&Value::Null)),
                last: text_of(item, "last"),
                current: item
                    .get("current")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                follow: false,
            })
            .collect();
        let count = rows.len();
        rows.push(ThreadRow {
            name: String::new(),
            num: "*".to_string(),
            addr: String::new(),
            n: 0,
            last: "Follow Textractor's own selection (fallback; needs Textractor shown)"
                .to_string(),
            current: false,
            follow: true,
        });
        self.rows = rows;
        self.selected = keep.and_then(|key| {
            self.rows.iter().position(|row| {
                let row_key = if row.follow { "*" } else { row.name.as_str() };
                row_key == key.as_str()
            })
        });
        self.sampling = false;
        self.waiting = false;
        self.status =
            format!("{count} thread(s) — pick the text hook (list keeps updating as you advance)");
    }

    /// Handle a `threads.pick` reply. Returns true when the caller should keep
    /// retrying: only a waiting `nobridge` answer does that.
    fn apply_pick(&mut self, reply: &Result<Value, String>, wait_for_bridge: bool) -> bool {
        let value = match reply {
            Ok(value) => value,
            Err(error) => {
                self.status = error.clone();
                self.sampling = false;
                self.waiting = false;
                return false;
            }
        };
        let result = value
            .get("result")
            .and_then(Value::as_str)
            .unwrap_or_default();
        if result.is_empty() {
            self.status = "Searching for text threads… (advance the game text)".to_string();
            self.sampling = true;
            self.waiting = false;
            false
        } else if result == "nobridge" {
            if wait_for_bridge {
                self.status = "Waiting for the game and the text bridge…".to_string();
                self.sampling = false;
                self.waiting = true;
                true
            } else {
                self.status = "No live translation session (bridge :6677 silent).\n\
                     Launch Setup Text Hooker for translation first."
                    .to_string();
                self.sampling = false;
                self.waiting = false;
                false
            }
        } else {
            self.status = result.to_string();
            self.sampling = false;
            self.waiting = false;
            false
        }
    }

    pub fn render(
        &self,
        app: &MainView,
        _window: &Window,
        cx: &mut Context<MainView>,
    ) -> AnyElement {
        let theme = &app.theme;
        let gid = self.gid.clone();

        let status = div()
            .w_full()
            .flex_shrink_0()
            .text_size(px(theme.body))
            .text_color(theme.text)
            .child(self.status.clone());

        let mut list = div()
            .id("threads-list")
            .flex_1()
            .min_h(px(0.))
            .w_full()
            .flex()
            .flex_col()
            .overflow_y_scroll();

        for (index, row) in self.rows.iter().enumerate() {
            let selected = self.selected == Some(index);
            let text_color = if selected {
                theme.selected_text
            } else {
                theme.text
            };
            let detail_color = if selected {
                theme.selected_text
            } else {
                theme.text_secondary
            };
            let fill = if selected {
                theme.selected
            } else {
                theme.background
            };
            let hover = theme.hover;
            let gid = gid.clone();

            let mut entry = div()
                .id(("thread-row", index))
                .flex()
                .flex_col()
                .flex_shrink_0()
                .gap(px(2.))
                .w_full()
                .px(px(theme.row_padding_x))
                .py(px(theme.xs))
                .rounded(px(theme.radius))
                .text_size(px(theme.body))
                .text_color(text_color)
                .bg(fill)
                .cursor_pointer()
                .on_click(cx.listener(move |this, _event: &ClickEvent, _window, cx| {
                    if let Modal::Threads(state) = &mut this.modal
                        && state.gid == gid
                    {
                        state.selected = Some(index);
                    }
                    cx.notify();
                }));
            if !selected {
                entry = entry.hover(move |style| style.bg(hover));
            }

            if row.follow {
                entry = entry.child(div().w_full().truncate().child(row.last.clone()));
            } else {
                let mut title = format!("{}  (#{}", row.name, row.num);
                if !row.addr.is_empty() {
                    title.push_str(", @");
                    title.push_str(&row.addr);
                }
                title.push_str(&format!(", {} lines)", row.n));
                if row.current {
                    title.push_str("  ← current");
                }
                entry = entry.child(
                    div()
                        .w_full()
                        .truncate()
                        .font_weight(FontWeight::BOLD)
                        .child(title),
                );
                if !row.last.is_empty() {
                    entry = entry.child(
                        div()
                            .w_full()
                            .truncate()
                            .text_size(px(theme.caption))
                            .text_color(detail_color)
                            .child(row.last.clone()),
                    );
                }
            }
            list = list.child(entry);
        }

        let body = div()
            .flex()
            .flex_col()
            .gap(px(theme.sm))
            .w_full()
            .h_full()
            .child(status)
            .child(list);

        let cancel = button(
            theme,
            "threads-cancel",
            "Cancel",
            true,
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, window, cx| {
                let Modal::Threads(state) = std::mem::replace(&mut this.modal, Modal::None) else {
                    cx.notify();
                    return;
                };
                this.close_threads(state, window, cx);
            }),
        );
        let accept = button(
            theme,
            "threads-accept",
            "OK",
            self.selected.is_some(),
            Variant::Normal,
            cx.listener(|this, _event: &ClickEvent, window, cx| {
                this.accept_threads(window, cx);
            }),
        );

        overlay(
            theme,
            "threads",
            ModalCard {
                title: SharedString::from("Pick Text Hooker"),
                width: px(560.),
                height: Some(px(460.)),
            },
            body.into_any_element(),
            Some(footer(theme, vec![cancel, accept])),
        )
    }
}

impl MainView {
    pub fn open_threads(
        &mut self,
        gid: String,
        wait_for_bridge: bool,
        window: &Window,
        cx: &mut Context<Self>,
    ) {
        self.modal = Modal::Threads(ThreadsState::new(gid.clone(), wait_for_bridge));
        cx.notify();
        let gid_for_reply = gid.clone();
        self.rpc(
            window,
            cx,
            "threads.pick",
            json!({"gid": gid}),
            move |this, reply, window, cx| {
                let retry = if let Modal::Threads(state) = &mut this.modal
                    && state.gid == gid_for_reply
                {
                    state.apply_pick(&reply, wait_for_bridge)
                } else {
                    false
                };
                if retry {
                    this.start_threads_retry(gid_for_reply.clone(), window, cx);
                }
                cx.notify();
            },
        );
    }

    /// While the Setup flow waits for the game/bridge, re-issue `threads.pick`
    /// every two seconds; `ThreadsState::waiting` goes false as soon as the
    /// bridge answers something other than `nobridge`.
    fn start_threads_retry(&mut self, gid: String, window: &Window, cx: &mut Context<Self>) {
        cx.spawn_in(window, async move |this, cx| {
            loop {
                cx.background_executor().timer(Duration::from_secs(2)).await;
                let keep_going = this.update_in(cx, |this, window, cx| {
                    let waiting = matches!(
                        &this.modal,
                        Modal::Threads(state) if state.gid == gid && state.waiting
                    );
                    if !waiting {
                        return false;
                    }
                    let gid_for_reply = gid.clone();
                    this.rpc(
                        window,
                        cx,
                        "threads.pick",
                        json!({"gid": gid.clone()}),
                        move |this, reply, _window, cx| {
                            if let Modal::Threads(state) = &mut this.modal
                                && state.gid == gid_for_reply
                            {
                                state.apply_pick(&reply, true);
                            }
                            cx.notify();
                        },
                    );
                    true
                });
                if !matches!(keep_going, Ok(true)) {
                    break;
                }
            }
        })
        .detach();
    }

    pub fn close_threads(&mut self, _state: ThreadsState, window: &Window, cx: &mut Context<Self>) {
        self.rpc(window, cx, "threads.cancel", json!({}), |_, _, _, _| {});
        self.modal = Modal::None;
        cx.notify();
    }

    pub fn accept_threads(&mut self, window: &Window, cx: &mut Context<Self>) {
        let Modal::Threads(state) = std::mem::replace(&mut self.modal, Modal::None) else {
            return;
        };
        let Some(index) = state.selected else {
            self.modal = Modal::Threads(state);
            return;
        };
        let picked = state
            .rows
            .get(index)
            .map(|row| (row.follow, row.name.clone()));
        let Some((follow, name)) = picked else {
            self.modal = Modal::Threads(state);
            return;
        };
        let gid = state.gid.clone();
        let thread = if follow { String::new() } else { name };
        self.rpc(window, cx, "threads.cancel", json!({}), |_, _, _, _| {});
        self.rpc(
            window,
            cx,
            "threads.save",
            json!({"gid": gid, "thread": thread}),
            |_, _, _, _| {},
        );
        cx.notify();
    }
}
