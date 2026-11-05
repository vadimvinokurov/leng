//! Golden tests of the pure HUD fragment renderer, plus the cross-surface
//! link: the same bytes the backend pinned in `backend/tests/golden/` are
//! what this renderer produces for the HUD.

use leng_core::pipeline::HudPayload;
use leng_core::render::render_hud_html;
use leng_core::types::{QueryResponse, ResponsePayload};

/// The word case pinned by the backend golden run (`query_word.json`), as a
/// Rust fixture: same query_id, kind, canonical, main and notes.
fn word_response() -> QueryResponse {
    QueryResponse {
        query_id: 1,
        kind: "word".into(),
        canonical: Some("throughput".into()),
        response: ResponsePayload {
            main: "пропускная способность".into(),
            notes: vec![
                "В контексте распределённых систем (Kafka) — объём сообщений, обрабатываемых в единицу времени; часто не переводится.".into(),
            ],
        },
        soft_mode: false,
    }
}

/// Read one golden fragment. Golden files are exact rendered bytes plus a
/// single trailing newline.
fn golden(name: &str) -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/golden/");
    std::fs::read_to_string(format!("{path}{name}"))
        .unwrap_or_else(|err| panic!("golden {name} must exist: {err}"))
}

/// Byte-compare with the only allowed drift protection: the golden's trailing
/// newline is normalized so editors can keep the file's final newline.
fn assert_golden(actual: &str, name: &str) {
    let golden = golden(name);
    let expected = golden.trim_end_matches('\n');
    assert_eq!(
        actual.trim_end_matches('\n'),
        expected,
        "rendered fragment must match golden {name} byte-for-byte"
    );
}

#[test]
fn response_fixture_renders_golden_bytes() {
    let html = render_hud_html(&HudPayload::Response(word_response()));
    assert_golden(&html, "response.html");
}

#[test]
fn error_fixture_renders_golden_bytes() {
    let html = render_hud_html(&HudPayload::Error("backend unavailable: timed out".into()));
    assert_golden(&html, "error.html");
}

/// Cross-surface test: the backend's pinned golden response, parsed by the
/// same serde types the wire client uses, renders to the same client golden —
/// one file connects the backend corpus and the HUD renderer.
#[test]
fn backend_golden_word_renders_client_golden_bytes() {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../backend/tests/golden/query_word.json"
    );
    let raw = std::fs::read_to_string(path)
        .unwrap_or_else(|err| panic!("backend golden query_word.json must exist: {err}"));
    let response: QueryResponse =
        serde_json::from_str(&raw).expect("backend golden must parse as QueryResponse");
    let html = render_hud_html(&HudPayload::Response(response));
    assert_golden(&html, "response.html");
}

#[test]
fn script_tag_in_main_is_escaped() {
    let payload = HudPayload::Response(QueryResponse {
        query_id: 1,
        kind: "word".into(),
        canonical: Some("test".into()),
        response: ResponsePayload {
            main: "<script>alert(1)</script> & \"quotes\"".into(),
            notes: vec!["<b>note</b>".into()],
        },
        soft_mode: false,
    });
    let html = render_hud_html(&payload);
    assert!(
        !html.contains("<script>"),
        "raw script must not survive: {html}"
    );
    assert!(
        !html.contains("<b>"),
        "raw tags in notes must not survive: {html}"
    );
    assert!(
        html.contains("&lt;script&gt;alert(1)&lt;/script&gt;"),
        "escaped main: {html}"
    );
    assert!(
        html.contains("&amp; &quot;quotes&quot;"),
        "escaped specials: {html}"
    );
    assert!(
        html.contains("&lt;b&gt;note&lt;/b&gt;"),
        "escaped note: {html}"
    );
}

#[test]
fn general_error_message_renders_as_headline_without_cause() {
    let html = render_hud_html(&HudPayload::Error("clipboard is empty".into()));
    assert!(
        html.contains("clipboard is empty"),
        "message is the headline: {html}"
    );
    assert!(
        !html.contains("Бэкенд недоступен"),
        "unavailable label only for its prefix: {html}"
    );
}

#[test]
fn payload_wire_json_parses_to_hud_payload() {
    let response = HudPayload::Response(word_response());
    let json = serde_json::to_string(&response).expect("HudPayload serialization cannot fail");
    let parsed: HudPayload = serde_json::from_str(&json).expect("wire JSON must parse");
    assert_eq!(parsed, response);

    let parsed: HudPayload =
        serde_json::from_str(r#"{"type":"error","message":"clipboard is empty"}"#)
            .expect("error wire JSON must parse");
    assert_eq!(parsed, HudPayload::Error("clipboard is empty".into()));
}

#[test]
fn invalid_payload_json_is_rejected() {
    let result: Result<HudPayload, _> = serde_json::from_str("<html>");
    assert!(result.is_err(), "non-JSON payload must be rejected");
    let result: Result<HudPayload, _> = serde_json::from_str(r#"{"type":"mystery"}"#);
    assert!(result.is_err(), "unknown variant must be rejected");
}
