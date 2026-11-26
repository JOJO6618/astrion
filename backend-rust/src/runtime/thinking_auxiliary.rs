//! Optional auxiliary model requests. They run alongside the main response.

use std::{sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}}, time::Duration};

use serde_json::json;
use tokio::{sync::mpsc, task::{AbortHandle, JoinHandle}, time::Instant};

use super::{ModelConfig, Runtime, billing::{BillingScope, ModelPrice}, store::read_json, thinking::publish_summary};
use crate::model::{RoundBudget, StreamDelta};

const PROMPT: &str = include_str!("../../../prompts/thinking_summary.txt");
const REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

pub(crate) struct ThinkingWork {
    job: SummaryJob,
    workers: Mutex<Workers>,
}

#[derive(Default)]
struct Workers {
    jobs: Vec<JoinHandle<()>>,
    abort: Vec<(AbortHandle, Arc<AtomicBool>)>,
}

#[derive(Clone)]
struct SummaryJob {
    runtime: Runtime,
    task: String,
    conversation: String,
    input: String,
    model: ModelConfig,
    interval: Duration,
    published: Arc<Mutex<Vec<(String, String)>>>,
}

enum Change { Chunk(String), End }

pub(super) struct SummaryStream {
    sender: mpsc::UnboundedSender<Change>,
    worker: JoinHandle<()>,
    cancelled: Arc<AtomicBool>,
}

impl ThinkingWork {
    pub(crate) fn for_task(runtime: &Runtime, task_id: &str, main_model: &ModelConfig) -> Option<Self> {
        if main_model.uses_responses() { return None; }
        let store = runtime.store().ok()?;
        let task = store.tasks.get(task_id)?;
        let settings = read_json(&store.data_dir.join("personalization.json")).ok()??;
        if settings["thinking_summary_enabled"] != true { return None; }
        let key = settings["thinking_summary_model"].as_str()?.trim();
        if key.is_empty() { return None; }
        let catalog = store.title_catalog.as_ref()?.load();
        let selected = catalog.select(Some(catalog.auxiliary_key(key)?), false).ok()?;
        let model = ModelConfig::from_catalog(selected).ok()?.with_session(&task.conversation_id, false);
        let interval = settings["thinking_summary_interval_seconds"].as_u64().filter(|seconds| (1..=3600).contains(seconds)).unwrap_or(20);
        Some(Self { job: SummaryJob { runtime: runtime.clone(), task: task_id.into(), conversation: task.conversation_id.clone(),
            input: task.start.message.clone(), model, interval: Duration::from_secs(interval), published: Arc::default() },
            workers: Mutex::default() })
    }

    pub(super) fn start(&self, response: &str) -> SummaryStream {
        let (sender, receiver) = mpsc::unbounded_channel();
        let job = self.job.clone();
        let response = response.to_owned();
        let cancelled = Arc::new(AtomicBool::new(false));
        let token = cancelled.clone();
        let worker = tokio::spawn(async move { job.run(response, receiver, token).await; });
        SummaryStream { sender, worker, cancelled }
    }

    pub(super) fn keep(&self, stream: SummaryStream) {
        stream.end();
        let mut workers = self.workers.lock().unwrap_or_else(|error| error.into_inner());
        workers.abort.push((stream.worker.abort_handle(), stream.cancelled));
        workers.jobs.push(stream.worker);
    }

    pub(super) fn remove(&self, response: &str) {
        if let Ok(mut published) = self.job.published.lock() { published.retain(|entry| entry.0 != response); }
    }

    pub(crate) async fn finish(&self) {
        let jobs = std::mem::take(&mut self.workers.lock().unwrap_or_else(|error| error.into_inner()).jobs);
        for job in jobs { let _ = job.await; }
    }
}

impl Drop for ThinkingWork {
    fn drop(&mut self) {
        let workers = self.workers.get_mut().unwrap_or_else(|error| error.into_inner());
        for (request, cancelled) in &workers.abort { cancelled.store(true, Ordering::Release); request.abort(); }
    }
}

impl SummaryStream {
    pub(super) fn push(&self, delta: &str) { let _ = self.sender.send(Change::Chunk(delta.into())); }
    pub(super) fn end(&self) { let _ = self.sender.send(Change::End); }
    pub(super) fn cancel(self) { self.cancelled.store(true, Ordering::Release); self.worker.abort(); }
}

impl SummaryJob {
    async fn run(self, response: String, mut updates: mpsc::UnboundedReceiver<Change>, cancelled: Arc<AtomicBool>) {
        let mut pending = String::new();
        let mut first = None;
        let mut last_request = None;
        let mut ended = false;
        let mut summaries = Vec::new();
        loop {
            let minimum = if last_request.is_some() { 500 } else { 200 };
            let ready = pending.chars().count() >= minimum;
            if ended && !ready { return; }
            let due = last_request.map(|last: Instant| last + self.interval)
                .or_else(|| first.map(|start: Instant| start + Duration::from_secs(3)));
            if ready && (due.is_some_and(|deadline| deadline <= Instant::now()) || ended && last_request.is_some()) {
                let start = pending.char_indices().rev().nth(1999).map_or(0, |(index, _)| index);
                let fragment = pending.split_off(start);
                pending.clear();
                last_request = Some(Instant::now());
                if let Some(summary) = self.generate(&fragment).await {
                    summaries.push(summary.clone());
                    if publish_summary(&self.runtime, &self.task, &response, &summaries, || !cancelled.load(Ordering::Acquire)) {
                        self.published.lock().unwrap_or_else(|error| error.into_inner()).push((response.clone(), summary));
                    }
                }
                continue;
            }
            let change = match due.filter(|_| ready) {
                Some(deadline) if ended => { tokio::time::sleep_until(deadline).await; continue; }
                Some(deadline) => tokio::select! {
                    _ = tokio::time::sleep_until(deadline) => continue,
                    next = updates.recv() => next,
                },
                None => updates.recv().await,
            };
            match change {
                Some(Change::Chunk(fragment)) => { first.get_or_insert_with(Instant::now); pending.push_str(&fragment); }
                Some(Change::End) | None => ended = true,
            }
        }
    }

    async fn generate(&self, fragment: &str) -> Option<String> {
        let previous = self.published.lock().ok()?.iter().map(|entry| entry.1.as_str()).collect::<Vec<_>>().join("\n");
        let input = &self.input;
        let previous = if previous.is_empty() { "无" } else { &previous };
        let prompt = format!("请为如下内容生成思考摘要\n\n<用户原始输入>\n{input}\n</用户原始输入>\n\n<此前摘要>\n{previous}\n</此前摘要>\n\n<当前片段原文>\n{fragment}\n</当前片段原文>");
        let messages = [json!({"role":"system","content":PROMPT}), json!({"role":"user","content":prompt})];
        let model = &self.model;
        let mut bill = self.runtime.billing.begin(BillingScope::thinking_summary(self.conversation.clone()), ModelPrice {
            model_key: model.key(), model_id: model.model_id(), identity: &model.price_identity, responses: false,
        }).await;
        let mut received_output = false;
        let mut usage = None;
        let mut budget = RoundBudget::default();
        let result = tokio::time::timeout(REQUEST_TIMEOUT, model.complete_with_updates(&messages, &[], &mut budget, |delta| {
            match delta {
                StreamDelta::Text(text) | StreamDelta::Thinking(text) => {
                    received_output |= !text.is_empty();
                    bill.observe_text(text);
                }
                StreamDelta::Usage(value) => usage = Some(value.clone()),
                StreamDelta::NativeSummary(_) => (),
            }
        })).await;
        let completion = result.ok().and_then(Result::ok);
        if completion.is_some() || received_output || usage.is_some() {
            let _ = bill.settle_success(usage.as_ref(), None).await;
        }
        clean_summary(completion?.message["content"].as_str()?)
    }
}

fn clean_summary(text: &str) -> Option<String> {
    let normalized: String = text.split_whitespace().collect::<Vec<_>>().join(" ").chars()
        .filter(|ch| !ch.is_ascii_punctuation() && !"，。！？：；（）【】《》「」『』、…—".contains(*ch)).collect();
    let text = normalized.trim();
    if text.is_empty() { return None; }
    if text.chars().any(|ch| ('\u{4e00}'..='\u{9fff}').contains(&ch)) {
        (text.chars().count() <= 15).then(|| text.to_owned())
    } else {
        (!text.is_ascii() || (2..=4).contains(&text.split_whitespace().count())).then(|| text.to_owned())
    }
}
