use std::sync::Arc;

use crate::{
    catalog::CatalogSource,
    runtime::ModelConfig,
};

/// Trusted server-side model selection. The resolver receives the conversation
/// or request model key (empty means the account default), the thinking flag and
/// the effective reasoning effort. The conversation session and ChatGPT Fast
/// preference are applied after this selection.
pub type ModelResolver =
    Arc<dyn Fn(Option<&str>, bool, Option<&str>) -> Result<ModelConfig, String> + Send + Sync>;

/// Host default: the same read-only catalog snapshot the Agent gateway uses.
pub fn catalog_resolver(catalog: CatalogSource) -> ModelResolver {
    Arc::new(move |key, thinking, effort| {
        let selected = catalog.load().select_for_turn(key, thinking, effort)?;
        ModelConfig::from_catalog(selected)
    })
}

pub(crate) fn resolve(
    resolver: &ModelResolver,
    key: Option<&str>,
    thinking: bool,
    effort: Option<&str>,
    task_id: &str,
) -> Result<ModelConfig, crate::chat_mode::ChatError> {
    let key = key.map(str::trim).filter(|key| !key.is_empty());
    let effort = effort.map(str::trim).filter(|effort| !effort.is_empty());
    resolver(key, thinking, effort).map_err(|error| {
        crate::chat_mode::ChatError::new(
            503,
            format!("Chat model is unavailable for task {task_id}: {error}"),
        )
    })
}
