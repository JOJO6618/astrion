use std::{fs, process::Command};

use super::*;
use crate::{legacy::LegacyDocument, runtime::{Runtime, store::TaskStart}};
use tokio::sync::oneshot;
use uuid::Uuid;

#[test]
fn archives_edit_time_net_bytes_and_restores_a_file_removed_during_task() {
    let scratch = std::env::temp_dir().join(format!("astrion-history-{}", Uuid::new_v4().simple()));
    let root = scratch.join("work");
    let data = scratch.join("data");
    fs::create_dir_all(&root).unwrap();
    let root = root.canonicalize().unwrap();
    let runtime = Runtime::open(&data).unwrap();
    runtime.bind_workspace("history", &root).unwrap();
    let mut store = runtime.store().unwrap();
    let id = "conv_history_test";
    store.persist("history", LegacyDocument::from_value(json!({
        "id":id,"title":"fixture","metadata":{"project_path":root},"messages":[
            {"role":"user","content":"Fixture request","message_id":"anchor"}
        ]
    })).unwrap(), true).unwrap();
    let timestamp = super::super::now();
    let (cancel, _) = oneshot::channel();
    let mut task = Task::new(TaskStart {
        id: "fixture-task".into(), created_at: timestamp, message: "Fixture request".into(),
        model_key: None, message_source: None,
    }, "history", id.into(), cancel);
    task.version_message_id = Some("anchor".into());
    store.register_task(task);
    let path = "带 空格.txt";
    let put = |store: &mut Store, path: &str, before: Option<&str>, after: &str| {
        record(store, "fixture-task", &root, RecordedWrite {
            path: path.into(),
            before: before.map_or(FileText::Missing, |text| FileText::Text(text.into())),
            after: FileText::Text(after.into()),
        }, "write").unwrap();
    };
    put(&mut store, path, Some("first\r\noriginal"), "first\r\nintermediate");
    put(&mut store, path, Some("first\r\nintermediate"), "first\r\nlatest");
    put(&mut store, "empty.txt", None, "");
    fs::write(root.join(path), "first\r\noriginal").unwrap();
    let history = root.join(".astrion/modify_history").join(id);
    let file = fs::read_dir(&history).unwrap().next().unwrap().unwrap().path();
    let patch = fs::read_to_string(&file).unwrap();
    assert!(patch.contains("+latest\n\\ No newline at end of file"));
    assert!(!patch.contains("intermediate"));
    assert_eq!(fs::read_dir(&history).unwrap().count(), 1);
    let git_apply = || {
        let output = Command::new("git").args(["apply","--whitespace=nowarn"])
            .arg(&file).current_dir(&root).output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    };
    git_apply();
    assert_eq!(fs::read(root.join(path)).unwrap(), b"first\r\nlatest");
    assert_eq!(fs::read(root.join("empty.txt")).unwrap(), b"");
    fs::remove_file(root.join(path)).unwrap();
    fs::remove_file(root.join("empty.txt")).unwrap();
    finish(&mut store, "fixture-task", timestamp + 10.0);
    let patch = fs::read_to_string(&file).unwrap();
    assert!(patch.contains("任务结束时已不存在"));
    assert!(!patch.contains("进行中"));
    git_apply();
    assert_eq!(fs::read(root.join(path)).unwrap(), b"first\r\nlatest");
    let raw = store.read("history", id).unwrap().into_raw();
    let files = raw["messages"][0]["metadata"]["edit_summary"]["files"].as_array().unwrap();
    assert_eq!(files.len(), 2);
    let edited = files.iter().find(|entry| entry["path"] == path).unwrap();
    assert_eq!(edited["added"], 1);
    assert_eq!(edited["removed"], 1);
    assert!(!edited["lines"].to_string().contains("intermediate"));
    assert_eq!(raw["metadata"]["edited_files"].as_array().unwrap().len(), 2);
    let snapshot = store.tasks["fixture-task"].poll(0);
    let kinds = snapshot["data"]["events"].as_array().unwrap().iter()
        .filter_map(|event| event["type"].as_str()).collect::<Vec<_>>();
    assert!(kinds.contains(&"edit_summary_updated"));
    assert!(kinds.contains(&"edited_files_updated"));
    drop(store);
    drop(runtime);
    fs::remove_dir_all(scratch).unwrap();
}

#[test]
fn full_net_reversal_removes_the_patch_but_not_the_work_entry() {
    let mut entry = EditHistory::default();
    let root = Path::new(".");
    entry.add(root, "text", RecordedWrite {
        path: "text".into(), before: FileText::Text("first".into()), after: FileText::Text("changed".into()),
    });
    entry.add(root, "text", RecordedWrite {
        path: "text".into(), before: FileText::Text("changed".into()), after: FileText::Text("first".into()),
    });
    assert_eq!(entry.summary("", false)["files"], json!([]));
}
