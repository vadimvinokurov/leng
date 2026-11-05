//! Tests of the AppleScript tab-output parser and the source routing
//! decision (browser vs everything else).

use leng_core::platform::source::{parse_tab, source_kind, BrowserKind};

#[test]
fn chrome_stdout_is_parsed_as_title_and_url() {
    let stdout = "Stack Overflow - Where Developers Learn, Share, & Build Careers\n\
                  https://stackoverflow.com/questions/123\n";
    assert_eq!(
        parse_tab(stdout),
        Some((
            "Stack Overflow - Where Developers Learn, Share, & Build Careers".to_string(),
            "https://stackoverflow.com/questions/123".to_string()
        ))
    );
}

#[test]
fn safari_stdout_is_parsed_as_title_and_url() {
    let stdout = "Rust Programming Language\nhttps://www.rust-lang.org/";
    assert_eq!(
        parse_tab(stdout),
        Some((
            "Rust Programming Language".to_string(),
            "https://www.rust-lang.org/".to_string()
        ))
    );
}

#[test]
fn osascript_error_text_is_not_a_tab() {
    assert_eq!(
        parse_tab("execution error: Not authorized to send Apple events"),
        None
    );
}

#[test]
fn osascript_timeout_error_single_line_is_not_a_tab() {
    assert_eq!(
        parse_tab("execution error: Google Chrome got an error: AppleEvent timed out. (-1712)"),
        None
    );
}

#[test]
fn empty_stdout_is_not_a_tab() {
    assert_eq!(parse_tab(""), None);
}

#[test]
fn missing_url_line_is_not_a_tab() {
    assert_eq!(parse_tab("Only A Title\n"), None);
}

#[test]
fn empty_url_line_is_not_a_tab() {
    assert_eq!(parse_tab("Some Title\n"), None);
}

#[test]
fn url_line_without_a_scheme_is_not_a_tab() {
    assert_eq!(parse_tab("Some Title\nnot-a-url"), None);
}

#[test]
fn title_surrounding_whitespace_is_trimmed() {
    let stdout = "  Padded Title  \n  https://example.com/page  \n";
    assert_eq!(
        parse_tab(stdout),
        Some((
            "Padded Title".to_string(),
            "https://example.com/page".to_string()
        ))
    );
}

// Source-kind routing: browsers script tabs, everything else falls back to
// the AX window title (PROJECT.md §6: PDF reader, IDE — window title).

#[test]
fn chrome_routes_to_browser_tabs() {
    assert_eq!(source_kind("Google Chrome"), BrowserKind::Chrome);
}

#[test]
fn safari_routes_to_browser_tabs() {
    assert_eq!(source_kind("Safari"), BrowserKind::Safari);
}

#[test]
fn non_browsers_route_to_other() {
    // PDF readers, IDEs, terminals, anything: no per-app routing.
    for name in ["Preview", "Finder", "Terminal", "IntelliJ IDEA", "iTerm2"] {
        assert_eq!(source_kind(name), BrowserKind::Other, "{name}");
    }
}

#[test]
fn browser_kind_is_name_exact() {
    // The routing matches the localized name exactly, as the platform
    // reports it — no normalization, case folding or bundle id mapping.
    assert_eq!(source_kind("chrome"), BrowserKind::Other);
    assert_eq!(source_kind("google chrome"), BrowserKind::Other);
    assert_eq!(source_kind("safari "), BrowserKind::Other);
    assert_eq!(source_kind(""), BrowserKind::Other);
}
