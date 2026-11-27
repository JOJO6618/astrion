use super::WebStatusConfig;
use super::preferences::{Personalization, pending_modes, reasoning_effort, run_mode};
use serde_json::{Value, json};
use crate::runtime::{WebConversationSnapshot, guidance::queue_snapshot, web_messages::project_preview_targets};
use std::path::{Component, Path, PathBuf};

pub(super) fn build(
    app: &WebStatusConfig,
    id: &str,
    prefs: &Personalization,
    snapshot: WebConversationSnapshot,
) -> Value {
    let WebConversationSnapshot {
        mut document, activity, goal_progress, compression, pending_user_questions,
        pending_plan_approvals, pending_tool_approvals, resolved_tool_approval_ids, approval_review_records,
        has_running_background_commands, has_running_multi_agent,
    } = snapshot;
    let activity = activity.as_ref();
    let goal_progress = goal_progress.as_ref();
    let metadata = document["metadata"].take();
    let messages = document["messages"].take();
    let task = activity.map(task_summary);
    let running = task.is_some();
    let goal_running = goal_progress.is_some_and(|progress| matches!(progress["status"].as_str(), Some("running" | "reviewing" | "retrying")));
    let thinking = metadata["thinking_mode"].as_bool();
    let run = run_mode(
        &metadata["run_mode"],
        if thinking == Some(true) {
            "thinking"
        } else {
            "fast"
        },
    );
    let uses_worktree = metadata["git_worktree_enabled"] == true;
    let mut meta = json!({
        "title": document["title"].as_str().unwrap_or("未知对话"),
        "run_mode": run, "thinking_mode": thinking.unwrap_or(run != "fast"),
        "reasoning_effort": reasoning_effort(&metadata["reasoning_effort"]),
        "model_key": metadata["model_key"].as_str(),
        "chatgpt_fast_mode": metadata["chatgpt_fast_mode"] == true,
        "git_worktree_enabled": uses_worktree,
        "git_worktree": if uses_worktree { &metadata["git_worktree"] } else { &Value::Null },
        "pending_runtime_modes": pending_modes(&metadata),
        "execution_mode_enabled": app.runtime.host_direct_execution_available(), "network_permission_enabled": app.runtime.host_network_permissions_available(),
        "messages_count": messages.as_array().map_or(0, Vec::len),
    });
    prefs.modes(&metadata).write_fields(&mut meta);

    let live = activity.map(live_display).unwrap_or_default();
    let cursor = activity
        .and_then(|current| current["next_offset"].as_u64())
        .unwrap_or(0);
    let limit = positive_integer(&metadata["compress_limit_tokens"])
        .filter(|value| (1_000..=2_000_000).contains(value));
    let limit_source = match limit {
        None => "missing",
        Some(_) if metadata["compress_limit_source"] == "custom" => "custom",
        Some(_) => "snapshot",
    };
    let root = file_base(&app.project_path, &metadata);

    json!({
        "conversation_id": id, "workspace_id": app.workspace_id, "workspace": app.workspace(),
        "meta": meta, "messages": messages,
        "display": { "revision": task.as_ref().and_then(|task| task["task_id"].as_str()),
            "messages": live.messages, "task": task, "next_event_idx": cursor,
            "state": { "streaming": live.streaming, "api_request_pending": live.pending,
                "goal_progress": goal_progress, "goal_running": goal_running, "summary_tool_batch_id": null, "summary_tool_batch_sequence": 0,
                "pending_tool_approvals": pending_tool_approvals, "resolved_tool_approval_ids": resolved_tool_approval_ids, "pending_user_questions": pending_user_questions,
                "pending_plan_approvals": pending_plan_approvals, "approval_review_records": approval_review_records,
                "compression": if compression["in_progress"] == true { &compression } else { &Value::Null } } },
        "running": {
            "is_main_running": running,
            "main_task_id": task.as_ref().and_then(|task| task["task_id"].as_str()),
            "main_task_type": task.as_ref().and_then(|task| task["task_type"].as_str()),
            "has_running_sub_agents": false, "has_running_background_commands": has_running_background_commands,
            "has_running_multi_agent": has_running_multi_agent,
            "is_truly_active": running || has_running_background_commands || has_running_multi_agent,
        },
        "todo_list": document["todo_list"],
        "current_context_tokens": positive_integer(&document["token_statistics"]["current_context_tokens"]).unwrap_or(0),
        "context_baseline_tokens": positive_integer(&metadata["context_baseline_tokens"]).unwrap_or(0),
        "compress_limit_tokens": limit, "compress_limit_source": limit_source,
        "runtime_queue": queue_snapshot(&metadata["runtime_message_queue"], !running),
        "compression": compression,
        "edited_files": edited_files(&metadata["edited_files"], root.as_deref()),
        "preview_targets": project_preview_targets(&metadata["preview_targets"], &|path| existing_file(root.as_deref(), path)),
        "preview_base": null, "preview_token": null,
    })
}

fn task_summary(task: &Value) -> Value {
    let status = if task["cancel_requested"] == true {
        "cancel_requested"
    } else {
        task["status"].as_str().unwrap_or("running")
    };
    json!({
        "task_id": task["task_id"], "conversation_id": task["conversation_id"],
        "workspace_id": task["workspace_id"], "status": status, "created_at": task["created_at"],
        "updated_at": task["updated_at"], "task_type": task["task_type"],
        "goal_mode": task["goal_mode"] == true, "goal_progress": task["goal_progress"],
    })
}

#[derive(Default)]
struct LiveDisplay {
    messages: Vec<Value>,
    streaming: bool,
    pending: bool,
}

fn live_display(activity: &Value) -> LiveDisplay {
    let mut output = LiveDisplay {
        streaming: true,
        pending: activity["api_request_pending"] == true,
        ..LiveDisplay::default()
    };
    let Some(task_id) = activity["task_id"].as_str() else {
        return output;
    };
    let stream = &activity["stream"];
    let thinking = &activity["thinking_stream"];
    let Some(start_idx) = thinking["start_idx"].as_u64().or_else(|| stream["start_idx"].as_u64()) else {
        return output;
    };
    let timestamp = thinking["timestamp"].as_f64().or_else(|| stream["timestamp"].as_f64());
    let text = stream["content"].as_str().unwrap_or_default();
    let streaming_text = stream["streaming"] == true;
    let streaming_thinking = thinking["streaming"] == true;
    let actions = crate::runtime::thinking::display_actions(activity);
    output.messages.push(json!({
        "id": format!("{task_id}:{start_idx}:assistant"), "role": "assistant",
        "taskId": task_id, "timestamp": timestamp,
        "actions": actions,
        "streamingThinking": if streaming_thinking { thinking["content"].as_str().unwrap_or_default() } else { "" },
        "streamingText": if streaming_text { text } else { "" },
        "currentStreamingType": if streaming_text { Some("text") } else if streaming_thinking { Some("thinking") } else { None },
        "activeThinkingId": if streaming_thinking { actions.first().and_then(|action| action["id"].as_str()) } else { None },
        "awaitingFirstContent": false, "generatingLabel": "",
        "streamAttemptStart": 0, "streamAttemptBatchId": "",
    }));
    output
}

fn entries(value: &Value) -> impl Iterator<Item = &Value> {
    value.as_array().into_iter().flatten()
}

fn file_base(project_path: &Path, metadata: &Value) -> Option<PathBuf> {
    let root = project_path.canonicalize().ok()?;
    if metadata["git_worktree_enabled"] != true {
        return Some(root);
    }
    let state = &metadata["git_worktree"];
    let directory = state["directory"].as_str().map(Path::new).unwrap_or(&root);
    let actual = directory.canonicalize().ok()?;
    (directory.is_absolute() && actual == directory && actual.starts_with(&root) && actual.is_dir())
        .then_some(actual)
}

fn existing_file(root: Option<&Path>, path: &str) -> bool {
    let Some(root) = root else { return false };
    let path = Path::new(path);
    if path.is_absolute()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_) | Component::CurDir))
    {
        return false;
    }
    root.join(path)
        .canonicalize()
        .is_ok_and(|path| path.starts_with(root) && path.is_file())
}

fn edited_files(value: &Value, root: Option<&Path>) -> Vec<Value> {
    entries(value)
        .filter_map(|entry| {
            let path = entry["path"].as_str()?;
            existing_file(root, path).then(|| json!({
            "path": path, "op": entry["op"].as_str().filter(|op| !op.is_empty()).unwrap_or("edit"),
            "ts": entry["ts"].as_str().unwrap_or_default(),
        }))
        })
        .collect()
}

fn positive_integer(value: &Value) -> Option<u64> {
    value
        .as_u64()
        .or_else(|| value.as_str()?.trim().parse().ok())
}
