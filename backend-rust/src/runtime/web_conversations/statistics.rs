use super::{Runtime, RuntimeError};
use crate::runtime::conversation_token_statistics::validated_token_statistics;
use serde_json::{Value, json};
use std::collections::BTreeMap;

impl Runtime {
    pub fn web_context_tokens(&self, workspace: &str, id: &str) -> Result<Value, RuntimeError> {
        let id = id.trim();
        let id = if id.starts_with("conv_") {
            id.to_owned()
        } else {
            format!("conv_{id}")
        };
        let tokens = match self.store()?.read(workspace, &id) {
            Ok(doc) => validated_token_statistics(doc.raw().get("token_statistics"))
                .and_then(|stats| stats.get("current_context_tokens").cloned())
                .unwrap_or(json!(0)),
            Err(error) if error.status == 404 => json!(0),
            Err(error) => return Err(error),
        };
        Ok(json!({"success":true,"data":{"total_tokens":tokens}}))
    }

    pub fn web_statistics(&self, workspace: &str) -> Result<Value, RuntimeError> {
        let rows = self.store()?.session_rows(workspace)?;
        let mut statuses = BTreeMap::<String, usize>::new();
        let (mut messages, mut tools, mut thinking, mut users) = (0_u64, 0_u64, 0_usize, 0_usize);
        let (mut input, mut output, mut counted) = (0_u64, 0_u64, 0_usize);
        for row in &rows {
            *statuses
                .entry(row["status"].as_str().unwrap_or("active").into())
                .or_default() += 1;
            thinking += usize::from(row["thinking_mode"] == true);
            messages = messages.saturating_add(row["total_messages"].as_u64().unwrap_or(0));
            tools = tools.saturating_add(row["total_tools"].as_u64().unwrap_or(0));
            let Some(id) = row["id"].as_str() else {
                continue;
            };
            let Ok(doc) = self.store()?.read(workspace, id) else {
                continue;
            };
            let raw = doc.raw();
            users += raw["messages"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|m| m["role"] == "user")
                .count();
            counted += 1;
            if let Some(stats) = validated_token_statistics(raw.get("token_statistics")) {
                input = input.saturating_add(stats["total_input_tokens"].as_u64().unwrap_or(0));
                output = output.saturating_add(stats["total_output_tokens"].as_u64().unwrap_or(0));
            }
        }
        Ok(json!({"success":true,"data":{
            "total_conversations":rows.len(),"total_messages":messages,"total_user_messages":users,"total_tools":tools,
            "status_distribution":statuses,"thinking_mode_distribution":{"thinking":thinking,"fast":rows.len()-thinking},
            "token_statistics":{"total_input_tokens":input,"total_output_tokens":output,"total_tokens":input.saturating_add(output),"conversations_with_stats":counted}
        }}))
    }
}
