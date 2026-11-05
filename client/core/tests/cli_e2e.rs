//! End-to-end CLI path as a library: wiremock backend + the same
//! `pipeline::process` the headless CLI drives, with a HUD that prints the
//! `HudPayload` as JSON exactly the way `leng-cli trigger` does.

use leng_core::api::BackendClient;
use leng_core::pipeline::{process, Hud, HudPayload};
use leng_core::types::Source;
use serde_json::Value;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

/// CLI-equivalent HUD: one JSON line per shown payload.
struct JsonLineHud {
    lines: std::sync::Mutex<Vec<String>>,
}

impl JsonLineHud {
    fn new() -> Self {
        Self {
            lines: std::sync::Mutex::new(Vec::new()),
        }
    }
}

impl Hud for JsonLineHud {
    fn show(&self, payload: HudPayload) {
        self.lines
            .lock()
            .unwrap()
            .push(serde_json::to_string(&payload).expect("HudPayload serialization cannot fail"));
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
async fn trigger_word_shows_response_payload_as_json() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "query_id": 7,
            "kind": "word",
            "canonical": "throughput",
            "response": {
                "main": "пропускная способность",
                "notes": ["measured in units per unit of time"]
            },
            "soft_mode": false
        })))
        .mount(&server)
        .await;
    let api = BackendClient::new(&server.uri(), "secret", env!("CARGO_PKG_VERSION"));
    let hud = JsonLineHud::new();

    process("throughput", &api, &hud, &source(), "leng-cli").await;

    let lines = hud.lines.lock().unwrap().clone();
    assert_eq!(lines.len(), 1, "exactly one payload must be shown");
    let payload: Value = serde_json::from_str(&lines[0]).expect("shown line must be JSON");
    assert_eq!(payload["type"], "response");
    assert_eq!(payload["response"]["main"], "пропускная способность");
    assert_eq!(
        payload["response"]["notes"][0],
        "measured in units per unit of time"
    );
    assert_eq!(payload["query_id"], 7);
    assert_eq!(payload["kind"], "word");
    assert_eq!(payload["canonical"], "throughput");
    assert_eq!(payload["soft_mode"], false);
}

#[tokio::test]
async fn trigger_empty_text_shows_error_json_without_api_call() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({})))
        .mount(&server)
        .await;
    let api = BackendClient::new(&server.uri(), "secret", env!("CARGO_PKG_VERSION"));
    let hud = JsonLineHud::new();

    process("   ", &api, &hud, &source(), "leng-cli").await;

    let lines = hud.lines.lock().unwrap().clone();
    assert_eq!(lines.len(), 1, "exactly one payload must be shown");
    let payload: Value = serde_json::from_str(&lines[0]).expect("shown line must be JSON");
    assert_eq!(payload["type"], "error");
    assert!(
        payload["message"].as_str().is_some_and(|m| !m.is_empty()),
        "error payload must carry a message: {payload}"
    );
    assert!(
        server
            .received_requests()
            .await
            .expect("requests")
            .is_empty(),
        "empty selection must not hit the backend"
    );
}
