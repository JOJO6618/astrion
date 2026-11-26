use super::{
    LegacyDocument, ModelConfig, ReleaseTask, Runtime, RuntimeError, Status, Task, TaskCompletion,
    TaskStart, iso_time, legacy_error, now, system_prompt, verify_workspace_binding,
};
use crate::{knowledge::storage, workspace_catalog::RegisteredWorkspace};
use serde_json::{Value, json};
use std::{path::Path, sync::Arc};
use tokio::sync::oneshot;
use uuid::Uuid;

const PROMPT: &str = include_str!("../../../prompts/deep_compression_summary.txt");

pub(super) fn projection(task: Option<&Task>, id: &str, metadata: &Value) -> Value {
    let task = task.filter(|task| task.status == Status::Running
        && (task.kind == "compression" || task.compression_stage.is_some() || task.compression_pending));
    let mode = task.map(|task| match (task.kind, task.auto_compression) {
        ("compression", _) => "manual",
        (_, true) => "auto",
        _ => "requested",
    });
    let stage = task.map(|task| task.compression_stage.unwrap_or(if task.kind == "compression" { "queued" } else { "waiting_for_tools" }));
    json!({"conversation_id":id,"in_progress":task.is_some(),"mode":mode,"stage":stage,
        "job_id":task.map(|task| &task.start.id),"error":metadata["compression_error"],
        "count":metadata["compression_count"].as_u64().unwrap_or(0)})
}

impl Runtime {
    pub fn compression_status(
        &self,
        ws: &RegisteredWorkspace,
        id: &str,
    ) -> Result<Value, RuntimeError> {
        let store = self.store()?;
        verify_workspace_binding(&store, &ws.id, Some(&ws.path))?;
        let (header, _) = store.header(&ws.id, id)?;
        let task = store.tasks.values().find(|t| {
            t.workspace_id == ws.id
                && t.conversation_id == id
                && t.status == Status::Running
        });
        let data = projection(task, id, &header["metadata"]);
        Ok(
            json!({"success":true,"data":{"conversation_id":id,"compression_in_progress":data["in_progress"],
            "compression_mode":data["mode"],"compression_job_id":data["job_id"],"compression_stage":data["stage"],
            "compression_error":data["error"],"compression_count":data["count"]}}),
        )
    }

    pub fn cancel_compression(
        &self,
        ws: &RegisteredWorkspace,
        id: &str,
    ) -> Result<Value, RuntimeError> {
        let status = self.compression_status(ws, id)?;
        let task = status["data"]["compression_job_id"]
            .as_str()
            .ok_or_else(RuntimeError::task_not_found)?;
        self.cancel_task(&ws.id, task)?;
        Ok(json!({"success":true,"conversation_id":id}))
    }

    pub fn request_compression(
        &self,
        ws: &RegisteredWorkspace,
        id: &str,
        model: ModelConfig,
        mut settings: super::TaskApprovalSettings,
    ) -> Result<Value, RuntimeError> {
        let mut store = self.store()?;
        verify_workspace_binding(&store, &ws.id, Some(&ws.path))?;
        let recent = store
            .compression_requests
            .entry(settings.principal.username().to_owned())
            .or_default();
        let instant = std::time::Instant::now();
        while recent
            .front()
            .is_some_and(|entry| instant.duration_since(*entry).as_secs() >= 300)
        {
            recent.pop_front();
        }
        if recent.len() >= 5 {
            return Err(RuntimeError::new(429, "压缩请求过于频繁，请稍后重试"));
        }
        recent.push_back(instant);
        let (header, count) = store.header(&ws.id, id)?;
        if count == 0 {
            return Err(RuntimeError::bad_request("对话尚无可压缩的消息"));
        }
        if store.shutting_down {
            return Err(RuntimeError::new(503, "Runtime is stopping"));
        }
        if let Some(task) = store.tasks.values_mut().find(|t| {
            t.workspace_id == ws.id && t.conversation_id == id && t.status == Status::Running
        }) {
            if task.kind == "compression"
                || task.compression_pending
                || task.compression_stage.is_some()
                || task.cancel_requested
                || task.closing_turn
            {
                return Err(RuntimeError::new(409, "对话正在压缩或结束本轮任务"));
            }
            task.compression_pending = true;
            task.auto_compression = false;
            return Ok(
                json!({"success":true,"data":{"task_id":task.start.id,"conversation_id":id,"workspace_id":ws.id,"status":"running","queued":true,"task_type":task.kind}}),
            );
        }
        let task_id = Uuid::new_v4().to_string();
        let created_at = now();
        let (stop, cancel) = oneshot::channel();
        let mut task = Task::new(
            TaskStart {
                id: task_id.clone(),
                created_at,
                message: String::new(),
                model_key: Some(model.key().to_owned()),
                message_source: None,
            },
            &ws.id,
            id.to_owned(),
            stop,
        );
        settings.apply_conversation_modes(&header["metadata"]);
        task.approval = Some(settings);
        task.execution_root = Self::worktree_execution_root(&store, &ws.id, &header["metadata"])?;
        task.kind = "compression";
        task.compression_pending = true;
        let completion = Arc::new(TaskCompletion::default());
        task.completion = Some(completion.clone());
        store.register_task(task);
        drop(store);
        let runtime = self.clone();
        let job = task_id.clone();
        tokio::spawn(async move {
            let _release = ReleaseTask(completion);
            let result = tokio::select! {
                biased;
                _=cancel => Err("Task stopped".to_owned()),
                result=runtime.compress_turn(&job,&model) => result.map(|_| Vec::new()),
            };
            let _ = tokio::task::spawn_blocking(move || runtime.finish(&job, result)).await;
        });
        Ok(
            json!({"success":true,"data":{"task_id":task_id,"conversation_id":id,"workspace_id":ws.id,"created_at":created_at,"status":"running","task_type":"compression"}}),
        )
    }

    pub(super) async fn finish_agent_context(
        &self,
        id: &str,
        model: &ModelConfig,
        reply: Option<Value>,
    ) -> Result<std::ops::ControlFlow<Vec<Value>, Vec<Value>>, String> {
        {
            let mut store = self.store().map_err(|e| e.message)?;
            let task = super::writable_task(&mut store, id)?;
            task.closing_turn = true;
            if !task.compression_pending {
                return Ok(std::ops::ControlFlow::Break(reply.into_iter().collect()));
            }
            let (workspace, conversation) =
                (task.workspace_id.clone(), task.conversation_id.clone());
            if let Some(message) = reply {
                store
                    .append_messages(&workspace, &conversation, vec![message], now(), None, None)
                    .map_err(|e| e.message)?;
            }
        }
        let history = self
            .compress_turn(id, model)
            .await?
            .ok_or("Queued compression was not completed")?;
        let mut store = self.store().map_err(|e| e.message)?;
        let task = super::writable_task(&mut store, id)?;
        task.guidance_closed = false;
        task.closing_turn = false;
        Ok(std::ops::ControlFlow::Continue(history))
    }

    pub(super) async fn compress_turn(
        &self,
        task_id: &str,
        model: &ModelConfig,
    ) -> Result<Option<Vec<Value>>, String> {
        {
            let mut store = self.store().map_err(|e| e.message)?;
            let task = super::writable_task(&mut store, task_id)?;
            if !task.compression_pending {
                return Ok(None);
            }
            task.compression_pending = false;
            task.compression_stage = Some("generating_summary");
            task.emit("compression_state",json!({"in_progress":true,"stage":"generating_summary","job_id":task_id,"mode":if task.kind=="compression" {"manual"}else{"requested"}}));
        }
        let result = self.summarize(task_id, model).await;
        if let Err(reason) = &result
            && let Ok(mut store) = self.store()
            && let Some(task) = store.tasks.get_mut(task_id)
        {
            task.compression_stage = None;
            task.emit(
                "compression_state",
                json!({"in_progress":false,"error":reason}),
            );
            let (ws, id) = (task.workspace_id.clone(), task.conversation_id.clone());
            if let Ok(path) = store.conversation_path(&ws, &id)
                && let Ok(mut db) = crate::conversation_db::Database::open(path.parent().unwrap())
            {
                let _ = db.set_field(&id, "metadata", "compression_error", &json!(reason));
            }
        }
        result.map(Some)
    }

    async fn summarize(&self, task_id: &str, model: &ModelConfig) -> Result<Vec<Value>, String> {
        let (ws, id, mode, root, mut document, mut history, round) = {
            let store = self.store().map_err(|e| e.message)?;
            let task = &store.tasks[task_id];
            let root = store.workspaces[&task.workspace_id].clone();
            let exec = task.execution_root.as_deref().unwrap_or(&root);
            let mut raw = store
                .read(&task.workspace_id, &task.conversation_id)
                .map_err(|e| e.message)?
                .into_raw();
            system_prompt::freeze(&store, &task.workspace_id, &mut raw, model, true)
                .map_err(|e| e.message)?;
            let doc = LegacyDocument::from_value(raw).map_err(|e| e.message)?;
            let history = doc
                .agent_history_with_media(&store.data_dir, &root, exec)
                .map_err(|e| e.message)?;
            let round = doc.raw()["metadata"]["compression_count"]
                .as_u64()
                .unwrap_or(0)
                + 1;
            (
                task.workspace_id.clone(),
                task.conversation_id.clone(),
                if task.kind == "compression" {
                    "manual"
                } else if task.auto_compression {
                    "auto"
                } else {
                    "requested"
                },
                root,
                doc.into_raw(),
                history,
                round,
            )
        };
        let tools = {
            let store = self.store().map_err(|e| e.message)?;
            store
                .workspace_tools
                .get(&ws)
                .unwrap_or(&self.tools)
                .clone()
        };
        let schemas = super::agent::tool_menu::for_round(self, task_id, &tools, model)
            .await?
            .round_schemas;
        let original = document["messages"].take();
        let inputs: Vec<String> = original
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(user_input)
            .collect();
        let latest = inputs.last().map(String::as_str).unwrap_or("（无）");
        let prompt = PROMPT
            .replace("{compression_index}", &round.to_string())
            .replace("{latest_user_input}", latest)
            .replace(
                "{incremental_item}",
                if round > 1 {
                    "请单列自上次压缩以来新增的工作。"
                } else {
                    ""
                },
            );
        history.push(json!({"role":"user","content":prompt}));
        let scope = super::billing::BillingScope::main(id.clone(), &document["token_statistics"]);
        let data_dir = self.store().map_err(|e| e.message)?.data_dir.clone();
        let history = crate::tools::media::model_messages(
            &history,
            &data_dir,
            model.supports_images(),
            model.supports_videos(),
        );
        let history = crate::mcp::model_messages(&history, model.supports_images());
        let mut attempts = 0;
        let summary = loop {
            attempts += 1;
            let mut bill = self
                .billing
                .begin(
                    scope.clone(),
                    super::billing::ModelPrice {
                        model_key: model.key(),
                        model_id: model.model_id(),
                        identity: model.price_identity(),
                        responses: model.endpoint.trim_end_matches('/').ends_with("/responses"),
                    },
                )
                .await;
            let reason = match model
                .complete(&history, &schemas, |text| bill.observe_text(text))
                .await
            {
                Ok(reply) => {
                    if bill
                        .settle_success(reply.usage.as_ref(), reply.responses_duration_ms)
                        .await
                        .map_err(|e| e.to_string())?
                        && let Some(usage) = &reply.usage
                    {
                        self.record_usage(task_id, usage)?;
                    }
                    if let Some(text) = reply.message["content"]
                        .as_str()
                        .map(str::trim)
                        .filter(|text| !text.is_empty())
                    {
                        break text.to_owned();
                    }
                    "压缩模型返回了空总结".to_owned()
                }
                Err(error) => error,
            };
            if attempts == 5 {
                return Err(reason);
            }
            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        };
        let workspace = RegisteredWorkspace {
            id: ws.clone(),
            label: String::new(),
            path: root.clone(),
        };
        let names = match &self.sub_agents {
            Some(children) => children.names_for_summary(&workspace, &id)?,
            None => Vec::new(),
        };
        let mut state = Vec::new();
        if !names.is_empty() {
            state.push(format!(
                "- 已创建的子智能体名称：{}。继续工作请发送消息；名称不可重复创建。",
                names.join("、")
            ));
        }
        let terminals = self
            .command_terminals(&workspace, Some(&id))
            .map_err(|e| e.message)?;
        let sessions: Vec<_> = terminals["sessions"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|row| row["session_name"].as_str())
            .collect();
        state.push(if sessions.is_empty() {
            "- 持久终端：当前没有终端会话。".into()
        } else {
            format!("- 持久终端：{}。", sessions.join("、"))
        });
        let state = state.join("\n");
        let mut store = self.store().map_err(|e| e.message)?;
        super::writable_task(&mut store, task_id)?.compression_stage = Some("writing_compact");
        let mut current = store.read(&ws, &id).map_err(|e| e.message)?.into_raw();
        let previous = original.as_array().unwrap();
        let messages = current["messages"].as_array_mut().unwrap();
        if !messages.starts_with(previous) {
            return Err("生成摘要期间对话历史发生了变化".into());
        }
        let stamp = iso_time(now()).ok_or("Invalid timestamp")?;
        let mut marked = 0;
        for message in messages.iter_mut().take(previous.len()) {
            if message["metadata"]["deep_compacted"] != true {
                if !message["metadata"].is_object() {
                    message["metadata"] = json!({});
                }
                message["metadata"]["deep_compacted"] = json!(true);
                message["metadata"]["deep_compacted_round"] = json!(round);
                message["metadata"]["deep_compacted_at"] = json!(stamp);
                marked += 1;
            }
        }
        let previous_records = document["metadata"]["deep_compression_records"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        let text = format!(
            "{}\n## 当前运行时状态\n\n{state}\n",
            compact_text(round, &inputs, &previous_records, &summary)
        );
        let (directory, name) = write_record(&root, &text, round).map_err(|e| e.to_string())?;
        let published = (|| -> Result<Vec<Value>, String> {
            let messages = current["messages"].as_array_mut().unwrap();
            let path = format!(".astrion/compact_result/{name}");
            messages.push(json!({"role":"user","message_id":Uuid::new_v4().to_string(),"timestamp":stamp,
                "content":format!("请依据下方摘要与运行时状态继续工作。\n\n压缩记录文件：{path}\n\n本次压缩摘要：\n{summary}\n\n当前运行时状态：\n{state}"),
                "metadata":{"message_source":"compression_handoff"}}));
            let count = messages.len();
            let record = json!({"count":round,"compact_file":path,"created_at":stamp,"source_conversation_id":id,"compressed_conversation_id":id,"user_inputs_before":inputs.len(),"summary":summary});
            let mut records = previous_records;
            records.push(record.clone());
            let meta = &mut current["metadata"];
            meta["deep_compression_records"] = json!(records);
            meta["last_deep_compression_record"] = record;
            meta["compression_count"] = json!(round);
            meta["compression_in_progress"] = json!(false);
            for key in [
                "compression_error",
                "compression_mode",
                "compression_stage",
                "compression_job_id",
                "compression_resume_payload",
                "compression_pid",
                "workspace_memory_snapshot",
                "workspace_memory_write_version",
            ] {
                meta[key] = Value::Null;
            }
            meta["workspace_memory_enabled"] = json!(true);
            meta["context_baseline_pending"] = json!(true);
            meta["external_session_id"] = json!(Uuid::new_v4().to_string());
            meta["total_messages"] = json!(count);
            meta["browser_snapshot_epoch"] = json!(
                meta["browser_snapshot_epoch"]
                    .as_u64()
                    .unwrap_or(0)
                    .saturating_add(1)
            );
            if meta["tool_loading"].is_object() {
                meta["tool_loading"]["pending"] = meta["tool_loading"]["deferred_set"].clone();
                meta["tool_loading"]["loaded"] = json!([]);
            }
            system_prompt::invalidate_after_compression(meta.as_object_mut().unwrap());
            current["token_statistics"]["current_context_tokens"] = json!(0);
            current["token_statistics"]["cache_cold_start_pending"] = json!(true);
            current["updated_at"] = json!(stamp);
            if mode != "manual" {
                system_prompt::freeze(&store, &ws, &mut current, model, true)
                    .map_err(|e| e.message)?;
            }
            let doc = LegacyDocument::from_value(current).map_err(|e| e.message)?;
            let next = if mode == "manual" {
                Vec::new()
            } else {
                doc.agent_history_with_media(
                    &store.data_dir,
                    &root,
                    store.tasks[task_id]
                        .execution_root
                        .as_deref()
                        .unwrap_or(&root),
                )
                .map_err(|e| e.message)?
            };
            store.persist(&ws, doc, false).map_err(|e| e.message)?;
            let task = store.tasks.get_mut(task_id).unwrap();
            task.compression_stage = None;
            task.emit("compression_finished",json!({"source_conversation_id":id,"conversation_id":id,"in_progress":false,"compact_file":path,"marked_count":marked,"mode":mode,"job_id":task_id,"compress_behavior":if mode=="manual" {"wait"}else{"continue"},"guide_inserted":true}));
            Ok(next)
        })();
        if published.is_err() {
            let _ = directory.remove_file(&name);
        }
        published
    }
}

fn user_input(message: &Value) -> Option<String> {
    let source = message["metadata"]["message_source"]
        .as_str()
        .unwrap_or("user")
        .trim();
    if message["role"] != "user"
        || !(source.is_empty()
            || source.eq_ignore_ascii_case("user")
            || source.eq_ignore_ascii_case("presend"))
    {
        return None;
    }
    let text = match &message["content"] {
        Value::String(s) => s.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter(|p| p["type"] == "text")
            .filter_map(|p| p["text"].as_str())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => return None,
    };
    let text = text.trim();
    (!text.is_empty()).then(|| text.to_owned())
}

fn compact_text(round: u64, inputs: &[String], previous: &[Value], summary: &str) -> String {
    let mut text = format!("# 对话已被第{round}次压缩\n\n## 用户的所有输入\n\n");
    for (i, input) in inputs.iter().enumerate() {
        text.push_str(&format!("{}. {input}\n", i + 1));
    }
    text.push_str("\n## 历次压缩总结\n");
    for record in previous {
        if let Some(s) = record["summary"].as_str() {
            text.push_str(&format!("\n### 第 {} 次\n\n{s}\n", record["count"]));
        }
    }
    text.push_str(&format!("\n### 第 {round} 次\n\n{summary}\n"));
    text
}

fn write_record(
    root: &Path,
    text: &str,
    round: u64,
) -> std::io::Result<(cap_std::fs::Dir, String)> {
    let workspace = storage::open_canonical_dir(root)?;
    let internal = storage::ensure_dir(&workspace, ".astrion")?;
    let directory = storage::ensure_dir(&internal, "compact_result")?;
    let stamp = time::OffsetDateTime::now_utc();
    let prefix = format!(
        "compact_{:04}{:02}{:02}_{:02}{:02}{:02}_{round:03}",
        stamp.year(),
        u8::from(stamp.month()),
        stamp.day(),
        stamp.hour(),
        stamp.minute(),
        stamp.second()
    );
    let mut name = format!("{prefix}.md");
    if let Err(error) = storage::publish(&directory, &name, text.as_bytes(), false) {
        if error.kind() != std::io::ErrorKind::AlreadyExists {
            return Err(error);
        }
        name = format!("{prefix}_{}.md", Uuid::new_v4().simple());
        storage::publish(&directory, &name, text.as_bytes(), false)?;
    }
    Ok((directory, name))
}
