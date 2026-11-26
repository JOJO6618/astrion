use std::{path::Path, sync::Arc, time::{Duration, Instant}};

use serde_json::{Value, json};
use uuid::Uuid;

use crate::{
    catalog::CatalogSource,
    chat::Completion,
    runtime::ModelConfig,
    usage::{Actor, Ledger, PriceRefresh, RequestTiming, calculate_cost},
    workflows::ReviewEvidence,
};

use super::{GoalError, GoalFailure, Goals, ReviewMode};

const SYSTEM_PROMPT: &str = include_str!("../../../prompts/goal_review_agent.txt");
const ACTIVE_START: &str = "{{ACTIVE_REVIEW_ONLY}}";
const ACTIVE_END: &str = "{{/ACTIVE_REVIEW_ONLY}}";
const REPORT: &str = "report_goal_status";
const COMMAND: &str = "run_command";
const ATTEMPTS: u16 = 5;

#[derive(Clone)]
pub struct GoalReviewer {
    goals: Goals,
    catalog: CatalogSource,
    evidence: Option<Arc<dyn ReviewEvidence>>,
    pricing: PriceRefresh,
}

#[derive(Debug)]
pub(super) enum Verdict {
    Done(String),
    Continue(String),
    Blocked { reason: &'static str, message: String },
}

pub(super) struct ReviewRequest<'a> {
    pub conversation_id: &'a str,
    pub workspace: &'a Path,
    pub mode: ReviewMode,
    pub payload: &'a str,
}

impl GoalReviewer {
    pub fn new(goals: Goals, catalog: CatalogSource, pricing_config: &Path) -> Self {
        let pricing = PriceRefresh::new(goals.store.data_dir(), pricing_config);
        Self { goals, catalog, evidence: None, pricing }
    }

    /// The shared Host adapter must enforce an independent read-only sandbox.
    /// A main task's permission level is never passed to this adapter.
    pub fn with_evidence(mut self, evidence: Arc<dyn ReviewEvidence>) -> Self {
        self.evidence = Some(evidence);
        self
    }

    pub(super) async fn evaluate(
        &self,
        request: ReviewRequest<'_>,
        progress: impl Fn(&str, Value) -> Result<(), GoalError>,
    ) -> Result<Result<Verdict, GoalFailure>, GoalError> {
        let record=crate::conversation_db::reviews::Record::begin(self.goals.store.data_dir(),"goal_review",request.conversation_id,"goal_turn",request.workspace,true).map_err(GoalError::storage)?;
        record.event(&json!({"request":request.payload})).map_err(GoalError::storage)?;
        let result=self.evaluate_inner(request,&record,|kind,event|{
            record.event(&event).map_err(GoalError::storage)?;
            progress(kind,event)
        }).await;
        let (outcome,detail)=match &result {
            Ok(Ok(Verdict::Done(text)))=>("done",json!(text)),
            Ok(Ok(Verdict::Continue(text)))=>("continue",json!(text)),
            Ok(Ok(Verdict::Blocked{reason,message}))=>("blocked",json!({"reason":reason,"message":message})),
            Ok(Err(error))=>("error",json!(error.message)),Err(error)=>("error",json!(error.message)),
        };
        record.finish(outcome,&detail).map_err(GoalError::storage)?;
        result
    }

    async fn evaluate_inner(&self,request:ReviewRequest<'_>,record:&crate::conversation_db::reviews::Record,progress:impl Fn(&str,Value)->Result<(),GoalError>)->Result<Result<Verdict,GoalFailure>,GoalError>{
        let active = request.mode == ReviewMode::Active;
        if active && self.evidence.is_none() {
            return Ok(Ok(Verdict::Blocked { reason: "configuration", message: "目标审核的只读取证执行器不可用。".into() }));
        }
        let (model, max_rounds) = match self.model_profile() {
            Ok(settings) => settings,
            Err(message) => return Ok(Ok(Verdict::Blocked { reason: "configuration", message })),
        };
        let model = model.with_session(&Uuid::new_v4().to_string(), false);
        let mut tools = vec![report_tool()];
        if active { tools.insert(0, command_tool()); }
        let mut messages = vec![
            json!({"role":"system","content":system_prompt(active)}),
            json!({"role":"user","content":request.payload}),
        ];
        let emit = |data: Value| progress("goal_review_progress", json!({"conversation_id":request.conversation_id,"progress":data}));
        let mut saved = 0;
        for round in 1..=max_rounds {
            record.messages(&messages[saved..],round as usize).map_err(GoalError::storage)?;
            saved = messages.len()+1;
            emit(json!({"stage":"model_call","round":round,"message":format!("审核模型调用第 {round} 轮")}))?;
            let completion = match self.complete(request.conversation_id, &model, &messages, &tools).await {
                Ok(completion) => completion,
                Err(error) => return Ok(Err(error)),
            };
            record.messages(std::slice::from_ref(&completion.message),round as usize).map_err(GoalError::storage)?;
            let calls = completion.message["tool_calls"].as_array().filter(|calls| !calls.is_empty());
            if let Some(calls) = calls {
                let mut results = Vec::new();
                for call in calls {
                    let function = &call["function"];
                    let args = match &function["arguments"] {
                        Value::String(text) => serde_json::from_str(text).unwrap_or_default(),
                        object @ Value::Object(_) => object.clone(),
                        _ => Value::Null,
                    };
                    match function["name"].as_str() {
                        Some(REPORT) => return Ok(Ok(verdict(&args))),
                        Some(COMMAND) if active => {
                            let Some(id) = call["id"].as_str() else { return Ok(Ok(invalid_review())); };
                            let command = args["command"].as_str().unwrap_or_default().trim();
                            emit(json!({"stage":"run_command","round":round,"command":command}))?;
                            let evidence = self.evidence.as_deref().expect("active reviewer has checked evidence");
                            let value = if command.is_empty() {
                                json!({"success":false,"error":"command 不能为空"})
                            } else {
                                match tokio::time::timeout(Duration::from_secs(60), evidence.run(request.workspace, command)).await {
                                    Ok(result) => result,
                                    Err(_) => json!({"success":false,"error":"审核取证命令超时"}),
                                }
                            };
                            results.push(json!({"role":"tool","name":COMMAND,"tool_call_id":id,"content":value.to_string()}));
                        }
                        _ => return Ok(Ok(invalid_review())),
                    }
                }
                messages.push(completion.message);
                messages.extend(results);
            } else {
                messages.push(completion.message);
                messages.push(json!({"role":"user","content":"必须调用 report_goal_status 返回审核结论。需要取证时，请使用可用的取证工具。"}));
            }
        }
        Ok(Ok(Verdict::Blocked { reason: "review_invalid", message: format!("目标审核达到 {max_rounds} 轮，未产出有效结论。") }))
    }

    fn model_profile(&self) -> Result<(ModelConfig, u64), String> {
        let settings = self.goals.personalization();
        let config = &settings["review_agents"]["goal_review"];
        let rounds = config["max_rounds"].as_u64().filter(|rounds| *rounds != 0).unwrap_or(10);
        let catalog = self.catalog.load();
        let explicit = config["model"].as_str().map(str::trim).filter(|model| !model.is_empty());
        let public = catalog.models_response();
        let default = public["items"].as_array().and_then(|items| {
            let first = items.first()?;
            let is_codex = |item: &Value| item["provider_id"].as_str().is_some_and(|id| id == "openai-codex" || id.starts_with("openai-codex-"));
            if items.iter().all(is_codex) {
                return items.iter().find(|item| item["model_key"].as_str().is_some_and(|key| key.contains("-luna")))
                    .unwrap_or(first)["model_key"].as_str();
            }
            first["model_key"].as_str()
        });
        let key = explicit.or(default).ok_or("未配置目标审核智能体的模型。")?;
        let model = ModelConfig::from_catalog(catalog.select(Some(key), true)?)?;
        Ok((model, rounds))
    }

    async fn complete(
        &self, conversation_id: &str, model: &ModelConfig,
        messages: &[Value], tools: &[Value],
    ) -> Result<Completion, GoalFailure> {
        for attempt in 1..=ATTEMPTS {
            let request_id = Uuid::new_v4().to_string();
            let price = self.pricing.capture(model.key(), model.model_id(), model.price_identity()).await;
            let began = Instant::now();
            let mut first_output = None;
            let result = model.complete(messages, tools, |delta| {
                if !delta.is_empty() { first_output.get_or_insert_with(Instant::now); }
            }).await;
            if result.is_ok() || first_output.is_some() {
                let usage = result.as_ref().ok().and_then(|completion| completion.usage.as_ref());
                let entry = calculate_cost(usage, price);
                let timing = RequestTiming {
                    duration_ms: first_output.unwrap_or(began).elapsed().as_millis().try_into().ok(),
                    ttft_ms: first_output.and_then(|first| first.duration_since(began).as_millis().try_into().ok()),
                    ..RequestTiming::default()
                };
                let ledger = Ledger::new(self.goals.store.data_dir());
                let conversation_id = conversation_id.to_owned();
                let _ = tokio::task::spawn_blocking(move || ledger.record_request(
                    &conversation_id, &request_id, &Actor::new("review_agent:goal_review", "goal_review", "review_agent"), &entry, &timing,
                )).await;
            }
            match result {
                Ok(completion) => return Ok(completion),
                Err(message) => {
                    let failure = GoalFailure::from_message(message);
                    if failure.reason() != "retryable" || attempt == ATTEMPTS { return Err(failure); }
                    tokio::time::sleep(Duration::from_secs(10)).await;
                }
            }
        }
        unreachable!("final reviewer attempt returns a terminal result")
    }
}

fn invalid_review() -> Verdict {
    Verdict::Blocked { reason: "review_invalid", message: "审核未返回有效结论。".into() }
}

fn verdict(arguments: &Value) -> Verdict {
    let message = arguments["message"].as_str().unwrap_or_default().trim();
    let message = |fallback: &str| if message.is_empty() { fallback.to_owned() } else { message.to_owned() };
    match arguments["status"].as_str().unwrap_or_default().trim().to_ascii_lowercase().as_str() {
        "done" => Verdict::Done(message("目标已达成。")),
        "continue" => Verdict::Continue(message("请根据原始目标和实际进展继续。")),
        "blocked" => Verdict::Blocked { reason: "goal_blocked", message: message("当前无法继续推进目标。") },
        _ => invalid_review(),
    }
}

fn system_prompt(active: bool) -> String {
    if active {
        return SYSTEM_PROMPT.replace(ACTIVE_START, "").replace(ACTIVE_END, "").trim().into();
    }
    let (head, active) = SYSTEM_PROMPT.split_once(ACTIVE_START).expect("review prompt markers");
    let (_, tail) = active.split_once(ACTIVE_END).expect("review prompt markers");
    format!("{head}{tail}").trim().into()
}

fn report_tool() -> Value {
    json!({"type":"function","function":{
        "name":REPORT,"description":"提交目标审核结论并结束本次审核。",
        "parameters":{"type":"object","properties":{
            "status":{"type":"string","enum":["done","continue","blocked"]},
            "message":{"type":"string","description":"完成依据、具体下一步或阻塞原因。"}},
            "required":["status","message"]}
    }})
}

fn command_tool() -> Value {
    json!({"type":"function","function":{
        "name":COMMAND,"description":"在独立只读沙箱中核查文件、状态与差异。",
        "parameters":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}
    }})
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/goal_reviewer_contract.rs"]
mod tests;
