//! Tests of the bounded child-process runner.

use leng_core::platform::source::run_bounded_process;
use std::process::Command;
use std::time::{Duration, Instant};

fn command(program: &str, arg: &str) -> Command {
    let mut cmd = Command::new(program);
    cmd.arg(arg);
    cmd
}

#[test]
fn fast_command_is_captured_in_full() {
    let output = run_bounded_process(command("echo", "hello"), Duration::from_secs(5))
        .expect("echo must finish within the budget");
    assert!(output.status.success());
    assert_eq!(String::from_utf8_lossy(&output.stdout), "hello\n");
}

#[test]
fn failing_command_keeps_its_nonzero_status() {
    let output = run_bounded_process(command("false", ""), Duration::from_secs(5))
        .expect("false must finish within the budget");
    assert!(!output.status.success());
}

#[test]
fn hanging_command_is_killed_after_the_budget() {
    let started = Instant::now();
    let output = run_bounded_process(command("sleep", "5"), Duration::from_millis(300));
    let elapsed = started.elapsed();
    assert!(output.is_none(), "a hanging command must not be waited on");
    assert!(
        elapsed < Duration::from_secs(2),
        "the wait must be bounded by the budget, took {elapsed:?}"
    );
}

#[test]
fn missing_program_is_none() {
    let output = run_bounded_process(
        command("definitely-not-a-program-xyz", ""),
        Duration::from_millis(500),
    );
    assert!(output.is_none());
}
