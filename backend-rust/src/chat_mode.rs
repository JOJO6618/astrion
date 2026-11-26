//! Workspace-free, account-scoped Chat service for the Rust Host runtime.
//!
//! The module owns the `/api/chat` surface, message-tree admission and the Chat
//! generation task. Durable documents use
//! `<DATA_DIR>/chat/conversations/conversations.sqlite3`, and every write happens
//! inside [`Runtime::with_chat_storage`] so Chat shares the Host Store mutex and
//! never opens a second writer lock.

mod definitions;
mod drafts;
mod duplicate;
mod handoff;
mod media;
mod media_gc;
mod media_http;
mod memory;
mod model;
mod routes;
mod settings;
mod store;
mod tasks;
mod tools;
mod tree;

#[cfg(test)]
#[path = "../../test/2026-11-24_Rust后端/chat_mode_contract.rs"]
mod tests;

use std::{
    fmt,
    path::{Path, PathBuf},
};

use axum::Router;
use serde_json::{Value, json};

use crate::{
    catalog::CatalogSource,
    legacy::LegacyError,
    runtime::{Runtime, RuntimeError},
    web_assets::personalization::Personalization,
};

use model::ModelResolver;

pub use definitions::{CHAT_TOOL_NAMES, definitions as tool_definitions};
pub use model::catalog_resolver;

pub(crate) fn migration_summary(document: &Value) -> Value {
    store::index_row(document)
}

/// Authentication is mounted by the Host router; Chat itself only resolves the
/// trusted, account-owned Chat root from `DATA_DIR`.
#[derive(Clone)]
pub struct ChatMode {
    pub(crate) runtime: Runtime,
    pub(crate) root: PathBuf,
    pub(crate) preferences_dir: PathBuf,
    pub(crate) resources: PathBuf,
    pub(crate) model: ModelResolver,
    pub(crate) catalog: Option<CatalogSource>,
    pub(crate) personalization: Option<Personalization>,
    tasks: tasks::ChatTasks,
    handoffs: handoff::Handoffs,
}

impl fmt::Debug for ChatMode {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ChatMode")
            .field("root", &self.root)
            .field("preferences_dir", &self.preferences_dir)
            .finish_non_exhaustive()
    }
}

/// Normal Host entry point. `data_dir` is the trusted `DATA_DIR`; Chat data is
/// always rooted at `data_dir/chat` and never comes from request input.
impl ChatMode {
    pub fn open(
        runtime: Runtime,
        data_dir: impl AsRef<Path>,
        resources: impl AsRef<Path>,
        catalog: CatalogSource,
    ) -> Result<Self, ChatError> {
        let resolver = catalog_resolver(catalog.clone());
        Self::open_with_model(runtime, data_dir, resources, resolver)
            .map(|mode| mode.with_catalog(catalog))
    }

    /// Same entry point with an injected model resolver (used by contract tests
    /// and by the Host when model selection is assembled elsewhere).
    pub fn open_with_model(
        runtime: Runtime,
        data_dir: impl AsRef<Path>,
        resources: impl AsRef<Path>,
        model: ModelResolver,
    ) -> Result<Self, ChatError> {
        let data_dir = data_dir.as_ref();
        std::fs::create_dir_all(data_dir.join("chat").join("conversations"))
            .map_err(|error| ChatError::storage(format!("Chat storage is unavailable: {error}")))?;
        let data_dir = data_dir.canonicalize()?;
        Ok(Self {
            runtime,
            root: data_dir.join("chat"),
            preferences_dir: data_dir,
            resources: resources.as_ref().to_path_buf(),
            model,
            catalog: None,
            personalization: None,
            tasks: tasks::ChatTasks::default(),
            handoffs: handoff::Handoffs::default(),
        })
    }

    /// Share the Agent personalization state so Chat settings use the same lock,
    /// raw document, and secret-free transaction as `/api/personalization`.
    pub fn with_personalization(mut self, personalization: Personalization) -> Self {
        self.personalization = Some(personalization);
        self
    }

    /// Attach the read-only catalog used by the account Chat settings response.
    /// The model resolver may be injected independently for contract tests.
    pub(crate) fn with_catalog(mut self, catalog: CatalogSource) -> Self {
        self.catalog = Some(catalog);
        self
    }

    /// Mount `/api/chat` after the Host authentication layer.
    pub fn router(self) -> Router {
        routes::router(self)
    }
}

/// Free-function form for the main router merge.
pub fn router(mode: ChatMode) -> Router {
    mode.router()
}

#[derive(Debug)]
pub struct ChatError {
    pub status: u16,
    pub code: Option<&'static str>,
    pub message: String,
}

impl ChatError {
    pub(crate) fn new(status: u16, message: impl Into<String>) -> Self {
        Self {
            status,
            code: None,
            message: message.into(),
        }
    }

    pub(crate) fn bad_request(message: impl Into<String>) -> Self {
        Self::new(400, message)
    }

    pub(crate) fn not_found(message: impl Into<String>) -> Self {
        Self::new(404, message)
    }

    pub(crate) fn conflict(message: impl Into<String>) -> Self {
        Self::new(409, message)
    }

    pub(crate) fn unavailable(message: impl Into<String>) -> Self {
        Self::new(503, message)
    }

    pub(crate) fn storage(error: impl fmt::Display) -> Self {
        Self::new(503, format!("Chat storage unavailable: {error}"))
    }

    pub(crate) fn reply_body(&self) -> Value {
        let mut body = json!({"success": false, "error": self.message});
        if let Some(code) = self.code {
            body["code"] = json!(code);
        }
        body
    }
}

impl fmt::Display for ChatError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for ChatError {}

impl From<LegacyError> for ChatError {
    fn from(error: LegacyError) -> Self {
        Self::new(error.status, error.message)
    }
}

impl From<RuntimeError> for ChatError {
    fn from(error: RuntimeError) -> Self {
        Self::new(error.status, error.message)
    }
}

impl From<std::io::Error> for ChatError {
    fn from(error: std::io::Error) -> Self {
        let status = match error.kind() {
            std::io::ErrorKind::NotFound => 404,
            std::io::ErrorKind::PermissionDenied => 403,
            _ => 500,
        };
        Self::new(status, format!("Chat storage error: {error}"))
    }
}

impl From<serde_json::Error> for ChatError {
    fn from(error: serde_json::Error) -> Self {
        Self::bad_request(format!("Invalid Chat JSON: {error}"))
    }
}

/// Resolve the trusted Chat account scope. The Rust Host runs a single account
/// (`host`), so the only input is the server-owned data directory.
pub fn chat_scope(data_dir: impl AsRef<Path>) -> Result<ChatScope, ChatError> {
    let data_dir = data_dir.as_ref();
    if !data_dir.is_absolute() {
        return Err(ChatError::bad_request("Chat data root must be absolute"));
    }
    Ok(ChatScope {
        root: data_dir.join("chat"),
        preferences_dir: data_dir.to_path_buf(),
    })
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ChatScope {
    pub root: PathBuf,
    pub preferences_dir: PathBuf,
}
