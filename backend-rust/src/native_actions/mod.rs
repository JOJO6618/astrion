//! Authenticated Host browser routes and shared native file saving.

mod platform;
mod routes;
mod save;
mod scope;

pub use routes::NativeActions;
pub use save::{SaveError, save_file, save_file_from};

#[cfg(test)]
#[path = "../../../test/2026-11-26_Rust宿主交互/native_actions.rs"]
mod tests;
