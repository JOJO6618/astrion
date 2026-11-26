use super::{Result, ReviewDecision, ReviewMode, ReviewRequest, WorkflowError};
use crate::{catalog::SelectedModel, runtime::ModelConfig};
use serde_json::{Value, json};
use std::{future::Future, path::Path, pin::Pin, time::Duration};
use uuid::Uuid;

const REVIEW_SYSTEM_PROMPT: &str = include_str!("../../../prompts/workflow_review_agent.txt");
const START: &str = "{{ACTIVE_REVIEW_ONLY}}";
const END: &str = "{{/ACTIVE_REVIEW_ONLY}}";
const REPORT: &str = "report_workflow_review";
const COMMAND: &str = "run_command";

/// A host-supplied evidence executor. It must enforce read-only filesystem and
/// restricted-network policy inside the selected execution root.
pub trait ReviewEvidence: Send + Sync + 'static {
    fn run<'a>(
        &'a self,
        workspace: &'a Path,
        command: &'a str,
    ) -> Pin<Box<dyn Future<Output = Value> + Send + 'a>>;
}

/// Send a separate request with only the reviewer tools. The main assistant's
/// summary can supply evidence but cannot create a reviewer verdict.
pub async fn run_model_review<Bill, Begin, Settle>(
    request: &ReviewRequest,
    model: &SelectedModel,
    max_rounds: usize,
    workspace: &Path,
    evidence: Option<&dyn ReviewEvidence>,
    progress: impl FnMut(Value) -> Result<()>,
    billing: (
        impl FnMut() -> Begin,
        impl FnMut(Bill, Option<Value>, Option<u64>) -> Settle,
    ),
) -> Result<ReviewDecision>
where
    Begin: Future<Output = Bill> + Send,
    Settle: Future<Output = Result<()>> + Send,
    Bill: Send,
{
    run_recorded_review(request,model,max_rounds,workspace,evidence,progress,billing,|_,_| Ok(())).await
}

pub(crate) async fn run_recorded_review<Bill, Begin, Settle>(
    request: &ReviewRequest,
    model: &SelectedModel,
    max_rounds: usize,
    workspace: &Path,
    evidence: Option<&dyn ReviewEvidence>,
    mut progress: impl FnMut(Value) -> Result<()>,
    (mut begin, mut settle): (
        impl FnMut() -> Begin,
        impl FnMut(Bill, Option<Value>, Option<u64>) -> Settle,
    ),
    trace: impl Fn(&[Value],usize) -> Result<()>,
) -> Result<ReviewDecision>
where
    Begin: Future<Output = Bill> + Send,
    Settle: Future<Output = Result<()>> + Send,
    Bill: Send,
{
    if request.mode == ReviewMode::Active && evidence.is_none() {
        return Err(WorkflowError::new(
            503,
            "工作流审核的只读取证执行器不可用。",
        ));
    }
    let model = match ModelConfig::from_catalog(model.clone()) {
        Ok(model) => model.with_session(&Uuid::new_v4().to_string(), false),
        Err(error) => return Ok(ReviewDecision::Unavailable(error)),
    };
    let active = request.mode == ReviewMode::Active;
    let mut tools = vec![report_schema()];
    if active {
        tools.insert(0, command_schema());
    }
    let mut messages = vec![
        json!({"role":"system", "content":system_prompt(active)}),
        json!({"role":"user", "content":request.payload}),
    ];
    trace(&messages,1)?;
    progress(request.progress.clone())?;
    for round in 1..=max_rounds.max(1) {
        progress(progress_event(
            request,
            json!({
                "stage":"model_call", "round":round, "message":format!("审核模型调用第 {round} 轮"),
            }),
        ))?;
        let mut attempt = 1;
        let completion = loop {
            let billing = begin().await;
            match model.complete(&messages, &tools, |_| {}).await {
                Ok(completion) => {
                    settle(billing, completion.usage, completion.responses_duration_ms).await?;
                    break completion.message;
                }
                Err(error) if attempt < 5 && retryable(&error) => {
                    attempt += 1;
                    tokio::time::sleep(Duration::from_secs(10)).await;
                }
                Err(error) => return Ok(ReviewDecision::Unavailable(error)),
            }
        };
        trace(std::slice::from_ref(&completion),round)?;
        let calls = completion.get("tool_calls").and_then(Value::as_array);
        let mut tool_results = Vec::new();
        if let Some(calls) = calls.filter(|calls| !calls.is_empty()) {
            for call in calls {
                let function = &call["function"];
                let arguments = function["arguments"]
                    .as_str()
                    .and_then(|text| serde_json::from_str::<Value>(text).ok())
                    .unwrap_or_default();
                match function["name"].as_str() {
                    Some(REPORT) => return Ok(verdict(&arguments)),
                    Some(COMMAND) if active => {
                        let exec =
                            evidence.expect("active workflow review requires an evidence adapter");
                        let command = arguments["command"].as_str().unwrap_or_default().trim();
                        let id = call["id"]
                            .as_str()
                            .ok_or_else(|| WorkflowError::new(503, "审核工具调用缺少 ID。"))?;
                        progress(progress_event(
                            request,
                            json!({"stage":"run_command", "round":round, "command":command}),
                        ))?;
                        let value = if command.is_empty() {
                            json!({"success":false, "error":"command 不能为空"})
                        } else {
                            match tokio::time::timeout(
                                Duration::from_secs(60),
                                exec.run(workspace, command),
                            )
                            .await
                            {
                                Ok(result) => result,
                                Err(_) => json!({"success":false, "error":"审核取证命令超时"}),
                            }
                        };
                        tool_results.push(json!({"role":"tool", "name":COMMAND, "tool_call_id":id, "content":value.to_string()}));
                    }
                    _ => {
                        return Ok(ReviewDecision::Unavailable(
                            "审核模型请求了不可用的工具。".into(),
                        ));
                    }
                }
            }
            messages.push(completion);
            trace(&tool_results,round)?;
            messages.extend(tool_results);
        } else {
            messages.push(completion);
            let reminder = json!({"role":"user", "content":"请通过 report_workflow_review 提交明确审核结论。需要进一步取证时，请先使用可用的取证工具。"});
            trace(std::slice::from_ref(&reminder),round)?;
            messages.push(reminder);
        }
    }
    Ok(ReviewDecision::Unavailable(
        "审核达到轮次上限，未产出有效结论。".into(),
    ))
}

fn retryable(error: &str) -> bool {
    let error = error.to_ascii_lowercase();
    if [
        "insufficient_quota",
        "usage_limit",
        "subscription usage limit",
        "quota_exceeded",
        "billing_hard_limit",
        "insufficient balance",
        "invalid_api_key",
    ]
    .iter()
    .any(|marker| error.contains(marker))
    {
        return false;
    }
    if let Some(status) = error
        .split_once(" http ")
        .and_then(|(_, suffix)| suffix.split_whitespace().next())
        .and_then(|code| code.parse::<u16>().ok())
    {
        return matches!(status, 408 | 409 | 425 | 429 | 500..=599);
    }
    [
        "rate_limit",
        "timed out",
        "timeout",
        "connection",
        "stream read failed",
        "stream ended before",
    ]
    .iter()
    .any(|marker| error.contains(marker))
}

fn verdict(arguments: &Value) -> ReviewDecision {
    let decision = arguments["decision"]
        .as_str()
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    let message = arguments["message"].as_str().unwrap_or_default().trim();
    match decision.as_str() {
        "pass" if !message.is_empty() => ReviewDecision::Pass(message.into()),
        "pass" => ReviewDecision::Pass("审核通过。".into()),
        "reject" if !message.is_empty() => ReviewDecision::Reject(message.into()),
        "reject" => ReviewDecision::Reject("审核未通过，请补充能验证阶段目标已达到的证据。".into()),
        _ => ReviewDecision::Unavailable("审核未产出有效结论。".into()),
    }
}

fn progress_event(request: &ReviewRequest, data: Value) -> Value {
    json!({"conversation_id": request.progress["conversation_id"], "progress": data})
}

fn system_prompt(active: bool) -> String {
    if active {
        return REVIEW_SYSTEM_PROMPT
            .replace(START, "")
            .replace(END, "")
            .trim()
            .into();
    }
    let mut text = REVIEW_SYSTEM_PROMPT.to_owned();
    while let Some((start, suffix)) = text.split_once(START) {
        let Some((_, end)) = suffix.split_once(END) else {
            break;
        };
        text = format!("{start}{end}");
    }
    text.trim().into()
}

fn report_schema() -> Value {
    json!({"type":"function", "function":{
        "name":REPORT,
        "description":"提交工作流阶段审核结论并结束本次审核。",
        "parameters":{"type":"object", "properties":{
            "decision":{"type":"string", "enum":["pass", "reject"]},
            "message":{"type":"string", "description":"通过理由或可执行的整改意见。"},
        }, "required":["decision", "message"]}
    }})
}

fn command_schema() -> Value {
    json!({"type":"function", "function":{
        "name":COMMAND,
        "description":"执行受限的只读取证命令。",
        "parameters":{"type":"object", "properties":{"command":{"type":"string"}}, "required":["command"]}
    }})
}
