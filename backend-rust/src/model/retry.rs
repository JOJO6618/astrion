//! Retries only the upstream model request, before assistant/tool history is committed.
use std::time::Duration;

use serde_json::{Value, json};

use crate::goals::GoalFailure;

const MAX_ATTEMPTS: u16 = 5;
const DELAY: Duration = Duration::from_secs(10);

pub(crate) struct Attempt {
    number: u16,
    limit: u16,
}

impl Attempt {
    pub(crate) fn new(number: u16, enabled: bool) -> Self {
        Self { number, limit: if enabled { MAX_ATTEMPTS } else { 1 } }
    }

    pub(crate) fn should_retry(&self, error: &str) -> bool {
        if self.number >= self.limit {
            return false;
        }
        let failure = GoalFailure::from_message(error);
        match failure.reason() {
            "retryable" => true,
            "unknown_error" if failure.status_code.is_none() => {
                let error = error.to_ascii_lowercase();
                error.contains("stream read failed:")
                    || error.contains("stream ended before [done]")
                    || error.starts_with("responses stream error (stream_incomplete):")
                    || error.contains(" request failed: connect:")
                    || error.contains(" request failed: body:")
                    || error.contains(" request failed: transport:")
            }
            _ => false,
        }
    }

    pub(crate) fn start_event(&self) -> Value {
        json!({"attempt":self.number,"max_attempts":self.limit})
    }

    pub(crate) fn error_event(&self, error: &str) -> Value {
        json!({"message":error,"retry":true,"fatal":false,"retry_in":DELAY.as_secs(),
            "attempt":self.number,"max_attempts":self.limit})
    }

    pub(crate) fn reset_event(&self) -> Value {
        json!({"reason":"stream_disconnected","attempt":self.number + 1,
            "max_attempts":self.limit,"retry_in":DELAY.as_secs()})
    }

    pub(crate) async fn wait(&self) {
        // The parent worker owns the cancellation select, even during backoff.
        tokio::time::sleep(DELAY).await;
    }
}

#[cfg(test)]
#[path = "../../../test/2026-11-27_Rust模型断流重试/retry_contract.rs"]
mod tests;
