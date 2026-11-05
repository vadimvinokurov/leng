//! Glue between text capture, the backend client, and the HUD.

use serde::{
    de::Deserialize,
    ser::{Serialize, SerializeMap, Serializer},
};

use crate::api::BackendClient;
use crate::types::{ApiError, QueryResponse, Source};

/// What the HUD gets shown for one selection.
///
/// Serializes as a tagged map with stable variant names: `response` for
/// [`HudPayload::Response`] (the `QueryResponse` fields flattened beside
/// `"type"`, e.g. `{"type":"response","query_id":...,"response":{...}}`) and
/// `error` for [`HudPayload::Error`] (`{"type":"error","message":..."}`).
/// A `#[serde(tag = "type")]` derive cannot express the `Error(String)`
/// newtype (internally tagged serde can only tag map-shaped payloads), so the
/// impl below is the single place pinning those names.
#[derive(Debug, Clone, PartialEq)]
pub enum HudPayload {
    Response(QueryResponse),
    Error(String),
}

impl Serialize for HudPayload {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            HudPayload::Response(response) => {
                let mut map = serializer.serialize_map(Some(6))?;
                map.serialize_entry("type", "response")?;
                map.serialize_entry("query_id", &response.query_id)?;
                map.serialize_entry("kind", &response.kind)?;
                map.serialize_entry("canonical", &response.canonical)?;
                map.serialize_entry("response", &response.response)?;
                map.serialize_entry("soft_mode", &response.soft_mode)?;
                map.end()
            }
            HudPayload::Error(message) => {
                let mut map = serializer.serialize_map(Some(2))?;
                map.serialize_entry("type", "error")?;
                map.serialize_entry("message", message)?;
                map.end()
            }
        }
    }
}

impl HudPayload {
    /// Parse one payload from its wire JSON (the exact strings [`Serialize`]
    /// produces): the inverse of the impl above, used by the HUD's render
    /// adapter and pinned by `tests/render.rs`.
    pub fn from_wire_json(json: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(json)
    }
}

/// Wire shape for [`HudPayload`]'s derived deserialization: same variant
/// names the manual `Serialize` pins. Both variants are map-shaped with a
/// `type` tag, so serde's internally-tagged repr covers them.
#[derive(serde::Deserialize)]
#[serde(tag = "type")]
enum HudPayloadShape {
    #[serde(rename = "response")]
    Response {
        query_id: i64,
        kind: String,
        canonical: Option<String>,
        response: crate::types::ResponsePayload,
        soft_mode: bool,
    },
    #[serde(rename = "error")]
    Error { message: String },
}

impl<'de> Deserialize<'de> for HudPayload {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        match HudPayloadShape::deserialize(deserializer)? {
            HudPayloadShape::Response {
                query_id,
                kind,
                canonical,
                response,
                soft_mode,
            } => Ok(HudPayload::Response(crate::types::QueryResponse {
                query_id,
                kind,
                canonical,
                response,
                soft_mode,
            })),
            HudPayloadShape::Error { message } => Ok(HudPayload::Error(message)),
        }
    }
}

/// Sink for processed selections.
pub trait Hud: Send + Sync {
    fn show(&self, payload: HudPayload);
}

/// Trim the selection, gate empties before any backend call, and hand the
/// outcome (query response or human-readable failure) to the HUD.
pub async fn process(
    text: &str,
    api: &BackendClient,
    hud: &dyn Hud,
    source: &Source,
    client_name: &str,
) {
    let text = text.trim();
    if text.is_empty() {
        hud.show(HudPayload::Error("empty selection".into()));
        return;
    }
    let payload = match api.query(text, source, client_name).await {
        Ok(response) => HudPayload::Response(response),
        Err(err) => HudPayload::Error(describe(err)),
    };
    hud.show(payload);
}

fn describe(err: ApiError) -> String {
    match err {
        ApiError::Timeout => "backend unavailable: timed out".into(),
        ApiError::Network(details) => format!("backend unavailable: {details}"),
        ApiError::Status(409, code) if code == "version_mismatch" => {
            "ваша версия не совпадает с версией сервера — обновите приложение (меню в трее)".into()
        }
        ApiError::Status(status, code) => format!("server error {status}: {code}"),
    }
}
