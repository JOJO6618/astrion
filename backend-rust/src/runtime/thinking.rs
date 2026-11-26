//! Display-only live reasoning and completed provider summaries.

use std::{fs, path::{Path, PathBuf}};

use serde_json::{Value, json};

use super::{Runtime, RuntimeError, store::{Store, read_json, write_atomically}};
use super::thinking_auxiliary::{SummaryStream, ThinkingWork};

pub(crate) struct ThinkingStream<'a> {
    runtime: &'a Runtime,
    task: &'a str,
    response: &'a str,
    text: String,
    summaries: Vec<String>,
    ended: bool,
    keep_summary: bool,
    work: Option<&'a ThinkingWork>,
    auxiliary: Option<SummaryStream>,
}

impl<'a> ThinkingStream<'a> {
    pub(crate) fn new(runtime: &'a Runtime, task: &'a str, response: &'a str, work: Option<&'a ThinkingWork>) -> Self {
        Self { runtime, task, response, text: String::new(), summaries: Vec::new(), ended: false, keep_summary: false,
            work, auxiliary: None }
    }

    pub(crate) fn push(&mut self, text: &str) -> Result<(), String> {
        if self.ended { return Ok(()); }
        if self.text.is_empty() {
            self.runtime.emit(self.task, "thinking_start", json!({"response_message_id":self.response,"thinking_generation":self.response}))?;
            self.auxiliary = self.work.map(|work| work.start(self.response));
        }
        self.text.push_str(text);
        if let Some(auxiliary) = &self.auxiliary { auxiliary.push(text); }
        self.runtime.emit(self.task, "thinking_chunk", json!({"content":text}))
    }

    pub(crate) fn native_summary(&mut self, text: &str) {
        let cleaned = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if self.text.is_empty() || cleaned.is_empty() || self.summaries.last() == Some(&cleaned) { return; }
        self.summaries.push(cleaned);
        publish_summary(self.runtime, self.task, self.response, &self.summaries, || true);
    }

    pub(crate) fn end(&mut self) -> Result<(), String> {
        if !self.ended && !self.text.is_empty() {
            self.runtime.emit(self.task, "thinking_end", json!({"full_content":self.text}))?;
            if let Some(auxiliary) = &self.auxiliary { auxiliary.end(); }
        }
        self.ended = true;
        Ok(())
    }

    pub(crate) fn keep(&mut self) {
        self.keep_summary = true;
        if let (Some(work), Some(auxiliary)) = (self.work, self.auxiliary.take()) { work.keep(auxiliary); }
    }

    pub(crate) fn started(&self) -> bool { !self.text.is_empty() }
}

impl Drop for ThinkingStream<'_> {
    fn drop(&mut self) {
        if self.keep_summary { return; }
        let ran_auxiliary = self.auxiliary.is_some();
        if let Some(auxiliary) = self.auxiliary.take() { auxiliary.cancel(); }
        if let Some(work) = self.work { work.remove(self.response); }
        if (ran_auxiliary || !self.summaries.is_empty()) && let Ok(store) = self.runtime.store() {
            let _ = update_summary(&store, self.task, self.response, None);
        }
    }
}

pub(super) fn publish_summary(runtime: &Runtime, task: &str, response: &str, summaries: &[String], active: impl FnOnce() -> bool) -> bool {
    let Some(text) = summaries.last() else { return false; };
    let revision = summaries.len();
    let entry = json!({"text":text,"revision":revision,"generation":response,"summaries":summaries});
    if let Ok(mut store) = runtime.store()
        && active()
        && super::writable_task(&mut store, task).is_ok()
        && update_summary(&store, task, response, Some(entry)).is_ok()
    {
        store.tasks.get_mut(task).expect("locked thinking task").emit("thinking_summary", json!({
            "response_message_id":response,"generation":response,"summary":text,"revision":revision,
        }));
        return true;
    }
    false
}

fn update_summary(store: &Store, task: &str, response: &str, entry: Option<Value>) -> Result<(), RuntimeError> {
    let task = store.tasks.get(task).ok_or_else(RuntimeError::task_not_found)?;
    let id = &task.conversation_id;
    let directory = if task.workspace_id.is_empty() {
        store.data_dir.join("chat/conversations")
    } else {
        store.conversation_path(&task.workspace_id, id)?.parent().expect("conversation parent").to_owned()
    };
    let path = summary_path(&directory, id).ok_or_else(|| RuntimeError::bad_request("Invalid conversation ID"))?;
    let parent = path.parent().expect("summary directory");
    if entry.is_some() {
        match fs::create_dir(parent) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(error) => return Err(RuntimeError::storage(error)),
        }
    }
    if !fs::symlink_metadata(parent).is_ok_and(|metadata| metadata.is_dir()) {
        return Err(RuntimeError::storage("Thinking summary directory is unavailable"));
    }
    let mut state = read_json(&path)?.unwrap_or_else(|| json!({"version":1,"responses":{}}));
    if state["version"] != 1 { return Err(RuntimeError::storage("Invalid thinking summary version")); }
    let records = state["responses"].as_object_mut().ok_or_else(|| RuntimeError::storage("Invalid thinking summaries"))?;
    if let Some(entry) = entry { records.insert(response.into(), entry); }
    else if records.shift_remove(response).is_none() { return Ok(()); }
    write_atomically(&path, &state)
}

fn summary_path(directory: &Path, id: &str) -> Option<PathBuf> {
    let id = id.strip_prefix("conv_")?;
    super::store::valid_component(id).then(|| directory.join("thinking_summaries").join(format!("conv_{id}.json")))
}

pub(crate) fn decorate(directory: &Path, id: &str, document: &mut Value) {
    let Some(path) = summary_path(directory, id) else { return; };
    if !path.parent().and_then(|path| fs::symlink_metadata(path).ok()).is_some_and(|metadata| metadata.is_dir()) { return; }
    let Ok(Some(state)) = read_json(&path) else { return; };
    let Some(messages) = document["messages"].as_array_mut() else { return; };
    for message in messages {
        let Some(response) = message["message_id"].as_str() else { continue; };
        let entry = &state["responses"][response];
        if entry["text"].is_string() && entry["revision"].is_u64() { message["thinking_summary"] = entry.clone(); }
    }
}

pub(crate) fn display_actions(activity: &Value) -> Vec<Value> {
    let Some(task) = activity["task_id"].as_str() else { return Vec::new(); };
    [("thinking", &activity["thinking_stream"]), ("text", &activity["stream"])].into_iter().filter_map(|(kind, streamed)| {
        let start = streamed["start_idx"].as_u64()?;
        let mut action = json!({"id":format!("{task}:{start}:{kind}"),"type":kind,"content":streamed["content"],
            "timestamp":streamed["timestamp"],"responseMessageId":streamed["response_message_id"],"streaming":streamed["streaming"]});
        if kind == "thinking" {
            action["thinkingGeneration"] = streamed["response_message_id"].clone();
            if let Some(summary) = streamed["summary"].as_object() {
                action["thinkingSummary"] = summary["summary"].clone();
                action["thinkingSummaryRevision"] = summary["revision"].clone();
            }
        }
        Some(action)
    }).collect()
}
