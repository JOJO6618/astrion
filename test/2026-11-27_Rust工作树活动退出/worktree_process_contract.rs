use std::{fs, time::Duration};

use super::{ExecutionMode, Invocation, Network, Owner, ProcessResources, SandboxAccess, Scope};
use uuid::Uuid;

#[test]
fn worktree_exit_checks_live_shells_and_background_processes_in_own_conversation() {
    let tmp = std::env::temp_dir().join(format!("astrion-worktree-{}", Uuid::new_v4().simple()));
    let workspace = tmp.join("workspace");
    let tree = workspace.join("tree");
    let data = tmp.join("data");
    fs::create_dir_all(&tree).unwrap();
    fs::create_dir(&data).unwrap();
    let workspace = workspace.canonicalize().unwrap();
    let tree = tree.canonicalize().unwrap();
    let mut resources = ProcessResources::default();
    resources.configure(&tmp.join("deploy"), &data).unwrap();
    if !resources.available_mode(ExecutionMode::Direct) {
        fs::remove_dir_all(tmp).unwrap();
        return; // Non-interactive/root test hosts need not provide the native shell adapter.
    }
    let owner = Owner {
        user: "fixture".into(), workspace_id: "fixture".into(), workspace: workspace.clone(),
        conversation: "conv_busy".into(), actor: None,
    };
    let scope = || Scope {
        owner: owner.clone(), execution_root: tree.clone(), mode: ExecutionMode::Direct,
        access: SandboxAccess { write: true, network: Network::None }, approval_required: false,
    };
    resources.start(scope(), Invocation::Open { name:"shell".into(), cwd:".".into() }).unwrap();
    assert_eq!(resources.worktree_busy("fixture", &workspace, "conv_busy", &tree), (false, Some("shell".into())));
    assert_eq!(resources.worktree_busy("fixture", &workspace, "conv_other", &tree), (false, None));
    assert_eq!(resources.worktree_busy("fixture", &workspace, "conv_busy", &workspace.join("other-tree")), (false, None));

    // A real local process stays alive while the exit guard samples its state.
    let running = resources.start(scope(), Invocation::Command {
        command: "sleep 15".into(), timeout: Duration::from_secs(20), background: true, full_access: false,
    }).unwrap();
    assert_eq!(resources.worktree_busy("fixture", &workspace, "conv_busy", &tree), (true, Some("shell".into())));
    resources.start(scope(), Invocation::Close { name:"shell".into() }).unwrap();
    assert_eq!(resources.worktree_busy("fixture", &workspace, "conv_busy", &tree), (true, None));

    drop(running);
    resources.close_conversation("fixture", Some("conv_busy"));
    assert_eq!(resources.worktree_busy("fixture", &workspace, "conv_busy", &tree), (false, None));
    fs::remove_dir_all(tmp).unwrap();
}
