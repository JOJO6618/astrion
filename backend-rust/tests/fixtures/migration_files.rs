use super::*;
use std::fs;

struct Fixture {
    root: PathBuf,
    data: PathBuf,
    legacy: PathBuf,
    project: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("target")
            .join(format!("memory-files-{}", Uuid::new_v4()));
        let project = root.join("sample-workspace");
        let legacy = project.join(".astrion/memory");
        let data = root.join("data");
        fs::create_dir_all(&legacy).unwrap();
        fs::create_dir_all(&data).unwrap();
        Self {
            data: data.canonicalize().unwrap(),
            project: project.canonicalize().unwrap(),
            legacy,
            root,
        }
    }

    fn workspace(&self) -> Workspace {
        Workspace {
            label: "Sample workspace".into(),
            project: self.project.clone(),
            target: target_path(&self.data, &self.project).unwrap(),
            files: list(&self.project).unwrap(),
        }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn preserves_conflicts_and_mtime_and_only_cleans_verified_notes() {
    let fixture = Fixture::new();
    let source = fixture.legacy.join("topic.md");
    fs::write(&source, "legacy bytes").unwrap();
    fs::write(fixture.legacy.join("skip.txt"), "skip").unwrap();
    fs::create_dir(fixture.legacy.join("folder.md")).unwrap();
    let workspace = fixture.workspace();
    assert_eq!(workspace.files, ["topic.md"]);
    fs::create_dir_all(&workspace.target).unwrap();
    fs::write(workspace.target.join("topic.md"), "keep current topic").unwrap();
    let mut copied = 0;
    let copies = copy(&fixture.data, &workspace, || copied += 1).unwrap();
    assert_eq!(copied, 1);
    assert!(copies[0].target.starts_with("topic_migrated_"));
    let target = workspace.target.join(&copies[0].target);
    assert_eq!(fs::read_to_string(&target).unwrap(), "legacy bytes");
    let mtime = |path| fs::metadata(path).unwrap().modified().unwrap();
    assert_eq!(mtime(&source), mtime(&target));
    let mut verified = 0;
    verify_and_clean(&fixture.data, &workspace, &copies, || verified += 1).unwrap();
    assert_eq!(verified, 1);
    assert!(!source.exists());
    assert_eq!(
        fs::read_to_string(workspace.target.join("topic.md")).unwrap(),
        "keep current topic"
    );
    assert!(fixture.legacy.join("skip.txt").is_file());
    assert!(fixture.legacy.join("folder.md").is_dir());
    assert!(fixture.legacy.join(NOTICE).is_file());
}

#[test]
fn source_changes_leave_legacy_content_for_retry() {
    let fixture = Fixture::new();
    let source = fixture.legacy.join("changed.md");
    fs::write(&source, "original").unwrap();
    let workspace = fixture.workspace();
    let copies = copy(&fixture.data, &workspace, || {}).unwrap();
    fs::write(&source, "updated and longer").unwrap();
    assert!(verify_and_clean(&fixture.data, &workspace, &copies, || {}).is_err());
    assert_eq!(fs::read_to_string(&source).unwrap(), "updated and longer");
    assert!(!fixture.legacy.join(NOTICE).exists());
    assert_eq!(
        fs::read_to_string(workspace.target.join("changed.md")).unwrap(),
        "original"
    );
}

#[test]
fn refuses_legacy_hardlinks() {
    let fixture = Fixture::new();
    let original = fixture.root.join("outside.md");
    let linked = fixture.legacy.join("linked.md");
    fs::write(&original, "test data outside the registered workspace").unwrap();
    fs::hard_link(&original, &linked).unwrap();
    let workspace = fixture.workspace();
    assert!(copy(&fixture.data, &workspace, || {}).is_err());
    assert!(linked.is_file());
    assert!(!workspace.target.join("linked.md").exists());
}
