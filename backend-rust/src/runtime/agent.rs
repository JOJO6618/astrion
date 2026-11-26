use std::{collections::HashSet, time::Duration};

use serde_json::{Value, json};
use tokio::sync::oneshot;

use super::{AgentTools, ModelConfig, Runtime, approval::workspace_write_request, billing::{BillingScope, ModelPrice, RequestBilling}};
use crate::{chat::Completion, goals::{GoalFailure, INTERACTION_DENIED}, process_runtime::MonitorStage};
use crate::model::RoundBudget;
use crate::tools::write_file::{EDIT_NAME, WRITE_NAME, edit_file_tool, write_file_tool};


const GOAL_API_ATTEMPTS: u16 = 5;
const GOAL_API_RETRY_DELAY: Duration = Duration::from_secs(10);

pub(super) async fn run(
    runtime: &Runtime,
    task_id: &str,
    model: ModelConfig,
    mut messages: Vec<Value>,
    tools: &dyn AgentTools,
    billing_scope: BillingScope,
    saved_context_tokens: Option<u64>,
) -> Result<Vec<Value>, String> {
    let mut budget = RoundBudget::new(saved_context_tokens);
    let goal = runtime.task_goal(task_id)?;
    let workflow = tools.allows_workflow();
    let transfer_paths = if workflow { runtime.transfer_paths(task_id)? } else { Value::Null };
    if workflow {
        runtime.workflow_task_started(task_id)?;
        let runtime = runtime.clone();
        let task_id = task_id.to_owned();
        tokio::task::spawn_blocking(move || runtime.activate_sub_agents_for_task(&task_id))
            .await.map_err(|_| "Child activation interrupted".to_owned())??;
    }
    let writer = runtime.task_has_approval_scope(task_id)?.then(|| tools.writer()).flatten();
    let mut schemas = tools.schemas();
    let allow_todos = workflow && tools.writer().is_some();
    if allow_todos { schemas.extend(super::todos::schemas()); }
    let is_todo = |name: &str| allow_todos && matches!(name, super::todos::CREATE | super::todos::UPDATE);
    if writer.is_some() {
        schemas.extend([write_file_tool(), edit_file_tool()]);
    }
    if workflow {
        schemas.extend(runtime.workflow_tool_schemas());
        schemas.extend(runtime.sub_agent_tool_schemas());
        schemas.extend(crate::tools::web::definitions());
    }
    let responses = reqwest::Url::parse(&model.endpoint).is_ok_and(|url| {
        url.path().trim_end_matches('/').rsplit('/').next() == Some("responses")
    });
    let mut used_ids: HashSet<String> = messages
        .iter()
        .filter_map(|message| message["tool_calls"].as_array())
        .flatten()
        .filter_map(|call| call["id"].as_str().map(str::to_owned))
        .collect();

    let max_rounds = runtime.store().map_err(|e| e.message)?.tasks[task_id].max_iterations;
    let mut rounds: u64 = 0;
    let mut batch_sequence = 0;
    loop {
        rounds += 1;
        if workflow && let Some(compacted) = runtime.compress_turn(task_id, &model).await? {
            messages=compacted;
            budget=RoundBudget::new(None);
        }
        let mut round_schemas = schemas.clone();
        let mcp = if workflow { runtime.mcp_for_task(task_id).await? } else { None };
        let mcp_snapshot = if let Some(manager) = &mcp {
            let snapshot = manager.prepare_tools().await;
            round_schemas.extend_from_slice(snapshot.schemas());
            snapshot
        } else {
            crate::mcp::ToolSnapshot::default()
        };
        let interaction_schemas = if workflow { runtime.interaction_tools(task_id)? } else { Vec::new() };
        let interaction_names = interaction_schemas.iter().filter_map(|tool| tool["function"]["name"].as_str())
            .map(str::to_owned).collect::<HashSet<_>>();
        round_schemas.extend(interaction_schemas);
        let process_schemas = if workflow { runtime.process_tool_schemas(task_id)? } else { Vec::new() };
        let process_names = process_schemas.iter().filter_map(|tool| tool["function"]["name"].as_str())
            .map(str::to_owned).collect::<HashSet<_>>();
        round_schemas.extend(process_schemas);
        let disabled = if workflow { runtime.disabled_task_tools(task_id)? } else { Default::default() };
        round_schemas.retain(|schema| !schema["function"]["name"].as_str().is_some_and(|name| disabled.contains(name)));
        let complete_schemas = round_schemas;
        let mut deferred = if workflow { runtime.deferred_tools(task_id, &complete_schemas)? } else { None };
        let round_schemas = deferred.as_ref().map(|state| state.schemas(&complete_schemas))
            .unwrap_or_else(|| complete_schemas.clone());
        let workflow_prompt = if workflow {
            messages.extend(runtime.drain_sub_agent_notices(task_id, None)?);
            messages.extend(runtime.drain_process_notices(task_id, None)?);
            messages.extend(runtime.workflow_drain_notices(task_id, None)?);
            runtime.workflow_before_round(task_id)?
        } else {
            None
        };
        let goal_prompt = goal.as_ref().map(|run| runtime.goals.prompt(run)).transpose()
            .map_err(|error| error.message)?.flatten();
        let with_instructions = if workflow_prompt.is_some() || goal_prompt.is_some() {
            let mut request = messages.clone();
            if let Some(prompt) = workflow_prompt {
                request.push(json!({"role":"system","content":prompt}));
            }
            if let Some(prompt) = goal_prompt {
                request.push(json!({"role":"user","content":prompt["content"]}));
            }
            Some(request)
        } else {
            None
        };
        let request = crate::mcp::model_messages(with_instructions.as_deref().unwrap_or(&messages), model.supports_images());
        let mut attempt = 0;
        let (completion, bill, text_started) = loop {
            attempt += 1;
            let goal_active = goal.as_ref().map(|run| runtime.goals.is_active(run)).transpose()
                .map_err(|error| error.message)?.unwrap_or(false);
            let max_attempts = if goal_active { GOAL_API_ATTEMPTS } else { 1 };
            let bill = runtime.billing.begin(billing_scope.clone(), ModelPrice {
                model_key: model.key(), model_id: &model.model, identity: &model.price_identity, responses,
            }).await;
            runtime.emit(task_id, "api_request_start", json!({"attempt":attempt,"max_attempts":max_attempts}))?;
            let (result, bill, text_started) = stream_completion(runtime, task_id, &model, &request, &round_schemas, &mut budget, bill, workflow).await?;
            match result {
                Ok(completion) => break (completion, bill, text_started),
                Err(error) if attempt < max_attempts && GoalFailure::from_message(&error).reason() == "retryable" => {
                    runtime.emit(task_id, "error", json!({
                        "message":error,"retry":true,"fatal":false,"retry_in":GOAL_API_RETRY_DELAY.as_secs(),
                        "attempt":attempt,"max_attempts":max_attempts,
                    }))?;
                    if text_started {
                        runtime.emit(task_id, "stream_reset", json!({"reason":"stream_disconnected","attempt":attempt + 1}))?;
                    }
                    tokio::time::sleep(GOAL_API_RETRY_DELAY).await;
                }
                Err(error) => return Err(error),
            }
        };
        let mut completion = completion;
        crate::tools::web::annotate(&mut completion.message, &messages);
        let request_id = bill.request_id().to_owned();
        let settled = bill.settle_success(completion.usage.as_ref(), completion.responses_duration_ms)
            .await.map_err(|error| format!("Could not persist model usage: {error}"))?;
        budget.observe_usage(completion.usage.as_ref());
        if text_started {
            let content = completion.message["content"].as_str().unwrap_or_default();
            let mut event = json!({"full_content":content});
            if let Some(citations) = completion.message["metadata"].get("citations") {
                event["citations"] = citations.clone();
            }
            runtime.emit(task_id, "text_end", event)?;
        }
        if settled && let Some(usage) = completion.usage {
            let runtime = runtime.clone();
            let id = task_id.to_owned();
            let goal = goal.clone();
            tokio::task::spawn_blocking(move || {
                if let Some(goal) = goal
                    && let Some(tokens) = crate::usage::normalize_usage(&usage)
                {
                    runtime.goals.record_usage(&goal, &request_id, tokens.total_tokens).map_err(|error| error.message)?;
                }
                runtime.record_usage(&id, &usage)
            })
                .await
                .map_err(|_| "Usage writer exited unexpectedly".to_owned())??;
        }

        let calls = validated_calls(&completion.message, &mut used_ids, |name, arguments| {
            if disabled.contains(name) { return Err(format!("Tool {name} is disabled")); }
            if let Some(run) = &goal
                && runtime.goals.interaction_denied(run, name, arguments, false).map_err(|error| error.message)?
            {
                return Ok(true);
            }
            if workflow && (name == super::tool_loading::NAME || crate::tools::web::supports(name)
                || deferred.as_ref().is_some_and(|state| state.pending(name)))
            {
                return Ok(false);
            }
            if is_todo(name) {
                return arguments.as_object().map(|_| false).ok_or_else(|| format!("Invalid {name} arguments"));
            }
            if process_names.contains(name) || interaction_names.contains(name) || mcp_snapshot.allows(name) {
                return Ok(false);
            }
            if writer.is_some() && matches!(name, WRITE_NAME | EDIT_NAME) {
                return workspace_write_request("validation", name, arguments.clone()).map(|_| false);
            }
            if workflow && let Some(result) = runtime.validate_workflow_tool(name, arguments) {
                return result.map(|_| false);
            }
            if workflow && let Some(result) = runtime.validate_sub_agent_tool(name, arguments) {
                return result.map(|_| false);
            }
            tools.validate(name, arguments).map(|_| false)
        })?;
        if calls.is_empty() {
            if workflow && max_rounds.is_none_or(|limit| rounds < limit) {
                let mut notices = runtime.drain_sub_agent_notices(task_id, Some(completion.message.clone()))?;
                let preceding = notices.is_empty().then(|| completion.message.clone());
                notices.extend(runtime.drain_process_notices(task_id, preceding)?);
                let preceding = notices.is_empty().then_some(&completion.message);
                notices.extend(runtime.workflow_drain_notices(task_id, preceding)?);
                if !notices.is_empty() {
                    messages.push(completion.message);
                    messages.extend(notices);
                    continue;
                }
            }
            if let Some(run) = &goal {
                messages.push(completion.message);
                let assistant = messages.last().expect("goal assistant message");
                if let Some(user) = runtime.review_goal_turn(run, assistant, &messages).await? {
                    messages.push(user);
                    rounds = 0;
                    continue;
                }
                let guidance = runtime.guidance_after_response(task_id, None).await?;
                if !guidance.is_empty() {
                    messages.extend(guidance);
                    rounds = 0;
                    continue;
                }
                return runtime.finish_agent_context(task_id,&model,None).await;
            }
            let guidance = runtime.guidance_after_response(task_id, Some(completion.message.clone())).await?;
            if guidance.is_empty() {
                return runtime.finish_agent_context(task_id,&model,Some(completion.message)).await;
            }
            messages.push(completion.message);
            messages.extend(guidance);
            rounds = 0;
            continue;
        }
        if max_rounds.is_some_and(|limit| rounds >= limit) {
            return Err("Agent reached the model round limit".into());
        }
        let tool_count = calls.len() as u64;
        let mut batch = Vec::with_capacity(calls.len() + 1);
        batch.push(completion.message);
        let questions = calls.iter().filter(|call| !call.interaction_denied
            && call.name == crate::interactions::ASK_USER
            && interaction_names.contains(&call.name)).collect::<Vec<_>>();
        for call in &questions {
            emit_tool_start(runtime, task_id, call, None)?;
        }
        let mut answers = runtime.wait_for_questions(task_id, questions.into_iter().map(|call| {
            (call.id.as_str(), &call.arguments)
        })).await?;
        for call in calls {
            let answer = answers.remove(&call.id);
            let monitor = (workflow && !call.interaction_denied
                && matches!(call.name.as_str(), "read_file" | WRITE_NAME | EDIT_NAME))
                .then(|| (Runtime::process_monitor_id(), call.arguments.clone()));
            if let Some((id, args)) = &monitor {
                runtime.capture_process_monitor(task_id, id, MonitorStage::Before, &call.name, args).await;
            }
            if answer.is_none() {
                emit_tool_start(runtime, task_id, &call, monitor.as_ref().map(|(id, _)| id.as_str()))?;
            }
            let result = if call.interaction_denied {
                json!({"success":false,"error_code":"goal_interaction_denied","error":INTERACTION_DENIED})
            } else if workflow && call.name == super::tool_loading::NAME {
                deferred.as_mut().map(|state| state.load(&call.arguments, &complete_schemas))
                    .unwrap_or_else(|| super::tool_loading::failure("当前对话未启用工具加载。"))
            } else if deferred.as_ref().is_some_and(|state| state.pending(&call.name)) {
                super::tool_loading::blocked(&call.name)
            } else if workflow && crate::tools::web::supports(&call.name) {
                match runtime.web_request_context(task_id)? {
                    Some(data) => crate::tools::web::execute(&data, &call.name, &call.arguments).await,
                    None => json!({"success":false,"code":"network_disabled","error":"当前对话已禁止联网。"}),
                }
            } else if let Some(answer) = answer {
                answer
            } else if call.name == crate::interactions::SUBMIT_PLAN && interaction_names.contains(&call.name) {
                runtime.submit_plan(task_id, &call.id, &call.arguments).await?
            } else if is_todo(&call.name) {
                runtime.execute_todo(task_id, &call.name, call.arguments).await?
            } else if process_names.contains(&call.name) {
                runtime.execute_process_tool(task_id, &call.id, &call.name, call.arguments, &batch).await?
            } else if let Some(manager) = &mcp && mcp_snapshot.allows(&call.name) {
                manager.execute(&mcp_snapshot, &call.name, call.arguments).await
            } else if workflow && runtime.validate_sub_agent_tool(&call.name, &call.arguments).is_some() {
                runtime.execute_sub_agent_tool(task_id, &model, &call.name, &call.arguments).await?
            } else if let Some(writer) = &writer
                && matches!(call.name.as_str(), WRITE_NAME | EDIT_NAME)
            {
                let request = workspace_write_request(&call.id, &call.name, call.arguments)?;
                let file_path = request.arguments["file_path"].as_str().map(str::to_owned);
                let result = runtime.execute_workspace_write(writer.clone(), task_id, request, &messages, &batch).await?;
                if result["success"] == true && let Some(path) = file_path {
                    runtime.record_preview_file(task_id, &path)?;
                }
                result
            } else if workflow && runtime.validate_workflow_tool(&call.name, &call.arguments).is_some() {
                let history = messages.iter().chain(&batch).cloned().collect::<Vec<_>>();
                runtime.execute_workflow_tool(task_id, &call.name, &call.arguments, &history).await?
            } else {
                let arguments = super::transfer::read_arguments(&call.name, call.arguments, &transfer_paths);
                if workflow && call.name == crate::tools::read_file::NAME
                    && let Some(result) = runtime.read_authorized_host_path(task_id, &arguments).await?
                {
                    result
                } else {
                    tools.execute(&call.name, arguments).await?
                }
            };
            if let Some((id, args)) = monitor && result["success"] != false {
                runtime.capture_process_monitor(task_id, &id, MonitorStage::After, &call.name, &args).await;
            }
            let content = match result {
                Value::String(text) => text,
                other => other.to_string(),
            };
            batch.push(json!({"role":"tool","tool_call_id":call.id,"name":call.name,"content":content}));
        }
        let checkpoint = batch.clone();
        let batch_writer = runtime.clone();
        let id = task_id.to_owned();
        let goal_run = goal.clone();
        batch_sequence += 1;
        let guidance = tokio::task::spawn_blocking(move || {
            let guidance = batch_writer.commit_tool_batch(&id, batch_sequence, checkpoint)?;
            if let Some(run) = goal_run {
                batch_writer.goals.record_tool_calls(&run, tool_count).map_err(|error| error.message)?;
            }
            Ok::<_, String>(guidance)
        }).await.map_err(|_| "Tool batch writer exited unexpectedly".to_owned())??;
        for row in &mut batch {
            if row["role"]=="tool" { row.as_object_mut().unwrap().remove("name"); }
        }
        messages.extend(batch);
        messages.extend(guidance);
        if workflow { super::shallow::after_batch(runtime,task_id,&model,&mut messages)?; }
    }
}

fn emit_tool_start(runtime: &Runtime, task_id: &str, call: &ToolCall, execution_id: Option<&str>) -> Result<(), String> {
    let mut event = json!({
        "id":call.id,"preparing_id":call.id,"name":call.name,"arguments":call.arguments,
    });
    if let Some(id) = execution_id {
        event["execution_id"] = json!(id);
    }
    runtime.emit(task_id, "tool_start", event)
}

async fn stream_completion(
    runtime: &Runtime, task_id: &str, model: &ModelConfig, request: &[Value], schemas: &[Value],
    budget: &mut RoundBudget, mut bill: RequestBilling, track_preview: bool,
) -> Result<(Result<Completion, String>, RequestBilling, bool), String> {
    let mut text_started = false;
    let mut preview = track_preview.then(super::preview_tracking::ModelText::default);
    let (abort, failed) = oneshot::channel();
    let mut abort = Some(abort);
    let completion = tokio::select! {
        biased;
        failure = failed => return Err(failure.unwrap_or_else(|_| "Text event writer closed".into())),
        completion = model.complete_with_budget(request, schemas, budget, |delta| {
            bill.observe_text(delta);
            if abort.is_none() { return; }
            let emitted = emit_text(runtime, task_id, &mut text_started, delta);
            let emitted = emitted.and_then(|()| {
                if let Some(preview) = preview.as_mut() {
                    runtime.record_preview_targets(task_id, preview.feed(delta, false))?;
                }
                Ok(())
            });
            if let Err(error) = emitted {
                let _ = abort.take().expect("text event writer").send(error);
            }
        }) => completion,
    };
    if abort.is_none() { return Err("Could not publish streamed task text".into()); }
    if completion.is_ok() && let Some(mut preview) = preview {
        runtime.record_preview_targets(task_id, preview.feed("", true))?;
    }
    Ok((completion, bill, text_started))
}

fn emit_text(runtime: &Runtime, id: &str, started: &mut bool, delta: &str) -> Result<(), String> {
    if !*started {
        runtime.emit(id, "text_start", json!({}))?;
        *started = true;
    }
    runtime.emit(id, "text_chunk", json!({"content":delta}))
}

struct ToolCall {
    id: String,
    name: String,
    arguments: Value,
    interaction_denied: bool,
}

fn validated_calls(
    message: &Value,
    used_ids: &mut HashSet<String>,
    validate: impl Fn(&str, &Value) -> Result<bool, String>,
) -> Result<Vec<ToolCall>, String> {
    let Some(calls) = message["tool_calls"].as_array() else {
        return Ok(Vec::new());
    };
    // No tool runs until the entire batch has been validated.
    calls
        .iter()
        .map(|call| {
            let function = &call["function"];
            let name = function["name"].as_str().ok_or("Missing tool name")?;
            let id = call["id"].as_str().ok_or("Missing tool call id")?;
            if !used_ids.insert(id.into()) {
                return Err(format!("Duplicate tool call ID: {id}"));
            }
            let arguments =
                serde_json::from_str(function["arguments"].as_str().unwrap_or_default())
                    .map_err(|_| format!("Invalid {name} arguments"))?;
            let interaction_denied = validate(name, &arguments)?;
            Ok(ToolCall {
                id: id.into(),
                name: name.into(),
                arguments,
                interaction_denied,
            })
        })
        .collect()
}
