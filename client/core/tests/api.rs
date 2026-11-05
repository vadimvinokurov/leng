//! Wiremock-based tests of `BackendClient` error mapping and the wire contract.

use leng_core::api::BackendClient;
use leng_core::types::{ApiError, QueryResponse, ResponsePayload, Source};
use serde_json::json;
use wiremock::matchers::{body_partial_json, header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn source() -> Source {
    Source {
        app: "tester".into(),
        title: None,
        url: None,
    }
}

fn happy_body() -> serde_json::Value {
    json!({
        "query_id": 7,
        "kind": "word",
        "canonical": "hello",
        "response": {"main": "привет", "notes": ["note one", "note two"]},
        "soft_mode": false
    })
}

#[tokio::test]
async fn happy_payload_deserializes_exactly() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .and(header("x-api-token", "secret"))
        .and(body_partial_json(json!({
            "raw_text": "hello",
            "source": {"app": "tester"},
            "client": "leng-cli"
        })))
        .respond_with(ResponseTemplate::new(200).set_body_json(happy_body()))
        .mount(&server)
        .await;
    // Trailing slash exercises the base_url normalization in `new`.
    let client = BackendClient::new(&format!("{}/", server.uri()), "secret");

    let response = client.query("hello", &source(), "leng-cli").await;

    assert_eq!(
        response,
        Ok(QueryResponse {
            query_id: 7,
            kind: "word".into(),
            canonical: Some("hello".into()),
            response: ResponsePayload {
                main: "привет".into(),
                notes: vec!["note one".into(), "note two".into()],
            },
            soft_mode: false,
        })
    );
}

#[tokio::test]
async fn unauthorized_maps_to_status() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(401).set_body_json(json!({"error": "unauthorized"})))
        .mount(&server)
        .await;
    let client = BackendClient::new(&server.uri(), "wrong-token");

    let response = client.query("hello", &source(), "leng-cli").await;

    assert_eq!(response, Err(ApiError::Status(401, "unauthorized".into())));
}

#[tokio::test]
async fn text_too_long_maps_to_status_with_body_code() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/v1/query"))
        .respond_with(ResponseTemplate::new(400).set_body_json(json!({"error": "text_too_long"})))
        .mount(&server)
        .await;
    let client = BackendClient::new(&server.uri(), "secret");

    let response = client.query("hello", &source(), "leng-cli").await;

    assert_eq!(response, Err(ApiError::Status(400, "text_too_long".into())));
}

/// The connect hangs until the client's total timeout (12 s) fires, so an
/// unresponsive peer must surface as `ApiError::Timeout`. The 12 s wall
/// time is deliberately not asserted here — only the error mapping is.
#[tokio::test]
async fn unresponsive_backend_maps_to_timeout() {
    // A listener that accepts the connection and never answers, so the
    // request can only end through the 12 s total timeout (deterministic,
    // unlike an unroutable address, which may fail fast with no route).
    let held = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let addr = held.local_addr().expect("local_addr");
    std::thread::spawn(move || {
        let (conn, _) = held.accept().expect("accept");
        std::thread::sleep(std::time::Duration::from_secs(20));
        drop(conn);
    });

    let client = BackendClient::new(&format!("http://{addr}"), "secret");

    let response = client.query("hello", &source(), "leng-cli").await;

    assert_eq!(response, Err(ApiError::Timeout));
}

/// A peer that closes the connection mid-exchange is a transport failure:
/// neither a timeout nor a server status, so it must map to `Network`.
/// The close happens immediately, so no wall time is involved.
#[tokio::test]
async fn connection_dropped_mid_exchange_maps_to_network() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let addr = listener.local_addr().expect("local_addr");
    std::thread::spawn(move || {
        let (conn, _) = listener.accept().expect("accept");
        drop(conn);
    });

    let client = BackendClient::new(&format!("http://{addr}"), "secret");

    let response = client.query("hello", &source(), "leng-cli").await;

    match response {
        Err(ApiError::Network(_)) => {}
        other => panic!("expected ApiError::Network, got {other:?}"),
    }
}
