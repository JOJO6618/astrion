//! Host MCP settings and conversation-owned external connections.

mod access;
mod registry;
mod routes;
mod runtime;
mod scope;
mod transport;

pub use access::Actor;
pub use registry::Registry;
pub use routes::{router, router_with_registry};
pub use runtime::{LIST_TOOL, Runtime, ToolSnapshot, is_tool, model_content};
pub(crate) use runtime::model_messages;
pub use scope::RuntimeScope;

pub(crate) fn main_categories(data:&std::path::Path,workspace:&str) -> std::collections::HashSet<String> {
    let registry=Registry::for_data(data);
    if !registry.enabled() {return Default::default();}
    let (servers,settings)=registry.settings();
    access::available(servers,&settings,Actor::Main,workspace,data,&Default::default())
        .iter().map(|server|registry::category(&server.id)).collect()
}

/// Discover the built-in list tool without connecting to any server.
pub(crate) fn main_has_servers(
    data: &std::path::Path, workspace: &str, overrides: &serde_json::Value,
) -> bool {
    let registry = Registry::for_data(data);
    if !registry.enabled() {
        return false;
    }
    let (servers, settings) = registry.settings();
    let overrides = overrides.as_object().into_iter().flatten().filter_map(|(name, flag)| {
        flag.as_bool().map(|flag| (name.clone(), flag))
    }).collect();
    !access::available(servers, &settings, Actor::Main, workspace, data, &overrides).is_empty()
}
