use std::collections::BTreeMap;
use std::sync::LazyLock;
use std::time::Duration;

use astrion_headless::responses::{
    ResponsesProfile, ResponsesRequest, ResponsesSseParser, build_responses_body,
    sanitize_tool_arguments, should_send_prompt_cache_key, should_use_developer_prompt,
    tool_required_map,
};
use serde_json::{Value, json};

static FIXTURES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("responses_fixtures.json"))
        .expect("fixtures from responses_cases.py must be valid JSON")
});

fn array(value: &Value) -> &[Value] {
    value.as_array().expect("fixture must contain a JSON array")
}

#[test]
fn requests_match_python() {
    for case in array(&FIXTURES["requests"]) {
        let input = &case["input"];
        let mut request = ResponsesRequest::new(
            input["model_id"].as_str().unwrap(),
            array(&input["messages"]),
        );
        request.instructions = input["instructions"].as_str().unwrap();
        if let Some(tools) = input.get("tools") {
            request.tools = array(tools);
        }
        request.effort = input["effort"].as_str();
        if let Some(summary) = input["summary"].as_str() {
            request.summary = summary;
        }
        request.max_output_tokens = input["max_output_tokens"].as_i64();
        request.verbosity = input["verbosity"].as_str();
        request.session_id = input["session_id"].as_str();
        request.use_developer_prompt = input["use_developer_prompt"].as_bool().unwrap_or(false);

        let actual = build_responses_body(request);
        let expected = &case["expected"];
        assert_eq!(&actual, expected, "request {}", case["name"]);
        assert_eq!(
            actual.to_string(),
            expected.to_string(),
            "property order for request {}",
            case["name"],
        );
    }
}

#[test]
fn provider_policies_match_python() {
    for case in array(&FIXTURES["policies"]) {
        let input = &case["input"];
        let profile = ResponsesProfile {
            model_id: input["model_id"].as_str(),
            base_url: input["base_url"].as_str(),
            provider_id: input["provider_id"].as_str(),
            responses_auth: input["responses_auth"].as_str(),
        };
        assert_eq!(
            should_use_developer_prompt(&profile),
            case["developer"].as_bool().unwrap(),
            "developer prompt for {profile:?}",
        );
        assert_eq!(
            should_send_prompt_cache_key(&profile),
            case["cache"].as_bool().unwrap(),
            "cache key for {profile:?}",
        );
    }
}

#[test]
fn arguments_and_required_fields_match_python() {
    let required = tool_required_map(array(&FIXTURES["tools"]));
    let sorted: BTreeMap<_, Vec<_>> = required
        .iter()
        .map(|(name, fields)| {
            let mut fields: Vec<_> = fields.iter().collect();
            fields.sort_unstable();
            (name, fields)
        })
        .collect();
    assert_eq!(json!(sorted), FIXTURES["required"]);

    for case in array(&FIXTURES["argument_cases"]) {
        assert_eq!(
            sanitize_tool_arguments(case["name"].as_str(), &case["input"], &required),
            case["expected"].as_str().unwrap(),
            "arguments {}",
            case["input"],
        );
    }
}

#[test]
fn sse_chunks_reasoning_and_failures_match_python_after_each_line() {
    for case in array(&FIXTURES["streams"]) {
        let name = case["name"].as_str().unwrap();
        let mut parser = ResponsesSseParser::new(tool_required_map(array(&FIXTURES["tools"])));
        let expected_steps = array(&case["steps"]);
        let mut observed_error = Value::Null;
        let mut processed = 0;
        for (line_index, line) in array(&case["lines"]).iter().enumerate() {
            let at = Duration::from_secs_f64(line[0].as_f64().unwrap() / 1_000.0);
            let line = line[1].as_str().unwrap();
            processed += 1;
            match parser.push_line_at(line, at) {
                Ok(chunks) => assert_eq!(
                    chunks,
                    array(&expected_steps[line_index]),
                    "{name} line {line_index}"
                ),
                Err(error) => {
                    assert!(
                        array(&expected_steps[line_index]).is_empty(),
                        "{name} line {line_index}"
                    );
                    assert_eq!(parser.finish(), Err(error.clone()), "{name} terminal error");
                    observed_error =
                        json!({"line": line_index, "code": error.code, "message": error.message});
                    break;
                }
            }
        }
        assert_eq!(processed, expected_steps.len(), "{name} processed lines");
        if observed_error.is_null()
            && let Err(error) = parser.finish()
        {
            observed_error = json!({"line": null, "code": error.code, "message": error.message});
        }
        assert_eq!(observed_error, case["error"], "{name} failure");
        assert_eq!(
            parser.reasoning_items(),
            array(&case["reasoning"]),
            "{name} reasoning items"
        );
    }
}

#[test]
fn normal_line_entry_point_accepts_crlf_and_keeps_completion_at_eof() {
    let mut parser = ResponsesSseParser::default();
    assert_eq!(
        parser.finish().unwrap_err().code.as_deref(),
        Some("stream_incomplete")
    );
    assert_eq!(
        parser
            .push_line("data: {\"type\":\"response.created\"}\r")
            .unwrap(),
        vec![json!({"choices": [{"index": 0, "delta": {"role": "assistant"}}]})],
    );
    assert_eq!(
        parser
            .push_line("data: {\"type\":\"response.completed\"}")
            .unwrap(),
        vec![
            json!({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "_response_output_timing": {}})
        ],
    );
    assert!(parser.push_line("data: [DONE]").unwrap().is_empty());
    assert!(parser.is_completed());
    assert_eq!(parser.finish(), Ok(()));
}

#[test]
fn framed_sse_payload_can_span_multiple_data_lines() {
    let mut parser = ResponsesSseParser::default();
    let chunks = parser
        .push_data("{\n\"type\": \"response.output_text.delta\",\n\"delta\": \"split JSON\"\n}")
        .unwrap();
    assert_eq!(
        chunks,
        vec![json!({"choices": [{"index": 0, "delta": {"content": "split JSON"}}]})]
    );
    assert!(!parser.is_completed());
    parser
        .push_data("{\n\"type\": \"response.completed\"\n}")
        .unwrap();
    assert!(parser.is_completed());
    assert_eq!(parser.finish(), Ok(()));
}

#[test]
fn completion_preserves_observed_zero_usage_without_inventing_missing_usage() {
    for usage in [
        json!({}),
        json!({"unknown": 3}),
        json!({"input_tokens": null, "output_tokens": "unknown", "total_tokens": -1}),
        json!({"input_tokens_details": {"cached_tokens": 2}}),
        json!({"output_tokens_details": {"reasoning_tokens": "unknown"}}),
    ] {
        let chunks = ResponsesSseParser::default()
            .push_data(
                &json!({"type":"response.completed", "response":{"usage":usage}}).to_string(),
            )
            .unwrap();
        assert!(chunks.last().unwrap().get("usage").is_none(), "{usage}");
    }
    for usage in [
        json!({"input_tokens": 0}),
        json!({"output_tokens": 0}),
        json!({"total_tokens": 0}),
        json!({"output_tokens_details":{"reasoning_tokens": 0}}),
    ] {
        let chunks = ResponsesSseParser::default()
            .push_data(
                &json!({"type":"response.completed", "response":{"usage":usage}}).to_string(),
            )
            .unwrap();
        assert_eq!(
            chunks.last().unwrap()["usage"]["total_tokens"],
            0,
            "{usage}"
        );
    }
}
