//! The active source of captured text: frontmost app plus, for browsers,
//! the active tab's title and URL.
//!
//! The tab title and URL come from AppleScript (see the script constants
//! below); `parse_tab` is the pure inverse of those scripts' output format.
//! The helper child-process runner bounds the wait so a stuck `osascript`
//! (its AppleEvent timeout is ~2 minutes) cannot freeze the caller.

use std::io::Read;
use std::process::{Command, Output, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

/// Active application context as seen by the platform.
///
/// Mirror of [`crate::types::Source`] minus the wire attributes, so the
/// pipeline can build a wire `Source` from it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceInfo {
    pub app: String,
    pub title: Option<String>,
    pub url: Option<String>,
}

/// AppleScript for the active tab of the frontmost Google Chrome window.
///
/// Output contract: `<title>\n<url>\n` — exactly what [`parse_tab`] expects.
/// `\\n` is the AppleScript string-literal escape for the separator.
pub const CHROME_TAB_SCRIPT: &str = "tell application \"Google Chrome\" to return (title of active tab of front window) & \"\\n\" & (URL of active tab of front window)";

/// AppleScript for the active tab of the frontmost Safari window.
///
/// Output contract: `<title>\n<url>\n` — exactly what [`parse_tab`] expects.
/// Safari names the tab title property `name`, not `title`.
pub const SAFARI_TAB_SCRIPT: &str = "tell application \"Safari\" to return (name of current tab of front window) & \"\\n\" & (URL of current tab of front window)";

/// Which capture path an app name routes to.
///
/// Browsers get the AppleScript tab route (title and URL); every other app
/// gets the generic AX window-title route (`Other`). The match is on the
/// localized name exactly as `NSRunningApplication.localizedName` reports
/// it — no bundle-identifier normalization happens anywhere.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrowserKind {
    Chrome,
    Safari,
    Other,
}

/// The app's browser routing, or [`BrowserKind::Other`].
pub fn source_kind(app_name: &str) -> BrowserKind {
    match app_name {
        "Google Chrome" => BrowserKind::Chrome,
        "Safari" => BrowserKind::Safari,
        _ => BrowserKind::Other,
    }
}

/// The AppleScript that reports the active tab for the given app, if any.
///
/// `app_name` is the app name exactly as the platform layer gets it from
/// `NSRunningApplication.localizedName` ("Google Chrome", "Safari").
/// Non-browsers return `None`: their tab route does not exist (they fall
/// back to the AX window title instead — see `BrowserKind`).
pub fn tab_script(app_name: &str) -> Option<&'static str> {
    match source_kind(app_name) {
        BrowserKind::Chrome => Some(CHROME_TAB_SCRIPT),
        BrowserKind::Safari => Some(SAFARI_TAB_SCRIPT),
        BrowserKind::Other => None,
    }
}

/// Parse the stdout of one of the tab scripts above into `(title, url)`.
///
/// The scripts emit `<title>\n<url>`; `osascript` appends a trailing
/// newline. Anything that does not match that shape — empty output, an
/// error line on stdout, a missing or malformed URL — parses to `None`.
pub fn parse_tab(stdout: &str) -> Option<(String, String)> {
    let (title, url) = stdout.split_once('\n')?;
    let title = title.trim();
    let url = url.trim();
    if title.is_empty() || !url.contains("://") {
        return None;
    }
    Some((title.to_string(), url.to_string()))
}

/// Run a child process with a hard cap on the wait, like `Command::output()`
/// with a budget.
///
/// Captures `stdin = null`, `stdout = pipe`, `stderr = pipe` (the
/// `Command::output()` configuration). If the child has not finished within
/// `budget`, it is killed and `None` is returned; on a spawn failure or a
/// panicked worker, `None` too. The pipe-reading worker is left to finish on
/// its own when the budget expires (its result is discarded).
///
/// Same-shape `Output` on success, non-zero statuses included. Not
/// cfg-gated: bounded process execution is not platform-specific.
pub fn run_bounded_process(mut cmd: Command, budget: Duration) -> Option<Output> {
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().ok()?;
    let stdout = child.stdout.take()?;
    let stderr = child.stderr.take()?;

    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut out = Vec::new();
        let mut err = Vec::new();
        let mut stdout = stdout;
        let mut stderr = stderr;
        // Reading to EOF: if the parent kills the child below, the pipes
        // close and the reads return immediately (killing is not possible
        // from inside the thread — the handle stays with the caller, which
        // is the only owner that can act on timeout).
        let _ = stdout.read_to_end(&mut out);
        let _ = stderr.read_to_end(&mut err);
        let _ = tx.send((out, err));
    });

    // The budget covers the wait from just after the spawn; a first (and
    // only) miss — timeout or dead worker — degrades to `None`.
    let (out, err) = match rx.recv_timeout(budget) {
        Ok(pair) => pair,
        Err(_) => {
            let _ = child.kill();
            let _ = child.wait();
            return None;
        }
    };
    let status = child.wait().ok()?;
    Some(Output {
        status,
        stdout: out,
        stderr: err,
    })
}
