use std::fs;
use std::path::PathBuf;

use astrion_headless::tools::read_file::{ReadFile, schema};
use serde_json::{Value, json};
use uuid::Uuid;

struct Fixture {
    root: PathBuf,
    workspace: PathBuf,
    data: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("read_file_test_{}", Uuid::new_v4().simple()));
        fs::create_dir(&root).unwrap();
        let workspace = root.join("workspace");
        let data = workspace.join("runtime-storage");
        fs::create_dir_all(&data).unwrap();
        Self {
            root,
            workspace,
            data,
        }
    }

    fn tool(&self) -> ReadFile {
        ReadFile::open(&self.workspace, &self.data).unwrap()
    }

    fn write(&self, name: &str, contents: impl AsRef<[u8]>) {
        let path = self.workspace.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, contents).unwrap();
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.root).unwrap();
    }
}

fn read(tool: &ReadFile, path: &str) -> Value {
    tool.execute(&json!({"path": path, "type": "read"}))
}

fn assert_rejected(tool: &ReadFile, path: &str) {
    let result = read(tool, path);
    assert_eq!(
        result["success"], false,
        "unexpected result for {path}: {result}"
    );
    assert!(
        result.get("content").is_none(),
        "unexpected content for {path}: {result}"
    );
}

#[test]
fn chat_tool_has_all_read_search_and_extract_parameters() {
    let tool = schema();
    assert_eq!(tool["type"], "function");
    assert_eq!(tool["function"]["name"], "read_file");
    let params = &tool["function"]["parameters"];
    assert_eq!(params["required"], json!(["path", "type"]));
    let props = &params["properties"];
    assert_eq!(props["type"]["enum"], json!(["read", "search", "extract"]));
    for key in [
        "path",
        "max_chars",
        "start_line",
        "end_line",
        "query",
        "max_matches",
        "context_before",
        "context_after",
        "case_sensitive",
        "segments",
    ] {
        assert!(props.get(key).is_some(), "missing schema property {key}");
    }
    assert_eq!(props["segments"]["minItems"], 1);
    assert_eq!(
        props["segments"]["items"]["required"],
        json!(["start_line", "end_line"])
    );
}

#[test]
fn read_search_extract_results_match_python() {
    let fixtures: Value = serde_json::from_str(include_str!("read_file_fixtures.json")).unwrap();
    let fixture = Fixture::new();
    for (name, content) in fixtures["files"].as_object().unwrap() {
        fixture.write(name, content.as_str().unwrap());
    }
    let tool = fixture.tool();
    for case in fixtures["cases"].as_array().unwrap() {
        assert_eq!(
            tool.execute(&case["args"]),
            case["result"],
            "{}",
            case["name"]
        );
    }
}

#[test]
fn accepts_relative_workspace_alias_and_in_scope_absolute_paths() {
    let fixture = Fixture::new();
    fixture.write("nested/text.txt", "approved\n");
    let tool = fixture.tool();
    let absolute = fixture.workspace.join("nested/text.txt");
    for path in [
        "./nested/text.txt",
        "/workspace/nested/text.txt",
        absolute.to_str().unwrap(),
    ] {
        let result = read(&tool, path);
        assert_eq!(result["content"], "approved\n", "{path}: {result}");
        assert_eq!(
            PathBuf::from(result["path"].as_str().unwrap()),
            PathBuf::from("nested/text.txt")
        );
    }
}

#[test]
fn denies_parent_traversal_sibling_prefixes_and_default_private_paths() {
    let fixture = Fixture::new();
    fixture.write("text.txt", "approved\n");
    fixture.write(".ssh/fixture", "synthetic private content\n");
    fixture.write("runtime-storage/tasks.json", "synthetic private metadata\n");
    fixture.write("runtime-storage/host_api_token", "synthetic test value\n");
    let sibling = fixture.root.join("workspace-other");
    fs::create_dir(&sibling).unwrap();
    fs::write(sibling.join("outside.txt"), "synthetic outside\n").unwrap();
    let tool = fixture.tool();
    let absolute_outside = sibling.join("outside.txt");
    for path in [
        "../workspace-other/outside.txt",
        "/workspace/../workspace-other/outside.txt",
        absolute_outside.to_str().unwrap(),
        "nested/../text.txt",
        ".ssh/fixture",
        ".env",
        "nested/app.env.example",
        ".ENV.local",
        ".npmrc",
        "Library/Keychains/key",
        "runtime-storage/tasks.json",
        "runtime-storage/host_api_token",
    ] {
        assert_rejected(&tool, path);
        assert!(
            read(&tool, path)["error"]
                .as_str()
                .unwrap()
                .contains("工作区"),
            "{path}"
        );
    }
    let extract = tool.execute(&json!({"type": "extract", "path": "runtime-storage/tasks.json", "segments": [{"start_line": 1, "end_line": 1}]}));
    let search = tool.execute(
        &json!({"type": "search", "path": "runtime-storage/tasks.json", "query": "synthetic"}),
    );
    assert_eq!(extract["success"], false);
    assert_eq!(search["success"], false);
    assert!(ReadFile::open(&fixture.data, &fixture.data).is_err());
}

#[test]
fn blocks_hardlinks_and_moved_runtime_data() {
    let fixture = Fixture::new();
    let outside = fixture.root.join("outside.txt");
    fs::write(&outside, "outside fixture").unwrap();
    fs::hard_link(&outside, fixture.workspace.join("hardlink.txt")).unwrap();
    fixture.write("runtime-storage/private.json", "private fixture");
    let tool = fixture.tool();
    assert_rejected(&tool, "hardlink.txt");
    fs::rename(&fixture.data, fixture.workspace.join("moved")).unwrap();
    assert_rejected(&tool, "moved/private.json");
}

#[cfg(unix)]
#[test]
fn blocks_symlink_targets_and_ancestors_even_within_workspace() {
    use std::os::unix::fs::symlink;

    let fixture = Fixture::new();
    fixture.write("folder/file.txt", "inside fixture");
    fs::write(fixture.root.join("outside.txt"), "outside fixture").unwrap();
    symlink(
        "folder/file.txt",
        fixture.workspace.join("inside-alias.txt"),
    )
    .unwrap();
    symlink(
        "../outside.txt",
        fixture.workspace.join("outside-alias.txt"),
    )
    .unwrap();
    symlink("folder", fixture.workspace.join("inside-dir")).unwrap();
    symlink("..", fixture.workspace.join("outside-dir")).unwrap();
    let tool = fixture.tool();
    for path in [
        "inside-alias.txt",
        "outside-alias.txt",
        "inside-dir/file.txt",
        "outside-dir/outside.txt",
    ] {
        assert_rejected(&tool, path);
    }
}

#[cfg(unix)]
#[test]
fn a_concurrently_substituted_symlink_never_returns_outside_content() {
    use std::os::unix::fs::symlink;
    use std::sync::Barrier;
    use std::thread;

    let fixture = Fixture::new();
    fixture.write("race.txt", "inside fixture");
    let outside = fixture.root.join("outside.txt");
    fs::write(&outside, "outside fixture").unwrap();
    let target = fixture.workspace.join("race.txt");
    let next = fixture.workspace.join("staging.txt");
    let tool = fixture.tool();
    let start = Barrier::new(2);
    thread::scope(|scope| {
        scope.spawn(|| {
            start.wait();
            for _ in 0..200 {
                symlink(&outside, &next).unwrap();
                fs::rename(&next, &target).unwrap();
                thread::yield_now();
                fs::write(&next, "inside fixture").unwrap();
                fs::rename(&next, &target).unwrap();
                thread::yield_now();
            }
        });
        start.wait();
        for _ in 0..600 {
            let result = read(&tool, "race.txt");
            if result["success"] == true {
                assert_eq!(result["content"], "inside fixture");
            } else {
                assert!(result.get("content").is_none());
            }
            thread::yield_now();
        }
    });
}

#[cfg(unix)]
#[test]
fn startup_directory_capability_survives_path_replacement() {
    let fixture = Fixture::new();
    fixture.write("file.txt", "the selected workspace");
    let tool = fixture.tool();
    fs::rename(&fixture.workspace, fixture.root.join("moved-workspace")).unwrap();
    fixture.write("file.txt", "replacement at original pathname");
    assert_eq!(read(&tool, "file.txt")["content"], "the selected workspace");
}

#[cfg(unix)]
#[test]
fn declines_fifo_without_blocking() {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let fixture = Fixture::new();
    let path = fixture.workspace.join("pipe");
    let path = CString::new(path.as_os_str().as_bytes()).unwrap();
    // SAFETY: path is a live NUL-terminated pathname in the isolated fixture workspace.
    assert_eq!(unsafe { libc::mkfifo(path.as_ptr(), 0o600) }, 0);
    assert_rejected(&fixture.tool(), "pipe");
}

#[test]
fn reports_invalid_utf8_oversize_and_line_ranges_across_index_blocks() {
    let fixture = Fixture::new();
    fixture.write("invalid.txt", [0xff, b'\n']);
    let large = fs::File::create(fixture.workspace.join("large.txt")).unwrap();
    large.set_len(100 * 1024 * 1024 + 1).unwrap();
    fixture.write(
        "lines.txt",
        (1..=513)
            .map(|index| format!("{index}\n"))
            .collect::<String>(),
    );
    let tool = fixture.tool();
    assert!(
        read(&tool, "invalid.txt")["error"]
            .as_str()
            .unwrap()
            .contains("UTF-8")
    );
    assert!(
        read(&tool, "large.txt")["error"]
            .as_str()
            .unwrap()
            .contains("太大")
    );
    assert_rejected(&tool, "runtime-storage");
    let result = tool
        .execute(&json!({"path": "lines.txt", "type": "read", "start_line": 255, "end_line": 258}));
    assert_eq!(result["content"], "255\n256\n257\n258\n");
    assert_eq!(result["total_lines"], 513);
}
