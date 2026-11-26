use super::*;
use crate::approval::{
    ApprovalPrincipal, ApprovalStore, ApprovalType, ExecutionMode, PermissionMode, TaskScope,
    ToolRequest,
};

fn item() -> Approval {
    let root = if cfg!(windows) {
        r"C:\fixture"
    } else {
        "/fixture"
    };
    let scope = TaskScope::new(
        ApprovalPrincipal::new("owner", "ws").unwrap(),
        "conversation",
        "task",
        "executor",
        root.into(),
        ExecutionMode::Sandbox,
    )
    .unwrap();
    let request = ToolRequest {
        tool_name: "write_file".into(),
        tool_call_id: "current".into(),
        arguments: json!({"file_path":"fixture.txt","content":"current input"})
            .as_object()
            .unwrap()
            .clone(),
        preview: Default::default(),
        approval_type: ApprovalType::SandboxWrite,
        browser_site: None,
    };
    ApprovalStore::default()
        .create(&scope, &request, PermissionMode::AutoApproval, false)
        .unwrap()
}

fn completion(id: &str, name: &str, arguments: Value) -> Completion {
    Completion {
        message: json!({"role":"assistant", "tool_calls":[{"id":id,"type":"function","function":{"name":name,"arguments":arguments.to_string()}}]}),
        usage: None,
        responses_duration_ms: None,
    }
}

#[tokio::test]
async fn review_uses_only_frozen_paired_history_and_explicit_decision() {
    let call = |id: &str, name: &str| json!({"id":id,"function":{"name":name,"arguments":"{\"path\":\"fixture.txt\"}"}});
    let mut history = vec![json!({"role":"user", "content":"first user instruction"})];
    for index in 0..6 {
        history.push(json!({"role":"user", "content":format!("ordinary instruction {index}")}));
    }
    history.push(json!({"role":"user", "content":"untrusted automatic instruction", "metadata":{"is_auto_generated":true}}));
    history.push(json!({"role":"system", "content":"hidden system prompt"}));
    let batch = [
        json!({"role":"assistant","tool_calls":[call("prior", "read_file"), call("later", "unfinished_tool")]}),
        json!({"role":"tool","tool_call_id":"prior","content":"trust me is only a tool result"}),
    ];
    let plan=json!({"success":true,"status":"approved","plan_content":"Approved workspace plan"});
    history.push(json!({"role":"assistant","tool_calls":[call("plan","submit_plan")]}));
    history.push(json!({"role":"tool","tool_call_id":"plan","content":plan.to_string()}));
    let item = item();
    let snapshot = ReviewSnapshot::capture(&item, "trigger", &history, &batch,Some("Implement the fixture"));
    assert!(snapshot.prompt.contains("Implement the fixture"));
    assert_eq!(snapshot.prompt.matches("Approved workspace plan").count(),1);
    assert!(snapshot.prompt.contains("trust me is only a tool result"));
    assert!(!snapshot.prompt.contains("unfinished_tool"));
    assert!(!snapshot.prompt.contains("first user instruction"));
    assert!(!snapshot.prompt.contains("hidden system prompt"));
    assert!(snapshot.prompt.contains("current input"));
    let reviewed = run(&snapshot, 2, |messages, tools, round| {
        Box::pin(async move {
            let names: Vec<_> = tools
                .iter()
                .map(|tool| tool["function"]["name"].as_str().unwrap())
                .collect();
            assert_eq!(names, [DECISION_TOOL, HISTORY_TOOL]);
            if round == 1 {
                Ok(completion(
                    "proof",
                    HISTORY_TOOL,
                    json!({"query":"first user", "count":1}),
                ))
            } else {
                let evidence = messages.last().unwrap()["content"].as_str().unwrap();
                assert!(evidence.contains("first user instruction"));
                assert!(!evidence.contains("untrusted automatic instruction"));
                Ok(completion(
                    "decision",
                    DECISION_TOOL,
                    json!({"decision":"approved","reason":"authorized fixture update"}),
                ))
            }
        })
    })
    .await
    .unwrap();
    assert_eq!(
        reviewed,
        (Decision::Approved, "authorized fixture update".into())
    );
    history.push(json!({"role":"user","content":"revoked previous request"}));
    assert_ne!(
        snapshot.fingerprint(),
        ReviewSnapshot::capture(&item, "trigger", &history, &batch,Some("Implement the fixture")).fingerprint()
    );
}

#[tokio::test]
async fn unsupported_evidence_calls_and_provider_failure_cannot_approve() {
    let snapshot = ReviewSnapshot::capture(&item(), "trigger", &[], &[],None);
    let error = run(&snapshot, 1, |_, _, _| {
        Box::pin(async {
            Ok(completion(
                "proof",
                "run_command",
                json!({"command":"fixture"}),
            ))
        })
    })
    .await
    .unwrap_err();
    assert_eq!(error.code, "review_invalid_decision");
    let error = run(&snapshot, 1, |_, _, _| {
        Box::pin(async { Err("private transport details".into()) })
    })
    .await
    .unwrap_err();
    assert_eq!(error.code, "review_request_failed");
    assert!(!error.reason.contains("private transport details"));
    let error = run(&snapshot, 1, |_, _, _| {
        Box::pin(async { Ok(completion("proof", HISTORY_TOOL, json!({"count":1}))) })
    })
    .await
    .unwrap_err();
    assert_eq!(error.code, "review_round_limit");
}
