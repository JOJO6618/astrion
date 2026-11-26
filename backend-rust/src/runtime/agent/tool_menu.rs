use super::*;
use std::sync::Arc;

pub(super) fn supports_media(model: &ModelConfig, name: &str) -> bool {
    match name {
        media::IMAGE => model.supports_images(),
        media::VIDEO => model.supports_videos(),
        _ => false,
    }
}

pub(in crate::runtime) struct ToolMenu {
    pub(super) complete_schemas: Vec<Value>,
    pub(in crate::runtime) round_schemas: Vec<Value>,
    pub(super) browser_names: HashSet<String>,
    pub(super) interaction_names: HashSet<String>,
    pub(super) process_names: HashSet<String>,
    pub(super) plugins: Vec<super::super::custom_tools::Plugin>,
    pub(super) disabled: HashSet<String>,
    pub(super) mcp: Option<Arc<crate::mcp::Runtime>>,
    pub(super) mcp_snapshot: crate::mcp::ToolSnapshot,
    pub(super) deferred: Option<super::super::tool_loading::State>,
}

pub(in crate::runtime) async fn for_round(
    runtime: &Runtime,
    task_id: &str,
    tools: &dyn AgentTools,
    model: &ModelConfig,
) -> Result<ToolMenu, String> {
    let workflow = tools.allows_workflow();
    let can_write = runtime.task_has_approval_scope(task_id)? && tools.writer().is_some();
    let browser = if workflow && runtime.task_has_approval_scope(task_id)? {
        runtime.browser_schemas()
    } else {
        Vec::new()
    };
    let browser_names = browser
        .iter()
        .filter_map(|schema| schema["function"]["name"].as_str())
        .map(str::to_owned)
        .collect::<HashSet<_>>();

    let mut schemas = tools.schemas();
    schemas.extend(browser);
    let allow_todos = workflow && tools.writer().is_some();
    if allow_todos {
        schemas.extend(super::super::todos::schemas());
    }
    if can_write {
        schemas.extend([write_file_tool(), edit_file_tool()]);
    }
    if workflow {
        schemas.extend(runtime.workflow_tool_schemas());
        schemas.extend(runtime.worktree_tools(task_id)?);
        schemas.extend(conversation_tools::definitions());
        schemas.push(crate::tools::easter_egg::definition());
        if runtime.schedules_available() {
            schemas.extend(super::super::schedule_tools::definitions());
        }
        if runtime.personalization.is_some() {
            schemas.push(crate::web_assets::personalization::model_tool());
            if can_write {
                schemas.push(crate::web_assets::personalization::create_skill_definition());
            }
        }
        schemas.extend(runtime.sub_agent_tool_schemas());
        schemas.extend(crate::tools::web::definitions());
        if can_write {
            schemas.push(crate::tools::web::save_definition());
        }
        schemas.extend(super::super::knowledge_tools::write_schemas());
        if !model.supports_images() && !model.supports_videos() {
            schemas.push(crate::tools::vlm::definition());
        }
        schemas.extend(
            [media::IMAGE, media::VIDEO]
                .into_iter()
                .filter(|name| supports_media(model, name))
                .map(media::definition),
        );
    }
    let mut round_schemas = schemas;
    let mcp = if workflow {
        runtime.mcp_for_task(task_id).await?
    } else {
        None
    };
    let mcp_snapshot = if let Some(manager) = &mcp {
        let snapshot = manager.prepare_tools().await;
        round_schemas.extend_from_slice(snapshot.schemas());
        snapshot
    } else {
        crate::mcp::ToolSnapshot::default()
    };
    let interaction_schemas = if workflow {
        runtime.interaction_tools(task_id)?
    } else {
        Vec::new()
    };
    let interaction_names = interaction_schemas
        .iter()
        .filter_map(|tool| tool["function"]["name"].as_str())
        .map(str::to_owned)
        .collect::<HashSet<_>>();
    round_schemas.extend(interaction_schemas);
    let process_schemas = if workflow {
        runtime.process_tool_schemas(task_id)?
    } else {
        Vec::new()
    };
    let process_names = process_schemas
        .iter()
        .filter_map(|tool| tool["function"]["name"].as_str())
        .map(str::to_owned)
        .collect::<HashSet<_>>();
    round_schemas.extend(process_schemas);
    let mut plugins = if workflow && can_write {
        let data = runtime.store().map_err(|error|error.message)?.data_dir.clone();
        super::super::custom_tools::load(&data)?
    } else { Vec::new() };
    plugins.retain(|plugin| !round_schemas.iter().any(|spec|spec["function"]["name"] == plugin.name));
    round_schemas.extend(plugins.iter().map(|plugin|plugin.schema.clone()));
    let disabled = if workflow {
        runtime.disabled_task_tools(task_id)?
    } else {
        Default::default()
    };
    round_schemas.retain(|schema| {
        !schema["function"]["name"]
            .as_str()
            .is_some_and(|name| disabled.contains(name))
    });
    let complete_schemas = round_schemas;
    let deferred = if workflow {
        runtime.deferred_tools(task_id, &complete_schemas)?
    } else {
        None
    };
    let round_schemas = deferred
        .as_ref()
        .map(|state| state.schemas(&complete_schemas))
        .unwrap_or_else(|| complete_schemas.clone());
    Ok(ToolMenu {
        complete_schemas,
        round_schemas,
        browser_names,
        interaction_names,
        process_names,
        plugins,
        disabled,
        mcp,
        mcp_snapshot,
        deferred,
    })
}
