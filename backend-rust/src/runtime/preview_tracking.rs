use std::{
    collections::HashSet,
    path::{Component, Path},
};

use serde_json::{Value, json};

use super::{Runtime, web_messages::project_preview_targets, writable_task};

const MAX_TARGETS: usize = 100;
const MAX_URL: usize = 4096;
fn url_character(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ":./_-~%[]@#?=&+".contains(ch)
}

#[derive(Default)]
pub(super) struct ModelText {
    pending: String,
    seen: HashSet<String>,
}

impl ModelText {
    pub(super) fn feed(&mut self, chunk: &str, final_chunk: bool) -> Vec<Value> {
        self.pending.push_str(chunk);
        let end = if final_chunk {
            self.pending.len()
        } else {
            self.pending
                .rfind(|ch: char| !url_character(ch))
                .map_or(0, |offset| {
                    offset + self.pending[offset..].chars().next().unwrap().len_utf8()
                })
        };
        let candidates = self.pending[..end].to_owned();
        self.pending.drain(..end);
        if self.pending.len() > MAX_URL {
            self.pending.clear();
        }
        let candidates = candidates
            .split(|ch: char| !url_character(ch))
            .filter_map(|candidate| {
                let candidate = candidate.trim_end_matches(['.', ':', '?']);
                let scheme = candidate.get(..8)?;
                if !(scheme[..7].eq_ignore_ascii_case("http://")
                    || scheme.eq_ignore_ascii_case("https://"))
                {
                    return None;
                }
                let parsed = reqwest::Url::parse(candidate).ok()?;
                if !matches!(
                    parsed.host_str(),
                    Some("localhost" | "127.0.0.1" | "0.0.0.0" | "[::1]")
                ) || parsed.port() == Some(0)
                    || !parsed.username().is_empty()
                    || parsed.password().is_some()
                {
                    return None;
                }
                Some(json!({"type":"server","url":candidate,"source":"model_output"}))
            })
            .collect::<Vec<_>>();
        let mut new = project_preview_targets(&json!(candidates), &|_| true);
        new.retain(|row| {
            row["url"]
                .as_str()
                .is_some_and(|url| self.seen.len() < MAX_TARGETS && self.seen.insert(url.into()))
        });
        new
    }
}

impl Runtime {
    pub(super) fn record_preview_file(&self, task_id: &str, path: &str) -> Result<(), String> {
        let normalized = path.trim().replace('\\', "/");
        let file = Path::new(&normalized);
        if normalized.contains(':')
            || file
                .components()
                .any(|part| !matches!(part, Component::CurDir | Component::Normal(_)))
            || !file
                .extension()
                .and_then(|part| part.to_str())
                .is_some_and(|suffix| {
                    suffix.eq_ignore_ascii_case("html") || suffix.eq_ignore_ascii_case("htm")
                })
        {
            return Ok(());
        }
        let display = normalized.trim_start_matches("./");
        let label = file
            .file_name()
            .and_then(|part| part.to_str())
            .unwrap_or(display);
        self.record_preview_targets(
            task_id,
            vec![json!({"type":"file","path":display,"label":label,"source":"file_edit"})],
        )
    }

    pub(super) fn record_preview_targets(
        &self,
        task_id: &str,
        proposed: Vec<Value>,
    ) -> Result<(), String> {
        if proposed.is_empty() {
            return Ok(());
        }
        let mut store = self.store().map_err(|error| error.message)?;
        let task = writable_task(&mut store, task_id)?;
        let workspace = task.workspace_id.clone();
        let conversation = task.conversation_id.clone();
        let (header, _) = store
            .header(&workspace, &conversation)
            .map_err(|error| error.message)?;
        let known = project_preview_targets(&header["metadata"]["preview_targets"], &|_| true);
        let original_count = known.len();
        let mut combined = known;
        combined.extend(proposed);
        let mut all = project_preview_targets(&json!(combined), &|_| true);
        if all.len() == original_count {
            return Ok(());
        }
        let start = all.len().saturating_sub(MAX_TARGETS);
        all.drain(..start);
        store
            .update_agent_metadata(&workspace, &conversation, |metadata| {
                metadata["preview_targets"] = json!(all);
            })
            .map_err(|error| error.message)?;
        writable_task(&mut store, task_id)?.emit(
            "preview_targets_updated",
            json!({"conversation_id":conversation,"preview_targets":all}),
        );
        Ok(())
    }
}
