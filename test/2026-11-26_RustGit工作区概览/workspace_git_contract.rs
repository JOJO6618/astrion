use std::{
    ffi::OsStr,
    fs,
    path::{Path, PathBuf},
    process::Command,
};

use serde_json::{Value, json};
use uuid::Uuid;

use super::{
    BINDING_UNAUTHORIZED, BINDING_UNAVAILABLE, CONVERSATION_MISSING, GIT_UNAVAILABLE, read,
};
use crate::{
    project_git::{canonicalize, runner::NULL_DEVICE},
    runtime::Runtime,
    workspace_catalog::RegisteredWorkspace,
};

struct Fixture {
    root: PathBuf,
    workspace: RegisteredWorkspace,
    runtime: Runtime,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("workspace-git-{}", Uuid::new_v4().simple()));
        let workspace = root.join("project");
        let data = root.join("data");
        fs::create_dir_all(&workspace).unwrap();
        fs::create_dir(&data).unwrap();
        let workspace = RegisteredWorkspace {
            id: "fixture".into(),
            label: "Fixture".into(),
            path: canonicalize(&workspace).unwrap(),
        };
        let runtime = Runtime::open(&data).unwrap();
        runtime
            .bind_workspace(&workspace.id, &workspace.path)
            .unwrap();
        Self {
            root,
            workspace,
            runtime,
        }
    }

    fn query(&self, diff: bool, query: &[(&str, &str)]) -> Result<Value, &'static str> {
        let query = serde_urlencoded::to_string(query).unwrap();
        read(&self.runtime, &self.workspace, Some(&query), diff)
    }

    fn repo(&self) {
        git(&self.workspace.path, ["init", "-b", "main"]);
        git(&self.workspace.path, ["config", "core.autocrlf", "false"]);
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn git(root: &Path, args: impl IntoIterator<Item = impl AsRef<OsStr>>) -> String {
    let mut command = Command::new("git");
    for (key, _) in std::env::vars_os() {
        if key
            .to_string_lossy()
            .to_ascii_uppercase()
            .starts_with("GIT_")
        {
            command.env_remove(key);
        }
    }
    let output = command
        .current_dir(root)
        .args(["-c", "commit.gpgsign=false"])
        .env("GIT_CONFIG_GLOBAL", NULL_DEVICE)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("XDG_CONFIG_HOME", root)
        .env("GIT_AUTHOR_NAME", "Fixture")
        .env("GIT_COMMITTER_NAME", "Fixture")
        .env("GIT_AUTHOR_EMAIL", "fixture@local.invalid")
        .env("GIT_COMMITTER_EMAIL", "fixture@local.invalid")
        .args(args)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap().trim().to_owned()
}

fn content(changes: &[(usize, &str)]) -> String {
    (1..=40)
        .map(|number| {
            if let Some((_, content)) = changes.iter().find(|(line, _)| *line == number) {
                format!("{content}\n")
            } else {
                format!("line {number:02}\n")
            }
        })
        .collect()
}

#[test]
fn projects_index_and_checkout_changes_with_per_file_context_and_fold_rows() {
    let fixture = Fixture::new();
    assert_eq!(
        fixture.query(false, &[]).unwrap(),
        json!({"has_git": false})
    );
    assert_eq!(
        fixture.query(true, &[]).unwrap(),
        json!({"has_git": false, "files": []})
    );
    fixture.repo();
    let repo = &fixture.workspace.path;
    let first = repo.join("first.txt");
    let other = repo.join("other name.txt");
    for file in [&first, &other] {
        fs::write(file, content(&[])).unwrap();
    }
    fs::write(repo.join("binary"), [0, 1, 2]).unwrap();
    git(repo, ["add", "-A"]);
    git(repo, ["commit", "-m", "Fixture"]);
    fs::write(&first, content(&[(8, "staged")])).unwrap();
    git(repo, ["add", "first.txt"]);
    fs::write(&first, content(&[(8, "staged"), (20, "unstaged")])).unwrap();
    fs::write(&other, content(&[(15, "another file")])).unwrap();
    fs::write(repo.join("binary"), [0, 3, 4, 5]).unwrap();
    fs::write(repo.join("untracked"), "Untracked contents\n").unwrap();

    let before = fs::read(repo.join(".git/index")).unwrap();
    let summary = fixture.query(false, &[]).unwrap();
    assert_eq!(
        summary,
        json!({"has_git":true,"project_name":"project","project_path":repo,
        "branch":"main","upstream":"","additions":3,"deletions":3,"git_worktree_enabled":false,"git_worktree":null})
    );
    let diff = fixture
        .query(
            true,
            &[
                ("context", "0"),
                (
                    "context_map",
                    r#"{"other name.txt":" 2 ", "first.txt": null}"#,
                ),
                (
                    "fold_context_map",
                    r#"{"first.txt":{"before:8":3,"before:20":5.8,"after":4},"ignored":[]}"#,
                ),
            ],
        )
        .unwrap();
    assert_eq!(diff["context"], 0);
    assert_eq!(diff["additions"], 3);
    let files = diff["files"].as_array().unwrap();
    assert_eq!(files.len(), 2);
    let first = &files[0];
    assert_eq!(first["path"], "first.txt");
    assert_eq!(first["after_hidden"], 16);
    assert_eq!(first["after_fold_key"], "after");
    let first_hunk = &first["hunks"][0];
    assert_eq!(first_hunk["before_fold_key"], "before:8");
    assert_eq!(first_hunk["before_hidden"], 4);
    assert_eq!(
        first_hunk["lines"][0],
        json!({"kind":"context","text":"line 05","old_line":5,"new_line":5})
    );
    assert_eq!(
        first_hunk["lines"][3],
        json!({"kind":"delete","text":"line 08","old_line":8,"new_line":null})
    );
    assert_eq!(
        first_hunk["lines"][4],
        json!({"kind":"add","text":"staged","old_line":null,"new_line":8})
    );
    let second = &first["hunks"][1];
    assert_eq!(second["before_hidden"], 6);
    assert_eq!(second["lines"][0]["old_line"], 15);
    assert_eq!(
        second["lines"].as_array().unwrap().last().unwrap()["text"],
        "line 24"
    );
    assert_eq!(files[1]["path"], "other name.txt");
    assert_eq!(files[1]["hunks"][0]["old_start"], 13);
    assert_eq!(files[1]["hunks"][0]["old_count"], 5);
    assert_eq!(before, fs::read(repo.join(".git/index")).unwrap());
    assert_eq!(
        fixture
            .query(true, &[("context", "junk"), ("context_map", "{")])
            .unwrap()["context"],
        3
    );
    assert_eq!(
        fixture
            .query(true, &[("context", "99999999999999999999")])
            .unwrap()["context"],
        200
    );
}

#[test]
fn follows_the_conversation_binding_with_grant_and_reports_stale_or_missing_bindings() {
    let fixture = Fixture::new();
    fixture.repo();
    let repo = &fixture.workspace.path;
    fs::write(repo.join("file.txt"), "original\n").unwrap();
    git(repo, ["add", "-A"]);
    git(repo, ["commit", "-m", "Fixture"]);
    let head = git(repo, ["rev-parse", "--short", "HEAD"]);
    let linked = canonicalize(&fixture.root).unwrap().join("linked");
    git(
        repo,
        [
            OsStr::new("worktree"),
            OsStr::new("add"),
            OsStr::new("--detach"),
            linked.as_os_str(),
            OsStr::new("HEAD"),
        ],
    );
    fs::write(linked.join("file.txt"), "linked work\n").unwrap();
    let mut binding = json!({"directory":linked,"active":true,"branch":""});
    let created = fixture
        .runtime
        .create_web_conversation(
            &fixture.workspace,
            json!({"git_worktree_enabled":true,"git_worktree":binding})
                .as_object()
                .unwrap()
                .clone(),
        )
        .unwrap();
    let id = created["conversation_id"].as_str().unwrap();
    let query = [("conversation_id", id)];
    assert_eq!(fixture.query(true, &query), Err(BINDING_UNAUTHORIZED));
    assert_eq!(
        fixture.query(false, &query).unwrap()["binding_error"],
        BINDING_UNAUTHORIZED
    );
    binding["grant"] = json!({"path":linked,"added":true});
    fixture
        .runtime
        .update_idle_web_metadata(&fixture.workspace.id, id, |metadata| {
            metadata.insert("git_worktree".into(), binding.clone());
        })
        .unwrap();
    assert_eq!(fixture.query(false, &[]).unwrap()["additions"], 0);
    let summary = fixture.query(false, &query).unwrap();
    assert_eq!(summary["branch"], format!("HEAD {head}"));
    assert_eq!(summary["git_worktree"], binding);
    assert_eq!(summary["project_name"], "project");
    let diff = fixture.query(true, &query).unwrap();
    assert_eq!(diff["project_name"], "linked");
    assert_eq!(
        diff["files"][0]["hunks"][0]["lines"][1]["text"],
        "linked work"
    );

    fs::remove_file(linked.join(".git")).unwrap();
    assert_eq!(
        fixture.query(false, &query).unwrap()["binding_error"],
        GIT_UNAVAILABLE
    );
    assert_eq!(
        fixture.query(true, &query).unwrap(),
        json!({"has_git":false,"files":[]})
    );
    fs::rename(&linked, fixture.root.join("renamed")).unwrap();
    assert_eq!(
        fixture.query(false, &query).unwrap()["binding_error"],
        BINDING_UNAVAILABLE
    );
    assert_eq!(fixture.query(true, &query), Err(BINDING_UNAVAILABLE));
    assert_eq!(
        fixture.query(false, &[("conversation_id", "conv_missing")]),
        Err(CONVERSATION_MISSING)
    );
}

#[cfg(unix)]
#[test]
fn patch_headers_preserve_literal_paths_and_content_prefixes() {
    let fixture = Fixture::new();
    fixture.repo();
    let repo = &fixture.workspace.path;
    let name = "测试\t 文件.txt";
    fs::write(repo.join(name), content(&[(8, "-- not a header")])).unwrap();
    git(repo, ["add", "-A"]);
    git(repo, ["commit", "-m", "Fixture"]);
    fs::write(repo.join(name), content(&[(8, "++ not a header")])).unwrap();
    let context = json!({name: 0}).to_string();
    let diff = fixture.query(true, &[("context_map", &context)]).unwrap();
    let file = &diff["files"][0];
    assert_eq!(file["path"], name);
    assert_eq!(file["old_path"], name);
    assert_eq!(
        file["hunks"][0]["lines"],
        json!([
            {"kind":"delete","text":"-- not a header","old_line":8,"new_line":null},
            {"kind":"add","text":"++ not a header","old_line":null,"new_line":8},
        ])
    );
}
