//! Config resolution (flags > config.json > env) and the update decision.

use leng_core::config::{resolve, update_decision, CliOverrides, Env, UpdateDecision};
use std::path::PathBuf;

/// A static environment map standing in for real process env.
struct MapEnv(Vec<(String, String)>);

impl MapEnv {
    fn new(entries: &[(&str, &str)]) -> Self {
        Self(
            entries
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        )
    }
}

impl Env for MapEnv {
    fn get(&self, name: &str) -> Option<String> {
        self.0
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.clone())
    }
}

/// Write a config.json under a unique temp name; no new deps.
fn temp_config(tag: &str, contents: &str) -> PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!(
        "leng-config-test-{}-{}-{}.json",
        tag,
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock works")
            .subsec_nanos()
    ));
    std::fs::write(&path, contents).expect("write temp config");
    path
}

const FULL_FILE: &str = r#"{"backend_url": "http://file:99", "api_token": "file-token"}"#;
const NO_FLAGS: CliOverrides = CliOverrides {
    base_url: None,
    token: None,
};

#[test]
fn flags_beat_config_beats_env() {
    let path = temp_config("flags", FULL_FILE);
    let env = MapEnv::new(&[
        ("LENG_BACKEND_URL", "http://env:3"),
        ("LENG_API_TOKEN", "env-token"),
        ("LENG_CLIENT_VERSION", "1.2.3"),
    ]);

    let resolved = resolve(
        CliOverrides {
            base_url: Some("http://flags:1".into()),
            token: Some("flags-token".into()),
        },
        Some(&path),
        &env,
    )
    .expect("all sources present");

    assert_eq!(resolved.base_url, "http://flags:1");
    assert_eq!(resolved.token, "flags-token");
    assert_eq!(resolved.client_version, "1.2.3");
}

#[test]
fn config_file_used_when_no_flags() {
    let path = temp_config("file", FULL_FILE);
    let resolved = resolve(NO_FLAGS, Some(&path), &MapEnv::new(&[])).expect("file complete");

    assert_eq!(resolved.base_url, "http://file:99");
    assert_eq!(resolved.token, "file-token");
}

#[test]
fn env_used_when_no_config_and_no_flags() {
    let env = MapEnv::new(&[
        ("LENG_BACKEND_URL", "http://env:3"),
        ("LENG_API_TOKEN", "env-token"),
    ]);

    let resolved = resolve(NO_FLAGS, None, &env).expect("env complete");

    assert_eq!(resolved.base_url, "http://env:3");
    assert_eq!(resolved.token, "env-token");
}

#[test]
fn malformed_config_is_an_error_not_a_panic() {
    let path = temp_config("malformed", "{ not json");
    let env = MapEnv::new(&[("LENG_API_TOKEN", "env-token")]);

    let err = resolve(NO_FLAGS, Some(&path), &env).expect_err("malformed file is an error");

    assert!(!err.0.is_empty());
}

#[test]
fn incomplete_config_is_an_error() {
    let path = temp_config("incomplete", r#"{"backend_url": "http://file:99"}"#);

    assert!(resolve(NO_FLAGS, Some(&path), &MapEnv::new(&[])).is_err());
}

#[test]
fn missing_everything_is_an_error() {
    assert!(resolve(NO_FLAGS, None, &MapEnv::new(&[])).is_err());
}

#[test]
fn client_version_from_env_or_crate_default() {
    let path = temp_config("version", FULL_FILE);

    let from_env = resolve(
        NO_FLAGS,
        Some(&path),
        &MapEnv::new(&[("LENG_CLIENT_VERSION", "9.9.9")]),
    )
    .expect("complete");
    assert_eq!(from_env.client_version, "9.9.9");

    let default = resolve(NO_FLAGS, Some(&path), &MapEnv::new(&[])).expect("complete");
    assert_eq!(default.client_version, env!("CARGO_PKG_VERSION"));
}

#[test]
fn update_decision_matches_or_dev_is_quiet() {
    assert_eq!(update_decision("1.2.0", "1.2.0"), UpdateDecision::None);
    assert_eq!(update_decision("1.2.0", "dev"), UpdateDecision::None);
}

#[test]
fn update_decision_other_version_offers() {
    assert_eq!(
        update_decision("1.1.0", "1.2.0"),
        UpdateDecision::Offer {
            version: "1.2.0".into()
        }
    );
    // Either direction: a client newer than its server must also update.
    assert_eq!(
        update_decision("1.3.0", "1.2.0"),
        UpdateDecision::Offer {
            version: "1.2.0".into()
        }
    );
}

/// A unique temp directory to write settings into.
fn temp_dir(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "leng-settings-test-{}-{}-{}",
        tag,
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock works")
            .subsec_nanos()
    ));
    std::fs::create_dir_all(&dir).expect("create temp dir");
    dir
}

#[test]
fn write_config_creates_file_that_resolves() {
    use leng_core::config::write_config;
    let dir = temp_dir("create");
    write_config(&dir, "http://new:1", "new-token").expect("write settings");

    let resolved = resolve(NO_FLAGS, Some(&dir.join("config.json")), &MapEnv::new(&[]))
        .expect("written config resolves");
    assert_eq!(resolved.base_url, "http://new:1");
    assert_eq!(resolved.token, "new-token");
}

#[test]
fn write_config_overwrites_previous_values() {
    use leng_core::config::write_config;
    let dir = temp_dir("overwrite");
    write_config(&dir, "http://old:1", "old-token").expect("first write");
    write_config(&dir, "http://new:2", "new-token").expect("second write");

    let resolved = resolve(NO_FLAGS, Some(&dir.join("config.json")), &MapEnv::new(&[]))
        .expect("overwritten config resolves");
    assert_eq!(resolved.base_url, "http://new:2");
    assert_eq!(resolved.token, "new-token");
}

#[test]
fn write_config_rejects_blank_values() {
    use leng_core::config::write_config;
    let dir = temp_dir("blank");
    let url_err = write_config(&dir, "   ", "tok").expect_err("blank url rejected");
    let token_err = write_config(&dir, "http://x", "").expect_err("blank token rejected");
    assert!(url_err.0.contains("backend_url"));
    assert!(token_err.0.contains("api_token"));
    assert!(
        !dir.join("config.json").exists(),
        "no file on rejected write"
    );
}

#[test]
fn config_dir_on_macos_is_outside_the_bundle() {
    use leng_core::config::config_dir;
    let home = PathBuf::from("/Users/tester");
    let exe = PathBuf::from("/Applications/Leng.app/Contents/MacOS");

    let dir = config_dir(&home, Some(&exe));
    assert!(
        dir.ends_with("Library/Application Support/Leng"),
        "macOS config must live in Application Support, got {dir:?}"
    );
    assert!(
        !dir.starts_with(&exe),
        "the config must not live inside the updatable bundle"
    );
}

#[test]
fn migrate_config_moves_bundled_file_outside_the_bundle() {
    use leng_core::config::migrate_config;
    let exe = temp_dir("bundle");
    let target = temp_dir("appsupport");
    let bundled = exe.join("config.json");
    std::fs::write(&bundled, FULL_FILE).expect("seed bundled config");

    migrate_config(Some(&exe), &target).expect("migration succeeds");
    let migrated = target.join("config.json");
    assert_eq!(
        std::fs::read_to_string(&migrated).expect("migrated file"),
        FULL_FILE,
        "the bundled config lands at the canonical location"
    );
    // A second migration is a no-op and never overwrites the new location.
    std::fs::write(
        &bundled,
        r#"{"backend_url": "http://stale:9", "api_token": "stale"}"#,
    )
    .expect("re-seed bundled config");
    migrate_config(Some(&exe), &target).expect("idempotent migration");
    assert_eq!(
        std::fs::read_to_string(&migrated).expect("unchanged file"),
        FULL_FILE,
        "an existing canonical config is never overwritten by migration"
    );
}

#[test]
fn migrate_config_without_bundled_file_is_a_no_op() {
    use leng_core::config::migrate_config;
    let target = temp_dir("appsupport-empty");
    migrate_config(None, &target).expect("nothing to migrate is fine");
    migrate_config(Some(&temp_dir("no-config")), &target).expect("missing file is fine");
    assert!(!target.join("config.json").exists());
}
