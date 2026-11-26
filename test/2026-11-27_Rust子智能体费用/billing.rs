use std::{fs, path::PathBuf};

use serde_json::json;
use uuid::Uuid;

use super::{Billing, BillingScope, ModelConfig, ModelPrice, Runtime};
use crate::usage::{Ledger, PriceIdentity, Rates};

struct Fixture(PathBuf);

impl Fixture {
    fn new() -> Self {
        let directory =
            std::env::temp_dir().join(format!("astrion-child-billing-{}", Uuid::new_v4()));
        fs::create_dir(&directory).unwrap();
        Self(directory)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[tokio::test]
async fn priced_child_requests_share_the_parent_ledger_and_restore_historical_stats_once() {
    let fixture = Fixture::new();
    let billing = Billing::new(&fixture.0);
    let identity = PriceIdentity::default();
    let model = || ModelPrice {
        model_key: "fixture-text",
        model_id: "fixture-text",
        identity: &identity,
        responses: false,
    };
    let parent = "conv_billing";
    let child = "sub_fixture";
    let baseline = json!({"api_calls":3,"current_context_tokens":8,"token_usage":{
        "prompt":20,"completion":5,"cached_input":7,"total":25
    }});
    let mut request = billing
        .begin(
            BillingScope::sub_agent(parent.into(), child, "Fixture reader", &baseline),
            model(),
        )
        .await;
    request.price.rates = Rates {
        input: Some(2.into()),
        cache_read: Some(1.into()),
        output: Some(4.into()),
    };
    let usage = json!({"prompt_tokens":10,"prompt_tokens_details":{"cached_tokens":2},"completion_tokens":3,"total_tokens":13});
    let actor = request
        .settle_with_actor(Some(&usage), None)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(actor["name"], "Fixture reader");
    assert_eq!(actor["kind"], "sub_agent");
    assert_eq!(actor["historical_unpriced"], true);
    assert_eq!(actor["input_tokens"], 30);
    assert_eq!(actor["cached_input_tokens"], 9);
    assert_eq!(actor["output_tokens"], 8);
    assert_eq!(actor["total_tokens"], 38);
    assert_eq!(actor["cost_usd"], "0.00003");
    assert!(
        request
            .settle_with_actor(Some(&usage), None)
            .await
            .unwrap()
            .is_none()
    );

    let main = billing
        .begin(
            BillingScope::main(parent.into(), &json!({"total_input_tokens":4})),
            model(),
        )
        .await;
    assert!(main.settle_success(Some(&usage), None).await.unwrap());
    assert!(!main.settle_success(Some(&usage), None).await.unwrap());
    let review = billing
        .begin(
            BillingScope::review_agent(parent.into(), "workflow_review"),
            model(),
        )
        .await;
    assert!(review.settle_success(None, None).await.unwrap());
    assert!(!review.settle_success(None, None).await.unwrap());

    let runtime = Runtime::open(&fixture.0).unwrap();
    let mut recovered = baseline;
    recovered["cost_ledger_requests"] = json!(0);
    runtime
        .recover_child_billing(parent, child, &mut recovered)
        .await
        .unwrap();
    assert_eq!(recovered["api_calls"], 4);
    assert_eq!(
        recovered["token_usage"],
        json!({"prompt":30,"completion":8,"cached_input":9,"total":38})
    );
    assert_eq!(recovered["current_context_tokens"], 10);
    assert_eq!(recovered["cost_usd"], "0.00003");
    recovered["current_context_tokens"] = json!(0); // committed post-compression state
    runtime
        .recover_child_billing(parent, child, &mut recovered)
        .await
        .unwrap();
    assert_eq!(recovered["api_calls"], 4);
    assert_eq!(recovered["current_context_tokens"], 0);

    let summary = Ledger::new(&fixture.0).summarize(parent, 0, &[]).unwrap();
    assert_eq!(summary["total_usd"], "0.00003");
    assert_eq!(summary["partial"], true);
    assert_eq!(summary["actors"].as_array().unwrap().len(), 3);
}

#[test]
fn child_opencode_headers_do_not_inherit_the_parent_session() {
    let model = ModelConfig::fixed(
        "https://opencode.ai/zen/v1/chat/completions".into(),
        None,
        "fixture".into(),
    )
    .with_opencode_headers(Some("parent-fixture"), false);
    assert_eq!(
        model.options.headers["x-opencode-session"],
        "parent-fixture"
    );
    let child = model
        .clone()
        .with_opencode_headers(Some("child-fixture"), true);
    assert_eq!(child.options.headers["x-opencode-session"], "child-fixture");
    assert_eq!(child.options.headers["user-agent"], "Astrion/1.0.0");
    assert!(
        model
            .with_opencode_headers(None, true)
            .options
            .headers
            .get("x-opencode-session")
            .is_none()
    );
}
