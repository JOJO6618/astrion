use std::{collections::HashSet, time::Duration};

use serde_json::{Value, json};
use tokio::sync::oneshot;

use super::conversation_review::agent_tools as conversation_tools;
use super::{AgentTools, ModelConfig, Runtime, approval::workspace_write_request, billing::{BillingScope, ModelPrice, RequestBilling}};
use crate::{chat::Completion, goals::{GoalFailure, INTERACTION_DENIED}, process_runtime::MonitorStage};
use crate::model::RoundBudget;
use crate::tools::{easter_egg, media, vlm};
use crate::web_assets::personalization;
use crate::tools::write_file::{EDIT_NAME, WRITE_NAME, edit_file_tool, write_file_tool};


pub(super) mod tool_menu;

const GOAL_API_ATTEMPTS: u16 = 5;
const GOAL_API_RETRY_DELAY: Duration = Duration::from_secs(10);

pub(super) async fn run(
    runtime: &Runtime,
    task_id: &str,
    model: ModelConfig,
    messages: Vec<Value>,
    tools: &dyn AgentTools,
    billing_scope: BillingScope,
    saved_context_tokens: Option<u64>,
) -> Result<Vec<Value>, String> {
    let thinking = super::thinking_auxiliary::ThinkingWork::for_task(runtime, task_id, &model);
    let result = run_inner(runtime, task_id, model, messages, tools, billing_scope, saved_context_tokens, thinking.as_ref()).await;
    if result.is_ok() && let Some(thinking) = &thinking { thinking.finish().await; }
    result
}

async fn run_inner(
    runtime: &Runtime, task_id: &str, model: ModelConfig, mut messages: Vec<Value>, tools: &dyn AgentTools,
    billing_scope: BillingScope, saved_context_tokens: Option<u64>, thinking: Option<&super::thinking_auxiliary::ThinkingWork>,
) -> Result<Vec<Value>, String> {
    let mut budget = RoundBudget::new(saved_context_tokens);
    let goal = runtime.task_goal(task_id)?;
    let workflow = tools.allows_workflow();
    let transfer_paths = if workflow { runtime.transfer_paths(task_id)? } else { Value::Null };
    let media_data = runtime.store().map_err(|error| error.message)?.data_dir.clone();
    let images = model.supports_images();
    let videos = model.supports_videos();
    let media_allowed = |name: &str| match name {
        media::IMAGE => images,
        media::VIDEO => videos,
        _ => false,
    };
    if workflow {
        runtime.workflow_task_started(task_id)?;
        let runtime = runtime.clone();
        let task_id = task_id.to_owned();
        tokio::task::spawn_blocking(move || runtime.activate_sub_agents_for_task(&task_id))
            .await.map_err(|_| "Child activation interrupted".to_owned())??;
    }
    let mut writer = runtime.task_has_approval_scope(task_id)?.then(|| tools.writer()).flatten();
    let mut rebound: Option<crate::tools::ToolSet> = None;
    let is_todo = |name: &str| workflow && tools.writer().is_some() && matches!(name, super::todos::CREATE | super::todos::UPDATE);
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
        if workflow {
            runtime.queue_auto_compression(task_id)?;
            if let Some(compacted) = runtime.compress_turn(task_id, &model).await? {
                messages = compacted;
                budget = RoundBudget::new(None);
            }
        }
        if workflow && runtime.task_has_approval_scope(task_id)? && let Some(notice) = runtime.apply_browser_settings(task_id)? {
            messages.push(notice);
        }
        if workflow {
            // 运行期模式切换在完整工具批次后生效；通知必须随本轮请求送达模型。
            for notice in runtime.apply_pending_host_modes(task_id, rounds == 1)? {
                messages.push(json!({"role": "user", "content": notice["content"].clone()}));
            }
        }
        let tool_menu::ToolMenu {complete_schemas, round_schemas, browser_names, interaction_names, process_names, disabled, mcp, mcp_snapshot, mut deferred} =
            tool_menu::for_round(runtime, task_id, tools, &model).await?;
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
        let request = media::model_messages(with_instructions.as_deref().unwrap_or(&messages), &media_data, images, videos);
        let request = crate::mcp::model_messages(&request, images);
        let mut attempt = 0;
        let (completion, bill, stream) = loop {
            attempt += 1;
            let goal_active = goal.as_ref().map(|run| runtime.goals.is_active(run)).transpose()
                .map_err(|error| error.message)?.unwrap_or(false);
            let max_attempts = if goal_active { GOAL_API_ATTEMPTS } else { 1 };
            let bill = runtime.billing.begin(billing_scope.clone(), ModelPrice {
                model_key: model.key(), model_id: &model.model, identity: &model.price_identity, responses,
            }).await;
            runtime.emit(task_id, "api_request_start", json!({"attempt":attempt,"max_attempts":max_attempts}))?;
            let (result, bill, stream) = stream_completion(runtime, task_id, &model, &request, &round_schemas, &mut budget, bill, workflow, thinking).await?;
            match result {
                Ok(completion) => break (completion, bill, stream),
                Err(error) if attempt < max_attempts && GoalFailure::from_message(&error).reason() == "retryable" => {
                    runtime.emit(task_id, "error", json!({
                        "message":error,"retry":true,"fatal":false,"retry_in":GOAL_API_RETRY_DELAY.as_secs(),
                        "attempt":attempt,"max_attempts":max_attempts,
                    }))?;
                    if stream.text_started || stream.thinking_started {
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
        let content = completion.message["content"].as_str().unwrap_or_default();
        if stream.text_started || (stream.thinking_started && !content.is_empty()) {
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
            let model_key = model.key().to_owned();
            tokio::task::spawn_blocking(move || {
                if let Some(goal) = goal
                    && let Some(tokens) = crate::usage::normalize_usage(&usage)
                {
                    runtime.goals.record_usage(&goal, &request_id, tokens.total_tokens).map_err(|error| error.message)?;
                }
                runtime.record_usage(&id, &usage, Some(&model_key))
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
                || super::knowledge_tools::is_managed(name) || media_allowed(name) || name == crate::tools::web::SAVE
                || super::worktree::supports(name) || matches!(name, conversation_tools::SEARCH | conversation_tools::REVIEW) || matches!(name, personalization::TOOL | personalization::CREATE_SKILL) || (name == vlm::NAME && !images && !videos)
                || super::schedule_tools::supports(name) || name == easter_egg::NAME
                || deferred.as_ref().is_some_and(|state| state.pending(name)))
            {
                return Ok(false);
            }
            if is_todo(name) {
                return arguments.as_object().map(|_| false).ok_or_else(|| format!("Invalid {name} arguments"));
            }
            if process_names.contains(name) || interaction_names.contains(name) || browser_names.contains(name) || mcp_snapshot.allows(name) {
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
            let active: &dyn AgentTools = rebound.as_ref().map(|tools| tools as &dyn AgentTools).unwrap_or(tools);
            active.validate(name, arguments).map(|_| false)
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
                // Goal review has already committed the assistant and reached a terminal decision.
                let _ = runtime.finish_agent_context(task_id,&model,None).await?;
                return Ok(Vec::new());
            }
            let guidance = runtime.guidance_after_response(task_id, Some(completion.message.clone())).await?;
            if guidance.is_empty() {
                match runtime.finish_agent_context(task_id,&model,Some(completion.message)).await? {
                    std::ops::ControlFlow::Break(reply)=>return Ok(reply),
                    std::ops::ControlFlow::Continue(history)=>{
                        messages=history;
                        budget=RoundBudget::new(None);
                        rounds=0;
                        continue;
                    }
                }
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
        let mut browser_images = Vec::new();
        let independent_tool = calls.len() == 1;
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
            let mut read_state = None;
            let mut result = if call.interaction_denied {
                json!({"success":false,"error_code":"goal_interaction_denied","error":INTERACTION_DENIED})
            } else if workflow && call.name == super::tool_loading::NAME {
                deferred.as_mut().map(|state| state.load(&call.arguments, &complete_schemas))
                    .unwrap_or_else(|| super::tool_loading::failure("当前对话未启用工具加载。"))
            } else if deferred.as_ref().is_some_and(|state| state.pending(&call.name)) {
                super::tool_loading::blocked(&call.name)
            } else if workflow && call.name == easter_egg::NAME {
                if runtime.can_edit_settings(task_id).unwrap_or(false) {
                    easter_egg::execute(&call.arguments,super::now())
                } else {
                    json!({"success":false,"error":"只读或计划模式不触发彩蛋。"})
                }
            } else if workflow && super::schedule_tools::supports(&call.name) {
                runtime.execute_schedule_tool(task_id,&call.id,&call.name,&call.arguments,&batch).await
            } else if workflow && call.name == personalization::CREATE_SKILL {
                let source = call.arguments["source_dir"].as_str().unwrap_or_default().trim().to_owned();
                let context = runtime.personalization.clone().ok_or_else(|| "个人技能库不可用。".to_owned())
                    .and_then(|settings| runtime.skill_roots(task_id,&source).map(|(workspace,root)|(settings,workspace,root)));
                match context {
                    Ok((settings,workspace,root)) => {
                        match runtime.approve_host_mutation(task_id,&call.id,&call.name,&call.arguments,&batch).await {
                            Ok(()) => {
                                match runtime.skill_roots(task_id,&source) {
                                    Ok(current) if current == (workspace.clone(),root.clone()) => {
                                        tokio::task::spawn_blocking(move || settings.create_workspace_skill(&workspace,&root,&source)).await
                                            .map_err(|error|error.to_string())?
                                            .unwrap_or_else(|error| json!({"success":false,"error":error.to_string()}))
                                    }
                                    _ => json!({"success":false,"error":"执行前技能目录授权已变化。"}),
                                }
                            }
                            Err(result) => result,
                        }
                    }
                    Err(error) => json!({"success":false,"error":error}),
                }
            } else if workflow && call.name == personalization::TOOL {
                match runtime.personalization.clone().filter(|_| runtime.can_edit_settings(task_id).unwrap_or(false)) {
                    Some(settings) => {
                        let arguments = call.arguments.clone();
                        tokio::task::spawn_blocking(move || settings.apply_model_tool(&arguments)).await
                            .map_err(|error| error.to_string())?
                            .unwrap_or_else(|_| json!({"success":false,"error":"无法访问个人偏好。"}))
                    }
                    None => json!({"success":false,"error":"当前对话没有个人偏好权限。"}),
                }
            } else if workflow && call.name == conversation_tools::SEARCH {
                runtime.agent_conversations_search(task_id, &call.arguments)
                    .unwrap_or_else(|error| json!({"success":false,"error":error.message}))
            } else if workflow && call.name == conversation_tools::REVIEW {
                match runtime.agent_conversation_review(task_id, &call.arguments) {
                    Ok(review) => match review.save {
                        Some(arguments) => if let Some(writer) = &writer {
                            let request = workspace_write_request(&call.id, WRITE_NAME, arguments)?;
                            let written = runtime.execute_workspace_write(writer.clone(), task_id, request, &messages, &batch).await?;
                            if written["success"] == true { review.result } else { written }
                        } else {
                            json!({"success":false,"error":"当前对话没有回顾文件写入权限。"})
                        },
                        None => review.result,
                    },
                    Err(error) => json!({"success":false,"error":error.message}),
                }
            } else if workflow && super::worktree::supports(&call.name) {
                if !independent_tool {
                    json!({"success":false,"error":"请在单独的工具批次切换工作树。"})
                } else {
                    let (result, changed) = runtime.execute_worktree_tool(task_id, &call.name, &call.arguments).await?;
                    if let Some(active) = changed {
                        writer = active.writer();
                        rebound = Some(active);
                    }
                    result
                }
            } else if workflow && call.name == vlm::NAME && !images && !videos {
                let path = call.arguments["path"].as_str().unwrap_or_default().trim();
                if path.is_empty() {
                    json!({"success":false,"error":"缺少本地图片路径。","warnings":[]})
                } else if runtime.web_request_context(task_id)?.is_none() {
                    json!({"success":false,"code":"network_disabled","error":"当前对话已禁止联网。","warnings":[]})
                } else {
                    match runtime.media_roots(task_id, path, &transfer_paths) {
                        Ok((workspace, execution)) => vlm::execute(media_data.clone(),workspace,execution,transfer_paths.clone(),
                            path.to_owned(),&call.arguments["prompt"]).await,
                        Err(error) => json!({"success":false,"error":error,"warnings":[]}),
                    }
                }
            } else if workflow && media_allowed(&call.name) {
                let path = call.arguments["path"].as_str().unwrap_or_default().trim();
                if path.is_empty() {
                    json!({"success":false,"error":"缺少本地媒体路径。"})
                } else {
                    match runtime.media_roots(task_id, path, &transfer_paths) {
                        Ok((workspace, execution)) => {
                            let (name, path, data, aliases) = (call.name.clone(), path.to_owned(), media_data.clone(), transfer_paths.clone());
                            tokio::task::spawn_blocking(move || media::execute(&data, &workspace, &execution, &aliases, &name, &path, images, videos))
                                .await.map_err(|error|error.to_string())?
                        }
                        Err(error) => json!({"success":false,"error":error}),
                    }
                }
            } else if workflow && super::knowledge_tools::is_managed(&call.name) {
                let (result, checkpoint) = runtime.execute_knowledge(task_id, &call.name, &call.arguments, &batch).await?;
                read_state = checkpoint;
                result
            } else if workflow && call.name == crate::tools::web::SAVE {
                match (&writer, runtime.web_request_context(task_id)?) {
                    (Some(writer), Some(data)) if runtime.can_edit_settings(task_id).unwrap_or(false) => match crate::tools::web::prepare_save(&data, &call.arguments).await {
                        Ok(save) => {
                            let request = workspace_write_request(&call.id, WRITE_NAME, save.arguments.clone())?;
                            let written = runtime.execute_workspace_write(writer.clone(), task_id, request, &messages, &batch).await?;
                            save.result(written)
                        }
                        Err(result) => result,
                    },
                    _ => json!({"success":false,"code":"save_webpage_unavailable","error":"当前对话没有联网或文件写入权限。"}),
                }
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
            } else if browser_names.contains(&call.name) {
                runtime.execute_browser_tool(task_id, &call.id, &call.name, call.arguments, &batch).await?
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
                    let active: &dyn AgentTools = rebound.as_ref().map(|tools| tools as &dyn AgentTools).unwrap_or(tools);
                    active.execute(&call.name, arguments).await?
                }
            };
            if call.name == "browser_take_screenshot" && model.supports_images()
                && let Some(path) = result["screenshot_path"].as_str()
                && let Some(image) = runtime.browser_screenshot(task_id, std::path::Path::new(path))
            {
                browser_images.push(image);
                result["image_attached"] = json!(true);
            }
            if let Some((id, args)) = monitor && result["success"] != false {
                runtime.capture_process_monitor(task_id, &id, MonitorStage::After, &call.name, &args).await;
            }
            let media_reference = result["media_ref"].as_object().map(|_| result.clone());
            let content = match result {
                Value::String(text) => text,
                other => other.to_string(),
            };
            let mut message = json!({"role":"tool","tool_call_id":call.id,"name":call.name,"content":content});
            if let Some(result) = media_reference {
                media::annotate(&mut message, &result);
            }
            if let Some(read_state) = read_state {
                super::knowledge_tools::annotate(&mut message, read_state);
            }
            batch.push(message);
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
        if !browser_images.is_empty() {
            browser_images.insert(0, json!({"type":"text","text":"本轮浏览器工具的截图："}));
            messages.push(json!({"role":"user","content":browser_images}));
        }
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

struct StreamProgress {
    text_started: bool,
    thinking_started: bool,
}

async fn stream_completion(
    runtime: &Runtime, task_id: &str, model: &ModelConfig, request: &[Value], schemas: &[Value],
    budget: &mut RoundBudget, mut bill: RequestBilling, track_preview: bool, work: Option<&super::thinking_auxiliary::ThinkingWork>,
) -> Result<(Result<Completion, String>, RequestBilling, StreamProgress), String> {
    use crate::model::StreamDelta;

    let mut text_started = false;
    let response_id = format!("msg_{}", uuid::Uuid::new_v4().simple());
    let mut thinking = super::thinking::ThinkingStream::new(runtime, task_id, &response_id, work);
    let mut preview = track_preview.then(super::preview_tracking::ModelText::default);
    let mut tool_stream = super::tool_call_stream::ToolCallStream::default();
    let (abort, failed) = oneshot::channel();
    let mut abort = Some(abort);
    let mut completion = tokio::select! {
        biased;
        failure = failed => return Err(failure.unwrap_or_else(|_| "Text event writer closed".into())),
        completion = model.complete_with_updates(request, schemas, budget, |delta| {
            if abort.is_none() { return; }
            let emitted = match delta {
                StreamDelta::Thinking(delta) => thinking.push(delta),
                StreamDelta::NativeSummary(summary) => { thinking.native_summary(summary); Ok(()) }
                StreamDelta::Usage(_) => Ok(()),
                StreamDelta::ToolCall(update) => {
                    let events = tool_stream.push(update);
                    // Arguments are streaming: the reasoning display closes before
                    // the preparing blocks open, matching the previous backend.
                    thinking.end().and_then(|()| {
                        events.iter().try_for_each(|event| {
                            super::tool_call_stream::publish(event, |kind, data| runtime.emit(task_id, kind, data))
                        })
                    })
                }
                StreamDelta::Text(delta) => {
                    bill.observe_text(delta);
                    thinking.end().and_then(|()| emit_text(runtime, task_id, &response_id, &mut text_started, delta))
                        .and_then(|()| {
                            if let Some(preview) = preview.as_mut() {
                                runtime.record_preview_targets(task_id, preview.feed(delta, false))?;
                            }
                            Ok(())
                        })
                }
            };
            if let Err(error) = emitted {
                let _ = abort.take().expect("text event writer").send(error);
            }
        }) => completion,
    };
    if abort.is_none() { return Err("Could not publish streamed task text".into()); }
    if let Ok(completion) = &mut completion {
        thinking.end()?;
        thinking.keep();
        completion.message["message_id"] = json!(response_id);
    }
    if completion.is_ok() && let Some(mut preview) = preview {
        runtime.record_preview_targets(task_id, preview.feed("", true))?;
    }
    Ok((completion, bill, StreamProgress { text_started, thinking_started: thinking.started() }))
}

fn emit_text(runtime: &Runtime, id: &str, response_id: &str, started: &mut bool, delta: &str) -> Result<(), String> {
    if !*started {
        runtime.emit(id, "text_start", json!({"response_message_id":response_id}))?;
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
