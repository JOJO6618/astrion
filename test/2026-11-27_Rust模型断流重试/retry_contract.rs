use super::Attempt;

#[test]
fn retries_transient_failures_with_bounded_attempts() {
    let first = Attempt::new(1, true);
    for error in [
        "Chat Completions HTTP 503 Service Unavailable: gateway",
        "Responses HTTP 429 Too Many Requests: rate_limit",
        "Chat Completions stream read failed: error decoding response body",
        "Chat Completions stream ended before [DONE] or a successful finish reason",
        "Responses stream error (stream_incomplete): Responses 响应流中断",
        "Chat Completions request failed: connect: error sending request",
    ] {
        assert!(first.should_retry(error), "{error}");
        assert!(Attempt::new(9, true).should_retry(error), "{error}");
        assert!(!Attempt::new(10, true).should_retry(error), "{error}");
        assert!(!Attempt::new(1, false).should_retry(error), "{error}");
    }
    assert_eq!(first.start_event()["max_attempts"], 10);
    assert_eq!(first.reset_event()["attempt"], 2);
    assert_eq!(first.error_event("fixture")["retry_in"], 10);
}

#[test]
fn retries_codex_oauth_connect_errors_and_tls_handshake_eof() {
    let first = Attempt::new(1, true);
    for error in [
        "request_error: connect: error sending request for url (https://chatgpt.com/backend-api/codex/responses) | client error (Connect) | tls handshake eof",
        "request_error: connect: error sending request | client error (Connect)",
        "request_error: request: TLS handshake EOF",
    ] {
        assert!(first.should_retry(error), "{error}");
    }
}

#[test]
fn authentication_configuration_and_account_limits_do_not_retry() {
    let first = Attempt::new(1, true);
    for error in [
        "Codex Responses HTTP 429 (subscription usage limit): rate_limit",
        "Chat Completions HTTP 401 Unauthorized: timeout",
        "Chat Completions HTTP 400 Bad Request: connection option invalid",
        "Codex Responses HTTP 401 Unauthorized: tls handshake eof",
        "Responses HTTP 418: connection option invalid",
        "request_error: empty session id",
        "Responses expected an SSE stream (HTTP 200 OK): unexpected body",
        "invalid Chat Completions SSE chunk: delimiter",
    ] {
        assert!(!first.should_retry(error), "{error}");
    }
}
