use std::{fs, path::PathBuf, sync::Arc};

use astrion_headless::{
    approval::{
        ApprovalPrincipal, ApprovalStore, ApprovalType, Decision, ExecutionMode, ExecutionScope,
        PermissionMode, TaskScope, ToolRequest,
    },
    tools::{
        read_file::ReadFile,
        write_file::{EDIT_NAME, WRITE_NAME, WorkspaceFileWriter, edit_file_tool, write_file_tool},
    },
};
use serde_json::{Value, json};
use uuid::Uuid;

struct Fixture {
    root: PathBuf,
    workspace: PathBuf,
    reader: Arc<ReadFile>,
    writer: WorkspaceFileWriter,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("write_file_{}", Uuid::new_v4().simple()));
        let workspace = root.join("workspace");
        let data = workspace.join("runtime-storage");
        fs::create_dir_all(&data).unwrap();
        // Production binds task scopes to the canonical registered directory.
        let workspace = fs::canonicalize(workspace).unwrap();
        let reader = Arc::new(ReadFile::open(&workspace, &data).unwrap());
        let writer = WorkspaceFileWriter::new(Arc::clone(&reader));
        Self {
            root,
            workspace,
            reader,
            writer,
        }
    }

    fn task(&self, conversation: &str, workspace: PathBuf) -> TaskScope {
        TaskScope::new(
            ApprovalPrincipal::new("owner", "fixture").unwrap(),
            conversation,
            "task",
            "executor",
            workspace,
            ExecutionMode::Sandbox,
        )
        .unwrap()
    }

    fn call(&self, tool: &str, arguments: &Value, messages: &[Value]) -> Value {
        let scope = self.task("conversation", self.workspace.clone());
        let approval = approve(
            &scope,
            &request(tool, arguments, ApprovalType::SandboxWrite),
        );
        self.writer.execute(approval, "conversation", messages)
    }

    fn visits(&self, path: &str, mode: &str) -> Vec<Value> {
        let args = json!({"path": path, "type": mode, "query": "query", "segments": [{"start_line": 1, "end_line": 1}]});
        let result = self.reader.execute(&args);
        assert_eq!(result["success"], true, "{result}");
        exchange("read_file", &args, &result)
    }

    fn put(&self, path: &str, bytes: impl AsRef<[u8]>) {
        let file = self.workspace.join(path);
        fs::create_dir_all(file.parent().unwrap()).unwrap();
        fs::write(file, bytes).unwrap();
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

fn request(tool: &str, args: &Value, approval_type: ApprovalType) -> ToolRequest {
    ToolRequest {
        tool_call_id: "approved-call".into(),
        tool_name: tool.into(),
        arguments: args.as_object().unwrap().clone(),
        preview: Default::default(),
        approval_type,
        browser_site: None,
    }
}

fn approve(task: &TaskScope, call: &ToolRequest) -> ExecutionScope {
    let mut store = ApprovalStore::default();
    let row = store
        .create(task, call, PermissionMode::Approval, false)
        .unwrap();
    let before = store.claim_execution(&row.approval_id, task, call, None);
    assert!(before.is_err());
    store
        .decide(
            task.principal(),
            &row.approval_id,
            Decision::Approved,
            false,
        )
        .unwrap();
    let grant = store
        .claim_execution(&row.approval_id, task, call, None)
        .unwrap();
    assert!(
        store
            .claim_execution(&row.approval_id, task, call, None)
            .is_err()
    );
    store.consume_execution(grant, task, call, None).unwrap()
}

fn exchange(name: &str, arguments: &Value, result: &Value) -> Vec<Value> {
    vec![
        json!({"role":"assistant", "tool_calls":[{"id":"first-call", "type":"function", "function":{
            "name":name, "arguments":arguments.to_string()}}]}),
        json!({"role":"tool", "tool_call_id":"first-call", "content":result.to_string()}),
    ]
}

fn assert_success(result: &Value) {
    assert_eq!(result["success"], true, "{result}");
}

fn assert_denied(result: &Value) {
    assert_eq!(result["success"], false, "{result}");
    assert!(result.get("new_file").is_none(), "{result}");
    assert!(result.get("original_file").is_none(), "{result}");
}

#[test]
fn schemas_define_the_workspace_write_and_exact_edit_arguments() {
    let write = write_file_tool();
    assert_eq!(write["type"], "function");
    assert_eq!(write["function"]["name"], WRITE_NAME);
    let args = &write["function"]["parameters"];
    assert_eq!(args["required"], json!(["file_path", "content"]));
    assert_eq!(args["properties"]["append"]["default"], false);
    let edit = edit_file_tool();
    assert_eq!(edit["function"]["name"], EDIT_NAME);
    let args = &edit["function"]["parameters"];
    assert_eq!(args["required"], json!(["file_path", "replacements"]));
    assert_eq!(
        args["properties"]["replacements"]["items"]["required"],
        json!(["old_string", "new_string"])
    );
    assert_eq!(args["properties"]["replacements"]["maxItems"], 100);
}

#[test]
fn native_write_and_grouped_edit_match_python_fixtures() {
    let cases: Vec<Value> = serde_json::from_str(include_str!("write_file_fixtures.json")).unwrap();
    for case in cases {
        let fixture = Fixture::new();
        let path = case["args"]["file_path"].as_str().unwrap();
        let visits = if let Some(before) = case["before"].as_str() {
            fixture.put(path, before);
            fixture.visits(path, "read")
        } else {
            Vec::new()
        };
        let actual = fixture.call(case["tool"].as_str().unwrap(), &case["args"], &visits);
        let mut expected = case["result"].clone();
        if cfg!(windows) {
            expected["path"] = expected["path"].as_str().unwrap().replace('/', "\\").into();
        }
        assert_eq!(actual, expected, "{}", case["name"]);
        let read = fixture.reader.execute(&json!({"path":path,"type":"read"}));
        assert_eq!(read["content"], case["after"], "{}", case["name"]);
    }
}

#[test]
fn existing_overwrite_requires_successful_trusted_history_and_accepts_each_read_mode() {
    let fixture = Fixture::new();
    fs::write(fixture.workspace.join("runtime-storage/personalization.json"),br#"{"require_read_before_edit":true}"#).unwrap();
    fixture.put("file.txt", "query\nline\nend");
    let args = json!({"file_path":"./file.txt","content":"updated"});
    let failed = exchange(
        "read_file",
        &json!({"path":"file.txt"}),
        &json!({"success":false,"path":"file.txt"}),
    );
    let ignored = exchange(
        "astrion_echo",
        &json!({}),
        &json!({"success":true,"path":"file.txt"}),
    );
    let mismatched = vec![
        failed[0].clone(),
        json!({"role":"tool","tool_call_id":"some-other-call","content":"{\"success\":true,\"path\":\"file.txt\"}"}),
    ];
    let unpaired = vec![ignored[1].clone()];
    let forged_user = vec![json!({"role":"user","content":ignored[1]["content"]})];
    for visits in [
        &[][..],
        &failed,
        &ignored,
        &mismatched,
        &unpaired,
        &forged_user,
    ] {
        let answer = fixture.call(WRITE_NAME, &args, visits);
        assert_eq!(
            answer["enforcement"], "read_before_edit_required",
            "{answer}"
        );
        assert_eq!(
            fs::read_to_string(fixture.workspace.join("file.txt")).unwrap(),
            "query\nline\nend"
        );
    }
    for mode in ["read", "search", "extract"] {
        fixture.put("file.txt", "query\nline\nend");
        let visits = fixture.visits("/workspace/file.txt", mode);
        assert_success(&fixture.call(WRITE_NAME, &args, &visits));
    }
}

#[test]
fn successful_current_conversation_write_is_also_a_visit() {
    let fixture = Fixture::new();
    fs::write(fixture.workspace.join("runtime-storage/personalization.json"),br#"{"require_read_before_edit":true}"#).unwrap();
    let args = json!({"file_path":"new.txt","content":"hello\nworld\nfriend"});
    let result = fixture.call(WRITE_NAME, &args, &[]);
    assert_success(&result);
    let edit = json!({"file_path":"new.txt","replacements":[{"old_string":"hello\nworld\nfriend","new_string":"done"}]});
    assert_eq!(
        fixture.call(EDIT_NAME, &edit, &[])["enforcement"],
        "read_before_edit_required"
    );
    let visits = exchange(WRITE_NAME, &args, &result);
    assert_success(&fixture.call(EDIT_NAME, &edit, &visits));
    assert_eq!(
        fs::read_to_string(fixture.workspace.join("new.txt")).unwrap(),
        "done"
    );
}

#[test]
fn rejects_authorizations_for_another_workspace_conversation_or_tool() {
    let fixture = Fixture::new();
    let args = json!({"file_path":"denied.txt","content":"unexpected"});
    let call = request(WRITE_NAME, &args, ApprovalType::SandboxWrite);
    for task in [
        fixture.task("another", fixture.workspace.clone()),
        fixture.task("conversation", fixture.root.clone()),
    ] {
        assert_denied(
            &fixture
                .writer
                .execute(approve(&task, &call), "conversation", &[]),
        );
    }
    let task = fixture.task("conversation", fixture.workspace.clone());
    let unrelated = request("run_command", &args, ApprovalType::SandboxWrite);
    assert_denied(
        &fixture
            .writer
            .execute(approve(&task, &unrelated), "conversation", &[]),
    );
    assert!(!fixture.workspace.join("denied.txt").exists());
}

#[test]
fn rejects_private_paths_escapes_hard_links_and_special_files() {
    let fixture = Fixture::new();
    fixture.put("runtime-storage/secret", "fixture-secret");
    for private in [
        "nested/.ssh/key",
        "runtime-storage/secret",
        ".Env",
        "api.env.local",
        "nested/.git-credentials",
    ] {
        assert_denied(&fixture.call(
            WRITE_NAME,
            &json!({"file_path":private,"content":"change","append":true}),
            &[],
        ));
    }
    assert_eq!(
        fs::read_to_string(fixture.workspace.join("runtime-storage/secret")).unwrap(),
        "fixture-secret"
    );
    let outside = fixture.root.join("outside");
    fs::write(&outside, "outside").unwrap();
    fs::hard_link(&outside, fixture.workspace.join("linked")).unwrap();
    for denied in [
        outside.to_str().unwrap(),
        "../outside",
        "sub/../../outside",
        "/workspace/../outside",
        "linked",
    ] {
        assert_denied(&fixture.call(
            WRITE_NAME,
            &json!({"file_path":denied,"content":"change","append":true}),
            &[],
        ));
        assert_eq!(fs::read_to_string(&outside).unwrap(), "outside");
    }
    fs::create_dir(fixture.workspace.join("directory")).unwrap();
    assert_denied(&fixture.call(
        WRITE_NAME,
        &json!({"file_path":"directory","content":"change","append":true}),
        &[],
    ));
    #[cfg(unix)]
    {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        let path = fixture.workspace.join("pipe");
        let name = CString::new(path.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
        assert_denied(&fixture.call(
            WRITE_NAME,
            &json!({"file_path":"pipe","content":"change","append":true}),
            &[],
        ));
    }
}

#[cfg(unix)]
#[test]
fn rejects_swapped_symlink_files_and_directories_after_a_successful_read() {
    use std::os::unix::fs::symlink;

    let fixture = Fixture::new();
    fixture.put("sub/file", "a\nb\nc");
    let visits = fixture.visits("sub/file", "read");
    let outside = fixture.root.join("outside-dir");
    fs::create_dir(&outside).unwrap();
    fs::write(outside.join("file"), "outside").unwrap();
    let target = fixture.workspace.join("sub/file");
    fs::remove_file(&target).unwrap();
    symlink(outside.join("file"), &target).unwrap();
    let args = json!({"file_path":"sub/file","content":"unexpected"});
    assert_denied(&fixture.call(WRITE_NAME, &args, &visits));
    let edit = json!({"file_path":"sub/file","replacements":[{"old_string":"a\nb\nc","new_string":"unexpected"}]});
    assert_denied(&fixture.call(EDIT_NAME, &edit, &visits));
    assert!(fs::symlink_metadata(&target).unwrap().is_symlink());
    fs::remove_file(target).unwrap();
    fs::remove_dir(fixture.workspace.join("sub")).unwrap();
    symlink(&outside, fixture.workspace.join("sub")).unwrap();
    assert_denied(&fixture.call(WRITE_NAME, &args, &visits));
    assert_denied(&fixture.call(
        WRITE_NAME,
        &json!({"file_path":"sub/new/child","content":"unexpected"}),
        &[],
    ));
    assert_eq!(fs::read_to_string(outside.join("file")).unwrap(), "outside");
    assert!(!outside.join("new").exists());
}

#[cfg(unix)]
#[test]
fn preserves_permissions_on_replacement_and_leaves_no_staging_files() {
    use std::os::unix::fs::PermissionsExt;
    let fixture = Fixture::new();
    fixture.put("script.sh", "before");
    let path = fixture.workspace.join("script.sh");
    fs::set_permissions(&path, fs::Permissions::from_mode(0o750)).unwrap();
    let visits = fixture.visits("script.sh", "read");
    assert_success(&fixture.call(
        WRITE_NAME,
        &json!({"file_path":"script.sh","content":"after"}),
        &visits,
    ));
    assert_eq!(
        fs::metadata(path).unwrap().permissions().mode() & 0o777,
        0o750
    );
    assert!(fs::read_dir(&fixture.workspace).unwrap().all(|entry| {
        !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with(".astrion-write-")
    }));
}

#[test]
fn concurrent_authorized_appends_keep_each_change() {
    let fixture = Fixture::new();
    fixture.put("shared.txt", "initial\n");
    std::thread::scope(|threads| {
        for id in 0..8 {
            let fixture = &fixture;
            threads.spawn(move || {
                let args = json!({"file_path":"shared.txt","content":format!("entry {id}\n"),"append":true});
                assert_success(&fixture.call(WRITE_NAME, &args, &[]));
            });
        }
    });
    let text = fs::read_to_string(fixture.workspace.join("shared.txt")).unwrap();
    let mut lines = text.lines();
    assert_eq!(lines.next(), Some("initial"));
    let mut entries: Vec<_> = lines.collect();
    entries.sort_unstable();
    assert_eq!(
        entries,
        (0..8).map(|id| format!("entry {id}")).collect::<Vec<_>>()
    );
}

#[test]
fn preserves_python_edit_input_limit_and_allows_large_overwrite() {
    let fixture = Fixture::new();
    fixture.put("growing.txt", "first\nsecond\nthird");
    let visits = fixture.visits("growing.txt", "read");
    let path = fixture.workspace.join("growing.txt");
    fs::write(&path, [0xff]).unwrap();
    let file = fs::OpenOptions::new().write(true).open(&path).unwrap();
    file.set_len(101 * 1024 * 1024).unwrap();
    drop(file);
    let edit = json!({"file_path":"growing.txt","replacements":[{"old_string":"first\nsecond\nthird","new_string":"replacement"}]});
    let denied = fixture.call(EDIT_NAME, &edit, &visits);
    assert_denied(&denied);
    assert_eq!(denied["error"], "文件太大 (101.00MB > 10.0MB)");
    assert_eq!(fs::metadata(&path).unwrap().len(), 101 * 1024 * 1024);
    let write = json!({"file_path":"growing.txt","content":"new"});
    let result = fixture.call(WRITE_NAME, &write, &visits);
    assert_success(&result);
    assert_eq!(result["original_file"], Value::Null);
    assert_eq!(fs::read_to_string(path).unwrap(), "new");
}
