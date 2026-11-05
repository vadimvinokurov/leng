//! Client configuration resolution in one place, shared by the GUI and
//! the CLI: CLI flags > `config.json` next to the executable >
//! `LENG_*` environment variables.

use std::path::Path;

pub struct CliOverrides {
    pub base_url: Option<String>,
    pub token: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedConfig {
    pub base_url: String,
    pub token: String,
    pub client_version: String,
}

/// A human-readable resolution failure; callers print `{}`.
#[derive(Debug)]
pub struct ConfigError(pub String);

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

pub trait Env {
    fn get(&self, name: &str) -> Option<String>;
}

/// The real process environment; empty values count as unset.
pub struct StdEnv;

impl Env for StdEnv {
    fn get(&self, name: &str) -> Option<String> {
        std::env::var(name)
            .ok()
            .filter(|value| !value.trim().is_empty())
    }
}

/// A missing config.json is normal (env mode); a malformed one is an
/// error, never a panic.
#[derive(serde::Deserialize)]
struct FileConfig {
    backend_url: Option<String>,
    api_token: Option<String>,
}

fn read_config(path: &Path) -> Result<FileConfig, ConfigError> {
    if !path.exists() {
        return Ok(FileConfig {
            backend_url: None,
            api_token: None,
        });
    }
    let contents = std::fs::read_to_string(path)
        .map_err(|err| ConfigError(format!("cannot read {}: {err}", path.display())))?;
    serde_json::from_str(&contents)
        .map_err(|err| ConfigError(format!("invalid JSON in {}: {err}", path.display())))
}

pub fn resolve(
    overrides: CliOverrides,
    config_path: Option<&Path>,
    env: &dyn Env,
) -> Result<ResolvedConfig, ConfigError> {
    let file = match config_path {
        Some(path) => read_config(path)?,
        None => FileConfig {
            backend_url: None,
            api_token: None,
        },
    };
    let missing = |what: &str| -> ConfigError {
        ConfigError(format!(
            "{what} is not configured: pass a config.json with it next to the \
             executable, set the LENG_* env var, or use the CLI flags"
        ))
    };
    let base_url = overrides
        .base_url
        .or(file.backend_url)
        .filter(|value| !value.trim().is_empty())
        .or_else(|| env.get("LENG_BACKEND_URL"))
        .ok_or_else(|| missing("backend_url"))?;
    let token = overrides
        .token
        .or(file.api_token)
        .filter(|value| !value.trim().is_empty())
        .or_else(|| env.get("LENG_API_TOKEN"))
        .ok_or_else(|| missing("api_token"))?;
    let client_version = env
        .get("LENG_CLIENT_VERSION")
        .unwrap_or_else(|| env!("CARGO_PKG_VERSION").to_string());
    Ok(ResolvedConfig {
        base_url,
        token,
        client_version,
    })
}

/// What the client should do about its version after reading the
/// server's.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UpdateDecision {
    None,
    Offer { version: String },
}

pub fn update_decision(client_version: &str, server_version: &str) -> UpdateDecision {
    // A dev server is the same person developing, never a release target.
    if server_version == "dev" || server_version == client_version {
        UpdateDecision::None
    } else {
        UpdateDecision::Offer {
            version: server_version.to_string(),
        }
    }
}
