//! HTTP client for the Leng backend's `/v1/query` endpoint.

use serde_json::Value;
use std::time::Duration;

use crate::types::{ApiError, QueryResponse, Source};

/// Total deadline for one backend call.
const TOTAL_TIMEOUT: Duration = Duration::from_secs(12);

/// Cap for unparsed error bodies surfaced in `ApiError::Status`.
const UNPARSED_BODY_MAX_CHARS: usize = 200;

pub struct BackendClient {
    base_url: String,
    token: String,
    http: reqwest::Client,
}

impl BackendClient {
    pub fn new(base_url: &str, token: &str) -> Self {
        Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            token: token.to_string(),
            http: reqwest::Client::builder()
                .timeout(TOTAL_TIMEOUT)
                .build()
                .expect("reqwest client builder with fixed settings cannot fail"),
        }
    }

    pub async fn query(
        &self,
        text: &str,
        source: &Source,
        client_name: &str,
    ) -> Result<QueryResponse, ApiError> {
        let response = self
            .http
            .post(format!("{}/v1/query", self.base_url))
            .header("X-Api-Token", &self.token)
            .json(&serde_json::json!({
                "raw_text": text,
                "source": source,
                "client": client_name,
            }))
            .send()
            .await;
        let response = match response {
            Ok(response) => response,
            Err(err) => return Err(transport_error(&err)),
        };

        if !response.status().is_success() {
            return Err(ApiError::Status(
                response.status().as_u16(),
                error_code(response).await,
            ));
        }
        // Deliberately shares the Network variant (the enum has no slot for
        // it); split out if later consumers need to branch on malformed 2xx
        // bodies separately from transport failures.
        response
            .json::<QueryResponse>()
            .await
            .map_err(|err| ApiError::Network(format!("invalid response body: {err}")))
    }
}

/// Map a failing `reqwest` call: a total-timeout expiry is `Timeout`,
/// everything else (connect failures and the like) is `Network`.
fn transport_error(err: &reqwest::Error) -> ApiError {
    if err.is_timeout() {
        ApiError::Timeout
    } else {
        ApiError::Network(err.to_string())
    }
}

/// Extract the flat error code from a `{"error": <code>}` body; bodies that
/// do not parse that way are used verbatim, truncated to 200 characters.
async fn error_code(response: reqwest::Response) -> String {
    match response.text().await {
        Ok(body) => serde_json::from_str::<Value>(&body)
            .ok()
            .and_then(|value| {
                value
                    .get("error")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .unwrap_or_else(|| body.chars().take(UNPARSED_BODY_MAX_CHARS).collect()),
        Err(err) => format!("body read failed: {err}"),
    }
}
