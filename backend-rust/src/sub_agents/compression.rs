use super::{execution::now, worker::Context};
use crate::knowledge::Knowledge;
use serde_json::{Value, json};
use std::{collections::HashSet, io, path::Path};

const BEGIN: &str = "<!-- astrion-workspace-memory:start -->";
const END: &str = "<!-- astrion-workspace-memory:end -->";

pub(super) fn refresh_memory(prompt: &str, root: &Path, data: &Path) -> io::Result<String> {
    let memory = Knowledge::open(root, data)?.prompt_workspace_memory()?;
    let fragment = include_str!("../../../prompts/workspace_memory.txt").trim()
        .replace("{workspace_memory_path}",memory.record["path"].as_str().unwrap_or_default())
        .replace("{workspace_memory_maintenance}","你是子智能体：这份共享记忆由主智能体维护。发现需要新增或修正的内容时，向主智能体汇报；不要自行修改。")
        .replace("{workspace_memory_content}", if memory.text.is_empty() { "（当前记忆为空，可直接创建文件。）" } else { &memory.text });
    let fresh = format!("{BEGIN}\n{fragment}\n{END}");
    if let (Some(start), Some(end)) = (prompt.find(BEGIN), prompt.rfind(END)) {
        if end > start {
            return Ok(format!(
                "{}{}{}",
                &prompt[..start],
                fresh,
                &prompt[end + END.len()..]
            ));
        }
    }
    Ok(format!("{}\n\n{fresh}", prompt.trim_end()))
}

pub(super) fn compact(context: &mut Context, root: &Path, data: &Path) -> io::Result<Option<u64>> {
    let doc = &mut context.document;
    let tokens = doc["stats"]["current_context_tokens"]
        .as_u64()
        .unwrap_or_default();
    if tokens < context.threshold {
        return Ok(None);
    }
    let round = doc["stats"]["compress_round"].as_u64().unwrap_or_default() + 1;
    let messages = doc["messages"]
        .as_array_mut()
        .ok_or(io::ErrorKind::InvalidData)?;
    let mut split = messages.len().saturating_sub(8);
    while split > 1 && messages[split]["role"] == "tool" {
        split -= 1;
    }
    if split <= 1 {
        return Ok(None);
    }
    let frozen = refresh_memory(
        messages[0]["content"]
            .as_str()
            .ok_or(io::ErrorKind::InvalidData)?,
        root,
        data,
    )?;
    let mut lines = vec![
        "系统提示：以下是根据之前的对话记录生成的压缩摘要，请在此基础上继续工作。".to_owned(),
        format!(
            "（压缩轮次：第 {round} 次，压缩前消息数：{}）",
            messages.len()
        ),
    ];
    let mut paired = HashSet::new();
    for message in &messages[1..split] {
        let role = message["role"].as_str().unwrap_or_default();
        if role == "tool" {
            if !message["tool_call_id"]
                .as_str()
                .is_some_and(|id| paired.contains(id))
            {
                lines.push(format!(
                    "- {}",
                    message["name"].as_str().unwrap_or("unknown_tool")
                ));
            }
            continue;
        }
        if let Some(content) = message["content"].as_str().filter(|s| !s.trim().is_empty()) {
            lines.push(format!(
                "{role}：{}",
                content
                    .chars()
                    .take(if role == "system" { 300 } else { 500 })
                    .collect::<String>()
            ));
        }
        for call in message["tool_calls"].as_array().into_iter().flatten() {
            if let Some(id) = call["id"].as_str() {
                paired.insert(id);
            }
            let function = &call["function"];
            let parsed = function["arguments"]
                .as_str()
                .and_then(|v| serde_json::from_str::<Value>(v).ok());
            let args = parsed.as_ref().unwrap_or(&function["arguments"]);
            let intent = args["intent"]
                .as_str()
                .filter(|v| !v.trim().is_empty())
                .or(function["name"].as_str())
                .unwrap_or("unknown_tool");
            lines.push(format!("- {intent}"));
        }
    }
    let summary = json!({"role":"system","content":lines.join("\n"),"metadata":{"compression":{"round":round,"compressed_count":split-1,"created_at":now()}}});
    messages[0]["content"] = json!(frozen);
    messages.splice(1..split, [summary]);
    doc["stats"]["compress_round"] = json!(round);
    doc["stats"]["current_context_tokens"] = json!(0);
    doc["external_session_id"] = json!(uuid::Uuid::new_v4().to_string());
    context.rewrite_history = true;
    Ok(Some(round))
}
