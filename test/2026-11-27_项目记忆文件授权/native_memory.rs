use super::project_memory_root;
use serde_json::json;
use std::fs;

#[test]
fn native_files_open_only_the_bound_memory_and_honor_denies() {
    let tmp = std::env::temp_dir().canonicalize().unwrap().join(uuid::Uuid::new_v4().to_string());
    let data = tmp.join("data");
    let deploy = tmp.join("deploy");
    let workspace = tmp.join("project");
    for dir in [&data, &deploy, &workspace] { fs::create_dir_all(dir).unwrap(); }
    let namespace = crate::knowledge::workspace_namespace(&workspace).unwrap();
    let root = data.join("memory").join(namespace);
    let topic = root.join("topics").join("note.md");
    let resolve = |path: &std::path::Path| project_memory_root(&data, Some(&deploy), &workspace, path);
    let grant = resolve(&topic).unwrap();
    assert!(grant.open(&data, false).is_err());
    let reader = grant.open(&data, true).unwrap();
    fs::create_dir(root.join("topics")).unwrap();
    fs::write(&topic, "a project note").unwrap();
    assert_eq!(reader.execute(&json!({"path":topic}))["success"], true);
    assert!(resolve(&data.join("memory/another-project/topics/note.md")).is_err());
    assert!(resolve(&data.join("host_api_token")).is_err());
    assert!(resolve(&root.join("../another-project/topics/note.md")).is_err());
    assert!(resolve(&root.join(".env")).is_err());
    fs::write(deploy.join("host_sandbox_policy.json"),
        json!({"macos_deny_read_paths":[topic]}).to_string()).unwrap();
    assert!(resolve(&topic).is_err());
    #[cfg(unix)]
    {
        let alias = root.join("other.md");
        std::os::unix::fs::symlink(&topic, &alias).unwrap();
        assert_eq!(reader.execute(&json!({"path":alias}))["success"], false);
    }
    fs::remove_dir_all(tmp).unwrap();
}
