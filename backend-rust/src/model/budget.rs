use serde_json::{Value, json};

use super::RequestOptions;

/// Tracks the effective input size across the requests of a single task.
#[derive(Default)]
pub(crate) struct RoundBudget {
    last_input: Option<u64>,
    last_estimate: Option<u64>,
}

impl RoundBudget {
    pub(crate) fn new(saved_input: Option<u64>) -> Self {
        Self {
            last_input: saved_input,
            last_estimate: None,
        }
    }

    pub(crate) fn observe_usage(&mut self, usage: Option<&Value>) {
        if let Some(input) = usage.and_then(|usage| {
            ["current_context_tokens", "prompt_tokens", "input_tokens"]
                .iter()
                .find_map(|field| usage[field].as_u64().filter(|tokens| *tokens > 0))
        }) {
            self.last_input = Some(input);
        }
    }

    pub(crate) fn apply(
        &mut self,
        options: &mut RequestOptions,
        window: u64,
        responses: bool,
        messages: &[Value],
        tools: &[Value],
    ) {
        let estimated = messages.iter().chain(tools).map(estimate_chars).sum::<u64>().div_ceil(4);
        // A saved conversation has the last upstream measurement, but no comparable
        // local estimate. Subsequent rounds can account for newly appended tool output.
        let input = match (self.last_input, self.last_estimate) {
            (Some(input), Some(previous)) => input.saturating_add(estimated.saturating_sub(previous)),
            (Some(input), None) => input.max(estimated),
            (None, _) => estimated,
        };
        self.last_input = Some(input);
        self.last_estimate = Some(estimated);

        let available = window.saturating_sub(input);
        let fields: &[&str] = if responses {
            &["max_output_tokens"]
        } else {
            &["max_tokens", "max_completion_tokens"]
        };
        let effective_field = fields.iter().copied().rev().find(|field| options.extra_params.contains_key(*field));
        if let Some(field) = effective_field {
            // An explicit provider override is the effective limit and must fit the same budget.
            let limit = &mut options.extra_params[field];
            if available == 0 {
                *limit = json!(1);
            } else if limit.as_u64().is_some_and(|tokens| tokens > available) {
                *limit = json!(available);
            }
            // Send a single Chat Completions limit when the provider chose the newer field.
            if field == "max_completion_tokens" {
                options.extra_params.shift_remove("max_tokens");
            }
        } else if available == 0 {
            options.max_output_tokens = Some(1);
        } else if let Some(configured) = options.max_output_tokens {
            options.max_output_tokens = Some(configured.min(available));
        }
    }
}

fn estimate_chars(value: &Value) -> u64 {
    match value {
        Value::String(text) => text.len() as u64,
        Value::Array(values) => values.iter().map(estimate_chars).sum(),
        Value::Object(fields) => fields.iter().map(|(key, value)| {
            // An inline media URL carries bytes rather than prompt text. Its cost comes from usage.
            let inline_media = key == "url" && value.as_str().is_some_and(|url| {
                url.starts_with("data:image/") || url.starts_with("data:video/")
            });
            key.len() as u64 + if inline_media { 0 } else { estimate_chars(value) }
        }).sum(),
        _ => 4,
    }
}
