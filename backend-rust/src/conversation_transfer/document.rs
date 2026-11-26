use serde_json::{Value, json};
use uuid::Uuid;

use crate::{catalog::Catalog, legacy::{LegacyDocument, LegacyKind}, runtime::RuntimeError};

use super::{archive::Manifest, bad, conflict, sha256};

pub(crate) fn validate(raw: &Value) -> Result<LegacyKind, RuntimeError> {
    let document = LegacyDocument::from_value(raw.clone()).map_err(|error| bad(error.message))?;
    let messages = raw["messages"].as_array().expect("validated messages");
    if messages.iter().any(|item| !matches!(item["role"].as_str(), Some("system" | "user" | "assistant" | "tool"))) {
        return Err(bad("Invalid conversation message"));
    }
    if document.kind() == LegacyKind::Chat {
        document.chat_revision().map_err(|error| bad(error.message))?;
        let chat = &raw["metadata"]["chat"];
        if ["active_turn_id", "latest_leaf_id"].iter().any(|key| chat.get(key).is_none())
            || messages.iter().any(|item| item["metadata"]["chat"].get("parent_id").is_none())
            || chat["handoffs"].as_object().is_some_and(|items| items.values().any(|v| !v.is_object()))
            || (!chat["handoffs"].is_null() && !chat["handoffs"].is_object())
        {
            return Err(bad("Invalid Chat tree relationships"));
        }
    }
    Ok(document.kind())
}

pub(crate) fn text<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value[key].as_str().filter(|text| !text.is_empty())
}

fn protocol(catalog: &Catalog, key: &str) -> Option<String> {
    catalog.models_response()["items"].as_array().and_then(|items| {
        items.iter().find(|item| item["model_key"] == key)
    }).and_then(|item| item["api_protocol"].as_str().map(str::to_owned))
        .or_else(|| catalog.select(Some(key), false).ok().map(|model| model.api_protocol))
}

pub(crate) fn source_protocol(raw: &Value, catalog: &Catalog) -> Option<String> {
    let metadata = &raw["metadata"];
    text(metadata, "api_protocol").map(str::to_owned)
        .or_else(|| protocol(catalog, text(metadata, "model_key")?))
}

pub(crate) fn check_model(raw: &Value, source: &Value, selected: Option<&str>, catalog: &Catalog) -> Result<(), RuntimeError> {
    let key = selected.or_else(|| text(&raw["metadata"], "model_key"));
    if let Some(key) = key {
        catalog.resolve_key(Some(key), false).map_err(|_| bad(format!("Model is unavailable on this installation: {key}")))?;
    }
    if let Some(key) = selected {
        let original = text(source, "api_protocol").map(str::to_owned).or_else(|| source_protocol(raw, catalog));
        if let Some(original) = original
            && protocol(catalog, key).as_deref() != Some(original.as_str())
        {
            return Err(bad("The selected model uses an incompatible conversation protocol"));
        }
    }
    Ok(())
}

fn canonical(value: &Value, output: &mut String) {
    match value {
        Value::Object(object) => {
            output.push('{');
            let mut entries: Vec<_> = object.iter().collect();
            entries.sort_unstable_by(|a, b| a.0.cmp(b.0));
            for (index, (key, value)) in entries.into_iter().enumerate() {
                if index > 0 { output.push(','); }
                output.push_str(&serde_json::to_string(key).expect("JSON key"));
                output.push(':');
                canonical(value, output);
            }
            output.push('}');
        }
        Value::Array(items) => {
            output.push('[');
            for (index, value) in items.iter().enumerate() {
                if index > 0 { output.push(','); }
                canonical(value, output);
            }
            output.push(']');
        }
        Value::Number(number) if number.is_f64() => {
            let raw = format!("{:?}", number.as_f64().expect("finite JSON float"));
            if let Some((mantissa, exponent)) = raw.split_once('e') {
                let exponent: i32 = exponent.parse().expect("float exponent");
                output.push_str(&format!("{mantissa}e{exponent:+03}"));
            } else { output.push_str(&raw); }
        }
        _ => output.push_str(&value.to_string()),
    }
}

fn history_hash(messages: &[Value]) -> String {
    let mut encoded = String::from("[");
    for (index, message) in messages.iter().enumerate() {
        if index > 0 { encoded.push(','); }
        canonical(message, &mut encoded);
    }
    encoded.push(']');
    sha256(encoded.as_bytes())
}

pub(crate) fn verify_import(raw: &Value, source_id: &str, hash: &str, operation: &str) -> Result<(), RuntimeError> {
    let transfer = &raw["metadata"]["conversation_transfer"];
    let prefix = transfer["history_length"].as_u64().and_then(|length| usize::try_from(length).ok())
        .and_then(|length| raw["messages"].as_array()?.get(..length));
    if transfer["source_conversation_id"] != source_id || transfer["document_sha256"] != hash
        || transfer["operation_id"] != operation
        || prefix.is_none_or(|messages| transfer["history_sha256"] != history_hash(messages))
    {
        return Err(conflict("The migration destination has changed; the source is preserved"));
    }
    Ok(())
}

pub(crate) struct Import<'a> {
    pub manifest: &'a Manifest,
    pub target: &'a Value,
    pub modes: &'a Value,
    pub selected: Option<&'a str>,
    pub operation: &'a str,
    pub id: &'a str,
    pub timestamp: &'a str,
}

impl Import<'_> {
    pub fn prepare(&self, raw: &Value, paths: Value) -> Result<Value, RuntimeError> {
        let mut result = raw.clone();
        result["id"] = json!(self.id);
        result["updated_at"] = json!(self.timestamp);
        let messages = raw["messages"].as_array().expect("validated messages");
        let metadata = &mut result["metadata"];
        let mut previous = metadata["conversation_transfer"].as_object().cloned().unwrap_or_default();
        previous.shift_remove("paths");
        metadata["conversation_transfer"] = json!({
            "source_conversation_id":raw["id"], "source":self.manifest.source,
            "target":self.target, "document_sha256":self.manifest.document_sha256,
            "operation_id":self.operation, "history_length":messages.len(),
            "history_sha256":history_hash(messages), "imported_at":self.timestamp,
            "paths":paths, "previous":previous,
        });
        if self.manifest.kind == "agent" {
            metadata["project_path"] = self.target["project_path"].clone();
            metadata["project_relative_path"] = json!(".");
            for (key, value) in self.modes.as_object().expect("resolved Agent modes") {
                metadata[key] = value.clone();
            }
            for key in ["pending_permission_mode", "pending_execution_mode", "pending_work_mode",
                "pending_network_permission", "project_file_tree", "project_statistics", "project_snapshot_at",
                "compression_resume_payload", "compression_job_id", "compression_error", "compression_stage",
                "compression_mode", "context_baseline_tokens", "context_baseline_model_key", "context_baseline_at",
                "git_worktree", "git_worktree_notice"] {
                if !key.starts_with("git_worktree") || metadata.get(key).is_some() {
                    metadata[key] = Value::Null;
                }
            }
            metadata["compression_in_progress"] = json!(false);
            metadata["context_baseline_pending"] = json!(true);
        }
        if let Some(model) = self.selected { metadata["model_key"] = json!(model); }
        let mut notice = json!({"role":"user", "content":self.notice(),
            "message_id":format!("msg_{}", Uuid::new_v4().simple()), "timestamp":self.timestamp,
            "metadata":{"conversation_migration":true,"message_source":"notify","visibility":"compact","starts_work":false}});
        if self.manifest.kind == "chat" {
            let chat = &mut metadata["chat"];
            if !chat["active_turn_id"].is_null() { return Err(bad("An active Chat turn cannot be imported")); }
            let previous_leaf = chat["latest_leaf_id"].clone();
            notice["metadata"]["chat"] = json!({"parent_id":previous_leaf,"turn_id":null});
            chat.as_object_mut().expect("validated Chat tree").shift_remove("handoff_presentation_id");
            let old = chat["handoffs"].as_object().cloned().unwrap_or_default();
            let mut detached = serde_json::Map::new();
            let mut count = chat["handoff_count"].as_u64().unwrap_or_default().max(old.len() as u64);
            for (position, (key, mut item)) in old.into_iter().enumerate() {
                let source = text(&item, "id").unwrap_or(&key).to_owned();
                let id = format!("handoff_{}", Uuid::new_v4().simple());
                item["source_handoff_id"] = json!(source);
                item["id"] = json!(id);
                for (field, default) in [("message_id", json!(key)), ("sequence", json!(position + 1)),
                    ("tag_index", json!(0)), ("present", json!(true))] {
                    item.as_object_mut().expect("validated handoff").entry(field).or_insert(default);
                }
                count = count.max(item["sequence"].as_u64().unwrap_or_default());
                for key in ["presentation_id", "operation_id", "task_id", "agent_task_id"] {
                    item.as_object_mut().expect("validated handoff").shift_remove(key);
                }
                item["countdown_enabled"] = json!(false);
                item["deadline"] = Value::Null;
                if matches!(item["status"].as_str(), Some("preparing" | "selecting" | "failed")) {
                    item["status"] = json!("pending");
                    item["error"] = json!("");
                }
                if !previous_leaf.is_null() && item["leaf_id"] == previous_leaf {
                    item["leaf_id"] = notice["message_id"].clone();
                }
                detached.insert(id, item);
            }
            chat["handoff_count"] = json!(count);
            chat["handoffs"] = Value::Object(detached);
            chat["commands"] = json!({});
            chat["latest_leaf_id"] = notice["message_id"].clone();
            chat["revision"] = json!(chat["revision"].as_u64().and_then(|n| n.checked_add(1))
                .ok_or_else(|| bad("Invalid Chat revision"))?);
            metadata["pending_network_notices"] = json!([]);
        }
        metadata["total_messages"] = json!(messages.len() + 1);
        metadata["total_tools"] = json!(messages.iter().filter(|item| item["role"] == "assistant")
            .filter_map(|item| item["tool_calls"].as_array()).map(Vec::len).sum::<usize>());
        result["messages"].as_array_mut().expect("validated messages").push(notice);
        validate(&result)?;
        Ok(result)
    }

    fn notice(&self) -> String {
        let source = &self.manifest.source;
        let target = self.target;
        let modes = self.modes;
        let old_name = text(source, "workspace_name").or_else(|| text(source, "workspace_id")).unwrap_or("Chat");
        let old_path = text(source, "project_path").unwrap_or("无");
        let old_env = text(source, "environment").unwrap_or("未知");
        let new_name = text(target, "workspace_name").unwrap_or("Chat");
        let new_path = text(target, "project_path").unwrap_or("无");
        let new_env = text(target, "environment").unwrap_or("未知");
        let mut notice = format!("[系统通知|conversation_migrated]\n本对话已迁移，历史对话内容保持原样。\n原工作区：{old_name}\n原工作区位置：{old_path}\n原运行环境：{old_env}\n当前工作区：{new_name}\n当前工作区位置：{new_path}\n当前运行环境：{new_env}");
        if let Some(work) = text(modes, "work_mode") {
            let permission = text(modes, "permission_mode").unwrap_or_default();
            notice.push_str(&format!("\n当前工作模式：{work}；权限：{permission}；执行环境：sandbox；网络：restricted。"));
        }
        let total = self.manifest.resources.len();
        let materials = self.manifest.resources.iter().filter(|row| row.kind == "material").count();
        notice.push_str(&format!("\n本次携带 {} 项发送过或阅读过的图片/视频，以及 {materials} 项摘要或交接材料。\n普通上传附件、项目源码及其他工作文件未迁移。\n历史路径、文件存在情况、终端、进程、审批及子智能体状态属于迁移前的记录。旧工作区规范与冻结提示中的环境描述属于原环境，当前实际能力与可访问范围以目标环境为准。\n旧图片/视频及材料引用由应用资源映射解析，无需改写历史；其他旧路径使用前请先确认当前位置。后续从原有进度继续。", total - materials));
        if self.manifest.shared_media {
            notice.push_str("\n当前迁移共用原媒体库，已发送和阅读的图片/视频继续使用原有媒体引用，无需复制。");
        }
        notice
    }
}

pub(crate) fn modes(preferences: &Value) -> Value {
    let work = match text(preferences, "default_work_mode").map(|mode| mode.trim().to_ascii_lowercase()).as_deref() {
        Some("ask") => "ask", Some("execute") => "execute", _ => "plan",
    };
    let permission = match (work, text(preferences, "default_permission_mode").map(|mode| mode.trim().to_ascii_lowercase()).as_deref()) {
        ("plan", _) | (_, Some("readonly")) => "readonly",
        (_, Some("auto_approval")) => "auto_approval",
        (_, Some("unrestricted")) => "unrestricted",
        (_, None | Some("approval")) => "approval",
        _ => "unrestricted",
    };
    json!({"work_mode":work,"permission_mode":permission,"execution_mode":"sandbox","network_permission":"restricted"})
}
