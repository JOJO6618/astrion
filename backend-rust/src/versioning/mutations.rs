use std::{io, path::Path};

use cap_std::fs::Dir;
use serde_json::{Map, Value, json};
use uuid::Uuid;

use super::{
    CONVERSATION_ONLY, INPUTS, RuntimeError, Store, WORKSPACE_AND_CONVERSATION, checkpoint_missing,
    deep, files,
    integration::{execution_root, require_idle, timestamp},
    open_data_directory, python_bool, sequence, shallow, shallow_write, storage, tracking_mode,
    transaction::{Scope, Transaction},
    verify_record_owner,
};
use crate::{
    legacy::{LegacyDocument, LegacyKind},
    runtime::{legacy_error, store::Task},
};

const META: &str = "meta.json";
const EMPTY_TREE: &str = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

pub(super) struct Directories {
    pub records: Dir,
    pub session: Dir,
}

impl Directories {
    pub fn open(store: &Store, workspace: &str, id: &str) -> Result<Self, RuntimeError> {
        store.conversation_path(workspace, id)?;
        let data = open_data_directory(&store.data_dir).map_err(storage)?;
        verify_record_owner(store, &data, workspace, id)?;
        let records = files::child(&data, "save", true)
            .map_err(storage)?
            .expect("save created");
        let session = files::child(&records, id, true)
            .map_err(storage)?
            .expect("session records created");
        Ok(Self { records, session })
    }
}

pub(super) fn configure(
    store: &Store,
    workspace: &str,
    id: &str,
    enabled: bool,
    requested: Option<&str>,
) -> Result<Value, RuntimeError> {
    let document = agent(store, workspace, id)?;
    let record = Directories::open(store, workspace, id)?;
    let _lock = files::lock(&record.session).map_err(storage)?;
    let current = document.raw();
    let config = &current["metadata"]["versioning"];
    let full = is_full(config);
    let tracking = if full {
        requested.map_or_else(
            || tracking_mode(&config["tracking_mode"]),
            |mode| tracking_mode(&json!(mode)),
        )
    } else {
        CONVERSATION_ONLY
    };
    let root = execution_root(store, workspace, current)?;
    let mut rows = rows(&record.session)?;
    let mut previous_meta = meta(&record.session)?;
    let mut initial = None;
    if enabled && !rows.iter().any(|row| sequence(row) == 0) {
        let tree = if tracking == WORKSPACE_AND_CONVERSATION && full {
            deep::baseline_tree(store, &record.session, id, &root)?
        } else {
            EMPTY_TREE.into()
        };
        let label = if crate::web_assets::personalization::english(&store.data_dir) {
            "Initial State"
        } else {
            "初始状态"
        };
        let mut row = checkpoint_row(0, label, -1, "initial", &root, tracking, &tree, None);
        row["snapshot_file"] = json!("snapshots/0.json");
        rows.push(row);
        initial = Some(snapshot(current, -1, "initial", None));
    }
    let (seq, tree) = latest(&rows);
    let timestamp = timestamp();
    let versioning = next_config(config, enabled, full, tracking, seq, &tree, &timestamp);
    set_meta(&mut previous_meta, &versioning, &tree);
    let mut next = current.clone();
    next["metadata"]["versioning"] = versioning;
    let next = LegacyDocument::from_value(next).map_err(legacy_error)?;
    let mut tx = Transaction::new(
        &record.session,
        &record.records,
        workspace,
        id,
        None,
        Some(current),
        &next,
    )?;
    if let Some(initial) = initial {
        put_json(&mut tx, id, "snapshots/0.json", &initial)?;
        put_rows(&mut tx, id, &rows)?;
    }
    put_json(&mut tx, id, META, &previous_meta)?;
    tx.commit(store, next)?;
    Ok(
        json!({"conversation_id":id,"enabled":enabled,"mode":"overwrite",
        "tracking_mode":tracking,"last_commit":tree,"last_input_seq":seq}),
    )
}

pub(super) fn checkpoint(
    store: &Store,
    task: &Task,
    current: &Value,
    root: &Path,
    message_index: usize,
    message_id: &str,
    run_status: &str,
) -> Result<(), RuntimeError> {
    let id = &task.conversation_id;
    let workspace = &task.workspace_id;
    let record = Directories::open(store, workspace, id)?;
    let _lock = files::lock(&record.session).map_err(storage)?;
    let mut rows = rows(&record.session)?;
    if rows
        .iter()
        .any(|row| row["shallow_message_id"] == message_id || row["rust_task_id"] == task.start.id)
    {
        return Ok(());
    }
    let mut previous_meta = meta(&record.session)?;
    let config = &current["metadata"]["versioning"];
    let full = is_full(config);
    let tracking = tracking_mode(&config["tracking_mode"]);
    let (last, tree) = latest(&rows);
    let seq = last
        .max(config["last_input_seq"].as_u64().unwrap_or(0))
        .max(previous_meta["last_input_seq"].as_u64().unwrap_or(0))
        .checked_add(1)
        .ok_or_else(|| storage(io::Error::other("Checkpoint sequence exhausted")))?;
    let timestamp = timestamp();
    let mut index = None;
    let mut row = if full && tracking == WORKSPACE_AND_CONVERSATION {
        let result = deep::checkpoint(
            store,
            &record.session,
            id,
            root,
            tree.as_str().unwrap_or(EMPTY_TREE),
        )?;
        let mut row = checkpoint_row(
            seq,
            &task.start.message,
            message_index as i64,
            run_status,
            root,
            tracking,
            &result.tree,
            tree.as_str(),
        );
        row.as_object_mut()
            .expect("checkpoint row object")
            .extend(result.summary);
        index = Some(result.index);
        row
    } else {
        checkpoint_row(
            seq,
            &task.start.message,
            message_index as i64,
            run_status,
            root,
            tracking,
            EMPTY_TREE,
            Some(EMPTY_TREE),
        )
    };
    row["snapshot_file"] = json!(format!("snapshots/{seq}.json"));
    row["rust_task_id"] = json!(task.start.id);
    let has_shallow = !full
        && shallow::History::open(&record.session)
            .map_err(storage)?
            .is_some();
    if has_shallow {
        row["shallow_message_id"] = json!(message_id);
    }
    rows.push(row.clone());
    let versioning = next_config(
        config,
        true,
        full,
        tracking,
        seq,
        &row["tree_hash"],
        &timestamp,
    );
    set_meta(&mut previous_meta, &versioning, &row["tree_hash"]);
    let mut next = current.clone();
    next["metadata"]["versioning"] = versioning;
    let next = LegacyDocument::from_value(next).map_err(legacy_error)?;
    let mut tx = Transaction::new(
        &record.session,
        &record.records,
        workspace,
        id,
        None,
        Some(current),
        &next,
    )?;
    if has_shallow {
        shallow_write::checkpoint(
            &record.session,
            id,
            root,
            &store.data_dir,
            message_id,
            &timestamp,
            &mut tx,
        )
        .map_err(storage)?;
    }
    if let Some(index) = index {
        tx.put(&format!("{id}/git/index"), index).map_err(storage)?;
    }
    let state = snapshot(
        current,
        message_index as i64,
        run_status,
        has_shallow.then_some(message_id),
    );
    put_json(&mut tx, id, &format!("snapshots/{seq}.json"), &state)?;
    put_rows(&mut tx, id, &rows)?;
    put_json(&mut tx, id, META, &previous_meta)?;
    tx.commit(store, next)
}

pub(super) fn restore(
    store: &Store,
    workspace: &str,
    id: &str,
    body: &Value,
) -> Result<Value, RuntimeError> {
    let document = agent(store, workspace, id)?;
    let current = document.raw();
    let config = &current["metadata"]["versioning"];
    if !python_bool(&config["enabled"]) {
        return Err(RuntimeError::bad_request("Versioning is not enabled"));
    }
    let seq = if body["seq"].is_null() {
        0
    } else {
        as_u64(&body["seq"])
            .ok_or_else(|| RuntimeError::bad_request("Invalid checkpoint sequence"))?
    };
    let copy = body["mode"]
        .as_str()
        .is_some_and(|mode| mode.eq_ignore_ascii_case("copy"));
    let record = Directories::open(store, workspace, id)?;
    let _lock = files::lock(&record.session).map_err(storage)?;
    let rows = rows(&record.session)?;
    let row = rows
        .iter()
        .rev()
        .find(|row| sequence(row) == seq)
        .ok_or_else(checkpoint_missing)?;
    let tracking = tracking_mode(
        body.get("tracking_mode")
            .or_else(|| row.get("tracking_mode"))
            .unwrap_or(&config["tracking_mode"]),
    );
    let full = is_full(config);
    let root = execution_root(store, workspace, current)?;
    let current_snapshot = load_snapshot(&record.session, row)?;
    let shallow_id =
        super::message_id(row).or_else(|| current_snapshot.as_ref().and_then(super::message_id));
    let full_workspace = full && tracking == WORKSPACE_AND_CONVERSATION;
    if full_workspace
        && (tracking_mode(&row["tracking_mode"]) != WORKSPACE_AND_CONVERSATION
            || row["tree_hash"].as_str().is_none_or(str::is_empty))
    {
        return Err(RuntimeError::bad_request(
            "Checkpoint has no workspace tree",
        ));
    }
    if full_workspace || (!full && shallow_id.is_some()) {
        require_idle(store, workspace, None)?;
        verify_origin(row, &root)?;
    }
    let target_id = if copy {
        format!("conv_{}", Uuid::new_v4().simple())
    } else {
        id.to_owned()
    };
    let timestamp = timestamp();
    let tree = row
        .get("tree_hash")
        .filter(|value| value.is_string())
        .cloned()
        .unwrap_or_else(|| json!(EMPTY_TREE));
    let versioning = next_config(config, true, full, tracking, seq, &tree, &timestamp);
    let mut previous_meta = meta(&record.session)?;
    set_meta(&mut previous_meta, &versioning, &tree);
    previous_meta["last_restored_tree_hash"] = tree.clone();
    let next = restored_document(
        current,
        current_snapshot.as_ref(),
        row,
        &target_id,
        &timestamp,
        versioning,
        copy,
    )?;
    let mut tx = Transaction::new(
        &record.session,
        &record.records,
        workspace,
        id,
        (full_workspace || (!full && shallow_id.is_some())).then_some(root.as_path()),
        (!copy).then_some(current),
        &next,
    )?;
    if full_workspace {
        deep::restore(
            store,
            &record.session,
            id,
            &target_id,
            &root,
            tree.as_str().expect("tree validated"),
            &mut tx,
        )?;
    }
    let kept: Vec<_> = rows
        .iter()
        .filter(|row| sequence(row) <= seq)
        .cloned()
        .collect();
    if !full {
        let history = shallow::History::open(&record.session).map_err(storage)?;
        if let Some(shallow_id) = shallow_id {
            let history = history
                .as_ref()
                .ok_or_else(|| storage(io::Error::other("Missing shallow backup history")))?;
            shallow_write::restore(history, &root, &store.data_dir, shallow_id, &mut tx)
                .map_err(storage)?;
        }
        if let Some(history) = history.as_ref() {
            let mut retained = Vec::new();
            for row in &kept {
                let snapshot = load_snapshot(&record.session, row)?;
                if let Some(message_id) =
                    super::message_id(row).or_else(|| snapshot.as_ref().and_then(super::message_id))
                {
                    retained.push(message_id.to_owned());
                }
            }
            shallow_write::save_branch(history, &target_id, &retained, copy, &mut tx)
                .map_err(storage)?;
        }
    }
    for row in &rows {
        let Some(name) = snapshot_name(row)? else {
            continue;
        };
        let path = format!("{target_id}/snapshots/{name}");
        if copy && sequence(row) <= seq {
            if let Some(state) = read_snapshot_bytes(&record.session, &name)? {
                tx.put(&path, state).map_err(storage)?;
            }
        } else if !copy && sequence(row) > seq {
            tx.change(Scope::Records, &path, None).map_err(storage)?;
        }
    }
    put_rows(&mut tx, &target_id, &kept)?;
    put_json(&mut tx, &target_id, META, &previous_meta)?;
    tx.commit(store, next)?;
    Ok(
        json!({"restore_mode":if copy {"copy"} else {"overwrite"},"conversation_id":target_id,
        "restored_seq":seq,"restored_commit":row.get("tree_hash"),"tracking_mode":tracking,"source_conversation_id":id}),
    )
}

fn agent(store: &Store, workspace: &str, id: &str) -> Result<LegacyDocument, RuntimeError> {
    let document = store.read(workspace, id)?;
    if document.kind() != LegacyKind::Agent {
        return Err(RuntimeError::session_not_found());
    }
    Ok(document)
}

fn verify_origin(row: &Value, actual: &Path) -> Result<(), RuntimeError> {
    let expected = row["execution_root"]
        .as_str()
        .or_else(|| row["workspace_path"].as_str())
        .filter(|path| !path.is_empty())
        .map(Path::new);
    if expected.is_some_and(|root| root != actual) {
        return Err(RuntimeError::new(
            409,
            "Checkpoint belongs to a different execution root",
        ));
    }
    Ok(())
}

pub(super) fn is_full(config: &Value) -> bool {
    config["backup_mode"]
        .as_str()
        .is_some_and(|mode| mode.trim().eq_ignore_ascii_case("full"))
}

fn rows(record: &Dir) -> Result<Vec<Value>, RuntimeError> {
    files::rows::<Map<String, Value>>(record, INPUTS)
        .map(|rows| rows.into_iter().map(Value::Object).collect())
        .map_err(storage)
}

fn meta(record: &Dir) -> Result<Value, RuntimeError> {
    Ok(files::read_json::<Map<String, Value>>(record, META)
        .map_err(storage)?
        .map(Value::Object)
        .unwrap_or_else(|| json!({})))
}

fn latest(rows: &[Value]) -> (u64, Value) {
    rows.iter()
        .max_by_key(|row| sequence(row))
        .map_or((0, Value::Null), |row| {
            (sequence(row), row["tree_hash"].clone())
        })
}

fn next_config(
    config: &Value,
    enabled: bool,
    full: bool,
    tracking: &str,
    seq: u64,
    tree: &Value,
    time: &str,
) -> Value {
    let mut config = config.as_object().cloned().unwrap_or_default();
    config.extend([
        ("enabled".into(), json!(enabled)),
        ("mode".into(), json!("overwrite")),
        (
            "backup_mode".into(),
            json!(if full { "full" } else { "shallow" }),
        ),
        ("tracking_mode".into(), json!(tracking)),
        ("last_input_seq".into(), json!(seq)),
        ("last_commit".into(), tree.clone()),
        ("updated_at".into(), json!(time)),
    ]);
    Value::Object(config)
}

fn set_meta(meta: &mut Value, config: &Value, tree: &Value) {
    for key in [
        "enabled",
        "mode",
        "tracking_mode",
        "last_input_seq",
        "updated_at",
    ] {
        meta[key] = config[key].clone();
    }
    meta["last_tree_hash"] = tree.clone();
}

fn checkpoint_row(
    seq: u64,
    message: &str,
    index: i64,
    run_status: &str,
    root: &Path,
    tracking: &str,
    tree: &str,
    parent: Option<&str>,
) -> Value {
    let message = message.trim();
    let preview: String = message
        .lines()
        .next()
        .unwrap_or_default()
        .chars()
        .take(200)
        .collect();
    json!({"seq":seq,"message":message,"message_preview":preview,"message_index":index,
        "timestamp":timestamp(),"workspace_path":root,"execution_root":root,
        "tree_hash":tree,"parent_tree_hash":parent,"insertions":0,"deletions":0,
        "files_changed":0,"files":[],"changed":false,"run_status":run_status,"tracking_mode":tracking})
}

fn snapshot(document: &Value, index: i64, status: &str, shallow_id: Option<&str>) -> Value {
    json!({"conversation_id":document["id"],"title":document["title"],"metadata":document["metadata"],
        "messages":document["messages"],"message_index":index,"run_status":status,"shallow_message_id":shallow_id})
}

fn snapshot_name(row: &Value) -> Result<Option<String>, RuntimeError> {
    let Some(path) = row["snapshot_file"].as_str() else {
        return Ok(None);
    };
    let name = format!("{}.json", sequence(row));
    if path
        .strip_prefix("snapshots/")
        .or_else(|| path.strip_prefix("snapshots\\"))
        != Some(name.as_str())
    {
        return Err(RuntimeError::bad_request(
            "Invalid checkpoint snapshot path",
        ));
    }
    Ok(Some(name))
}

fn read_snapshot_bytes(record: &Dir, name: &str) -> Result<Option<Vec<u8>>, RuntimeError> {
    let Some(directory) = files::child(record, "snapshots", false).map_err(storage)? else {
        return Ok(None);
    };
    files::read(&directory, name).map_err(storage)
}

fn load_snapshot(record: &Dir, row: &Value) -> Result<Option<Value>, RuntimeError> {
    let Some(name) = snapshot_name(row)? else {
        return Ok(None);
    };
    read_snapshot_bytes(record, &name)?
        .map(|bytes| serde_json::from_slice(&bytes).map_err(|error| storage(error.into())))
        .transpose()
}

fn restored_document(
    current: &Value,
    snapshot: Option<&Value>,
    row: &Value,
    id: &str,
    time: &str,
    versioning: Value,
    copy: bool,
) -> Result<LegacyDocument, RuntimeError> {
    let mut raw = current.clone();
    let messages = match snapshot.and_then(|snapshot| snapshot["messages"].as_array()) {
        Some(messages) => messages.clone(),
        None => {
            let count = row["message_index"]
                .as_i64()
                .or_else(|| row["message_index"].as_str()?.parse().ok())
                .unwrap_or(0)
                .saturating_add(1)
                .max(0);
            current["messages"]
                .as_array()
                .expect("validated conversation")
                .iter()
                .take(count as usize)
                .cloned()
                .collect()
        }
    };
    if let Some(snapshot) = snapshot {
        if let Some(metadata) = snapshot["metadata"].as_object() {
            let next = raw["metadata"]
                .as_object_mut()
                .expect("validated conversation");
            next.extend(
                metadata
                    .iter()
                    .filter(|(key, _)| !immutable_metadata(key))
                    .map(|(key, value)| (key.clone(), value.clone())),
            );
        }
        if let Some(title) = snapshot["title"].as_str() {
            raw["title"] = json!(title);
        }
    }
    if copy {
        raw["id"] = json!(id);
        raw["created_at"] = json!(time);
        raw["metadata"]
            .as_object_mut()
            .expect("validated conversation")
            .remove("runtime_message_queue");
    }
    raw["messages"] = json!(messages);
    raw["updated_at"] = json!(time);
    raw["metadata"]["total_messages"] = json!(messages.len());
    let tools: usize = messages
        .iter()
        .filter(|message| message["role"] == "assistant")
        .filter_map(|message| message["tool_calls"].as_array())
        .map(Vec::len)
        .sum();
    raw["metadata"]["total_tools"] = json!(tools);
    raw["metadata"]["versioning"] = versioning;
    if raw.get("todo_list").is_some() {
        raw["todo_list"] = Value::Null;
    }
    LegacyDocument::from_value(raw).map_err(legacy_error)
}

fn immutable_metadata(key: &str) -> bool {
    key.starts_with("frozen_")
        || key.starts_with("git_worktree")
        || matches!(
            key,
            "conversation_kind"
                | "runtime_message_queue"
                | "workspace_id"
                | "project_path"
                | "project_relative_path"
        )
}

fn put_json(
    tx: &mut Transaction<'_>,
    id: &str,
    path: &str,
    value: &Value,
) -> Result<(), RuntimeError> {
    tx.put(
        &format!("{id}/{path}"),
        serde_json::to_vec_pretty(value).map_err(|error| storage(error.into()))?,
    )
    .map_err(storage)
}

fn put_rows(tx: &mut Transaction<'_>, id: &str, rows: &[Value]) -> Result<(), RuntimeError> {
    tx.put(
        &format!("{id}/{INPUTS}"),
        files::encode_rows(rows).map_err(storage)?,
    )
    .map_err(storage)
}

fn as_u64(value: &Value) -> Option<u64> {
    value
        .as_u64()
        .or_else(|| value.as_str()?.trim().parse().ok())
}
