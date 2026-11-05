//! Wire types mirroring the backend `/v1/query` contract (snake_case serde).

use serde::{Deserialize, Serialize};

/// Active application context reported by the client.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Source {
    pub app: String,
    pub title: Option<String>,
    pub url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct QueryResponse {
    pub query_id: i64,
    pub kind: String,
    pub canonical: Option<String>,
    pub response: ResponsePayload,
    pub soft_mode: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResponsePayload {
    pub main: String,
    pub notes: Vec<String>,
}

/// Failure of a backend call.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApiError {
    Timeout,
    Status(u16, String),
    Network(String),
}

/// GET /v1/health answer: the server version powers the update decision.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Health {
    pub status: String,
    pub version: String,
}
