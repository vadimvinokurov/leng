//! Tests of the double-tap trigger state machine.

use leng_core::trigger::DoubleTap;
use std::time::Duration;

#[test]
fn first_press_is_never_a_trigger() {
    let mut tap = DoubleTap::default();
    assert!(!tap.press(at(0)));
}

#[test]
fn second_press_within_threshold_triggers() {
    let mut tap = DoubleTap::default();
    tap.press(at(0));
    assert!(tap.press(at(100)));
}

#[test]
fn second_press_at_threshold_is_still_a_trigger() {
    let mut tap = DoubleTap::default();
    let last = at(0);
    tap.press(last);
    assert!(tap.press(last + Duration::from_millis(500)));
}

#[test]
fn second_press_outside_threshold_does_not_trigger() {
    let mut tap = DoubleTap::default();
    tap.press(at(0));
    assert!(!tap.press(at(600)));
}

#[test]
fn outside_press_starts_new_cycle_so_late_pair_triggers() {
    let mut tap = DoubleTap::default();
    tap.press(at(0));
    assert!(!tap.press(at(600)), "outside threshold: no trigger");
    assert!(tap.press(at(700)), "next press is a fresh pair");
}

#[test]
fn trigger_resets_so_third_rapid_press_is_a_new_first_press() {
    let mut tap = DoubleTap::default();
    tap.press(at(0));
    assert!(tap.press(at(100)));
    assert!(!tap.press(at(200)), "third press alone cannot trigger");
    assert!(tap.press(at(300)), "fourth press pairs with the third");
}

#[test]
fn constructor_uses_configured_threshold() {
    let mut tap = DoubleTap::new(Duration::from_millis(200));
    tap.press(at(0));
    assert!(!tap.press(at(250)), "beyond the configured threshold");
    assert!(tap.press(at(300)), "within the configured threshold");
}

fn at(offset_ms: u64) -> std::time::Instant {
    std::time::Instant::now() + Duration::from_millis(offset_ms)
}
