//! Pure HUD content renderer: `HudPayload` -> HTML content fragment.
//!
//! The fragment is the CONTENT for the HUD page's `#content` element —
//! no `<html>`/`<head>`/`<body>` and no CSS links. It reproduces the
//! visible structure the former `hud.js` renderer produced (tab header
//! row, response or error section) using the classes styled by
//! `client/src/hud.css`. No Tauri runtime involvement: a pure function
//! over the payload, golden-tested in `tests/render.rs`.

use crate::pipeline::HudPayload;

/// Tab header row shared by every variant: «Перевод» active, «Разбор» a
/// disabled MVP-2 marker.
const TABS: &str = concat!(
    "<header class=\"hud__tabs\">",
    "<div class=\"hud__tab hud__tab--active\">Перевод</div>",
    "<div class=\"hud__tab hud__tab--disabled\" title=\"MVP-2\">Разбор</div>",
    "</header>"
);

/// Error headline for any message carrying the pipeline's unavailability
/// prefix; the remainder of the message becomes the cause subtext.
const UNAVAILABLE_PREFIX: &str = "backend unavailable";

/// Render one payload as the HUD content fragment (no trailing newline).
pub fn render_hud_html(payload: &HudPayload) -> String {
    match payload {
        HudPayload::Response(response) => {
            let mut html = format!(
                "{TABS}<section class=\"hud__section\"><div class=\"hud__main\">{}</div>",
                escape(&response.response.main),
            );
            if !response.response.notes.is_empty() {
                html.push_str("<ul class=\"hud__notes\">");
                for note in &response.response.notes {
                    html.push_str(&format!("<li>{}</li>", escape(note)));
                }
                html.push_str("</ul>");
            }
            html.push_str("</section>");
            html
        }
        HudPayload::Error(message) => {
            let (title, detail) = if let Some(rest) = message.strip_prefix(UNAVAILABLE_PREFIX) {
                (
                    "Бэкенд недоступен",
                    rest.trim_start_matches([':', ' ', '\t', '\n', '\r']),
                )
            } else {
                (message.as_str(), "")
            };
            format!(
                "{TABS}<section class=\"hud__section hud__error\">\
<div class=\"hud__error-title\">{}</div>\
<div class=\"hud__error-detail\">{}</div></section>",
                escape(title),
                escape(detail),
            )
        }
    }
}

/// HTML-escape a payload-provided text: never trust payload strings.
fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(ch),
        }
    }
    out
}
