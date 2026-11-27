//! 运行期模式（权限/执行环境/网络/运行模式）切换的对话内通知。
//!
//! 系统提示词在对话首次受理时冻结，切换模式不会重写它。这里把变化以 user 消息
//! 注入对话，让模型按新规则执行；`runtime_mode_baseline` 记录模型已知晓的模式，
//! 空闲期切换会在下一条消息前补发一次，通知真正注入后同步基线，天然去重。

use std::path::Path;

use serde_json::{Map, Value, json};

use super::system_prompt::{execution_section, permission_section, work_section};

/// conversation metadata：本对话「智能体已知晓」的运行期模式基线。
pub(super) const BASELINE: &str = "runtime_mode_baseline";
/// 需要通知模型的模式种类；顺序决定同批次注入顺序。
const KINDS: [&str; 4] = [
    "work_mode",
    "permission_mode",
    "execution_mode",
    "network_permission",
];

/// 切换通知的 message_source，与前端隐藏规则和历史数据保持一致。
fn source(kind: &str) -> &'static str {
    match kind {
        "permission_mode" => "permission_change",
        "execution_mode" => "execution_change",
        "network_permission" => "network_change",
        _ => "work_mode_change",
    }
}

fn label(kind: &str, mode: &str) -> String {
    match (kind, mode) {
        ("permission_mode", "readonly") => "只读",
        ("permission_mode", "approval") => "人工审批",
        ("permission_mode", "auto_approval") => "自动审核",
        ("permission_mode", "unrestricted") => "无限制",
        ("execution_mode", "direct") => "完全访问",
        ("execution_mode", _) => "受限执行",
        ("network_permission", "restricted") => "受限",
        ("network_permission", "full") => "完全开放",
        ("network_permission", _) => "完全禁止",
        ("work_mode", "ask") => "询问",
        ("work_mode", "execute") => "执行",
        ("work_mode", _) => "计划",
        _ => mode,
    }
    .to_owned()
}

/// 通知正文：携带切换后的完整规则，覆盖冻结系统提示词里的旧说明。
fn text(kind: &str, mode: &str) -> String {
    let label = label(kind, mode);
    match kind {
        "permission_mode" => format!(
            "权限模式被用户修改为{label}。以本次规则为准。\n\n{}",
            permission_section(mode)
        ),
        "execution_mode" => format!(
            "执行环境被用户修改为{label}。以本次规则为准。\n\n{}",
            execution_section(mode)
        ),
        "network_permission" => format!("网络权限被用户修改为{label}。"),
        _ => format!(
            "运行模式被用户修改为{label}。与之前系统提示词中的运行模式说明不一致时，以本次为准。\n\n{}",
            work_section(mode)
        ),
    }
}

/// 一条可直接注入对话（并可持久化）的 user 消息。
pub(super) fn message(kind: &str, mode: &str) -> Value {
    json!({
        "role": "user",
        "content": text(kind, mode),
        "metadata": {
            "source": source(kind),
            "message_source": "runtime_notice",
            "runtime_mode_notice": true,
            "visibility": "compact",
            "starts_work": false,
            "kind": kind,
            "mode": mode,
        },
    })
}

/// 与基线比较，返回需要补发通知的模式。
///
/// 基线缺失（新对话、历史对话）时静默对齐当前值并返回空：首条消息的冻结系统
/// 提示词本身就是当前模式，无需额外通知。
pub(super) fn drift(
    data_dir: &Path,
    metadata: &mut Map<String, Value>,
) -> Vec<(&'static str, String)> {
    let current = current(data_dir, metadata);
    let Some(baseline) = metadata.get(BASELINE).and_then(Value::as_object).cloned() else {
        metadata.insert(BASELINE.into(), Value::Object(current));
        return Vec::new();
    };
    KINDS
        .iter()
        .filter_map(|kind| {
            let now = current.get(*kind)?.as_str()?.to_owned();
            (baseline.get(*kind).and_then(Value::as_str) != Some(now.as_str()))
                .then(|| (*kind, now))
        })
        .collect()
}

/// 通知真正注入对话后同步基线，避免下一条 user 消息重复补发。
pub(super) fn mark_known(data_dir: &Path, metadata: &mut Map<String, Value>) {
    let current = current(data_dir, metadata);
    metadata.insert(BASELINE.into(), Value::Object(current));
}

/// 当前生效的模式值；对话 metadata 里从未出现过的键不参与比对。
fn current(data_dir: &Path, metadata: &Map<String, Value>) -> Map<String, Value> {
    let value = Value::Object(metadata.clone());
    crate::web_status::effective_modes(data_dir, &value)
        .into_iter()
        .filter(|(kind, _)| metadata.get(*kind).is_some_and(Value::is_string))
        .map(|(kind, mode)| (kind.to_owned(), json!(mode)))
        .collect()
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_运行期模式通知/mode_notice_contract.rs"]
mod contract;
