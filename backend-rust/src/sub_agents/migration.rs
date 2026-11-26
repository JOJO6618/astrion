use std::{
    collections::BTreeMap,
    io::{self, BufRead, BufReader, ErrorKind},
    path::Path,
};

use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};
use cap_std::fs::{Dir, File, OpenOptions};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde_json::{Value, json};

use crate::knowledge::storage::open_canonical_dir;

use super::{
    activity, normalize_conversation,
    registry::{Registry, checked_parent, mutation_guard, sql},
};

fn child_root(data: &Path) -> io::Result<Option<Dir>> {
    match data.canonicalize() {
        Ok(path) => open_canonical_dir(&path).map(Some),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn file(dir: &Dir, name: &str) -> io::Result<Option<File>> {
    let mut options = OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    #[cfg(unix)]
    {
        use cap_std::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NONBLOCK);
    }
    match dir.open_with(name, &options) {
        Ok(file) => {
            let information = file.metadata()?;
            if !information.is_file() || information.nlink() != 1 {
                return Err(io::Error::new(
                    ErrorKind::PermissionDenied,
                    "Invalid legacy child file",
                ));
            }
            Ok(Some(file))
        }
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

fn document(dir: &Dir, name: &str) -> io::Result<Option<Value>> {
    file(dir, name)?
        .map(|file| serde_json::from_reader(BufReader::new(file)).map_err(io::Error::from))
        .transpose()
}

fn source(root: &Dir) -> io::Result<Option<Value>> {
    let value = document(root, "sub_agent_runtime.json")?;
    if let Some(value) = value.as_ref()
        && (value["version"] != 1
            || ["tasks", "states"]
                .iter()
                .any(|key| !value[*key].is_null() && !value[*key].is_object()))
    {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "Unsupported legacy child registry",
        ));
    }
    Ok(value)
}

fn imported(db: Option<&Connection>, task: &str) -> io::Result<bool> {
    db.map(|db| {
        db.query_row(
            "SELECT EXISTS(SELECT 1 FROM imported_child WHERE task_id=?)",
            [task],
            |row| row.get(0),
        )
        .map_err(sql)
    })
    .transpose()
    .map(|found| found.unwrap_or(false))
}

pub(crate) fn legacy_pending(data: &Path) -> Result<usize, String> {
    pending(data).map_err(|error| error.to_string())
}

fn pending(data: &Path) -> io::Result<usize> {
    let Some(root) = child_root(data)? else {
        return Ok(0);
    };
    let Some(source) = source(&root)? else {
        return Ok(0);
    };
    let registry = Registry::new(data.to_owned());
    let db = registry.connect(false)?;
    let mut count = 0;
    for (id, record) in source["tasks"].as_object().into_iter().flatten() {
        if checked_parent(id, record).is_some() && !imported(db.as_ref(), id)? {
            count += 1;
        }
    }
    Ok(count)
}

pub(crate) fn migrate_legacy(data: &Path) -> Result<usize, String> {
    migrate(data).map_err(|error| error.to_string())
}

fn migrate(data: &Path) -> io::Result<usize> {
    let Some(root) = child_root(data)? else {
        return Ok(0);
    };
    let Some(source) = source(&root)? else {
        return Ok(0);
    };
    let registry = Registry::new(data.to_owned());
    let database = registry.connect(false)?;
    let mut groups = BTreeMap::<String, Vec<_>>::new();
    for (id, record) in source["tasks"].as_object().into_iter().flatten() {
        if let Some(parent) = checked_parent(id, record)
            && !imported(database.as_ref(), id)?
        {
            groups.entry(parent).or_default().push((id, record));
        }
    }
    if groups.is_empty() {
        return Ok(0);
    }
    let task_root = optional_directory(&root, "sub_agent_tasks")?;
    let states = source["states"].as_object();
    let mut total = 0;
    let mut failures = Vec::new();
    for (parent, records) in groups {
        let result = (|| {
            let mut children = Vec::new();
            for (id, record) in records {
                let directory = task_root
                    .as_ref()
                    .map(|root| optional_directory(root, id))
                    .transpose()?
                    .flatten();
                children.push(ChildSource::load(id, record, directory.as_ref())?);
            }
            let state = states.and_then(|states| {
                states.get(&parent).or_else(|| {
                    states.iter().find_map(|(id, value)| {
                        (normalize_conversation(id).as_deref() == Some(parent.as_str()))
                            .then_some(value)
                    })
                })
            });
            import_parent(&registry, &parent, children, state)
        })();
        match result {
            Ok(count) => total += count,
            Err(error) => failures.push(format!("{parent}: {error}")),
        }
    }
    if failures.is_empty() {
        Ok(total)
    } else {
        Err(io::Error::other(failures.join("\n")))
    }
}

fn optional_directory(root: &Dir, path: &str) -> io::Result<Option<Dir>> {
    match root.open_dir_nofollow(path) {
        Ok(directory) => Ok(Some(directory)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error),
    }
}

struct ChildSource<'a> {
    id: &'a str,
    record: &'a Value,
    context: Option<Value>,
    stats: Option<Value>,
    output: Option<Value>,
    activity: Option<File>,
}

impl<'a> ChildSource<'a> {
    fn load(id: &'a str, record: &'a Value, directory: Option<&Dir>) -> io::Result<Self> {
        let mut source = Self {
            id,
            record,
            context: None,
            stats: None,
            output: None,
            activity: None,
        };
        if let Some(directory) = directory {
            source.context = document(directory, "conversation.json")?;
            if let Some(context) = source.context.as_ref()
                && context["task_id"] != id
            {
                return Err(io::Error::new(
                    ErrorKind::InvalidData,
                    "Legacy child context identity differs from its registry",
                ));
            }
            source.stats = document(directory, "stats.json")?.or_else(|| {
                source
                    .context
                    .as_ref()
                    .and_then(|context| context.get("stats").cloned())
            });
            source.output = document(directory, "output.json")?;
            source.activity = file(directory, "progress.jsonl")?;
        }
        Ok(source)
    }
}

fn import_parent(
    registry: &Registry,
    parent: &str,
    children: Vec<ChildSource<'_>>,
    state: Option<&Value>,
) -> io::Result<usize> {
    let _writer = mutation_guard()?;
    let mut db = registry.connect(true)?.ok_or(ErrorKind::NotFound)?;
    let tx = db
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(sql)?;
    if let Some(state) = state {
        import_state(&tx, parent, state, &children)?;
    }
    let mut count = 0;
    for child in children {
        if imported(Some(&tx), child.id)? {
            continue;
        }
        let added = tx.execute(
            "INSERT OR IGNORE INTO child_task (id,parent_id,record,context,stats,output) VALUES(?,?,?,?,?,?)",
            params![child.id, parent, child.record.to_string(), child.context.map(|value| value.to_string()),
                child.stats.map(|value| value.to_string()), child.output.map(|value| value.to_string())],
        ).map_err(sql)?;
        if added != 1 {
            return Err(io::Error::new(
                ErrorKind::AlreadyExists,
                format!("Child task ID {} already exists", child.id),
            ));
        }
        if let Some(file) = child.activity {
            import_activity(&tx, child.id, file)?;
        }
        tx.execute("INSERT INTO imported_child VALUES(?)", [child.id])
            .map_err(sql)?;
        count += 1;
    }
    tx.commit().map_err(sql)?;
    Ok(count)
}

fn import_activity(db: &Connection, id: &str, file: File) -> io::Result<()> {
    let mut reader = BufReader::new(file);
    let mut bytes = Vec::new();
    let mut start = 0_u64;
    loop {
        bytes.clear();
        let length = reader.read_until(b'\n', &mut bytes)? as u64;
        if bytes.last() != Some(&b'\n') {
            if length != 0 {
                db.execute(
                    "UPDATE child_task SET activity_tail=? WHERE id=?",
                    params![length, id],
                )
                .map_err(sql)?;
            }
            return Ok(());
        }
        let end = start.checked_add(length).ok_or(ErrorKind::InvalidData)?;
        let event = serde_json::from_slice::<Value>(&bytes)
            .ok()
            .filter(Value::is_object)
            .map(|event| event.to_string());
        activity::insert(db, id, start, end, event.as_deref())?;
        start = end;
    }
}

fn import_state(
    db: &Connection,
    parent: &str,
    source: &Value,
    children: &[ChildSource<'_>],
) -> io::Result<()> {
    if db
        .execute(
            "INSERT OR IGNORE INTO imported_child_state VALUES(?)",
            [parent],
        )
        .map_err(sql)?
        == 0
    {
        return Ok(());
    }
    if !source.is_object() {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            "Invalid legacy child collaboration state",
        ));
    }
    let current = db
        .query_row(
            "SELECT state FROM child_state WHERE parent_id=?",
            [parent],
            |row| row.get::<_, String>(0),
        )
        .optional()
        .map_err(sql)?;
    let mut merged = source.clone();
    let pending = source["pending_master_messages"]
        .as_array()
        .is_some_and(|values| !values.is_empty());
    let mut owner = source["rust_workspace_root"].as_str();
    if pending {
        let mut owners = children
            .iter()
            .filter_map(|child| child.record["workspace_root"].as_str());
        let unambiguous = owners.next().filter(|first| {
            Path::new(first).is_absolute()
                && owners.all(|path| super::same_path(Path::new(path), Path::new(first)))
        });
        owner = owner
            .filter(|owner| Path::new(owner).is_absolute())
            .or(unambiguous);
        let owner = owner
            .filter(|owner| {
                children.iter().any(|child| {
                    child.record["workspace_root"]
                        .as_str()
                        .is_some_and(|path| super::same_path(Path::new(path), Path::new(owner)))
                })
            })
            .ok_or_else(|| {
                io::Error::new(
                    ErrorKind::InvalidData,
                    "Legacy child notification workspace is ambiguous",
                )
            })?;
        merged["rust_workspace_root"] = json!(owner);
    }
    if let Some(current) = current {
        let current: Value = serde_json::from_str(&current)?;
        if pending
            && let (Some(old), Some(active)) = (owner, current["rust_workspace_root"].as_str())
            && !super::same_path(Path::new(old), Path::new(active))
        {
            return Err(io::Error::new(
                ErrorKind::InvalidData,
                "Child notification workspace conflicts with the existing state",
            ));
        }
        for (key, value) in current.as_object().ok_or(ErrorKind::InvalidData)? {
            match key.as_str() {
                "pending_master_messages" => {
                    let mut messages = source[key].as_array().cloned().unwrap_or_default();
                    messages.extend(value.as_array().into_iter().flatten().cloned());
                    merged[key] = json!(messages);
                }
                "agents" => {
                    let mut agents = value.as_array().cloned().unwrap_or_default();
                    for agent in source[key].as_array().into_iter().flatten() {
                        if !agents
                            .iter()
                            .any(|item| item["task_id"] == agent["task_id"])
                        {
                            agents.push(agent.clone());
                        }
                    }
                    merged[key] = json!(agents);
                }
                _ => merged[key] = value.clone(),
            }
        }
    }
    db.execute(
        "INSERT INTO child_state VALUES(?,?) ON CONFLICT(parent_id) DO UPDATE SET state=excluded.state",
        params![parent, merged.to_string()],
    ).map_err(sql)?;
    Ok(())
}
