use std::{collections::HashSet, time::Duration};

use serde_json::{Value, json};
use tokio::{sync::watch, time::Instant};

use crate::{goals::GoalFailure, model::RoundBudget, runtime::ModelConfig, tools::{read_file, write_file::{EDIT_NAME, WRITE_NAME}}};

use super::{SubAgents, execution::{Parent, Stop, now}, protocol::{self, failed}, registry::Registry, scope::ChildScope};

pub(super) struct Context {
    pub(super) document: Value,
}

impl Context {
    pub(super) fn new(record: &Value, prompt: &str) -> Self {
        Self { document: json!({
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
        Ok(Self { document })
    }

    fn messages(&self) -> &[Value] { self.document["messages"].as_array().expect("validated child context") }

    fn append(&mut self, message: Value) {
        self.document["messages"].as_array_mut().expect("validated child context").push(message);
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

    fn save(&self, registry: &Registry) -> std::io::Result<()> {
        registry.save_context(&self.document)
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
        self.append(json!({"role":"tool","tool_call_id":id,"content":result.to_string()}));
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
    async fn run(&self, context: &mut Context, stop: &mut watch::Receiver<Stop>) -> Result<End, String> {
        let mcp = self.app.mcp_for_child(self.parent, self.id, self.scope)?;
        let base_schemas = protocol::child_schemas();
        let mut budget = RoundBudget::new(context.document["stats"]["current_context_tokens"].as_u64());
        loop {
            self.app.consume_guidance(self.parent, self.id, context)?;
            self.scope.check()?;
            if self.timeout.is_some_and(|deadline| Instant::now() >= deadline) { return Ok(End::Timeout); }
            if stop.borrow().cancelled() { return Ok(End::Failed("子智能体已停止。".into())); }
            let mcp_snapshot = tokio::select! {
                biased;
                _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                snapshot = mcp.prepare_tools() => snapshot,
            };
            let mut schemas = base_schemas.clone();
            schemas.extend_from_slice(mcp_snapshot.schemas());
            let request = crate::mcp::model_messages(context.messages(), self.model.supports_images());
            let completion = {
                let mut attempt = 0;
                loop {
                    if self.timeout.is_some_and(|deadline| Instant::now() >= deadline) { return Ok(End::Timeout); }
                    if stop.borrow().cancelled() { return Ok(End::Failed("子智能体已停止。".into())); }
                    attempt += 1;
                    let response = tokio::select! {
                        biased;
                        _ = stop.changed() => return Ok(End::Failed("子智能体已停止。".into())),
                        response = self.model.complete_with_budget(&request, &schemas, &mut budget, |_| {}) => response,
                    };
                    match response {
                        Ok(completion) => break completion,
                        Err(error) if attempt < 5 && GoalFailure::from_message(&error).reason() == "retryable" => {
                            let retry = Instant::now() + Duration::from_secs(10);
                            let wake = self.timeout.map_or(retry, |deadline| deadline.min(retry));
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
            let calls = calls(context.messages(), &completion.message)?;
            let content = completion.message["content"].as_str().unwrap_or_default().to_owned();
            budget.observe_usage(completion.usage.as_ref());
            context.usage(completion.usage.as_ref());
            context.append(completion.message);
            context.save(&self.app.registry).map_err(io_error)?;
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
                        self.scope.read(args.clone())
                    }
                    WRITE_NAME | EDIT_NAME if allowed_args(args) => {
                        context.add_stat(if name == WRITE_NAME { "write_files" } else { "edit_files" });
                        self.scope.write(id, name, args.clone(), context.messages())
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
