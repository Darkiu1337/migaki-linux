//! omarchy design tokens, parsed at runtime.
//!
//! Colors come from ~/.local/state/omarchy/current/theme/colors.toml, sizes
//! and the type scale from shell.toml next to it (plus the user's
//! ~/.config/omarchy/shell.toml for the font size). A missing or unreadable
//! file keeps the embedded tokyo-night palette, and a background poller
//! re-parses when the theme files change so `omarchy theme set` repaints the
//! window without a relaunch.
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::SystemTime;

use gpui::{Hsla, rgba};

/// Fallback palette used when omarchy's files are absent or unreadable.
const FALLBACK_COLORS: &[(&str, u32)] = &[
    ("background", 0x1a1b26),
    ("dark_background", 0x13141c),
    ("darker_background", 0x0e0e14),
    ("lighter_background", 0x24283b),
    ("foreground", 0xa9b1d6),
    ("dark_foreground", 0x565f89),
    ("light_foreground", 0xb4bee6),
    ("bright_foreground", 0xc0caf5),
    ("accent", 0x7aa2f7),
    ("selection", 0x292e42),
    ("muted", 0x414868),
    ("red", 0xf7768e),
    ("yellow", 0xe0af68),
    ("green", 0x9ece6a),
    ("blue", 0x7aa2f7),
];

/// Spacing tokens, in the shell's own names, with the shell's defaults.
const SPACING_TOKENS: &[(&str, f32)] = &[
    ("xxs", 2.0),
    ("xs", 3.0),
    ("sm", 4.0),
    ("md", 6.0),
    ("lg", 8.0),
    ("xl", 10.0),
    ("xxl", 12.0),
    ("xxxl", 14.0),
    ("huge", 18.0),
    ("control-gap", 8.0),
    ("control-padding-x", 10.0),
    ("control-padding-y", 6.0),
    ("input-padding-y", 7.0),
    ("control-height", 28.0),
    ("popup-row-height", 28.0),
    ("row-gap", 8.0),
    ("row-padding-x", 12.0),
    ("panel-gap", 14.0),
    ("panel-padding", 18.0),
    ("popup-padding", 14.0),
    ("dropdown-width", 240.0),
];

/// Type-scale tokens, relative to `base-size` (shell.toml ratios).
const FONT_TOKENS: &[(&str, f32)] = &[
    ("caption", 10.0),
    ("body-small", 11.0),
    ("body", 12.0),
    ("subtitle", 13.0),
    ("title", 14.0),
    ("heading", 16.0),
];

#[derive(Clone, Debug)]
pub struct Theme {
    pub background: Hsla,
    pub raised: Hsla,
    pub text: Hsla,
    pub text_secondary: Hsla,
    pub text_disabled: Hsla,
    pub accent: Hsla,
    pub hairline: Hsla,
    pub border: Hsla,
    pub hover: Hsla,
    pub focus: Hsla,
    pub selected: Hsla,
    pub selected_text: Hsla,
    pub scrim: Hsla,
    pub error: Hsla,
    pub warning: Hsla,
    pub success: Hsla,
    pub radius: f32,

    pub ui_font: String,
    pub mono_font: String,

    pub caption: f32,
    pub body_small: f32,
    pub body: f32,
    pub subtitle: f32,
    pub heading: f32,

    pub xxs: f32,
    pub xs: f32,
    pub sm: f32,
    pub md: f32,
    pub lg: f32,
    pub xl: f32,
    pub xxl: f32,
    pub control_gap: f32,
    pub control_padding_x: f32,
    pub control_padding_y: f32,
    pub control_height: f32,
    pub popup_row_height: f32,
    pub row_gap: f32,
    pub row_padding_x: f32,
    pub panel_gap: f32,
    pub panel_padding: f32,
    pub popup_padding: f32,
    pub dropdown_width: f32,
}

/// The theme directory omarchy points at (a symlink into themes/<name>/).
pub fn theme_dir() -> PathBuf {
    home().join(".local/state/omarchy/current/theme")
}

fn home() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/".into()))
}

fn read_toml(path: &Path) -> Option<toml::Value> {
    let text = fs::read_to_string(path).ok()?;
    text.parse::<toml::Value>().ok()
}

fn table<'a>(value: &'a Option<toml::Value>, key: &str) -> Option<&'a toml::Value> {
    value.as_ref()?.get(key)
}

fn number(value: Option<&toml::Value>) -> Option<f32> {
    match value? {
        toml::Value::Integer(i) => Some(*i as f32),
        toml::Value::Float(f) => Some(*f as f32),
        _ => None,
    }
}

fn hex_to_rgba(text: &str) -> Option<Hsla> {
    let raw = text.trim().trim_start_matches('#');
    let value = u32::from_str_radix(raw, 16).ok()?;
    match raw.len() {
        6 => Some(rgba((value << 8) | 0xff).into()),
        8 => Some(rgba(value).into()),
        3 => {
            let r = (value >> 8) & 0xf;
            let g = (value >> 4) & 0xf;
            let b = value & 0xf;
            let expanded = (r * 0x11) << 16 | (g * 0x11) << 8 | (b * 0x11);
            Some(rgba((expanded << 8) | 0xff).into())
        }
        _ => None,
    }
}

fn color(colors: &Option<toml::Value>, key: &str) -> Hsla {
    if let Some(toml::Value::String(text)) = table(colors, key)
        && let Some(c) = hex_to_rgba(text)
    {
        return c;
    }
    let fallback = FALLBACK_COLORS
        .iter()
        .find(|(name, _)| *name == key)
        .map(|(_, rgb)| *rgb)
        .unwrap_or(0x1a1b26);
    rgba((fallback << 8) | 0xff).into()
}

fn with_alpha(c: Hsla, alpha: f32) -> Hsla {
    Hsla { a: alpha, ..c }
}

/// Hyprland's decoration rounding, the same source the omarchy shell uses for
/// its corner radius. 0 when hyprctl is unavailable (or rounding is off).
fn corner_radius() -> f32 {
    let out = Command::new("hyprctl")
        .args(["getoption", "decoration:rounding"])
        .output();
    let Ok(out) = out else { return 0.0 };
    let text = String::from_utf8_lossy(&out.stdout);
    let digits: String = text
        .split_whitespace()
        .last()
        .unwrap_or("0")
        .chars()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    digits.parse::<f32>().unwrap_or(0.0).clamp(0.0, 16.0)
}

fn fc_family(pattern: &str) -> Option<String> {
    let out = Command::new("fc-match")
        .args(["-f", "%{family}", pattern])
        .output()
        .ok()?;
    let family = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (!family.is_empty()).then_some(family)
}

impl Theme {
    /// Parse the live omarchy theme. `cfg_font`/`cfg_size` are the app's own
    /// override from config.json (empty/0 = follow the desktop).
    pub fn load(cfg_font: &str, cfg_size: i64) -> Theme {
        let dir = theme_dir();
        let colors = read_toml(&dir.join("colors.toml"));
        let shell = read_toml(&dir.join("shell.toml"));
        let user_shell = read_toml(&home().join(".config/omarchy/shell.toml"));

        let background = color(&colors, "background");
        let foreground = color(&colors, "foreground");
        let accent = color(&colors, "accent");

        let base_size = if cfg_size > 0 {
            cfg_size as f32
        } else {
            number(
                table(&shell, "font")
                    .and_then(|f| f.get("base-size"))
                    .or_else(|| table(&user_shell, "font").and_then(|f| f.get("base-size"))),
            )
            .unwrap_or(12.0)
        }
        .max(1.0);

        let spacing_table = table(&shell, "spacing");
        let scale = number(spacing_table.and_then(|s| s.get("scale"))).unwrap_or(1.0);
        let scale_with_font = spacing_table
            .and_then(|s| s.get("scale-with-font"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let font_factor = if scale_with_font {
            base_size / 12.0
        } else {
            1.0
        };
        let mut resolved: Vec<(String, f32)> = Vec::new();
        for (name, default) in SPACING_TOKENS {
            let raw = number(spacing_table.and_then(|s| s.get(*name))).unwrap_or(*default);
            let value = (raw * scale * font_factor).round();
            resolved.push(((*name).to_string(), value));
        }
        let space = |name: &str| -> f32 {
            resolved
                .iter()
                .find(|(key, _)| key == name)
                .map(|(_, value)| *value)
                .unwrap_or(0.0)
        };

        let font_table = table(&shell, "font");
        let font_user_table = table(&user_shell, "font");
        let font_size = |name: &str| -> f32 {
            number(font_user_table.and_then(|t| t.get(name)))
                .or_else(|| number(font_table.and_then(|t| t.get(name))))
                .unwrap_or_else(|| {
                    let ratio = FONT_TOKENS
                        .iter()
                        .find(|(token, _)| *token == name)
                        .map(|(_, value)| *value)
                        .unwrap_or(12.0);
                    (base_size / 12.0 * ratio).round()
                })
        };

        let mono_font = fc_family("monospace").unwrap_or_else(|| "monospace".to_string());
        let ui_font = if cfg_font.trim().is_empty() {
            mono_font.clone()
        } else {
            cfg_font.trim().to_string()
        };

        let muted = color(&colors, "muted");
        Theme {
            background,
            raised: color(&colors, "lighter_background"),
            text: foreground,
            text_secondary: with_alpha(foreground, 0.7),
            text_disabled: muted,
            accent,
            hairline: with_alpha(foreground, 0.25),
            border: with_alpha(foreground, 0.4),
            hover: with_alpha(foreground, 0.08),
            focus: with_alpha(foreground, 0.08),
            selected: with_alpha(foreground, 0.18),
            selected_text: accent,
            scrim: with_alpha(background, 0.82),
            error: color(&colors, "red"),
            warning: color(&colors, "yellow"),
            success: color(&colors, "green"),
            radius: corner_radius(),
            ui_font,
            mono_font,
            caption: font_size("caption"),
            body_small: font_size("body-small"),
            body: font_size("body"),
            subtitle: font_size("subtitle"),
            heading: font_size("heading"),
            xxs: space("xxs"),
            xs: space("xs"),
            sm: space("sm"),
            md: space("md"),
            lg: space("lg"),
            xl: space("xl"),
            xxl: space("xxl"),
            control_gap: space("control-gap"),
            control_padding_x: space("control-padding-x"),
            control_padding_y: space("control-padding-y"),
            control_height: space("control-height"),
            popup_row_height: space("popup-row-height"),
            row_gap: space("row-gap"),
            row_padding_x: space("row-padding-x"),
            panel_gap: space("panel-gap"),
            panel_padding: space("panel-padding"),
            popup_padding: space("popup-padding"),
            dropdown_width: space("dropdown-width"),
        }
    }
}

/// Poll the three theme files for changes (cheap mtime check, 2 s) and hand a
/// freshly parsed theme to the window. A parse failure keeps the last theme.
pub fn spawn_poller(
    sender: async_channel::Sender<Theme>,
    cfg: std::sync::Arc<std::sync::Mutex<(String, i64)>>,
) {
    std::thread::Builder::new()
        .name("theme-poller".into())
        .spawn(move || {
            let mut last = theme_stamp();
            loop {
                std::thread::sleep(std::time::Duration::from_secs(2));
                let now = theme_stamp();
                if now == last {
                    continue;
                }
                last = now;
                let (font, size) = cfg
                    .lock()
                    .map(|cfg| cfg.clone())
                    .unwrap_or_default();
                if sender.send_blocking(Theme::load(&font, size)).is_err() {
                    return;
                }
            }
        })
        .ok();
}

fn theme_stamp() -> Vec<Option<SystemTime>> {
    let dir = theme_dir();
    [
        dir.join("colors.toml"),
        dir.join("shell.toml"),
        home().join(".config/omarchy/shell.toml"),
    ]
    .iter()
    .map(|p| fs::metadata(p).and_then(|m| m.modified()).ok())
    .collect()
}
