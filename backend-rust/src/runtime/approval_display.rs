//! Display-only projection of approval events. It never authorizes an operation.
use std::collections::VecDeque;

use serde_json::{Value, json};

const MAX_RECORDS: usize = 30;
const MAX_RESOLVED: usize = 256;
const MAX_PROGRESS: usize = 100;

#[derive(Default)]
pub(super) struct ApprovalDisplay {
    required: VecDeque<Value>,
    records: VecDeque<Value>,
    resolved: VecDeque<String>,
}

impl ApprovalDisplay {
    pub(super) fn apply(&mut self, cursor: u64, event: &str, data: &Value) {
        match event {
            "tool_approval_required" => {
                let row = &data["approval"];
                let Some(id) = row["approval_id"].as_str() else { return; };
                if row["status"] != "pending" || self.resolved.iter().any(|value| value == id) {
                    return;
                }
                self.required.retain(|previous| previous["approval_id"] != id);
                self.required.push_back(row.clone());
                trim(&mut self.required, MAX_RESOLVED);
            }
            "tool_approval_resolved" => self.resolve(data),
            "auto_approval_progress" => self.progress(cursor, "auto", data),
            "goal_review_progress" => self.progress(cursor, "goal", data),
            "workflow_review_progress" => self.progress(cursor, "workflow", data),
            _ => {}
        }
    }

    fn resolve(&mut self, data: &Value) {
        let Some(id) = data["approval_id"].as_str() else { return; };
        let Some(decision @ ("approved" | "rejected" | "expired" | "cancelled" | "timeout" | "error")) =
            data["decision"].as_str() else { return; };
        if self.resolved.iter().any(|value| value == id) { return; }
        self.resolved.push_back(id.into());
        trim(&mut self.resolved, MAX_RESOLVED);
        let Some(position) = self.required.iter().position(|row| row["approval_id"] == id) else { return; };
        let mut row = self.required.remove(position).expect("known approval index");
        self.decorate_pending(&mut row);
        let mut record = self.records.iter()
            .position(|record| record["kind"] == "auto" && record["approval_id"] == id)
            .and_then(|position| self.records.remove(position))
            .unwrap_or_else(|| json!({"id":format!("auto-{id}"),"kind":"auto","approval_id":id,"progress":[]}));
        if decision_value(decision) { record["final_decision"] = json!(decision); }
        record["reason"] = json!(data["reason"].as_str().or_else(|| row["reason"].as_str()).unwrap_or_default());
        record["tool_name"] = row["tool_name"].clone();
        row["status"] = json!(decision);
        record["approval"] = row;
        self.records.push_back(record);
        trim(&mut self.records, MAX_RECORDS);
    }

    fn progress(&mut self, cursor: u64, kind: &str, data: &Value) {
        let item = &data["progress"];
        if !item.is_object() { return; }
        let id = data["approval_id"].as_str();
        if kind == "auto" && id.is_some_and(|id| self.resolved.iter().any(|value| value == id)) {
            return;
        }
        let previous = if item["stage"] == "start" && id.is_none() { None } else {
            self.records.iter().rposition(|row| row["kind"] == kind && row["approval_id"].as_str() == id)
        };
        let record = match previous {
            Some(index) => &mut self.records[index],
            None => {
                self.records.push_back(json!({
                    "id":format!("{kind}-{cursor}"), "kind":kind, "approval_id":id, "progress":[]
                }));
                trim(&mut self.records, MAX_RECORDS);
                self.records.back_mut().expect("new record is present")
            }
        };
        let progress = record["progress"].as_array_mut().expect("progress list");
        progress.push(item.clone());
        if progress.len() > MAX_PROGRESS { progress.remove(0); }
        if item["decision"].as_str().is_some_and(decision_value) {
            record["decision"] = item["decision"].clone();
        }
    }

    pub(super) fn decorate_pending(&self, row: &mut Value) {
        if let Some(id) = row["approval_id"].as_str()
            && let Some(record) = self.records.iter().find(|item| item["kind"] == "auto" && item["approval_id"] == id)
            && let Some(progress) = record["progress"].as_array()
            && let Some(last) = progress.last()
        {
            row["auto_review_progress"] = record["progress"].clone();
            // The trusted pending approval may already know the review result.
            // Never regress its terminal review status to a prior progress stage.
            if !row["auto_review_status"].as_str().is_some_and(decision_value) {
                let status = last["decision"].as_str().filter(|s| decision_value(s))
                    .or_else(|| last["stage"].as_str().filter(|s| decision_value(s) || *s == "pending"))
                    .unwrap_or("reviewing");
                row["auto_review_status"] = json!(status);
            }
        }
    }

    pub(super) fn records(&self) -> Vec<Value> { self.records.iter().cloned().collect() }
    pub(super) fn resolved(&self) -> Vec<String> { self.resolved.iter().cloned().collect() }
}

fn decision_value(text: &str) -> bool { matches!(text, "approved" | "rejected" | "error") }

fn trim<T>(rows: &mut VecDeque<T>, limit: usize) {
    while rows.len() > limit { rows.pop_front(); }
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust审批快照/approval_display_contract.rs"]
mod tests;
