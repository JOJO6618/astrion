use super::*;
use crate::{
    approval::ApprovalPrincipal,
    catalog::CatalogSource,
    runtime::{approval::resolve_task_approval_settings, store::{Task, TaskStart}},
};

struct Fixture {
    root: std::path::PathBuf,
    workspace: std::path::PathBuf,
    runtime: Runtime,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("astrion-process-test-{}", Uuid::new_v4()));
        let workspace = root.join("workspace");
        fs::create_dir_all(&workspace).unwrap();
        let data = root.join("data");
        let runtime = Runtime::open(&data).unwrap();
        runtime.bind_workspace("workspace", &workspace).unwrap();
        Self { root, workspace: workspace.canonicalize().unwrap(), runtime }
    }

    fn admit(&self, task_id: &str, user: &str, defaults: Value, modes: Value) -> String {
        let session = self.runtime.create_session("workspace", &json!({})).unwrap();
        let id = session["conversation_id"].as_str().unwrap().to_owned();
        let principal = ApprovalPrincipal::new(user, "workspace").unwrap();
        let data = self.root.join("data");
        fs::write(data.join("personalization.json"), defaults.to_string()).unwrap();
        let source = CatalogSource::with_paths(&self.root, &self.root, &data);
        let mut approval = resolve_task_approval_settings(principal, &source).unwrap();
        approval.apply_conversation_modes(&modes);
        let (cancel, _) = tokio::sync::oneshot::channel();
        let start = TaskStart {
            id: task_id.into(),
            message: "Process resource fixture".into(),
            created_at: crate::runtime::now(),
            model_key: None,
            message_source: None,
        };
        let mut task = Task::new(start, "workspace", id.clone(), cancel);
        task.approval = Some(approval);
        self.runtime.store().unwrap().tasks.insert(task_id.into(), task);
        id
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn command_scope_uses_workspace_and_effective_admission_modes() {
    let fixture = Fixture::new();
    let defaults = json!({"default_work_mode":"execute","default_permission_mode":"unrestricted"});
    let conversation = fixture.admit("write", HOST, defaults.clone(), json!({}));
    fixture.admit("readonly", HOST, defaults.clone(), json!({"permission_mode":"readonly","execution_mode":"direct"}));
    fixture.admit("approval", HOST, defaults.clone(), json!({"permission_mode":"approval"}));
    fixture.admit("plan", HOST, defaults.clone(), json!({"work_mode":"plan","execution_mode":"direct"}));
    fixture.admit("direct", HOST, defaults.clone(), json!({"execution_mode":"direct"}));
    fixture.admit("invalid", HOST, defaults.clone(), json!({"permission_mode":"unknown"}));
    fixture.admit("foreign", "other-user", defaults, json!({}));
    let mut store = fixture.runtime.store().unwrap();
    let (write, _) = live_scope(&store, "write").unwrap();
    assert_eq!(write.owner.conversation, conversation);
    assert_eq!(write.execution_root, fixture.workspace);
    assert_eq!(write.access, SandboxAccess { write: true, network: Network::Restricted });
    assert!(!write.approval_required);
    for id in ["readonly", "plan", "approval"] {
        let (scope, _) = live_scope(&store, id).unwrap();
        assert!(!scope.access.write);
        assert_eq!(scope.approval_required, id == "approval");
    }
    assert_eq!(live_scope(&store, "direct").unwrap().0.mode, ExecutionMode::Direct);
    for id in ["foreign", "invalid"] {
        assert!(live_scope(&store, id).is_err(), "{id}");
    }
    store.tasks.get_mut("write").unwrap().cancel_requested = true;
    assert!(live_scope(&store, "write").is_err());
}

#[test]
fn process_arguments_cannot_select_an_execution_root_or_escalate() {
    for forged in [
        json!({"command":"printf ok","timeout":1,"working_dir":"."}),
        json!({"command":"printf ok","timeout":1,"request_full_access":true}),
        json!({"command":"printf ok","timeout":1,"_execution_scope":"fixture"}),
    ] {
        assert!(Invocation::parse("run_command", &forged).is_err());
    }
    assert!(Invocation::parse("run_command", &json!({"command":"printf ok","timeout":1})).is_ok());
    assert!(readonly_command("pwd"));
    assert!(readonly_command("git status | head"));
    assert!(!readonly_command("printf fixture > file"));
    assert!(!readonly_command("ls\nmkdir unauthorized"));
}

#[tokio::test]
async fn monitor_snapshot_follows_task_identity_and_scope_removal() {
    let fixture = Fixture::new();
    let id = fixture.admit("task", HOST, json!({}), json!({}));
    fs::write(fixture.workspace.join("notes.txt"), "first line\nsecond line\n").unwrap();
    let display_id = Runtime::process_monitor_id();
    let snapshot = fixture.runtime.capture_process_monitor("task", &display_id, MonitorStage::Before,
        "read_file", &json!({"path":"notes.txt"})).await.unwrap();
    assert_eq!(snapshot["lines"], 2);
    let mut store = fixture.runtime.store().unwrap();
    assert!(store.process_resources.monitor("other-user", &display_id, MonitorStage::Before).is_none());
    let (owner, cached) = store.process_resources.monitor(HOST, &display_id, MonitorStage::Before).unwrap();
    assert_eq!(owner.conversation, id);
    assert_eq!(cached, snapshot);
    assert!(store.process_resources.monitor(HOST, &display_id, MonitorStage::After).is_none());
    Runtime::close_process_scope(&mut store, "other-workspace", Some(&id));
    assert!(store.process_resources.monitor(HOST, &display_id, MonitorStage::Before).is_some());
    Runtime::close_process_scope(&mut store, "workspace", Some(&id));
    assert!(store.process_resources.monitor(HOST, &display_id, MonitorStage::Before).is_none());
}

#[cfg(target_os = "macos")]
#[tokio::test]
#[ignore = "requires native Seatbelt without an enclosing sandbox"]
async fn native_seatbelt_runs_readonly_write_and_terminal_scopes() {
    let fixture = Fixture::new();
    fixture.runtime.configure_host_processes(&fixture.root.join("deploy")).unwrap();
    let defaults = json!({"default_work_mode":"execute","default_permission_mode":"unrestricted"});
    let readonly = fixture.admit("readonly", HOST, defaults.clone(), json!({"permission_mode":"readonly"}));
    let write = fixture.admit("write", HOST, defaults, json!({}));
    fs::write(fixture.workspace.join(".env"), "isolated private marker").unwrap();
    let command = |task, command: &str| {
        let args = json!({"command":command,"timeout":5});
        fixture.runtime.execute_process_tool(task, "command", "run_command", args, &[])
    };
    let shown = command("readonly", "printf readonly-ok").await.unwrap();
    assert_eq!(shown["output"], "readonly-ok", "{shown}");
    let denied = command("readonly", "printf denied > denied.txt").await.unwrap();
    assert_eq!(denied["success"], false, "{denied}");
    assert!(!fixture.workspace.join("denied.txt").exists());
    let secret = command("write", "cat .env").await.unwrap();
    assert_eq!(secret["success"], false, "{secret}");
    assert!(!secret["output"].as_str().unwrap_or("").contains("isolated private marker"));
    let created = command("write", "printf fixture > allowed.txt").await.unwrap();
    assert_eq!(created["success"], true, "{created}");
    assert_eq!(fs::read_to_string(fixture.workspace.join("allowed.txt")).unwrap(), "fixture");

    let open = fixture.runtime.execute_process_tool("write", "open", "terminal_session", json!({
        "action":"open","session_name":"fixture-terminal"
    }), &[]).await.unwrap();
    assert_eq!(open["success"], true, "{open}");
    let input = fixture.runtime.execute_process_tool("write", "input", "terminal_input", json!({
        "session_name":"fixture-terminal","command":"printf terminal-ok","output_wait":1
    }), &[]).await.unwrap();
    assert!(input["output"].as_str().unwrap_or("").contains("terminal-ok"), "{input}");
    let store = fixture.runtime.store().unwrap();
    let make_owner = |id| Owner {
        user: HOST.into(), workspace_id: "workspace".into(), workspace: fixture.workspace.clone(), conversation: id, actor: None,
    };
    assert!(store.process_resources.terminal_output(&make_owner(readonly), "fixture-terminal", 100).is_err());
    assert!(store.process_resources.terminal_output(&make_owner(write), "fixture-terminal", 100).is_ok());
    drop(store);
    fixture.runtime.shutdown_process_resources().unwrap();
}

#[cfg(target_os = "macos")]
#[tokio::test]
#[ignore = "requires native Seatbelt without an enclosing sandbox"]
async fn native_background_cancel_reaps_the_owned_process_and_suppresses_notices() {
    let fixture = Fixture::new();
    fixture.runtime.configure_host_processes(&fixture.root.join("deploy")).unwrap();
    let defaults = json!({"default_work_mode":"execute","default_permission_mode":"unrestricted"});
    let id = fixture.admit("owner", HOST, defaults.clone(), json!({}));
    let other = fixture.admit("other", HOST, defaults, json!({}));
    let started = fixture.runtime.execute_process_tool("owner", "background", "run_command", json!({
        "command":"printf '%s\\n' \"$$\"; /bin/sleep 30; printf finished",
        "timeout":40, "run_in_background":true,
    }), &[]).await.unwrap();
    assert_eq!(started["status"], "running_background", "{started}");
    let command = started["command_id"].as_str().unwrap();
    let pid = started["output"].as_str().unwrap().trim().parse::<libc::pid_t>().unwrap();
    let (owner, cancellation) = {
        let mut store = fixture.runtime.store().unwrap();
        let owner = live_owner(&store, "owner").unwrap();
        assert_eq!(owner.conversation, id);
        let foreign = live_owner(&store, "other").unwrap();
        assert_eq!(foreign.conversation, other);
        assert!(store.process_resources.cancel_background(&foreign, command).is_err());
        let cancellation = store.process_resources.cancel_background(&owner, command).unwrap();
        (owner, cancellation)
    };
    let cancelled = cancellation.finish().await.unwrap();
    assert_eq!(cancelled["data"]["status"], "cancelled", "{cancelled}");
    // Signal zero only checks whether the shell's PID still exists.
    assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
    let mut store = fixture.runtime.store().unwrap();
    assert!(store.process_resources.take_notices(&owner).is_empty());
    assert!(!store.process_resources.has_pending(&owner));
}

#[cfg(target_os = "macos")]
#[tokio::test]
#[ignore = "requires native Seatbelt without an enclosing sandbox"]
async fn native_command_approval_binds_single_operation() {
    use crate::approval::{ApprovalBackend, Decision};
    use std::time::Duration;

    let fixture = Fixture::new();
    fixture.runtime.configure_host_processes(&fixture.root.join("deploy")).unwrap();
    let defaults = json!({"default_work_mode":"execute","default_permission_mode":"approval"});
    let conversation = fixture.admit("approval", HOST, defaults, json!({}));
    let readonly = fixture.runtime.execute_process_tool("approval", "readonly", "run_command", json!({
        "command":"printf ready", "timeout":4,
    }), &[]).await.unwrap();
    assert_eq!(readonly["output"], "ready", "{readonly}");

    let worker = tokio::spawn({
        let runtime = fixture.runtime.clone();
        async move {
            runtime.execute_process_tool("approval", "approved", "run_command", json!({
                "command":"printf approved > approved.txt","timeout":4,
            }), &[]).await.unwrap()
        }
    });
    let principal = ApprovalPrincipal::new(HOST, "workspace").unwrap();
    let approval = tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if let Some(approval) = fixture.runtime.list_pending(&principal, Some(&conversation)).unwrap().pop() {
                break approval;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }).await.unwrap();
    assert!(!fixture.workspace.join("approved.txt").exists());
    assert_eq!(approval.request().tool_name, "run_command");
    fixture.runtime.decide(&principal, &approval.approval_id, Decision::Approved, false).unwrap();
    let result = worker.await.unwrap();
    assert_eq!(result["success"], true, "{result}");
    assert_eq!(fs::read_to_string(fixture.workspace.join("approved.txt")).unwrap(), "approved");
}
