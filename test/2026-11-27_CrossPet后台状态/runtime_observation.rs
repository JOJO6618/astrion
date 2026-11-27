use super::*;
use crate::{
    approval::ExecutionMode,
    process_runtime::{Invocation, Network, Owner, SandboxAccess, Scope},
    runtime::ModelConfig,
};
use serde_json::json;
use std::{fs, path::PathBuf, time::Duration};
use tokio::{net::TcpListener, time::{sleep, timeout}};
use uuid::Uuid;

struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn crosspet_workspace_free_chat_has_no_background_work() {
    let path = std::env::temp_dir().join(format!("astrion-crosspet-chat-{}", Uuid::new_v4()));
    let scratch = Scratch(path);
    let runtime = Runtime::open(&scratch.0).unwrap();
    let (task_id, _receiver) = runtime.register_chat_task(crate::runtime::ChatTaskStart {
        conversation_id: "conv_chat_fixture".into(),
        message: "Fixture".into(),
        model_key: None,
        message_source: None,
        created_at: 0.0,
    }).unwrap();
    let task = TaskRef {
        task_id,
        session: SessionIdentity {
            workspace_id: String::new(),
            conversation_id: "conv_chat_fixture".into(),
        },
    };
    assert_eq!(runtime.crosspet_observation(&task, None).unwrap().background, Some(false));
}

#[tokio::test]
async fn crosspet_runtime_observes_pending_command_until_delivery_after_main_stops() {
    let path = std::env::temp_dir().join(format!("astrion-crosspet-observation-{}", Uuid::new_v4()));
    fs::create_dir(&path).unwrap();
    let scratch = Scratch(path.canonicalize().unwrap());
    let workspace = RegisteredWorkspace {
        id: "project".into(),
        label: "Project".into(),
        path: scratch.0.join("project"),
    };
    let deploy = scratch.0.join("config");
    fs::create_dir(&workspace.path).unwrap();
    fs::create_dir(&deploy).unwrap();
    let runtime = Runtime::open(scratch.0.join("data")).unwrap();
    runtime.bind_workspace(&workspace.id, &workspace.path).unwrap();
    runtime.configure_host_processes(&deploy).unwrap();
    // The fixture uses an ordinary user and a local shell, not a real model.
    if !runtime.store().unwrap().process_resources.available_mode(ExecutionMode::Direct) {
        return;
    }
    let model = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let model = ModelConfig::fixed(
        format!("http://{}/v1/responses", model.local_addr().unwrap()), None, "fixture".into(),
    );
    let accepted = runtime.create_task(&workspace.id, &json!({"message":"fixture"}), model).unwrap();
    let identity = TaskRef {
        session: SessionIdentity {
            workspace_id: workspace.id.clone(),
            conversation_id: accepted["data"]["conversation_id"].as_str().unwrap().into(),
        },
        task_id: accepted["data"]["task_id"].as_str().unwrap().into(),
    };
    let observe = || runtime.crosspet_observation(&identity, Some(&workspace)).unwrap();
    assert_eq!(observe().background, Some(false));

    let owner = Owner {
        user: "host".into(),
        workspace_id: workspace.id.clone(),
        workspace: workspace.path.clone(),
        conversation: identity.session.conversation_id.clone(),
        actor: None,
    };
    let started = runtime.store().unwrap().process_resources.start(
        Scope {
            owner: owner.clone(),
            execution_root: workspace.path.clone(),
            access: SandboxAccess { write: false, network: Network::None },
            mode: ExecutionMode::Direct,
            approval_required: false,
        },
        Invocation::Command {
            command: "while [ ! -f finish-fixture ]; do /bin/sleep 0.02; done".into(),
            timeout: Duration::from_secs(10), background: true, full_access: false,
        },
    ).unwrap();
    assert_eq!(observe().background, Some(true));
    let (result, _) = started.finish().await;
    assert_eq!(result["background_task_created"], true);

    runtime.cancel_task(&workspace.id, &identity.task_id).unwrap();
    timeout(Duration::from_secs(4), async {
        while observe().terminal.is_none() {
            sleep(Duration::from_millis(20)).await;
        }
    }).await.unwrap();
    assert_eq!(observe().terminal, Some(TerminalStatus::Stopped));
    assert_eq!(observe().background, Some(true));

    fs::write(workspace.path.join("finish-fixture"), "").unwrap();
    timeout(Duration::from_secs(4), async {
        loop {
            if runtime.store().unwrap().process_resources.has_notices(&owner) {
                break;
            }
            sleep(Duration::from_millis(20)).await;
        }
    }).await.unwrap();
    assert_eq!(observe().background, Some(true), "terminal but undelivered");
    assert_eq!(runtime.store().unwrap().process_resources.take_notices(&owner).len(), 1);
    assert_eq!(observe().background, Some(false));

    let unrelated = RegisteredWorkspace { path: scratch.0.join("elsewhere"), ..workspace.clone() };
    assert_eq!(runtime.crosspet_observation(&identity, Some(&unrelated)).unwrap().background, None);
    let unknown = TaskRef { task_id: "missing".into(), ..identity.clone() };
    assert!(runtime.crosspet_observation(&unknown, Some(&workspace)).is_none());
}
