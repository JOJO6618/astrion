use super::{Runtime, RuntimeError};
use serde_json::{Value, json};

fn first_user_sentence(messages: &Value) -> String {
    let Some(message) = messages
        .as_array()
        .and_then(|rows| rows.iter().find(|m| m["role"] == "user"))
    else {
        return String::new();
    };
    let text = match &message["content"] {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|part| match part {
                Value::String(text) => Some(text.as_str()),
                _ if part["type"] == "text" => part["text"].as_str(),
                _ => None,
            })
            .collect(),
        Value::Object(_) => message["content"]["text"]
            .as_str()
            .unwrap_or_default()
            .to_owned(),
        _ => message["text"].as_str().unwrap_or_default().to_owned(),
    };
    let normalized = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let end = normalized
        .char_indices()
        .skip(1)
        .find(|(_, c)| "。！？.!?".contains(*c))
        .map_or(normalized.len(), |(i, c)| i + c.len_utf8());
    normalized[..end].to_lowercase()
}

fn entry(row: &Value, kind: &str) -> Value {
    json!({"id":row["id"],"title":row["title"],"created_at":row["created_at"],
        "updated_at":row["updated_at"],"project_path":row["project_path"],"incompatible":row["incompatible"]==true,"match_type":kind})
}

impl Runtime {
    pub fn search_conversations(
        &self,
        workspace: &str,
        needle: &str,
        limit: usize,
    ) -> Result<Vec<Value>, RuntimeError> {
        let mut rows = self.store()?.session_rows(workspace)?;
        rows.sort_by(|a, b| {
            b["updated_at"]
                .as_str()
                .cmp(&a["updated_at"].as_str())
                .then_with(|| a["id"].as_str().cmp(&b["id"].as_str()))
        });
        let (titles, others): (Vec<_>, Vec<_>) = rows.iter().partition(|r| {
            r["title"]
                .as_str()
                .unwrap_or_default()
                .to_lowercase()
                .contains(needle)
        });
        let mut matches: Vec<_> = titles
            .into_iter()
            .take(limit)
            .map(|r| entry(r, "title"))
            .collect();
        for row in others {
            if matches.len() == limit {
                break;
            }
            let Some(id) = row["id"].as_str() else {
                continue;
            };
            if self.first_sentence_matches(workspace, id, needle)? {
                matches.push(entry(row, "content"));
            }
        }
        Ok(matches)
    }

    fn first_sentence_matches(
        &self,
        workspace: &str,
        id: &str,
        needle: &str,
    ) -> Result<bool, RuntimeError> {
        let store=self.store()?;
        let path=store.conversation_path(workspace,id)?;
        let dir=path.parent().unwrap();
        if !dir.join(crate::conversation_db::FILENAME).exists(){return Ok(false);}
        let db=crate::conversation_db::Database::open(dir).map_err(RuntimeError::storage)?;
        let message=db.first_user(id).map_err(RuntimeError::storage)?;
        Ok(message.is_some_and(|m|first_user_sentence(&json!([m])).contains(needle)))
    }
}
