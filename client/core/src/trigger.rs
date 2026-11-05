//! Double-tap keypress trigger: fires when a second press arrives within
//! a threshold after the previous one, then restarts the cycle.

use std::time::{Duration, Instant};

/// State machine for the double-copy shortcut.
#[derive(Debug, Clone)]
pub struct DoubleTap {
    threshold: Duration,
    previous_press: Option<Instant>,
}

impl DoubleTap {
    pub fn new(threshold: Duration) -> Self {
        Self {
            threshold,
            previous_press: None,
        }
    }

    /// Registers a keypress at `at`; returns `true` when it completes a
    /// double tap. After a trigger the next press starts a fresh cycle.
    pub fn press(&mut self, at: Instant) -> bool {
        let threshold = self.threshold;
        let triggered = self
            .previous_press
            .map(|previous| at.saturating_duration_since(previous) <= threshold)
            .unwrap_or(false);
        self.previous_press = if triggered { None } else { Some(at) };
        triggered
    }
}

impl Default for DoubleTap {
    fn default() -> Self {
        Self::new(Duration::from_millis(500))
    }
}
