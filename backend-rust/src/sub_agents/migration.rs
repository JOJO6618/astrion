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

use crate::conversation_db::migration::Issue;

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

#[derive(Default)]
pub(crate) struct ChildMigration {
    plan: Option<Plan>,
}

struct Plan {
    root: Dir,
    source: Value,
    groups: BTreeMap<String,Vec<String>>,
}

pub(crate) fn discover_legacy(data: &Path) -> (ChildMigration,Vec<Issue>) {
    let (root, source) = match child_root(data).and_then(|root| {
        let Some(root) = root else { return Ok(None); };
        Ok(source(&root)?.map(|source|(root,source)))
    }) {
        Ok(Some(source)) => source,
        Ok(None) => return Default::default(),
        Err(error) => return (ChildMigration::default(),vec![Issue::new(1,"子智能体注册表",error)]),
    };

    let mut groups = BTreeMap::<String,Vec<String>>::new();
    let mut blocked = std::collections::BTreeSet::new();
    let mut issues = Vec::new();
    for (id,record) in source["tasks"].as_object().into_iter().flatten() {
        let fields = ["workspace_root","working_dir","execution_root","status"];
        let parent = checked_parent(id,record).filter(|_| fields.iter().all(|key|
            record.get(key).is_none_or(|value|value.is_null() || value.is_string())
        ));
        if let Some(parent) = parent {
            groups.entry(parent).or_default().push(id.clone());
        } else {
            if let Some(parent) = record["conversation_id"].as_str().and_then(normalize_conversation) {
                blocked.insert(parent);
            }
            issues.push(Issue::new(1,&format!("子智能体 {id:?}"),"旧任务身份或字段无效"));
        }
    }
    if groups.is_empty() { return (ChildMigration::default(), issues); }
    let pending = groups.values().map(Vec::len).sum::<usize>();
    let database = match Registry::new(data.to_owned()).connect(false) {
        Ok(database) => database,
        Err(error) => {
            issues.push(Issue::new(pending,"子智能体数据库",error));
            return (ChildMigration::default(),issues);
        }
    };
    let mut available = BTreeMap::new();
    for (parent,records) in groups {
        let mut unimported = Vec::new();
        for id in records {
            match imported(database.as_ref(),&id) {
                Ok(false) => unimported.push(id),
                Ok(true) => (),
                Err(error) => issues.push(Issue::new(1,&format!("子智能体 {id}"),error)),
            }
        }
        if unimported.is_empty() { continue; }
        if blocked.contains(&parent) {
            issues.push(Issue::new(unimported.len(),&format!("子智能体父对话 {parent}"),"先修复此对话中的无效子任务"));
        } else {
            available.insert(parent,unimported);
        }
    }
    let plan = (!available.is_empty()).then_some(Plan { root, source, groups:available });
    (ChildMigration { plan }, issues)
}

impl ChildMigration {
    pub(crate) fn count(&self) -> usize {
        self.plan.as_ref().map_or(0, |plan|plan.groups.values().map(Vec::len).sum())
    }

    /// A parent group commits together. The caller receives each independently committed count.
    pub(crate) fn import(self, data: &Path, mut report: impl FnMut(Result<(usize,usize),Issue>)) {
        let count = self.count();
        let Some(Plan { root, source, groups }) = self.plan else { return; };
        let task_root = match optional_directory(&root, "sub_agent_tasks") {
            Ok(root) => root,
            Err(error) => {
                report(Err(Issue::new(count,"子智能体任务目录",error)));
                return;
            }
        };
        let states = source["states"].as_object();
        let registry = Registry::new(data.to_owned());
        for (parent, records) in groups {
            let count = records.len();
            let result = (|| {
                let mut children = Vec::with_capacity(count);
                for id in &records {
                    let directory = task_root.as_ref().map(|root|optional_directory(root,id))
                        .transpose()?.flatten();
                    children.push(ChildSource::load(id,&source["tasks"][id],directory.as_ref())?);
                }
                let state = states.and_then(|states| states.get(&parent).or_else(|| {
                    states.iter().find_map(|(id,value)| {
                        (normalize_conversation(id).as_deref() == Some(parent.as_str())).then_some(value)
                    })
                }));
                import_parent(&registry,&parent,children,state)
            })();
            report(result.map(|inserted|(count,inserted))
                .map_err(|error|Issue::new(count,&format!("子智能体父对话 {parent}"),error)));
        }
    }
}

#[cfg(test)]
pub(crate) fn legacy_pending(data: &Path) -> Result<usize,String> {
    let (scan,issues) = discover_legacy(data);
    if issues.is_empty() { Ok(scan.count()) }
    else { Err(issues.into_iter().map(|issue|issue.message).collect::<Vec<_>>().join("\n")) }
}

#[cfg(test)]
pub(crate) fn migrate_legacy(data: &Path) -> Result<usize,String> {
    let (scan,mut issues) = discover_legacy(data);
    let mut count = 0;
    scan.import(data,|result|match result {
        Ok((_,inserted)) => count += inserted,
        Err(issue) => issues.push(issue),
    });
    if issues.is_empty() { Ok(count) }
    else { Err(issues.into_iter().map(|issue|issue.message).collect::<Vec<_>>().join("\n")) }
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
            "INSERT OR IGNORE INTO child_task (id,parent_id,record,stats,output) VALUES(?,?,?,?,?)",
            params![child.id, parent, child.record.to_string(),
                child.stats.map(|value| value.to_string()), child.output.map(|value| value.to_string())],
        ).map_err(sql)?;
        if added != 1 {
            return Err(io::Error::new(
                ErrorKind::AlreadyExists,
                format!("Child task ID {} already exists", child.id),
            ));
        }
        if let Some(context) = child.context.as_ref() {
            super::context_rows::write(&tx,child.id,context,0,false)?;
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
