use std::sync::{Arc, Barrier, Mutex};
use std::time::Instant;

use axum::{
    Router,
    body::{Body, to_bytes},
    http::{Request, StatusCode, header::CONTENT_TYPE},
};
use serde_json::{Value, json};
use tower::ServiceExt;

use super::*;

fn principal(user: &str, workspace: &str) -> ApprovalPrincipal {
    ApprovalPrincipal::new(user, workspace).unwrap()
}

fn scope(
    owner: ApprovalPrincipal,
    conversation: &str,
    task: &str,
    executor: &str,
    mode: ExecutionMode,
) -> TaskScope {
    let root = if cfg!(windows) {
        r"C:\fixture"
    } else {
        "/fixture"
    };
    TaskScope::new(owner, conversation, task, executor, root.into(), mode).unwrap()
}

fn task() -> TaskScope {
    scope(
        principal("alice", "workspace-a"),
        "conversation",
        "task",
        "worker",
        ExecutionMode::Sandbox,
    )
}

fn call(approval_type: ApprovalType) -> ToolRequest {
    ToolRequest {
        tool_call_id: "call".into(),
        tool_name: "run_command".into(),
        arguments: json!({"command":"fixture", "timeout": 10, "request_full_access": approval_type == ApprovalType::FullAccess, "full_access_reason":"synthetic", "other":{"visible":[1,2]}}).as_object().unwrap().clone(),
        preview: json!({"tool_summary":"test preview"}).as_object().unwrap().clone(),
        approval_type,
        browser_site: None,
    }
}

#[test]
fn requirements_match_permission_rules_and_capture_policy_at_creation() {
    let full = ApprovalType::FullAccess;
    for (mode, toggle, expected) in [
        (PermissionMode::Approval, false, (false, true)),
        (PermissionMode::Approval, true, (false, true)),
        (PermissionMode::AutoApproval, false, (true, true)),
        (PermissionMode::AutoApproval, true, (true, false)),
        (PermissionMode::Unrestricted, false, (false, true)),
        (PermissionMode::Unrestricted, true, (true, false)),
    ] {
        let requirements = resolve_approval_requirements(mode, full, toggle).unwrap();
        assert_eq!(
            (
                requirements.auto_review_required(),
                requirements.human_approval_required()
            ),
            expected
        );
    }
    assert!(resolve_approval_requirements(PermissionMode::ReadOnly, full, true).is_err());
    let scope = task();
    let mut store = ApprovalStore::default();
    let row = store
        .create(&scope, &call(full), PermissionMode::AutoApproval, false)
        .unwrap();
    let changed = store
        .create(&scope, &call(full), PermissionMode::AutoApproval, true)
        .unwrap();
    assert!(
        store
            .get(scope.principal(), &row.approval_id)
            .unwrap()
            .requirements
            .human_approval_required()
    );
    assert!(!changed.requirements.human_approval_required());
    let direct = call(ApprovalType::DirectExecution);
    assert!(
        store
            .create(&scope, &direct, PermissionMode::Approval, false)
            .is_err()
    );
    let direct_scope = super::tests::scope(
        scope.principal().clone(),
        "conversation",
        "task",
        "worker",
        ExecutionMode::Direct,
    );
    assert!(
        store
            .create(&direct_scope, &call(full), PermissionMode::Approval, false)
            .is_err()
    );
    assert!(
        store
            .create(&direct_scope, &direct, PermissionMode::Approval, false)
            .is_ok()
    );
}

#[test]
fn snapshots_are_independent_and_scope_checks_cover_owner_workspace_worker_and_call() {
    let mut store = ApprovalStore::default();
    let task = task();
    let original = call(ApprovalType::SandboxWrite);
    let mut changed = original.clone();
    let mut row = store
        .create(&task, &changed, PermissionMode::Approval, false)
        .unwrap();
    changed.arguments.get_mut("other").unwrap()["visible"][1] = json!(3);
    row.request.arguments.insert("timeout".into(), json!(20));
    row.requirements.human_approval_required = false;
    let id = &row.approval_id;
    let stored = store.get(task.principal(), id).unwrap();
    assert_eq!(stored.request(), &original);
    assert!(stored.requirements.human_approval_required());
    store
        .decide(task.principal(), id, Decision::Approved, false)
        .unwrap();
    assert_eq!(
        store
            .claim_execution(id, &task, &changed, None)
            .unwrap_err(),
        ApprovalError::ScopeMismatch
    );
    let scopes = [
        scope(
            principal("alice", "workspace-b"),
            "conversation",
            "task",
            "worker",
            ExecutionMode::Sandbox,
        ),
        scope(
            principal("bob", "workspace-a"),
            "conversation",
            "task",
            "worker",
            ExecutionMode::Sandbox,
        ),
        scope(
            task.principal().clone(),
            "other",
            "task",
            "worker",
            ExecutionMode::Sandbox,
        ),
        scope(
            task.principal().clone(),
            "conversation",
            "other",
            "worker",
            ExecutionMode::Sandbox,
        ),
        scope(
            task.principal().clone(),
            "conversation",
            "task",
            "other",
            ExecutionMode::Sandbox,
        ),
        scope(
            task.principal().clone(),
            "conversation",
            "task",
            "worker",
            ExecutionMode::Direct,
        ),
        TaskScope::new(
            task.principal().clone(),
            "conversation",
            "task",
            "worker",
            task.workspace_root().join("other"),
            ExecutionMode::Sandbox,
        )
        .unwrap(),
    ];
    for scope in scopes {
        assert_eq!(
            store
                .claim_execution(id, &scope, &original, None)
                .unwrap_err(),
            ApprovalError::ScopeMismatch
        );
    }
    let mut changed_id = original.clone();
    changed_id.tool_call_id = "other".into();
    assert_eq!(
        store
            .claim_execution(id, &task, &changed_id, None)
            .unwrap_err(),
        ApprovalError::ScopeMismatch
    );
    let mut changed_tool = original.clone();
    changed_tool.tool_name = "other".into();
    assert_eq!(
        store
            .claim_execution(id, &task, &changed_tool, None)
            .unwrap_err(),
        ApprovalError::ScopeMismatch
    );
    let mut changed_type = original.clone();
    changed_type.approval_type = ApprovalType::FullAccess;
    assert_eq!(
        store
            .claim_execution(id, &task, &changed_type, None)
            .unwrap_err(),
        ApprovalError::ScopeMismatch
    );
    let grant = store.claim_execution(id, &task, &original, None).unwrap();
    assert_eq!(
        store
            .claim_execution(id, &task, &original, None)
            .unwrap_err(),
        ApprovalError::NotApproved
    );
    let scope = store
        .consume_execution(grant, &task, &original, None)
        .unwrap();
    assert_eq!(scope.access(), ExecutionAccess::SandboxWrite);
    assert_eq!(scope.task(), &task);
    assert_eq!(scope.call(), &original);
}

#[test]
fn double_approval_accepts_either_order_and_preserves_a_terminal_rejection() {
    let scope = task();
    let call = call(ApprovalType::FullAccess);
    for human_first in [true, false] {
        let mut store = ApprovalStore::default();
        let row = store
            .create(&scope, &call, PermissionMode::AutoApproval, false)
            .unwrap();
        let id = &row.approval_id;
        let review = store.start_auto_review(&scope, id, "inputs").unwrap();
        let human = |store: &mut ApprovalStore| {
            store
                .decide(scope.principal(), id, Decision::Approved, false)
                .unwrap()
        };
        let auto = |store: &mut ApprovalStore| {
            store
                .finish_auto_review(&review, "inputs", Decision::Approved, None)
                .unwrap()
        };
        let first = if human_first {
            human(&mut store)
        } else {
            auto(&mut store)
        };
        assert_eq!(first.status, Status::Pending);
        assert_eq!(
            store
                .claim_execution(id, &scope, &call, Some("inputs"))
                .unwrap_err(),
            ApprovalError::NotApproved
        );
        let last = if human_first {
            auto(&mut store)
        } else {
            human(&mut store)
        };
        assert_eq!(last.status, Status::Approved);
        let grant = store
            .claim_execution(id, &scope, &call, Some("inputs"))
            .unwrap();
        assert_eq!(
            store
                .consume_execution(grant, &scope, &call, Some("inputs"))
                .unwrap()
                .access(),
            ExecutionAccess::FullAccessOnce
        );
    }

    let mut store = ApprovalStore::default();
    let row = store
        .create(&scope, &call, PermissionMode::AutoApproval, false)
        .unwrap();
    let id = &row.approval_id;
    let review = store.start_auto_review(&scope, id, "inputs").unwrap();
    store
        .decide(scope.principal(), id, Decision::Approved, false)
        .unwrap();
    assert_eq!(
        store
            .decide(scope.principal(), id, Decision::Rejected, false)
            .unwrap()
            .status,
        Status::Rejected
    );
    let late = store
        .finish_auto_review(&review, "inputs", Decision::Approved, None)
        .unwrap();
    assert_eq!(late.status, Status::Rejected);
    assert_eq!(late.human_decision, Some(Decision::Rejected));
    assert_eq!(
        store
            .decide(scope.principal(), id, Decision::Approved, false)
            .unwrap()
            .status,
        Status::Rejected
    );
}

#[test]
fn automatic_only_and_failure_stay_on_the_trusted_review_path() {
    let scope = task();
    let mut store = ApprovalStore::default();
    for ty in [ApprovalType::SandboxWrite, ApprovalType::FullAccess] {
        let request = call(ty);
        let row = store
            .create(&scope, &request, PermissionMode::AutoApproval, true)
            .unwrap();
        let id = &row.approval_id;
        assert!(!row.needs_human_decision());
        assert_eq!(
            row.required_event_payload().unwrap()["approval"]["approval_id"],
            *id
        );
        assert!(row.resolved_event_payload().is_none());
        for decision in [Decision::Approved, Decision::Rejected] {
            assert!(matches!(
                store.decide(scope.principal(), id, decision, false),
                Err(ApprovalError::Invalid(_))
            ));
        }
        let review = store.start_auto_review(&scope, id, "inputs").unwrap();
        let failed = store
            .fail_auto_review(
                &review,
                "inputs",
                "review_invalid_decision",
                "Bad reviewer output",
            )
            .unwrap();
        assert_eq!(failed.status, Status::Expired);
        assert_eq!(failed.decision, None);
        assert_eq!(failed.auto_review_status, Some(AutoReviewStatus::Error));
        let event = failed.resolved_event_payload().unwrap();
        assert_eq!(event["decision"], "error");
        assert_eq!(event["code"], "review_invalid_decision");
        assert_eq!(event["conversation_id"], "conversation");
        assert!(failed.required_event_payload().is_none());
        assert_eq!(
            store
                .finish_auto_review(&review, "inputs", Decision::Approved, None)
                .unwrap()
                .status,
            Status::Expired
        );
        assert_eq!(
            store
                .claim_execution(id, &scope, &request, Some("inputs"))
                .unwrap_err(),
            ApprovalError::NotApproved
        );
    }
    let request = call(ApprovalType::FullAccess);
    let rejected = store
        .create(&scope, &request, PermissionMode::AutoApproval, false)
        .unwrap();
    let review = store
        .start_auto_review(&scope, &rejected.approval_id, "inputs")
        .unwrap();
    let row = store
        .finish_auto_review(&review, "inputs", Decision::Rejected, Some(" risk "))
        .unwrap();
    assert_eq!(row.status, Status::Rejected);
    assert_eq!(row.reason.as_deref(), Some("risk"));
    let human = store
        .create(&scope, &request, PermissionMode::Approval, false)
        .unwrap();
    assert!(
        store
            .start_auto_review(&scope, &human.approval_id, "inputs")
            .is_err()
    );
}

#[test]
fn review_freshness_is_checked_when_publishing_claiming_and_starting() {
    let mut store = ApprovalStore::default();
    let scope = task();
    let request = call(ApprovalType::FullAccess);
    let row = store
        .create(&scope, &request, PermissionMode::Unrestricted, true)
        .unwrap();
    let id = &row.approval_id;
    let first = store.start_auto_review(&scope, id, "first").unwrap();
    assert_eq!(
        store
            .finish_auto_review(&first, "second", Decision::Approved, None)
            .unwrap_err(),
        ApprovalError::ReviewChanged
    );
    let second = store.start_auto_review(&scope, id, "second").unwrap();
    assert_eq!(
        store
            .finish_auto_review(&first, "first", Decision::Approved, None)
            .unwrap_err(),
        ApprovalError::ReviewChanged
    );
    assert_eq!(
        store
            .finish_auto_review(&second, "second", Decision::Approved, None)
            .unwrap()
            .status,
        Status::Approved
    );
    for fingerprint in [None, Some("first")] {
        assert_eq!(
            store
                .claim_execution(id, &scope, &request, fingerprint)
                .unwrap_err(),
            ApprovalError::ReviewChanged
        );
    }
    let grant = store
        .claim_execution(id, &scope, &request, Some("second"))
        .unwrap();
    assert_eq!(
        store
            .consume_execution(grant, &scope, &request, Some("third"))
            .unwrap_err(),
        ApprovalError::ReviewChanged
    );
    assert_eq!(
        store
            .claim_execution(id, &scope, &request, Some("second"))
            .unwrap_err(),
        ApprovalError::NotApproved
    );
}

#[test]
fn cancellation_revokes_claims_and_only_terminal_records_expire_by_ttl() {
    let mut store = ApprovalStore::default();
    let scope = task();
    let call = call(ApprovalType::SandboxWrite);
    let pending = store
        .create(&scope, &call, PermissionMode::Approval, false)
        .unwrap();
    let approved = store
        .create(&scope, &call, PermissionMode::Approval, false)
        .unwrap();
    store
        .decide(
            scope.principal(),
            &approved.approval_id,
            Decision::Approved,
            false,
        )
        .unwrap();
    let grant = store
        .claim_execution(&approved.approval_id, &scope, &call, None)
        .unwrap();
    store.expire_task("unrelated");
    assert_eq!(
        store.list_pending(scope.principal(), None).unwrap().len(),
        1
    );
    store.expire_task(scope.task_id());
    assert_eq!(
        store
            .decide(
                scope.principal(),
                &pending.approval_id,
                Decision::Approved,
                false
            )
            .unwrap()
            .status,
        Status::Expired
    );
    assert_eq!(
        store
            .consume_execution(grant, &scope, &call, None)
            .unwrap_err(),
        ApprovalError::NotApproved
    );
    assert!(
        store
            .list_pending(scope.principal(), None)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        store
            .get(scope.principal(), &approved.approval_id)
            .unwrap()
            .status,
        Status::Approved
    );
    let unexpired = store
        .create(&scope, &call, PermissionMode::Approval, false)
        .unwrap();
    store
        .records
        .get_mut(&pending.approval_id)
        .unwrap()
        .resolved_at = Instant::now().checked_sub(RESOLVED_TTL);
    assert_eq!(
        store
            .get(scope.principal(), &pending.approval_id)
            .unwrap_err(),
        ApprovalError::NotFound
    );
    assert_eq!(
        store.list_pending(scope.principal(), None).unwrap()[0].approval_id,
        unexpired.approval_id
    );
}

#[test]
fn browser_scope_requires_the_approved_site_and_only_manual_approval_can_remember_it() {
    let mut store = ApprovalStore::default();
    let task = task();
    let mut request = call(ApprovalType::BrowserAction);
    request.tool_name = "browser_click".into();
    assert!(
        store
            .create(&task, &request, PermissionMode::Approval, false)
            .is_err()
    );
    request.browser_site = Some("example.test".into());
    let row = store
        .create(&task, &request, PermissionMode::Approval, false)
        .unwrap();
    let result = store
        .decide(task.principal(), &row.approval_id, Decision::Approved, true)
        .unwrap();
    assert_eq!(result.remember_site, Some(true));
    let grant = store
        .claim_execution(&row.approval_id, &task, &request, None)
        .unwrap();
    request.browser_site = Some("other.test".into());
    assert_eq!(
        store
            .consume_execution(grant, &task, &request, None)
            .unwrap_err(),
        ApprovalError::ScopeMismatch
    );
    let row = store
        .create(&task, &request, PermissionMode::AutoApproval, false)
        .unwrap();
    assert!(
        store
            .decide(task.principal(), &row.approval_id, Decision::Approved, true)
            .is_err()
    );
    let ticket = store
        .start_auto_review(&task, &row.approval_id, "inputs")
        .unwrap();
    assert_eq!(
        store
            .finish_auto_review(&ticket, "inputs", Decision::Approved, None)
            .unwrap()
            .remember_site,
        Some(false)
    );
}

#[test]
fn two_workers_sharing_the_task_lock_cannot_both_claim() {
    let mut store = ApprovalStore::default();
    let scope = task();
    let request = call(ApprovalType::SandboxWrite);
    let row = store
        .create(&scope, &request, PermissionMode::Approval, false)
        .unwrap();
    store
        .decide(
            scope.principal(),
            &row.approval_id,
            Decision::Approved,
            false,
        )
        .unwrap();
    let store = Mutex::new(store);
    let start = Barrier::new(2);
    std::thread::scope(|threads| {
        let claim = || {
            start.wait();
            store
                .lock()
                .unwrap()
                .claim_execution(&row.approval_id, &scope, &request, None)
        };
        let first = threads.spawn(claim);
        let second = threads.spawn(claim);
        let outcomes = [first.join().unwrap(), second.join().unwrap()];
        assert_eq!(outcomes.iter().filter(|result| result.is_ok()).count(), 1);
        assert_eq!(
            outcomes.into_iter().find_map(Result::err),
            Some(ApprovalError::NotApproved)
        );
    });
}

#[derive(Default)]
struct TestBackend(Mutex<ApprovalStore>);

impl ApprovalBackend for TestBackend {
    fn list_pending(
        &self,
        principal: &ApprovalPrincipal,
        conversation_id: Option<&str>,
    ) -> Result<Vec<Approval>, ApprovalError> {
        self.0
            .lock()
            .unwrap()
            .list_pending(principal, conversation_id)
    }

    fn decide(
        &self,
        principal: &ApprovalPrincipal,
        id: &str,
        decision: Decision,
        remember_site: bool,
    ) -> Result<Approval, ApprovalError> {
        self.0
            .lock()
            .unwrap()
            .decide(principal, id, decision, remember_site)
    }
}

async fn http_request(
    router: &Router,
    access: Option<ApprovalAccess>,
    path: &str,
    payload: Option<Value>,
) -> (StatusCode, Value) {
    let mut request = Request::builder().uri(path);
    if let Some(access) = access {
        request = request.extension(access);
    }
    let body = match payload {
        Some(payload) => {
            request = request
                .method("POST")
                .header(CONTENT_TYPE, "application/json");
            Body::from(payload.to_string())
        }
        None => Body::empty(),
    };
    let response = router
        .clone()
        .oneshot(request.body(body).unwrap())
        .await
        .unwrap();
    let status = response.status();
    let bytes = to_bytes(response.into_body(), 16_384).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

#[tokio::test]
async fn http_routes_require_authenticated_workspace_and_return_legacy_tool_shapes() {
    const PENDING: &str = "/api/tool-approvals/pending";
    let router = router::<()>();
    let backend = Arc::new(TestBackend::default());
    let alice = task();
    let bob = scope(
        principal("bob", "workspace-a"),
        "conversation",
        "other-task",
        "worker",
        ExecutionMode::Sandbox,
    );
    let other = scope(
        principal("alice", "workspace-b"),
        "conversation",
        "other-task-2",
        "worker",
        ExecutionMode::Sandbox,
    );
    let request = call(ApprovalType::SandboxWrite);
    let id = {
        let mut store = backend.0.lock().unwrap();
        for scope in [&bob, &other] {
            store
                .create(scope, &request, PermissionMode::Approval, false)
                .unwrap();
        }
        store
            .create(&alice, &request, PermissionMode::Approval, false)
            .unwrap()
            .approval_id
    };
    let endpoint = format!("/api/tool-approvals/{id}/decision");
    let access = |principal: &ApprovalPrincipal| {
        Some(ApprovalAccess::new(principal.clone(), backend.clone()))
    };
    let approved = || Some(json!({"decision":"approved"}));
    assert_eq!(
        http_request(&router, None, PENDING, None).await.0,
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        http_request(&router, None, &endpoint, approved()).await.0,
        StatusCode::UNAUTHORIZED
    );
    let (status, list) = http_request(
        &router,
        access(alice.principal()),
        &format!("{PENDING}?conversation_id=%20conversation%20"),
        None,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(list["conversation_id"], "conversation");
    assert_eq!(list["items"].as_array().unwrap().len(), 1);
    assert_eq!(list["items"][0]["approval_id"], id);
    assert_eq!(list["items"][0]["workspace_id"], "workspace-a");
    assert_eq!(list["items"][0]["task_id"], "task");
    assert_eq!(list["items"][0]["tool_name"], "run_command");
    assert_eq!(list["items"][0]["human_approval_required"], true);
    assert_eq!(list["items"][0]["arguments"], json!(request.arguments));
    let (_, list) = http_request(
        &router,
        access(alice.principal()),
        &format!("{PENDING}?conversation_id=wrong"),
        None,
    )
    .await;
    assert_eq!(list["items"], json!([]));
    for principal in [bob.principal(), other.principal()] {
        assert_eq!(
            http_request(&router, access(principal), &endpoint, approved())
                .await
                .0,
            StatusCode::FORBIDDEN
        );
    }
    assert_eq!(
        http_request(
            &router,
            access(alice.principal()),
            "/api/tool-approvals/missing/decision",
            approved()
        )
        .await
        .0,
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        http_request(
            &router,
            access(alice.principal()),
            &endpoint,
            Some(json!({"decision":"error"}))
        )
        .await
        .0,
        StatusCode::BAD_REQUEST
    );
    let forged = json!({"decision":"approved", "username":"alice", "workspace_id":"workspace-a"});
    assert_eq!(
        http_request(&router, access(other.principal()), &endpoint, Some(forged))
            .await
            .0,
        StatusCode::FORBIDDEN
    );
    let (status, result) = http_request(
        &router,
        access(alice.principal()),
        &endpoint,
        Some(json!({"decision":" APPROVED "})),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(result["item"]["status"], "approved");
    let (_, list) = http_request(&router, access(alice.principal()), PENDING, None).await;
    assert_eq!(list["items"], json!([]));
}
