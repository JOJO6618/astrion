use super::*;
use crate::{runtime::Runtime, workspace_catalog::RegisteredWorkspace};

#[test]
fn web_exit_workspace_refuses_live_children_but_retains_the_binding() {
    use std::sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    };

    let temp = Temp::new();
    let repo = temp.repo();
    let account = temp.0.join("runtime-data");
    let runtime = Runtime::open(&account).unwrap();
    runtime.bind_workspace("worktree", &repo).unwrap();
    let active = Arc::new(AtomicBool::new(true));
    let children = crate::sub_agents::SubAgents::new(&account, runtime.clone())
        .with_live_records({
            let active = active.clone();
            move |workspace, conversation| {
                Ok(if active.load(Ordering::Relaxed) {
                    vec![json!({"task_id":"sub_busy","status":"running",
                        "conversation_id":conversation,"workspace_root":workspace.path})]
                } else {
                    vec![]
                })
            }
        });
    let runtime = runtime.with_sub_agents(children);
    let workspace = RegisteredWorkspace {
        id: "worktree".into(),
        path: repo.clone(),
        label: "Fixture".into(),
    };
    let preferences = json!({"git_worktree_enabled":true,"work_mode":"execute","permission_mode":"approval"});
    let created = runtime.create_web_conversation(&workspace, preferences.as_object().unwrap().clone()).unwrap();
    let id = created["conversation_id"].as_str().unwrap();
    let entered = runtime.change_worktree("worktree", id, Some(target(json!({
        "action":"create","branch_name":"busy-exit","start_ref":"HEAD","include_uncommitted":false
    })))).unwrap();
    let directory = PathBuf::from(entered["state"]["directory"].as_str().unwrap());

    let busy = runtime.change_worktree("worktree", id, None).unwrap_err();
    assert_eq!(busy.status, 409);
    assert!(busy.message.contains("子智能体"));
    assert_eq!(runtime.worktree_view("worktree", Some(id), false).unwrap()["active"], true);
    active.store(false, Ordering::Relaxed);
    assert_eq!(runtime.change_worktree("worktree", id, None).unwrap()["state"]["active"], false);
    assert!(directory.is_dir()); // Leaving never deletes the user's branch or edits.
}
