//! The active-source layer of the current platform (cfg dispatch per plan —
//! no mock abstraction).

pub mod source;

#[cfg(target_os = "macos")]
pub mod macos;

#[cfg(not(target_os = "macos"))]
compile_error!("leng-core platform layer supports macOS only");

/// The active application context for the current platform.
#[cfg(target_os = "macos")]
pub fn platform_source() -> source::SourceInfo {
    self::macos::source_info()
}
