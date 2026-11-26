//! Billing lifecycle for one upstream model attempt.

use std::{
    io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{ModelConfig, Runtime};

use crate::usage::{
    Actor, Ledger, PriceIdentity, PriceRefresh, PriceSnapshot, RequestTiming, SpeedSample, Tokens,
    calculate_cost,
};

#[derive(Clone)]
pub(super) struct Billing {
    data_dir: PathBuf,
    ledger: Ledger,
    pricing: Option<PriceRefresh>,
}

impl Billing {
    pub(super) fn new(data_dir: &Path) -> Self {
        Self {
            data_dir: data_dir.to_owned(),
            ledger: Ledger::new(data_dir),
            pricing: None,
        }
    }

    pub(super) fn configure_pricing(&mut self, config_dir: &Path) {
        self.pricing = Some(PriceRefresh::new(&self.data_dir, config_dir));
    }

    /// Freeze selected provider/model price and allocate an ID before dispatch.
    pub(super) async fn begin(
        &self,
        scope: BillingScope,
        lookup: ModelPrice<'_>,
    ) -> RequestBilling {
        let price = match &self.pricing {
            Some(pricing) => {
                pricing
                    .capture(lookup.model_key, lookup.model_id, lookup.identity)
                    .await
            }
            None => PriceSnapshot {
                model_key: lookup.model_key.to_owned(),
                provider_id: lookup.identity.provider_id.clone(),
                model_id: Some(lookup.model_id.to_owned()),
                modelsdev_key: lookup.identity.modelsdev_key.clone(),
                ..PriceSnapshot::default()
            },
        };
        RequestBilling {
            request_id: Uuid::new_v4().to_string(),
            ledger: self.ledger.clone(),
            scope,
            price,
            responses: lookup.responses,
            began_at: Instant::now(),
            first_output: None,
            goal_run: None,
        }
    }
}

pub(super) struct ModelPrice<'a> {
    pub model_key: &'a str,
    pub model_id: &'a str,
    pub identity: &'a PriceIdentity,
    pub responses: bool,
}

#[derive(Clone)]
pub(super) struct BillingScope {
    conversation_id: String,
    baseline: Tokens,
    actor: Option<(String, String, &'static str)>,
}

impl BillingScope {
    /// The baseline has effect only when the conversation has no main actor
    /// yet. Use the conversation totals before applying this request's usage.
    pub(super) fn main(conversation_id: String, previous: &Value) -> Self {
        let input_tokens = previous["total_input_tokens"].as_u64().unwrap_or_default();
        let output_tokens = previous["total_output_tokens"].as_u64().unwrap_or_default();
        let baseline = Tokens {
            input_tokens,
            output_tokens,
            cached_input_tokens: previous["total_cached_input_tokens"]
                .as_u64()
                .unwrap_or_default(),
            total_tokens: previous["total_tokens"]
                .as_u64()
                .unwrap_or_else(|| input_tokens.saturating_add(output_tokens)),
            current_context_tokens: previous["current_context_tokens"]
                .as_u64()
                .unwrap_or_default(),
        };
        Self {
            conversation_id,
            baseline,
            actor: None,
        }
    }

    pub(super) fn review(conversation_id: String) -> Self {
        Self::review_agent(conversation_id, "auto_approval")
    }

    pub(super) fn review_agent(conversation_id: String, name: &str) -> Self {
        Self {
            conversation_id,
            baseline: Tokens::default(),
            actor: Some((
                format!("review_agent:{name}"),
                name.to_owned(),
                "review_agent",
            )),
        }
    }

    pub(super) fn thinking_summary(conversation_id: String) -> Self {
        Self {
            conversation_id,
            baseline: Tokens::default(),
            actor: Some(("thinking_summary".into(), String::new(), "thinking_summary")),
        }
    }

    fn sub_agent(conversation_id: String, id: &str, name: &str, previous: &Value) -> Self {
        let usage = &previous["token_usage"];
        let input = usage["prompt"].as_u64().unwrap_or_default();
        let output = usage["completion"].as_u64().unwrap_or_default();
        Self {
            conversation_id,
            actor: Some((id.to_owned(), name.to_owned(), "sub_agent")),
            baseline: Tokens {
                input_tokens: input,
                output_tokens: output,
                cached_input_tokens: usage["cached_input"].as_u64().unwrap_or_default(),
                total_tokens: usage["total"]
                    .as_u64()
                    .unwrap_or_else(|| input.saturating_add(output)),
                current_context_tokens: previous["current_context_tokens"]
                    .as_u64()
                    .unwrap_or_default(),
            },
        }
    }

    fn actor(&self) -> Actor<'_> {
        let mut actor = self.actor.as_ref().map_or_else(
            || Actor::new("main", "", "main"),
            |(id, name, kind)| Actor::new(id, name, kind),
        );
        actor.historical_unpriced = self.baseline.input_tokens > 0
            || (actor.kind == "sub_agent" && self.baseline.total_tokens > 0);
        actor.baseline = self.baseline;
        actor
    }
}

/// Done once for each completed upstream response. Dropping a failed or
/// cancelled attempt has no side effects. Calling this again keeps the same ID.
pub(crate) struct RequestBilling {
    request_id: String,
    ledger: Ledger,
    scope: BillingScope,
    price: PriceSnapshot,
    responses: bool,
    began_at: Instant,
    first_output: Option<Instant>,
    goal_run: Option<String>,
}

impl RequestBilling {
    pub(super) fn request_id(&self) -> &str {
        &self.request_id
    }

    pub(crate) fn observe_text(&mut self, delta: &str) {
        if !delta.is_empty() {
            self.first_output.get_or_insert_with(Instant::now);
        }
    }

    /// A missing usage object on a successful response counts as unpriced.
    /// The detached blocking write still completes if its waiter is cancelled.
    pub(super) async fn settle_success(
        &self,
        usage: Option<&Value>,
        responses_duration_ms: Option<u64>,
    ) -> io::Result<bool> {
        self.settle_with_actor(usage, responses_duration_ms)
            .await
            .map(|actor| actor.is_some())
    }

    async fn settle_with_actor(
        &self,
        usage: Option<&Value>,
        responses_duration_ms: Option<u64>,
    ) -> io::Result<Option<Value>> {
        let entry = calculate_cost(usage, self.price.clone());
        let speed_sample = self
            .responses
            .then(|| responses_speed(usage, entry.tokens.output_tokens, responses_duration_ms));
        let timing = RequestTiming {
            duration_ms: Some(milliseconds(
                self.first_output.unwrap_or(self.began_at).elapsed(),
            )),
            ttft_ms: self
                .first_output
                .map(|first| milliseconds(first.duration_since(self.began_at))),
            speed_sample,
        };
        let ledger = self.ledger.clone();
        let scope = self.scope.clone();
        let request_id = self.request_id.clone();
        tokio::task::spawn_blocking(move || {
            ledger.record_request_with_actor(
                &scope.conversation_id,
                &request_id,
                &scope.actor(),
                &entry,
                &timing,
            )
        })
        .await
        .map_err(io::Error::other)?
    }
}

impl Runtime {
    pub(crate) async fn begin_child_billing(
        &self,
        parent: &str,
        child: &str,
        name: &str,
        stats: &Value,
        model: &ModelConfig,
    ) -> RequestBilling {
        let responses = reqwest::Url::parse(&model.endpoint).is_ok_and(|url| {
            url.path().trim_end_matches('/').rsplit('/').next() == Some("responses")
        });
        let scope = BillingScope::sub_agent(parent.to_owned(), child, name, stats);
        let mut request = self
            .billing
            .begin(
                scope,
                ModelPrice {
                    model_key: model.key(),
                    model_id: model.model_id(),
                    identity: model.price_identity(),
                    responses,
                },
            )
            .await;
        let goals = self.goals.clone();
        let parent = parent.to_owned();
        request.goal_run = tokio::task::spawn_blocking(move || {
            let progress = goals.progress(&parent).ok()?;
            matches!(
                progress["status"].as_str(),
                Some("running" | "reviewing" | "retrying")
            )
            .then(|| progress["run_id"].as_str().map(str::to_owned))
            .flatten()
        })
        .await
        .ok()
        .flatten();
        request
    }

    pub(crate) async fn settle_child_billing(
        &self,
        billing: &RequestBilling,
        usage: Option<&Value>,
        responses_duration_ms: Option<u64>,
    ) -> Result<Option<Value>, String> {
        let actor = billing
            .settle_with_actor(usage, responses_duration_ms)
            .await
            .map_err(|error| format!("无法记录子智能体费用：{error}"))?;
        if actor.is_some()
            && let Some(run) = &billing.goal_run
            && let Some(usage) = usage.and_then(crate::usage::normalize_usage)
        {
            let goals = self.goals.clone();
            let (parent, run, request_id) = (
                billing.scope.conversation_id.clone(),
                run.clone(),
                billing.request_id.clone(),
            );
            tokio::task::spawn_blocking(move || {
                goals.record_conversation_usage(&parent, &run, &request_id, usage.total_tokens)
            })
            .await
            .map_err(|error| error.to_string())?
            .map_err(|error| error.message)?;
        }
        Ok(actor)
    }

    pub(crate) async fn recover_child_billing(
        &self,
        parent: &str,
        child: &str,
        stats: &mut Value,
    ) -> io::Result<()> {
        let ledger = self.billing.ledger.clone();
        let (parent, child) = (parent.to_owned(), child.to_owned());
        let actor = tokio::task::spawn_blocking(move || ledger.actor_statistics(&parent, &child))
            .await
            .map_err(io::Error::other)??;
        let Some(actor) = actor.filter(|actor| actor["kind"] == "sub_agent") else {
            return Ok(());
        };
        if !stats.is_object() {
            *stats = json!({});
        }
        if let Some(total) = actor["total_tokens"]
            .as_u64()
            .filter(|total| *total > stats["token_usage"]["total"].as_u64().unwrap_or_default())
        {
            stats["token_usage"] = json!({"prompt":actor["input_tokens"],"completion":actor["output_tokens"],
                "cached_input":actor["cached_input_tokens"],"total":total});
            if let Some(context) = actor["current_context_tokens"].as_u64() {
                stats["current_context_tokens"] = json!(context);
            }
        }
        if let Some(requests) = actor["requests"].as_u64() {
            let calls = stats["api_calls"].as_u64().unwrap_or_default();
            let recovered = stats["cost_ledger_requests"].as_u64().map_or_else(
                || calls.max(requests),
                |tracked| calls.saturating_add(requests.saturating_sub(tracked)),
            );
            stats["api_calls"] = json!(recovered);
            stats["cost_ledger_requests"] = json!(requests);
        }
        stats["cost_usd"] = actor["cost_usd"].clone();
        Ok(())
    }
}

fn responses_speed(usage: Option<&Value>, output: u64, duration_ms: Option<u64>) -> SpeedSample {
    let Some(duration_ms) = duration_ms.filter(|ms| *ms > 0) else {
        return SpeedSample::default();
    };
    let Some(usage) = usage else {
        return SpeedSample::default();
    };
    let reasoning = usage["completion_tokens_details"]["reasoning_tokens"]
        .as_u64()
        .or_else(|| usage["output_tokens_details"]["reasoning_tokens"].as_u64())
        .or_else(|| usage["reasoning_tokens"].as_u64());
    let Some(tokens) = reasoning
        .and_then(|reasoning| output.checked_sub(reasoning))
        .filter(|tokens| *tokens > 0)
    else {
        return SpeedSample::default();
    };
    SpeedSample {
        output_tokens: Some(tokens),
        duration_ms: Some(duration_ms),
    }
}

fn milliseconds(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

#[cfg(test)]
#[path = "../../../test/2026-11-24_Rust后端/usage_runtime_billing.rs"]
mod tests;

#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust子智能体费用/billing.rs"]
mod child_tests;
