use super::{host_paths::Workspace, launch::read_or_create_token};
use std::{fs, path::PathBuf};
use uuid::Uuid;

struct Tree(PathBuf);
impl Tree {
    fn new() -> Self {
        let tree = Self(std::env::temp_dir().join(Uuid::new_v4().to_string()));
        fs::create_dir(&tree.0).unwrap();
        tree
    }
}
impl Drop for Tree {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn creates_private_token_once_and_preserves_python_token_verbatim_on_disk() {
    let tree = Tree::new();
    let first = read_or_create_token(&tree.0).unwrap();
    assert_eq!(first, read_or_create_token(&tree.0).unwrap());
    let path = tree.0.join("host_api_token");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
    let python_token = "urlsafe-test-credential_with-dash\n";
    fs::write(&path, python_token).unwrap();
    assert_eq!(read_or_create_token(&tree.0).unwrap(), python_token.trim());
    assert_eq!(fs::read_to_string(&path).unwrap(), python_token);
}

#[test]
fn python_registered_workspace_id_and_nested_scope_are_used_without_rewriting_registry() {
    let tree = Tree::new();
    let project = tree.0.join("parent/child");
    fs::create_dir_all(&project).unwrap();
    let registry = tree.0.join("host_workspaces.json");
    assert_eq!(
        Workspace::resolve(&project, &registry, &tree.0).unwrap().id,
        ""
    );
    fs::write(&registry, br#"{"auto_default_workspace":true,"workspaces":[]}"#).unwrap();
    assert!(Workspace::resolve(&project, &registry, &tree.0).unwrap().id.is_empty());
    let bytes=serde_json::to_vec(&serde_json::json!({"workspaces":[{"workspace_id":"old-key","path":project,"label":"Child"},{"workspace_id":"other","path":tree.0.join("parent") }]})).unwrap();
    fs::write(&registry, &bytes).unwrap();
    let ws = Workspace::resolve(&project, &registry, &tree.0).unwrap();
    assert_eq!(ws.id, "old-key");
    assert_eq!(ws.label, "Child");
    let unrelated = Workspace::resolve(&tree.0, &registry, &tree.0).unwrap();
    assert!(unrelated.id.is_empty());
    assert_eq!(unrelated.path, tree.0.canonicalize().unwrap());
    assert_eq!(fs::read(&registry).unwrap(), bytes);
}
