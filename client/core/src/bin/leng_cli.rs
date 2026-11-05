//! Headless CLI surface (`leng-cli`) over the same pipeline the GUI uses.
//!
//! Exit codes: 0 once a payload was produced (the payload IS the result,
//! errors included), 2 on a usage error (usage goes to stderr).

use leng_core::api::BackendClient;
use leng_core::config::{resolve, CliOverrides, StdEnv};
use leng_core::pipeline::{process, Hud, HudPayload};
use leng_core::types::Source;

/// Prints each payload as one JSON line on stdout. Stable wire names
/// (`response` / `error`) are pinned by `HudPayload`'s `Serialize` impl.
struct CliHud;

impl Hud for CliHud {
    fn show(&self, payload: HudPayload) {
        println!(
            "{}",
            serde_json::to_string(&payload).expect("HudPayload serialization cannot fail")
        );
    }
}

const USAGE: &str = "\
usage: leng-cli trigger --text TEXT [--base-url URL] [--token TOKEN] \
[--app NAME] [--client-name NAME]";

#[tokio::main]
async fn main() {
    std::process::exit(run().await);
}

async fn run() -> i32 {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("trigger") => trigger(&args[1..]).await,
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}

async fn trigger(args: &[String]) -> i32 {
    let mut text: Option<&str> = None;
    let mut base_url: Option<&str> = None;
    let mut token: Option<&str> = None;
    let mut app: Option<&str> = None;
    let mut client_name: Option<&str> = None;

    for pair in args.chunks(2) {
        let (Some(flag), Some(value)) = (pair.first(), pair.get(1)) else {
            eprintln!("{USAGE}");
            return 2;
        };
        let slot = match flag.as_str() {
            "--text" => &mut text,
            "--base-url" => &mut base_url,
            "--token" => &mut token,
            "--app" => &mut app,
            "--client-name" => &mut client_name,
            _ => {
                eprintln!("{USAGE}");
                return 2;
            }
        };
        *slot = Some(value);
    }

    let Some(text) = text else {
        eprintln!("{USAGE}");
        return 2;
    };
    let overrides = CliOverrides {
        base_url: base_url.map(str::to_string),
        token: token.map(str::to_string),
    };
    // config.json lives next to the executable, per the deployment spec.
    let config_path = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(std::path::PathBuf::from))
        .map(|dir| dir.join("config.json"));
    let resolved = match resolve(overrides, config_path.as_deref(), &StdEnv) {
        Ok(resolved) => resolved,
        Err(err) => {
            eprintln!("config error: {err}");
            return 2;
        }
    };

    let api = BackendClient::new(
        &resolved.base_url,
        &resolved.token,
        &resolved.client_version,
    );
    let source = Source {
        app: app.unwrap_or("unknown").to_string(),
        title: None,
        url: None,
    };
    let client_name = client_name.unwrap_or("leng-cli");
    process(text, &api, &CliHud, &source, client_name).await;
    0
}
