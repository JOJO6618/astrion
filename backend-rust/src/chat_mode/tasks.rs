use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

use serde_json::{Map, Value, json};
use tokio::sync::oneshot;
use uuid::Uuid;

use super::{
    ChatError, ChatMode,
    definitions::{definitions, is_chat_tool},
    tools,
    tree::{self, Selection},
};
use crate::runtime::{ChatTaskStart, ModelConfig};

const MAX_MODEL_ROUNDS: usize = 8;
const EFFORTS: [&str; 5] = ["low", "medium", "high", "xhigh", "max"];

#[derive(Clone, Default)]
pub(super) struct ChatTasks {
    admission: Arc<Mutex<()>>,
    operations: Arc<Mutex<HashMap<String, String>>>,
}

pub(super) fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

struct Plan {
    conversation_id: String,
    created: bool,
    model_key: Option<String>,
    thinking: bool,
    reasoning_effort: Option<String>,
    chatgpt_fast_mode: bool,
}

struct BeginInfo {
    turn_id: String,
    title: String,
    revision: u64,
    title_pending: bool,
}

impl ChatMode {
    /// Accepts a durable Chat send before starting the model. Returns the public
    /// task payload; the caller can poll `/api/chat/tasks/<id>`.
    pub(super) fn create_task(&self, payload: &Value) -> Result<Value, ChatError> {
        let operation = validate_payload(payload)?;
        let admission = self.tasks.admission.lock().map_err(ChatError::storage)?;
        let timestamp_now = now();
        if let Some(conversation) = operation.conversation_hint.as_deref()
            && let Some(task_id) = self.tasks.operation(conversation, &operation.id)
        {
            if let Ok(task) = self.runtime.poll_task("", &task_id, 0) {
                return Ok(public_task(task));
            }
            self.tasks.remove_operation(conversation, &operation.id);
        }

        let effective = super::settings::apply_new_conversation_defaults(self, payload)?;
        let payload = &effective;
        let mut media = Vec::new();
        for (field, kind) in [("images", "image"), ("videos", "video")] {
            if let Some(values) = payload.get(field).filter(|value| !value.is_null()) {
                media.extend(super::media::resolve_typed_refs(&self.root, values, Some(kind))?);
            }
        }
        let plan = self.preflight(payload, &operation, timestamp_now)?;
        let model = super::model::resolve(
            &self.model,
            plan.model_key.as_deref(),
            plan.thinking,
            plan.reasoning_effort.as_deref(),
            &operation.id,
        )?;
        if media.iter().any(|entry| entry["kind"] == "image") && !model.supports_images()
            || media.iter().any(|entry| entry["kind"] == "video") && !model.supports_videos()
        {
            return Err(ChatError::bad_request("The selected Chat model does not support this media type"));
        }
        let handle = tokio::runtime::Handle::try_current()
            .map_err(|_| ChatError::unavailable("No Tokio runtime is available"))?;

        let (task_id, cancel) = self.runtime.register_chat_task(ChatTaskStart {
            conversation_id: plan.conversation_id.clone(),
            message: payload["message"].as_str().unwrap_or_default().to_owned(),
            model_key: Some(model.key().to_owned()),
            message_source: None,
            created_at: timestamp_now,
        })?;

        let begin = match self.begin(payload, &operation, &plan, timestamp_now, media) {
            Ok(begin) => begin,
            Err(error) => {
                let _ = self.runtime.finish_chat_task(
                    &task_id,
                    "failed",
                    Some(error.message.as_str()),
                );
                return Err(error);
            }
        };

        self.tasks
            .set_operation(&plan.conversation_id, &operation.id, &task_id);
        drop(admission);
        let mode = self.clone();
        let conversation_id = plan.conversation_id.clone();
        let turn_id = begin.turn_id.clone();
        let title = begin.title.clone();
        let op_key = operation.id.clone();
        let message = payload["message"].as_str().unwrap_or_default().to_owned();
        let model = self.runtime.with_conversation_session(model, "", &conversation_id, plan.chatgpt_fast_mode);
        if begin.title_pending {
            self.runtime.spawn_automatic_title("", &conversation_id, &title, &message);
        }
        let worker_task_id = task_id.clone();
        handle.spawn(async move {
            mode.run_worker(
                worker_task_id,
                conversation_id,
                turn_id,
                op_key,
                model,
                message,
                cancel,
            )
            .await;
        });

        let mut task = self
            .runtime
            .poll_task("", &task_id, 0)
            .map(public_task)
            .unwrap_or_else(|_| {
                json!({
                    "task_id": task_id,
                    "workspace_id": Value::Null,
                    "conversation_kind": "chat",
                    "workspace_label": "Chat",
                    "status": "running",
                    "created_at": timestamp_now,
                    "updated_at": timestamp_now,
                    "message": payload["message"],
                    "conversation_id": plan.conversation_id,
                    "error": Value::Null,
                    "message_source": Value::Null,
                    "goal_mode": false,
                    "goal_progress": Value::Null,
                    "task_type": "chat",
                })
            });
        task["conversation_title"] = json!(title);
        task["conversation_revision"] = json!(begin.revision);
        Ok(task)
    }

    fn preflight(
        &self,
        payload: &Value,
        operation: &Operation,
        timestamp_now: f64,
    ) -> Result<Plan, ChatError> {
        let requested = payload["conversation_id"]
            .as_str()
            .map(str::trim)
            .filter(|id| !id.is_empty());
        if let Some(id) = requested
            && !super::store::valid_conversation_id(id)
        {
            return Err(ChatError::bad_request("Invalid Chat conversation ID"));
        }
        if let Some(task_id) = requested.and_then(|id| self.tasks.operation(id, &operation.id)) {
            if self.runtime.poll_task("", &task_id, 0).is_ok() {
                return Err(ChatError::conflict(format!(
                    "This Chat send is already running as task {task_id}"
                )));
            }
            if let Some(id) = requested {
                self.tasks.remove_operation(id, &operation.id);
            }
        }
        self.storage(|paths| {
            if let Some(id) = requested {
                let (raw, _) = paths.read_header(id)?;
                if raw["metadata"]["chat"]["commands"]
                    .get(&operation.id)
                    .is_some()
                {
                    return Err(ChatError::conflict(
                        "This Chat send was already accepted; reload the conversation",
                    ));
                }
                let model_key = payload["model_key"]
                    .as_str()
                    .map(str::to_owned)
                    .or_else(|| raw["metadata"]["model_key"].as_str().map(str::to_owned));
                let thinking = payload["thinking_mode"]
                    .as_bool()
                    .unwrap_or(raw["metadata"]["thinking_mode"] == true);
                let settings = payload["settings"].as_object();
                let reasoning_effort = settings
                    .and_then(|settings| settings.get("reasoning_effort"))
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|effort| !effort.is_empty())
                    .map(str::to_owned)
                    .or_else(|| {
                        raw["metadata"]["reasoning_effort"]
                            .as_str()
                            .map(str::to_owned)
                    });
                let chatgpt_fast_mode = settings
                    .and_then(|settings| settings.get("chatgpt_fast_mode"))
                    .and_then(Value::as_bool)
                    .unwrap_or(raw["metadata"]["chatgpt_fast_mode"] == true);
                return Ok(Plan {
                    conversation_id: id.to_owned(),
                    created: false,
                    model_key,
                    thinking,
                    reasoning_effort,
                    chatgpt_fast_mode,
                });
            }
            let id = paths.next_conversation_id(timestamp_now)?;
            let settings = payload["settings"].as_object();
            Ok(Plan {
                conversation_id: id,
                created: true,
                model_key: payload["model_key"].as_str().map(str::to_owned),
                thinking: payload["thinking_mode"].as_bool().unwrap_or(false),
                reasoning_effort: settings
                    .and_then(|settings| settings.get("reasoning_effort"))
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|effort| !effort.is_empty())
                    .map(str::to_owned),
                chatgpt_fast_mode: settings
                    .and_then(|settings| settings.get("chatgpt_fast_mode"))
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            })
        })
    }

    fn begin(
        &self,
        payload: &Value,
        operation: &Operation,
        plan: &Plan,
        timestamp_now: f64,
        media: Vec<Value>,
    ) -> Result<BeginInfo, ChatError> {
        let configured = configured(payload);
        let parent_id = payload["parent_id"].as_str().filter(|id| !id.is_empty());
        let regenerate = payload["regenerate_turn_id"]
            .as_str()
            .filter(|id| !id.is_empty());
        let edit_message = payload["edit_message_id"]
            .as_str()
            .filter(|id| !id.is_empty());
        if regenerate.is_some() && edit_message.is_some() {
            return Err(ChatError::bad_request(
                "Regenerate and edit-and-send are mutually exclusive",
            ));
        }
        let presentation = payload["presentation_id"].as_str();
        let message = payload["message"].as_str().unwrap_or_default();
        let result = self.storage(|paths| {
            let mut recovered_parent = None;
            let initial = if plan.created {
                Some(paths.initial_document(&plan.conversation_id, timestamp_now, payload)?)
            } else {
                let (header, count) = paths.read_header(&plan.conversation_id)?;
                if let Some(active) = tree::active_turn(&header) {
                    // If the client sent the old tip before recovery, continue
                    // from the recovered tip, which now includes the tool results.
                    let previous_tip = header["metadata"]["chat"]["latest_leaf_id"].as_str();
                    tree::recover(paths, &plan.conversation_id, active, "failed", timestamp_now)?;
                    if parent_id.is_some() && parent_id == previous_tip {
                        recovered_parent = paths.read_header(&plan.conversation_id)?.0["metadata"]
                            ["chat"]["latest_leaf_id"].as_str().map(str::to_owned);
                    }
                }
                if count != 0 && parent_id.is_none() && regenerate.is_none() && edit_message.is_none() {
                    return Err(ChatError::bad_request("Continuing a Chat requires an explicit parent message"));
                }
                None
            };
            let parent_id = recovered_parent.as_deref().or(parent_id);
            let selection = match (regenerate, edit_message) {
                (Some(turn_id), None) => Selection::Regenerate { turn_id },
                (None, Some(message_id)) => Selection::EditAndSend {
                    message_id,
                    content: message,
                },
                (None, None) => Selection::Send {
                    parent_id,
                    message: json!({
                        "role": "user",
                        "content": message,
                        "message_id": format!("msg_{}", Uuid::new_v4().simple()),
                        "media_refs": media,
                        "metadata": {"visibility":"chat","starts_work":true},
                    }),
                },
                (Some(_), Some(_)) => unreachable!("validated mutually exclusive selectors"),
            };
            let receipt = tree::begin(
                paths, &plan.conversation_id, &operation.id, selection, initial, None, presentation,
                &configured, timestamp_now,
            )?;
            let (header, count) = paths.read_header(&plan.conversation_id)?;
            let title_pending = count == 1 && parent_id.is_none() && regenerate.is_none() && edit_message.is_none()
                && header["metadata"]["chat"]["turns"].as_array().is_some_and(|turns| {
                    turns.len() == 1 && turns[0]["id"] == receipt["value"]["id"]
                })
                && crate::runtime::automatic_title::eligible(&header).is_some();
            Ok((receipt, header["title"].as_str().unwrap_or("新对话").to_owned(), title_pending))
        })?;
        let (receipt, title, title_pending) = result;
        let turn = receipt["value"]
            .as_object()
            .ok_or_else(|| ChatError::storage("Chat begin receipt is incomplete"))?;
        Ok(BeginInfo {
            turn_id: turn["id"]
                .as_str()
                .ok_or_else(|| ChatError::storage("Chat begin receipt is incomplete"))?
                .to_owned(),
            title,
            revision: receipt["revision"].as_u64().unwrap_or_default(),
            title_pending,
        })
    }

    async fn run_worker(
        self,
        task_id: String,
        conversation_id: String,
        turn_id: String,
        operation: String,
        model: ModelConfig,
        message: String,
        mut cancel: oneshot::Receiver<()>,
    ) {
        let pending = Arc::new(Mutex::new(String::new()));
        let thinking = crate::runtime::thinking_auxiliary::ThinkingWork::for_task(&self.runtime, &task_id, &model);
        let outcome = tokio::select! {
            biased;
            _ = &mut cancel => Outcome::stopped(),
            result = async {
                let result = self.model_loop(
                &task_id,
                &conversation_id,
                &turn_id,
                &model,
                message,
                pending.clone(),
                thinking.as_ref(),
                ).await;
                if result.status == "succeeded" && let Some(thinking) = &thinking { thinking.finish().await; }
                result
            } => result,
        };

        if outcome.status == "stopped" {
            let partial = pending.lock().map(|text| text.clone()).unwrap_or_default();
            if !partial.trim().is_empty() {
                let _ = self.append_partial(&conversation_id, &turn_id, &partial);
            }
        }
        let tree_status = match outcome.status {
            "succeeded" => "complete",
            status => status,
        };
        let durable = self
            .storage(|paths| {
                if tree_status == "complete" {
                    tree::finish(paths, &conversation_id, &turn_id, tree_status, now())
                } else {
                    tree::recover(paths, &conversation_id, &turn_id, tree_status, now())
                }
            })
            .map(|_| ());
        // A generated answer that could not release its durable turn must not be
        // reported as a successful task; the next send would otherwise hit the
        // active-turn gate forever.
        let (status, error) = match durable {
            Ok(()) => (outcome.status, outcome.error.clone()),
            Err(error) => (
                "failed",
                Some(format!("Chat persistence failed: {}", error.message)),
            ),
        };
        self.tasks.remove_operation(&conversation_id, &operation);
        let _ = self.runtime.finish_chat_task(&task_id, status, error.as_deref());
    }

    async fn model_loop(
        &self,
        task_id: &str,
        conversation_id: &str,
        turn_id: &str,
        model: &ModelConfig,
        _message: String,
        pending: Arc<Mutex<String>>,
        work: Option<&crate::runtime::thinking_auxiliary::ThinkingWork>,
    ) -> Outcome {
        use crate::model::{RoundBudget, StreamDelta};

        if let Err(error) = self
            .storage(|paths| tree::consume_network_notices(paths, conversation_id, turn_id, now()))
        {
            return Outcome::failed(error.message);
        }
        let (mut messages, _network) = match self.model_messages(conversation_id, turn_id) {
            Ok(value) => value,
            Err(error) => return Outcome::failed(error.message),
        };
        let tools = definitions();
        let mut used_ids: std::collections::HashSet<String> = messages
            .iter()
            .filter_map(|message| message["tool_calls"].as_array())
            .flatten()
            .filter_map(|call| call["id"].as_str().map(str::to_owned))
            .collect();

        for round in 0..MAX_MODEL_ROUNDS {
            if round > 0 {
                match self.storage(|paths| tree::consume_network_notices(paths, conversation_id, turn_id, now())) {
                    // Apply UI switches only between complete tool batches.
                    // The normal case keeps the current wire history in memory.
                    Ok(true) => match self.model_messages(conversation_id, turn_id) {
                        Ok((updated, _)) => messages = updated,
                        Err(error) => return Outcome::failed(error.message),
                    },
                    Ok(false) => (),
                    Err(error) => return Outcome::failed(error.message),
                }
            }
            let response_message_id = format!("msg_{}", Uuid::new_v4().simple());
            if !self.emit(task_id, "api_request_start", json!({
                "attempt":1,"max_attempts":1,"response_message_id":response_message_id,
            })) {
                return Outcome::stopped();
            }
            let pending_text = pending.clone();
            if let Ok(mut text) = pending_text.lock() {
                text.clear();
            }
            let mut started = false;
            let mut tool_stream = crate::runtime::tool_call_stream::ToolCallStream::default();
            let mut thinking = crate::runtime::thinking::ThinkingStream::new(&self.runtime, task_id, &response_message_id, work);
            let mut completion = match model
                .complete_with_updates(&messages, &tools, &mut RoundBudget::default(), |update| {
                    let delta = match update {
                        StreamDelta::Thinking(delta) => { let _ = thinking.push(delta); return; }
                        StreamDelta::NativeSummary(summary) => { thinking.native_summary(summary); return; }
                        StreamDelta::Usage(_) => return,
                        StreamDelta::ToolCall(update) => {
                            let events = tool_stream.push(update);
                            let _ = thinking.end();
                            for event in &events {
                                let _ = crate::runtime::tool_call_stream::publish(event, |kind, data| {
                                    if self.emit(task_id, kind, data) { Ok(()) } else { Err("Chat task stopped".into()) }
                                });
                            }
                            return;
                        }
                        StreamDelta::Text(delta) => delta,
                    };
                    let _ = thinking.end();
                    if let Ok(mut text) = pending_text.lock() {
                        text.push_str(delta);
                    }
                    if !started {
                        started = true;
                        let _ = self.emit(task_id, "text_start", json!({"response_message_id":response_message_id}));
                    }
                    let _ = self.emit(task_id, "text_chunk", json!({"content":delta}));
                })
                .await
            {
                Ok(completion) => completion,
                Err(error) => {
                    let partial = pending.lock().map(|text| text.clone()).unwrap_or_default();
                    if !partial.trim().is_empty() {
                        let _ = self.append_partial(conversation_id, turn_id, &partial);
                    }
                    return Outcome::failed(error);
                }
            };
            if thinking.end().is_err() { return Outcome::stopped(); }
            crate::tools::web::annotate(&mut completion.message, &messages);
            if started {
                let content = completion.message["content"].as_str().unwrap_or_default();
                let mut event = json!({"full_content":content});
                if let Some(citations) = completion.message["metadata"].get("citations") {
                    event["citations"] = citations.clone();
                }
                if !self.emit(task_id, "text_end", event) {
                    return Outcome::stopped();
                }
            }
            let calls = validated_calls(&completion.message, &mut used_ids);
            let mut stored = assistant_message(&completion.message, model.key(), &response_message_id);
            if calls.is_err() {
                // An invalid provider batch is never executed or replayed.
                // Preserve the assistant prose and usage without storing calls
                // that would make subsequent branch projection fail.
                stored.as_object_mut().expect("assistant message object").shift_remove("tool_calls");
            }
            let stats = completion.usage.as_ref().and_then(usage_statistics);
            if let Err(error) = self.store_message(
                conversation_id, turn_id, stored, stats.as_ref(),
            ) {
                return Outcome::failed(error.message);
            }
            thinking.keep();
            if let Ok(mut text) = pending.lock() {
                text.clear();
            }
            if let Some(stats) = stats {
                let _ = self.emit(task_id, "token_update", stats);
            }

            let calls = match calls {
                Ok(calls) => calls,
                Err(error) => return Outcome::failed(error),
            };
            if calls.is_empty() {
                return Outcome::succeeded();
            }
            if round + 1 == MAX_MODEL_ROUNDS {
                return Outcome::failed("Chat reached the model round limit".into());
            }
            messages.push(assistant_model_message(&completion.message));
            for call in &calls {
                if !self.emit(
                    task_id,
                    "tool_start",
                    json!({"id":call.id,"preparing_id":call.id,"name":call.name,"arguments":call.arguments}),
                ) {
                    return Outcome::stopped();
                }
                let result = tools::execute(self, &call.name, &call.arguments, conversation_id)
                    .await
                    .map(|value| value.to_string())
                    .unwrap_or_else(|error| {
                        json!({"success":false,"error":error.message}).to_string()
                    });
                let parsed: Value =
                    serde_json::from_str(&result).unwrap_or_else(|_| json!(result));
                let status = if parsed["success"] == false {
                    "failed"
                } else {
                    "completed"
                };
                let mut message = json!({
                    "message_id": format!("msg_{}", Uuid::new_v4().simple()),
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": result,
                });
                if let Err(error) = self.store_message(conversation_id, turn_id, message.clone(), None) {
                    return Outcome::failed(error.message);
                }
                // Match what a subsequent task reads from durable history;
                // message IDs, tool names and UI metadata are not wire fields.
                if let Some(fields) = message.as_object_mut() {
                    fields.shift_remove("message_id");
                    fields.shift_remove("name");
                }
                messages.push(message);
                if !self.emit(
                    task_id,
                    "update_action",
                    json!({"id":call.id,"preparing_id":call.id,"status":status,"result":parsed}),
                ) {
                    return Outcome::stopped();
                }
            }
        }
        Outcome::failed("Chat model loop ended unexpectedly".into())
    }

    fn model_messages(
        &self,
        conversation_id: &str,
        turn_id: &str,
    ) -> Result<(Vec<Value>, bool), ChatError> {
        let document = self.storage(|paths| paths.read_document(conversation_id))?;
        let raw = document.raw();
        let mut messages = document.chat_turn_history_with_media(turn_id, &self.root)?;
        if messages.first().is_none_or(|message| message["role"] != "system") {
            messages.insert(0, json!({
                "role": "system",
                "content": self.system_prompt(raw)?,
            }));
        }
        Ok((messages, raw["metadata"]["network_enabled"] == true))
    }

    pub(super) fn system_prompt(&self, raw: &Value) -> Result<String, ChatError> {
        let mut parts = Vec::new();
        for name in ["chat_system.txt", "show_ui.txt", "show_html.txt"] {
            match std::fs::read_to_string(self.resources.join("prompts").join(name)) {
                Ok(text) if !text.trim().is_empty() => parts.push(text),
                Ok(_) | Err(_) => {}
            }
        }
        if parts.is_empty() {
            parts.push("你是 Astrion 的 Chat 助手。使用用户的语言，保持准确和诚实。".into());
        }
        let model = raw["metadata"]["model_key"]
            .as_str()
            .unwrap_or("当前模型");
        parts.push(format!("当前模型：{model}"));
        let personalization = super::settings::load_preferences(self)?;
        if let Some(profile) = crate::runtime::personalization_prompt(&personalization) {
            parts.push(profile);
        }
        if let Some(instructions) = personalization["chat_instructions"]
            .as_str()
            .filter(|value| !value.trim().is_empty())
        {
            parts.push(format!("## Chat 专属个人指令\n{instructions}"));
        }
        let (global, topics) = super::memory::prompt_sections(&self.root)?;
        if !global.is_empty() {
            let entries = global
                .iter()
                .enumerate()
                .map(|(index, entry)| format!("{}. {entry}", index + 1))
                .collect::<Vec<_>>()
                .join("\n");
            parts.push(format!("## Chat 全局记忆\n{entries}"));
        }
        if !topics.is_empty() {
            let index = topics
                .iter()
                .map(|(name, description)| format!("{name}：{description}"))
                .collect::<Vec<_>>()
                .join("\n");
            parts.push(format!("## Chat 专题记忆索引\n{index}"));
        }
        let network = if raw["metadata"]["network_enabled"] == true {
            "开启"
        } else {
            "关闭"
        };
        let now = jiff::Zoned::now();
        parts.push(format!(
            "当前时间：{}；时区：UTC{}；联网：{network}。",
            now.strftime("%Y-%m-%dT%H:%M%:z"),
            now.strftime("%:z")
        ));
        Ok(parts.join("\n\n"))
    }

    fn store_message(
        &self,
        conversation_id: &str,
        turn_id: &str,
        message: Value,
        statistics: Option<&Value>,
    ) -> Result<(), ChatError> {
        self.storage(|paths| {
            tree::append(
                paths,
                conversation_id,
                turn_id,
                message,
                now(),
                statistics,
            )
            .map(|_| ())
        })
    }

    fn append_partial(
        &self,
        conversation_id: &str,
        turn_id: &str,
        partial: &str,
    ) -> Result<(), ChatError> {
        let message = json!({
            "message_id": format!("msg_{}", Uuid::new_v4().simple()),
            "role": "assistant",
            "content": partial,
            "metadata": {"stopped": true},
        });
        self.storage(|paths| {
            tree::append(paths, conversation_id, turn_id, message, now(), None).map(|_| ())
        })
    }

    fn emit(&self, task_id: &str, kind: &str, data: Value) -> bool {
        self.runtime.emit_chat_event(task_id, kind, data).is_ok()
    }
}

struct Operation {
    id: String,
    conversation_hint: Option<String>,
}

fn validate_payload(payload: &Value) -> Result<Operation, ChatError> {
    if !payload.is_object() {
        return Err(ChatError::bad_request("Expected a JSON object"));
    }
    let operation = tree::operation_id(payload.get("operation_id"))?;
    let message = payload["message"].as_str().unwrap_or_default();
    if message.chars().count() > 1_000_000 {
        return Err(ChatError::bad_request("Invalid Chat message"));
    }
    if payload["files"].as_array().is_some_and(|files| !files.is_empty()) {
        return Err(ChatError::bad_request("Chat does not accept files"));
    }
    let has_media = ["images", "videos"].into_iter().any(|field| {
        payload[field].as_array().is_some_and(|items| !items.is_empty())
    });
    let regenerate = payload["regenerate_turn_id"]
        .as_str()
        .filter(|value| !value.is_empty());
    if message.trim().is_empty() && !has_media && regenerate.is_none() && payload["edit_message_id"].is_null() {
        return Err(ChatError::bad_request("Enter a message or select media"));
    }
    if let Some(thinking) = payload.get("thinking_mode")
        && !thinking.is_null()
        && !thinking.is_boolean()
    {
        return Err(ChatError::bad_request("Thinking mode must be a boolean"));
    }
    if let Some(settings) = payload.get("settings")
        && !settings.is_null()
    {
        let settings = settings
            .as_object()
            .ok_or_else(|| ChatError::bad_request("Invalid Chat initial settings"))?;
        if settings
            .keys()
            .any(|key| !matches!(key.as_str(), "reasoning_effort" | "network_enabled" | "chatgpt_fast_mode"))
        {
            return Err(ChatError::bad_request("Invalid Chat initial settings"));
        }
        if let Some(effort) = settings.get("reasoning_effort")
            && !effort.is_null()
            && effort
                .as_str()
                .is_none_or(|value| !EFFORTS.contains(&value))
        {
            return Err(ChatError::bad_request("Invalid reasoning effort"));
        }
        if settings
            .get("network_enabled")
            .is_some_and(|value| !value.is_boolean())
        {
            return Err(ChatError::bad_request("Network state must be a boolean"));
        }
        if settings
            .get("chatgpt_fast_mode")
            .is_some_and(|value| !value.is_boolean())
        {
            return Err(ChatError::bad_request("chatgpt_fast_mode must be a boolean"));
        }
    }
    if let Some(model_key) = payload.get("model_key")
        && !model_key.is_null()
        && model_key
            .as_str()
            .is_none_or(|value| value.trim().is_empty())
    {
        return Err(ChatError::bad_request("Invalid model identity"));
    }
    let conversation = payload["conversation_id"]
        .as_str()
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    Ok(Operation {
        id: operation,
        conversation_hint: conversation,
    })
}

fn configured(payload: &Value) -> Value {
    let mut settings = Map::new();
    if let Some(model) = payload["model_key"].as_str() {
        settings.insert("model_key".into(), json!(model));
    }
    if let Some(thinking) = payload["thinking_mode"].as_bool() {
        settings.insert("thinking_mode".into(), json!(thinking));
    }
    if let Some(value) = payload["settings"].as_object() {
        for key in ["reasoning_effort", "chatgpt_fast_mode"] {
            if let Some(value) = value.get(key).filter(|value| !value.is_null()) {
                settings.insert(key.into(), value.clone());
            }
        }
    }
    Value::Object(settings)
}

#[derive(Debug)]
pub(super) struct Call {
    id: String,
    name: String,
    arguments: Value,
}

pub(super) fn validated_calls(
    message: &Value,
    used: &mut std::collections::HashSet<String>,
) -> Result<Vec<Call>, String> {
    let calls = match &message["tool_calls"] {
        Value::Null => return Ok(Vec::new()),
        Value::Array(calls) => calls,
        _ => return Err("Invalid Chat tool calls array".into()),
    };
    calls
        .iter()
        .map(|call| {
            let id = call["id"]
                .as_str()
                .filter(|id| !id.trim().is_empty())
                .ok_or("Missing Chat tool call id")?;
            if call["type"] != "function" {
                return Err("Unsupported Chat tool call type".into());
            }
            if !used.insert(id.into()) {
                return Err(format!("Duplicate Chat tool call ID: {id}"));
            }
            let name = call["function"]["name"].as_str().ok_or("Missing Chat tool name")?;
            if !is_chat_tool(name) {
                return Err(format!("Tool unavailable in Chat: {name}"));
            }
            let arguments = call["function"]["arguments"]
                .as_str()
                .and_then(|arguments| serde_json::from_str::<Value>(arguments).ok())
                .filter(Value::is_object)
                .ok_or_else(|| format!("Invalid {name} arguments: expected a JSON object"))?;
            Ok(Call {
                id: id.into(),
                name: name.into(),
                arguments,
            })
        })
        .collect()
}

fn assistant_message(message: &Value, model_key: &str, response_message_id: &str) -> Value {
    let mut stored = json!({
        "message_id": response_message_id,
        "role": "assistant",
        "content": message["content"].as_str().unwrap_or_default(),
        "metadata": {"model_key": model_key},
    });
    if let Some(citations) = message["metadata"].get("citations") {
        stored["metadata"]["citations"] = citations.clone();
    }
    if let Some(calls) = message["tool_calls"].as_array()
        && !calls.is_empty()
    {
        stored["tool_calls"] = json!(calls);
    }
    if let Some(reasoning) = message["reasoning_content"].as_str()
        && !reasoning.is_empty()
    {
        stored["reasoning_content"] = json!(reasoning);
    }
    for field in ["_responses_connection", "responses_reasoning_items"] {
        if let Some(value) = message.get(field) { stored["metadata"][field] = value.clone(); }
    }
    stored
}

fn assistant_model_message(message: &Value) -> Value {
    let mut projected = json!({
        "role":"assistant",
        "content":message["content"],
        "tool_calls":message["tool_calls"],
    });
    // Responses' transport filters these opaque items by its configured
    // connection; Chat Completions does not receive the Responses fields.
    for field in ["reasoning_content", "_responses_connection", "responses_reasoning_items"] {
        if let Some(value) = message.get(field) {
            projected[field] = value.clone();
        }
    }
    projected
}

fn usage_statistics(usage: &Value) -> Option<Value> {
    let prompt = usage["prompt_tokens"].as_u64();
    let completion = usage["completion_tokens"].as_u64();
    let cached = usage["prompt_tokens_details"]["cached_tokens"]
        .as_u64()
        .or_else(|| usage["cached_input_tokens"].as_u64());
    let exempt = usage["cache_exempt_input_tokens"].as_u64();
    let context = usage["current_context_tokens"].as_u64().or(prompt);
    if prompt.is_none() && completion.is_none() && cached.is_none() && context.is_none() && exempt.is_none() {
        return None;
    }
    let mut stats = Map::new();
    let mut set = |key: &str, value: Option<u64>| {
        if let Some(value) = value {
            stats.insert(key.into(), json!(value));
        }
    };
    set("total_input_tokens", prompt);
    set("total_output_tokens", completion);
    set("total_cached_input_tokens", cached);
    set("cache_exempt_input_tokens", exempt);
    set("current_context_tokens", context);
    Some(Value::Object(stats))
}

struct Outcome {
    status: &'static str,
    error: Option<String>,
}

impl Outcome {
    fn succeeded() -> Self {
        Self {
            status: "succeeded",
            error: None,
        }
    }
    fn stopped() -> Self {
        Self {
            status: "stopped",
            error: None,
        }
    }
    fn failed(error: String) -> Self {
        Self {
            status: "failed",
            error: Some(error),
        }
    }
}

pub(super) fn public_task(mut value: Value) -> Value {
    let mut data = value["data"].take();
    if data.is_null() {
        return value;
    }
    data["workspace_id"] = Value::Null;
    data["workspace_label"] = json!("Chat");
    data["conversation_kind"] = json!("chat");
    data["task_type"] = json!("chat");
    data["username"] = json!("host");
    data
}

fn key(conversation: &str, operation: &str) -> String {
    format!("{conversation}\u{1f}{operation}")
}

impl ChatTasks {
    fn operation(&self, conversation: &str, operation: &str) -> Option<String> {
        self.operations
            .lock()
            .ok()?
            .get(&key(conversation, operation))
            .cloned()
    }

    fn set_operation(&self, conversation: &str, operation: &str, task_id: &str) {
        if let Ok(mut operations) = self.operations.lock() {
            operations.insert(key(conversation, operation), task_id.to_owned());
        }
    }

    fn remove_operation(&self, conversation: &str, operation: &str) {
        if let Ok(mut operations) = self.operations.lock() {
            operations.remove(&key(conversation, operation));
        }
    }
}
