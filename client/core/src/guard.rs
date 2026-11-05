//! Generation guard: distinguishing results of older triggers from newer
//! ones (pure logic, unit-testable).

/// Monotonically increasing token source for concurrent trigger tasks.
///
/// Each trigger starts a task and takes a token via [`start`]; only a task
/// whose token is still the newest (`is_current`) may render its payload.
/// This closes the late-arriver-wins race: an older slow task's result is
/// dropped instead of overwriting the newer trigger's content.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct GenerationGuard {
    current: u64,
}

impl GenerationGuard {
    /// Starts a new task: its token supersedes every earlier one.
    pub fn start(&mut self) -> u64 {
        self.current += 1;
        self.current
    }

    /// Whether `token` still names the newest task; older results must be
    /// dropped silently.
    pub fn is_current(&self, token: u64) -> bool {
        self.current == token
    }
}

#[cfg(test)]
mod tests {
    use super::GenerationGuard;

    #[test]
    fn first_task_is_current() {
        let mut guard = GenerationGuard::default();
        let token = guard.start();
        assert!(guard.is_current(token));
    }

    #[test]
    fn unknown_token_is_not_current() {
        let guard = GenerationGuard::default();
        assert!(!guard.is_current(1));
    }

    #[test]
    fn start_supersedes_previous_token() {
        let mut guard = GenerationGuard::default();
        let first = guard.start();
        let second = guard.start();
        assert!(!guard.is_current(first));
        assert!(guard.is_current(second));
    }

    #[test]
    fn out_of_order_completion_drops_stale_result() {
        // Trigger 1 in flight, trigger 2 arrives fast: when the payloads
        // settle, the newest token wins regardless of completion order.
        let mut guard = GenerationGuard::default();
        let slow = guard.start();
        let fast = guard.start();
        assert!(guard.is_current(fast), "latest trigger may render");
        assert!(!guard.is_current(slow), "stale trigger must be dropped");
    }

    #[test]
    fn dismissal_restart_invalidates_the_outstanding_token() {
        // The HUD is dismissed while a trigger is in flight: the dismiss
        // path starts a fresh generation (without a new trigger), so the
        // in-flight payload's token goes stale and its later commit drops
        // instead of resurrecting the closed window.
        let mut guard = GenerationGuard::default();
        let in_flight = guard.start();
        guard.start(); // dismissal marks the window session dead
        assert!(!guard.is_current(in_flight));
    }
}
