use std::path::Path;

use serde_json::{Value, json};
use tokio::{sync::oneshot, time::{Duration, Instant}};

use crate::workspace_catalog::RegisteredWorkspace;

use super::{SubAgents, execution::{Parent, Question, find_name, matching, push_notice, queue, storage},
    protocol::{self, NoticeType, message_id, notice}, safe_id,
};

impl SubAgents {
    pub(super) async fn question(&self, parent: &Parent, from: &str, tool: &str, args: &Value) -> Result<Value, String> {
        let text = protocol::text(args, "question")?;
        let timeout = protocol::timeout(args)?.unwrap_or(600);
        let key = if args["question_id"].is_null() { message_id("question") } else {
            protocol::text(args, "question_id")?.to_owned()
        };
        if key.len() > 100 || !safe_id(&key) { return Err("question_id格式无效。".into()); }
        // A client may reuse a question_id after timeout. A late natural
        // answer or receiver cleanup must not affect the newer question.
        let generation = message_id("question_generation");
        let receiver = {
            let mut live = self.lock()?;
            let mut doc = self.registry.document().map_err(storage)?;
            let records = matching(&doc, parent);
            let from_record = records.get(from).ok_or("子智能体任务不存在。")?;
            if from_record["status"] == "terminated" || !live.workers.get(from).is_some_and(|worker| !worker.borrow().cancelled()) {
                return Err("子智能体已停止。".into());
            }
            if live.questions.contains_key(&key) { return Err("question_id已被使用。".into()); }
            let from_name = super::string(from_record, "name").unwrap_or_default();
            let mut target = None;
            match tool {
                protocol::ASK_MASTER => push_notice(&mut doc, parent, notice(NoticeType::AskMaster, from_name, None, &key, text))?,
                protocol::ASK_OTHER => {
                    let name = protocol::name(&args["name"])?;
                    let id = find_name(&records, name).filter(|id| *id != from).ok_or("无法向该子智能体提问。")?;
                    let record = &mut doc["tasks"][id];
                    self.check_resumable(&live, parent, id, record)?;
                    let name = record["name"].as_str().unwrap_or_default();
                    let message = notice(NoticeType::AskOther, from_name, Some(name), &key, text);
                    queue(record, &format!("{message}\n\n请使用 answer_other_agent 回答，question_id 为 {key}。"), None)?
                        ["rust_answer_question"] = json!({"id":key,"generation":generation});
                    push_notice(&mut doc, parent, message)?;
                    target = Some(id.to_owned());
                }
                _ => return Err("未知的子智能体通信工具。".into()),
            }
            self.registry.store(&doc).map_err(storage)?;
            let (answer, receiver) = oneshot::channel();
            live.questions.insert(key.clone(), Question {
                child: from.into(), target: target.clone(), generation: generation.clone(), answer,
            });
            if let Some(id) = target && let Err(error) = self.launch_locked(&mut live, &mut doc, parent, &id) {
                live.questions.remove(&key);
                return Err(error);
            }
            receiver
        };
        let outcome = match Instant::now().checked_add(Duration::from_secs(timeout)) {
            Some(deadline) => tokio::time::timeout_at(deadline, receiver).await.ok(),
            None => Some(receiver.await),
        };
        if let Ok(mut live) = self.lock()
            && live.questions.get(&key).is_some_and(|question| question.generation == generation)
        { live.questions.remove(&key); }
        Ok(match outcome {
            Some(Ok(answer)) => json!({"success":true,"answer":answer,"question_id":key}),
            Some(Err(_)) => json!({"success":false,"error":"回答方已终结。","question_id":key}),
            None => json!({"success":false,"error":"等待回答超时。","question_id":key}),
        })
    }

    pub(crate) fn answer(&self, parent: &Parent, actor: Option<&str>, args: &Value) -> Result<Value, String> {
        let key = protocol::text(args, "question_id")?;
        let answer = protocol::text(args, "answer")?;
        let mut live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        let records = matching(&doc, parent);
        let Some(question) = live.questions.get(key).filter(|question| {
            question.target.as_deref() == actor && records.contains_key(&question.child)
                && actor.is_none_or(|id| records.contains_key(id) && live.workers.get(id).is_some_and(|worker| !worker.borrow().cancelled()))
        }) else { return Ok(json!({"success":false,"question_id":key})); };
        if let Some(actor) = actor {
            let from = super::string(&records[actor], "name").unwrap_or_default();
            let target = super::string(&records[&question.child], "name").unwrap_or_default();
            let text = notice(NoticeType::Answer, from, Some(target), &message_id("answer"), answer);
            push_notice(&mut doc, parent, text)?;
            self.registry.store(&doc).map_err(storage)?;
        }
        let question = live.questions.remove(key).expect("validated live question");
        Ok(json!({"success":question.answer.send(answer.to_owned()).is_ok(),"question_id":key}))
    }

    pub fn has_pending(&self, workspace: &RegisteredWorkspace, parent: &str) -> std::io::Result<bool> {
        let parent = self.verified_parent(workspace, parent).map_err(std::io::Error::other)?;
        self.pending_notices(&parent).map(|notices| !notices.is_empty()).map_err(std::io::Error::other)
    }

    pub(crate) fn pending_notices(&self, parent: &Parent) -> Result<Vec<String>, String> {
        let _live = self.lock()?;
        let doc = self.registry.document().map_err(storage)?;
        let Some(queue) = notice_queue(&doc, parent) else { return Ok(Vec::new()); };
        Ok(queue.iter().filter_map(Value::as_str).map(str::to_owned).collect())
    }

    /// Remove the committed prefix. New producers can append while the main
    /// conversation gate is persisting messages, so preserve the suffix.
    pub(crate) fn ack_notices(&self, parent: &Parent, notices: &[String]) -> Result<(), String> {
        if notices.is_empty() { return Ok(()); }
        let _live = self.lock()?;
        let mut doc = self.registry.document().map_err(storage)?;
        if notice_queue(&doc, parent).is_none() { return Err("子智能体消息队列不可用。".into()); }
        let queue = doc["states"][&parent.conversation_id]["pending_master_messages"].as_array_mut()
            .ok_or("子智能体消息队列不可用。")?;
        let mut committed = notices.iter();
        let mut expected = committed.next();
        let mut count = 0;
        for value in queue.iter() {
            if expected.is_none() { break; }
            if let Some(text) = value.as_str() {
                if expected.map(String::as_str) != Some(text) { return Err("子智能体消息队列已改变。".into()); }
                expected = committed.next();
            }
            count += 1;
        }
        if expected.is_some() { return Err("子智能体消息队列已改变。".into()); }
        queue.drain(..count);
        self.registry.store(&doc).map_err(storage)
    }
}

fn notice_queue<'a>(doc: &'a Value, parent: &Parent) -> Option<&'a Vec<Value>> {
    let state = &doc["states"][&parent.conversation_id];
    if let Some(root) = state["rust_workspace_root"].as_str() {
        if !super::same_path(Path::new(root), &parent.root) { return None; }
    } else {
        // A legacy state contains no workspace ID. Only unambiguous task
        // ownership can bind its previously queued messages to a workspace.
        let records = doc["tasks"].as_object()?;
        let mut seen = false;
        for record in records.values().filter_map(Value::as_object) {
            if super::string(record, "conversation_id").and_then(super::normalize_conversation).as_deref() == Some(parent.conversation_id.as_str()) {
                if !super::owns_path(record, &parent.root) { return None; }
                seen = true;
            }
        }
        if !seen { return None; }
    }
    state["pending_master_messages"].as_array()
}
