//! Clipboard capture filter: accepts a clipboard read only when it holds
//! fresh, non-empty text.

/// Returns the current clipboard text when it is non-empty after trimming
/// and differs from the previously captured text; otherwise `None`.
pub fn capture(previous: Option<&str>, current: Option<&str>) -> Option<String> {
    let text = current?;
    if text.trim().is_empty() {
        return None;
    }
    if current == previous {
        return None;
    }
    Some(text.to_string())
}
