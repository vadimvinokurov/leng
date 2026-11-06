//! Tests of the pipeline's gating, result shaping, and error wording.

use leng_core::api::BackendClient;
use leng_core::pipeline::{process, Hud, HudPayload};
use leng_core::types::{QueryResponse, ResponsePayload, Source};
use serde_json::json;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

struct CaptureHud {
    shown: std::sync::Mutex<Vec<HudPayload>>,
    offered: std::sync::atomic::AtomicUsize,
}

impl CaptureHud {
    fn new() -> Self {
        Self {
            shown: std::sync::Mutex::new(Vec::new()),
            offered: std::sync::atomic::AtomicUsize::new(0),
        }
    }
}

impl Hud for CaptureHud {
    fn show(&self, payload: HudPayload) {
        self.shown.lock().unwrap().push(payload);
    }

    fn offer_update(&self) {
        self.offered
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    }
}

fn source() -> Source {
    Source {
        app: "tester".into(),
        title: None,
        url: None,
    }
}

#[tokio::test]
async fn empty_text_shows_error_without_calling_backend() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(500).set_body_json(json!({"error": "empty_text"})))
        .mount(&server)
        .await;
    let api = BackendClient::new(&server.uri(), "secret", env!("CARGO_PKG_VERSION"));
    let hud = CaptureHud::new();

    process("   \t ", &api, &hud, &source(), "leng-cli").await;

    let shown = hud.shown.lock().unwrap().clone();
    assert_eq!(shown, vec![HudPayload::Error("empty selection".into())]);
    assert!(
        server
            .received_requests()
            .await
            .expect("requests")
            .is_empty(),
        "empty selection must not hit the backend"
    );
}

#[tokio::test]
async fn successful_query_shows_response() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(200).set_body_json(json!({
            "query_id": 3,
            "kind": "phrase",
            "canonical": null,
            "response": {"main": "break a leg", "notes": []},
            "soft_mode": true
        })))
        .mount(&server)
        .await;
    let api = BackendClient::new(&server.uri(), "secret", env!("CARGO_PKG_VERSION"));
    let hud = CaptureHud::new();

    process("  hello  ", &api, &hud, &source(), "leng-cli").await;

    let shown = hud.shown.lock().unwrap().clone();
    assert_eq!(
        shown,
        vec![HudPayload::Response(QueryResponse {
            query_id: 3,
            kind: "phrase".into(),
            canonical: None,
            response: ResponsePayload {
                main: "break a leg".into(),
                notes: vec![],
            },
            soft_mode: true,
        })]
    );
}

#[tokio::test]
async fn server_error_shows_message_including_status() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(502).set_body_json(json!({"error": "llm_error"})))
        .mount(&server)
        .await;
    let api = BackendClient::new(&server.uri(), "secret", env!("CARGO_PKG_VERSION"));
    let hud = CaptureHud::new();

    process("hello", &api, &hud, &source(), "leng-cli").await;

    let shown = hud.shown.lock().unwrap().clone();
    assert_eq!(shown.len(), 1);
    match &shown[0] {
        HudPayload::Error(message) => {
            assert!(
                message.contains("502"),
                "message must carry the status: {message}"
            );
            assert!(
                message.contains("llm_error"),
                "message must carry the code: {message}"
            );
        }
        other => panic!("expected HudPayload::Error, got {other:?}"),
    }
}

/// A release server rejects wrong-version clients with one flat code; the
/// HUD must say "update" in plain words, not a raw status line.
#[tokio::test]
async fn version_mismatch_shows_update_hint() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(
            ResponseTemplate::new(409).set_body_json(json!({"error": "version_mismatch"})),
        )
        .mount(&server)
        .await;
    let api = BackendClient::new(&server.uri(), "secret", "0.1.0");
    let hud = CaptureHud::new();

    process("hello", &api, &hud, &source(), "leng-cli").await;

    let shown = hud.shown.lock().unwrap().clone();
    match &shown[..] {
        [HudPayload::Error(message)] => {
            assert!(message.contains("версия"), "got: {message}");
            assert!(message.contains("обнов"), "got: {message}");
            assert!(!message.contains("409"), "got: {message}");
        }
        other => panic!("expected a single error payload, got {other:?}"),
    }
    assert_eq!(hud.offered.load(std::sync::atomic::Ordering::SeqCst), 1);
}

/// Any other error must not pretend an update exists.
#[tokio::test]
async fn other_error_does_not_offer_update() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(502).set_body_json(json!({"error": "llm_error"})))
        .mount(&server)
        .await;
    let api = BackendClient::new(&server.uri(), "secret", env!("CARGO_PKG_VERSION"));
    let hud = CaptureHud::new();

    process("hello", &api, &hud, &source(), "leng-cli").await;

    assert_eq!(hud.offered.load(std::sync::atomic::Ordering::SeqCst), 0);
}
