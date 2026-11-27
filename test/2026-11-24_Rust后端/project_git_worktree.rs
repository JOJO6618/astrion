use std::{
    env, fs,
    path::{Path, PathBuf},
    process::Command,
};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{Target, Worktree};

struct Temp(PathBuf);

impl Temp {
    fn new() -> Self {
        let root =
            env::temp_dir().join(format!("project_git_worktree_{}", Uuid::new_v4().simple()));
        fs::create_dir(&root).unwrap();
        Self(root.canonicalize().unwrap())
    }
    fn repo(&self) -> PathBuf {
        let repo = self.0.join("project");
        fs::create_dir(&repo).unwrap();
        git(&repo, &["init", "-b", "main"]);
        fs::write(repo.join("text"), "one\ntwo\nthree\nfour\nfive\n").unwrap();
        fs::write(repo.join("binary"), [0, 1, 2]).unwrap();
        git(&repo, &["add", "-A"]);
        git(&repo, &["commit", "-m", "initial"]);
        repo
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn git(repo: &Path, args: &[&str]) -> String {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", crate::project_git::runner::NULL_DEVICE)
        .env("GIT_AUTHOR_NAME", "Fixture")
        .env("GIT_AUTHOR_EMAIL", "fixture@local.invalid")
        .env("GIT_COMMITTER_NAME", "Fixture")
        .env("GIT_COMMITTER_EMAIL", "fixture@local.invalid")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().to_owned()
}

fn target(data: Value) -> Target {
    serde_json::from_value(data).unwrap()
}

#[test]
fn creates_managed_worktree_with_fixed_start_and_copies_each_change_layer() {
    let temp = Temp::new();
    let repo = temp.repo();
    let children = Worktree::new(&repo).unwrap();
    let start = git(&repo, &["rev-parse", "HEAD"]);
    fs::write(repo.join("text"), "one\nstaged\nthree\nfour\nfive\n").unwrap();
    fs::write(repo.join("binary"), [0, 9, 1, 2]).unwrap();
    git(&repo, &["add", "text", "binary"]);
    let staged_sources = git(&repo, &["diff", "--cached", "--binary"]);
    fs::write(repo.join("text"), "one\nstaged\nthree\nunstaged\nfive\n").unwrap();
    fs::write(repo.join("untracked"), "new file stays local\n").unwrap();
    let unstaged_sources = git(&repo, &["diff", "--binary"]);
    let requested = json!({"action":"create","branch_name":"astrion/test-branch","start_ref":start,"include_uncommitted":true});
    assert!(
        children
            .prepare(target(requested.clone()), &repo, "plan", "readonly")
            .is_err()
    );
    assert!(!repo.join(".astrion").exists());
    let binding = children
        .prepare(target(requested), &repo, "execute", "approval")
        .unwrap();
    assert_eq!(binding.branch, "astrion/test-branch");
    assert_eq!(binding.head, start);
    assert!(
        binding
            .directory
            .starts_with(repo.join(".astrion/worktrees"))
    );
    assert_eq!(
        git(&repo, &["diff", "--cached", "--binary"]),
        staged_sources
    );
    assert_eq!(git(&repo, &["diff", "--binary"]), unstaged_sources);
    assert_eq!(
        git(&binding.directory, &["diff", "--cached", "--binary"]),
        staged_sources
    );
    assert_eq!(
        git(&binding.directory, &["diff", "--binary"]),
        unstaged_sources
    );
    assert_eq!(
        fs::read(binding.directory.join("untracked")).unwrap(),
        fs::read(repo.join("untracked")).unwrap()
    );
    assert_eq!(git(&repo, &["branch", "--show-current"]), "main");
    assert!(!git(&repo, &["status", "--porcelain"]).contains(".astrion"));
}

#[test]
fn scoped_restore_validates_external_worktree_and_preserves_missing_binding() {
    let temp = Temp::new();
    let repo = temp.repo();
    let external = temp.0.join("external");
    git(
        &repo,
        &[
            "worktree",
            "add",
            "--detach",
            external.to_str().unwrap(),
            "HEAD",
        ],
    );
    let worktree = Worktree::new(&repo).unwrap();
    assert!(worktree.inspect(&repo).is_err());
    let binding = worktree.inspect(&external).unwrap();
    let metadata = json!({"git_worktree_enabled":true,"git_worktree":binding});
    assert_eq!(metadata["git_worktree"]["grant"]["path"], json!(external));
    assert_eq!(worktree.execution_root(&metadata).unwrap(), external);
    let invalid =
        json!({"git_worktree_enabled":true,"git_worktree":{"directory":external,"active":true}});
    assert!(worktree.execution_root(&invalid).is_err());
    let forged = temp.0.join("forged");
    fs::create_dir(&forged).unwrap();
    fs::copy(external.join(".git"), forged.join(".git")).unwrap();
    assert!(worktree.inspect(&forged).is_err());
    fs::rename(&external, temp.0.join("moved")).unwrap();
    assert!(worktree.execution_root(&metadata).is_err());
    let view = worktree.view(&metadata).unwrap();
    assert_eq!(view["directory"], json!(external));
    assert!(
        view["unavailable"]
            .as_str()
            .is_some_and(|message| !message.is_empty())
    );
}

#[test]
fn unsafe_refs_and_untracked_path_conflicts_preserve_the_source() {
    let temp = Temp::new();
    let repo = temp.repo();
    fs::write(repo.join("collision"), "in target\n").unwrap();
    git(&repo, &["add", "collision"]);
    git(&repo, &["commit", "-m", "add target file"]);
    let target_sha = git(&repo, &["rev-parse", "HEAD"]);
    git(&repo, &["reset", "--hard", "HEAD^"]);
    fs::write(repo.join("collision"), "local source\n").unwrap();
    let service = Worktree::new(&repo).unwrap();
    let bad = target(
        json!({"action":"create","branch_name":"bad","start_ref":"--help","include_uncommitted":false}),
    );
    assert!(service.prepare(bad, &repo, "execute", "approval").is_err());
    let conflict = target(
        json!({"action":"create","branch_name":"test-conflict","start_ref":target_sha,"include_uncommitted":true}),
    );
    let error = match service.prepare(conflict, &repo, "execute", "approval") {
        Ok(_) => panic!("conflicting untracked file should fail"),
        Err(error) => error.to_string(),
    };
    assert!(error.contains("已保留分支 test-conflict"), "{error}");
    let destination = service
        .prepare(
            target(json!({"action":"enter","branch_name":"test-conflict"})),
            &repo,
            "execute",
            "readonly",
        )
        .unwrap();
    assert_eq!(
        fs::read_to_string(destination.directory.join("collision")).unwrap(),
        "in target\n"
    );
    assert_eq!(
        fs::read_to_string(repo.join("collision")).unwrap(),
        "local source\n"
    );
}

#[path = "../2026-11-27_Rust工作树活动退出/worktree_exit_contract.rs"]
mod exit_contract;
