//! Billing lifecycle for one upstream model attempt.

use std::{
    io,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

use serde_json::Value;
use uuid::Uuid;

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
    review: Option<(String, String)>,
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
            review: None,
        }
    }

    pub(super) fn review(conversation_id: String) -> Self {
        Self::review_agent(conversation_id, "auto_approval")
    }

    pub(super) fn review_agent(conversation_id: String, name: &str) -> Self {
        Self {
            conversation_id,
            baseline: Tokens::default(),
            review: Some((format!("review_agent:{name}"), name.to_owned())),
        }
    }

    fn actor(&self) -> Actor<'_> {
        if let Some((id, name)) = &self.review {
            return Actor::new(id, name, "review_agent");
        }
        let mut actor = Actor::new("main", "", "main");
        actor.historical_unpriced = self.baseline.input_tokens > 0;
        actor.baseline = self.baseline;
        actor
    }
}

/// Done once for each completed upstream response. Dropping a failed or
/// cancelled attempt has no side effects. Calling this again keeps the same ID.
pub(super) struct RequestBilling {
    request_id: String,
    ledger: Ledger,
    scope: BillingScope,
    price: PriceSnapshot,
    responses: bool,
    began_at: Instant,
    first_output: Option<Instant>,
}

impl RequestBilling {
    pub(super) fn request_id(&self) -> &str {
        &self.request_id
    }

    pub(super) fn observe_text(&mut self, delta: &str) {
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
            ledger.record_request(
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
