//! Tests of the clipboard capture filter.

use leng_core::capture::capture;

#[test]
fn new_text_is_captured() {
    assert_eq!(
        capture(Some("hello world"), Some("goodbye")),
        Some("goodbye".to_string())
    );
}

#[test]
fn unchanged_buffer_is_not_captured() {
    assert_eq!(capture(Some("hello"), Some("hello")), None);
}

#[test]
fn empty_current_is_not_captured() {
    assert_eq!(capture(Some("hello"), Some("")), None);
}

#[test]
fn whitespace_only_current_is_not_captured() {
    assert_eq!(capture(Some("hello"), Some("   \t\n ")), None);
}

#[test]
fn missing_current_is_not_captured() {
    assert_eq!(capture(Some("hello"), None), None);
}

#[test]
fn first_capture_with_no_previous_is_captured() {
    assert_eq!(capture(None, Some("hello")), Some("hello".to_string()));
}

#[test]
fn captured_text_keeps_raw_value_including_surrounding_whitespace() {
    assert_eq!(
        capture(None, Some("  hello  ")),
        Some("  hello  ".to_string())
    );
}

#[test]
fn no_current_after_cleared_previous_is_not_captured() {
    assert_eq!(capture(None, None), None);
}
