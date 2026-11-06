//! Client configuration resolution in one place, shared by the GUI and
//! the CLI: CLI flags > `config.json` next to the executable >
//! `LENG_*` environment variables.

use std::path::{Path, PathBuf};

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

/// The settings written by the UI: the same shape `FileConfig` reads.
#[derive(serde::Serialize)]
struct PersistableConfig {
    backend_url: String,
    api_token: String,
}

/// Write `config.json` into `dir` (next to the executable in
/// production). Blank url or token is rejected — the file must never
/// contain values the resolution chain would fail on. The write lands
/// through a temp file + rename, so a crash cannot leave a torn file.
pub fn write_config(dir: &Path, base_url: &str, token: &str) -> Result<(), ConfigError> {
    let base_url = base_url.trim();
    let token = token.trim();
    if base_url.is_empty() {
        return Err(ConfigError("backend_url is required".into()));
    }
    if token.is_empty() {
        return Err(ConfigError("api_token is required".into()));
    }
    let json = serde_json::to_string(&PersistableConfig {
        backend_url: base_url.to_string(),
        api_token: token.to_string(),
    })
    .expect("two-string serialization cannot fail");
    std::fs::create_dir_all(dir)
        .map_err(|err| ConfigError(format!("cannot create {}: {err}", dir.display())))?;
    let path = dir.join("config.json");
    let temp = dir.join(".config.json.tmp");
    let res = (|| -> Result<(), ConfigError> {
        std::fs::write(&temp, json)
            .map_err(|err| ConfigError(format!("cannot write {}: {err}", temp.display())))?;
        std::fs::rename(&temp, &path)
            .map_err(|err| ConfigError(format!("cannot replace {}: {err}", path.display())))?;
        Ok(())
    })();
    let _ = std::fs::remove_file(&temp);
    res
}

/// The canonical config directory, OUTSIDE the updatable app bundle: an
/// updater replaces the whole bundle and would wipe a config inside it
/// (observed on the v0.1.8 live update, 2026-11-06). On macOS this is
/// ~/Library/Application Support/Leng; platforms without a shipped
/// updater convention keep the bundled location until their port lands
/// (PROJECT.md §3). `home` goes in so tests can pin it.
pub fn config_dir(home: &Path, exe_dir: Option<&Path>) -> PathBuf {
    if cfg!(target_os = "macos") {
        home.join("Library/Application Support/Leng")
    } else {
        exe_dir
            .map(Path::to_path_buf)
            .unwrap_or_else(|| home.to_path_buf())
    }
}

/// Move a config.json that sits next to the executable (the pre-relocation
/// place) into `target_dir`. Skipped when there is nothing to move or the
/// canonical file already exists — a migration never overwrites the live
/// config. The copy lands atomically (temp file + rename in the target
/// directory).
pub fn migrate_config(exe_dir: Option<&Path>, target_dir: &Path) -> Result<(), ConfigError> {
    let Some(exe_dir) = exe_dir else {
        return Ok(());
    };
    let bundled = exe_dir.join("config.json");
    if !bundled.exists() {
        return Ok(());
    }
    let target = target_dir.join("config.json");
    if target.exists() {
        return Ok(());
    }
    std::fs::create_dir_all(target_dir)
        .map_err(|err| ConfigError(format!("cannot create {}: {err}", target_dir.display())))?;
    let contents = std::fs::read(&bundled)
        .map_err(|err| ConfigError(format!("cannot read {}: {err}", bundled.display())))?;
    let temp = target_dir.join(".config.json.migrate.tmp");
    let res = (|| -> Result<(), ConfigError> {
        std::fs::write(&temp, contents)
            .map_err(|err| ConfigError(format!("cannot write {}: {err}", temp.display())))?;
        std::fs::rename(&temp, &target)
            .map_err(|err| ConfigError(format!("cannot place {}: {err}", target.display())))?;
        Ok(())
    })();
    let _ = std::fs::remove_file(&temp);
    res
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
            "{what} is not configured: pass a config.json in the application \
             support directory (config_dir), set the LENG_* env var, or use the CLI flags"
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
