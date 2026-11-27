use std::{collections::HashSet, path::Path};

use serde_json::{Value, json};
use tokio::{sync::watch, time::Instant};

use crate::{
    conversation_db::Database,
    knowledge::{self, Knowledge, KnowledgeAccess}, model::{RoundBudget, retry::{Attempt, RETRY_DELAY}},
    runtime::{ModelConfig, todos::operations as todos},
    tools::{media, read_file, web, write_file::{EDIT_NAME, WRITE_NAME}},
};

use super::{SubAgents, execution::{Parent, Stop, now}, protocol::{self, failed}, registry::Registry, scope::ChildScope};

pub(super) struct Context {
    pub(super) document: Value,
    pub(super) persisted_messages: usize,
    pub(super) rewrite_history: bool,
    pub(super) threshold: u64,
}

impl Context {
    pub(super) fn new(record: &Value, prompt: &str) -> Self {
        Self { persisted_messages: 0, rewrite_history:false, threshold:250_000, document: json!({
            "agent_id":record["agent_id"],"task_id":record["task_id"],"name":record["name"],"working_dir":record["working_dir"],
            "execution_root":record["execution_root"],"status":"idle","round_id":0,
            "messages":[{"role":"system","content":prompt}],
            "stats":{"runtime_start":now()*1000.0,"api_calls":0,"token_usage":{"prompt":0,"completion":0,"total":0,"cached_input":0}},
        }) }
    }

    pub(super) fn load(registry: &Registry, id: &str) -> Result<Self, String> {
        let document = registry.context(id).map_err(|error| error.to_string())?
            .ok_or("原子智能体上下文不存在。")?;
        let valid = document["messages"].as_array().is_some_and(|messages| {
            !messages.is_empty() && messages.iter().all(Value::is_object) && messages[0]["role"] == "system"
        });
        if !valid { return Err("原子智能体上下文无效。".into()); }
        Ok(Self { persisted_messages: document["messages"].as_array().unwrap().len(), rewrite_history:false, threshold:250_000, document })
    }

    fn messages(&self) -> &[Value] { self.document["messages"].as_array().expect("validated child context") }

    fn append(&mut self, message: Value) {
        self.document["messages"].as_array_mut().expect("validated child context").push(message);
    }

    pub(super) fn append_worktree(&mut self, directory: &Path, branch: &str, revision: u64, inside: bool) {
        let reason = if inside { String::new() } else { "父对话选择了原工作区外的工作树。子智能体保持原工作区权限。
".to_owned() };
        self.document["execution_root"] = json!(directory);
        self.append(json!({"role":"user","name":format!("worktree_change_{revision}"),
            "content":format!("[工作树环境变更]
{reason}当前执行目录：{}
父对话分支：{branch}", directory.display())}));
    }

    pub(super) fn append_work(&mut self, work: &Value) -> Result<(), String> {
        let message = work["message"].as_str().ok_or("待处理的子智能体消息无效。")?;
        let id = work["rust_work_id"].as_str();
        let applied = &mut self.document["rust_applied_work"];
        if applied.is_null() { *applied = json!([]); }
        let applied = applied.as_array_mut().ok_or("原子智能体消息游标无效。")?;
        if let Some(id) = id {
            if applied.iter().any(|value| value == id) { return Ok(()); }
            applied.push(json!(id));
        }
        self.append(json!({"role":"user","content":message}));
        Ok(())
    }

    pub(super) fn start(&mut self, round_id: u64) {
        self.document["round_id"] = json!(round_id);
        self.set_status("running", "");
    }

    pub(super) fn set_status(&mut self, status: &str, reason: &str) {
        self.document["status"] = json!(status);
        self.document["end_reason"] = if reason.is_empty() { Value::Null } else { json!(reason) };
        self.document["updated_at"] = json!(now());
    }

    pub(super) fn mark_saved(&mut self) { self.persisted_messages = self.messages().len(); self.rewrite_history=false; }

    fn save(&mut self, registry: &Registry) -> std::io::Result<()> {
        registry.save_context(self)
    }

    pub(super) fn final_output(&self, content: &str) -> (Value, Value) {
        (
            progress(json!({"subtype":"output","content":content,"is_final":true})),
            json!({"status":self.document["status"],"summary":content,
                "success":self.document["end_reason"] == "completed", "stats":self.document["stats"]}),
        )
    }

    fn activity(&self, registry: &Registry, event: Value) -> std::io::Result<()> {
        let id = self.document["task_id"].as_str().ok_or(std::io::ErrorKind::InvalidData)?;
        registry.append_activity(id, &progress(event))
    }

    fn add_stat(&mut self, field: &str) {
        let stats = &mut self.document["stats"];
        stats[field] = json!(stats[field].as_u64().unwrap_or_default().saturating_add(1));
    }

    fn usage(&mut self, usage: Option<&Value>) {
        self.add_stat("api_calls");
        if let Some(usage) = usage.and_then(crate::usage::normalize_usage) {
            let stats = &mut self.document["stats"];
            stats["current_context_tokens"] = json!(usage.current_context_tokens);
            let tokens = &mut stats["token_usage"];
            for (field, amount) in [("prompt", usage.input_tokens), ("completion", usage.output_tokens),
                ("total", usage.total_tokens), ("cached_input", usage.cached_input_tokens)] {
                tokens[field] = json!(tokens[field].as_u64().unwrap_or_default().saturating_add(amount));
            }
        }
    }

    pub(super) fn complete_missing(&mut self, stop: Stop) {
        let messages = self.messages();
        let Some(last_assistant) = messages.iter().rposition(|message| message["role"] == "assistant") else { return; };
        let trailing = &messages[last_assistant + 1..];
        if trailing.iter().any(|message| message["role"] != "tool") { return; }
        let ids = trailing.iter().filter_map(|message| message["tool_call_id"].as_str()).collect::<HashSet<_>>();
        let missing = messages[last_assistant]["tool_calls"].as_array().into_iter().flatten()
            .filter_map(|call| call["id"].as_str()).filter(|id| !ids.contains(id)).map(str::to_owned).collect::<Vec<_>>();
        for id in missing { self.tool_result(&id, json!({"success":false,"error":stop.tool_result()})); }
    }

    fn tool_result(&mut self, id: &str, result: Value) {
        let mut message = json!({"role":"tool","tool_call_id":id,"content":result.to_string()});
        media::annotate(&mut message, &result);
        self.append(message);
    }
}

pub(super) enum End {
    Completed(String),
    Timeout,
    Failed(String),
}

pub(super) async fn supervise(app: SubAgents, parent: Parent, id: String, mut stop: watch::Receiver<Stop>,
    scope: ChildScope, mut context: Context, model: ModelConfig, timeout: Option<Instant>) {
    let worker = Worker { app: &app, parent: &parent, id: &id, scope: &scope, model: &model, timeout };
    let result = worker.run(&mut context, &mut stop).await.unwrap_or_else(End::Failed);
    if stop.borrow().cancelled() || matches!(result, End::Failed(_)) {
        let runtime = app.runtime.clone();
        let parent = parent.clone();
        let id = id.clone();
        let _ = tokio::task::spawn_blocking(move || runtime.close_child_processes(&parent.workspace_id, &parent.conversation_id, &id)).await;
    }
    if let Err(error) = app.finish(&parent, &id, &mut context, result) {
        eprintln!("Child worker {id}: {error}");
        app.fail_worker(&id);
    }
}

struct Worker<'a> {
    app: &'a SubAgents,
    parent: &'a Parent,
    id: &'a str,
    scope: &'a ChildScope,
    model: &'a ModelConfig,
    timeout: Option<Instant>,
}

impl Worker<'_> {
    fn web_enabled(&self) -> Result<bool, String> {
        let directory = self.app.registry.data_dir().join("conversations").join(&self.parent.workspace_id);
        let database = Database::open(&directory)?;
        if !database.contains(&self.parent.conversation_id)? {
            return Err("无法验证父对话的网络权限。".into());
        }
        Ok(database.field(&self.parent.conversation_id, "metadata", "network_permission")?
            .is_none_or(|value| value != "none"))
    }


    async fn run(&self, context: &mut Context, stop: &mut watch::Receiver<Stop>) -> Result<End, String> {
        let scope = self.scope;
        let mcp = self.app.mcp_for_child(self.parent, self.id, scope)?;
        let mut base_schemas = protocol::child_schemas();
        base_schemas.extend(knowledge::definitions(KnowledgeAccess::ReadOnly));
        base_schemas.extend(web::definitions());
        base_schemas.push(web::save_definition());
        base_schemas.extend(todos::schemas());
        base_schemas.push(media::definition(media::READ));
        let images = self.model.supports_images();
        let videos = self.model.supports_videos();
        let data = self.app.registry.data_dir();
        if let Some(saved) = self.app.registry.stats(self.id).map_err(io_error)?
            && saved["token_usage"]["total"].as_u64().unwrap_or_default() >
                context.document["stats"]["token_usage"]["total"].as_u64().unwrap_or_default()
        {
            context.document["stats"] = saved;
        }
        self.app.runtime.recover_child_billing(
            &self.parent.conversation_id,self.id,&mut context.document["stats"],
        ).await.map_err(io_error)?;
        let child_name = context.document["name"].as_str().filter(|name|!name.trim().is_empty())
            .unwrap_or(self.id).to_owned();
        let mut budget = RoundBudget::new(context.document["stats"]["current_context_tokens"].as_u64());
        let mut rebound_scope = None;
        loop {
            self.app.consume_guidance(self.parent, self.id, context)?;
            if let Some(updated) = self.app.child_worktree_at_safe_point(self.parent, self.id, context)? {
                rebound_scope = Some(updated);
            }
            let scope = rebound_scope.as_ref().unwrap_or(self.scope);
            scope.check()?;
            if self.timeout.is_some_and(|deadline| Instant::now() >= deadline) { return Ok(End::Timeout); }
            if stop.borrow().cancelled() { return Ok(End::Failed("子智能体已停止。".into())); }
            let mcp_snapshot = tokio::select! {
                biased;
                _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                snapshot = mcp.prepare_tools() => snapshot,
            };
            let mut schemas = base_schemas.clone();
            schemas.extend_from_slice(mcp_snapshot.schemas());
            schemas.extend(self.app.process_tools(scope));
            let opencode = self.model.is_opencode_endpoint();
            if opencode && context.document["external_session_id"].as_str().is_none_or(|id|id.trim().is_empty()) {
                context.document["external_session_id"] = json!(uuid::Uuid::new_v4().simple().to_string());
                // Preserve this actor's provider identity even when the first request fails.
                context.save(&self.app.registry).map_err(io_error)?;
            }
            let external_id = context.document["external_session_id"].as_str().map(str::trim).filter(|id|!id.is_empty());
            let model = external_id.map_or_else(||self.model.clone(),|id|self.model.clone().with_session(id,false))
                .with_opencode_headers(external_id.filter(|_|opencode),true);
            let request = media::model_messages(context.messages(), data, images, videos);
            let request = crate::mcp::model_messages(&request, images);
            let (mut completion, billing) = {
                let mut attempt = 0;
                loop {
                    if self.timeout.is_some_and(|deadline| Instant::now() >= deadline) { return Ok(End::Timeout); }
                    if stop.borrow().cancelled() { return Ok(End::Failed("子智能体已停止。".into())); }
                    attempt += 1;
                    let retry = Attempt::new(attempt, true);
                    let mut billing = tokio::select! {
                        biased;
                        _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                        billing = self.app.runtime.begin_child_billing(&self.parent.conversation_id,self.id,
                            &child_name,&context.document["stats"],&model) => billing,
                    };
                    if self.timeout.is_some_and(|deadline| Instant::now() >= deadline) { return Ok(End::Timeout); }
                    let response = tokio::select! {
                        biased;
                        _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                        response = model.complete_with_budget(&request, &schemas, &mut budget, |text|billing.observe_text(text)) => response,
                    };
                    match response {
                        Ok(completion) => break (completion,billing),
                        Err(error) if retry.should_retry(&error) => {
                            let next_attempt = Instant::now() + RETRY_DELAY;
                            let wake = self.timeout.map_or(next_attempt, |deadline| deadline.min(next_attempt));
                            tokio::select! {
                                biased;
                                _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                                _ = tokio::time::sleep_until(wake) => (),
                            }
                        }
                        Err(error) => return Err(error),
                    }
                }
            };
            if let Some(actor) = self.app.runtime.settle_child_billing(
                &billing,completion.usage.as_ref(),completion.responses_duration_ms,
            ).await? {
                context.usage(completion.usage.as_ref());
                context.document["stats"]["cost_usd"] = actor["cost_usd"].clone();
                context.document["stats"]["cost_ledger_requests"] = actor["requests"].clone();
            }
            web::annotate(&mut completion.message, context.messages());
            let calls = calls(context.messages(), &completion.message)?;
            let content = completion.message["content"].as_str().unwrap_or_default().to_owned();
            budget.observe_usage(completion.usage.as_ref());
            let compressed = super::compression::compact(context, &self.parent.root, data).map_err(io_error)?;
            context.append(completion.message);
            context.save(&self.app.registry).map_err(io_error)?;
            if let Some(round) = compressed {
                budget=RoundBudget::new(None);
                context.activity(&self.app.registry,json!({"subtype":"compression","round":round})).map_err(io_error)?;
            }
            if calls.is_empty() { return Ok(End::Completed(content)); }
            if !content.is_empty() {
                context.activity(&self.app.registry, json!({"subtype":"output","content":content,"is_final":false})).map_err(io_error)?;
            }
            for call in &calls {
                if stop.borrow().cancelled() { return Ok(End::Failed("子智能体已停止。".into())); }
                let Call { id, name, args } = call;
                context.activity(&self.app.registry, json!({"tool":name,"id":id,"arguments":args,"status":"running"})).map_err(io_error)?;
                let result = match name.as_str() {
                    read_file::NAME if allowed_args(args) => {
                        context.add_stat("files_read");
                        scope.read(&self.app.runtime, args.clone())
                    }
                    WRITE_NAME | EDIT_NAME if allowed_args(args) => {
                        context.add_stat(if name == WRITE_NAME { "write_files" } else { "edit_files" });
                        scope.write(id, name, args.clone(), context.messages())
                    }
                    media::READ => {
                        match scope.path(&args["path"]) {
                            Ok(path) => {
                                let root = self.parent.root.clone();
                                let data = data.to_path_buf();
                                let result = tokio::task::spawn_blocking(move || media::execute(
                                    &data, &root, &root, &Value::Null, media::READ, &path, images, videos,
                                )).await.map_err(|error| error.to_string())?;
                                Ok(result)
                            }
                            Err(error) => Err(error),
                        }
                    }
                    name if knowledge::is_read_only_tool(name) => {
                        let root = self.parent.root.clone();
                        let data = self.app.registry.data_dir().to_path_buf();
                        let name = name.to_owned();
                        let args = args.clone();
                        tokio::task::spawn_blocking(move || {
                            let knowledge = Knowledge::open(&root, &data).map_err(io_error)?;
                            knowledge.execute_read_only(&name, &args).ok_or("未知知识工具。".into())
                        }).await.map_err(|error| error.to_string())?
                    }
                    web::SAVE => {
                        if !self.web_enabled()? {
                            Ok(json!({"success":false,"code":"network_disabled","error":"当前对话已禁止联网。"}))
                        } else {
                            let save = tokio::select! {
                                biased;
                                _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                                result = web::prepare_save(data, args) => result,
                            };
                            match save {
                                Ok(save) => scope.write(id, WRITE_NAME, save.arguments.clone(), context.messages())
                                    .map(|written| save.result(written)),
                                Err(result) => Ok(result),
                            }
                        }
                    }
                    name if web::supports(name) => {
                        let data = self.app.registry.data_dir();
                        if !self.web_enabled()? {
                            Ok(json!({"success":false,"code":"network_disabled","error":"当前对话已禁止联网。"}))
                        } else {
                            tokio::select! {
                                biased;
                                _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                                result = web::execute(data, name, args) => Ok(result),
                            }
                        }
                    }
                    todos::CREATE | todos::UPDATE => {
                        let update = if name == todos::CREATE {
                            todos::create(args, false)
                        } else {
                            todos::update(context.document["todo_list"].clone(), args, false)
                        };
                        update.map(|(list, message)| {
                            let snapshot = todos::snapshot(list.clone());
                            context.document["todo_list"] = list;
                            json!({"success":true,"message":message,"todo_list":snapshot})
                        })
                    }
                    protocol::STATUS => self.app.status(self.parent, args),
                    protocol::ANSWER_OTHER => self.app.answer(self.parent, Some(self.id), args),
                    protocol::ASK_MASTER | protocol::ASK_OTHER => {
                        tokio::select! {
                            biased;
                            _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                            result = self.app.question(self.parent, self.id, name, args) => result,
                        }
                    }
                    name if super::processes::is_tool(name) => {
                        let cancellation = stop.clone();
                        tokio::select! {
                            biased;
                            _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                            result = self.app.process_tool(scope, self.id, name, args, cancellation) => result,
                        }
                    }
                    name if mcp_snapshot.allows(name) => {
                        tokio::select! {
                            biased;
                            _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                            result = mcp.execute(&mcp_snapshot, name, args.clone()) => Ok(result),
                        }
                    }
                    _ => Err("该工具或参数不属于子智能体的固定权限。".into()),
                }.unwrap_or_else(failed);
                context.tool_result(id, result.clone());
                context.save(&self.app.registry).map_err(io_error)?;
                context.activity(&self.app.registry, json!({"tool":name,"id":id,"arguments":args,"result":result,
                    "status":if result["success"] == false { "error" } else { "done" }})).map_err(io_error)?;
            }
        }
    }
}

fn progress(mut event: Value) -> Value {
    event["type"] = json!("progress");
    event["ts"] = json!((now() * 1000.0) as u64);
    event
}

fn allowed_args(args: &Value) -> bool {
    args.is_object() && ["request_full_access", "full_access_reason", "run_in_background"].iter().all(|key| args.get(key).is_none())
}

struct Call { id: String, name: String, args: Value }

fn calls(previous: &[Value], message: &Value) -> Result<Vec<Call>, String> {
    let mut ids = previous.iter().filter_map(|message| message["tool_calls"].as_array()).flatten()
        .filter_map(|call| call["id"].as_str()).collect::<HashSet<_>>();
    message["tool_calls"].as_array().into_iter().flatten().map(|call| {
        let id = call["id"].as_str().filter(|id| !id.is_empty()).ok_or("模型工具调用缺少 ID。")?;
        if !ids.insert(id) { return Err("模型返回了重复的工具调用 ID。".into()); }
        let name = call["function"]["name"].as_str().ok_or("模型工具调用缺少名称。")?.to_owned();
        let args = serde_json::from_str(call["function"]["arguments"].as_str().unwrap_or_default())
            .map_err(|_| "模型工具调用参数不是完整 JSON。")?;
        Ok(Call { id: id.into(), name, args })
    }).collect()
}

fn io_error(error: std::io::Error) -> String { format!("子智能体上下文写入失败：{error}") }
