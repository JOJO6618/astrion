use super::*;
use crate::{
    approval::{ApprovalBackend, Decision},
    runtime::store::{Task, TaskStart},
    tools::read_file::ReadFile,
};

fn settings() -> TaskApprovalSettings {
    TaskApprovalSettings {
        principal: ApprovalPrincipal::new("owner", "ws").unwrap(),
        permission: PermissionMode::Approval,
        work_mode: WorkMode::Interactive,
        execution_mode: ExecutionMode::Sandbox,
        valid_modes: true,
        reviewer: None,
        max_review_rounds: 10,
    }
}

fn write_request(path: &str) -> ToolRequest {
    workspace_write_request(
        "write-call",
        WRITE_NAME,
        json!({"file_path":path,"content":"approved fixture"}),
    )
    .unwrap()
}

#[test]
fn saved_modes_keep_readonly_plan_and_unknown_modes_fail_closed() {
    let mut settings = settings();
    let root = if cfg!(windows) {
        r"C:\fixture"
    } else {
        "/fixture"
    };
    let scope = TaskScope::new(
        settings.principal.clone(),
        "conv",
        "task",
        "worker",
        root.into(),
        ExecutionMode::Sandbox,
    )
    .unwrap();
    let ordinary = write_request("notes.md");
    assert_eq!(settings.route(&scope, &ordinary), Ok(WriteRoute::Approval));
    for permission in ["auto_approval", "unrestricted"] {
        settings
            .apply_conversation_modes(&json!({"permission_mode":permission,"work_mode":"execute"}));
        assert_eq!(settings.route(&scope, &ordinary), Ok(WriteRoute::Exempt));
    }
    settings.apply_conversation_modes(
        &json!({"permission_mode":"readonly", "execution_mode":"direct"}),
    );
    assert_eq!(settings.effective_execution(), ExecutionMode::Sandbox);
    assert!(settings.route(&scope, &ordinary).is_err());
    settings
        .apply_conversation_modes(&json!({"work_mode":"plan", "permission_mode":"unrestricted"}));
    let plan = write_request(".astrion/plan/test.MD");
    assert_eq!(settings.route(&scope, &plan), Ok(WriteRoute::Exempt));
    for path in [
        "notes.md",
        ".astrion/plan/../../notes.md",
        ".astrion/plan/notes.txt",
        ".astrion/plan-other/notes.md",
    ] {
        assert!(settings.route(&scope, &write_request(path)).is_err());
    }
    settings.apply_conversation_modes(&json!({"permission_mode":"old-privileged-mode"}));
    assert!(settings.route(&scope, &plan).is_err());
    assert!(
        workspace_write_request("x", WRITE_NAME, json!({"_execution_scope":"forged"})).is_err()
    );
}

struct Fixture {
    dir: std::path::PathBuf,
    workspace: std::path::PathBuf,
    runtime: Runtime,
    writer: Arc<WorkspaceFileWriter>,
}

impl Fixture {
    fn new() -> Self {
        let dir =
            std::env::temp_dir().join(format!("astrion-approval-test-{}", uuid::Uuid::new_v4()));
        let workspace = dir.join("workspace");
        fs::create_dir_all(&workspace).unwrap();
        let data = dir.join("data");
        let runtime = Runtime::open(&data).unwrap();
        runtime.bind_workspace("ws", &workspace).unwrap();
        let writer = Arc::new(WorkspaceFileWriter::new(Arc::new(
            ReadFile::open(&workspace, &data).unwrap(),
        )));
        Self {
            dir,
            workspace,
            runtime,
            writer,
        }
    }

    fn admit(&self, id: &str, settings: TaskApprovalSettings) {
        let session = self.runtime.create_session("ws", &json!({})).unwrap();
        let id_conversation = session["conversation_id"].as_str().unwrap().to_owned();
        let (cancel, _) = tokio::sync::oneshot::channel();
        let start = TaskStart {
            id: id.into(),
            message: "Create an isolated fixture".into(),
            created_at: super::super::now(),
            model_key: None,
            message_source: None,
        };
        let mut task = Task::new(start, "ws", id_conversation, cancel);
        task.approval = Some(settings);
        self.runtime.store().unwrap().tasks.insert(id.into(), task);
    }

    async fn pending(&self, principal: &ApprovalPrincipal) -> Approval {
        tokio::time::timeout(Duration::from_secs(5), async {
            loop {
                if let Some(item) = self
                    .runtime
                    .list_pending(principal, None)
                    .unwrap()
                    .into_iter()
                    .next()
                {
                    return item;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap()
    }

    fn start_write(
        &self,
        task: &'static str,
        path: &str,
    ) -> tokio::task::JoinHandle<Result<Value, String>> {
        let runtime = self.runtime.clone();
        let writer = self.writer.clone();
        let request = write_request(path);
        tokio::spawn(async move {
            runtime
                .execute_workspace_write(writer, task, request, &[], &[])
                .await
        })
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

#[tokio::test]
async fn approval_gate_binds_real_write_and_cancellation_with_no_model() {
    let fixture = Fixture::new();
    let settings = settings();
    fixture.admit("approved-task", settings.clone());
    let worker = fixture.start_write("approved-task", "approved.txt");
    let item = fixture.pending(&settings.principal).await;
    assert!(!fixture.workspace.join("approved.txt").exists());
    let other = ApprovalPrincipal::new("owner", "other-ws").unwrap();
    assert!(matches!(
        fixture
            .runtime
            .decide(&other, &item.approval_id, Decision::Approved, false),
        Err(ApprovalError::Forbidden)
    ));
    fixture
        .runtime
        .decide(
            &settings.principal,
            &item.approval_id,
            Decision::Approved,
            false,
        )
        .unwrap();
    assert_eq!(worker.await.unwrap().unwrap()["success"], true);
    assert_eq!(
        fs::read_to_string(fixture.workspace.join("approved.txt")).unwrap(),
        "approved fixture"
    );

    fixture.admit("cancelled-task", settings.clone());
    let worker = fixture.start_write("cancelled-task", "cancelled.txt");
    let item = fixture.pending(&settings.principal).await;
    fixture.runtime.cancel_task("ws", "cancelled-task").unwrap();
    let decision = fixture
        .runtime
        .decide(
            &settings.principal,
            &item.approval_id,
            Decision::Approved,
            false,
        )
        .unwrap();
    assert_eq!(decision.status, Status::Expired);
    let _ = worker.await.unwrap();
    assert!(!fixture.workspace.join("cancelled.txt").exists());
}

#[tokio::test]
async fn exempt_writes_never_expand_the_workspace_and_readonly_denies() {
    let fixture = Fixture::new();
    let mut settings = settings();
    settings.permission = PermissionMode::AutoApproval;
    fixture.admit("auto-task", settings.clone());
    assert_eq!(
        fixture
            .start_write("auto-task", "allowed.txt")
            .await
            .unwrap()
            .unwrap()["success"],
        true
    );
    assert_eq!(
        fixture
            .start_write("auto-task", "../pop-out.txt")
            .await
            .unwrap()
            .unwrap()["success"],
        false
    );
    assert!(!fixture.dir.join("pop-out.txt").exists());
    assert!(
        fixture
            .runtime
            .list_pending(&settings.principal, None)
            .unwrap()
            .is_empty()
    );

    settings.permission = PermissionMode::ReadOnly;
    fixture.admit("readonly-task", settings.clone());
    assert_eq!(
        fixture
            .start_write("readonly-task", "denied.txt")
            .await
            .unwrap()
            .unwrap()["code"],
        "readonly_denied"
    );
    assert!(!fixture.workspace.join("denied.txt").exists());
}

#[test]
fn personalization_selects_the_thinking_reviewer_and_honors_explicit_key() {
    let fixture = Fixture::new();
    let deploy = fixture.dir.join("deploy");
    let data = fixture.dir.join("data");
    fs::create_dir_all(&deploy).unwrap();
    fs::write(deploy.join("custom_models.json"), json!([
        {"model_name":"normal","model_id":"normal-model","url":"https://fixture.invalid/v1","apikey":"fixture"},
        {"model_name":"reviewer","model_id":"review-fast","url":"https://fixture.invalid/v1","apikey":"fixture","visible":false,
          "reasoning_capability":"fast, thinking", "thinkmode_status":{"type":"switch_model","fast_model_id":"review-fast","thinking_model_id":"review-thinking"}}
    ]).to_string()).unwrap();
    let source = CatalogSource::with_paths(&fixture.dir, &deploy, &data);
    let principal = settings().principal;
    let default = resolve_task_approval_settings(principal.clone(), &source).unwrap();
    assert_eq!(default.work_mode, WorkMode::Plan);
    assert_eq!(default.reviewer.unwrap().model, "normal-model");
    fs::write(
        data.join("personalization.json"),
        json!({
            "default_permission_mode":"auto_approval", "default_work_mode":"execute",
            "review_agents":{"auto_approval":{"model":"reviewer","max_rounds":6}}
        })
        .to_string(),
    )
    .unwrap();
    let selected = resolve_task_approval_settings(principal.clone(), &source).unwrap();
    assert_eq!(selected.permission, PermissionMode::AutoApproval);
    assert_eq!(selected.work_mode, WorkMode::Interactive);
    assert_eq!(selected.max_review_rounds, 6);
    assert_eq!(selected.reviewer.unwrap().model, "review-thinking");
    fs::write(
        data.join("personalization.json"),
        json!({"review_agents":{"auto_approval":{"model":"missing"}}}).to_string(),
    )
    .unwrap();
    assert!(
        resolve_task_approval_settings(principal, &source)
            .unwrap()
            .reviewer
            .is_none()
    );
}

#[tokio::test]
async fn readonly_plan_writes_only_markdown_in_the_bound_plan_directory() {
    let fixture = Fixture::new();
    let mut settings = settings();
    settings.apply_conversation_modes(&json!({"permission_mode":"readonly", "work_mode":"plan"}));
    fixture.admit("plan-task", settings.clone());
    assert_eq!(
        fixture
            .start_write("plan-task", ".astrion/plan/fixture.md")
            .await
            .unwrap()
            .unwrap()["success"],
        true
    );
    for path in [
        "denied.md",
        ".astrion/plan/denied.txt",
        ".astrion/plan/../../escaped.md",
    ] {
        assert_eq!(
            fixture
                .start_write("plan-task", path)
                .await
                .unwrap()
                .unwrap()["code"],
            "readonly_denied"
        );
    }
    assert!(
        fixture
            .runtime
            .list_pending(&settings.principal, None)
            .unwrap()
            .is_empty()
    );
    assert!(fixture.workspace.join(".astrion/plan/fixture.md").is_file());
    assert!(!fixture.workspace.join("escaped.md").exists());
    #[cfg(unix)]
    {
        let outside = fixture.dir.join("outside");
        fs::create_dir(&outside).unwrap();
        std::os::unix::fs::symlink(&outside, fixture.workspace.join(".astrion/plan/alternate"))
            .unwrap();
        assert_eq!(
            fixture
                .start_write("plan-task", ".astrion/plan/alternate/denied.md")
                .await
                .unwrap()
                .unwrap()["success"],
            false
        );
        assert!(!outside.join("denied.md").exists());
    }
}

#[tokio::test]
async fn execution_root_is_rebound_without_changing_workspace_ownership() {
    let fixture = Fixture::new();
    let settings = settings();
    fixture.admit("rebound-task", settings.clone());
    let stale_worker = fixture.start_write("rebound-task", "stale.txt");
    let stale = fixture.pending(&settings.principal).await;
    let root = fixture.dir.join("worktree");
    fs::create_dir(&root).unwrap();
    let root = fs::canonicalize(root).unwrap();
    fixture
        .runtime
        .store()
        .unwrap()
        .tasks
        .get_mut("rebound-task")
        .unwrap()
        .execution_root = Some(root.clone());
    fixture
        .runtime
        .decide(
            &settings.principal,
            &stale.approval_id,
            Decision::Approved,
            false,
        )
        .unwrap();
    assert_eq!(
        stale_worker.await.unwrap().unwrap()["code"],
        "approval_authority_denied"
    );
    assert!(!root.join("stale.txt").exists());
    assert!(!fixture.workspace.join("stale.txt").exists());

    let read_file = ReadFile::open(&root, &fixture.dir.join("data")).unwrap();
    let rooted_writer = Arc::new(WorkspaceFileWriter::new(Arc::new(read_file)));
    let runtime = fixture.runtime.clone();
    let worker = tokio::spawn(async move {
        runtime
            .execute_workspace_write(
                rooted_writer,
                "rebound-task",
                write_request("rebound.txt"),
                &[],
                &[],
            )
            .await
    });
    let fresh = fixture.pending(&settings.principal).await;
    assert_eq!(fresh.scope().workspace_root(), root);
    assert_eq!(fresh.scope().principal(), &settings.principal);
    fixture
        .runtime
        .decide(
            &settings.principal,
            &fresh.approval_id,
            Decision::Approved,
            false,
        )
        .unwrap();
    assert_eq!(worker.await.unwrap().unwrap()["success"], true);
    assert!(root.join("rebound.txt").is_file());
    assert!(!fixture.workspace.join("rebound.txt").exists());
}
